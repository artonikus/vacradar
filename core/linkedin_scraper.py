"""LinkedIn Guest Jobs API & Brazilian Gupy Vacancy Scraper for VacRadar (`vacradar.scoutpipe.com`).

Discovers companies actively hiring data miners, web scraping engineers, and crawler maintainers:
1. LinkedIn Public Guest Jobs API (`seeMoreJobPostings/search` + `jobPosting/{id}`).
2. Brazilian Gupy Public Job Search API (`portal.api.gupy.io/api/v1/jobs`).
3. Local CSV/sample fallback (`data/jobs_br_sample.csv`) so discovery works even when offline or rate-limited.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
import httpx
import pandas as pd

from core.cache import RadarCache
from core.models import LinkedInJobPosting
from core.prefilter import html_to_clean_text

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def infer_domain_from_company_name(company_name: str, known_domains: Optional[dict[str, str]] = None) -> str:
    """Resolve a company's domain from known Ocean.io mappings or slugify as `<slug>.com.br`."""
    norm = (company_name or "").strip().lower()
    if known_domains and norm in known_domains:
        return known_domains[norm]
    if known_domains:
        for k_name, dom in known_domains.items():
            if norm in k_name or k_name in norm:
                return dom
    slug = _NON_ALNUM_RE.sub("", norm.split("-")[0].split("|")[0].strip())
    return f"{slug}.com.br" if slug else "unknown.com.br"


async def _fetch_linkedin_job_description(
    client: httpx.AsyncClient,
    job_id: str,
    semaphore: asyncio.Semaphore,
) -> str:
    """Fetch full job description text from LinkedIn's public guest jobPosting endpoint."""
    if not job_id:
        return ""
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    async with semaphore:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return ""
            soup = BeautifulSoup(resp.text, "html.parser")
            desc_node = soup.select_one(".show-more-less-html__markup, .description__text")
            if desc_node:
                return html_to_clean_text(str(desc_node))
            return html_to_clean_text(resp.text)[:4000]
        except Exception:
            return ""


async def search_linkedin_jobs_async(
    keywords_list: list[str],
    location: str = "Brazil",
    max_jobs_per_query: int = 10,
    cache: Optional[RadarCache] = None,
    known_domains: Optional[dict[str, str]] = None,
    force_refresh: bool = False,
) -> list[LinkedInJobPosting]:
    """Query LinkedIn public Guest Jobs API and Gupy API for data-mining & scraping vacancies."""
    cache = cache or RadarCache()
    user_agent = os.getenv(
        "SCRAPER_USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    )
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    semaphore = asyncio.Semaphore(int(os.getenv("SCRAPER_MAX_CONCURRENCY", "5")))
    discovered: list[LinkedInJobPosting] = []
    seen_keys: set[str] = set()

    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=True,
    ) as client:
        for query in keywords_list:
            query_clean = query.strip()
            if not query_clean:
                continue

            cache_key = f"li_search:{location.lower()}:{query_clean.lower()}:{max_jobs_per_query}"
            if not force_refresh:
                cached_items = cache.get("linkedin_jobs", cache_key)
                if cached_items:
                    for item in cached_items:
                        posting = LinkedInJobPosting.model_validate(item)
                        dedup = f"{posting.company_name.lower()}::{posting.job_title.lower()}"
                        if dedup not in seen_keys:
                            seen_keys.add(dedup)
                            discovered.append(posting)
                    continue

            search_url = (
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                f"?keywords={quote_plus(query_clean)}&location={quote_plus(location)}&start=0"
            )
            query_postings: list[LinkedInJobPosting] = []

            try:
                resp = await client.get(search_url)
                if resp.status_code == 200 and resp.text.strip():
                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.select("li, div.base-card, div.job-search-card")
                    for card in cards[:max_jobs_per_query]:
                        title_el = card.select_one(".base-search-card__title, h3")
                        company_el = card.select_one(".base-search-card__subtitle, h4")
                        loc_el = card.select_one(".job-search-card__location")
                        link_el = card.select_one("a.base-card__full-link, a")
                        time_el = card.select_one("time")

                        if not title_el or not company_el:
                            continue

                        job_title = title_el.get_text(strip=True)
                        company_name = company_el.get_text(strip=True)
                        job_loc = loc_el.get_text(strip=True) if loc_el else location
                        job_url = link_el.get("href", "").split("?")[0] if link_el else ""
                        company_li_url = ""
                        comp_a = company_el.select_one("a")
                        if comp_a and comp_a.get("href"):
                            company_li_url = comp_a.get("href", "").split("?")[0]

                        urn = card.get("data-entity-urn", "")
                        if not urn:
                            urn_div = card.select_one("[data-entity-urn]")
                            if urn_div:
                                urn = urn_div.get("data-entity-urn", "")
                        job_id = urn.split(":")[-1] if "jobPosting:" in urn else ""
                        if not job_id and job_url:
                            m = re.search(r"-(\d{7,12})/?$", job_url)
                            if m:
                                job_id = m.group(1)

                        posted_date = (
                            time_el.get("datetime", "") or time_el.get_text(strip=True)
                            if time_el
                            else ""
                        )

                        dedup = f"{company_name.lower()}::{job_title.lower()}"
                        if dedup in seen_keys:
                            continue
                        seen_keys.add(dedup)

                        desc = await _fetch_linkedin_job_description(client, job_id, semaphore)
                        domain = infer_domain_from_company_name(company_name, known_domains)

                        posting = LinkedInJobPosting(
                            job_id=job_id or RadarCache.compute_hash(dedup)[:10],
                            job_title=job_title,
                            company_name=company_name,
                            domain=domain,
                            location=job_loc,
                            job_url=job_url or f"https://www.linkedin.com/jobs/view/{job_id}",
                            company_linkedin_url=company_li_url,
                            posted_date=posted_date,
                            job_description=desc or f"{job_title} at {company_name} ({query_clean})",
                            source="linkedin",
                        )
                        query_postings.append(posting)
                        discovered.append(posting)
            except Exception:
                pass

            # Also check Brazilian Gupy public job portal API if location is Brazil
            if "brazil" in location.lower() or "brasil" in location.lower():
                try:
                    gupy_url = f"https://portal.api.gupy.io/api/v1/jobs?jobName={quote_plus(query_clean)}&limit=10"
                    g_resp = await client.get(gupy_url)
                    if g_resp.status_code == 200:
                        g_data = g_resp.json().get("data", [])
                        for item in g_data[:max_jobs_per_query]:
                            g_title = str(item.get("name", "")).strip()
                            g_company = str(item.get("careerPageName", "")).strip()
                            if not g_title or not g_company:
                                continue
                            dedup = f"{g_company.lower()}::{g_title.lower()}"
                            if dedup in seen_keys:
                                continue
                            seen_keys.add(dedup)
                            g_desc = html_to_clean_text(str(item.get("description", "")))
                            g_url = str(item.get("jobUrl", ""))
                            domain = infer_domain_from_company_name(g_company, known_domains)
                            posting = LinkedInJobPosting(
                                job_id=str(item.get("id", "")),
                                job_title=g_title,
                                company_name=g_company,
                                domain=domain,
                                location=f"{item.get('city', '')} {item.get('state', 'BR')}".strip(),
                                job_url=g_url,
                                posted_date=str(item.get("publishedDate", ""))[:10],
                                job_description=g_desc or g_title,
                                source="gupy",
                            )
                            query_postings.append(posting)
                            discovered.append(posting)
                except Exception:
                    pass

            if query_postings:
                cache.set(
                    namespace="linkedin_jobs",
                    cache_key=cache_key,
                    payload=[p.model_dump() for p in query_postings],
                    content_hash=RadarCache.compute_hash(query_clean),
                )

    return discovered


def load_sample_linkedin_vacancies(
    sample_csv: str | Path = "data/jobs_br_sample.csv",
    known_companies: Optional[dict[str, dict[str, str]]] = None,
) -> list[LinkedInJobPosting]:
    """Load curated Brazilian LinkedIn/Gupy scraping vacancies from `data/jobs_br_sample.csv`."""
    path = Path(sample_csv)
    if not path.exists():
        return []

    df = pd.read_csv(path, dtype=str).fillna("")
    postings: list[LinkedInJobPosting] = []

    for idx, row in df.iterrows():
        domain = str(row.get("domain", "")).strip().lower()
        job_title = str(row.get("job_title", "Engenheiro de Web Scraping / Dados")).strip()
        job_desc = str(row.get("job_description", "")).strip()
        comp_meta = (known_companies or {}).get(domain, {})
        company_name = (
            str(row.get("company_name", "")).strip()
            or comp_meta.get("company_name")
            or domain.split(".")[0].capitalize()
        )
        linkedin_url = (
            str(row.get("company_linkedin_url", "")).strip()
            or comp_meta.get("linkedin_url", f"https://www.linkedin.com/company/{domain.split('.')[0]}")
        )
        job_url = str(row.get("job_url", "")).strip() or (
            f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(job_title + ' ' + company_name)}&location=Brazil"
        )

        postings.append(
            LinkedInJobPosting(
                job_id=f"br-sample-{idx + 1}",
                job_title=job_title,
                company_name=company_name,
                domain=domain,
                location="Brazil",
                job_url=job_url,
                company_linkedin_url=linkedin_url,
                posted_date="Recent",
                job_description=job_desc,
                source="linkedin_sample",
            )
        )

    return postings
