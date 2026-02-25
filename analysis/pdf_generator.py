"""
PDF Report Generator for PT Detector Analysis Results

Generates a one-page PDF report with:
- Portfolio score card with AI percentage, confidence, and risk level
- Risk summary table (count by risk level)
- Answer summary table (unit, question, risk level, AI%, confidence)
- Assessor recommendations based on portfolio risk
"""

from datetime import datetime
from io import BytesIO
from pathlib import Path
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT


def get_risk_color(risk_level):
    """Return color for risk level badge."""
    colors_map = {
        'High': colors.red,
        'Medium': colors.orange,
        'Low': colors.yellow,
        'Human': colors.green,
    }
    return colors_map.get(risk_level, colors.grey)


def generate_pdf_report(results):
    """
    Generate a PDF report from analysis results.

    Args:
        results: Dict containing:
            - results: List of answer analysis results
            - summary: Portfolio summary with portfolio_score, portfolio_confidence, portfolio_risk, etc.

    Returns:
        bytes: PDF document as bytes
    """
    # Extract data
    answer_results = results.get('results', [])
    summary = results.get('summary', {})
    learner_name = results.get('learner_name', 'Unknown Learner')

    portfolio_score = summary.get('portfolio_score', 0)
    portfolio_confidence = summary.get('portfolio_confidence', 0.5)
    portfolio_risk = summary.get('portfolio_risk', 'Unknown')
    short_answer_count = summary.get('short_answer_count', 0)
    short_answer_pct = summary.get('short_answer_pct', 0)
    quality_note = summary.get('quality_note', '')
    risk_breakdown = summary.get('risk_breakdown', {})

    # Create PDF document
    pdf_buffer = BytesIO()
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    # PT Academy Brand Colors
    PRIMARY_COLOR = '#030303'      # Dark/Black
    GOLD_COLOR = '#c6a906'          # Gold accent
    LIGHT_GRAY = '#d8d8d8'          # Light gray
    WHITE = '#ffffff'               # White

    # Custom styles with PT Academy branding
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor(PRIMARY_COLOR),
        spaceAfter=6,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold',
    )
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=12,
        textColor=colors.HexColor(WHITE),
        spaceAfter=8,
        spaceBefore=8,
        fontName='Helvetica-Bold',
    )
    small_text = ParagraphStyle(
        'SmallText',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor('#666666'),
    )

    # Build document content
    elements = []

    # ── HEADER WITH LOGO ────────────────────────────────────────────────────
    # Try to add logo if it exists
    logo_path = Path(__file__).parent.parent / 'logo.png'
    if logo_path.exists():
        try:
            logo = Image(str(logo_path), width=1.2*inch, height=1.2*inch)
            elements.append(logo)
            elements.append(Spacer(1, 0.1 * inch))
        except Exception as e:
            print(f"Warning: Could not load logo: {e}")

    title = Paragraph('PT Academy AI Detection Report', title_style)
    elements.append(title)

    learner_para = Paragraph(f'<b>Learner:</b> {learner_name}', ParagraphStyle('LearnerName', parent=styles['Normal'], fontSize=11, textColor=colors.HexColor(PRIMARY_COLOR), spaceAfter=4, fontName='Helvetica-Bold'))
    elements.append(learner_para)

    analysis_date = datetime.now().strftime('%d %B %Y at %H:%M')
    date_para = Paragraph(f'<i>Report Generated: {analysis_date}</i>', small_text)
    elements.append(date_para)

    # Recommendation headline based on portfolio score
    if portfolio_score >= 65:
        recommendation_headline = '⚠️ High AI Content Detected — Review Required'
        recommendation_color = colors.HexColor('#ff4f6a')  # Red
    elif portfolio_score >= 35:
        recommendation_headline = '🔍 Moderate AI Content Detected'
        recommendation_color = colors.HexColor('#c6a906')  # Gold/Amber
    elif portfolio_score >= 20:
        recommendation_headline = '🔎 Some AI Content Detected — Worth Reviewing'
        recommendation_color = colors.HexColor('#c6a906')  # Gold/Amber
    else:
        recommendation_headline = '✅ Likely Human-Written'
        recommendation_color = colors.HexColor('#22c97a')  # Green

    recommendation_para = Paragraph(
        f'<b>{recommendation_headline}</b>',
        ParagraphStyle('Recommendation', parent=styles['Normal'], fontSize=12, textColor=recommendation_color, spaceAfter=4, fontName='Helvetica-Bold')
    )
    elements.append(recommendation_para)

    elements.append(Spacer(1, 0.15 * inch))

    # ── PORTFOLIO SCORE CARD ────────────────────────────────────────────
    elements.append(Paragraph('Portfolio Score', heading_style))

    # Count AI and Human classified answers
    ai_count = sum(1 for r in answer_results if r.get('overall_classification') == 'AI')
    human_count = sum(1 for r in answer_results if r.get('overall_classification') == 'Human')

    # Score card table - simplified to match frontend
    score_card_data = [
        ['Metric', 'Value'],
        ['Average AI Percentage', f'{portfolio_score}%'],
        ['Total Answers Analyzed', f'{len(answer_results)}'],
        ['AI Classified', f'{ai_count}'],
        ['Human Classified', f'{human_count}'],
        ['Short Answers (<50 words)', f'{short_answer_count} ({short_answer_pct}%)'],
    ]

    score_table = Table(score_card_data, colWidths=[2.5 * inch, 2.0 * inch])
    score_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(GOLD_COLOR)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor(PRIMARY_COLOR)),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor(WHITE)),
        ('GRID', (0, 0), (-1, -1), 1.5, colors.HexColor(GOLD_COLOR)),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor(WHITE), colors.HexColor(LIGHT_GRAY)]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(score_table)
    elements.append(Spacer(1, 0.12 * inch))

    # Quality note if present
    if quality_note:
        quality_para = Paragraph(
            f'<b>⚠️ Quality Note:</b> {quality_note}',
            ParagraphStyle('QualityNote', parent=styles['Normal'], fontSize=8, textColor=colors.red)
        )
        elements.append(quality_para)
        elements.append(Spacer(1, 0.1 * inch))


    # ── ANSWER SUMMARY TABLE (selected answers only) ─────────────────────
    elements.append(Paragraph('Answer-by-Answer Analysis', heading_style))
    elements.append(Paragraph('<i>Sorted by AI Percentage (highest first)</i>', small_text))
    elements.append(Spacer(1, 0.08 * inch))

    # Sort answers by AI percentage (descending)
    sorted_answers = sorted(answer_results, key=lambda x: x.get('ai_percentage', 0), reverse=True)

    # Create detailed answer breakdown
    for idx, result in enumerate(sorted_answers, 1):
        unit = result.get('unit', 'Unknown')
        question = result.get('question', '')
        answer_text = result.get('answer_full', '')  # Full answer text from storage
        ai_pct = result.get('ai_percentage', 0)
        feedback = result.get('feedback', 'No feedback available')  # Zero GPT feedback
        classification = result.get('overall_classification', 'Unknown')

        # Create answer section - use full question (no truncation)
        answer_heading = Paragraph(
            f'<b>{idx}. {unit}</b><br/><b>Question:</b> {question}',
            ParagraphStyle('AnswerHeading', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor(GOLD_COLOR), spaceAfter=6, leading=11, fontName='Helvetica-Bold')
        )
        elements.append(answer_heading)

        # AI Score and Classification
        score_text = Paragraph(
            f'<b>AI Probability:</b> <b>{ai_pct}%</b> | <b>Classification:</b> {classification}',
            ParagraphStyle('ScoreText', parent=styles['Normal'], fontSize=9, spaceAfter=2, fontName='Helvetica-Bold', textColor=colors.HexColor(PRIMARY_COLOR))
        )
        elements.append(score_text)

        # Feedback from Zero GPT
        feedback_text = Paragraph(
            f'<b>Verdict:</b> <i>{feedback}</i>',
            ParagraphStyle('FeedbackText', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor(PRIMARY_COLOR), spaceAfter=4)
        )
        elements.append(feedback_text)

        # Answer text (truncate if extremely long to fit on page)
        answer_preview = answer_text[:1000] + '...' if len(answer_text) > 1000 else answer_text
        answer_para = Paragraph(
            f"<b>Learner's Answer:</b> {answer_preview}",
            ParagraphStyle('AnswerText', parent=styles['Normal'], fontSize=7, spaceAfter=8, leading=9)
        )
        elements.append(answer_para)

        # Add divider between answers (except last one)
        if idx < len(sorted_answers):
            elements.append(Spacer(1, 0.06 * inch))
            divider = Paragraph('─' * 80, small_text)
            elements.append(divider)
            elements.append(Spacer(1, 0.06 * inch))

    elements.append(Spacer(1, 0.12 * inch))

    # ── RECOMMENDATIONS ────────────────────────────────────────────────
    elements.append(Paragraph('Assessor Recommendations', heading_style))

    if portfolio_score >= 65:
        recommendation = (
            '<b>⚠️ High AI Content (≥65%):</b> This portfolio shows significant evidence of AI-generated content. '
            'Please review all answers, especially those with high AI percentages. '
            'Consider requesting evidence of independent work through additional questioning or practical assessment.'
        )
    elif portfolio_score >= 35:
        recommendation = (
            '<b>🔍 Moderate AI Content (35-64%):</b> This portfolio contains some AI-generated content. '
            'Review answers with higher AI percentages carefully. '
            'Discuss findings with the learner to verify understanding and authenticity of work.'
        )
    elif portfolio_score >= 20:
        recommendation = (
            '<b>🔎 Some AI Content (20-34%):</b> This portfolio shows minor AI indicators. '
            'The work appears mostly human-written with some potential AI assistance. '
            'Monitor flagged answers and discuss with learner if needed.'
        )
    else:
        recommendation = (
            '<b>✅ Likely Human-Written (<20%):</b> All answers appear to be genuine human-written work. '
            'Portfolio shows strong integrity with no significant AI detection signals.'
        )

    rec_para = Paragraph(recommendation, styles['Normal'])
    elements.append(rec_para)

    elements.append(Spacer(1, 0.12 * inch))

    # ── FOOTER ──────────────────────────────────────────────────────────
    footer_text = (
        '<font size="6"><i>This report was generated by PT Detector, an AI detection system using Zero GPT AI detection. '
        'Results should be considered alongside other assessment evidence and learner context.</i></font>'
    )
    footer_para = Paragraph(footer_text, styles['Normal'])
    elements.append(footer_para)

    # Build PDF
    doc.build(elements)
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()
