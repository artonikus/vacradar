# VacRadar (`vacradar.scoutpipe.com`)

**VacRadar** is a two-stage B2B & LinkedIn Vacancy Bottleneck Radar that detects companies hiring data miners, web-scraping engineers (`Playwright`, `Scrapy`, `Selenium`, `Puppeteer`), and crawler maintainers struggling with anti-bot systems (`Cloudflare`, `DataDome`, `reCAPTCHA`, `hCaptcha`) and high-friction Brazilian data targets (`TJSP`, `PJe`, `e-SAJ`, `Receita Federal/CNPJ`, `Mercado Livre`, `iFood`, `Shopee`).

## Quick Start

```bash
pip install -r requirements.txt

# 1. Search LinkedIn & Gupy vacancies for data-mining / scraping roles
python3 main.py search-linkedin --keywords "Playwright,Scrapy,Web Scraping,Raspagem de Dados" --location "Brazil"

# 2. Run on Ocean.io CSV + job descriptions
python3 main.py run-b2b --input data/ocean-data.csv --min-score 7

# 3. Start the Web UI (for vacradar.scoutpipe.com)
python3 main.py serve --port 8001
```
