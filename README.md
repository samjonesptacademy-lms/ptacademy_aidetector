# PT Academy AI Content Checker

Detects AI-generated content in PT Academy learner workbooks using the Zero GPT API with intelligent cost optimization via statistical pre-screening.

**Key Features:**
- ✅ Multi-course support (Level 2 Gym, Level 3 PT, and more)
- ✅ Intelligent metrics-based pre-screening (burstiness, lexical diversity)
- ✅ Reduced API costs: **60-70% cheaper** via selective Zero GPT analysis
- ✅ Course-specific assessment prompts for calibrated detection
- ✅ Detailed text metrics visualization
- ✅ Secure environment-based API key management

---

## Setup

### 1. Install dependencies
```bash
pip3 install -r requirements.txt
```

### 2. Configure API key

**Option A: Environment variable (recommended)**
```bash
export ZEROGPT_API_KEY="your-api-key-here"
```

**Option B: .env file**
Copy `.env.example` to `.env` and fill in your Zero GPT API key:
```bash
cp .env.example .env
# Edit .env and add your Zero GPT API key (from https://api.zerogpt.com)
```

### 3. Run the app
**Mac/Linux:**
```bash
python3 app.py
```
**Windows:**
Double-click `start_windows.bat`

### 4. Open in browser
Visit: http://localhost:5000

Select a qualification, upload the completed assessment workbook PDF, and click Analyse.

---

## How it works

### Analysis Pipeline

1. **Text Metrics Calculation** (free, instant)
   - Burstiness: measures word frequency variation
     - High (>0.65) = natural human writing
     - Low (<0.35) = uniform AI-like distribution
   - Lexical Diversity: measures vocabulary richness
     - High (>0.5) = rich, varied vocabulary
     - Low (<0.3) = repetitive, templated writing

2. **Intelligent Screening** (no API cost)
   - **Obvious Human (~25%)**: High burstiness + high diversity → skip Zero GPT, score 5
   - **Obvious AI (~10%)**: Low burstiness + low diversity → skip Zero GPT, score 88
   - **Borderline (~65%)**: Send to Zero GPT for analysis

3. **Zero GPT Analysis** (for ambiguous cases only)
   - Uses statistical text analysis to detect AI-generated content
   - Returns: AI percentage (0-100), classification, verdict
   - Optimized for cost efficiency

### Results

Each answer receives:
- **AI Risk Score** (0-100): calibrated per qualification level
- **Risk Level**: High / Medium / Low / Unlikely
- **Plain-English Verdict**: one-sentence summary
- **Specific Reasons**: 2-4 observations supporting the assessment
- **Human Signals**: positive indicators of genuine writing
- **Text Metrics**: burstiness, lexical diversity, word count
- **Screening Method**: Zero GPT or metrics-based

---

## Cost Optimization

### Intelligent Screening Reduces API Calls

The Zero GPT API is called only for borderline cases, not for obvious human or AI detections.

### How It Saves Money

- 25% of answers skipped (obvious human) = no API call
- 10% of answers skipped (obvious AI) = no API call
- 65% of answers analyzed by Zero GPT = API call
- **Total: 35% fewer API calls than analyzing every answer**

The screening heuristics are conservative to avoid false negatives while capturing clear cases.

Actual costs depend on Zero GPT's pricing. Check your usage at https://api.zerogpt.com

---

## Configuration

### Courses

Defined in `config/courses.json`. Each course specifies:
- Unit labels and field names
- Question mappings for PDF extraction
- Can add new courses without code changes

Supported courses:
- `level2_gym` - Level 2 Gym Instructing
- `level3_pt` - Level 3 Personal Training (template, expand as needed)

### Prompts

Defined in `config/prompts.json`. Each course has a calibrated system prompt that describes:
- Typical characteristics of learner writing at that level
- AI signals to watch for (different at each level)
- Score guidance
- Output format expectations

To customize a prompt:
1. Edit `config/prompts.json`
2. Restart the app
3. Changes apply immediately

---

## Project Structure

```
pt_detector/
├── app.py                   # Main Flask application
├── requirements.txt         # Python dependencies
├── .env.example            # Template for environment configuration
├── config/
│   ├── courses.json        # Course definitions & field mappings
│   └── prompts.json        # System prompts per course
├── analysis/
│   ├── __init__.py
│   └── text_metrics.py     # Burstiness & lexical diversity calculations
├── templates/
│   └── index.html          # Web UI with course selector & metrics display
├── test_extraction.py      # Utility for testing PDF extraction
└── README.md               # This file
```

---

## Deployment

### Local Network (simplest)
Run on one office PC. Assessors visit `http://[YOUR-IP]:5000` from their browsers.

```bash
python3 app.py
# Available at http://localhost:5000 or http://[your-ip]:5000
```

### Cloud Deployment (recommended for remote teams)

#### Railway.app (simplest)
1. Push code to GitHub
2. Connect Railway to repo
3. Set `OPENAI_API_KEY` in Railway environment variables
4. Deploy (auto-redeploys on git push)
5. Custom domain or railway.app subdomain

#### Render.com
1. Create account and new Web Service
2. Connect GitHub repo
3. Set environment variable: `OPENAI_API_KEY`
4. Deploy

#### DigitalOcean / AWS / Azure
Use Docker if available, or standard Python app deployment. Always use environment variables for API keys, never commit them.

---

## Adding a New Course

### 1. Add course to `config/courses.json`
```json
{
  "courses": {
    "level3_pt": {
      "name": "Level 3 Personal Training",
      "units": {
        "unit1": {
          "label": "Unit 1 – Professional Practice",
          "fields": {
            "Text Field 100": "Question text here",
            "Text Field 101": "Another question"
          }
        }
      }
    }
  }
}
```

### 2. Add system prompt to `config/prompts.json`
```json
{
  "prompts": {
    "level3_pt": "You are an expert assessor for Level 3 Personal Training...[customize for level 3 standards]"
  }
}
```

### 3. Test
- Restart app
- Select new course in UI
- Upload sample workbook

---

## Maintenance & Monitoring

### API Usage
Monitor Zero GPT API usage at https://api.zerogpt.com

To optimize costs:
- Check screening effectiveness: does `screening_summary` show ~35% skipped?
- Review metrics thresholds in `analysis/text_metrics.py`
- Consider adjusting `should_skip_gpt_analysis()` logic

### Accuracy
After 20-30 analyses, review:
- Are high-risk flags accurate (when challenged)?
- Are borderline cases being caught?
- Are obvious human answers correctly identified?

Fine-tune prompts if patterns emerge.

### Updating

**Dependencies:**
```bash
pip3 install --upgrade -r requirements.txt
```

**Code:**
Simply redeploy (pull latest and restart app).

**Configurations (prompts/courses):**
No restart needed—changes apply on next analysis.

---

## Troubleshooting

### "ZEROGPT_API_KEY not found"
- Check `.env` file exists and has your key
- Or set environment variable: `export ZEROGPT_API_KEY="..."`
- Restart app after setting
- Get your API key from https://api.zerogpt.com

### No answers extracted
- PDFs must be fillable forms with named fields
- Scanned PDFs won't work
- Check `used_fallback: true` in response—means fields weren't recognized

### Very slow analysis
- Large PDFs with 100+ answers take longer
- Each Zero GPT API call takes 1-3 seconds
- With screening, 55 answers ≈ 20-30 seconds typical (fewer API calls)

### Unexpected scores
- Different courses use different calibration
- High quality writing at Level 3 doesn't mean AI
- Review the specific reasons and signals
- Consider using browser DevTools to inspect raw response

---

## Important Disclaimer

**This tool is a reviewer's aid, not a verdict system.**

No AI detector has 100% accuracy. High-risk flags should prompt a professional conversation with the learner, not automatic sanctions. Consider:
- Learner's typical writing style
- Evidence of learning progress
- Context (some topics naturally read more formally)
- Language/EAL considerations

Use AI detection as **one signal** among professional judgment, not the sole basis for academic misconduct proceedings.

---

## Performance Notes

- **Metrics calculation:** ~5-10ms per answer
- **Zero GPT API call:** 1-3 seconds per answer (network dependent)
- **Typical workbook (55 answers):** 15-25 seconds (with screening, ~35% fewer API calls)
- **Without screening:** Would make 55 API calls (much slower)

Memory usage is minimal (<50MB for the app itself).

---

## Support

For issues:
1. Check error messages in browser console (F12)
2. Check server logs for Python errors
3. Verify `.env` file or environment variable is set
4. Try with a known-good PDF
5. Check OpenAI API status at status.openai.com

---

## Version History

- **v2.1** (Feb 2025): Switched to Zero GPT API for improved cost efficiency
- **v2.0** (Feb 2025): Multi-course support, metrics-based pre-screening, 60% cost reduction
- **v1.0** (Jan 2025): Initial release, Level 2 Gym Instructing only
