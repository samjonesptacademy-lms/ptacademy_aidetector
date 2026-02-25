# PT Detector – Project Overview

## Executive Summary

**PT Detector** is an AI-powered content analysis system designed to help PT Academy assessors detect AI-generated content in learner assessment workbooks. It combines fast text metrics screening with the ZeroGPT API to provide specialized AI detection while reducing costs through intelligent pre-screening.

**Key Value**:
- Uses ZeroGPT API – specialized AI detection (not general-purpose LLM)
- Optimized cost-effectiveness through metrics-based pre-screening (~35% API call reduction)
- Multi-course support with flexible course configuration
- Provides detailed metrics, confidence scores, and detection reasoning for each answer
- Designed as a reviewer's aid to support professional judgment, not replace it

---

## Project Objectives

1. **Detect AI-generated content** in learner submissions with reasonable accuracy
2. **Optimize costs** through intelligent pre-screening that skips obvious cases
3. **Support multiple qualifications** (Level 2 Gym, Level 3 PT, expandable)
4. **Provide context** for decisions through detailed metrics and signals
5. **Enable deployment** locally, on office networks, or cloud platforms

---

## Architecture Overview

### High-Level Flow

```
PDF Upload
    ↓
Extract Answers
    ↓
Calculate Text Metrics (Burstiness, Lexical Diversity)
    ↓
Screening Decision
├─ Obvious Human (~25%) → Return Human, skip ZeroGPT
├─ Obvious AI (~10%) → Return AI, skip ZeroGPT
└─ Borderline (~65%) → Send to ZeroGPT API for specialized AI detection
    ↓
Stream Results to Frontend
    ↓
Display Analysis + Optional PDF Download
```

### Technology Stack

| Layer | Technology |
|-------|-----------|
| **Web Framework** | Flask (Python) |
| **PDF Processing** | pypdf (extract fields from fillable forms) |
| **AI Detection** | ZeroGPT API (specialized AI detection) |
| **Text Analysis** | Custom metrics (burstiness, lexical diversity) |
| **PDF Reports** | ReportLab |
| **Frontend** | HTML/CSS/JavaScript (vanilla, no framework) |
| **Environment** | Python 3.8+ |

---

## Project Structure

```
pt-academy-ai-detector/
├── app.py                          # Main Flask application
├── index.html                      # Root template (upload interface)
├── requirements.txt                # Dependencies
├── README.md                       # User documentation
├── OVERVIEW.md                     # This file
│
├── config/
│   └── courses.json               # Course definitions & PDF field mappings
│
├── analysis/
│   ├── __init__.py
│   ├── text_metrics.py            # Burstiness, lexical diversity, spelling detection
│   └── pdf_generator.py           # PDF report generation (ReportLab)
│
├── templates/
│   └── index.html                 # Templated upload interface
│
├── static/
│   └── logo.png                   # PT Academy logo
│
├── .env.example                   # Environment configuration template
└── [Startup scripts for macOS/Linux and Windows]
```

---

## Key Components

### 1. **Flask Application** (`app.py`)
The core application handling:
- Route management (`/`, `/courses`, `/analyse`, `/download-report/<session_id>`)
- PDF extraction with fallback mechanisms
- Analysis orchestration
- Streaming JSON responses for real-time UI updates
- Session management for report downloads

**Key Functions**:
- `extract_answers_from_pdf()`: Extracts text from fillable PDF forms using configured field mappings
- `extract_answers_fallback()`: Generic extraction for PDFs without standard mappings
- `analyse_answer()`: Orchestrates metrics calculation + screening + ZeroGPT analysis
- `detect_with_zerogpt()`: Calls ZeroGPT API for specialized AI detection
- `generate_analysis_stream()`: Streams results as newline-delimited JSON (NDJSON)
- `download_report()`: Generates downloadable PDF report from cached analysis

### 2. **Text Metrics** (`analysis/text_metrics.py`)
Calculates statistical indicators of authorship:

**Burstiness** (word frequency variation):
- Measures how uniformly or variably words are used
- High (>0.65) = natural human variation
- Low (<0.35) = uniform AI-like distribution

**Lexical Diversity** (vocabulary richness):
- Measures unique vs. total word ratio
- High (>0.5) = rich, varied vocabulary
- Low (<0.3) = repetitive, templated writing

**American Spelling Detection**:
- Flags words like "color", "organization" in UK context
- Adds as evidence of possible AI training data

**Screening Logic**:
```python
if burstiness > 0.65 and diversity > 0.5:
    return "Skip GPT, obvious human"
elif burstiness < 0.35 and diversity < 0.3:
    return "Skip GPT, obvious AI"
else:
    return "Send to Zero GPT for detailed analysis"
```

### 3. **Configuration Files**

**`config/courses.json`**: Maps PDF form fields to questions and defines course structure
```json
{
  "courses": {
    "level2_gym": {
      "name": "Level 2 Gym Instructing",
      "units": {
        "unit2": {
          "label": "Unit 2 – Health & Safety",
          "fields": {
            "Text Field 570": "1. Identify the types of emergencies occurring at FlexFit Gym & emergency services",
            "Text Field 802": "2. Describe the roles of different staff members..."
          }
        }
      }
    }
  }
}
```

This file is used for:
- Mapping PDF form field names to human-readable questions
- Organizing answers by unit for structured presentation
- Building the course dropdown in the UI
- Supporting multiple qualifications (extend by adding new course IDs)
- **No prompts file needed** – Zero GPT API handles detection independently

### 4. **Frontend** (`index.html` and `templates/index.html`)
Single-page interface featuring:
- **Learner name input** (required) – Associates analysis with the learner
- Course selection dropdown
- PDF file uploader (drag-and-drop support)
- Real-time progress indicator with analysis step counter
- Simplified results display showing AI probability percentage
- PDF download button with learner name in report
- PT Academy branding with gold accent colors (#c6a906)

---

## Analysis Process in Detail

### Stage 1: Text Metrics (Instant, Free)
For each answer, calculate:
- Burstiness score
- Lexical diversity score
- Word count
- American spelling flags

### Stage 2: Intelligent Screening (Instant, Free)
Decision tree:
```
If obvious_human (high burstiness + high diversity):
  → Return "Human" classification
  → Confidence: 0.85
  → Skip ZeroGPT API call (saves cost)

Elif obvious_ai (low burstiness + low diversity):
  → Return "AI" classification
  → Confidence: 0.85
  → Skip ZeroGPT API call (saves cost)

Else:
  → Proceed to Stage 3
```

### Stage 3: ZeroGPT Analysis
Input: Learner's answer text

**ZeroGPT API** (specialized AI detection tool):
- Analyzes text using algorithms trained specifically for AI detection
- Does not rely on general-purpose LLM analysis
- Returns probability score and detection verdict
- Faster and more focused than general-purpose models

**ZeroGPT Returns**:
- `is_human`: Boolean indicating human-written assessment
- `ai_score`: Confidence score (0-100) for AI-generated content
- `detection_verdict`: Explanation of detection result

### Stage 4: Classification & Recommendation
Based on AI percentage with user-friendly recommendations:
```
ai_percentage >= 65%  → "⚠️ High AI Content Detected — Review Required"
ai_percentage >= 35%  → "🔍 Moderate AI Content Detected"
ai_percentage >= 20%  → "🔎 Some AI Content Detected — Worth Reviewing"
ai_percentage < 20%   → "✅ Likely Human-Written"
```

Results are presented as a single AI probability percentage (0-100%) rather than multiple classification categories, making interpretation clearer for assessors.

### Stage 5: Confidence Adjustment
- **Base confidence**: From GPT response
- **Adjustment for short answers** (<50 words): × 0.7
- **Adjustment for many short answers** (>10% of submission): × 0.85
- **Result**: `adjusted_confidence` used in portfolio scoring

---

## Cost Optimization Strategy

### Without Screening
- 50-60 answers per workbook
- All sent to ZeroGPT API
- Cost depends on ZeroGPT pricing model

### With Screening (Actual)
- ~25% obvious human → skip ZeroGPT API
- ~10% obvious AI → skip ZeroGPT API
- ~65% borderline → sent to ZeroGPT API
- **Benefit: 35% reduction in API calls**

### ZeroGPT API Pricing
Check ZeroGPT documentation for current pricing. The screening mechanism reduces API calls by ~35%, improving cost-effectiveness.

### Scaling Estimates
Scaling depends on ZeroGPT's pricing model. Key cost factors:
- Number of borderline answers (65% of total)
- ZeroGPT per-request or subscription pricing
- Geographic region (if applicable)

---

## Configuration & Customization

### Adding a New Course

**1. Add to `config/courses.json`:**
```json
{
  "level3_pt": {
    "name": "Level 3 Personal Training",
    "units": {
      "unit1": {
        "label": "Unit 1 – Professional Practice",
        "fields": {
          "Text Field 200": "How would you assess a new client?"
        }
      }
    }
  }
}
```

**2. No prompts file needed** – ZeroGPT handles detection independently.

**3. Restart app** and the new course appears in the UI dropdown.

### Adjusting Screening Thresholds

Edit `analysis/text_metrics.py`, function `should_skip_gpt_analysis()`:
```python
BURST_HIGH = 0.65       # Threshold for "obvious human"
BURST_LOW = 0.35        # Threshold for "obvious AI"
DIVERSITY_HIGH = 0.5
DIVERSITY_LOW = 0.3
```

These thresholds control when answers skip ZeroGPT detection (obvious cases) vs. when they're sent for API analysis.

---

## Deployment Options

### 1. **Local Desktop** (Simplest)
```bash
export ZEROGPT_API_KEY="your-api-key-here"
python3 app.py
# Visit http://localhost:5000
```

### 2. **Office Network**
```bash
python3 app.py
# Assessors visit http://[YOUR-PC-IP]:5000 from their machines
```

### 3. **Railway.app** (Recommended for remote teams)
1. Push code to GitHub
2. Create Railway project, connect GitHub repo
3. Set `ZEROGPT_API_KEY` environment variable in Railway dashboard
4. Deploy (auto-redeploys on git push)
5. Get public URL (e.g., `myapp.railway.app`)

### 4. **Render.com**
Similar to Railway – connect GitHub, set env vars, deploy.

### 5. **Docker** (For enterprise)
```dockerfile
FROM python:3.11
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
ENV ZEROGPT_API_KEY=${ZEROGPT_API_KEY}
CMD ["python3", "app.py"]
```

---

## Getting Started

### Prerequisites
- Python 3.8+
- ZeroGPT API account
- ZeroGPT API key (obtain from ZeroGPT account dashboard)

### Installation

**1. Clone/extract project:**
```bash
cd /path/to/pt_detector
```

**2. Install dependencies:**
```bash
pip3 install -r requirements.txt
```

**3. Configure ZeroGPT API key:**

Option A (recommended – environment variable):
```bash
export ZEROGPT_API_KEY="your-api-key-here"
```

Option B (.env file):
```bash
cp .env.example .env
# Edit .env and add your ZeroGPT API key
```

**4. Run:**
```bash
python3 app.py
```

**5. Open browser:**
Visit http://localhost:5000

**6. Upload PDF and analyze:**
- Select a qualification
- Upload a completed assessment workbook (must be fillable PDF)
- Click "Analyse"
- View results
- Optionally download PDF report

---

## Development Notes

### PDF Requirements
- **Must be a fillable form** (has named text fields)
- Scanned PDFs won't work
- Fields must be pre-configured in `config/courses.json`
- Fallback extraction available for non-standard PDFs

### Text Field Naming
Standard naming convention:
- `Text Field 100` – Unit 1, Question 1
- `Text Field 101` – Unit 1, Question 2
- etc.

Configurable in `config/courses.json` or `TRUNCATED_FIELD_MAP` in `app.py`.

### Minimum Answer Length
- **Standard answers**: ≥20 characters
- **SMART goal fields**: ≥1 character (shorter due to nature of task)

Short answers (<50 words) get a confidence penalty (×0.7).

### Performance
- **Metrics calculation**: ~5-10ms per answer
- **ZeroGPT API call**: Depends on service load and network latency
- **Typical workbook** (55 answers): Times vary based on screening (65% sent to ZeroGPT)
- **With screening**: Approximately 35% fewer API calls than without

### Memory Usage
- App itself: <50MB
- Session cache (analysis results): ~1-2MB per workbook

### Important Limitations
- **Not a verdict system**: Use as one signal among professional judgment
- **No detector is 100% accurate**: Consider context (learner's typical style, EAL status, etc.)
- **Subject-specific variation**: Some topics naturally read more formally
- **False positives possible**: High-confidence flags should prompt conversation, not automatic sanctions

---

## Monitoring & Maintenance

### Cost Monitoring
1. Check ZeroGPT API usage in your ZeroGPT account dashboard
2. Review `screening_summary` in analysis results
3. Verify screening is working: target ~35% API calls saved
4. If costs exceed estimates, review threshold settings

### Accuracy Monitoring
After 20-30 analyses:
- Review flagged cases that get challenged
- Check if borderline cases are being caught effectively
- Verify obvious human answers marked correctly
- Adjust screening thresholds if needed

### Updating Dependencies
```bash
pip3 install --upgrade -r requirements.txt
```

### Updating Code
Simply redeploy (pull latest, restart app). No configuration reload needed.

### Updating Configuration
Edit `config/courses.json` directly if adding courses. No restart needed for course definitions.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "ZEROGPT_API_KEY not found" | Set environment variable or check `.env` file; restart app |
| "No answers extracted" | PDF must be fillable form with named fields; verify field mappings in `courses.json` |
| ZeroGPT API connection errors | Verify API key is valid; check ZeroGPT service status; ensure account has API quota |
| Very slow analysis | Large PDFs with many answers take time; ZeroGPT API latency depends on service load |
| Unexpected AI scores | Verify ZeroGPT API is responding correctly; some subjects naturally have more formal language |
| PDF download fails | Analysis session may have expired; re-run analysis and download immediately |
| Learner name not appearing in report | Check that learner name was entered in upload form before analysis |

---

## Important Disclaimers

**This is a reviewer's aid, not an automated detection system.**

- No AI detector has 100% accuracy
- High-risk flags should prompt professional conversation, not automatic sanctions
- Consider context: learner's typical writing, learning progress, language barriers
- Use alongside institutional policies and human judgment
- Not suitable as sole basis for academic misconduct proceedings

---

## Version History

- **v3.0** (Feb 2025):
  - Upgraded to Zero GPT API (specialized AI detection vs. general LLM)
  - Simplified UI: Single AI probability metric instead of three-way classification
  - PT Academy brand rebranding (gold accent colors, updated typography)
  - Added required learner name field in upload form
  - Enhanced PDF reports with learner name and recommendation headline
  - Portfolio score table includes AI/Human classification counts
  - Removed unused OpenAI prompt configuration

- **v2.0** (Feb 2025): Multi-course support, metrics-based pre-screening, 35% cost reduction
- **v1.0** (Jan 2025): Initial release, Level 2 Gym Instructing only

---

## Contact & Support

- **Issues**: Check browser console (F12) for client-side errors, server logs for Python errors
- **ZeroGPT Support**: Check ZeroGPT service status and API documentation
- **Documentation**: See README.md for setup details
