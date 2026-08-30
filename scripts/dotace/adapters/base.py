"""Sdílené stavební bloky adaptérů."""

from __future__ import annotations

import datetime as dt
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from .. import item as item_builder
from .. import parsing
from ..http import Fetcher, FetchError

DEFAULT_KEYWORDS = ("výzva", "vyzva", "dotace", "grant", "soutěž", "soutez", "podpora", "program")


@dataclass
class SourceResult:
    source_id: str
    items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False


def _registrable_host(url: str) -> str:
    """Host bez `www.` — weby odkazují na sebe v obou variantách."""
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def url_allowed(url: str, source: dict[str, Any]) -> bool:
    lower = url.lower()
    include = source.get("include_url_patterns") or []
    exclude = source.get("exclude_url_patterns") or []
    if include and not any(pattern.lower() in lower for pattern in include):
        return False
    return not any(pattern.lower() in lower for pattern in exclude)


def link_is_candidate(link: parsing.Link, source: dict[str, Any]) -> bool:
    url = link.url
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith(parsing.ATTACHMENT_EXTENSIONS):
        return False
    source_host = _registrable_host(source["url"])
    link_host = _registrable_host(url)
    if not source.get("allow_external") and source_host and link_host != source_host:
        return False
    if not url_allowed(url, source):
        return False
    keywords = source.get("candidate_keywords") or DEFAULT_KEYWORDS
    haystack = f"{url} {link.text}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def item_is_relevant(item: dict[str, Any], source: dict[str, Any]) -> bool:
    keywords = source.get("candidate_keywords") or DEFAULT_KEYWORDS
    haystack = f"{item.get('title', '')} {item.get('text', '')[:800]} {item.get('source_url', '')}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def fetch_detail(
    fetcher: Fetcher,
    source: dict[str, Any],
    url: str,
    result: SourceResult,
    today: dt.date | None = None,
) -> dict[str, Any] | None:
    """Stáhne detail a vrátí normalizovanou položku, nebo None."""
    try:
        content = fetcher.text(url)
    except FetchError as exc:
        result.warnings.append(f"{source['name']}: detail se nepodařilo stáhnout — {exc}")
        return None
    doc = parsing.parse(content, url)
    return item_builder.build_item(source, url, doc, today=today)


def harvest(
    fetcher: Fetcher,
    source: dict[str, Any],
    urls: list[str],
    result: SourceResult,
    limit: int,
    today: dt.date | None = None,
    require_relevance: bool = True,
) -> None:
    """Projde kandidátní URL, stáhne detaily a naplní výsledek."""
    seen: set[str] = set()
    for url in urls:
        if len(result.items) >= limit:
            result.truncated = True
            result.warnings.append(
                f"{source['name']}: dosažen limit {limit} položek, zbytek nebyl načten"
            )
            break
        url = parsing.canonical_url(url)
        if url in seen:
            continue
        seen.add(url)
        item = fetch_detail(fetcher, source, url, result, today=today)
        if not item:
            continue
        if require_relevance and not item_is_relevant(item, source):
            continue
        result.items.append(item)
    item_builder.apply_title_suffix(result.items)
