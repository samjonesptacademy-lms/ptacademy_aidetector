"""
PDF Report Generator for PT Detector Analysis Results

Structure:
- Summary page: review level, headline figures, verdict mix, what to do
- Flagged answers: one card each, with the sentences the detector identified
- Human-classified answers: compact table (there are usually dozens; a full card
  for each buried the flagged ones)
- Not assessed: compact table

Colour encodes *review priority*, not classification - three tiers plus a neutral
for "no result". The precise classification is always carried by its text label,
so colour never has to be read on its own. The tiers were validated for colour
vision deficiency; green was rejected for the "clear" tier because green against
the attention orange fails protanopia separation, hence blue.
"""

import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from analysis.report_content import (
    BRAND_GOLD, CLASSIFICATION_STYLE, FLAGGED_GROUP_LABELS, INK, INK_MUTED,
    INK_SOFT, PRIORITY_ATTENTION, PRIORITY_CLEAR, PRIORITY_CRITICAL,
    PRIORITY_NONE, READING_GUIDE, RULE, SURFACE, UNASSESSED, WHITE,
    build_notes, build_recommendation, flagged_sort_key, review_level,
    verdict_mix, verdict_mix_bar,
)

# Render the routine sections as a compact table rather than as full cards.
# These answers need no action, so they are listed for completeness only and
# should take as little space as possible. Set False to show the verdict and
# full answer text for every answer instead.
COMPACT_ROUTINE_SECTIONS = True


def get_risk_color(risk_level):
    """Return colour for a risk level. Retained for backwards compatibility."""
    return colors.HexColor({
        'High': PRIORITY_CRITICAL,
        'Medium': PRIORITY_ATTENTION,
        'Low': BRAND_GOLD,
        'Human': PRIORITY_CLEAR,
    }.get(risk_level, PRIORITY_NONE))


def _styles():
    """Paragraph styles used throughout the report."""
    base = getSampleStyleSheet()
    s = {}
    s['title'] = ParagraphStyle(
        'Title', parent=base['Normal'], fontName='Helvetica-Bold', fontSize=17,
        textColor=colors.HexColor(INK), leading=20, spaceAfter=1)
    s['subtitle'] = ParagraphStyle(
        'Subtitle', parent=base['Normal'], fontName='Helvetica', fontSize=8.5,
        textColor=colors.HexColor(INK_SOFT), leading=12)
    s['section'] = ParagraphStyle(
        'Section', parent=base['Normal'], fontName='Helvetica-Bold', fontSize=12,
        textColor=colors.HexColor(INK), leading=15)
    s['eyebrow'] = ParagraphStyle(
        'Eyebrow', parent=base['Normal'], fontName='Helvetica-Bold', fontSize=6.5,
        textColor=colors.HexColor(INK_MUTED), leading=9)
    s['caption'] = ParagraphStyle(
        'Caption', parent=base['Normal'], fontName='Helvetica', fontSize=7.5,
        textColor=colors.HexColor(INK_MUTED), leading=10.5)
    s['body'] = ParagraphStyle(
        'Body', parent=base['Normal'], fontName='Helvetica', fontSize=8.5,
        textColor=colors.HexColor(INK), leading=12)
    s['bodysoft'] = ParagraphStyle(
        'BodySoft', parent=base['Normal'], fontName='Helvetica', fontSize=8,
        textColor=colors.HexColor(INK_SOFT), leading=11)
    s['question'] = ParagraphStyle(
        'Question', parent=base['Normal'], fontName='Helvetica-Bold', fontSize=9.5,
        textColor=colors.HexColor(INK), leading=12.5)
    s['answer'] = ParagraphStyle(
        'Answer', parent=base['Normal'], fontName='Helvetica', fontSize=8,
        textColor=colors.HexColor(INK_SOFT), leading=11)
    s['flagged'] = ParagraphStyle(
        'Flagged', parent=base['Normal'], fontName='Helvetica', fontSize=8,
        textColor=colors.HexColor(PRIORITY_CRITICAL), leading=11, leftIndent=7)
    s['statnum'] = ParagraphStyle(
        'StatNum', parent=base['Normal'], fontName='Helvetica-Bold', fontSize=19,
        textColor=colors.HexColor(INK), leading=21)
    s['statlabel'] = ParagraphStyle(
        'StatLabel', parent=base['Normal'], fontName='Helvetica', fontSize=7.5,
        textColor=colors.HexColor(INK_MUTED), leading=10)
    s['banner'] = ParagraphStyle(
        'Banner', parent=base['Normal'], fontName='Helvetica-Bold', fontSize=14,
        textColor=colors.HexColor(INK), leading=17)
    s['bannersub'] = ParagraphStyle(
        'BannerSub', parent=base['Normal'], fontName='Helvetica', fontSize=8.5,
        textColor=colors.HexColor(INK_SOFT), leading=11)
    s['th'] = ParagraphStyle(
        'TH', parent=base['Normal'], fontName='Helvetica-Bold', fontSize=7,
        textColor=colors.HexColor(INK_MUTED), leading=9)
    s['td'] = ParagraphStyle(
        'TD', parent=base['Normal'], fontName='Helvetica', fontSize=8,
        textColor=colors.HexColor(INK), leading=10.5)
    return s


def _footer(canvas, doc, learner_name, label='AI Detection Report'):
    """Running footer: document label + learner on the left, page number right."""
    canvas.saveState()
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(colors.HexColor(INK_MUTED))
    y = 0.42 * inch
    canvas.setStrokeColor(colors.HexColor(RULE))
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, y + 12, doc.width + doc.leftMargin, y + 12)
    canvas.drawString(doc.leftMargin, y,
                      f'PT Academy {label} — {learner_name}')
    canvas.drawRightString(doc.width + doc.leftMargin, y,
                           f'Page {canvas.getPageNumber()}')
    canvas.restoreState()


def _stat_tile(s, value, label, accent=None):
    """A headline figure with its caption."""
    num_style = s['statnum']
    if accent:
        num_style = ParagraphStyle('StatNumAccent', parent=num_style,
                                   textColor=colors.HexColor(accent))
    return [Paragraph(str(value), num_style), Paragraph(label, s['statlabel'])]


def _distribution_bar(dist, total, width):
    """Stacked bar of the verdict mix.

    Segments carry a 2pt surface gap and every one is named in the legend
    beneath, so colour never carries meaning unaided.
    """
    segments = [(c, col) for _, c, col in dist if c > 0]
    if not segments or not total:
        return None

    gap = 2
    available = width - gap * max(0, len(segments) - 1)
    widths, cells = [], []
    style = [
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]
    col = 0
    for i, (count, colour) in enumerate(segments):
        if i:
            widths.append(gap)
            cells.append('')
            col += 1
        widths.append(max(3, available * count / total))
        cells.append('')
        style.append(('BACKGROUND', (col, 0), (col, 0), colors.HexColor(colour)))
        col += 1

    bar = Table([cells], colWidths=widths, rowHeights=[9])
    bar.setStyle(TableStyle(style))
    return bar


def _legend(s, dist, total, width):
    """Swatch + label + count + share, beneath the bar."""
    rows = []
    for label, count, colour in dist:
        share = f'{round(count / total * 100)}%' if total else '—'
        swatch = Table([['']], colWidths=[7], rowHeights=[7])
        swatch.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), colors.HexColor(colour)),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        rows.append([swatch, Paragraph(label, s['td']),
                     Paragraph(f'<b>{count}</b>', s['td']),
                     Paragraph(share, s['bodysoft'])])

    t = Table(rows, colWidths=[0.16 * inch, width - 1.36 * inch, 0.5 * inch, 0.7 * inch])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (2, 0), (3, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, colors.HexColor(RULE)),
    ]))
    return t


def _badge(text, colour):
    """Filled pill carrying the classification label."""
    p = ParagraphStyle('BadgeText', fontName='Helvetica-Bold', fontSize=6.5,
                       textColor=colors.HexColor(WHITE), leading=8, alignment=TA_CENTER)
    width = max(0.5 * inch, 0.05 * inch * len(text) + 0.14 * inch)
    t = Table([[Paragraph(text, p)]], colWidths=[width])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(colour)),
        ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return t, width


def _generate_learner_report(results):
    """A learner-facing version of the report.

    Deliberately narrower than the assessor report. It answers one question for
    the learner - "is there anything I need to do before my work is marked?" -
    and, if so, lists the answers to review. Everything that reads as a verdict,
    a score, a methodology note or an accusation is removed, and the accent is
    brand gold rather than red so a highlighted passage never signals guilt.
    """
    answer_results = results.get('results', [])
    summary = results.get('summary', {})
    learner_name = results.get('learner_name', 'Learner')
    assessor_name = results.get('assessor_name', '')
    course_name = results.get('course_name', '')

    # Only answers a learner might act on. Mixed / AI / AI-Polished all reduce to
    # the same ask - review and confirm it is in your own words - so no
    # distinction between them is drawn or needed here.
    review = sorted(
        [r for r in answer_results
         if r.get('overall_classification') not in ('Human',) + UNASSESSED],
        key=lambda x: x.get('order', 0))

    s = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.5 * inch, bottomMargin=0.75 * inch,
        title=f'Authenticity Check — {learner_name}', author='PT Academy',
    )
    W = doc.width
    el = []

    # ── MASTHEAD ─────────────────────────────────────────────────────────────
    generated = datetime.now().strftime('%d %B %Y')
    meta = [f'<b>Learner:</b> {escape(learner_name)}']
    if course_name:
        meta.append(f'<b>Course:</b> {escape(course_name)}')
    meta.append(f'{generated}')
    head_left = [Paragraph('PT ACADEMY', s['eyebrow']),
                 Paragraph('Workbook Authenticity Check', s['title']),
                 Spacer(1, 3),
                 Paragraph('  ·  '.join(meta), s['subtitle'])]

    logo_path = Path(__file__).parent.parent / 'logo.png'
    if logo_path.exists():
        try:
            head = Table(
                [[head_left, Image(str(logo_path), width=0.62 * inch, height=0.62 * inch)]],
                colWidths=[W - 0.8 * inch, 0.8 * inch])
            head.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))
            el.append(head)
        except Exception:
            el.extend(head_left)
    else:
        el.extend(head_left)
    el.append(Spacer(1, 16))

    # ── INTRO ────────────────────────────────────────────────────────────────
    intro = ('As part of our quality checks, every workbook is reviewed to confirm the '
             'answers are written in the learner\'s own words. This is a routine step that '
             'applies to all learners.')
    el.append(Paragraph(intro, s['body']))
    el.append(Spacer(1, 14))

    if not review:
        # Nothing to action.
        panel = Table([[[
            Paragraph('NOTHING TO DO', ParagraphStyle(
                'ClearEyebrow', parent=s['eyebrow'], textColor=colors.HexColor(PRIORITY_CLEAR))),
            Spacer(1, 5),
            Paragraph('Your workbook raised no questions in this check. There is nothing you '
                      'need to do. Well done.', s['body']),
        ]]], colWidths=[W])
        panel.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(SURFACE)),
            ('LEFTPADDING', (0, 0), (-1, -1), 13), ('RIGHTPADDING', (0, 0), (-1, -1), 13),
            ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('LINEABOVE', (0, 0), (-1, 0), 3, colors.HexColor(PRIORITY_CLEAR)),
        ]))
        el.append(panel)
    else:
        n = len(review)
        # ── WHAT TO DO ───────────────────────────────────────────────────────
        do = (f'{n} of your answers ' + ('has' if n == 1 else 'have') +
              ' been highlighted for you to look over before your work is marked. '
              'For each one, please read it back and make sure it is written fully in your '
              'own words. If you used any notes, websites or tools to help you, that is fine '
              '— just rewrite the answer in your own words so it reflects your own '
              'understanding, then let your assessor know once you have updated it.')
        reassure = ('Being asked to review an answer is not an accusation. This check is a '
                    'guide, not a decision, and sometimes there is nothing wrong at all — '
                    'reviewing simply confirms the work is your own.')
        panel = Table([[[
            Paragraph('WHAT TO DO', s['eyebrow']), Spacer(1, 5),
            Paragraph(do, s['body']), Spacer(1, 7),
            Paragraph(reassure, s['caption']),
        ]]], colWidths=[W])
        panel.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(SURFACE)),
            ('LEFTPADDING', (0, 0), (-1, -1), 13), ('RIGHTPADDING', (0, 0), (-1, -1), 13),
            ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('LINEABOVE', (0, 0), (-1, 0), 3, colors.HexColor(BRAND_GOLD)),
        ]))
        el.append(panel)

        # ── ANSWERS TO REVIEW ────────────────────────────────────────────────
        el.append(Spacer(1, 20))
        el.append(Paragraph('Answers to review', s['section']))
        el.append(Paragraph(f'{n} answer(s), in the order they appear in your workbook.',
                            s['caption']))
        el.append(Spacer(1, 10))

        gold_flag = ParagraphStyle('GoldFlag', parent=s['answer'],
                                   textColor=colors.HexColor('#7a6600'), leftIndent=7)

        for idx, r in enumerate(review, 1):
            # Strip the workbook's own leading number ("15. ...") so the clean
            # review-list number does not double up with it.
            q = re.sub(r'^\s*\d+\.\s*', '', r.get('question', ''))
            body = [Paragraph(f"{idx}. {escape(q)}", s['question'])]
            if r.get('unit'):
                body.append(Paragraph(escape(r.get('unit', '')), s['caption']))
            body.append(Spacer(1, 6))

            sentences = [str(x) for x in (r.get('ai_flagged_sentences') or []) if str(x).strip()]
            if sentences:
                body.append(Paragraph('Please review these passages and make sure they are '
                                      'in your own words:', s['bodysoft']))
                body.append(Spacer(1, 3))
                for sent in sentences[:10]:
                    body.append(Paragraph(f'•&nbsp; {escape(sent)}', gold_flag))
            else:
                body.append(Paragraph('Please review this answer in full and make sure it is '
                                      'written in your own words.', s['bodysoft']))

            card = Table([['', body]], colWidths=[3, W - 3])
            card.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, 0), colors.HexColor(BRAND_GOLD)),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (0, 0), 0), ('RIGHTPADDING', (0, 0), (0, 0), 0),
                ('LEFTPADDING', (1, 0), (1, 0), 12), ('RIGHTPADDING', (1, 0), (1, 0), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ]))
            el.append(KeepTogether([card, Spacer(1, 10)]))

    # ── CLOSE ────────────────────────────────────────────────────────────────
    el.append(Spacer(1, 16))
    contact = 'If you have any questions about this check or your workbook, please speak to '
    contact += (f'your assessor, {escape(assessor_name)}.' if assessor_name
                else 'your assessor.')
    close = Table([[Paragraph(contact, s['bodysoft'])]], colWidths=[W])
    close.setStyle(TableStyle([
        ('LINEABOVE', (0, 0), (-1, 0), 0.75, colors.HexColor(RULE)),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    el.append(close)

    def on_page(canvas, doc_):
        _footer(canvas, doc_, learner_name, label='Workbook Authenticity Check')

    doc.build(el, onFirstPage=on_page, onLaterPages=on_page)
    buf.seek(0)
    return buf.getvalue()


def generate_pdf_report(results, audience='assessor'):
    """Generate the analysis report as PDF bytes.

    audience='assessor' (default) is the full internal report. audience='learner'
    is a version safe to hand directly to a learner: no scores, no raw detector
    verdicts, no reliability caveats, no "misconduct" wording, and no red - a
    supportive "please review these answers in your own words" note instead of a
    judgement about the learner.
    """
    if audience == 'learner':
        return _generate_learner_report(results)
    answer_results = results.get('results', [])
    summary = results.get('summary', {})
    learner_name = results.get('learner_name', 'Unknown Learner')
    assessor_name = results.get('assessor_name', 'Unknown Assessor')
    course_name = results.get('course_name', '')
    analysis_mode_label = results.get('analysis_mode_label', 'Full Workbook Analysis')

    portfolio_risk = summary.get('portfolio_risk', 'Unknown')
    short_answer_count = summary.get('short_answer_count', 0)
    quality_note = summary.get('quality_note', '')

    total_answers = len(answer_results)
    assessed_count = summary.get('assessed_count', total_answers)
    unassessable_count = summary.get('unassessable_count', 0)
    flagged_count = summary.get('flagged_count', 0)
    flagged_pct = summary.get('flagged_pct', 0)
    enough_to_score = summary.get('enough_to_score', True)
    ai_count = summary.get('ai_count', 0)
    mixed_count = summary.get('mixed_count', 0)
    ai_polished_count = summary.get('ai_polished_count', 0)
    human_count = summary.get('human_count', 0)

    s = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.5 * inch, bottomMargin=0.75 * inch,
        title=f'AI Detection Report — {learner_name}', author='PT Academy',
    )
    W = doc.width
    el = []

    # ── MASTHEAD ─────────────────────────────────────────────────────────────
    generated = datetime.now().strftime('%d %B %Y at %H:%M')
    meta_lines = [
        f'<b>Learner:</b> {escape(learner_name)}',
        f'<b>Assessor:</b> {escape(assessor_name)}',
    ]
    if course_name:
        meta_lines.append(f'<b>Course:</b> {escape(course_name)}')
    meta_lines.append(f'{escape(analysis_mode_label)} · {generated}')

    head_left = [Paragraph('PT ACADEMY', s['eyebrow']),
                 Paragraph('AI Detection Report', s['title']),
                 Spacer(1, 3),
                 Paragraph('<br/>'.join(meta_lines), s['subtitle'])]

    logo_path = Path(__file__).parent.parent / 'logo.png'
    placed = False
    if logo_path.exists():
        try:
            head = Table(
                [[head_left, Image(str(logo_path), width=0.62 * inch, height=0.62 * inch)]],
                colWidths=[W - 0.8 * inch, 0.8 * inch])
            head.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))
            el.append(head)
            placed = True
        except Exception:
            placed = False
    if not placed:
        el.extend(head_left)

    el.append(Spacer(1, 12))

    # ── REVIEW LEVEL ─────────────────────────────────────────────────────────
    accent, level_title, level_sub = review_level(portfolio_risk)
    banner_body = [Paragraph('PORTFOLIO VERDICT', s['eyebrow']), Spacer(1, 2),
                   Paragraph(escape(level_title), s['banner'])]
    if level_sub:
        banner_body.append(Paragraph(escape(level_sub), s['bannersub']))
    banner = Table([['', banner_body]], colWidths=[5, W - 5])
    banner.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), colors.HexColor(accent)),
        ('BACKGROUND', (1, 0), (1, 0), colors.HexColor(SURFACE)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (0, 0), 0), ('RIGHTPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING', (1, 0), (1, 0), 11), ('RIGHTPADDING', (1, 0), (1, 0), 11),
        ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
    ]))
    el.append(banner)
    el.append(Spacer(1, 13))

    # ── HEADLINE FIGURES ─────────────────────────────────────────────────────
    tiles = [
        _stat_tile(s, flagged_count,
                   f'Flagged for review<br/>{flagged_pct}% of {assessed_count} assessed',
                   accent=accent if flagged_count else None),
        _stat_tile(s, ai_count, 'Outright AI verdicts',
                   accent=PRIORITY_CRITICAL if ai_count else None),
        _stat_tile(s, mixed_count + ai_polished_count, 'Mixed or partial indicators'),
        _stat_tile(s, f'{assessed_count}/{total_answers}', 'Answers assessed'),
    ]
    tile_table = Table([tiles], colWidths=[W / 4] * 4)
    tile_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING', (1, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LINEBEFORE', (1, 0), (-1, -1), 0.5, colors.HexColor(RULE)),
    ]))
    el.append(tile_table)
    el.append(Spacer(1, 15))

    # ── VERDICT MIX ──────────────────────────────────────────────────────────
    dist = verdict_mix(summary)
    bar_dist = verdict_mix_bar(summary)
    el.append(Paragraph('VERDICT MIX', s['eyebrow']))
    el.append(Spacer(1, 4))
    bar = _distribution_bar(bar_dist, total_answers, W)
    if bar:
        el.append(bar)
        el.append(Spacer(1, 6))
    el.append(_legend(s, dist, total_answers, W))
    el.append(Spacer(1, 13))

    # ── WHAT TO DO ───────────────────────────────────────────────────────────
    rec = summary.get('recommendation') or build_recommendation(summary)
    notes = summary.get('notes') or build_notes(summary)

    action = Table([[[Paragraph('WHAT TO DO', s['eyebrow']), Spacer(1, 4),
                      Paragraph(rec, s['body']), Spacer(1, 6),
                      Paragraph(' '.join(notes), s['caption'])]]], colWidths=[W])
    action.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(SURFACE)),
        ('LEFTPADDING', (0, 0), (-1, -1), 11), ('RIGHTPADDING', (0, 0), (-1, -1), 11),
        ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
        ('LINEABOVE', (0, 0), (-1, 0), 2, colors.HexColor(BRAND_GOLD)),
    ]))
    el.append(action)
    el.append(Spacer(1, 12))

    # Each answer carries two independent signals that regularly disagree. This
    # sat only in the Flagged section caption, where it was easy to miss, so a
    # reader hitting "AI Score 0%" beside a "mixed signals" verdict had nothing
    # telling them that is expected rather than a fault.
    term_style = ParagraphStyle('Term', parent=s['td'], fontName='Helvetica-Bold',
                                textColor=colors.HexColor(INK))
    guide_rows = [[Paragraph(term, term_style), Paragraph(body, s['bodysoft'])]
                  for term, body in READING_GUIDE]
    guide = Table(guide_rows, colWidths=[1.35 * inch, W - 1.35 * inch - 22])
    guide.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (0, -1), 11), ('LEFTPADDING', (1, 0), (1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 11),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, colors.HexColor(RULE)),
    ]))
    guide_panel = Table([[[Paragraph('READING EACH ANSWER', s['eyebrow']), Spacer(1, 5), guide]]],
                        colWidths=[W])
    guide_panel.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
        ('LINEABOVE', (0, 0), (-1, 0), 2, colors.HexColor(BRAND_GOLD)),
    ]))
    el.append(guide_panel)

    # ── ANSWER DETAIL ────────────────────────────────────────────────────────
    flagged = sorted([r for r in answer_results
                      if r.get('overall_classification') not in ('Human',) + UNASSESSED],
                     key=flagged_sort_key)
    human = sorted([r for r in answer_results
                    if r.get('overall_classification') == 'Human'],
                   key=lambda x: x.get('order', 0))
    unassessed = sorted([r for r in answer_results
                         if r.get('overall_classification') in UNASSESSED],
                        key=lambda x: x.get('order', 0))

    def section_header(title, caption):
        gap = 12 if COMPACT_ROUTINE_SECTIONS else 17
        return [Spacer(1, gap), Paragraph(escape(title), s['section']), Spacer(1, 2),
                Paragraph(caption, s['caption']), Spacer(1, 6)]

    def answer_card(idx, r, lead=None):
        """`lead` is kept on the same page as the card, so a group band never
        strands itself at the foot of a page above its first answer."""
        classification = r.get('overall_classification', 'Unknown')
        colour, badge_label = CLASSIFICATION_STYLE.get(
            classification, (PRIORITY_NONE, str(classification).upper()))
        badge, badge_w = _badge(badge_label, colour)

        meta_bits = [f'<b>#{idx}</b>']
        if classification not in UNASSESSED:
            meta_bits.append(f"AI Score <b>{r.get('ai_percentage', 0)}%</b>")
        if r.get('word_count'):
            meta_bits.append(f"{r['word_count']} words")
        meta = Table([[badge, Paragraph(' · '.join(meta_bits), s['bodysoft'])]],
                     colWidths=[badge_w + 7, None])
        meta.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))

        body = [meta, Spacer(1, 6),
                Paragraph(escape(r.get('question', '')), s['question']),
                Spacer(1, 2),
                Paragraph(escape(r.get('unit', '')), s['caption']),
                Spacer(1, 6)]

        if classification in UNASSESSED:
            body.append(Paragraph('Too little text to reach a reliable verdict — '
                                  'not counted in the verdict mix.', s['bodysoft']))
        else:
            body.append(Paragraph(f"<b>Verdict:</b> {escape(r.get('feedback', ''))}",
                                  s['bodysoft']))

        sentences = r.get('ai_flagged_sentences') or []
        if sentences and classification not in UNASSESSED:
            body.append(Spacer(1, 7))
            body.append(Paragraph('SENTENCES FLAGGED AS AI-GENERATED', ParagraphStyle(
                'FlagHead', parent=s['eyebrow'],
                textColor=colors.HexColor(PRIORITY_CRITICAL))))
            body.append(Spacer(1, 3))
            for sentence in sentences[:10]:
                body.append(Paragraph(f'▸ {escape(str(sentence))}', s['flagged']))

        answer_text = r.get('answer_full', '') or ''
        if len(answer_text) > 1400:
            answer_text = answer_text[:1400] + '…'
        if answer_text:
            body.append(Spacer(1, 8))
            ans = Table([[Paragraph(escape(answer_text).replace('\n', '<br/>'), s['answer'])]],
                        colWidths=[W - 0.32 * inch])
            ans.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(SURFACE)),
                ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]))
            body.append(ans)

        card_lead = lead or []
        card = Table([['', body]], colWidths=[3, W - 3])
        card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), colors.HexColor(colour)),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (0, 0), 0), ('RIGHTPADDING', (0, 0), (0, 0), 0),
            ('LEFTPADDING', (1, 0), (1, 0), 11), ('RIGHTPADDING', (1, 0), (1, 0), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 11),
        ]))
        return KeepTogether(card_lead + [card, Spacer(1, 10)])

    def compact_table(rows_data, show_score):
        """Dozens of routine answers read better as a table than as cards.

        Grouped under a unit band rather than carrying a Unit column. Repeating
        the same long unit label on every row was noise, but blanking the repeats
        read as missing data - and lost the unit entirely wherever the table
        broke across a page. A band states it once, unambiguously.
        """
        band_style = ParagraphStyle(
            'UnitBand', parent=s['th'], fontSize=7,
            textColor=colors.HexColor(INK), leading=9)
        cell = ParagraphStyle('CompactCell', parent=s['td'], fontSize=7.3, leading=8.8)

        ncols = 3 if show_score else 2
        head = [Paragraph('#', s['th']), Paragraph('QUESTION', s['th'])]
        if show_score:
            head.append(Paragraph('AI SCORE', s['th']))

        widths = ([0.26 * inch, W - 0.26 * inch - 0.62 * inch, 0.62 * inch] if show_score
                  else [0.26 * inch, W - 0.26 * inch])

        # One table per unit, with the unit band as its repeat row. A single
        # table with inline bands loses the unit heading wherever the list
        # breaks across a page, leaving a column of questions with no context.
        groups = []
        for idx, r in rows_data:
            unit = r.get('unit', '')
            if not groups or groups[-1][0] != unit:
                groups.append((unit, []))
            groups[-1][1].append((idx, r))

        tables = [Table([head], colWidths=widths)]
        tables[0].setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.HexColor(INK_MUTED)),
        ] + ([('ALIGN', (-1, 0), (-1, 0), 'RIGHT')] if show_score else [])))

        for unit, members in groups:
            rows = [[Paragraph(escape(unit), band_style)] + [''] * (ncols - 1)]
            style = [
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 1), (-1, -1), 2), ('BOTTOMPADDING', (0, 1), (-1, -1), 2),
                ('SPAN', (0, 0), (-1, 0)),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(SURFACE)),
                ('LEFTPADDING', (0, 0), (-1, 0), 5),
                ('TOPPADDING', (0, 0), (-1, 0), 3.5),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 3.5),
            ]
            if show_score:
                style.append(('ALIGN', (-1, 0), (-1, -1), 'RIGHT'))
            for idx, r in members:
                row = [Paragraph(str(idx), cell),
                       Paragraph(escape(r.get('question', '')), cell)]
                if show_score:
                    row.append(Paragraph(f"{r.get('ai_percentage', 0)}%", cell))
                rows.append(row)
                style.append(('LINEBELOW', (0, len(rows) - 1), (-1, len(rows) - 1), 0.4,
                              colors.HexColor(RULE)))
            t = Table(rows, colWidths=widths, repeatRows=1)
            t.setStyle(TableStyle(style))
            tables.append(t)

        return tables


    idx = 1
    if flagged:
        pending_section = section_header(
            'Flagged for review',
            f'{len(flagged)} answer(s) not classified as plainly human-written, ordered by '
            'severity — outright AI verdicts first, then mixed signals, then answers judged '
            'human but possibly containing AI parts. See <i>Reading each answer</i> on page 1 '
            'for how Verdict and AI Score relate.')
        group_labels = FLAGGED_GROUP_LABELS
        previous_group = None
        for r in flagged:
            group = r.get('overall_classification')
            if group != previous_group:
                colour = CLASSIFICATION_STYLE.get(group, (PRIORITY_NONE, ''))[0]
                count = sum(1 for x in flagged if x.get('overall_classification') == group)
                band = Table([[Paragraph(
                    f"{group_labels.get(group, str(group).upper())}  ({count})",
                    ParagraphStyle('GroupBand', parent=s['eyebrow'],
                                   fontSize=7.5, textColor=colors.HexColor(colour)))]],
                    colWidths=[W])
                band.setStyle(TableStyle([
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                    ('TOPPADDING', (0, 0), (-1, -1), 3),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.HexColor(colour)),
                ]))
                pending_band = [Spacer(1, 6 if previous_group else 0), band, Spacer(1, 9)]
                previous_group = group
            else:
                pending_band = None
            lead = (pending_section or []) + (pending_band or []) or None
            pending_section = None
            el.append(answer_card(idx, r, lead=lead))
            idx += 1

    def render_group(group, show_score, lead=None):
        nonlocal idx
        if COMPACT_ROUTINE_SECTIONS:
            el.extend(lead or [])
            el.extend(compact_table([(idx + i, r) for i, r in enumerate(group)],
                                    show_score=show_score))
            idx += len(group)
        else:
            pending = lead
            for r in group:
                el.append(answer_card(idx, r, lead=pending))
                pending = None
                idx += 1

    if human:
        head = section_header(
            'Human-classified answers',
            f'{len(human)} answer(s) classified as human-written, in unit and question '
            'order. Listed in full for completeness; no action indicated.')
        render_group(human, show_score=True, lead=head)

    if unassessed:
        head = section_header(
            'Not assessed',
            f'{len(unassessed)} answer(s) with too little text to assess. Excluded '
            'from the counts above; no verdict is shown because none would be reliable.')
        render_group(unassessed, show_score=False, lead=head)

    if not answer_results:
        el.append(Paragraph('No answers were analysed.', s['bodysoft']))

    el.append(Spacer(1, 16))
    el.append(Paragraph(
        'Generated by PT Detector. Results should be considered '
        'alongside other assessment evidence and learner context.', s['caption']))

    def on_page(canvas, doc_):
        _footer(canvas, doc_, learner_name)

    doc.build(el, onFirstPage=on_page, onLaterPages=on_page)
    buf.seek(0)
    return buf.getvalue()
