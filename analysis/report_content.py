"""Shared presentation content for the AI detection report.

The PDF and the on-screen results render the same analysis. Keeping the palette,
the labels and the prose here means both read from one definition instead of
each carrying its own copy - the two drifted apart once already, and the UI
spent a release showing classifications and wording the report had moved on
from.

app.py resolves this into the summary payload, so the browser renders exactly
the strings the PDF prints.
"""

# ── PALETTE ───────────────────────────────────────────────────────────────────
# Colour encodes review priority, not classification - three tiers plus a
# neutral for "no result". The precise classification is always carried by its
# text label, so colour never has to be read on its own.
#
# Validated on a white surface: lightness band PASS, chroma floor PASS, CVD
# separation PASS (worst adjacent dE 13.9 deutan), normal-vision floor PASS
# (15.7). Green was rejected for the "clear" tier because green against the
# attention orange fails protanopia separation at dE 5.6.
BRAND_GOLD = '#c6a906'
INK = '#111111'
INK_SOFT = '#55534d'
INK_MUTED = '#8a8880'
RULE = '#dedbd2'
SURFACE = '#f7f6f2'
WHITE = '#ffffff'

PRIORITY_CRITICAL = '#d03b3b'   # AI
PRIORITY_ATTENTION = '#ec835a'  # Mixed / Human-with-AI-parts
PRIORITY_CLEAR = '#2a78d6'      # Human
PRIORITY_NONE = '#9a9a94'       # not assessed

# classification -> (priority colour, badge label)
CLASSIFICATION_STYLE = {
    'AI': (PRIORITY_CRITICAL, 'AI'),
    'Mixed': (PRIORITY_ATTENTION, 'MIXED'),
    'AI Polished': (PRIORITY_ATTENTION, 'HUMAN + AI PARTS'),
    'Human': (PRIORITY_CLEAR, 'HUMAN'),
    'Insufficient Text': (PRIORITY_NONE, 'NOT ASSESSED'),
    'Unknown': (PRIORITY_NONE, 'NO RESULT'),
}

# review level -> (accent colour, headline, one-line explanation)
REVIEW_LEVEL_STYLE = {
    'Detailed Review': (PRIORITY_CRITICAL, 'Detailed Review Required',
                        'Widespread AI indicators — review the whole portfolio.'),
    'Review Required': (PRIORITY_ATTENTION, 'Review Flagged Answers',
                        'AI indicators found in specific answers.'),
    'Spot Check': (BRAND_GOLD, 'Spot-Check Suggested',
                   'Minor indicators only; no outright AI verdicts.'),
    'No Indicators': (PRIORITY_CLEAR, 'No AI Indicators Found',
                      'Every assessed answer was classified as human-written.'),
    'Insufficient Data': (PRIORITY_NONE, 'Not Enough Assessable Text',
                          'Too few answers could be assessed to score the portfolio.'),
}

UNASSESSED = ('Insufficient Text', 'Unknown')

# Ordering for the flagged list: most serious first, so the review queue arrives
# already prioritised.
SEVERITY_RANK = {'AI': 0, 'Mixed': 1, 'AI Polished': 2}

FLAGGED_GROUP_LABELS = {
    'AI': 'AI — judged AI-generated',
    'Mixed': 'MIXED SIGNALS — partly AI-generated',
    'AI Polished': 'HUMAN, MAY INCLUDE AI PARTS',
}

# Each answer carries two independent signals that regularly disagree, plus the
# sentence-level hits. Stated up front so a reader hitting "AI Score 0%" beside
# a "mixed signals" verdict knows that is expected rather than a fault.
READING_GUIDE = [
    ('Verdict',
     '<b>Primary indicator.</b> The overall assessment of the answer. This is the main '
     'signal, and it is what the classification and the portfolio verdict are built from.'),
    ('AI Score',
     '<b>Secondary indicator.</b> How much of the answer reads as AI-generated. It is not '
     'a probability and is measured separately from the Verdict, so the two can disagree — '
     'an answer may show 0% with a mixed signals verdict, or 100% with a human-leaning '
     'one. Neither is an error. <b>Where they differ, follow the Verdict.</b>'),
    ('Flagged sentences',
     'Where specific sentences were identified as AI-generated, they are listed in red '
     'with the answer. These show exactly which wording produced the result and are the '
     'quickest way into a review. An answer can carry a flagged verdict without any — the '
     'two are separate checks.'),
]

CLOSING_NOTE = (
    'AI detection is indicative, not conclusive. These results identify answers worth '
    'reading closely; they are not evidence of misconduct on their own and should be '
    'weighed alongside the learner\'s other work and your professional judgement.'
)

NO_PERCENTAGE_NOTE = (
    'There is no overall portfolio percentage: averaging verdicts produces a figure with '
    'no real meaning and invites being read as a grade. Use the review level above and '
    'the individual verdicts below.'
)


def review_level(portfolio_risk):
    """(accent, headline, subtitle) for a review level."""
    return REVIEW_LEVEL_STYLE.get(
        portfolio_risk, (PRIORITY_NONE, str(portfolio_risk), ''))


def build_recommendation(summary):
    """The "what to do" paragraph for a portfolio."""
    risk = summary.get('portfolio_risk')
    assessed = summary.get('assessed_count', 0)
    total = summary.get('total_answers', 0)
    ai = summary.get('ai_count', 0)
    flagged = summary.get('flagged_count', 0)

    if risk == 'Insufficient Data':
        return (f'Only {assessed} of {total} answers contained enough text to judge — too '
                'few to score the portfolio. Treat the individual verdicts as indicative '
                'only and rely on your own judgement.')
    if risk == 'Detailed Review':
        return (f'{ai} of {assessed} assessed answers carry an outright AI verdict — enough '
                'to be a pattern rather than isolated results. Review the whole portfolio, '
                'not only the flagged answers, and consider verifying understanding through '
                'additional questioning or practical assessment.')
    if risk == 'Review Required':
        return (f'{ai} of {assessed} assessed answers carry an AI verdict, and {flagged} in '
                'total were not classified as plainly human-written. Read each flagged '
                'answer — where specific sentences were identified they are listed with it — '
                'and judge whether the writing matches the learner\'s work elsewhere.')
    if risk == 'Spot Check':
        return (f'No answer carries an outright AI verdict, but {flagged} of {assessed} '
                'showed mixed or partial indicators. A spot-check of the flagged answers is '
                'sufficient.')
    return ('Every assessed answer was classified as human-written, with no AI or '
            'mixed-signal verdicts.')


def build_notes(summary):
    """The caveat sentences shown under the recommendation."""
    notes = []
    quality_note = summary.get('quality_note')
    short = summary.get('short_answer_count', 0)
    total = summary.get('total_answers', 0)

    if quality_note:
        notes.append(quality_note)
    elif short and summary.get('portfolio_risk') != 'Insufficient Data':
        notes.append(f'{short} of {total} answers are under 50 words; detection is less '
                     'reliable on short text.')
    notes.append(NO_PERCENTAGE_NOTE)
    notes.append(CLOSING_NOTE)
    return notes


def verdict_mix(summary):
    """Legend rows for the verdict mix: (label, count, colour)."""
    return [
        ('AI', summary.get('ai_count', 0), PRIORITY_CRITICAL),
        ('Mixed signals', summary.get('mixed_count', 0), PRIORITY_ATTENTION),
        ('Human, may include AI parts', summary.get('ai_polished_count', 0),
         PRIORITY_ATTENTION),
        ('Human', summary.get('human_count', 0), PRIORITY_CLEAR),
        ('Not assessed — too little text', summary.get('unassessable_count', 0),
         PRIORITY_NONE),
    ]


def verdict_mix_bar(summary):
    """Bar segments. Mixed and Human-with-AI-parts are one review tier, so they
    draw as a single segment; two same-coloured segments split by a gap read as
    a rendering artifact. The legend still itemises them."""
    return [
        ('AI', summary.get('ai_count', 0), PRIORITY_CRITICAL),
        ('Needs a look',
         summary.get('mixed_count', 0) + summary.get('ai_polished_count', 0),
         PRIORITY_ATTENTION),
        ('Human', summary.get('human_count', 0), PRIORITY_CLEAR),
        ('Not assessed', summary.get('unassessable_count', 0), PRIORITY_NONE),
    ]


def flagged_sort_key(r):
    """Most serious classification first; within one, the stronger detector
    wording first; then portfolio order so it stays predictable."""
    return (SEVERITY_RANK.get(r.get('overall_classification'), 3),
            -(r.get('feedback_level') or 0),
            r.get('order', 0))
