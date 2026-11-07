"""Stage 1: Zero-cost Lexical & Regex Pre-Filter for VacRadar (`vacradar.scoutpipe.com`).

Filters job descriptions, career pages, and company profiles down to the paragraphs
containing explicit web-scraping, data-mining, anti-bot, or Brazilian target markers.
"""

from __future__ import annotations

import re
from typing import Iterable

from bs4 import BeautifulSoup

from config.keywords_b2b_br import B2B_BR_CATEGORY_PATTERNS, B2B_BR_COMBINED_PATTERN
from core.models import PrefilterMatch

_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{2,}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú0-9\"'(\[])")


def html_to_clean_text(html_content: str) -> str:
    """Convert raw HTML into clean paragraph-separated plain text."""
    if not html_content or not html_content.strip():
        return ""

    if "<" not in html_content and ">" not in html_content:
        return html_content.strip()

    soup = BeautifulSoup(html_content, "html.parser")
    for noisy_tag in soup(["script", "style", "noscript", "svg", "iframe", "header", "footer", "nav"]):
        noisy_tag.decompose()

    block_tags = {"p", "div", "li", "section", "article", "h1", "h2", "h3", "h4", "tr", "blockquote"}
    for tag in soup.find_all(block_tags):
        tag.insert_before("\n\n")
        tag.insert_after("\n\n")

    raw_text = soup.get_text(separator=" ")
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in raw_text.splitlines()]
    cleaned_lines = [line for line in lines if line]
    return "\n\n".join(cleaned_lines)


def split_into_chunks(
    text: str,
    min_chunk_chars: int = 25,
    max_chunk_chars: int = 900,
) -> list[str]:
    """Split unstructured text into normalized paragraph chunks ready for regex scanning."""
    if not text or not text.strip():
        return []

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_paragraphs = _MULTI_NEWLINE_RE.split(normalized)

    chunks: list[str] = []
    pending_prefix = ""

    for raw_para in raw_paragraphs:
        para = _WHITESPACE_RE.sub(" ", raw_para.replace("\n", " ")).strip()
        if not para:
            continue

        if len(para) < min_chunk_chars:
            pending_prefix = f"{pending_prefix} | {para}".strip(" |") if pending_prefix else para
            continue

        if pending_prefix:
            para = f"{pending_prefix}: {para}"
            pending_prefix = ""

        if len(para) <= max_chunk_chars:
            chunks.append(para)
        else:
            sentences = _SENTENCE_SPLIT_RE.split(para)
            current = ""
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                if len(current) + len(sentence) + 1 <= max_chunk_chars:
                    current = f"{current} {sentence}".strip() if current else sentence
                else:
                    if current:
                        chunks.append(current)
                    if len(sentence) <= max_chunk_chars:
                        current = sentence
                    else:
                        for i in range(0, len(sentence), max_chunk_chars):
                            chunks.append(sentence[i : i + max_chunk_chars])
                        current = ""
            if current:
                chunks.append(current)

    if pending_prefix:
        if chunks:
            chunks[-1] = f"{chunks[-1]} ({pending_prefix})"
        else:
            chunks.append(pending_prefix)

    return chunks


def extract_b2b_brazil_chunks(
    text: str,
    max_chunks: int = 20,
) -> list[PrefilterMatch]:
    """Stage 1 Pre-Filter for VacRadar (`b2b-brazil`).

    Extracts paragraphs matching Scraping & Automation Stack, Anti-Bot & Blocking Pain,
    High-Friction Brazilian Targets, or Pipeline Pain Markers.
    """
    chunks: Iterable[str] = split_into_chunks(text, min_chunk_chars=25, max_chunk_chars=900)
    matches: list[PrefilterMatch] = []
    seen_signatures: set[str] = set()

    for chunk in chunks:
        if not B2B_BR_COMBINED_PATTERN.search(chunk):
            continue

        signature = re.sub(r"\W+", "", chunk.lower())[:160]
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        matched_categories: list[str] = []
        matched_keywords_set: set[str] = set()

        for category, pattern in B2B_BR_CATEGORY_PATTERNS.items():
            found = [m.group(0).lower() for m in pattern.finditer(chunk)]
            if found:
                matched_categories.append(category)
                matched_keywords_set.update(found)

        if matched_categories:
            is_job_chunk = "job description" in chunk.lower() or "vaga" in chunk.lower() or "linkedin" in chunk.lower()
            matches.append(
                PrefilterMatch(
                    chunk_text=chunk,
                    matched_categories=matched_categories,
                    matched_keywords=sorted(matched_keywords_set),
                    section_weight=1.5 if is_job_chunk else 1.0,
                )
            )

    matches.sort(key=lambda m: m.relevance_score, reverse=True)
    return matches[:max_chunks]


def format_matches_for_llm(
    matches: list[PrefilterMatch],
    max_total_chars: int = 12000,
) -> str:
    """Format Stage 1 matched chunks into a compact prompt payload for Stage 2 LLM."""
    if not matches:
        return ""

    blocks: list[str] = []
    total_chars = 0

    for idx, match in enumerate(matches, start=1):
        categories_str = ", ".join(match.matched_categories)
        keywords_str = ", ".join(match.matched_keywords)
        block = (
            f"[Chunk #{idx} | Categories: {categories_str} | "
            f"Triggers: {keywords_str}]\n{match.chunk_text}"
        )
        if total_chars + len(block) > max_total_chars and blocks:
            break
        blocks.append(block)
        total_chars += len(block) + 2

    return "\n\n".join(blocks)
