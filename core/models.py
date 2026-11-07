"""Pydantic data models and structured LLM output schemas for VacRadar (`vacradar.scoutpipe.com`)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class OceanCompanyInput(BaseModel):
    """Normalized company record loaded from Ocean.io CSV exports or LinkedIn vacancy discovery."""

    company_name: str = Field(..., description="Company display name")
    domain: str = Field(..., description="Primary company website domain")
    linkedin_url: str = Field(default="", description="Company LinkedIn URL")
    description: str = Field(default="", description="Company description from Ocean.io")
    industry: str = Field(default="", description="Company primary industry or tags")
    keywords: str = Field(default="", description="Additional keywords from Ocean.io")
    technologies: str = Field(default="", description="Detected tech stack from Ocean.io")
    buying_signals: str = Field(default="", description="Buying/hiring signals from Ocean.io")

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        for prefix in ("https://", "http://", "www."):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :]
        return cleaned.strip("/")


class LinkedInJobPosting(BaseModel):
    """Represents a job vacancy discovered via LinkedIn Jobs search or career pages (Gupy/LinkedIn)."""

    job_id: str = Field(default="", description="LinkedIn or job board unique ID")
    job_title: str = Field(..., description="Vacancy title (e.g. 'Engenheiro de Web Scraping Sênior')")
    company_name: str = Field(..., description="Hiring company name")
    domain: str = Field(default="", description="Company domain if known or inferred")
    location: str = Field(default="Brazil", description="Job location")
    job_url: str = Field(default="", description="Direct URL to the job posting on LinkedIn/Gupy")
    company_linkedin_url: str = Field(default="", description="Company LinkedIn profile URL")
    posted_date: str = Field(default="", description="Date or relative time posted")
    job_description: str = Field(default="", description="Full text of the job description")
    source: str = Field(default="linkedin", description="Source of the vacancy: linkedin, gupy, or csv")


class PrefilterMatch(BaseModel):
    """Represents a single text paragraph/chunk matched during Stage 1 regex pre-filtering."""

    chunk_text: str = Field(..., description="Extracted paragraph or text chunk")
    matched_categories: list[str] = Field(
        default_factory=list,
        description="Category names triggered by this chunk",
    )
    matched_keywords: list[str] = Field(
        default_factory=list,
        description="Distinct keywords/phrases found in this chunk",
    )
    section_weight: float = Field(
        default=1.0,
        description="Priority weight (e.g. 1.5x for live job descriptions)",
    )

    @property
    def relevance_score(self) -> float:
        return (len(self.matched_categories) * 2.0 + len(self.matched_keywords)) * self.section_weight


class B2BLeadSignal(BaseModel):
    """Stage 2 LLM Structured Output Schema for VacRadar (`b2b-brazil`)."""

    company_name: str = Field(..., description="Target company name")
    domain: str = Field(..., description="Target company domain")
    has_scraping_bottleneck: bool = Field(
        ...,
        description="True if the company exhibits data collection, anti-bot, or scraping pipeline bottlenecks",
    )
    pain_score: int = Field(
        ...,
        ge=1,
        le=10,
        description="Urgency score from 1 to 10 (10 = urgent broken scrapers / actively hiring scraping engineers for hard targets)",
    )
    detected_targets: list[str] = Field(
        default_factory=list,
        description='Specific targets or anti-bot systems, e.g. ["Tribunais PJe/TJSP", "Mercado Livre", "Cloudflare-protected portals"]',
    )
    bottleneck_summary: str = Field(
        ...,
        description="1 concise sentence in Russian explaining their exact technical pain",
    )
    decision_maker_role: str = Field(
        ...,
        description='Target buyer persona, e.g. "CTO / Head of Data / Tech Lead"',
    )
    outreach_hook_pt: str = Field(
        ...,
        description="A sharp, non-spammy 2-sentence cold email opener in natural Brazilian Portuguese referencing their specific data source/pain",
    )
    outreach_hook_en: str = Field(
        ...,
        description="Same sharp 2-sentence cold email opener in English",
    )
