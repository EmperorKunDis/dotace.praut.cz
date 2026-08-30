"""Registr adaptérů pro jednotlivé typy zdrojů."""

from __future__ import annotations

from typing import Callable

from . import dotaceeu, html_catalog, rss, sedia_api, sitemap, wordpress_vyzvy
from .base import SourceResult

ADAPTERS: dict[str, Callable[..., SourceResult]] = {
    "manual": lambda source, **_kwargs: SourceResult(source_id=source["id"]),
    "html_catalog": html_catalog.collect,
    "reference_page": html_catalog.collect_reference,
    "sitemap": sitemap.collect,
    "rss": rss.collect,
    "wordpress_vyzvy": wordpress_vyzvy.collect,
    "dotaceeu": dotaceeu.collect,
    "sedia_api": sedia_api.collect,
}


def get(name: str) -> Callable[..., SourceResult] | None:
    return ADAPTERS.get(name)


__all__ = ["ADAPTERS", "SourceResult", "get"]
