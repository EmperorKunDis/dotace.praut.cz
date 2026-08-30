"""Adaptér nad XML sitemapou."""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from typing import Any

from ..http import Fetcher, FetchError
from .base import SourceResult, harvest, url_allowed


def _collect_urls(
    fetcher: Fetcher, url: str, warnings: list[str], depth: int = 0, max_depth: int = 2
) -> list[str]:
    try:
        root = ET.fromstring(fetcher.text(url))
    except (FetchError, ET.ParseError) as exc:
        warnings.append(f"sitemapa {url} — {exc}")
        return []
    locations = [node.text or "" for node in root.iter() if node.tag.endswith("loc")]
    if root.tag.endswith("sitemapindex") and depth < max_depth:
        nested: list[str] = []
        for child in locations:
            nested.extend(_collect_urls(fetcher, child, warnings, depth + 1, max_depth))
        return nested
    return locations


def collect(
    source: dict[str, Any],
    fetcher: Fetcher,
    limit: int = 200,
    today: dt.date | None = None,
) -> SourceResult:
    result = SourceResult(source_id=source["id"])
    urls = _collect_urls(fetcher, source["url"], result.warnings)
    result.warnings[:] = [f"{source['name']}: {w}" for w in result.warnings]
    if not urls:
        result.warnings.append(f"{source['name']}: sitemapa nevrátila žádné URL")
        return result
    harvest(
        fetcher,
        source,
        [url for url in urls if url_allowed(url, source)],
        result,
        limit=limit,
        today=today,
    )
    return result
