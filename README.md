# AI Job Source Agent

An AI-powered agent that takes a LinkedIn job listing URL and automatically finds the company's official careers page and an open job posting URL — bypassing job board aggregators and going straight to the source.

## What it does

Given a LinkedIn job URL, the agent runs three steps:

1. **Extracts the company name** from the LinkedIn listing (handles auth walls gracefully)
2. **Finds the company's official careers page** via web search + browser navigation
3. **Locates a specific open job posting URL** on that careers page

**Output:**
```
Company Name    : Acme Corp
Career Page URL : https://jobs.acme.com
Open Position   : https://jobs.acme.com/software-engineer-123
```

## Tech stack

- **Python 3.10+**
- [`playwright`](https://playwright.dev/python/) — headless browser automation
- [`anthropic`](https://github.com/anthropics/anthropic-sdk-python) — Claude API (optional)
- [`ollama`](https://github.com/ollama/ollama-python) — local LLM fallback
- [`beautifulsoup4`](https://www.crummy.com/software/BeautifulSoup/) — HTML parsing
- [`python-dotenv`](https://github.com/theskumar/python-dotenv) — environment config

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Playwright browsers (one-time)

```bash
python -m playwright install chromium
```

### 3. Configure LLM provider

Copy `.env.example` to `.env` and fill in one of the two options:

```bash
cp .env.example .env
```

**Option A — Anthropic Claude (paid API):**

```env
ANTHROPIC_API_KEY=sk-ant-...
# CLAUDE_MODEL=claude-haiku-4-5-20251001  # optional, this is the default
```

**Option B — Ollama (free, runs locally):**

Leave `ANTHROPIC_API_KEY` unset. Then pull and serve a local model:

```bash
ollama pull llama3
ollama serve
```

```env
# OLLAMA_MODEL=llama3  # optional, this is the default
```

## Running

```bash
# Pass a LinkedIn job URL as an argument
python main.py "https://www.linkedin.com/jobs/view/4382179465/"

# Or run without arguments to use the built-in demo URL
python main.py
```

### Example output

```
============================================================
   AI Job Source Agent
   LLM: Claude (claude-haiku-4-5-20251001)
============================================================

Input: https://www.linkedin.com/jobs/view/4382179465/

[Step 1] Extracting company info from LinkedIn
----------------------------------------
  Company : Acme Corp

[Step 2] Searching for careers page  [Acme Corp]
----------------------------------------
  Career Page : https://jobs.acme.com

[Step 3] Finding an open job posting
----------------------------------------
  Position URL : https://jobs.acme.com/roles/software-engineer

============================================================
  RESULTS
============================================================
  Company Name    : Acme Corp
  Career Page URL : https://jobs.acme.com
  Open Position   : https://jobs.acme.com/roles/software-engineer
============================================================
```

## Project structure

```
.
├── main.py              # Entry point and orchestration
├── linkedin_scraper.py  # Extracts company name from LinkedIn
├── web_agent.py         # Finds careers page and open job posting
├── requirements.txt
├── .env.example
└── .env                 # Your local config (not committed)
```
