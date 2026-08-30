"""Adaptér pro centrální portál evropských fondů v ČR.

Přehled je postavený na ASP.NET WebForms — stránkování jede přes postback, takže
se z jednoho načtení dostaneme na aktuální dávku výzev. Odkazy vedou napříč
všemi operačními programy, unijními programy i finančními nástroji.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .. import parsing
from ..http import Fetcher, FetchError
from .base import SourceResult, harvest

DETAIL_PATTERN = re.compile(r"/Vyzvy/(Obdobi-|Unijni-programy|Financni-nastroje)", re.IGNORECASE)


def collect(
    source: dict[str, Any],
    fetcher: Fetcher,
    limit: int = 200,
    today: dt.date | None = None,
) -> SourceResult:
    result = SourceResult(source_id=source["id"])
    try:
        content = fetcher.text(source["url"])
    except FetchError as exc:
        result.warnings.append(f"{source['name']}: přehled se nepodařilo stáhnout — {exc}")
        return result
    doc = parsing.parse(content, source["url"])
    candidates = [link.url for link in doc.links if DETAIL_PATTERN.search(link.url)]
    if not candidates:
        result.warnings.append(f"{source['name']}: v přehledu nejsou odkazy na detail výzvy")
    harvest(fetcher, source, candidates, result, limit=limit, today=today, require_relevance=False)
    return result
