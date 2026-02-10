# 🔄 Pagination Bucket Classifier

**Automatically classify pagination patterns in career/job listing pages using intelligent heuristics and AI-powered detection.**

---

## 📋 Table of Contents

- [Overview](#overview)
- [Pagination Buckets](#pagination-buckets)
- [Architecture & Data Flow](#architecture--data-flow)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command Reference](#command-reference)
- [How It Works](#how-it-works)
- [AI Judge (Optional)](#ai-judge-optional)
- [Advanced Usage](#advanced-usage)
- [Performance](#performance)
- [Troubleshooting](#troubleshooting)
- [Examples](#examples)

---

## 🎯 Overview

This tool analyzes career/job listing pages and automatically determines their pagination strategy. It uses a combination of:

- **🔍 Heuristic Detection**: Pattern matching, DOM analysis, and behavioral testing
- **📚 Autopager Library**: Industry-standard pagination detection
- **🤖 AI Judge** (optional): OpenAI GPT-3.5 for edge cases and fallback scenarios
- **⚡ Progressive Retry Strategy**: Smart timeout handling (10s → 15s → 20s)

**Key Features:**
- ✅ Works without API key (heuristics-only mode)
- ✅ Parallel processing with multiple workers
- ✅ Live incremental saving (results appear as they complete)
- ✅ Row order preservation (output matches input order exactly)
- ✅ Smart error handling with AI fallback
- ✅ Visible or headless browser modes

---

## 🪣 Pagination Buckets

The classifier categorizes pages into **4 buckets** with pipeline-aware fallbacks:

| Bucket | Description | Example Elements | Common In |
|--------|-------------|------------------|-----------|
| **NEXT** | Sequential navigation with Next/Previous button or single arrow (takes priority if both present) | `Next`, `Previous`, `>`, `<`, `→`, `←`, `›`, `‹` | Traditional career pages, ATS systems (default for structural path) |
| **PAGESELECT** | Direct page number selection & jump buttons | `1 2 3 4`, `First`, `Last`, `»`, `>>`, `«`, `<<` | Government sites, large job boards |
| **LOADMORE** | Button to load more content | `Load More`, `Show More`, `View All` | Modern web apps, startups |
| **SCROLLDOWN** | Infinite scroll (automatic loading) | No button, content loads on scroll | Social platforms, modern sites (default for behavioral path) |

---

## 🏗️ Architecture & Data Flow

### High-Level Data Flow Diagram (DFD)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PAGINATION CLASSIFIER                            │
│                     Intelligent Bucket Detection System                  │
└─────────────────────────────────────────────────────────────────────────┘

INPUT: CSV File (URLs)
        ↓
┌───────────────────┐
│  Load CSV & Setup │  ← Read companyUrl/url column
│  Initialize Queue │  ← Create task queue for workers
└─────────┬─────────┘
          ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                          PARALLEL WORKERS                                │
│  Each worker has its own Chrome instance & processes unique URLs        │
└─────────────────────────────────────────────────────────────────────────┘
          ↓
    ┌─────────┐
    │ Worker 1│     ┌─────────┐     ┌─────────┐
    │ Worker 2│ ... │ Worker N│ ... │ Worker 5│  (Configurable: -w N)
    └────┬────┘     └────┬────┘     └────┬────┘
         │               │               │
         └───────────────┴───────────────┘
                      ↓
        ┌──────────────────────────┐
        │   Pick URL from Queue    │ ← Thread-safe, unique URLs only
        │   (Check if not already  │
        │    processed by others)  │
        └─────────────┬────────────┘
                      ↓
╔═════════════════════════════════════════════════════════════════════════╗
║                    PAGE LOADING (Progressive Timeouts)                   ║
╚═════════════════════════════════════════════════════════════════════════╝
                      ↓
        ┌──────────────────────────┐
        │   ATTEMPT 1 (10s timeout)│
        │   driver.get(url)        │
        │   Wait 3 seconds         │ ← Fast sites succeed here (80%)
        └───────┬──────────────────┘
                │
            SUCCESS? ──YES──→ Continue to Detection
                │
               NO (Timeout)
                ↓
        ┌──────────────────────────┐
        │   ATTEMPT 2 (15s timeout)│
        │   driver.get(url)        │
        │   Wait 5 seconds         │ ← Slower sites succeed (15%)
        └───────┬──────────────────┘
                │
            SUCCESS? ──YES──→ Continue to Detection
                │
               NO (Timeout)
                ↓
        ┌──────────────────────────┐
        │   ATTEMPT 3 (20s timeout)│
        │   driver.get(url)        │
        │   Wait 7 seconds         │ ← Very slow sites (4%)
        └───────┬──────────────────┘
                │
            SUCCESS? ──YES──→ Continue to Detection
                │
               NO (All Failed)
                ↓
        ┌──────────────────────────┐
        │  API Key Available?      │
        │                          │
        │  YES → 🤖 AI Judge       │ ← URL-based classification
        │  NO  → ❌ Error: Timeout │
        └──────────────────────────┘
                ↓

╔═════════════════════════════════════════════════════════════════════════╗
║                         DETECTION PIPELINE                               ║
║                  (When page loads successfully)                          ║
╚═════════════════════════════════════════════════════════════════════════╝
                ↓
    ┌───────────────────────┐
    │ Extract HTML & DOM    │ ← Get page source, visible elements
    │ Scroll 500px down     │ ← Trigger lazy-loaded elements
    └───────────┬───────────┘
                ↓
        ┌───────────────┐
        │   AUTOPAGER   │ ← Automatic pagination link detection
        │   Detection   │    (Industry-standard library)
        └───────┬───────┘
                │
                ├──→ Found Links? ──YES──→ Structural Path
                │
                └──→ Not Found? ──→ Behavioral Path

╔═════════════════════════════════════════════════════════════════════════╗
║                          STRUCTURAL PATH                                 ║
║           (When pagination links are detected by Autopager)              ║
╚═════════════════════════════════════════════════════════════════════════╝
                ↓
    ┌─────────────────────────────┐
    │  Analyze Link Patterns      │
    │  • XPath: //a[text()='Next']│
    │  • Keywords: "next", "→"    │
    │  • Page numbers: 1, 2, 3... │
    └────────────┬────────────────┘
                 ↓
        ┌────────────────┐
        │ Heuristic Pass │
        │ • Next signals │ → Strong indicators?
        │ • Page numbers │
        └────────┬───────┘
                 │
         Clear Signal? ──YES──→ Return NEXT or PAGESELECT
                 │
                NO (Ambiguous)
                 ↓
        ┌────────────────────┐
        │  🤖 AI JUDGE       │ ← GPT-3.5: "next" vs "pageselect"
        │  (if API key)      │   (Analyzes HTML snippet + context)
        └────────┬───────────┘
                 ↓
         Return: NEXT or PAGESELECT

╔═════════════════════════════════════════════════════════════════════════╗
║                         BEHAVIORAL PATH                                  ║
║         (When NO pagination links found - test for dynamic loading)      ║
╚═════════════════════════════════════════════════════════════════════════╝
                ↓
    ┌─────────────────────────────┐
    │  Measure Initial Height     │
    │  height_before = 1200px     │
    └────────────┬────────────────┘
                 ↓
    ┌─────────────────────────────┐
    │  Scroll to Bottom           │
    │  window.scrollTo(0, bottom) │
    │  Wait 2 seconds             │
    └────────────┬────────────────┘
                 ↓
    ┌─────────────────────────────┐
    │  Measure New Height         │
    │  height_after = 1800px?     │
    └────────────┬────────────────┘
                 │
         Height Increased > 10%? ──YES──→ Return SCROLLDOWN ✅
                 │
                NO (No auto-loading)
                 ↓
    ┌─────────────────────────────┐
    │  Search for Buttons         │
    │  • "Load More"              │
    │  • "Show More"              │
    │  • "View All"               │
    │  • Test if clickable        │
    └────────────┬────────────────┘
                 │
         Button Found & Works? ──YES──→ Return LOADMORE ✅
                 │
                NO (Ambiguous)
                 ↓
        ┌────────────────────┐
        │  🤖 AI JUDGE       │ ← GPT-3.5: "loadmore" vs "scrolldown"
        │  (if API key)      │   (Analyzes HTML + behavior)
        └────────┬───────────┘
                 │
                 ↓
         Return: LOADMORE or SCROLLDOWN (behavioral fallback: scrolldown)

╔═════════════════════════════════════════════════════════════════════════╗
║                      RESULT PROCESSING & SAVING                          ║
╚═════════════════════════════════════════════════════════════════════════╝
                ↓
    ┌─────────────────────────────┐
    │  Update DataFrame           │
    │  df.at[row_index, 'bucket'] │ ← Exact row preservation
    │  = classification_result    │
    └────────────┬────────────────┘
                 ↓
    ┌─────────────────────────────┐
    │  Save to CSV (Live)         │
    │  • Write entire DataFrame   │ ← All workers write to same file
    │  • Format: "url, bucket"    │    (thread-safe with locks)
    │  • Preserve row order       │
    └────────────┬────────────────┘
                 ↓
    ┌─────────────────────────────┐
    │  Terminal Print             │
    │  [Worker N] ✅ Row X: NEXT  │ ← Real-time feedback
    │  (processing_time)          │
    └─────────────────────────────┘
                 ↓
         Continue with next URL in queue
                 ↓
         (Loop until queue is empty)
                 ↓
OUTPUT: CSV File (URLs + Buckets)

┌─────────────────────────────────────────────────────────────────────────┐
│                          FINAL OUTPUT                                    │
│  companyUrl, bucket                                                      │
│  https://example.com/careers, NEXT                                       │
│  https://example2.com/jobs, LOADMORE                                     │
│  https://example3.com/positions, SCROLLDOWN                              │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Components

1. **Queue System**: Thread-safe task distribution ensures no URL is processed twice
2. **Progressive Timeouts**: 10s → 15s → 20s (optimized for speed vs reliability)
3. **Autopager Integration**: Sits after HTML extraction, before path decision
4. **Dual Path Detection**: Structural (links) vs Behavioral (dynamic loading)
5. **AI Judge**: Two modes - during detection (with page) and fallback (URL only)
6. **Live Saving**: Results written immediately with row order preservation

---

## 📦 Installation

### Prerequisites

- Python 3.7+
- Chrome or Chromium browser installed
- Internet connection (for ChromeDriver auto-download)

### Install Dependencies

```bash
# Clone or download the repository
cd url-pagination-bucket-detection

# Option 1: Direct install
pip install -r requirements.txt

# Option 2: Virtual environment (Recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Verify Installation

```bash
python app.py --help
```

You should see the help message with all available options.

---

## 🚀 Quick Start

### 1. Basic Usage (No API Key Required)

```bash
# Process URLs with heuristics only
python app.py -i test.csv -o output.csv -w 3
```

### 2. Prepare Your Input CSV

Create a CSV file with URLs:

```csv
companyUrl
https://www.example.com/careers
https://www.company.com/jobs
https://www.startup.io/positions
```

**Supported column names**: `companyUrl`, `url`, `link`, `Website`, `career_page_url`

### 3. Run Classification

```bash
python app.py -i your_file.csv -o results.csv -w 5
```

### 4. Check Output

```csv
companyUrl, bucket
https://www.example.com/careers, NEXT
https://www.company.com/jobs, LOADMORE
https://www.startup.io/positions, SCROLLDOWN
```

---

## 🎮 Command Reference

### Basic Syntax

```bash
python app.py [OPTIONS]
```

### Command-Line Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--input` | `-i` | String | `test.csv` | Input CSV file containing URLs |
| `--output` | `-o` | String | `output.csv` | Output CSV file for results |
| `--workers` | `-w` | Integer | `1` | Number of parallel workers (1-10 recommended) |
| `--headless` | - | Flag | `True` | Run browser in headless mode (default) |
| `--no-headless` | - | Flag | - | Show browser window (for debugging) |
| `--api-key` | - | String | `None` | OpenAI API key for AI Judge |

### Examples

```bash
# Single worker, visible browser (debugging)
python app.py -i test.csv -o output.csv -w 1

# 5 workers, headless mode (production)
python app.py -i urls.csv -o results.csv -w 5 --headless

# With AI Judge enabled
python app.py -i urls.csv -o results.csv -w 3 --api-key "sk-proj-xxxxx"

# Fast processing (headless + multiple workers)
python app.py -i large_file.csv -o output.csv -w 8 --headless
```

---

## ⚙️ How It Works

### Detection Pipeline

```
URL → Load Page → Wait for Stabilization → Extract DOM
    ↓
Autopager Detection
    ↓
    ├─→ Links Found? → STRUCTURAL PATH
    │                  ├─→ Next button/single arrow (>, →)? → NEXT (priority)
    │                  ├─→ Page numbers/First/Last/double arrows (», >>)? → PAGESELECT
    │                  ├─→ Ambiguous? → AI Judge (if available)
    │                  └─→ Fallback → NEXT (structural default)
    │
    └─→ No Links? → BEHAVIORAL PATH
                   ├─→ Scroll increases height? → SCROLLDOWN
                   ├─→ Load More button? → LOADMORE
                   ├─→ Ambiguous? → AI Judge (if available)
                   └─→ Fallback → SCROLLDOWN (behavioral default)
```

**Pipeline-Aware Fallbacks:**
- **STRUCTURAL PATH** (links found): Defaults to `NEXT` when uncertain
- **BEHAVIORAL PATH** (no links): Defaults to `SCROLLDOWN` when uncertain

### Progressive Retry Strategy

| Attempt | Timeout | Wait Time | Best Case | Worst Case | Success Rate |
|---------|---------|-----------|-----------|------------|--------------|
| 1       | 10s     | 3s        | ~4s       | ~14s       | 80%          |
| 2       | 15s     | 5s        | ~6s       | ~21s       | 15%          |
| 3       | 20s     | 7s        | ~8s       | ~28s       | 4%           |

**Maximum time per URL**: ~69 seconds (if all 3 attempts timeout)
**Average time per URL**: ~8-15 seconds

### Signal Detection

**Structural Signals (XPath/CSS)**:
- Next: `//a[contains(text(), 'Next')]`, `//a[contains(@class, 'next')]`
- Page Select: `//a[contains(text(), '1')]`, `//a[contains(text(), '2')]`
- Arrows: `→`, `»`, `›`

**Behavioral Signals (JavaScript)**:
- Scroll test: `scrollHeight` increase after `scrollTo(0, bottom)`
- Button test: Click verification on Load More buttons
- Dynamic content: Mutation observer for DOM changes

---

## 🤖 AI Judge (Optional)

### What is AI Judge?

AI Judge uses **OpenAI GPT-3.5-turbo** to classify pagination in two scenarios:

1. **During Detection**: When heuristics are ambiguous (e.g., "Next" and "1 2 3" both present)
2. **Fallback Mode**: When page fails to load after 3 retries (URL-only analysis)

### Setup API Key

**Option 1: Environment Variable (.env file - Recommended)**

```bash
# Create .env file in project root
echo "OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxx" > .env
```

**Option 2: Export Environment Variable**

```bash
# Linux/Mac
export OPENAI_API_KEY="sk-proj-xxxxxxxxxxxxx"

# Windows
set OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxx

# Alternative environment variable
export OUTSPARK_OPENAI_STAGING_API_KEY="sk-proj-xxxxxxxxxxxxx"
```

**Option 3: Command Line**

```bash
python app.py -i test.csv -o output.csv --api-key "sk-proj-xxxxxxxxxxxxx"
```

### Getting an OpenAI API Key

1. Visit [https://platform.openai.com/](https://platform.openai.com/)
2. Sign up or log in
3. Navigate to **API Keys** section
4. Click **Create new secret key**
5. Copy the key (starts with `sk-proj-` or `sk-`)
6. Add credits to your account (~$5 is enough for thousands of calls)

### Cost

- **Per AI Judge call**: ~$0.0001 (0.01 cents)
- **100 URLs** (5% fallback): ~$0.0005 (half a cent)
- **1000 URLs** (5% fallback): ~$0.005 (half a cent)

AI Judge is **extremely cost-effective**! 💰

### When AI Judge is Used

```
✅ AI Judge Called:
- Heuristics uncertain (both "Next" and page numbers present)
- Page load failed after 3 retries (fallback mode)

❌ AI Judge NOT Called:
- Clear signals detected (e.g., obvious Next button)
- No API key provided (heuristics-only mode)
```

---

## 🔧 Advanced Usage

### Performance Tuning

**For Fast Sites (modern, well-optimized)**:
```bash
# Use more workers, headless mode
python app.py -i urls.csv -o output.csv -w 8 --headless
```

**For Slow Sites (WordPress, heavy JS)**:
```bash
# Use fewer workers, visible browser (debugging)
python app.py -i urls.csv -o output.csv -w 2
```

**For Mixed Sites**:
```bash
# Balanced approach (default)
python app.py -i urls.csv -o output.csv -w 5 --headless
```

### Worker Recommendations

| URLs | Workers | Estimated Time | RAM Usage |
|------|---------|----------------|-----------|
| <20  | 1-2     | 2-5 min        | ~300MB    |
| 20-50| 3-5     | 5-10 min       | ~600MB    |
| 50-100| 5-7    | 10-15 min      | ~1GB      |
| 100+ | 7-10    | 15-30 min      | ~1.5GB    |

**Note**: More workers = more RAM. Monitor system resources!

### Debugging

**See what the browser is doing**:
```bash
# Visible browser + single worker
python app.py -i test.csv -o output.csv -w 1 --no-headless
```

**Watch terminal output**:
```
[Worker 1] [1/10] 🔄 Processing Row 1: https://example.com/careers
  ⏳ Waiting 3 sec for https://example.com/careers to get stabilize... (Attempt 1/3)
  Signals: Autopager found 5 links; XPath detected 'Next' button
[Worker 1] [1/10] ✅ Row 1: NEXT (5.2s)
```

### Handling Errors

**If a URL fails**:
```csv
companyUrl, bucket
https://broken-site.com/jobs, error: timeout_max_retries
```

**With AI Judge enabled**:
```csv
companyUrl, bucket
https://slow-site.com/careers, NEXT
```
*(AI Judge provides best guess based on URL pattern)*

---

## 📊 Performance

### Speed Benchmarks

**Single Worker**:
- Fast site: ~5-8 seconds
- Average site: ~10-15 seconds
- Slow site: ~20-30 seconds
- Timeout (all retries): ~60-70 seconds

**5 Workers**:
- 100 URLs: ~12-18 minutes
- Throughput: ~5-8 URLs/minute

**10 Workers**:
- 100 URLs: ~8-12 minutes
- Throughput: ~8-12 URLs/minute

### Optimization Tips

1. **Use headless mode** for 10-15% speed boost
2. **Increase workers** for batch processing (optimal: 5-8)
3. **Enable AI Judge** to reduce failures (costs < $0.01 per 100 URLs)
4. **Monitor terminal** to identify slow sites
5. **Pre-filter URLs** to remove obviously broken ones

---

## 🐛 Troubleshooting

### Chrome Driver Issues

**Error**: `selenium.common.exceptions.WebDriverException`

**Solution**:
```bash
# Ensure Chrome/Chromium is installed
google-chrome --version  # Linux
chrome --version         # Mac
"C:\Program Files\Google\Chrome\Application\chrome.exe" --version  # Windows

# ChromeDriver auto-downloads, but if it fails:
pip install --upgrade webdriver-manager
```

### No Results Appearing

**Problem**: Output CSV is empty or missing rows

**Solutions**:
1. Check input CSV has correct column name (`companyUrl`, `url`, etc.)
2. Ensure URLs start with `http://` or `https://`
3. Look for errors in terminal output
4. Try with `--no-headless` to see browser behavior

### API Key Not Working

**Error**: `AI Judge Error: 401 Unauthorized`

**Solutions**:
1. Verify API key starts with `sk-proj-` or `sk-`
2. Check key is set correctly:
   ```bash
   echo $OPENAI_API_KEY  # Should print your key
   ```
3. Ensure you have credits on OpenAI account
4. Try passing key via `--api-key` directly

**Error**: `AI Judge Error: 429 Rate Limit`

**Solution**: Tool has built-in rate limiting (5 requests/minute). This should not occur, but if it does, reduce workers:
```bash
python app.py -i urls.csv -o output.csv -w 2 --api-key "sk-..."
```

### Slow Processing

**Problem**: Taking too long to process URLs

**Solutions**:
1. Increase workers: `-w 5` or `-w 8`
2. Use headless mode: `--headless`
3. Check if sites are genuinely slow (use `curl` to test)
4. Reduce workers if system is overloaded

### Memory Issues

**Error**: `MemoryError` or system slowdown

**Solution**: Reduce workers:
```bash
python app.py -i urls.csv -o output.csv -w 3  # Instead of 10
```

Each worker uses ~150-200MB RAM for Chrome instance.

---

## 📝 Examples

### Example 1: Quick Test

```bash
# Create test file
echo "companyUrl" > quick_test.csv
echo "https://www.google.com/about/careers/" >> quick_test.csv
echo "https://www.microsoft.com/careers" >> quick_test.csv

# Run classification
python app.py -i quick_test.csv -o quick_output.csv -w 2

# Check results
cat quick_output.csv
```

### Example 2: Production Batch

```bash
# 500 URLs, 8 workers, headless, with AI Judge
python app.py \
  -i production_urls.csv \
  -o production_results.csv \
  -w 8 \
  --headless \
  --api-key "sk-proj-xxxxxxxxxxxxx"
```

### Example 3: Debug Single URL

```bash
# Create single URL test
echo "companyUrl" > debug.csv
echo "https://problematic-site.com/careers" >> debug.csv

# Run with visible browser
python app.py -i debug.csv -o debug_output.csv -w 1 --no-headless
```

### Example 4: Resume After Failure

The tool automatically resumes if output file exists:

```bash
# First run (processes 50 URLs, then crashes)
python app.py -i big_file.csv -o results.csv -w 5

# Resume (skips already processed URLs)
python app.py -i big_file.csv -o results.csv -w 5
```

---

## 📚 Additional Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Detailed DFD and system architecture
- **[AI_JUDGE.md](AI_JUDGE.md)** - Complete AI Judge functionality guide
- **[ERROR_HANDLING.md](ERROR_HANDLING.md)** - Error handling and retry logic
- **[RETRY_STRATEGY.md](RETRY_STRATEGY.md)** - Progressive retry timing details

---

## 🤝 Support

For questions, issues, or confusion about the bucket flow code:

**👨‍💻 Developer**: PRIYANSH  
**🔗 GitHub**: [https://github.com/Priyansh-03/](https://github.com/Priyansh-03/)  
**📧 Contact**: Please reach out to Priyansh for any technical questions or clarifications.

---

## 📄 License

This project is developed for pagination detection in career/job listing pages.

---

## 🎉 Quick Command Cheatsheet

```bash
# Most common commands

# Basic run (heuristics only)
python app.py -i urls.csv -o output.csv -w 5

# With AI Judge
python app.py -i urls.csv -o output.csv -w 5 --api-key "sk-..."

# Debug mode (visible browser)
python app.py -i urls.csv -o output.csv -w 1 --no-headless

# Production mode (fast)
python app.py -i urls.csv -o output.csv -w 8 --headless --api-key "sk-..."

# Check help
python app.py --help
```

---

**🔐 Copyright Notice**

*Bucket flow code has been developed by PRIYANSH ([https://github.com/Priyansh-03/](https://github.com/Priyansh-03/)). Please contact Priyansh for any confusion.*

---

**Happy Classifying! 🚀**
