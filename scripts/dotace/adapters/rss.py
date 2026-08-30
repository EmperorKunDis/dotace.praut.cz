"""Adaptér nad RSS/Atom kanálem.

Řada portálů publikuje výzvy v kanálu, který je stabilnější než HTML katalog
(u `esfcr.cz` je to jediná cesta, jak se dostat k výzvám a ne k metodikám).
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from typing import Any

from ..http import Fetcher, FetchError
from .base import SourceResult, harvest, url_allowed


def _entry_links(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    links: list[str] = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in {"item", "entry"}:
            continue
        for child in item:
            child_tag = child.tag.rsplit("}", 1)[-1]
            if child_tag != "link":
                continue
            url = (child.text or "").strip() or (child.attrib.get("href") or "").strip()
            if url:
                links.append(url)
                break
    return links


def collect(
    source: dict[str, Any],
    fetcher: Fetcher,
    limit: int = 200,
    today: dt.date | None = None,
) -> SourceResult:
    result = SourceResult(source_id=source["id"])
    try:
        xml_text = fetcher.text(source["url"])
    except FetchError as exc:
        result.warnings.append(f"{source['name']}: kanál se nepodařilo stáhnout — {exc}")
        return result
    try:
        links = _entry_links(xml_text)
    except ET.ParseError as exc:
        result.warnings.append(f"{source['name']}: kanál není validní XML — {exc}")
        return result
    if not links:
        result.warnings.append(f"{source['name']}: kanál neobsahuje žádné položky")
        return result
    harvest(
        fetcher,
        source,
        [url for url in links if url_allowed(url, source)],
        result,
        limit=limit,
        today=today,
    )
    return result
