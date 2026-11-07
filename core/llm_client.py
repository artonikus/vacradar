"""Stage 2: Anthropic Claude Wrapper with Pydantic Structured Outputs for VacRadar (`vacradar.scoutpipe.com`)."""

from __future__ import annotations

import os
from typing import Optional, Type, TypeVar

from anthropic import Anthropic, APIError, APITimeoutError, RateLimitError
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.models import B2BLeadSignal, OceanCompanyInput, PrefilterMatch
from core.prefilter import format_matches_for_llm

T = TypeVar("T", bound=BaseModel)

_PLACEHOLDER_KEYS = {
    "",
    "your_anthropic_api_key_here",
    "sk-ant-xxx",
    "none",
    "null",
}


class LLMClient:
    """Anthropic Claude client enforcing Pydantic structured outputs via tool calling."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        fast_model: Optional[str] = None,
    ) -> None:
        raw_key = (api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "")).strip()
        self.api_key = "" if raw_key.lower() in _PLACEHOLDER_KEYS else raw_key
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
        self.fast_model = fast_model or os.getenv("ANTHROPIC_FAST_MODEL", "claude-3-5-haiku-latest")
        self._client: Optional[Anthropic] = Anthropic(api_key=self.api_key) if self.api_key else None

    @property
    def is_live_api_configured(self) -> bool:
        """Return True if a non-placeholder Anthropic API key is configured."""
        return self._client is not None

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=2, max=16),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIError)),
    )
    def _extract_structured(
        self,
        schema: Type[T],
        system_prompt: str,
        user_prompt: str,
        tool_name: str,
        tool_description: str,
        use_fast_model: bool = False,
        max_tokens: int = 2048,
    ) -> T:
        """Invoke Anthropic Messages API with forced tool_choice to guarantee Pydantic schema output."""
        if not self._client:
            raise RuntimeError("Anthropic API client is not configured.")

        chosen_model = self.fast_model if use_fast_model else self.model
        json_schema = schema.model_json_schema()

        response = self._client.messages.create(
            model=chosen_model,
            max_tokens=max_tokens,
            temperature=0.1,
            system=system_prompt,
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": json_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user_prompt}],
        )

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                return schema.model_validate(block.input)

        raise ValueError(f"Claude response did not contain expected tool_use block '{tool_name}'.")

    def qualify_b2b_company(
        self,
        company: OceanCompanyInput,
        matches: list[PrefilterMatch],
        use_fast_model: bool = False,
    ) -> B2BLeadSignal:
        """Run Stage 2 qualification for a B2B company/vacancy based on Stage 1 matched chunks."""
        if not self.is_live_api_configured:
            return self._heuristic_qualify_b2b(company, matches)

        formatted_chunks = format_matches_for_llm(matches, max_total_chars=10000)
        system_prompt = (
            "You are a senior B2B technical sales engineer at ScoutPipe (managed web data extraction, "
            "anti-bot bypass, and real-time Brazilian public/commercial data pipelines).\n"
            "Analyze the company profile, LinkedIn job vacancies, and Stage 1 regex-extracted text chunks.\n"
            "Determine if the company is hiring data miners / scraping engineers or relies on web scraping, "
            "court/government portal extraction (TJSP, PJe, e-SAJ, Receita Federal, CNPJ, Serasa, Detran, Diário Oficial), "
            "or e-commerce monitoring (Mercado Livre, iFood, Shopee) and faces scraping/anti-bot/pipeline pain.\n"
            "Scoring rubric for `pain_score` (1-10):\n"
            "- 9-10: Actively hiring scraping engineers or maintaining complex crawlers across hard Brazilian "
            "targets (courts/PJe/TJSP, Receita Federal, Mercado Livre) with anti-bot/CAPTCHA/layout-breakage pain.\n"
            "- 7-8: Core product depends on automated external public/legal/pricing data collection in Brazil.\n"
            "- 4-6: Uses external data or basic automation, moderate scraping dependency.\n"
            "- 1-3: Incidental keyword match only.\n"
            "Write `bottleneck_summary` as 1 concise sentence in Russian explaining their exact technical pain.\n"
            "Write `outreach_hook_pt` as a sharp, non-spammy 2-sentence cold email opener in natural Brazilian "
            "Portuguese referencing their specific data sources/pain. Write `outreach_hook_en` in English."
        )

        user_prompt = (
            f"Company Name: {company.company_name}\n"
            f"Domain: {company.domain}\n"
            f"Industry: {company.industry}\n"
            f"Description: {company.description}\n"
            f"Keywords: {company.keywords}\n"
            f"Buying/Hiring Signals: {company.buying_signals}\n\n"
            f"--- STAGE 1 MATCHED CHUNKS ---\n{formatted_chunks}"
        )

        try:
            return self._extract_structured(
                schema=B2BLeadSignal,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tool_name="record_b2b_lead_signal",
                tool_description="Record structured B2B scraping bottleneck qualification and outreach hooks.",
                use_fast_model=use_fast_model,
            )
        except Exception:
            return self._heuristic_qualify_b2b(company, matches)

    def _heuristic_qualify_b2b(
        self,
        company: OceanCompanyInput,
        matches: list[PrefilterMatch],
    ) -> B2BLeadSignal:
        """Deterministic Stage 2 fallback scorer when ANTHROPIC_API_KEY is not configured."""
        all_categories: set[str] = set()
        all_keywords: set[str] = set()
        for m in matches:
            all_categories.update(m.matched_categories)
            all_keywords.update(k.lower() for k in m.matched_keywords)

        target_map = {
            "pje": "Tribunais PJe",
            "tjsp": "TJSP / e-SAJ",
            "esaj": "TJSP / e-SAJ",
            "projudi": "Projudi",
            "eproc": "eproc",
            "tribunais": "Tribunais Estaduais/Federais",
            "jusbrasil": "Jusbrasil / Diários Oficiais",
            "diário oficial": "Diários Oficiais",
            "diario oficial": "Diários Oficiais",
            "receita federal": "Receita Federal / CNPJ",
            "cnpj": "Receita Federal / CNPJ",
            "sintegra": "Sintegra / SEFAZ",
            "sefaz": "Sintegra / SEFAZ",
            "serasa": "Serasa / Bureaus de Crédito",
            "detran": "Portais Detran",
            "gov.br": "Portais Gov.br",
            "precatórios": "Portais de Precatórios / Tribunais",
            "mandados de prisão": "BNMP / Mandados Judiciais",
            "mercado livre": "Mercado Livre",
            "ifood": "iFood",
            "shopee": "Shopee",
            "cloudflare": "Portais protegidos por Cloudflare",
            "datadome": "Portais protegidos por DataDome",
            "recaptcha": "Portais com reCAPTCHA / Anti-bot",
            "hcaptcha": "Portais com hCaptcha",
            "turnstile": "Portais com Cloudflare Turnstile",
            "quebra de captcha": "Sistemas com bloqueio CAPTCHA",
        }

        detected_targets: list[str] = []
        for kw, label in target_map.items():
            if kw in all_keywords and label not in detected_targets:
                detected_targets.append(label)

        if not detected_targets and "scraping_automation_stack" in all_categories:
            detected_targets.append("Portais públicos e fontes web brasileiras")

        score = 3
        if "scraping_automation_stack" in all_categories:
            score += 2
        if "high_friction_brazilian_targets" in all_categories:
            score += 2
        if "antibot_blocking_pain" in all_categories:
            score += 2
        if "pipeline_pain_markers" in all_categories:
            score += 2

        high_intent_terms = {
            "playwright",
            "puppeteer",
            "selenium",
            "scrapy",
            "raspagem de dados",
            "captura antecipada",
            "consulta processual",
            "monitoramento de comunicações processuais",
            "quebra de captcha",
            "rotação de proxy",
            "proxy rotation",
            "manutenção de robôs",
            "quebra de layout",
        }
        overlap = all_keywords.intersection(high_intent_terms)
        if len(overlap) >= 2:
            score = max(score, 9)
        elif len(overlap) == 1 and len(all_categories) >= 2:
            score = max(score, 8)
        elif len(all_categories) >= 3:
            score = max(score, 8)
        elif len(all_categories) == 2:
            score = max(score, 7)

        score = min(10, max(1, score))
        has_bottleneck = score >= 6 or ("scraping_automation_stack" in all_categories)

        targets_str = ", ".join(detected_targets[:3]) if detected_targets else "внешних бразильских веб-источников"
        triggers_str = ", ".join(sorted(all_keywords)[:4]) if all_keywords else "web-источников"

        bottleneck_summary = (
            f"Компания {company.company_name} зависит от автоматизированного сбора данных из {targets_str} "
            f"(триггеры: {triggers_str}) и сталкивается с затратами на поддержку парсеров и обход блокировок."
        )

        if any(k in all_keywords for k in ("pje", "tjsp", "esaj", "consulta processual", "precatórios", "tribunais")):
            decision_maker = "CTO / Head of Legal Data / Engineering Manager"
            hook_pt = (
                f"Vi que a {company.company_name} opera captura e monitoramento em fontes como {targets_str}. "
                f"Como vocês estão lidando hoje com quebras de layout, CAPTCHAs e manutenção de robôs nesses tribunais sem drenar o time de engenharia?"
            )
            hook_en = (
                f"Noticed {company.company_name} runs automated legal data collection across Brazilian sources like {targets_str}. "
                f"How is your engineering team handling CAPTCHA walls and layout changes across those portals without constant scraper maintenance?"
            )
        elif any(k in all_keywords for k in ("mercado livre", "ifood", "shopee")):
            decision_maker = "CTO / Head of Data Engineering / VP of Product"
            hook_pt = (
                f"Acompanhei o trabalho da {company.company_name} com monitoramento de dados em marketplaces como {targets_str}. "
                f"Na ScoutPipe ajudamos times de dados a eliminar bloqueios de IP/anti-bot e manter a coleta contínua sem precisar escalar infraestrutura própria de proxies."
            )
            hook_en = (
                f"Saw how {company.company_name} tracks high-frequency marketplace data across {targets_str}. "
                f"At ScoutPipe we offload anti-bot bypass and residential proxy rotation so your engineers don't spend cycles fixing blocked crawlers."
            )
        else:
            decision_maker = "CTO / Head of Data / Tech Lead"
            hook_pt = (
                f"Notei que a operação da {company.company_name} exige coleta e estruturação contínua de dados públicos ({targets_str}). "
                f"Construímos na ScoutPipe uma infraestrutura gerenciada que absorve a rotação de proxies, CAPTCHAs e mudanças de layout para entregar os dados prontos via API."
            )
            hook_en = (
                f"Saw that {company.company_name} relies on continuous external data extraction ({targets_str}). "
                f"We built ScoutPipe to handle anti-bot bypass, proxy rotation, and layout breakages so your team receives clean structured feeds via API."
            )

        return B2BLeadSignal(
            company_name=company.company_name,
            domain=company.domain,
            has_scraping_bottleneck=has_bottleneck,
            pain_score=score,
            detected_targets=detected_targets or ["Fontes públicas / Web Scraping BR"],
            bottleneck_summary=bottleneck_summary,
            decision_maker_role=decision_maker,
            outreach_hook_pt=hook_pt,
            outreach_hook_en=hook_en,
        )
