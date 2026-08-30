"""Adaptér nad oficiálním vyhledávacím API portálu EU Funding & Tenders.

Portál je Angular aplikace, takže z HTML se nedá vytáhnout nic použitelného.
SEDIA API vrací tytéž výzvy strojově čitelně — Horizon Europe, EIC, Digital
Europe, LIFE a další přímo řízené programy, o které může česká firma žádat.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from ..http import Fetcher, FetchError
from .base import SourceResult

API_URL = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
API_KEY = "SEDIA"
STATUS_FORTHCOMING = "31094501"
STATUS_OPEN = "31094502"
TOPIC_DETAIL_URL = (
    "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/"
    "opportunities/topic-details/{identifier}"
)
PAGE_SIZE = 100
MAX_PAGES = 8

# Program se pozná z prefixu identifikátoru výzvy — je stabilnější než číselné
# kódy frameworkProgramme, které se mezi obdobími mění.
PROGRAM_PREFIXES = (
    ("HORIZON", "Horizon Europe"),
    ("EIC", "EIC"),
    ("DIGITAL", "Digital Europe"),
    ("LIFE", "LIFE"),
    ("CEF", "CEF"),
    ("INNOVFUND", "Innovation Fund"),
    ("ERASMUS", "Erasmus+"),
    ("CREA", "Creative Europe"),
    ("EDF", "Evropský obranný fond"),
    ("SMP", "Single Market Programme"),
    ("EU4H", "EU4Health"),
    ("CERV", "CERV"),
)


def _program_for(identifier: str) -> str:
    upper = (identifier or "").upper()
    for prefix, name in PROGRAM_PREFIXES:
        if upper.startswith(prefix):
            return name
    return ""


def _first(values: Any) -> str:
    if isinstance(values, list):
        return str(values[0]) if values else ""
    return str(values or "")


def _iso_date(value: str) -> str:
    if not value:
        return ""
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value[:10] if len(value) >= 10 else ""


def _query() -> str:
    return json.dumps(
        {
            "bool": {
                "must": [
                    {"terms": {"type": ["1", "2"]}},
                    {"terms": {"status": [STATUS_OPEN, STATUS_FORTHCOMING]}},
                    {"term": {"language": "en"}},
                ]
            }
        }
    )


def collect(
    source: dict[str, Any],
    fetcher: Fetcher,
    limit: int = 120,
    today: dt.date | None = None,
) -> SourceResult:
    result = SourceResult(source_id=source["id"])
    today = today or dt.date.today()
    collected: dict[str, dict[str, Any]] = {}

    for page in range(1, MAX_PAGES + 1):
        params = {"apiKey": API_KEY, "text": "***", "pageSize": PAGE_SIZE, "pageNumber": page}
        try:
            payload = fetcher.post_json(
                API_URL,
                params=params,
                headers={"Accept": "application/json"},
                files={"query": (None, _query(), "application/json")},
            )
        except (FetchError, ValueError) as exc:
            result.warnings.append(f"{source['name']}: SEDIA API strana {page} — {exc}")
            break
        results = payload.get("results") or []
        if not results:
            break
        for entry in results:
            item = _to_item(source, entry, today)
            if item:
                collected.setdefault(item["source_url"], item)
        if len(results) < PAGE_SIZE:
            break

    items = sorted(collected.values(), key=lambda entry: entry["closing_date"] or "9999-12-31")
    if len(items) > limit:
        result.truncated = True
        result.warnings.append(
            f"{source['name']}: nalezeno {len(items)} otevřených výzev, uloženo nejbližších {limit}"
        )
        items = items[:limit]
    result.items = items
    if not items:
        result.warnings.append(f"{source['name']}: SEDIA API nevrátilo žádné otevřené výzvy")
    return result


def _to_item(source: dict[str, Any], entry: dict[str, Any], today: dt.date) -> dict[str, Any] | None:
    metadata = entry.get("metadata") or {}
    identifier = _first(metadata.get("identifier"))
    title = _first(metadata.get("title")) or entry.get("content") or ""
    if not identifier or len(title) < 5:
        return None
    closing = _iso_date(_first(metadata.get("deadlineDate")))
    if closing and closing < today.isoformat():
        return None
    opening = _iso_date(_first(metadata.get("startDate")))
    url = TOPIC_DETAIL_URL.format(identifier=identifier)
    status_code = _first(metadata.get("status"))
    if status_code == STATUS_FORTHCOMING:
        status, code = "Připravované", "upcoming"
    else:
        status, code = "Probíhající", "active"
    summary = (entry.get("summary") or entry.get("content") or "").strip()

    from .. import item as item_builder

    return {
        "id": item_builder.stable_item_id(source["id"], url),
        "title": title.strip(),
        "title_source": "api",
        "doc_title": "",
        "code": identifier,
        "type": "Dotace",
        "type_code": "grant",
        "status": status,
        "status_code": code,
        "opening_date": opening,
        "closing_date": closing,
        "deadline": closing,
        "source_url": url,
        "source_id": source["id"],
        "source_name": source["name"],
        "program": _program_for(identifier),
        "allocation_czk": 0,
        "support_rate_pct": 0,
        "applicant_types": ["msp", "velky_podnik", "vyzkumna_organizace"],
        "regions": [],
        "for_business": True,
        "summary": summary[:420],
        "text": summary[:6000],
        "attachments": [],
    }
