"""Obecný adaptér: katalogová stránka -> odkazy na detaily -> položky."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .. import item as item_builder
from .. import parsing
from ..http import Fetcher, FetchError
from .base import SourceResult, harvest, link_is_candidate


def _catalog_pages(source: dict[str, Any]) -> list[str]:
    """URL katalogu včetně případného stránkování."""
    pages = [source["url"]]
    template = source.get("page_url_template")
    max_pages = int(source.get("max_pages", 1))
    if template and max_pages > 1:
        pages.extend(template.format(page=page) for page in range(2, max_pages + 1))
    return pages


def collect(
    source: dict[str, Any],
    fetcher: Fetcher,
    limit: int = 200,
    today: dt.date | None = None,
) -> SourceResult:
    result = SourceResult(source_id=source["id"])
    candidates: list[str] = []
    for page_url in _catalog_pages(source):
        try:
            catalog = fetcher.text(page_url)
        except FetchError as exc:
            result.warnings.append(f"{source['name']}: katalog {page_url} — {exc}")
            continue
        doc = parsing.parse(catalog, page_url)
        candidates.extend(link.url for link in doc.links if link_is_candidate(link, source))
    if not candidates:
        result.warnings.append(f"{source['name']}: katalog nevrátil žádné kandidáty na detail")
    harvest(fetcher, source, candidates, result, limit=limit, today=today)
    return result


def collect_reference(
    source: dict[str, Any],
    fetcher: Fetcher,
    limit: int = 200,
    today: dt.date | None = None,
) -> SourceResult:
    """Zdroj, který se eviduje jako jediná referenční stránka."""
    result = SourceResult(source_id=source["id"])
    try:
        content = fetcher.text(source["url"])
    except FetchError as exc:
        result.warnings.append(f"{source['name']}: {exc}")
        return result
    doc = parsing.parse(content, source["url"])
    item = item_builder.build_item(source, source["url"], doc, today=today)
    if item:
        item["type"], item["type_code"] = "Stránka", "page"
        result.items.append(item)
    return result
