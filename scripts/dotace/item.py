"""Sestavení normalizované položky z rozparsované stránky."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any

from . import extract, parsing

MAX_TEXT = 6000
MAX_SUMMARY = 420

_CODE_PATTERNS = (
    re.compile(r"\b(\d{1,3})\.\s*výzv", re.IGNORECASE),
    re.compile(r"\bvýzva\s*č\.?\s*(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\bvýzv[ay]\s+([IVXLC]{1,6})\b"),
)


def stable_item_id(source_id: str, url: str) -> int:
    digest = hashlib.sha1(f"{source_id}:{parsing.canonical_url(url)}".encode("utf-8")).hexdigest()
    return int(digest[:10], 16)


def extract_code(title: str) -> str:
    for pattern in _CODE_PATTERNS:
        match = pattern.search(title or "")
        if match:
            return match.group(1)
    return ""


def summarize(doc: parsing.Doc) -> str:
    for paragraph in doc.paragraphs:
        if len(paragraph) >= 60:
            return paragraph[:MAX_SUMMARY].rstrip()
    if doc.paragraphs:
        return doc.paragraphs[0][:MAX_SUMMARY].rstrip()
    return doc.text[:MAX_SUMMARY].rstrip()


def build_item(
    source: dict[str, Any],
    url: str,
    doc: parsing.Doc,
    today: dt.date | None = None,
) -> dict[str, Any] | None:
    title = parsing.normalize_spaces(doc.title)
    if not title or extract.is_blacklisted_title(title):
        return None

    text = doc.text or doc.full_text
    opening, closing = extract.extract_period(text)
    status, status_code = extract.status_for(opening, closing, today=today)
    item_type, type_code = extract.classify(title, url, text)
    applicants = extract.extract_applicants(text)
    return {
        "id": stable_item_id(source["id"], url),
        "title": title,
        "title_source": doc.title_source,
        "doc_title": doc.doc_title,
        "code": extract_code(title) or extract.extract_code_from_text(text),
        "type": item_type,
        "type_code": type_code,
        "status": status,
        "status_code": status_code,
        "opening_date": opening,
        "closing_date": closing,
        "deadline": closing,
        "source_url": parsing.canonical_url(url),
        "source_id": source["id"],
        "source_name": source["name"],
        "program": extract.infer_program(text, f"{source.get('topics', '')} {title}"),
        "allocation_czk": extract.extract_allocation(text),
        "support_rate_pct": extract.extract_support_rate(text),
        "applicant_types": applicants,
        "regions": extract.extract_regions(text),
        "for_business": bool(source.get("for_business")) or bool(set(applicants) & extract.BUSINESS_APPLICANTS),
        "summary": summarize(doc),
        "text": text[:MAX_TEXT].rstrip(),
        "attachments": parsing.attachments_from(doc.links),
    }


def apply_title_suffix(items: list[dict[str, Any]]) -> None:
    """Ořeže z titulků společnou koncovku s názvem webu.

    Týká se jen položek, jejichž titulek pochází z `<title>` — u `<h1>` je
    název čistý. Koncovka se odvodí ze vzorku stránek stejného zdroje, takže
    není potřeba pro každý web udržovat ruční pravidlo.
    """
    from_title = [item for item in items if item.get("title_source") in {"title", "og"}]
    if not from_title:
        return
    suffix = parsing.common_title_suffix([item["title"] for item in from_title])
    if not suffix:
        return
    for item in from_title:
        item["title"] = parsing.strip_title_suffix(item["title"], suffix)
