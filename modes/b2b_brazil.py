"""VacRadar (`vacradar.scoutpipe.com`): B2B Data & LinkedIn Scraping Vacancy Radar for ScoutPipe.

Operates on two complementary inputs:
1. Ocean.io CSV company exports (`data/ocean-data.csv` / `data/input_ocean.csv`) + career page scraping.
2. Live LinkedIn Guest Jobs & Brazilian Gupy Vacancy Search (`search_linkedin_jobs_async`) targeting
   companies actively hiring data miners, web scraping engineers, and crawler maintainers.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
from typing import Any, Optional

import httpx
import pandas as pd
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from config.keywords_b2b_br import (
    B2B_CAREER_AND_TECH_PATHS,
    DEFAULT_LINKEDIN_SEARCH_QUERIES,
)
from core.cache import RadarCache
from core.linkedin_scraper import (
    load_sample_linkedin_vacancies,
    search_linkedin_jobs_async,
)
from core.llm_client import LLMClient
from core.models import B2BLeadSignal, LinkedInJobPosting, OceanCompanyInput, PrefilterMatch
from core.prefilter import extract_b2b_brazil_chunks, html_to_clean_text

console = Console()

_SCRAPING_TECH_KEYWORDS = re.compile(
    r"\b(?:scrapy|playwright|puppeteer|selenium|beautifulsoup|crawler|cheerio|apify|bright\s*data|zyte)\b",
    re.IGNORECASE,
)


def _pick_first_non_empty(row: pd.Series, candidates: list[str]) -> str:
    """Return the first non-empty string value from a list of candidate column names."""
    for col in candidates:
        if col in row and pd.notna(row[col]):
            val = str(row[col]).strip()
            if val and val.lower() != "nan":
                return val
    return ""


def load_ocean_csv(csv_path: str | Path, limit: Optional[int] = None) -> list[OceanCompanyInput]:
    """Load and normalize Ocean.io CSV export (supports both raw Ocean.io and normalized schemas)."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Ocean.io input CSV not found at: {path}")

    df = pd.read_csv(path, dtype=str).fillna("")
    companies: list[OceanCompanyInput] = []
    seen_domains: set[str] = set()

    for _, row in df.iterrows():
        company_name = _pick_first_non_empty(
            row, ["company_name", "Company", "companyname", "Imprint: Company legal name"]
        )
        domain = _pick_first_non_empty(
            row, ["domain", "Domain", "companywebsite", "Website", "url"]
        )
        if not company_name or not domain:
            continue

        linkedin_url = _pick_first_non_empty(
            row, ["linkedin_url", "Company LinkedIn URL", "linkedincompanypageurl", "LinkedIn"]
        )
        description = _pick_first_non_empty(
            row, ["description", "Company Description", "Description"]
        )
        industry = _pick_first_non_empty(
            row, ["industry", "Industries (Ocean.io)", "Industries (third party)", "Industry"]
        )
        keywords = _pick_first_non_empty(
            row, ["keywords", "Company Keywords", "Keywords"]
        )
        technologies = _pick_first_non_empty(
            row, ["technologies", "Technologies"]
        )
        buying_signals = _pick_first_non_empty(
            row, ["buying_signals", "Buying signals"]
        )

        record = OceanCompanyInput(
            company_name=company_name,
            domain=domain,
            linkedin_url=linkedin_url,
            description=description,
            industry=industry,
            keywords=keywords,
            technologies=technologies,
            buying_signals=buying_signals,
        )

        if not record.domain or record.domain in seen_domains:
            continue
        seen_domains.add(record.domain)
        companies.append(record)

        if limit is not None and len(companies) >= limit:
            break

    return companies


def load_local_job_dumps(jobs_path: Optional[str | Path]) -> dict[str, list[dict[str, str]]]:
    """Load optional local job description dumps (CSV or directory) keyed by domain."""
    if not jobs_path:
        return {}

    path = Path(jobs_path)
    if not path.exists():
        return {}

    domain_jobs: dict[str, list[dict[str, str]]] = {}

    if path.is_file() and path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str).fillna("")
        for _, row in df.iterrows():
            dom = _pick_first_non_empty(row, ["domain", "Domain", "company_domain"]).lower().strip("/")
            title = _pick_first_non_empty(row, ["job_title", "title", "role", "vaga_titulo"]) or "Data / Scraping Role"
            text = _pick_first_non_empty(
                row, ["job_description", "description", "text", "vaga", "content"]
            )
            job_url = _pick_first_non_empty(row, ["job_url", "url", "linkedin_job_url"])
            if dom and text:
                domain_jobs.setdefault(dom, []).append(
                    {"job_title": title, "job_description": text, "job_url": job_url}
                )
    elif path.is_dir():
        for file_path in sorted(path.glob("*")):
            if file_path.is_file() and file_path.suffix.lower() in {".txt", ".md", ".html"}:
                key = file_path.stem.lower()
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                domain_jobs.setdefault(key, []).append(
                    {"job_title": file_path.stem, "job_description": html_to_clean_text(content), "job_url": ""}
                )

    return domain_jobs


async def _scrape_single_domain(
    client: httpx.AsyncClient,
    domain: str,
    cache: RadarCache,
    semaphore: asyncio.Semaphore,
    force_refresh: bool = False,
) -> str:
    """Polite async scraper fetching homepage and Brazilian career/tech paths for a domain."""
    if not force_refresh:
        cached = cache.get("http_scrape", domain)
        if cached is not None:
            return str(cached.get("text", ""))

    collected_pages: list[str] = []
    async with semaphore:
        for subpath in B2B_CAREER_AND_TECH_PATHS:
            url = f"https://{domain}{subpath}"
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.text:
                    cleaned = html_to_clean_text(resp.text)
                    if cleaned:
                        collected_pages.append(f"[Source: {url}]\n{cleaned[:5000]}")
            except Exception:
                if subpath == "/":
                    break
                continue

    combined_scraped = "\n\n".join(collected_pages)
    cache.set(
        namespace="http_scrape",
        cache_key=domain,
        payload={"domain": domain, "text": combined_scraped},
        content_hash=RadarCache.compute_hash(combined_scraped),
    )
    return combined_scraped


async def scrape_domains_batch(
    domains: list[str],
    cache: RadarCache,
    timeout_seconds: float = 6.0,
    max_concurrency: int = 5,
    force_refresh: bool = False,
) -> dict[str, str]:
    """Scrape a batch of company domains concurrently with polite headers and timeout handling."""
    if not domains:
        return {}

    user_agent = os.getenv(
        "SCRAPER_USER_AGENT",
        "Mozilla/5.0 (compatible; VacRadar/1.0; +https://vacradar.scoutpipe.com)",
    )
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    semaphore = asyncio.Semaphore(max_concurrency)
    results: dict[str, str] = {}

    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(timeout_seconds, connect=4.0),
        follow_redirects=True,
    ) as client:
        tasks = {
            domain: asyncio.create_task(
                _scrape_single_domain(client, domain, cache, semaphore, force_refresh=force_refresh)
            )
            for domain in domains
        }
        for domain, task in tasks.items():
            try:
                results[domain] = await task
            except Exception:
                results[domain] = ""

    return results


def build_company_corpus(
    company: OceanCompanyInput,
    scraped_text: str = "",
    job_entries: Optional[list[dict[str, str]]] = None,
) -> str:
    """Combine Ocean.io profile fields with scraped career/tech pages and LinkedIn/Gupy job descriptions."""
    blocks: list[str] = []

    if company.description:
        blocks.append(f"Company Description ({company.company_name}): {company.description}")
    if company.keywords:
        blocks.append(f"Product & Operational Keywords: {company.keywords}")
    if company.industry:
        blocks.append(f"Industry: {company.industry}")
    if company.buying_signals:
        blocks.append(f"Hiring & Buying Signals: {company.buying_signals}")

    if company.technologies and _SCRAPING_TECH_KEYWORDS.search(company.technologies):
        blocks.append(f"Detected Scraping Tech Stack: {company.technologies}")

    if job_entries:
        formatted_jobs = "\n\n".join(
            f"Job Vacancy ({j.get('job_title', 'Role')}): {j.get('job_description', '')}"
            for j in job_entries
        )
        blocks.append(f"LinkedIn / Career Job Descriptions (Vagas):\n{formatted_jobs}")
    if scraped_text:
        blocks.append(f"Scraped Website & Career Pages:\n{scraped_text}")

    return "\n\n".join(blocks)


def search_and_qualify_linkedin_vacancies(
    queries: Optional[list[str]] = None,
    location: str = "Brazil",
    max_jobs_per_query: int = 10,
    include_curated_br_vacancies: bool = True,
    min_score: int = 7,
    output_csv: str | Path = "data/outputs/linkedin_vacancy_leads.csv",
    ocean_reference_csv: Optional[str | Path] = "data/ocean-data.csv",
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], list[LinkedInJobPosting]]:
    """Search LinkedIn & Gupy for data-mining/scraping vacancies, filter via Stage 1, and score via Stage 2."""
    cache = RadarCache()
    llm_client = LLMClient()
    search_queries = queries or list(DEFAULT_LINKEDIN_SEARCH_QUERIES)

    known_domains: dict[str, str] = {}
    known_company_meta: dict[str, dict[str, str]] = {}
    if ocean_reference_csv and Path(ocean_reference_csv).exists():
        for comp in load_ocean_csv(ocean_reference_csv):
            known_domains[comp.company_name.lower()] = comp.domain
            known_company_meta[comp.domain] = {
                "company_name": comp.company_name,
                "linkedin_url": comp.linkedin_url,
                "industry": comp.industry,
                "description": comp.description,
            }

    live_postings = asyncio.run(
        search_linkedin_jobs_async(
            keywords_list=search_queries,
            location=location,
            max_jobs_per_query=max_jobs_per_query,
            cache=cache,
            known_domains=known_domains,
            force_refresh=force_refresh,
        )
    )

    all_postings: list[LinkedInJobPosting] = list(live_postings)
    if include_curated_br_vacancies or not all_postings:
        sample_postings = load_sample_linkedin_vacancies(
            sample_csv="data/jobs_br_sample.csv",
            known_companies=known_company_meta,
        )
        seen_domains = {p.domain for p in all_postings if p.domain}
        for sp in sample_postings:
            if sp.domain not in seen_domains:
                all_postings.append(sp)

    # Group postings by company domain and run Stage 1 + Stage 2
    grouped_by_domain: dict[str, list[LinkedInJobPosting]] = {}
    for p in all_postings:
        dom = p.domain or f"{p.company_name.lower().replace(' ', '')}.com.br"
        grouped_by_domain.setdefault(dom, []).append(p)

    cache_ns = "vac_llm" if llm_client.is_live_api_configured else "vac_llm_heuristic"
    enriched_rows: list[dict[str, Any]] = []

    for dom, postings in grouped_by_domain.items():
        first = postings[0]
        meta = known_company_meta.get(dom, {})
        comp_input = OceanCompanyInput(
            company_name=meta.get("company_name") or first.company_name,
            domain=dom,
            linkedin_url=first.company_linkedin_url or meta.get("linkedin_url", ""),
            description=meta.get("description", ""),
            industry=meta.get("industry", "Data & Software"),
        )
        job_entries = [
            {
                "job_title": p.job_title,
                "job_description": p.job_description,
                "job_url": p.job_url,
            }
            for p in postings
        ]
        corpus = build_company_corpus(comp_input, job_entries=job_entries)
        matches = extract_b2b_brazil_chunks(corpus)
        if not matches:
            continue

        content_hash = RadarCache.compute_hash(corpus)
        cached_payload = None if force_refresh else cache.get(cache_ns, dom, expected_hash=content_hash)
        if cached_payload:
            sig = B2BLeadSignal.model_validate(cached_payload)
        else:
            sig = llm_client.qualify_b2b_company(comp_input, matches)
            cache.set(cache_ns, dom, sig.model_dump(), content_hash=content_hash)

        all_kws = sorted({kw for m in matches for kw in m.matched_keywords})
        all_cats = sorted({cat for m in matches for cat in m.matched_categories})

        enriched_rows.append(
            {
                "company_name": sig.company_name,
                "domain": sig.domain,
                "is_hot_lead": sig.pain_score >= min_score and sig.has_scraping_bottleneck,
                "pain_score": sig.pain_score,
                "has_scraping_bottleneck": sig.has_scraping_bottleneck,
                "vacancy_titles": " | ".join({p.job_title for p in postings}),
                "vacancy_urls": " | ".join({p.job_url for p in postings if p.job_url}),
                "vacancy_sources": ", ".join(sorted({p.source for p in postings})),
                "detected_targets": "; ".join(sig.detected_targets),
                "decision_maker_role": sig.decision_maker_role,
                "bottleneck_summary": sig.bottleneck_summary,
                "outreach_hook_pt": sig.outreach_hook_pt,
                "outreach_hook_en": sig.outreach_hook_en,
                "linkedin_url": comp_input.linkedin_url,
                "industry": comp_input.industry,
                "matched_categories": ", ".join(all_cats),
                "matched_keywords": ", ".join(all_kws),
            }
        )

    enriched_rows.sort(
        key=lambda r: (r["is_hot_lead"], r["has_scraping_bottleneck"], r["pain_score"]),
        reverse=True,
    )

    out_path = Path(output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(enriched_rows).to_csv(out_path, index=False, encoding="utf-8")
    return enriched_rows, all_postings


def run_b2b_brazil_pipeline(
    input_csv: str | Path = "data/ocean-data.csv",
    output_csv: str | Path = "data/outputs/b2b_brazil_hot_leads.csv",
    min_score: int = 7,
    limit: Optional[int] = None,
    scrape_websites: bool = False,
    max_scrape_domains: int = 25,
    jobs_path: Optional[str | Path] = "data/jobs_br_sample.csv",
    force_refresh: bool = False,
    use_fast_model: bool = False,
    quiet: bool = False,
) -> list[B2BLeadSignal]:
    """Execute the end-to-end VacRadar (`b2b-brazil`) two-stage bottleneck detection pipeline."""
    cache = RadarCache()
    llm_client = LLMClient()

    companies = load_ocean_csv(input_csv, limit=limit)
    job_dumps = load_local_job_dumps(jobs_path)

    if not quiet:
        console.print(
            f"\n[bold cyan]VacRadar (`vacradar.scoutpipe.com`) — B2B & LinkedIn Scraping Radar[/bold cyan]\n"
            f"• Loaded [bold]{len(companies)}[/bold] unique companies from [green]{input_csv}[/green]\n"
            f"• Attached LinkedIn/Gupy job descriptions for [bold]{len(job_dumps)}[/bold] domains\n"
            f"• LLM Engine: [bold]{'Anthropic Claude (' + llm_client.model + ')' if llm_client.is_live_api_configured else 'Heuristic Structured Extractor (set ANTHROPIC_API_KEY for live Claude)'}[/bold]"
        )

    scraped_by_domain: dict[str, str] = {}
    if scrape_websites and companies:
        domains_to_scrape = [c.domain for c in companies[:max_scrape_domains]]
        timeout_sec = float(os.getenv("SCRAPER_TIMEOUT_SECONDS", "6.0"))
        max_conc = int(os.getenv("SCRAPER_MAX_CONCURRENCY", "5"))
        scraped_by_domain = asyncio.run(
            scrape_domains_batch(
                domains=domains_to_scrape,
                cache=cache,
                timeout_seconds=timeout_sec,
                max_concurrency=max_conc,
                force_refresh=force_refresh,
            )
        )

    stage1_candidates: list[tuple[OceanCompanyInput, list[PrefilterMatch], str]] = []
    for company in companies:
        scraped_text = scraped_by_domain.get(company.domain, "")
        job_entries = job_dumps.get(company.domain, [])
        corpus = build_company_corpus(company, scraped_text=scraped_text, job_entries=job_entries)
        matches = extract_b2b_brazil_chunks(corpus)
        if matches:
            stage1_candidates.append((company, matches, corpus))

    pct = (len(stage1_candidates) / len(companies) * 100.0) if companies else 0.0
    if not quiet:
        console.print(
            f"• [bold yellow]Stage 1 Regex Pre-Filter:[/bold yellow] Retained "
            f"[bold]{len(stage1_candidates)}/{len(companies)}[/bold] companies ({pct:.1f}%) with explicit scraping/target/pain markers."
        )

    cache_ns = "b2b_llm" if llm_client.is_live_api_configured else "b2b_llm_heuristic"
    signals: list[B2BLeadSignal] = []
    metadata_by_domain: dict[str, dict[str, str]] = {}

    for company, matches, corpus in stage1_candidates:
        content_hash = RadarCache.compute_hash(corpus)
        cached_payload = None if force_refresh else cache.get(cache_ns, company.domain, expected_hash=content_hash)

        if cached_payload:
            signal = B2BLeadSignal.model_validate(cached_payload)
        else:
            signal = llm_client.qualify_b2b_company(
                company=company,
                matches=matches,
                use_fast_model=use_fast_model,
            )
            cache.set(
                namespace=cache_ns,
                cache_key=company.domain,
                payload=signal.model_dump(),
                content_hash=content_hash,
            )

        signals.append(signal)
        all_cats = sorted({cat for m in matches for cat in m.matched_categories})
        all_kws = sorted({kw for m in matches for kw in m.matched_keywords})
        job_titles = " | ".join(j.get("job_title", "") for j in job_dumps.get(company.domain, []) if j.get("job_title"))
        metadata_by_domain[company.domain] = {
            "linkedin_url": company.linkedin_url,
            "industry": company.industry,
            "vacancy_titles": job_titles,
            "matched_categories": ", ".join(all_cats),
            "matched_keywords": ", ".join(all_kws),
        }

    signals.sort(key=lambda s: (s.pain_score >= min_score, s.has_scraping_bottleneck, s.pain_score), reverse=True)
    hot_leads = [s for s in signals if s.pain_score >= min_score and s.has_scraping_bottleneck]

    out_path = Path(output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows_to_export = []
    for sig in signals:
        meta = metadata_by_domain.get(sig.domain, {})
        rows_to_export.append(
            {
                "company_name": sig.company_name,
                "domain": sig.domain,
                "is_hot_lead": sig.pain_score >= min_score and sig.has_scraping_bottleneck,
                "pain_score": sig.pain_score,
                "has_scraping_bottleneck": sig.has_scraping_bottleneck,
                "vacancy_titles": meta.get("vacancy_titles", ""),
                "detected_targets": "; ".join(sig.detected_targets),
                "decision_maker_role": sig.decision_maker_role,
                "bottleneck_summary": sig.bottleneck_summary,
                "outreach_hook_pt": sig.outreach_hook_pt,
                "outreach_hook_en": sig.outreach_hook_en,
                "linkedin_url": meta.get("linkedin_url", ""),
                "industry": meta.get("industry", ""),
                "matched_categories": meta.get("matched_categories", ""),
                "matched_keywords": meta.get("matched_keywords", ""),
            }
        )

    pd.DataFrame(rows_to_export).to_csv(out_path, index=False, encoding="utf-8")

    if not quiet:
        _print_b2b_summary_table(signals, min_score=min_score, output_path=out_path, hot_count=len(hot_leads))
    return signals


def _print_b2b_summary_table(
    signals: list[B2BLeadSignal],
    min_score: int,
    output_path: Path,
    hot_count: int,
) -> None:
    """Render a clean Rich terminal table of top B2B leads."""
    table = Table(
        title=f"VacRadar B2B & LinkedIn Scraping Leads (Hot Leads >= {min_score}: {hot_count})",
        show_lines=True,
    )
    table.add_column("Score", justify="center", style="bold magenta", no_wrap=True)
    table.add_column("Company & Domain", style="bold cyan", ratio=2)
    table.add_column("Detected Targets", style="yellow", ratio=2)
    table.add_column("Bottleneck Summary (RU)", style="white", ratio=3)
    table.add_column("Outreach Hook (PT-BR)", style="green", ratio=3)

    display_leads = [s for s in signals if s.pain_score >= min_score] or signals[:15]
    for sig in display_leads[:20]:
        score_badge = f"🔥 {sig.pain_score}/10" if sig.pain_score >= min_score else f"{sig.pain_score}/10"
        table.add_row(
            score_badge,
            f"{sig.company_name}\n[dim]{sig.domain}[/dim]\n[italic]{sig.decision_maker_role}[/italic]",
            ", ".join(sig.detected_targets[:4]),
            sig.bottleneck_summary,
            sig.outreach_hook_pt,
        )

    console.print()
    console.print(table)
    console.print(f"\n[bold green]✔ Saved {len(signals)} qualified leads to:[/bold green] {output_path}\n")
