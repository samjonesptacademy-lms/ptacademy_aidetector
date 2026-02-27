"""
PT Academy AI Detection Tool
Uses Zero GPT API to analyse each learner answer for AI-generated content.
Supports multiple qualifications with configurable prompts and field mappings.
"""

import os
import re
import json
import tempfile
import threading
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, Response, session
from pypdf import PdfReader
import requests
import secrets

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_TYPE'] = 'filesystem'

# ── API KEY & CLIENT ────────────────────────────────────────────────────────────

ZEROGPT_API_KEY = os.getenv('ZEROGPT_API_KEY')
if not ZEROGPT_API_KEY:
    raise ValueError("ZEROGPT_API_KEY not found. Please set it in your .env file or environment.")

ZEROGPT_ENDPOINT = 'https://api.zerogpt.com/api/detect/detectText'

# ── CONFIGURATION LOADING ──────────────────────────────────────────────────────

CONFIG_DIR = Path(__file__).parent / 'config'

def load_courses_config():
    """Load course definitions and field mappings."""
    config_file = CONFIG_DIR / 'courses.json'
    with open(config_file, 'r') as f:
        return json.load(f)

# Load configurations at startup
COURSES_CONFIG = load_courses_config()

# Build flat field mapping for quick lookups
def build_field_map():
    """Create flat mapping of field_name -> (unit_id, unit_label, question_label, course_id)"""
    field_map = {}
    for course_id, course_data in COURSES_CONFIG['courses'].items():
        for unit_id, unit_data in course_data['units'].items():
            unit_label = unit_data['label']
            for field_name, question_label in unit_data['fields'].items():
                field_map[field_name] = (unit_id, unit_label, question_label, course_id)
    return field_map

FIELD_MAP = build_field_map()

# Legacy field name truncation pattern (for backwards compatibility)
TRUNCATED_FIELD_MAP = {"xt Field 802": "Text Field 802"}

# Pattern to skip non-text fields
MCQ_VALUE_PATTERN = re.compile(r'^/\d+$|^/Choice\d+$|^/Off$|^/Yes$|^/No$')

# ── PDF APPEARANCE STREAM EXTRACTION ───────────────────────────────────────────

def _decode_pdf_string(raw_bytes):
    """Decode a PDF string literal's raw bytes, handling octal and standard escapes."""
    result = []
    i = 0
    while i < len(raw_bytes):
        b = raw_bytes[i]
        if b == ord('\\'):
            i += 1
            if i >= len(raw_bytes):
                break
            c = raw_bytes[i]
            if c == ord('n'):
                result.append('\n')
            elif c == ord('r'):
                result.append('\n')
            elif c == ord('t'):
                result.append('\t')
            elif c in (ord('('), ord(')'), ord('\\')):
                result.append(chr(c))
            elif chr(c).isdigit():
                # Octal escape e.g. \050 = '('
                octal = chr(c)
                for _ in range(2):
                    i += 1
                    if i < len(raw_bytes) and chr(raw_bytes[i]).isdigit():
                        octal += chr(raw_bytes[i])
                    else:
                        i -= 1
                        break
                result.append(chr(int(octal, 8)))
            else:
                result.append(chr(c))
        elif b == ord('\r'):
            result.append('\n')
        else:
            result.append(raw_bytes[i:i+1].decode('latin-1', errors='replace'))
        i += 1
    return ''.join(result)


def _extract_ap_text(widget_obj):
    """Extract visible text from a widget's /AP/N appearance stream.

    Some PDFs save the displayed text only in the appearance stream, leaving
    the /V field value empty. This is the fallback for those cases.
    """
    try:
        ap = widget_obj.get('/AP')
        if not ap:
            return None
        n = ap.get_object().get('/N')
        if not n:
            return None
        data = n.get_object().get_data()
        parts = []
        pat = re.compile(rb'\(([^)\\]*(?:\\.[^)\\]*)*)\)\s*[Tj\'"]', re.DOTALL)
        for m in pat.finditer(data):
            text = _decode_pdf_string(m.group(1))
            if text.strip():
                parts.append(text)
        # Also handle TJ array form: [(string) ...] TJ
        for m in re.finditer(rb'\[([^\]]*)\]\s*TJ', data, re.DOTALL):
            for sm in re.finditer(rb'\(([^)\\]*(?:\\.[^)\\]*)*)\)', m.group(1), re.DOTALL):
                text = _decode_pdf_string(sm.group(1))
                if text.strip():
                    parts.append(text)
        text = ' '.join(parts).strip()
        return text if text else None
    except Exception:
        return None


def _build_widget_map(reader):
    """Build a field-name -> widget object map by scanning all page annotations.

    Required for AP stream fallback — get_fields() doesn't expose /AP directly.
    """
    widgets = {}
    for page in reader.pages:
        if '/Annots' not in page:
            continue
        for annot in page['/Annots']:
            try:
                obj = annot.get_object()
                if obj.get('/Subtype') == '/Widget':
                    name = str(obj.get('/T', ''))
                    if name and name not in widgets:
                        widgets[name] = obj
            except Exception:
                pass
    return widgets


# ── PDF EXTRACTION ─────────────────────────────────────────────────────────────

def extract_answers_from_pdf(pdf_path, course_id='level2_gym', selected_units=None):
    """Extract learner answers from PDF using configured field mappings.

    Args:
        pdf_path: Path to PDF file
        course_id: Course ID to filter by (default: 'level2_gym')
        selected_units: List of unit_id strings to include, or None for all units
    """
    reader = PdfReader(pdf_path)
    fields = reader.get_fields()
    if not fields:
        return []

    widget_map = _build_widget_map(reader)

    answers = []
    for raw_name, field in fields.items():
        name = TRUNCATED_FIELD_MAP.get(raw_name, raw_name)
        if name not in FIELD_MAP:
            continue

        unit_id, unit_label, question_label, detected_course = FIELD_MAP[name]

        # Skip if this field isn't part of the selected course
        # For backwards compatibility, allow level2_gym fields to be used by default
        if detected_course != course_id and detected_course != 'level2_gym':
            continue

        # Skip if unit is not in selected units (if filtering is active)
        if selected_units is not None and unit_id not in selected_units:
            continue

        value = field.get('/V', '')
        value_str = str(value).strip() if value else ''

        # Fall back to appearance stream when /V is empty (PDF saved without updating value)
        if not value_str or MCQ_VALUE_PATTERN.match(value_str):
            widget_obj = widget_map.get(name)
            if widget_obj:
                ap_text = _extract_ap_text(widget_obj)
                if ap_text:
                    value_str = ap_text

        if not value_str or MCQ_VALUE_PATTERN.match(value_str):
            continue

        # Allow short answers for SMART goal fields, otherwise require minimum 20 chars
        is_smart_goal = 'SMART' in question_label or 'Specific' in question_label or 'Measurable' in question_label or 'Attainable' in question_label or 'Relevant' in question_label or 'Timed' in question_label
        if len(value_str) < 20 and not is_smart_goal:
            continue

        answers.append({
            'field': name,
            'unit': unit_label,
            'question': question_label,
            'answer': value_str,
            'course': course_id,
        })

    return answers


def extract_answers_fallback(pdf_path):
    """Fallback extraction for PDFs without standard field mappings."""
    reader = PdfReader(pdf_path)
    fields = reader.get_fields()
    if not fields:
        return []

    skip = re.compile(
        r'^(Unit_[12]_Q|U5Q|U1_CB|Check Box|dateField|dateFiield|StartDate|resultFirst|'
        r'resultSecond|resultThird|U6Result|LearnerName|AssessorName)',
        re.IGNORECASE
    )
    answers = []
    for name, field in fields.items():
        if skip.match(name):
            continue
        value = field.get('/V', '')
        if not value:
            continue
        value_str = str(value).strip()
        if MCQ_VALUE_PATTERN.match(value_str) or len(value_str) < 40:
            continue
        if re.match(r'^\d{1,2}[./]\d{1,2}[./]\d{2,4}$', value_str):
            continue
        if re.match(r'^(PASS|REFER|FAIL)[\s\-]', value_str, re.IGNORECASE):
            continue
        answers.append({
            'field': name,
            'unit': 'Unknown Unit',
            'question': f'Field: {name}',
            'answer': value_str,
            'course': 'unknown',
        })
    return answers


# ── ANALYSIS FUNCTIONS ────────────────────────────────────────────────────────

def detect_with_zerogpt(question, answer, unit, course_id):
    """
    Detect AI-generated content using Zero GPT API.

    Returns dict with:
    - overall_classification: "AI", "AI Polished", or "Human"
    - ai_percentage, ai_polished_percentage, human_percentage
    - confidence: Detection confidence (0-1)
    - overall_verdict: Explanation
    - human_signals, ai_signals: Pattern lists
    """
    import time

    try:
        # Retry logic: attempt up to 3 times with delays
        max_retries = 2
        retry_delay = 1  # seconds

        for attempt in range(max_retries + 1):
            try:
                # Prepare request headers
                headers = {
                    'ApiKey': ZEROGPT_API_KEY,
                    'Content-Type': 'application/json',
                }

                # Prepare request payload
                payload = {
                    'input_text': answer
                }

                # Make API request to Zero GPT (increased timeout to 60s)
                response = requests.post(ZEROGPT_ENDPOINT, json=payload, headers=headers, timeout=60)
                response.raise_for_status()

                # Parse response
                response_data = response.json()
                break  # Success, exit retry loop

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, ValueError) as e:
                # Timeout, connection error, or JSON parse error - retry
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                else:
                    # All retries exhausted
                    raise

        # Extract data from Zero GPT response
        # Zero GPT returns: { "success": true, "data": { "fakePercentage": 0-100, "feedback": "...", ... } }
        data = response_data.get('data', {})

        # Validate response - check if data is empty or invalid
        if not data or not data.get('feedback'):
            raise ValueError("Empty or invalid response from Zero GPT API")

        # Extract and validate numeric fields (convert empty strings to 0)
        fake_percentage_raw = data.get('fakePercentage', 0)
        fake_percentage = float(fake_percentage_raw) if fake_percentage_raw else 0

        feedback = data.get('feedback', '')

        # Extract AI-flagged sentences (h array)
        ai_flagged_sentences = data.get('h', []) if data.get('h') else []

        text_words_raw = data.get('textWords', 0)
        text_words = int(text_words_raw) if text_words_raw else 0

        ai_words_raw = data.get('aiWords', 0)
        ai_words = int(ai_words_raw) if ai_words_raw else 0

        # Simplified display: single AI percentage with binary classification
        ai_percentage = round(fake_percentage, 2)
        human_percentage = round(100 - fake_percentage, 2)

        # Use feedback field for classification (Zero GPT's own verdict)
        if 'Human Written' in feedback or 'human written' in feedback:
            classification = 'Human'
            confidence = 0.9
        elif 'AI/GPT Generated' in feedback or 'AI Generated' in feedback:
            classification = 'AI'
            confidence = 0.9
        elif 'mixed signals' in feedback.lower() or 'mixed' in feedback.lower():
            classification = 'Mixed'
            confidence = 0.7
        else:
            # Fallback to percentage-based if feedback doesn't match known patterns
            if fake_percentage >= 60:
                classification = 'AI'
                confidence = 0.5 + (fake_percentage - 60) * 0.01
            elif fake_percentage < 30:
                classification = 'Human'
                confidence = 0.5 + (30 - fake_percentage) * 0.01
            else:
                classification = 'Mixed'
                confidence = 0.5

        confidence = max(0.0, min(1.0, confidence))

        # Generate signals based on percentage
        ai_signals = []
        if fake_percentage >= 75:
            ai_signals.append(f'High AI probability ({fake_percentage}%)')
        elif fake_percentage >= 50:
            ai_signals.append(f'Moderate AI probability ({fake_percentage}%)')
        elif fake_percentage > 0:
            ai_signals.append(f'Some AI signals detected ({fake_percentage}%)')

        # Use Zero GPT's feedback as the main verdict
        verdict = feedback if feedback else f'AI probability: {fake_percentage}%'

        return {
            'overall_classification': classification,
            'ai_percentage': ai_percentage,
            'human_percentage': human_percentage,
            'ai_polished_percentage': 0,  # Backward compatibility - always 0 for new system
            'confidence': round(confidence, 3),
            'overall_verdict': verdict,
            'feedback': feedback,
            'ai_signals': ai_signals,
            'ai_flagged_sentences': ai_flagged_sentences,
            'text_words': text_words,
            'ai_words': ai_words,
        }

    except requests.exceptions.Timeout:
        return {
            'overall_classification': 'Unknown',
            'ai_percentage': 0,
            'human_percentage': 0,
            'ai_polished_percentage': 0,
            'confidence': 0.0,
            'overall_verdict': 'Detection timeout: API request took too long',
            'feedback': 'Error: Request timeout',
            'ai_signals': [],
            'ai_flagged_sentences': [],
            'text_words': 0,
            'ai_words': 0,
            'error': True,
        }
    except requests.exceptions.HTTPError as e:
        return {
            'overall_classification': 'Unknown',
            'ai_percentage': 0,
            'human_percentage': 0,
            'ai_polished_percentage': 0,
            'confidence': 0.0,
            'overall_verdict': f'Detection error: {e.response.status_code} - {str(e)}',
            'feedback': f'Error: {e.response.status_code}',
            'ai_signals': [],
            'ai_flagged_sentences': [],
            'text_words': 0,
            'ai_words': 0,
            'error': True,
        }
    except Exception as e:
        return {
            'overall_classification': 'Unknown',
            'ai_percentage': 0,
            'human_percentage': 0,
            'ai_polished_percentage': 0,
            'confidence': 0.0,
            'overall_verdict': f'Detection error: {str(e)}',
            'feedback': 'Error: Detection failed',
            'ai_signals': [],
            'ai_flagged_sentences': [],
            'text_words': 0,
            'ai_words': 0,
            'error': True,
        }


def analyse_answer(question, answer, unit, course_id):
    """
    Analyse a single answer for AI-generated content using Zero GPT API.

    Process:
    1. Use Zero GPT for AI detection
    2. Apply risk classification and confidence adjustments based on word count
    3. Return analysis with confidence and signals

    Returns: dict with AI detection analysis
    """
    word_count = len(answer.split())

    # Use Zero GPT for detection
    try:
        zerogpt_result = detect_with_zerogpt(question, answer, unit, course_id)

        if zerogpt_result.get('error'):
            return {
                'overall_classification': 'Unknown',
                'ai_percentage': 0,
                'human_percentage': 0,
                'ai_polished_percentage': 0,
                'overall_verdict': zerogpt_result.get('overall_verdict', 'Detection error'),
                'feedback': zerogpt_result.get('feedback', ''),
                'ai_signals': [],
                'ai_flagged_sentences': [],
                'text_words': word_count,
                'ai_words': 0,
                'confidence': 0.0,
                'adjusted_confidence': 0.0,
                'risk_level': 'Unknown',
                'word_count': word_count,
                'low_confidence_flag': word_count < 50,
                'confidence_note': '⚠️ Low confidence (short answer < 50 words)' if word_count < 50 else '',
                'error': True,
            }

        # Get base confidence from Zero GPT result
        base_confidence = zerogpt_result.get('confidence', 0.5)

        # Apply confidence adjustment based on word count
        if word_count < 50:
            adjusted_confidence = round(base_confidence * 0.7, 3)
            low_confidence_flag = True
            confidence_note = '⚠️ Low confidence (short answer < 50 words)'
        else:
            adjusted_confidence = base_confidence
            low_confidence_flag = False
            confidence_note = ''

        # Determine risk level based on feedback-based classification
        classification = zerogpt_result.get('overall_classification', 'Unknown')
        if classification == 'AI':
            risk_level = 'High'
        elif classification == 'Mixed':
            risk_level = 'Medium'
        elif classification == 'Human':
            risk_level = 'Human'
        else:
            risk_level = 'Unknown'

        ai_pct = zerogpt_result.get('ai_percentage', 0)

        return {
            'overall_classification': classification,
            'ai_percentage': ai_pct,
            'human_percentage': zerogpt_result.get('human_percentage', 100 - ai_pct),
            'ai_polished_percentage': zerogpt_result.get('ai_polished_percentage', 0),
            'overall_verdict': zerogpt_result.get('overall_verdict', ''),
            'feedback': zerogpt_result.get('feedback', ''),
            'ai_signals': zerogpt_result.get('ai_signals', []),
            'ai_flagged_sentences': zerogpt_result.get('ai_flagged_sentences', []),
            'text_words': zerogpt_result.get('text_words', word_count),
            'ai_words': zerogpt_result.get('ai_words', 0),
            'confidence': base_confidence,
            'adjusted_confidence': adjusted_confidence,
            'risk_level': risk_level,
            'word_count': word_count,
            'low_confidence_flag': low_confidence_flag,
            'confidence_note': confidence_note,
        }

    except Exception as e:
        return {
            'overall_classification': 'Unknown',
            'ai_percentage': 0,
            'human_percentage': 0,
            'ai_polished_percentage': 0,
            'overall_verdict': f'Analysis unavailable: {str(e)}',
            'feedback': 'Error during analysis',
            'ai_signals': [],
            'ai_flagged_sentences': [],
            'text_words': word_count,
            'ai_words': 0,
            'confidence': 0.0,
            'adjusted_confidence': 0.0,
            'risk_level': 'Unknown',
            'word_count': word_count,
            'low_confidence_flag': word_count < 50,
            'confidence_note': '⚠️ Low confidence (short answer < 50 words)' if word_count < 50 else '',
            'error': True,
        }


# ── CORS FOR CHROME EXTENSION ──────────────────────────────────────────────────

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin', '')
    if origin.startswith('chrome-extension://'):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# ── FLASK ROUTES ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/test-email', methods=['GET'])
def test_email():
    """Test endpoint to send a test email notification."""
    try:
        from analysis.email_notifier import send_error_email
        success = send_error_email(
            error_title="Test Email",
            error_message="This is a test email from the PT Academy AI Detector. If you received this, email notifications are working correctly! ✅"
        )
        if success:
            return jsonify({'message': 'Test email sent successfully!'}), 200
        else:
            return jsonify({'error': 'Failed to send test email. Check server logs.'}), 500
    except Exception as e:
        return jsonify({'error': f'Error sending test email: {str(e)}'}), 500


@app.route('/courses', methods=['GET'])
def get_courses():
    """Return available courses with their units for UI selection."""
    courses = []
    for course_id, course_data in COURSES_CONFIG['courses'].items():
        units = [
            {'id': unit_id, 'label': unit_data['label']}
            for unit_id, unit_data in course_data['units'].items()
        ]
        courses.append({
            'id': course_id,
            'name': course_data['name'],
            'units': units,
        })
    return jsonify({'courses': courses})


def generate_analysis_stream(answers, course_id, filename, learner_name='Unknown', assessor_name='Unknown',
                            analysis_mode='full', selected_units=None):
    """Generator that yields analysis results as newline-delimited JSON.

    Args:
        answers: List of extracted answers
        course_id: Selected course ID
        filename: Original PDF filename
        learner_name: Learner's name
        assessor_name: Assessor's name
        analysis_mode: 'full' or 'partial'
        selected_units: List of unit_ids if partial mode, None for full
    """
    gpt_calls = 0
    results = []
    session_id = secrets.token_urlsafe(16)  # Generate session ID for PDF storage

    # Send start event
    yield json.dumps({
        'type': 'start',
        'total': len(answers),
        'course': course_id,
        'course_name': COURSES_CONFIG['courses'][course_id]['name'],
        'filename': filename,
        'session_id': session_id,
        'analysis_mode': analysis_mode,
    }) + '\n'

    # Analyse each answer and stream results
    for idx, a in enumerate(answers, 1):
        detection = analyse_answer(a['question'], a['answer'], a['unit'], course_id)

        gpt_calls += 1

        result = {
            'unit': a['unit'],
            'question': a['question'],
            'answer_preview': a['answer'][:300] + ('...' if len(a['answer']) > 300 else ''),
            'answer_full': a['answer'],
            'word_count': len(a['answer'].split()),
            **detection,
        }
        results.append(result)

        # Stream the individual result
        yield json.dumps({
            'type': 'answer',
            'index': idx,
            'total': len(answers),
            'result': result,
        }) + '\n'
    
    # Sort results by AI percentage for final summary
    results.sort(key=lambda x: x.get('ai_percentage', 0), reverse=True)

    # Calculate summary statistics based on classifications (all answers)
    ai_count = sum(1 for r in results if r.get('overall_classification') == 'AI')
    ai_polished_count = sum(1 for r in results if r.get('overall_classification') == 'AI Polished')
    human_count = sum(1 for r in results if r.get('overall_classification') == 'Human')

    # Count short answers
    short_answer_count = sum(1 for r in results if r.get('low_confidence_flag', False))
    short_answer_pct = round((short_answer_count / len(results)) * 100, 1) if results else 0

    # ── PORTFOLIO-LEVEL SCORING ────────────────────────────────────────────────
    # Short answers (< 50 words) are excluded from portfolio scoring as there is
    # insufficient text for reliable AI detection.
    scoreable_results = [r for r in results if not r.get('low_confidence_flag', False)]

    avg_ai_pct = round(sum(r.get('ai_percentage', 0) for r in scoreable_results) / len(scoreable_results)) if scoreable_results else 0
    avg_ai_polished_pct = 0  # Simplified display: always 0 in new system
    avg_human_pct = round(sum(r.get('human_percentage', 0) for r in scoreable_results) / len(scoreable_results)) if scoreable_results else 0

    portfolio_score = round(avg_ai_pct, 2)

    # Calculate portfolio confidence from scoreable answers only
    portfolio_confidence = round(
        sum(r.get('adjusted_confidence', 0.5) for r in scoreable_results) / len(scoreable_results)
        if scoreable_results else 0.5,
        3
    )

    quality_note = ""
    if short_answer_count > 0:
        excluded_note = f"{short_answer_count} short answer{'s' if short_answer_count > 1 else ''} (< 50 words) excluded from portfolio score."
        quality_note = excluded_note
        if not scoreable_results:
            quality_note += " No scoreable answers remain — portfolio score unavailable."

    # Classify portfolio risk based on portfolio score
    if portfolio_score >= 65:
        portfolio_risk = 'High'
    elif portfolio_score >= 35:
        portfolio_risk = 'Medium'
    elif portfolio_score >= 20:
        portfolio_risk = 'Low'
    else:
        portfolio_risk = 'Human'

    # Risk breakdown by level
    risk_breakdown = {
        'high': sum(1 for r in results if r.get('risk_level') == 'High'),
        'medium': sum(1 for r in results if r.get('risk_level') == 'Medium'),
        'low': sum(1 for r in results if r.get('risk_level') == 'Low'),
        'human': sum(1 for r in results if r.get('risk_level') == 'Human'),
    }

    # Store analysis in session for PDF download
    # Include all fields needed for comprehensive PDF report
    pdf_results = []
    for r in results:
        pdf_results.append({
            'unit': r.get('unit'),
            'question': r.get('question'),
            'ai_percentage': r.get('ai_percentage'),
            'overall_classification': r.get('overall_classification'),
            'feedback': r.get('feedback', 'No feedback available'),
            'answer_full': r.get('answer_full', ''),  # Full answer text
            'overall_verdict': r.get('overall_verdict', ''),
            'low_confidence_flag': r.get('low_confidence_flag'),
        })

    # Determine overall risk category based on classification distribution (backward compatibility)
    if ai_count >= 2 or avg_ai_pct >= 65:
        overall_risk = 'high'
    elif ai_count >= 1 or ai_polished_count >= 3 or avg_ai_pct >= 40:
        overall_risk = 'medium'
    elif avg_ai_pct >= 20:
        overall_risk = 'low'
    else:
        overall_risk = 'unlikely'

    # Store analysis in session for PDF download (using a simple in-memory store)
    if not hasattr(generate_analysis_stream, 'analysis_cache'):
        generate_analysis_stream.analysis_cache = {}

    # Build analysis mode label for PDF
    if analysis_mode == 'partial' and selected_units:
        # Get unit labels for selected units
        course_units = COURSES_CONFIG['courses'][course_id]['units']
        selected_unit_labels = [course_units[uid]['label'] for uid in selected_units if uid in course_units]
        analysis_mode_label = f"Partial Analysis - Units: {', '.join(selected_unit_labels)}"
    else:
        analysis_mode_label = "Full Workbook Analysis"

    analysis_for_pdf = {
        'results': pdf_results,
        'learner_name': learner_name,
        'assessor_name': assessor_name,
        'course_name': COURSES_CONFIG['courses'][course_id]['name'],
        'analysis_mode': analysis_mode,
        'analysis_mode_label': analysis_mode_label,
        'selected_units': selected_units,
        'summary': {
            'total_answers': len(results),
            'ai_count': ai_count,
            'ai_polished_count': ai_polished_count,
            'human_count': human_count,
            'avg_ai_percentage': avg_ai_pct,
            'portfolio_score': portfolio_score,
            'portfolio_confidence': portfolio_confidence,
            'portfolio_risk': portfolio_risk,
            'short_answer_count': short_answer_count,
            'short_answer_pct': short_answer_pct,
            'quality_note': quality_note,
            'risk_breakdown': risk_breakdown,
        }
    }
    generate_analysis_stream.analysis_cache[session_id] = analysis_for_pdf

    # Upload PDF to Dropbox in background (non-blocking)
    def upload_to_dropbox():
        try:
            from analysis.pdf_generator import generate_pdf_report
            from analysis.dropbox_uploader import upload_report_to_dropbox

            pdf_bytes = generate_pdf_report(analysis_for_pdf)
            course_name = analysis_for_pdf.get('course_name', 'Unknown Course')
            learner_name = analysis_for_pdf.get('learner_name', 'Unknown Learner')

            result = upload_report_to_dropbox(pdf_bytes, learner_name, course_name)
            if result['success']:
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"Dropbox upload successful: {result['path']}")
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Background Dropbox upload failed: {e}")

    upload_thread = threading.Thread(target=upload_to_dropbox, daemon=True)
    upload_thread.start()

    # Send summary event
    yield json.dumps({
        'type': 'summary',
        'summary': {
            'total_answers': len(results),
            'ai_count': ai_count,
            'ai_polished_count': ai_polished_count,
            'human_count': human_count,
            'avg_ai_percentage': avg_ai_pct,
            'avg_ai_polished_percentage': avg_ai_polished_pct,
            'avg_human_percentage': avg_human_pct,
            'overall_risk': overall_risk,
            # NEW: Portfolio-level scoring
            'portfolio_score': portfolio_score,
            'portfolio_confidence': portfolio_confidence,
            'portfolio_risk': portfolio_risk,
            'short_answer_count': short_answer_count,
            'short_answer_pct': short_answer_pct,
            'quality_note': quality_note,
            'risk_breakdown': risk_breakdown,
        },
        'zerogpt_calls': gpt_calls,
        'session_id': session_id,
    }) + '\n'


@app.route('/analyse', methods=['POST'])
def analyse():
    """Main analysis endpoint with streaming responses."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Please upload a PDF file'}), 400

    # Get course parameter (default to level2_gym for backwards compatibility)
    course_id = request.form.get('course', 'level2_gym')
    learner_name = request.form.get('learner_name', 'Unknown')
    assessor_name = request.form.get('assessor_name', 'Unknown')

    if course_id not in COURSES_CONFIG['courses']:
        return jsonify({'error': f'Unknown course: {course_id}'}), 400

    # Get selected units (partial analysis), or None for full analysis
    selected_units_raw = request.form.getlist('units')
    selected_units = selected_units_raw if selected_units_raw else None  # None = all units

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        # Extract answers (filtered by selected_units if provided)
        answers = extract_answers_from_pdf(tmp_path, course_id, selected_units=selected_units)
        used_fallback = False

        if not answers:
            answers = extract_answers_fallback(tmp_path)
            used_fallback = True

        if not answers:
            return jsonify({
                'error': 'No learner answers found. This may be a blank or scanned workbook.',
                'is_blank': True,
            }), 200

        # Stream analysis results with analysis mode info
        return Response(
            generate_analysis_stream(answers, course_id, file.filename, learner_name, assessor_name,
                                   analysis_mode='partial' if selected_units else 'full',
                                   selected_units=selected_units),
            mimetype='application/x-ndjson'
        )

    except Exception as e:
        # Send error notification email
        try:
            from analysis.email_notifier import send_error_email
            import traceback
            send_error_email(
                error_title=type(e).__name__,
                error_message=f"""Failed to process workbook:

Learner: {learner_name}
Assessor: {assessor_name}
Workbook: {file.filename}
Course: {course_id}

Error: {str(e)}""",
                error_traceback=traceback.format_exc()
            )
        except Exception as email_error:
            logging.error(f"Failed to send error email: {email_error}")

        return jsonify({'error': f'Failed to process workbook: {str(e)}'}), 500
    finally:
        os.unlink(tmp_path)


@app.route('/download-report/<session_id>', methods=['GET'])
def download_report(session_id):
    """Download PDF report for analysis results."""
    try:
        from analysis.pdf_generator import generate_pdf_report

        # Retrieve analysis from cache
        analysis_cache = getattr(generate_analysis_stream, 'analysis_cache', {})
        analysis_data = analysis_cache.get(session_id)

        if not analysis_data:
            return jsonify({'error': 'No analysis data found for this session. The analysis may have expired.'}), 404

        # Generate PDF
        pdf_bytes = generate_pdf_report(analysis_data)

        # Clean up cache entry after download
        if session_id in analysis_cache:
            del analysis_cache[session_id]

        # Generate dynamic filename with analysis mode
        # Format: "{Learner Name}_{Course Name}_AI Report_{Full|Partial[_Units]}_{YYYY-MM-DD}.pdf"
        from datetime import datetime
        learner = analysis_data.get('learner_name', 'Learner')
        course = analysis_data.get('course_name', 'Course')
        analysis_mode = analysis_data.get('analysis_mode', 'full')
        selected_units = analysis_data.get('selected_units')
        date_str = datetime.now().strftime('%Y-%m-%d')

        # Build mode suffix
        if analysis_mode == 'partial' and selected_units:
            # Extract unit numbers from unit_ids (e.g., 'unit2' -> 'U2')
            unit_suffixes = []
            for unit_id in selected_units:
                # Extract numeric part from unit_id
                import re as re_module
                match = re_module.search(r'\d+', unit_id)
                if match:
                    unit_suffixes.append(f"U{match.group()}")
            mode_suffix = 'Partial_' + '_'.join(unit_suffixes) if unit_suffixes else 'Partial'
        else:
            mode_suffix = 'Full'

        safe_name = re.sub(r'[^\w\s\-]', '', f"{learner}_{course}_AI Report_{mode_suffix}_{date_str}").strip()
        safe_name = re.sub(r'\s+', ' ', safe_name)
        filename = f"{safe_name}.pdf"

        # Return PDF as download
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"'
            }
        )

    except ImportError as e:
        return jsonify({'error': f'PDF generation not available. Please install reportlab. Error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Failed to generate PDF: {str(e)}'}), 500


@app.route('/analyse-text', methods=['OPTIONS'])
def analyse_text_preflight():
    """CORS preflight for Chrome extension."""
    return '', 204


@app.route('/analyse-text', methods=['POST'])
def analyse_text():
    """Analyse a single text answer — used by the Chrome extension."""
    data = request.get_json()
    text = (data or {}).get('text', '').strip()
    question = (data or {}).get('question', 'Learner Answer')
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    if len(text) < 20:
        return jsonify({'error': 'Text too short to analyse (minimum 20 characters)'}), 400
    result = analyse_answer(question, text, unit='Learner Submission', course_id='level2_gym')
    return jsonify(result)


@app.errorhandler(500)
def handle_500(e):
    """Handle server errors gracefully."""
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
