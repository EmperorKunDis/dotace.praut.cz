"""Adaptér pro WordPress weby operačních programů.

`opzp.cz` i `opst.cz` běží na téže šabloně: přehled na `/nabidka-dotaci/`,
detaily na `/dotace/<číslo>-vyzva/`. Detailní stránky nemají `<h1>`, takže se
název bere z `<title>` a společná koncovka s názvem programu se ořízne
až v `item.apply_title_suffix`.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .. import parsing
from ..http import Fetcher, FetchError
from .base import SourceResult, harvest

DETAIL_PATTERN = re.compile(r"/dotace/[^/]+/?$", re.IGNORECASE)


def collect(
    source: dict[str, Any],
    fetcher: Fetcher,
    limit: int = 200,
    today: dt.date | None = None,
) -> SourceResult:
    result = SourceResult(source_id=source["id"])
    catalogs = [source["url"], *(source.get("extra_catalog_urls") or [])]
    candidates: list[str] = []
    for catalog_url in catalogs:
        try:
            content = fetcher.text(catalog_url)
        except FetchError as exc:
            result.warnings.append(f"{source['name']}: katalog {catalog_url} — {exc}")
            continue
        doc = parsing.parse(content, catalog_url)
        candidates.extend(link.url for link in doc.links if DETAIL_PATTERN.search(link.url))
    if not candidates:
        result.warnings.append(f"{source['name']}: v přehledu nejsou odkazy na detail výzvy")
    harvest(fetcher, source, candidates, result, limit=limit, today=today, require_relevance=False)
    return result
