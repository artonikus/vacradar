"""VacRadar (`vacradar.scoutpipe.com`) — Typer CLI Entry Point.

Commands:
  1. python main.py search-linkedin --keywords "Playwright,Scrapy,Web Scraping" --location "Brazil"
  2. python main.py run-b2b --input data/ocean-data.csv --min-score 7
  3. python main.py serve --port 8001
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
import typer

from config.keywords_b2b_br import DEFAULT_LINKEDIN_SEARCH_QUERIES
from core.cache import RadarCache
from modes.b2b_brazil import (
    run_b2b_brazil_pipeline,
    search_and_qualify_linkedin_vacancies,
)
from web_app import run_web_server

load_dotenv()

console = Console()
app = typer.Typer(
    name="vacradar",
    help="VacRadar (vacradar.scoutpipe.com): LinkedIn Vacancy & B2B Data-Scraping Bottleneck Radar.",
    add_completion=False,
    no_args_is_help=True,
)


@app.command("search-linkedin")
def search_linkedin(
    keywords: str = typer.Option(
        ",".join(DEFAULT_LINKEDIN_SEARCH_QUERIES),
        "--keywords",
        "-k",
        help="Comma-separated LinkedIn/Gupy job search queries for data miners & web scrapers.",
    ),
    location: str = typer.Option(
        "Brazil",
        "--location",
        "-l",
        help="Target job location (e.g. Brazil, Worldwide, United States).",
    ),
    min_score: int = typer.Option(
        7,
        "--min-score",
        help="Minimum pain_score (1-10) threshold for hot leads.",
    ),
    output_csv: str = typer.Option(
        "data/outputs/linkedin_vacancy_leads.csv",
        "--output",
        "-o",
        help="Output CSV path for discovered LinkedIn vacancy leads.",
    ),
    force_refresh: bool = typer.Option(
        False,
        "--force-refresh",
        help="Bypass SQLite cache and fetch fresh LinkedIn/Gupy job postings.",
    ),
) -> None:
    """Search LinkedIn & Gupy for data-mining/scraping vacancies and score companies."""
    queries = [q.strip() for q in keywords.split(",") if q.strip()]
    console.print(
        f"\n[bold cyan]VacRadar — LinkedIn & Gupy Scraping Vacancy Search[/bold cyan]\n"
        f"• Queries: [bold]{' | '.join(queries)}[/bold]\n"
        f"• Location: [bold]{location}[/bold]"
    )
    rows, postings = search_and_qualify_linkedin_vacancies(
        queries=queries,
        location=location,
        min_score=min_score,
        output_csv=output_csv,
        force_refresh=force_refresh,
    )

    table = Table(
        title=f"VacRadar LinkedIn Scraping Vacancies ({len(rows)} Qualified Companies / {len(postings)} Postings)",
        show_lines=True,
    )
    table.add_column("Score", justify="center", style="bold magenta", no_wrap=True)
    table.add_column("Company & Domain", style="bold cyan", ratio=2)
    table.add_column("Active Scraping Vacancy", style="green", ratio=2)
    table.add_column("Detected Targets", style="yellow", ratio=2)
    table.add_column("Bottleneck Summary (RU)", style="white", ratio=3)

    for r in rows[:20]:
        score = int(r["pain_score"])
        badge = f"🔥 {score}/10" if score >= min_score else f"{score}/10"
        table.add_row(
            badge,
            f"{r['company_name']}\n[dim]{r['domain']}[/dim]",
            str(r.get("vacancy_titles", "")),
            str(r.get("detected_targets", "")),
            str(r.get("bottleneck_summary", "")),
        )

    console.print()
    console.print(table)
    console.print(f"\n[bold green]✔ Saved LinkedIn vacancy leads to:[/bold green] {output_csv}\n")


@app.command("run-b2b")
def run_b2b(
    input_csv: str = typer.Option(
        "data/ocean-data.csv",
        "--input",
        "-i",
        help="Path to Ocean.io CSV export.",
    ),
    output_csv: str = typer.Option(
        "data/outputs/b2b_brazil_hot_leads.csv",
        "--output",
        "-o",
        help="Output CSV path for scored B2B leads.",
    ),
    min_score: int = typer.Option(
        7,
        "--min-score",
        help="Minimum pain_score (1-10) threshold for highlighting hot leads.",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-n",
        help="Optional maximum number of companies to ingest from the CSV.",
    ),
    scrape: bool = typer.Option(
        False,
        "--scrape/--no-scrape",
        help="Enable live async HTTP scraping of /, /careers, /vagas, /trabalhe-conosco, /tech.",
    ),
    jobs_file: Optional[str] = typer.Option(
        "data/jobs_br_sample.csv",
        "--jobs-file",
        help="Optional CSV or directory of Brazilian job descriptions (LinkedIn / Gupy).",
    ),
    force_refresh: bool = typer.Option(
        False,
        "--force-refresh",
        help="Bypass SQLite deduplication cache.",
    ),
    fast: bool = typer.Option(
        False,
        "--fast",
        help="Use ANTHROPIC_FAST_MODEL (claude-3-5-haiku-latest).",
    ),
) -> None:
    """Run VacRadar on Ocean.io CSV + LinkedIn/Gupy job descriptions."""
    run_b2b_brazil_pipeline(
        input_csv=Path(input_csv),
        output_csv=Path(output_csv),
        min_score=min_score,
        limit=limit,
        scrape_websites=scrape,
        jobs_path=Path(jobs_file) if jobs_file and Path(jobs_file).exists() else None,
        force_refresh=force_refresh,
        use_fast_model=fast,
    )


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host address."),
    port: int = typer.Option(8001, "--port", "-p", help="Bind port for vacradar web UI."),
) -> None:
    """Launch the VacRadar Web UI server (for vacradar.scoutpipe.com)."""
    run_web_server(host=host, port=port)


@app.command("clear-cache")
def clear_cache(
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n"),
) -> None:
    """Clear the local SQLite deduplication cache."""
    cache = RadarCache()
    removed = cache.clear(namespace=namespace)
    console.print(f"[bold green]✔ Cleared {removed} cached records.[/bold green]")


if __name__ == "__main__":
    app()
