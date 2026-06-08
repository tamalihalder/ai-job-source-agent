"""
AI Job Source Agent
============================
Usage:
    python main.py [LINKEDIN_JOB_URL]

Required environment variable:
    ANTHROPIC_API_KEY   — set in .env or your shell

Output:
    company_name, career_page_url, open_position_url
"""

import asyncio
import sys
import os
from dotenv import load_dotenv

load_dotenv()

from linkedin_scraper import get_company_info
from web_agent import find_career_page, find_job_posting

DEMO_URL = "https://www.linkedin.com/jobs/view/4382179465/"


def _banner():
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if key and key != "your-api-key-here":
        llm = f"Claude ({os.getenv('CLAUDE_MODEL', 'claude-haiku-4-5-20251001')})"
    else:
        llm = f"Ollama ({os.getenv('OLLAMA_MODEL', 'llama3')})"
    print()
    print("=" * 60)
    print("   AI Job Source Agent")
    print(f"   LLM: {llm}")
    print("=" * 60)


def _section(n: int, title: str):
    print(f"\n[Step {n}] {title}")
    print("-" * 40)


def _results(company_name: str, career_url: str, position_url: str):
    print()
    print("=" * 60)
    print("  RESULTS")
    print("=" * 60)
    print(f"  Company Name    : {company_name}")
    print(f"  Career Page URL : {career_url}")
    print(f"  Open Position   : {position_url}")
    print("=" * 60)
    print()


async def run(linkedin_url: str) -> dict:
    _banner()
    print(f"\nInput: {linkedin_url}")

    # Step 1: Extract company info from LinkedIn
    _section(1, "Extracting company info from LinkedIn")
    company_info = await get_company_info(linkedin_url)

    company_name = company_info.get("name")

    if company_name:
        print(f"  Company : {company_name}")

    if not company_name:
        print("  Could not extract company name automatically.")
        company_name = input("  Enter company name manually: ").strip()

    # Step 2: Search for the careers page
    _section(2, f"Searching for careers page  [{company_name}]")
    career_url = await find_career_page(company_name)
    print(f"\n  Career Page : {career_url}")

    # Step 3: Find one open job posting
    _section(3, "Finding an open job posting")
    position_url = await find_job_posting(career_url)
    print(f"\n  Position URL : {position_url}")

    _results(company_name, career_url, position_url)

    return {
        "company_name": company_name,
        "career_page_url": career_url,
        "open_position_url": position_url,
    }


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEMO_URL
    asyncio.run(run(url))
