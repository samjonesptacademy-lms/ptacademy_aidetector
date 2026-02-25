"""
Text metrics module for AI detection.
Calculates statistical properties of text to support AI detection.
"""

import re
from collections import Counter
from math import log


def calculate_burstiness(text):
    """
    Measure variation in word frequency distribution using the Fano factor.

    Fano factor = variance / mean of word frequencies.
    High values indicate uneven distribution (human-like with preferred words).
    Low values indicate uniform distribution (AI-like with balanced vocabulary).

    Normalised to 0-1 via fano / (1 + fano):
      - 0.0 = perfectly uniform (very AI-like)
      - 0.5 = Poisson-like (neutral)
      - 1.0 = highly bursty (very human-like)

    Returns: float between 0 and 1
    """
    if not text or len(text.split()) < 10:
        return 0.0

    # Tokenize and count word frequencies
    words = text.lower().split()
    # Filter out very short words (< 3 chars) as they add noise
    words = [w for w in words if len(w) > 2 and w.isalpha()]

    if len(words) < 10:
        return 0.0

    word_freq = Counter(words)

    if len(word_freq) == 0:
        return 0.0

    frequencies = list(word_freq.values())
    n = len(frequencies)
    mean_freq = sum(frequencies) / n

    if mean_freq == 0:
        return 0.0

    # Fano factor: variance / mean
    variance = sum((f - mean_freq) ** 2 for f in frequencies) / n
    fano = variance / mean_freq

    # Normalise to 0-1: fano/(1+fano)
    burstiness = fano / (1 + fano)

    return burstiness


def calculate_lexical_diversity(text, anchor_word_count=100):
    """
    Measure vocabulary richness using Type-Token Ratio (TTR) variant.

    High diversity (>0.5) indicates rich vocabulary (human-like)
    Low diversity (<0.3) indicates repetitive vocabulary (AI-like templating)

    Uses length-adjusted TTR to account for varying text lengths.
    anchor_word_count sets the normalisation reference point — pass the workbook's
    average words-per-answer so scoring is calibrated to the actual submission length.

    Returns: float between 0 and 1
    """
    if not text:
        return 0.0

    words = text.lower().split()
    if len(words) < 5:
        return 0.0

    unique_words = len(set(words))
    total_words = len(words)

    ttr = unique_words / total_words

    # Adjust for text length using the workbook anchor as the reference point
    anchor = max(anchor_word_count, 10)  # Guard against degenerate anchor values
    if total_words > 0:
        length_factor = log(total_words) / log(anchor)
        adjusted_ttr = ttr / (1 + (length_factor * 0.5))
    else:
        adjusted_ttr = ttr

    diversity = max(0, min(1, adjusted_ttr))

    return diversity


def detect_american_spelling(text):
    """
    Detect American spelling patterns (red flag for UK learners).
    Returns: list of detected American spellings
    """
    american_patterns = {
        r'\borganization\b': 'organisation',
        r'\borganizations\b': 'organisations',
        r'\bcolor\b': 'colour',
        r'\bcolors\b': 'colours',
        r'\bcenter\b': 'centre',
        r'\bcenters\b': 'centres',
        r'\blabor\b': 'labour',
        r'\blabors\b': 'labours',
        r'\brealize\b': 'realise',
        r'\brealized\b': 'realised',
        r'\banalyze\b': 'analyse',
        r'\banalyzed\b': 'analysed',
    }
    
    detected = []
    text_lower = text.lower()
    
    for american_pattern, british_form in american_patterns.items():
        if re.search(american_pattern, text_lower):
            # Remove the \b word boundary markers for display
            american_word = american_pattern.replace(r'\b', '')
            detected.append(f"{american_word} should be {british_form}")
    
    return detected


def calculate_text_metrics(text, anchor_word_count=100):
    """
    Calculate comprehensive text metrics for AI detection support.

    anchor_word_count: normalisation reference for lexical diversity — pass the
    workbook's average words-per-answer so scoring is calibrated appropriately.

    Returns dict with:
    - burstiness: float 0-1 (>0.65 = human-like, Fano-factor based)
    - lexical_diversity: float 0-1 (>0.5 = good vocabulary range)
    - average_word_length: float in characters
    - sentence_count: int
    - avg_sentence_length: float in words
    - word_count: int
    - unique_words: int
    - american_spelling: list of detected American spelling patterns
    """
    if not text:
        return {
            'burstiness': 0.0,
            'lexical_diversity': 0.0,
            'average_word_length': 0.0,
            'sentence_count': 0,
            'avg_sentence_length': 0.0,
            'word_count': 0,
            'unique_words': 0,
            'american_spelling': [],
        }

    # Basic text statistics
    words = text.lower().split()
    word_count = len(words)
    unique_words = len(set(words))

    # Calculate average word length
    total_chars = sum(len(word) for word in words)
    avg_word_length = total_chars / word_count if word_count > 0 else 0.0

    # Sentence analysis
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sentence_count = len(sentences)

    avg_sentence_length = word_count / sentence_count if sentence_count > 0 else 0.0

    # Calculate key metrics
    burstiness = calculate_burstiness(text)
    lexical_diversity = calculate_lexical_diversity(text, anchor_word_count=anchor_word_count)
    american_spelling = detect_american_spelling(text)

    return {
        'burstiness': round(burstiness, 2),
        'lexical_diversity': round(lexical_diversity, 2),
        'average_word_length': round(avg_word_length, 2),
        'sentence_count': sentence_count,
        'avg_sentence_length': round(avg_sentence_length, 2),
        'word_count': word_count,
        'unique_words': unique_words,
        'american_spelling': american_spelling,
    }


