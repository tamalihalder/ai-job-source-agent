"""
Scrapes a LinkedIn job listing to extract company name and company website.

Strategy:
  1. Visit the job listing page — extract company name + LinkedIn company page URL.
  2. Visit the LinkedIn company page — scan for the external website link.
  3. If blocked or not found — search DuckDuckGo for "[company] official website".
"""

import re
from urllib.parse import urlparse, urlunparse, unquote, quote_plus
from playwright.async_api import async_playwright, Page

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_COMPANY_NAME_SELECTORS = [
    ".topcard__org-name-link",
    ".job-details-jobs-unified-top-card__company-name a",
    "a[data-tracking-control-name='public_jobs_topcard-org-name']",
    ".jobs-unified-top-card__company-name a",
    "[class*='company-name'] a",
]

_EXCLUDE_DOMAINS = {
    "linkedin.com", "google.com", "facebook.com", "twitter.com",
    "instagram.com", "youtube.com", "wikipedia.org", "crunchbase.com",
    "bloomberg.com", "glassdoor.com", "indeed.com", "bing.com",
    "duckduckgo.com", "reddit.com", "forbes.com", "techcrunch.com",
    "microsoft.com",
}


def _clean_linkedin_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _unwrap_linkedin_redirect(href: str) -> str | None:
    """Unwrap LinkedIn's /redir wrapper to get the real external URL."""
    if "linkedin.com/redir" in href:
        m = re.search(r"url=([^&]+)", href)
        return unquote(m.group(1)) if m else None
    if href.startswith("http") and "linkedin.com" not in href:
        return href
    return None


def _is_excluded(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower().lstrip("www.")
        return any(excl in netloc for excl in _EXCLUDE_DOMAINS)
    except Exception:
        return True


async def _get_company_name_and_url(page: Page) -> dict:
    """Extract company name and LinkedIn company page URL from a job listing page."""
    result = {"name": None, "linkedin_company_url": None}

    for sel in _COMPANY_NAME_SELECTORS:
        el = await page.query_selector(sel)
        if el:
            result["name"] = (await el.text_content() or "").strip()
            href = await el.get_attribute("href") or ""
            if href:
                full = href if href.startswith("http") else f"https://www.linkedin.com{href}"
                result["linkedin_company_url"] = _clean_linkedin_url(full)
            break

    # Fallback: page title is usually "Role at Company | LinkedIn"
    if not result["name"]:
        title = await page.title()
        m = re.search(r" at (.+?) \|", title)
        if m:
            result["name"] = m.group(1).strip()

    return result


async def _get_website_from_company_page(page: Page, company_url: str) -> str | None:
    """
    Visit the LinkedIn company page and extract the external website URL.
    Tries base URL then /about/ sub-page.
    """
    for url in [company_url, company_url.rstrip("/") + "/about/"]:
        print(f"  -> Visiting LinkedIn company page: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await page.wait_for_timeout(2_500)
        except Exception:
            continue

        if "authwall" in page.url or "login" in page.url:
            print("  !! Auth wall on this URL — trying next...")
            continue

        # Scroll to load the About section
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(800)

        # First pass: prefer links with website-related text
        for link in await page.query_selector_all("a[href]"):
            href = (await link.get_attribute("href") or "").strip()
            real_url = _unwrap_linkedin_redirect(href)
            if not real_url or _is_excluded(real_url):
                continue
            text = (await link.text_content() or "").lower()
            if any(kw in text for kw in ("website", "visit", "www", "homepage", "site")):
                print(f"  OK Website (text match): {real_url}")
                return real_url

        # Second pass: any outbound redirect link not in the exclude list
        for link in await page.query_selector_all("a[href]"):
            href = (await link.get_attribute("href") or "").strip()
            real_url = _unwrap_linkedin_redirect(href)
            if real_url and not _is_excluded(real_url):
                print(f"  OK Website (redirect link): {real_url}")
                return real_url

    return None


async def _search_website_by_name(company_name: str) -> str | None:
    """
    Search DuckDuckGo for the company's official website using a plain HTTP request.
    DDG's html endpoint is designed for non-JS clients — no browser needed.
    """
    import asyncio
    import urllib.request
    from urllib.parse import unquote

    query = quote_plus(f"{company_name} official website")
    search_url = f"https://html.duckduckgo.com/html/?q={query}"
    print(f"  -> Searching: '{company_name} official website'")

    def _fetch() -> str:
        req = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")

    try:
        html = await asyncio.to_thread(_fetch)
    except Exception as e:
        print(f"  !! Search failed: {e}")
        return None

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if not m:
            continue
        candidate = unquote(m.group(1))
        if candidate and not _is_excluded(candidate):
            print(f"  OK Search result: {candidate}")
            return candidate

    return None


async def get_company_info(linkedin_job_url: str) -> dict:
    """
    Given a LinkedIn job listing URL, return {"name": str, "website": str | None}.

    Flow:
      1. Job listing page  → company name + LinkedIn company page URL
      2. LinkedIn company page → look for external website link
      3. DuckDuckGo search fallback if no website found
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_UA,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()

        # Step 1: Job listing → company name + LinkedIn company URL
        print("  -> Visiting LinkedIn job listing...")
        try:
            await page.goto(linkedin_job_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3_000)
        except Exception as e:
            print(f"  !! Could not load job page: {e}")
            await browser.close()
            return {"name": None}

        if "authwall" in page.url or "login" in page.url:
            print("  !! LinkedIn requires login — cannot access job page")
            await browser.close()
            return {"name": None}

        info = await _get_company_name_and_url(page)
        print(f"  OK Company name : {info['name']}")

        await browser.close()
        return {"name": info["name"]}
