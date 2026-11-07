"""Stage 1 Regex & Keyword Dictionaries for VacRadar (`vacradar.scoutpipe.com` — PT-BR and EN)."""

from __future__ import annotations

import re
from typing import Final

SCRAPING_AUTOMATION_STACK: Final[list[str]] = [
    "playwright",
    "puppeteer",
    "selenium",
    "scrapy",
    "beautifulsoup",
    "crawler",
    "crawlers",
    "web scraping",
    "data mining",
    "raspagem de dados",
    "extração de dados",
    "extracao de dados",
    "captura de dados",
    "coleta de dados",
    "captura antecipada",
    "consulta processual",
    "monitoramento de comunicações processuais",
    "automação de coleta",
    "robôs de captura",
    "headless browser",
]

ANTIBOT_BLOCKING_PAIN: Final[list[str]] = [
    "cloudflare",
    "datadome",
    "perimeterx",
    "akamai",
    "azion",
    "hcaptcha",
    "recaptcha",
    "turnstile",
    "antibot",
    "anti-bot",
    "bloqueio de ip",
    "proxy rotation",
    "rotação de proxy",
    "rotacao de proxy",
    "fingerprint",
    "quebra de captcha",
    "captcha",
    "proxies residenciais",
    "residential proxy",
]

HIGH_FRICTION_BRAZILIAN_TARGETS: Final[list[str]] = [
    "receita federal",
    "cnpj",
    "tjsp",
    "pje",
    "esaj",
    "jusbrasil",
    "diário oficial",
    "diario oficial",
    "detran",
    "serasa",
    "gov.br",
    "mercado livre",
    "ifood",
    "shopee",
    "projudi",
    "eproc",
    "tribunais",
    "sintegra",
    "sefaz",
    "precatórios",
    "mandados de prisão",
]

PIPELINE_PAIN_MARKERS: Final[list[str]] = [
    "instabilidade",
    "manutenção de robôs",
    "manutencao de robos",
    "quebra de layout",
    "latência",
    "latencia",
    "escalabilidade",
    "dados desatualizados",
    "saneamento de base",
    "dados em tempo real",
    "real-time legal",
    "monitoramento automático",
]

B2B_BR_CATEGORIES: Final[dict[str, list[str]]] = {
    "scraping_automation_stack": SCRAPING_AUTOMATION_STACK,
    "antibot_blocking_pain": ANTIBOT_BLOCKING_PAIN,
    "high_friction_brazilian_targets": HIGH_FRICTION_BRAZILIAN_TARGETS,
    "pipeline_pain_markers": PIPELINE_PAIN_MARKERS,
}


def _compile_category_pattern(keywords: list[str]) -> re.Pattern[str]:
    """Compile a case-insensitive regex matching any keyword/phrase with word boundaries."""
    escaped = sorted((re.escape(k.strip()) for k in keywords if k.strip()), key=len, reverse=True)
    pattern = r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)"
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


B2B_BR_CATEGORY_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    category: _compile_category_pattern(keywords)
    for category, keywords in B2B_BR_CATEGORIES.items()
}

ALL_B2B_BR_KEYWORDS: Final[list[str]] = [
    kw for kws in B2B_BR_CATEGORIES.values() for kw in kws
]

B2B_BR_COMBINED_PATTERN: Final[re.Pattern[str]] = _compile_category_pattern(ALL_B2B_BR_KEYWORDS)

B2B_CAREER_AND_TECH_PATHS: Final[list[str]] = [
    "/",
    "/careers",
    "/vagas",
    "/trabalhe-conosco",
    "/tech",
]

DEFAULT_LINKEDIN_SEARCH_QUERIES: Final[list[str]] = [
    "Web Scraping OR Playwright OR Scrapy",
    "Raspagem de Dados OR Extração de Dados",
    "Selenium OR Puppeteer OR Crawlers",
    "Coleta de Dados OR Robôs de Captura",
]
