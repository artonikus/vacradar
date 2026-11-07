"""Web Application & API Server for VacRadar (`vacradar.scoutpipe.com`).

Provides a browser dashboard to:
1. Search LinkedIn & Gupy vacancies for companies hiring data miners / web scraping engineers.
2. Run the two-stage Bottleneck Radar on Ocean.io company exports (`data/ocean-data.csv`).
3. Filter leads by `pain_score`, copy PT-BR/EN outreach hooks, download CSV outputs,
   and download the standalone `vacradar.zip` project archive.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import zipfile

import pandas as pd

from config.keywords_b2b_br import DEFAULT_LINKEDIN_SEARCH_QUERIES
from modes.b2b_brazil import (
    run_b2b_brazil_pipeline,
    search_and_qualify_linkedin_vacancies,
)

BASE_DIR = Path(__file__).resolve().parent

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>VacRadar — LinkedIn & B2B Scraping Bottleneck Radar (vacradar.scoutpipe.com)</title>
  <style>
    :root {
      --bg: #0b0f17;
      --panel: #131b2e;
      --panel-border: #23304d;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --accent-hover: #0284c7;
      --hot: #f43f5e;
      --warm: #f59e0b;
      --ok: #10b981;
      --tag-bg: #1e293b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }
    header {
      background: linear-gradient(90deg, #0f172a 0%, #1e1b4b 100%);
      border-bottom: 1px solid var(--panel-border);
      padding: 18px 28px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .badge-domain {
      background: rgba(56, 189, 248, 0.15);
      color: var(--accent);
      border: 1px solid rgba(56, 189, 248, 0.35);
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
    }
    h1 {
      margin: 0;
      font-size: 20px;
      letter-spacing: -0.02em;
    }
    .header-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .btn {
      background: var(--accent);
      color: #0f172a;
      border: none;
      padding: 9px 16px;
      border-radius: 8px;
      font-weight: 600;
      font-size: 13px;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    .btn:hover { background: var(--accent-hover); color: #fff; }
    .btn-secondary {
      background: #1e293b;
      color: var(--text);
      border: 1px solid var(--panel-border);
    }
    .btn-secondary:hover { background: #334155; }
    main {
      max-width: 1440px;
      margin: 0 auto;
      padding: 24px 28px;
    }
    .controls-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 18px;
      margin-bottom: 22px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 12px;
      padding: 18px 20px;
    }
    .card h2 {
      margin: 0 0 8px 0;
      font-size: 16px;
      color: #f8fafc;
    }
    .card p {
      margin: 0 0 14px 0;
      font-size: 13px;
      color: var(--muted);
    }
    .form-row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }
    input[type="text"], input[type="number"], select {
      background: #0b1120;
      border: 1px solid var(--panel-border);
      color: var(--text);
      padding: 8px 12px;
      border-radius: 7px;
      font-size: 13px;
      flex: 1;
      min-width: 140px;
    }
    .stats-bar {
      display: flex;
      gap: 18px;
      flex-wrap: wrap;
      margin-bottom: 18px;
      padding: 12px 16px;
      background: rgba(19, 27, 46, 0.7);
      border: 1px solid var(--panel-border);
      border-radius: 10px;
      font-size: 13px;
    }
    .stat-item strong {
      color: var(--accent);
      font-size: 15px;
    }
    .table-wrap {
      overflow-x: auto;
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 12px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      padding: 14px 16px;
      text-align: left;
      border-bottom: 1px solid var(--panel-border);
      vertical-align: top;
    }
    th {
      background: #0f172a;
      color: var(--muted);
      font-weight: 600;
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.05em;
    }
    tr:hover td {
      background: rgba(30, 41, 59, 0.45);
    }
    .score-pill {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-weight: 700;
      font-size: 12px;
    }
    .score-hot { background: rgba(244, 63, 94, 0.2); color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.4); }
    .score-warm { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }
    .score-low { background: rgba(148, 163, 184, 0.15); color: #94a3b8; }
    .tag {
      display: inline-block;
      background: var(--tag-bg);
      border: 1px solid #334155;
      color: #cbd5e1;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 11px;
      margin: 2px 4px 2px 0;
    }
    .tag-vacancy {
      background: rgba(16, 185, 129, 0.15);
      border-color: rgba(16, 185, 129, 0.4);
      color: #34d399;
    }
    .hook-box {
      background: #0b1120;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 8px 10px;
      margin-bottom: 6px;
      font-size: 12px;
    }
    .company-links a {
      color: var(--accent);
      text-decoration: none;
      margin-right: 10px;
      font-size: 12px;
    }
    .company-links a:hover { text-decoration: underline; }
    #status-msg {
      color: var(--accent);
      font-weight: 600;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>🎯 VacRadar</h1>
      <span class="badge-domain">vacradar.scoutpipe.com</span>
      <span style="color: var(--muted); font-size: 13px;">LinkedIn & B2B Scraping Bottleneck Radar</span>
    </div>
    <div class="header-actions">
      <a class="btn btn-secondary" href="/api/download-csv">⬇ Export Leads CSV</a>
      <a class="btn" href="/api/download-project-zip">📦 Download vacradar.zip</a>
    </div>
  </header>

  <main>
    <div class="controls-grid">
      <div class="card">
        <h2>🔍 1. Search LinkedIn & Gupy Vacancies (Data Miners / Scrapers)</h2>
        <p>Find companies actively hiring engineers for Playwright, Scrapy, Selenium, anti-bot bypass, and data extraction.</p>
        <div class="form-row">
          <input type="text" id="li-keywords" value="Web Scraping OR Playwright OR Scrapy, Raspagem de Dados OR Extração de Dados" placeholder="Keywords (comma-separated)" />
        </div>
        <div class="form-row">
          <input type="text" id="li-location" value="Brazil" placeholder="Location (e.g. Brazil)" style="max-width: 180px;" />
          <button class="btn" onclick="runLinkedInScan()">🚀 Scan LinkedIn & Gupy Vacancies</button>
        </div>
      </div>

      <div class="card">
        <h2>🏢 2. Filter & Run Ocean.io B2B Dataset (729 BR Companies)</h2>
        <p>Stage 1 Regex Pre-Filter + Stage 2 Qualification across Brazilian LegalTech, FinTech, and E-Commerce companies.</p>
        <div class="form-row">
          <input type="text" id="search-filter" placeholder="Filter by company, target (TJSP, PJe, Mercado Livre, Cloudflare)..." oninput="renderTable()" />
          <select id="min-score-filter" onchange="renderTable()" style="max-width: 170px;">
            <option value="7" selected>Hot Leads (Score >= 7)</option>
            <option value="9">Urgent Only (Score >= 9)</option>
            <option value="1">All Matched (Score >= 1)</option>
          </select>
          <button class="btn btn-secondary" onclick="runFullB2B()">↻ Re-Run Full Dataset</button>
        </div>
      </div>
    </div>

    <div class="stats-bar">
      <div class="stat-item">Total Qualified Companies: <strong id="stat-total">0</strong></div>
      <div class="stat-item">Hot Leads (Score &ge; 7): <strong id="stat-hot">0</strong></div>
      <div class="stat-item">With Active Scraping Vacancies: <strong id="stat-vacancies">0</strong></div>
      <div id="status-msg">Ready</div>
    </div>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th style="width: 90px;">Pain Score</th>
            <th style="width: 230px;">Company & Role</th>
            <th style="width: 250px;">LinkedIn Vacancies & Targets</th>
            <th style="width: 310px;">Bottleneck Summary (RU)</th>
            <th>Outreach Hooks (PT-BR & EN)</th>
          </tr>
        </thead>
        <tbody id="leads-tbody">
          <tr><td colspan="5" style="text-align:center; padding: 30px;">Loading qualified leads...</td></tr>
        </tbody>
      </table>
    </div>
  </main>

  <script>
    let allLeads = [];

    async function loadInitialLeads() {
      document.getElementById('status-msg').textContent = 'Loading leads...';
      const res = await fetch('/api/leads');
      const data = await res.json();
      allLeads = data.leads || [];
      document.getElementById('status-msg').textContent = `Loaded ${allLeads.length} qualified companies`;
      renderTable();
    }

    async function runLinkedInScan() {
      const keywords = document.getElementById('li-keywords').value;
      const location = document.getElementById('li-location').value;
      document.getElementById('status-msg').textContent = 'Scanning LinkedIn & Gupy vacancies...';
      const res = await fetch('/api/search-linkedin', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ keywords, location })
      });
      const data = await res.json();
      allLeads = data.leads || [];
      document.getElementById('status-msg').textContent = `LinkedIn scan complete: ${allLeads.length} companies with scraping vacancies`;
      renderTable();
    }

    async function runFullB2B() {
      document.getElementById('status-msg').textContent = 'Running Stage 1 + Stage 2 on Ocean.io dataset...';
      const res = await fetch('/api/run-b2b', { method: 'POST' });
      const data = await res.json();
      allLeads = data.leads || [];
      document.getElementById('status-msg').textContent = `Full B2B scan complete (${allLeads.length} leads)`;
      renderTable();
    }

    function escapeHtml(str) {
      return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function renderTable() {
      const query = (document.getElementById('search-filter').value || '').toLowerCase();
      const minScore = parseInt(document.getElementById('min-score-filter').value || '1', 10);

      const hotCount = allLeads.filter(l => Number(l.pain_score) >= 7).length;
      const vacCount = allLeads.filter(l => (l.vacancy_titles || '').trim().length > 0).length;
      document.getElementById('stat-total').textContent = allLeads.length;
      document.getElementById('stat-hot').textContent = hotCount;
      document.getElementById('stat-vacancies').textContent = vacCount;

      const filtered = allLeads.filter(l => {
        if (Number(l.pain_score) < minScore) return false;
        if (!query) return true;
        const hay = [
          l.company_name, l.domain, l.detected_targets,
          l.vacancy_titles, l.matched_keywords, l.bottleneck_summary
        ].join(' ').toLowerCase();
        return hay.includes(query);
      });

      const tbody = document.getElementById('leads-tbody');
      if (!filtered.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 28px; color: #94a3b8;">No leads match current filter. Try lowering Min Score to >= 1.</td></tr>';
        return;
      }

      tbody.innerHTML = filtered.map(l => {
        const score = Number(l.pain_score);
        const pillClass = score >= 8 ? 'score-hot' : (score >= 7 ? 'score-warm' : 'score-low');
        const targets = String(l.detected_targets || '').split(';').map(t => t.trim()).filter(Boolean);
        const vacancies = String(l.vacancy_titles || '').split('|').map(v => v.trim()).filter(Boolean);
        const kws = String(l.matched_keywords || '').split(',').map(k => k.trim()).filter(Boolean).slice(0, 8);

        return `
          <tr>
            <td>
              <span class="score-pill ${pillClass}">🔥 ${score}/10</span>
            </td>
            <td>
              <div style="font-weight: 700; font-size: 14px; color: #f8fafc;">${escapeHtml(l.company_name)}</div>
              <div style="color: #94a3b8; font-size: 12px; margin-bottom: 4px;">${escapeHtml(l.domain)}</div>
              <div style="color: #38bdf8; font-size: 11px; margin-bottom: 6px;">${escapeHtml(l.decision_maker_role)}</div>
              <div class="company-links">
                <a href="https://${escapeHtml(l.domain)}" target="_blank" rel="noopener">🌐 Website</a>
                ${l.linkedin_url ? `<a href="${escapeHtml(l.linkedin_url)}" target="_blank" rel="noopener">💼 LinkedIn</a>` : ''}
              </div>
            </td>
            <td>
              ${vacancies.map(v => `<span class="tag tag-vacancy">🧑‍💻 ${escapeHtml(v)}</span>`).join('')}
              <div style="margin-top: 6px;">
                ${targets.map(t => `<span class="tag">🎯 ${escapeHtml(t)}</span>`).join('')}
              </div>
              <div style="margin-top: 6px; color: #64748b; font-size: 11px;">
                Triggers: ${escapeHtml(kws.join(', '))}
              </div>
            </td>
            <td>${escapeHtml(l.bottleneck_summary)}</td>
            <td>
              <div class="hook-box"><strong>🇧🇷 PT-BR:</strong> ${escapeHtml(l.outreach_hook_pt)}</div>
              <div class="hook-box"><strong>🇺🇸 EN:</strong> ${escapeHtml(l.outreach_hook_en)}</div>
            </td>
          </tr>
        `;
      }).join('');
    }

    loadInitialLeads();
  </script>
</body>
</html>
"""


def _ensure_leads_csv() -> Path:
    """Ensure `data/outputs/b2b_brazil_hot_leads.csv` exists and return its path."""
    csv_path = BASE_DIR / "data" / "outputs" / "b2b_brazil_hot_leads.csv"
    if not csv_path.exists():
        run_b2b_brazil_pipeline(
            input_csv=BASE_DIR / "data" / "ocean-data.csv",
            output_csv=csv_path,
            min_score=7,
            jobs_path=BASE_DIR / "data" / "jobs_br_sample.csv",
            quiet=True,
        )
    return csv_path


def _build_project_zip_bytes() -> bytes:
    """Pack the entire `vacradar/` directory into an in-memory ZIP archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(BASE_DIR.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(BASE_DIR)
            if any(part in {"__pycache__", ".cache", ".venv"} for part in rel.parts):
                continue
            if file_path.suffix in {".pyc", ".db", ".sqlite3"}:
                continue
            zf.write(file_path, arcname=str(Path("vacradar") / rel))
    return buf.getvalue()


class VacRadarRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the VacRadar web UI and JSON API endpoints."""

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/leads":
            csv_path = _ensure_leads_csv()
            df = pd.read_csv(csv_path).fillna("")
            self._send_json({"leads": df.to_dict(orient="records")})
            return

        if parsed.path == "/api/download-csv":
            csv_path = _ensure_leads_csv()
            data = csv_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="vacradar_hot_leads.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path == "/api/download-project-zip":
            zip_bytes = _build_project_zip_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="vacradar.zip"')
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            body_json = json.loads(raw_body) if raw_body.strip() else {}
        except json.JSONDecodeError:
            body_json = {}

        if parsed.path == "/api/search-linkedin":
            raw_kw = str(body_json.get("keywords", ""))
            queries = [q.strip() for q in raw_kw.split(",") if q.strip()] or list(DEFAULT_LINKEDIN_SEARCH_QUERIES)
            location = str(body_json.get("location", "Brazil")).strip() or "Brazil"
            rows, _ = search_and_qualify_linkedin_vacancies(
                queries=queries,
                location=location,
                output_csv=BASE_DIR / "data" / "outputs" / "linkedin_vacancy_leads.csv",
                ocean_reference_csv=BASE_DIR / "data" / "ocean-data.csv",
            )
            self._send_json({"leads": rows})
            return

        if parsed.path == "/api/run-b2b":
            csv_path = BASE_DIR / "data" / "outputs" / "b2b_brazil_hot_leads.csv"
            run_b2b_brazil_pipeline(
                input_csv=BASE_DIR / "data" / "ocean-data.csv",
                output_csv=csv_path,
                min_score=7,
                jobs_path=BASE_DIR / "data" / "jobs_br_sample.csv",
                quiet=True,
            )
            df = pd.read_csv(csv_path).fillna("")
            self._send_json({"leads": df.to_dict(orient="records")})
            return

        self._send_json({"error": "Not found"}, status=404)


def run_web_server(host: str = "0.0.0.0", port: Optional[int] = None) -> None:
    """Start the VacRadar HTTP server."""
    resolved_port = port or int(os.getenv("PORT", "8001"))
    _ensure_leads_csv()
    server = ThreadingHTTPServer((host, resolved_port), VacRadarRequestHandler)
    print(f"VacRadar web interface listening on http://{host}:{resolved_port}")
    server.serve_forever()


if __name__ == "__main__":
    run_web_server()
