"""
PT Academy AI Detection Tool
Uses Zero GPT API to analyse each learner answer for AI-generated content.
Supports multiple qualifications with configurable prompts and field mappings.
"""

import os
import re
import json
import logging
import tempfile
import threading
import time
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


class ZeroGptAPIError(Exception):
    """Zero GPT refused the request and said why in the body.

    Zero GPT reports its own failures as HTTP 200 with {"success": false,
    "code": 403, "message": "Not enough credits", "data": null} - so the status
    code is not enough to spot them. Carrying the API's own message lets the
    real reason reach the assessor instead of a generic parse failure.
    """

    def __init__(self, message, code=None):
        super().__init__(message)
        self.api_message = message
        self.code = code

    @property
    def is_account_error(self):
        """True when the account itself is blocked, not just this one request.

        Credits, auth and quota problems will fail identically for every
        remaining answer, so the caller aborts the run rather than repeating
        the same failure for each one.
        """
        if self.code in (401, 402, 403):
            return True
        text = (self.api_message or '').lower()
        return any(s in text for s in ('credit', 'quota', 'subscription', 'api key', 'unauthorized'))


# ── CONFIGURATION LOADING ──────────────────────────────────────────────────────

CONFIG_DIR = Path(__file__).parent / 'config'

def load_courses_config():
    """Load course definitions and field mappings."""
    config_file = CONFIG_DIR / 'courses.json'
    with open(config_file, 'r') as f:
        return json.load(f)

# Load configurations at startup
COURSES_CONFIG = load_courses_config()

# Presentation content shared with the PDF, so the browser renders exactly the
# strings the report prints rather than its own copy of them.
from analysis.report_content import (
    CLASSIFICATION_STYLE, FLAGGED_GROUP_LABELS, READING_GUIDE,
    build_notes, build_recommendation, review_level, verdict_mix, verdict_mix_bar,
)

# Build field mapping for quick lookups
def build_field_map():
    """Create mapping of field_name -> {course_id: (unit_id, unit_label, question_label)}.

    A single generic field name (e.g. "Text Field 577") is reused by several
    different course configs. Keying only by field name would let whichever course
    is defined last in courses.json silently clobber the others, dropping those
    questions from every earlier course. So we keep every course's mapping per
    field name and resolve by the requested course at extraction time.
    """
    field_map = {}
    for course_id, course_data in COURSES_CONFIG['courses'].items():
        # `order` is the field's position within its course config, used to sort
        # the report into natural unit -> question order (the PDF's own field
        # order is scrambled and would otherwise leak into the report).
        order = 0
        for unit_id, unit_data in course_data['units'].items():
            unit_label = unit_data['label']
            for field_name, question_label in unit_data['fields'].items():
                field_map.setdefault(field_name, {})[course_id] = (
                    unit_id, unit_label, question_label, order
                )
                order += 1
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


# Text-showing and line-positioning operators in an appearance stream, matched
# in document order so _extract_ap_text can reconstruct line breaks.
_AP_TEXT_PATTERN = re.compile(
    rb"\((?P<s>[^)\\]*(?:\\.[^)\\]*)*)\)\s*(?P<op>Tj|'|\")"
    rb"|\[(?P<arr>[^\]]*)\]\s*TJ"
    rb"|(?P<move>(?:(?P<tx>-?[\d.]+)\s+(?P<ty>-?[\d.]+)\s+)?(?P<op_move>Td|TD|T\*))",
    re.DOTALL,
)


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

        # Walk the stream in order so line breaks survive. Zero GPT's scoring is
        # sensitive to formatting - the same answer scored "AI/GPT Generated"
        # with its newlines and "contains mixed signals" once they were replaced
        # by spaces - so flattening every line into one paragraph (which this
        # used to do) silently changes the verdict.
        lines = []
        current = []

        def end_line():
            if current:
                lines.append(''.join(current))
                current.clear()

        for m in _AP_TEXT_PATTERN.finditer(data):
            if m.group('move'):
                # Td/TD move the text position; only a non-zero vertical shift
                # starts a new line (a zero shift is intra-line kerning). T* is
                # always a new line.
                if m.group('op_move') == b'T*' or float(m.group('ty') or 0) != 0:
                    end_line()
            elif m.group('arr') is not None:
                for sm in re.finditer(rb'\(([^)\\]*(?:\\.[^)\\]*)*)\)', m.group('arr'), re.DOTALL):
                    current.append(_decode_pdf_string(sm.group(1)))
            else:
                # The ' and " operators show text on the *next* line.
                if m.group('op') in (b"'", b'"'):
                    end_line()
                current.append(_decode_pdf_string(m.group('s')))

        end_line()

        # The stream records every *visual* line, so most breaks are soft word
        # wraps rather than breaks the learner typed. The /V path (used by most
        # PDFs) only ever carries hard breaks, so keeping the wraps here would
        # feed Zero GPT differently formatted text depending on which extraction
        # path a PDF happened to take - and formatting changes its verdict.
        # Re-join wrapped lines, treating a break as intentional only when the
        # previous line ends a sentence. Approximate, but far closer to what the
        # learner typed than either keeping every wrap or flattening all of them.
        merged = []
        for line in (ln.strip() for ln in lines):
            if not line:
                continue
            if merged and not re.search(r'[.!?:;]$', merged[-1]):
                merged[-1] = f'{merged[-1]} {line}'
            else:
                merged.append(line)

        text = '\n'.join(merged).strip()
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


def _coerce_field_value(value):
    """Resolve a form field /V into a clean string.

    Handles indirect references and stream-valued fields. Some PDF editors save
    long field values as a (flate-compressed) stream of Windows-1252 text rather
    than a plain string; pypdf decodes these with strict UTF-8, which raises a
    UnicodeDecodeError on bytes like 0x93 (a "smart" quote). We decode the raw
    bytes ourselves with a tolerant encoding fallback instead.
    """
    if value is None:
        return ''
    try:
        obj = value.get_object()  # resolve IndirectObject -> concrete object
    except Exception:
        obj = value
    get_data = getattr(obj, 'get_data', None)
    if callable(get_data):
        # Stream-valued field: decode the decompressed bytes ourselves.
        try:
            raw = obj.get_data()
        except Exception:
            return ''
        if isinstance(raw, bytes):
            for enc in ('utf-8', 'cp1252', 'latin-1'):
                try:
                    return raw.decode(enc).strip()
                except UnicodeDecodeError:
                    continue
            return raw.decode('latin-1', errors='replace').strip()
        return str(raw).strip()
    return str(obj).strip()


def _safe_get_fields(reader):
    """Safely extract form fields from PDF, handling malformed/undecodable fields.

    Some PDFs have incomplete or malformed /AP dictionaries that cause pypdf's
    get_fields() to raise KeyError, or store field values as streams of
    non-UTF-8 text that make pypdf raise UnicodeDecodeError. This function
    attempts get_fields() first, and falls back to manual annotation scanning if
    that fails for any reason.

    Returns a dict of {field_name: field_object} or {} if extraction fails completely.
    """
    try:
        # Try standard pypdf get_fields() first
        return reader.get_fields() or {}
    except Exception as e:
        logging.warning(f"get_fields() failed ({type(e).__name__}: {e}); "
                        "falling back to manual annotation scan")
        # Fallback: manually extract fields from annotations (skips malformed ones)
        fields = {}
        for page in reader.pages:
            if '/Annots' not in page:
                continue
            for annot in page['/Annots']:
                try:
                    obj = annot.get_object()
                    if obj.get('/Subtype') == '/Widget':
                        name = obj.get('/T')
                        if name:
                            fields[str(name)] = obj
                except Exception:
                    # Skip any malformed annotations
                    pass
        return fields


# ── PDF EXTRACTION ─────────────────────────────────────────────────────────────

def extract_answers_from_pdf(pdf_path, course_id='level2_gym', selected_units=None):
    """Extract learner answers from PDF using configured field mappings.

    Args:
        pdf_path: Path to PDF file
        course_id: Course ID to filter by (default: 'level2_gym')
        selected_units: List of unit_id strings to include, or None for all units
    """
    reader = PdfReader(pdf_path)
    fields = _safe_get_fields(reader)
    if not fields:
        return []

    widget_map = _build_widget_map(reader)

    answers = []
    for raw_name, field in fields.items():
        name = TRUNCATED_FIELD_MAP.get(raw_name, raw_name)
        owners = FIELD_MAP.get(name)
        if not owners:
            continue

        # Resolve the mapping for the requested course only. A generic name like
        # "Text Field 5026" exists in several course configs meaning completely
        # different questions, so borrowing another course's mapping invents
        # questions that aren't in this portfolio (e.g. a Level 3 V2 programme
        # overview cell being reported as a Level 2 "Unit 4" SMART goal).
        entry = owners.get(course_id)
        if not entry:
            continue

        unit_id, unit_label, question_label, order = entry

        # Skip if unit is not in selected units (if filtering is active)
        if selected_units is not None and unit_id not in selected_units:
            continue

        try:
            value_str = _coerce_field_value(field.get('/V'))
        except Exception:
            value_str = ''

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
            'unit_id': unit_id,
            'unit': unit_label,
            'question': question_label,
            'answer': value_str,
            'course': course_id,
            'order': order,
        })

    # Return in natural unit -> question order (PDF field order is scrambled).
    answers.sort(key=lambda a: a['order'])
    return answers


# ── ANALYSIS FUNCTIONS ────────────────────────────────────────────────────────

# Zero GPT returns two independent signals:
#   feedback       - its document-level verdict, one of nine graded strings.
#   fakePercentage - a document-level AI score. Confirmed with Zero GPT support
#                    that this is NOT what feedback is derived from, and it is
#                    not the same as aiWords/textWords (a 38-word answer came
#                    back with fakePercentage 24.0 but aiWords 14/38 = 36.8%).
# The verdict is the primary signal; the percentage is supporting detail.
#
# Matching must be case-insensitive - the API mixes casing across values
# ("Your Text is Human Written" but "Your Text is Most Likely AI/GPT generated")
# - and must prefer the longest match, because the middle values contain the
# outer ones as substrings ("Most Likely Human written, may include parts
# generated by AI/GPT" contains both "human written" and "ai/gpt generated").
FEEDBACK_SCALE = (
    ('human written', 0),
    ('most likely human written', 1),
    ('most likely human written, may include parts generated by ai/gpt', 2),
    ('likely human written, may include parts generate by ai/gpt', 3),
    ('contains mixed signals, with some parts generated by ai/gpt', 4),
    ('likely generated by ai/gpt', 5),
    ('most likely ai/gpt generated', 6),
    ('most of your text is ai/gpt generated', 7),
    ('ai/gpt generated', 8),
)

# Verdict level -> the classification labels the report and UI already use.
FEEDBACK_LEVEL_CLASSIFICATION = {
    0: 'Human', 1: 'Human',
    2: 'AI Polished', 3: 'AI Polished',   # human-written but with AI parts
    4: 'Mixed',
    5: 'AI', 6: 'AI', 7: 'AI', 8: 'AI',
}

# Zero GPT returns a confident-sounding default verdict for input it cannot
# parse: "N/A" and "60 sec" both come back as textWords 0 with the feedback
# "Your Text is Most Likely AI/GPT generated". Any result at or below this word
# count is treated as unassessable rather than as a detection.
MIN_ASSESSABLE_WORDS = 5

# Below this many assessable answers a portfolio percentage is not meaningful
# enough to publish as a headline figure.
MIN_ASSESSED_FOR_SCORE = 10

# Escalation from "review the flagged answers" to "review the whole portfolio".
# Either enough outright-AI verdicts to be a pattern in absolute terms, or
# enough as a share of the portfolio to be one in a small submission.
AI_VERDICTS_FOR_DETAILED_REVIEW = 8
AI_PCT_FOR_DETAILED_REVIEW = 10


def classify_feedback(feedback):
    """Map a Zero GPT feedback string to (level 0-8, classification).

    Returns (None, None) when the string matches nothing known, so the caller
    can fall back rather than silently mis-bin an unrecognised verdict.
    """
    if not feedback:
        return None, None
    lowered = feedback.lower()
    best_pattern, best_level = None, None
    for pattern, level in FEEDBACK_SCALE:
        if pattern in lowered and (best_pattern is None or len(pattern) > len(best_pattern)):
            best_pattern, best_level = pattern, level
    if best_level is None:
        return None, None
    return best_level, FEEDBACK_LEVEL_CLASSIFICATION[best_level]


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
        # Retry logic: re-check the answer until Zero GPT returns a *valid* result.
        # Crucially, the empty/invalid-response check lives INSIDE the loop, so a
        # 200 with an empty body is retried rather than failing immediately.
        # Retried: timeouts, connection errors, transient 429/5xx, and empty bodies.
        # Not retried: genuine 4xx (e.g. bad API key) — those won't change on retry.
        max_retries = 4
        base_delay = 1  # seconds; exponential backoff capped at 10s

        response_data = None
        data = {}
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

                # Fail fast on client errors that won't change on retry (bad key,
                # bad request). Zero GPT explains these in the body - as
                # {"message": ...} or {"detail": "Invalid API Key"} - so lift
                # that out rather than reporting a bare status code.
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    try:
                        body = response.json()
                    except ValueError:
                        body = {}
                    detail = body.get('message') or body.get('detail')
                    if detail:
                        raise ZeroGptAPIError(detail, response.status_code)
                    response.raise_for_status()

                # Transient server error or rate limit - retry
                if response.status_code in (429, 500, 502, 503, 504):
                    raise ValueError(f"Transient {response.status_code} from Zero GPT API")

                # Parse and validate response inside the loop so empty/invalid
                # bodies are re-checked instead of being accepted as final.
                # Zero GPT returns: { "success": true, "data": { "fakePercentage": 0-100, "feedback": "...", ... } }
                response_data = response.json()

                # Zero GPT signals its own refusals (no credits, bad key) as a
                # 200 with success:false and a message. Surface that message and
                # fail fast - retrying cannot conjure up credits, and the old
                # code spent ~16s of backoff per answer before reporting it as
                # an "empty response", which read like a server fault.
                if response_data.get('success') is False:
                    raise ZeroGptAPIError(
                        response_data.get('message') or 'Zero GPT rejected the request',
                        response_data.get('code'),
                    )

                data = response_data.get('data', {})
                if not data or not data.get('feedback'):
                    raise ValueError("Empty or invalid response from Zero GPT API")

                break  # Valid result received, exit retry loop

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, ValueError) as e:
                # Timeout, connection error, transient status, JSON parse error,
                # or empty body - retry with exponential backoff.
                if attempt < max_retries:
                    time.sleep(min(base_delay * (2 ** attempt), 10))
                    continue
                else:
                    # All retries exhausted
                    raise

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

        # Zero GPT reports textWords 0 for input it cannot parse, while still
        # returning a confident-sounding verdict. Treat those as unassessable
        # instead of letting the default verdict reach the report as a detection.
        assessable = text_words >= MIN_ASSESSABLE_WORDS

        feedback_level, classification = classify_feedback(feedback)

        if not assessable:
            feedback_level = None
            classification = 'Insufficient Text'
            confidence = 0.0
        elif classification is not None:
            # Confidence rises towards the ends of the scale, where Zero GPT's
            # wording is unqualified ("is Human Written" / "is AI/GPT Generated")
            # and falls in the hedged middle ("Most Likely...", "may include").
            confidence = 0.9 - 0.05 * (4 - abs(feedback_level - 4))
        else:
            # Unrecognised feedback string - fall back to the percentage, but
            # record that we did so rather than presenting it as a verdict.
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
            'feedback_level': feedback_level,
            'assessable': assessable,
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
            'feedback_level': None,
            'assessable': False,
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
            'feedback_level': None,
            'assessable': False,
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
    except ZeroGptAPIError as e:
        logging.error(f"Zero GPT refused the request: {e.api_message} (code {e.code})")
        return {
            'overall_classification': 'Unknown',
            'feedback_level': None,
            'assessable': False,
            'ai_percentage': 0,
            'human_percentage': 0,
            'ai_polished_percentage': 0,
            'confidence': 0.0,
            'overall_verdict': f'Detection unavailable: {e.api_message}',
            'feedback': f'Error: {e.api_message}',
            'ai_signals': [],
            'ai_flagged_sentences': [],
            'text_words': 0,
            'ai_words': 0,
            'error': True,
            # Tells the stream to stop rather than repeat this for every answer.
            'account_error': e.is_account_error,
            'account_error_message': e.api_message,
        }
    except Exception as e:
        return {
            'overall_classification': 'Unknown',
            'feedback_level': None,
            'assessable': False,
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
                'feedback_level': None,
                'assessable': False,
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
                # Carried through so the stream can abort on account-level
                # failures (no credits, bad key) instead of retrying each answer.
                'account_error': zerogpt_result.get('account_error', False),
                'account_error_message': zerogpt_result.get('account_error_message'),
            }

        # Get base confidence from Zero GPT result
        base_confidence = zerogpt_result.get('confidence', 0.5)

        # A short answer makes the verdict less certain, so it still damps
        # confidence - but it no longer removes the answer from the portfolio
        # score. Scoring on the graded verdict rather than on fakePercentage
        # means short answers remain usable; only genuinely unassessable ones
        # (Zero GPT parsed too few words) are excluded, via `assessable`.
        if not zerogpt_result.get('assessable', True):
            adjusted_confidence = 0.0
            low_confidence_flag = True
            confidence_note = 'ℹ️ Not assessed — too little text for a reliable verdict'
        elif word_count < 50:
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
        elif classification == 'AI Polished':
            risk_level = 'Low'
        elif classification == 'Human':
            risk_level = 'Human'
        else:
            risk_level = 'Unknown'

        ai_pct = zerogpt_result.get('ai_percentage', 0)

        return {
            'overall_classification': classification,
            'feedback_level': zerogpt_result.get('feedback_level'),
            'assessable': zerogpt_result.get('assessable', False),
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

        # An account-level failure (no credits, rejected key) will hit every
        # remaining answer identically, so stop and say so once. Grinding on
        # would produce a report of nothing but "Unknown" rows, which reads
        # like the workbook was clean rather than like the check never ran.
        if detection.get('account_error'):
            reason = detection.get('account_error_message') or 'the detection service rejected the request'
            yield json.dumps({
                'type': 'fatal',
                'error_title': 'Analysis stopped - detection service unavailable',
                'error': (
                    f'Zero GPT stopped accepting requests after {idx - 1} of {len(answers)} answers: '
                    f'"{reason}". No report has been produced. This is an account or API key '
                    'problem rather than a fault with the workbook - check the Zero GPT account '
                    'balance, then run the workbook again.'
                ),
            }) + '\n'
            return

        gpt_calls += 1

        result = {
            'unit': a['unit'],
            'question': a['question'],
            'answer_preview': a['answer'][:300] + ('...' if len(a['answer']) > 300 else ''),
            'answer_full': a['answer'],
            'word_count': len(a['answer'].split()),
            'order': a.get('order', idx),
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
    
    # Sort results into natural unit -> question order for the final report
    results.sort(key=lambda x: x.get('order', 0))

    # ── PORTFOLIO-LEVEL SCORING ────────────────────────────────────────────────
    # Scoring is driven by Zero GPT's graded verdict, not by averaging
    # fakePercentage. The percentage is near-binary on the short answers that
    # dominate these portfolios (one sample had 66 of 74 answers at exactly 0%
    # and 6 at exactly 100%), so its mean was decided by a handful of answers -
    # in that sample, two - while the verdicts on the rest were discarded.
    #
    # The only answers excluded now are those Zero GPT could not assess at all.
    assessed = [r for r in results if r.get('assessable') and not r.get('error')]
    unassessable_count = len(results) - len(assessed)

    ai_count = sum(1 for r in assessed if r.get('overall_classification') == 'AI')
    ai_polished_count = sum(1 for r in assessed if r.get('overall_classification') == 'AI Polished')
    mixed_count = sum(1 for r in assessed if r.get('overall_classification') == 'Mixed')
    human_count = sum(1 for r in assessed if r.get('overall_classification') == 'Human')

    # Anything Zero GPT did not call plainly human is worth an assessor's eye.
    flagged_count = ai_count + ai_polished_count + mixed_count
    flagged_pct = round(flagged_count / len(assessed) * 100, 1) if assessed else 0
    ai_verdict_pct = round(ai_count / len(assessed) * 100, 1) if assessed else 0

    # Short answers are still counted and shown, but no longer excluded.
    short_answer_count = sum(1 for r in results if r.get('word_count', 0) < 50)
    short_answer_pct = round((short_answer_count / len(results)) * 100, 1) if results else 0

    # There is deliberately no portfolio percentage. Averaging the verdict scale
    # produces a number with no natural meaning, and any single figure invites
    # being read as a grade for the learner. The portfolio-level answer is the
    # review level and the per-answer verdicts behind it.

    # Kept as supporting detail only - see the note above on why it is not the
    # headline figure.
    avg_ai_pct = round(sum(r.get('ai_percentage', 0) for r in assessed) / len(assessed)) if assessed else 0
    avg_ai_polished_pct = 0  # Simplified display: always 0 in new system
    avg_human_pct = round(sum(r.get('human_percentage', 0) for r in assessed) / len(assessed)) if assessed else 0

    portfolio_confidence = round(
        sum(r.get('adjusted_confidence', 0.5) for r in assessed) / len(assessed)
        if assessed else 0.5,
        3
    )

    # Risk is banded on how many answers carry an AI verdict rather than on the
    # mean, because a proportion degrades gracefully when answers are short
    # whereas a mean of a near-binary percentage does not.
    # Bands describe how much reviewing to do, not what the learner did. This
    # tool feeds decisions that can end in a misconduct process, and a wrong
    # "High AI Content" reads as an accusation, whereas a wrong "Review
    # Required" only costs the assessor some reading. So the wording is
    # action-first and the thresholds are deliberately blunt.
    #
    # Banding is driven by the *count* of outright-AI verdicts rather than by a
    # percentage. These portfolios are mostly very short answers, where a single
    # verdict is weak evidence and a proportion over a small base swings on one
    # answer; several AI verdicts is a real signal, because noise does not
    # cluster. Percentage only escalates, never decides on its own.
    enough_to_score = len(assessed) >= MIN_ASSESSED_FOR_SCORE
    if not enough_to_score:
        portfolio_risk = 'Insufficient Data'
    elif ai_count >= AI_VERDICTS_FOR_DETAILED_REVIEW or ai_verdict_pct >= AI_PCT_FOR_DETAILED_REVIEW:
        portfolio_risk = 'Detailed Review'
    elif ai_count >= 1:
        portfolio_risk = 'Review Required'
    elif flagged_count > 0:
        portfolio_risk = 'Spot Check'
    else:
        portfolio_risk = 'No Indicators'

    notes = []
    if unassessable_count:
        notes.append(
            f"{unassessable_count} answer{'s' if unassessable_count > 1 else ''} could not be "
            f"assessed (too little text to judge reliably) and {'are' if unassessable_count > 1 else 'is'} "
            f"excluded from the counts above."
        )
    if not enough_to_score:
        notes.append(
            f"Only {len(assessed)} answer{'s' if len(assessed) != 1 else ''} could be assessed — "
            f"too few for a reliable portfolio score. Review the flagged answers individually."
        )
    elif short_answer_count:
        notes.append(
            f"{short_answer_count} answer{'s are' if short_answer_count > 1 else ' is'} under 50 words; "
            f"verdicts on short answers are less reliable."
        )
    quality_note = ' '.join(notes)

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
            'confidence_note': r.get('confidence_note', ''),
            # Zero GPT's own sentence-level hits. Already paid for on every call
            # and far more actionable for an assessor than a portfolio number,
            # because it points at which sentence to read.
            'ai_flagged_sentences': r.get('ai_flagged_sentences', []),
            'assessable': r.get('assessable', False),
            'word_count': r.get('word_count', 0),
            'order': r.get('order', 0),
        })

    # Resolve the report's own wording here so the PDF and the on-screen
    # results cannot render different text for the same analysis.
    _core = {
        'portfolio_risk': portfolio_risk,
        'assessed_count': len(assessed),
        'total_answers': len(results),
        'ai_count': ai_count,
        'mixed_count': mixed_count,
        'ai_polished_count': ai_polished_count,
        'human_count': human_count,
        'unassessable_count': unassessable_count,
        'flagged_count': flagged_count,
        'short_answer_count': short_answer_count,
        'quality_note': quality_note,
    }
    review_accent, review_title, review_subtitle = review_level(portfolio_risk)
    presentation = {
        'review_accent': review_accent,
        'review_title': review_title,
        'review_subtitle': review_subtitle,
        'recommendation': build_recommendation(_core),
        'notes': build_notes(_core),
        'verdict_mix': [
            {'label': l, 'count': c, 'colour': col} for l, c, col in verdict_mix(_core)
        ],
        'verdict_mix_bar': [
            {'label': l, 'count': c, 'colour': col} for l, c, col in verdict_mix_bar(_core)
        ],
        'reading_guide': [{'term': t, 'body': b} for t, b in READING_GUIDE],
        'classification_style': {
            k: {'colour': v[0], 'label': v[1]} for k, v in CLASSIFICATION_STYLE.items()
        },
        'flagged_group_labels': FLAGGED_GROUP_LABELS,
    }

    # Keep the coarse badge in step with the banded portfolio risk.
    overall_risk = {
        'Detailed Review': 'high',
        'Review Required': 'medium',
        'Spot Check': 'low',
        'No Indicators': 'unlikely',
        'Insufficient Data': 'unlikely',
    }.get(portfolio_risk, 'unlikely')

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
            'assessed_count': len(assessed),
            'unassessable_count': unassessable_count,
            'ai_count': ai_count,
            'ai_polished_count': ai_polished_count,
            'mixed_count': mixed_count,
            'human_count': human_count,
            'flagged_count': flagged_count,
            'flagged_pct': flagged_pct,
            'ai_verdict_pct': ai_verdict_pct,
            'avg_ai_percentage': avg_ai_pct,
            'portfolio_confidence': portfolio_confidence,
            'portfolio_risk': portfolio_risk,
            'enough_to_score': enough_to_score,
            'short_answer_count': short_answer_count,
            'short_answer_pct': short_answer_pct,
            'quality_note': quality_note,
            'risk_breakdown': risk_breakdown,
            **presentation,
        }
    }
    # Persist to a shared on-disk store so any gunicorn worker can serve the
    # later /download-report request (an in-memory dict is per-worker and caused
    # ~half of downloads to 404 with "File wasn't available on site").
    store_analysis_report(session_id, analysis_for_pdf)

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
            'assessed_count': len(assessed),
            'unassessable_count': unassessable_count,
            'ai_count': ai_count,
            'ai_polished_count': ai_polished_count,
            'mixed_count': mixed_count,
            'human_count': human_count,
            'flagged_count': flagged_count,
            'flagged_pct': flagged_pct,
            'ai_verdict_pct': ai_verdict_pct,
            'avg_ai_percentage': avg_ai_pct,
            'avg_ai_polished_percentage': avg_ai_polished_pct,
            'avg_human_percentage': avg_human_pct,
            'overall_risk': overall_risk,
            # Portfolio-level scoring
            'portfolio_confidence': portfolio_confidence,
            'portfolio_risk': portfolio_risk,
            'enough_to_score': enough_to_score,
            'short_answer_count': short_answer_count,
            'short_answer_pct': short_answer_pct,
            'quality_note': quality_note,
            'risk_breakdown': risk_breakdown,
            **presentation,
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
        # Extract everything the course maps first, so a mismatched workbook can
        # be told apart from a unit selection that simply has no answers in it.
        all_answers = extract_answers_from_pdf(tmp_path, course_id)

        if not all_answers:
            # Previously this fell back to scraping every form field in the PDF.
            # That produced a report of unlabelled "Field: Text Field 1106" rows
            # with no unit, containing assessor feedback as well as learner work,
            # and - because the fallback has no unit mapping - it ignored the
            # selected units and analysed the whole workbook. A wrong report is
            # worse than none, and the real cause is nearly always the wrong
            # qualification or portfolio version, so say that instead.
            course_name = COURSES_CONFIG['courses'][course_id]['name']
            return jsonify({
                'error': (
                    f'No answers matched "{course_name}". This usually means the workbook is '
                    'a different qualification or portfolio version than the one selected. '
                    'Use "Which version do I have?" to check the front cover against the '
                    'options, then try again. If the version is correct, the workbook may be '
                    'blank or scanned rather than a fillable PDF.'
                ),
                'error_title': 'Workbook does not match the selected qualification',
                'is_mismatch': True,
            }), 200

        if selected_units:
            answers = [a for a in all_answers if a.get('unit_id') in selected_units]
            if not answers:
                unit_labels = COURSES_CONFIG['courses'][course_id]['units']
                chosen = ', '.join(unit_labels[u]['label'] for u in selected_units
                                   if u in unit_labels) or 'the selected units'
                return jsonify({
                    'error': (
                        f'The workbook matched "{COURSES_CONFIG["courses"][course_id]["name"]}", '
                        f'but no answers were found in {chosen}. Those units may be unanswered — '
                        'try a full workbook analysis, or select different units.'
                    ),
                    'error_title': 'No answers in the selected units',
                    'is_blank': True,
                }), 200
        else:
            answers = all_answers

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


# ── SHARED ANALYSIS REPORT STORE ────────────────────────────────────────────────
# Report data for PDF downloads is persisted to disk rather than an in-process
# dict because the app runs multiple gunicorn workers (see Dockerfile). The
# analysis (SSE) request and the later /download-report request can be routed to
# different workers, so an in-memory cache caused intermittent 404s.

REPORT_STORE_DIR = Path(tempfile.gettempdir()) / 'ai_detector_reports'
REPORT_STORE_TTL = 24 * 60 * 60  # seconds; reports older than this are pruned


def _report_path(session_id):
    # session_id is a secrets.token_urlsafe string; strip anything else to be
    # safe against path traversal before using it as a filename.
    safe = re.sub(r'[^A-Za-z0-9_-]', '', session_id or '')
    return REPORT_STORE_DIR / f'{safe}.json'


def _prune_old_reports():
    """Delete stored reports older than REPORT_STORE_TTL."""
    try:
        now = time.time()
        for p in REPORT_STORE_DIR.glob('*.json'):
            try:
                if now - p.stat().st_mtime > REPORT_STORE_TTL:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def store_analysis_report(session_id, analysis_data):
    """Persist analysis data for later PDF download (shared across workers)."""
    REPORT_STORE_DIR.mkdir(parents=True, exist_ok=True)
    _prune_old_reports()
    with open(_report_path(session_id), 'w', encoding='utf-8') as f:
        json.dump(analysis_data, f)


def load_analysis_report(session_id):
    """Load previously stored analysis data, or None if missing/unreadable."""
    if not session_id:
        return None
    try:
        with open(_report_path(session_id), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


@app.route('/training-deck', methods=['GET'])
def training_deck():
    """Assessor briefing deck, served inline so the Help button opens it in a tab."""
    deck = Path(__file__).parent / 'static' / 'AI_Detection_Report_Training_Deck.pdf'
    if not deck.exists():
        return jsonify({'error': 'Training deck not available.'}), 404
    return Response(
        deck.read_bytes(),
        mimetype='application/pdf',
        headers={'Content-Disposition': 'inline; filename="PT Academy - AI Detection Report - Assessor Briefing.pdf"'},
    )


@app.route('/report-preview/<session_id>', methods=['GET'])
def report_preview(session_id):
    """Serve the same PDF inline so the UI can embed it.

    The on-screen results and the PDF drifted apart once before - the UI kept
    showing classifications and wording the report had moved on from. Embedding
    the real PDF rather than restyling HTML to imitate it means there is only
    ever one rendering to maintain, so they cannot disagree again.
    """
    try:
        from analysis.pdf_generator import generate_pdf_report

        analysis_data = load_analysis_report(session_id)
        if not analysis_data:
            return jsonify({'error': 'No analysis data found for this session.'}), 404

        return Response(
            generate_pdf_report(analysis_data),
            mimetype='application/pdf',
            headers={'Content-Disposition': 'inline; filename="report.pdf"'},
        )
    except Exception as e:
        return jsonify({'error': f'Failed to generate report preview: {str(e)}'}), 500


@app.route('/download-report/<session_id>', methods=['GET'])
def download_report(session_id):
    """Download the assessor report for an analysis."""
    return _serve_report(session_id, audience='assessor')


@app.route('/download-report/<session_id>/learner', methods=['GET'])
def download_learner_report(session_id):
    """Download the learner-facing report - safe to share with the learner."""
    return _serve_report(session_id, audience='learner')


def _serve_report(session_id, audience):
    try:
        from analysis.pdf_generator import generate_pdf_report

        # Retrieve analysis from the shared on-disk store
        analysis_data = load_analysis_report(session_id)

        if not analysis_data:
            return jsonify({'error': 'No analysis data found for this session. The analysis may have expired.'}), 404

        pdf_bytes = generate_pdf_report(analysis_data, audience=audience)

        # Note: the stored report is intentionally NOT deleted here so the mentor
        # can re-download. Old reports are pruned by TTL in store_analysis_report.

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

        doc_label = 'Learner Copy' if audience == 'learner' else 'AI Report'
        base = (f"{learner}_{course}_{doc_label}_{date_str}" if audience == 'learner'
                else f"{learner}_{course}_{doc_label}_{mode_suffix}_{date_str}")
        safe_name = re.sub(r'[^\w\s\-]', '', base).strip()
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
