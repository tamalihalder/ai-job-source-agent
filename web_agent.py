"""
LLM-powered web agent that navigates a company website to find:
  1. The careers / jobs page URL  (via Bing search for "[company] careers")
  2. One open job posting URL from that page

Provider selection (via .env):
  - ANTHROPIC_API_KEY set   →  uses Claude (Anthropic API)
  - ANTHROPIC_API_KEY unset →  uses Ollama (free, local)

Ollama: run `ollama pull llama3` then `ollama serve`.
Claude: set ANTHROPIC_API_KEY and optionally CLAUDE_MODEL.
"""

import os
import re
from urllib.parse import urljoin, urlparse, quote_plus, unquote
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

_ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

if _ANTHROPIC_KEY and _ANTHROPIC_KEY != "your-api-key-here":
    import anthropic
    _anthropic_client = anthropic.AsyncAnthropic(api_key=_ANTHROPIC_KEY)
    _PROVIDER = "anthropic"
else:
    import ollama as _ollama
    _PROVIDER = "ollama"

# Third-party job aggregators — exclude from career page search results.
# ATS platforms (greenhouse, lever, workday…) are intentionally NOT here
# because some companies host their career page directly on an ATS.
_JOB_BOARDS = {
    "glassdoor.com", "indeed.com", "linkedin.com", "monster.com",
    "ziprecruiter.com", "simplyhired.com", "careerbuilder.com",
    "dice.com", "wellfound.com", "angel.co", "builtin.com",
    "handshake.com", "levels.fyi", "otta.com",
}

# URL patterns that indicate a specific single job posting
_JOB_POSTING_PATTERNS = [
    r"greenhouse\.io/.+/jobs/\d+",
    r"lever\.co/[^/]+/[a-f0-9-]{36}",
    r"myworkdayjobs\.com.*/job/[^/]+/[^/]+",
    r"ashbyhq\.com/.+/[a-f0-9-]{36}",
    r"jobvite\.com/.*job/",
    r"/jobs?/\d{4,}",
    r"/careers?/[^/]+/\d+[^/]*$",
    r"/job-details?/",
    r"/position/[^/]+$",
    r"/opening/[^/]+$",
    r"/apply/[^/]+$",
]

# ATS domains that host job postings
_ATS_DOMAINS = [
    "greenhouse.io", "lever.co", "workday.com", "ashbyhq.com",
    "myworkdayjobs.com", "jobvite.com", "smartrecruiters.com",
    "workable.com", "bamboohr.com", "icims.com", "taleo.net",
    "successfactors.com",
]

MAX_HOPS = 3

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_job_board(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    return any(board in netloc for board in _JOB_BOARDS)


def _root_domain(netloc: str) -> str:
    parts = netloc.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else netloc.lower()


def _extract_links(html: str, base_url: str, allow_all_domains: bool = False) -> list[dict]:
    """Extract unique <a href> links from HTML, with optional domain filtering."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    links: list[dict] = []
    base_root = _root_domain(urlparse(base_url).netloc)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(base_url, href)
        link_domain = urlparse(full_url).netloc

        if not allow_all_domains:
            is_same = _root_domain(link_domain) == base_root
            is_ats = any(ats in link_domain for ats in _ATS_DOMAINS)
            if not is_same and not is_ats:
                continue

        if full_url in seen:
            continue
        seen.add(full_url)
        text = " ".join(a.get_text().split())
        links.append({"text": text, "url": full_url})

    return links


def _is_specific_job_posting(url: str) -> bool:
    return any(re.search(pat, url, re.IGNORECASE) for pat in _JOB_POSTING_PATTERNS)


def _quick_job_link(links: list[dict], current_url: str) -> str | None:
    for link in links:
        if link["url"] != current_url and _is_specific_job_posting(link["url"]):
            return link["url"]
    return None


def _format_links_for_llm(links: list[dict], max_links: int = 80) -> str:
    lines = []
    for i, link in enumerate(links[:max_links], 1):
        label = (link["text"] or "(no text)")[:80]
        lines.append(f"{i}. [{label}] {link['url']}")
    return "\n".join(lines)


def _extract_url_from_response(response_text: str, links: list[dict]) -> str | None:
    link_urls = {l["url"] for l in links}
    # Trailing-slash-normalized lookup: maps stripped URL → original URL
    normalized = {l["url"].rstrip("/"): l["url"] for l in links}

    url_match = re.search(r"https?://[^\s\)\]\"\',]+", response_text)
    if url_match:
        candidate = url_match.group(0).rstrip(".,;)")
        if candidate in link_urls:
            return candidate
        norm = candidate.rstrip("/")
        if norm in normalized:
            return normalized[norm]

    # Maybe the model replied with a link index number
    num_match = re.search(r"\b(\d+)\b", response_text)
    if num_match:
        idx = int(num_match.group(1)) - 1
        if 0 <= idx < len(links):
            return links[idx]["url"]

    return None


async def _ask_llm(prompt: str, links: list[dict]) -> str | None:
    try:
        if _PROVIDER == "anthropic":
            response = await _anthropic_client.messages.create(
                model=_CLAUDE_MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
        else:
            response = _ollama.chat(
                model=_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0},
            )
            text = response.message.content.strip()
        print(f"     LLM raw: {text[:150]!r}")
        return _extract_url_from_response(text, links)
    except Exception as e:
        print(f"  !! LLM error ({_PROVIDER}): {e}")
        return None


async def _scroll_to_load(page) -> None:
    for offset in [300, 600, 900, 1500]:
        await page.evaluate(f"window.scrollTo(0, {offset})")
        await page.wait_for_timeout(400)


# ── Public API ─────────────────────────────────────────────────────────────────

async def _web_search(query: str) -> list[dict]:
    """
    Fetch DuckDuckGo HTML results using a plain HTTP request (no browser).
    DDG's html.duckduckgo.com/html/ endpoint is designed for non-JS/curl clients and
    does not require a headless browser — avoiding bot-detection entirely.
    """
    import asyncio
    import urllib.request

    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    def _fetch() -> str:
        req = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")

    try:
        html = await asyncio.to_thread(_fetch)
    except Exception as e:
        print(f"  !! Search error: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if not m:
            continue
        real_url = unquote(m.group(1))
        text = " ".join(a.get_text().split())
        if real_url and not _is_job_board(real_url):
            results.append({"text": text, "url": real_url})
    return results


def _simplify_name_for_search(company_name: str) -> str:
    """
    Clean a company name for web search.
    - Strips trademark/copyright symbols.
    - For "X, part of Y" patterns, combines both: "X Y" (more specific than X alone,
      which can be ambiguous when X is a common word like "Cicero").
    """
    name = re.sub(r"[™®©]", "", company_name)
    m = re.search(
        r",?\s*(?:part of|a division of|a subsidiary of|acquired by)\s+(.+)$",
        name, flags=re.IGNORECASE,
    )
    if m:
        parent = m.group(1).strip()
        base = re.sub(
            r",?\s*(?:part of|a division of|a subsidiary of|acquired by)\b.*",
            "", name, flags=re.IGNORECASE,
        ).strip()
        return f"{base} {parent}".strip(" ,")
    return name.strip(" ,")


async def find_career_page(company_name: str, company_url: str | None = None) -> str:
    """
    Find the company's careers page.

    If company_url is provided (extracted from LinkedIn), navigate from that URL
    directly — checking if it's already a careers page, then following career links.
    Falls back to a web search when no URL is available.
    """
    _CAREER_TERMS = {"career", "careers", "jobs", "hiring", "openings"}

    if company_url:
        print(f"  -> Starting from company website: {company_url}")
        url_lower = company_url.lower()
        if any(t in url_lower for t in _CAREER_TERMS):
            print(f"  OK Company URL is already a careers page")
            return company_url
        return await _navigate_to_career_page(company_url)

    search_name = _simplify_name_for_search(company_name)
    print(f"  -> Searching: '{search_name} careers jobs'")
    results = await _web_search(f"{search_name} careers jobs")
    print(f"     {len(results)} candidates after filtering job boards")
    for r in results[:10]:
        print(f"       {r['url']}")

    if not results:
        return ""

    name_slug = re.sub(r"[^a-z0-9]", "", search_name.lower())

    def _score(r: dict) -> int:
        url_lower = r["url"].lower()
        netloc = urlparse(r["url"]).netloc.lower().replace("-", "").replace(".", "")
        has_name = name_slug in netloc
        has_career = any(t in url_lower for t in _CAREER_TERMS)
        if has_name and has_career:
            return 2  # best: careers.blackrock.com
        if has_name:
            return 1  # ok: www.blackrock.com (will navigate to find career link)
        return 0

    top = max(results[:5], key=_score, default=None)
    if not top or _score(top) == 0:
        # LLM picks the most likely company URL from the numbered list
        prompt = (
            f"Which of these URLs is the official website or careers page for {company_name}?\n\n"
            f"{_format_links_for_llm(results[:5])}\n\n"
            f"Reply with ONLY the line number (e.g. '2'), or 'NONE' if none match."
        )
        chosen = await _ask_llm(prompt, results)
        if not chosen:
            print("  !! No matching company site found in search results")
            return ""
        top = {"url": chosen, "text": ""}

    seed_url = top["url"]
    score = _score(top)
    print(f"  OK Seed URL (score={score}) -> {seed_url}")

    # Score 2 means the URL itself is the career page — return immediately
    if score == 2:
        return seed_url

    # Score 0-1 means we have the company site but need to navigate to the career page
    print(f"  -> Navigating {seed_url} to find career link...")
    return await _navigate_to_career_page(seed_url)


async def _navigate_to_career_page(company_url: str) -> str:
    """Navigate a company website to find its careers/jobs page link."""
    current_url = company_url
    _CAREER_URL_SEGMENTS = {"career", "careers", "jobs", "hiring", "openings"}

    def _is_career_url(url: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        netloc = parsed.netloc.lower()
        for kw in _CAREER_URL_SEGMENTS:
            if re.search(rf"(^|/){re.escape(kw)}(/|$)", path):
                return True
            if netloc.startswith(f"{kw}."):
                return True
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=_BROWSER_UA, viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        for hop in range(MAX_HOPS):
            print(f"     [{hop+1}/{MAX_HOPS}] {current_url}")
            try:
                await page.goto(current_url, wait_until="domcontentloaded", timeout=25_000)
                await page.wait_for_timeout(1_500)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                await page.wait_for_timeout(500)
            except Exception as e:
                print(f"  !! Failed to load: {e}")
                break

            html = await page.content()
            links = _extract_links(html, current_url, allow_all_domains=False)

            # Heuristic: prefer links with career keyword as path segment or subdomain
            career_links = [l for l in links if _is_career_url(l["url"])]
            if career_links:
                best = min(career_links, key=lambda l: len(l["url"]))
                current_url = best["url"]
                print(f"  OK Heuristic -> {current_url}")
                break

            # LLM fallback
            if not links:
                break
            prompt = (
                f"Which link leads to the careers or jobs page for {current_url}?\n\n"
                f"{_format_links_for_llm(links)}\n\nReply with ONLY the URL or NONE."
            )
            chosen = await _ask_llm(prompt, links)
            if not chosen or chosen.upper() == "NONE" or chosen == current_url:
                break
            current_url = chosen
            print(f"  OK LLM -> {current_url}")

        await browser.close()

    return current_url


async def find_job_posting(career_page_url: str) -> str:
    """
    Given a careers listing page, return the URL of one open job posting.

    Uses URL pattern heuristics first; falls back to the LLM for selection.
    """
    current_url = career_page_url

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_BROWSER_UA,
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        for hop in range(MAX_HOPS):
            print(f"  -> [{hop+1}/{MAX_HOPS}] {current_url}")
            try:
                # networkidle ensures JS-rendered job listings are loaded
                await page.goto(current_url, wait_until="networkidle", timeout=35_000)
            except Exception:
                pass  # SPA polling loops can cause networkidle to time out; continue anyway

            # Wait for Workday job cards to finish rendering
            if "myworkdayjobs.com" in current_url:
                try:
                    await page.wait_for_selector("[data-automation-id='jobTitle']", timeout=12_000)
                except Exception:
                    pass

            await _scroll_to_load(page)

            html = await page.content()
            # allow_all_domains=True: job postings often live on ATS subdomains
            links = _extract_links(html, current_url, allow_all_domains=True)
            print(f"     {len(links)} links found")

            quick = _quick_job_link(links, current_url)
            if quick:
                print(f"  OK Heuristic -> {quick}")
                await browser.close()
                return quick

            if not links:
                break

            prompt = (
                f"You are navigating a company website to find a job posting.\n"
                f"Current URL: {current_url}\n\n"
                f"Numbered links on this page:\n{_format_links_for_llm(links)}\n\n"
                f"Pick the ONE link most likely to lead to a job search portal or individual job postings.\n"
                f"PREFER: external ATS portals (Workday, Greenhouse, Lever, etc.), 'Search Jobs', 'View all jobs', or 'Open positions'.\n"
                f"AVOID: region filters, category/department pages, internship overview pages, culture or benefits pages.\n"
                f"Reply with ONLY the line number (e.g. '3'). Do not include any other text."
            )
            chosen = await _ask_llm(prompt, links)
            print(f"  OK LLM -> {chosen}")

            if not chosen or chosen == current_url:
                # Fallback: navigate to any ATS or career-subdomain link on this page
                def _is_career_or_ats(url: str) -> bool:
                    netloc = urlparse(url).netloc.lower()
                    if netloc == urlparse(current_url).netloc.lower():
                        return False
                    if any(ats in netloc for ats in _ATS_DOMAINS):
                        return True
                    return bool(re.search(r"(^|\.)(career|careers|jobs|talent)(\.|$)", netloc))

                ats_link = next(
                    (l["url"] for l in links if _is_career_or_ats(l["url"])),
                    None,
                )
                if ats_link and hop < MAX_HOPS - 1:
                    print(f"  -> ATS fallback -> {ats_link}")
                    current_url = ats_link
                    continue
                break

            if _is_specific_job_posting(chosen):
                await browser.close()
                return chosen

            current_url = chosen  # navigate deeper (e.g. company page → ATS listing)

        await browser.close()

    return current_url
