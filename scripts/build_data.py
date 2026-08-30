#!/usr/bin/env python3
"""Sestaví statický export dotačních výzev do `data.js`.

Web je čistě statický, takže veškerá logika sběru a normalizace se odehraje
tady a do prohlížeče se dostane už jen hotový JSON. Skript je psaný tak, aby
výpadek jednoho zdroje nezpůsobil tichou ztrátu dat: každý zdroj má práh
minimálního počtu položek a varování se propisují do exportu.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

# Skript se spouští přímo (`python scripts/build_data.py`) i importuje z testů
# (`from scripts import build_data`). Kořen repozitáře na cestě zaručí, že se
# balík `scripts.dotace` v obou případech načte jako tentýž modul — jinak by
# existovaly dvě nezávislé kopie registru adaptérů.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dotace import adapters, extract, item as item_builder, parsing  # noqa: E402
from scripts.dotace.http import Fetcher  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "sources.json"
DEFAULT_DATA = ROOT / "data.js"
EXPORT_PREFIX = "window.DOTACE_EXPORT = "
EXPORT_SUFFIX = ";\n"
STATUS_CODES = {"active", "upcoming", "completed", "unknown"}
STATUS_ORDER = {"active": 0, "upcoming": 1, "unknown": 2, "completed": 3}

# Do prohlížeče posíláme zkrácený text; plné znění je na zdrojovém webu.
EXPORT_TEXT_CHARS = 1500
# Dávno uzavřené výzvy nemají pro žadatele hodnotu a jen ředí databázi.
KEEP_CLOSED_DAYS = 400
# Delší seznam příloh už nikdo neprojde a jen nafukuje stahovaný soubor.
MAX_ATTACHMENTS = 30

SOURCE_EXPORT_KEYS = (
    "id", "name", "category", "type", "url", "target_groups", "topics",
    "notes", "integration", "for_business",
)
ITEM_INTERNAL_KEYS = ("title_source", "doc_title")


# ---------------------------------------------------------------- sources ---
def load_sources(path: Path) -> list[dict[str, Any]]:
    sources = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(sources, list):
        raise ValueError("sources.json musí obsahovat seznam")
    seen: set[str] = set()
    for source in sources:
        for key in ("id", "name", "url", "integration", "adapter"):
            if not source.get(key):
                raise ValueError(f"Zdroj nemá vyplněné {key}: {source!r}")
        if source["id"] in seen:
            raise ValueError(f"Duplicitní id zdroje: {source['id']}")
        seen.add(source["id"])
        if adapters.get(source["adapter"]) is None:
            raise ValueError(f"Neznámý adaptér {source['adapter']!r} u zdroje {source['id']}")
    return sources


def source_export(source: dict[str, Any]) -> dict[str, Any]:
    exported = {key: source.get(key, "") for key in SOURCE_EXPORT_KEYS}
    exported["for_business"] = bool(source.get("for_business"))
    return {key: (value if isinstance(value, bool) else str(value)) for key, value in exported.items()}


# ------------------------------------------------------------- collecting ---
def collect_sources(
    sources: list[dict[str, Any]],
    fetcher: Fetcher,
    limit: int,
    today: dt.date,
    verbose: bool = False,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Vrátí (položky, varování, chyby prahů)."""
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    threshold_errors: list[str] = []
    for source in sources:
        host = urllib.parse.urlparse(source["url"]).netloc
        if source.get("ignore_robots"):
            fetcher.robots_exempt_hosts.add(host)
        if source.get("timeout"):
            fetcher.host_timeouts[host] = float(source["timeout"])
        adapter = adapters.get(source["adapter"])
        source_limit = int(source.get("limit", limit))
        try:
            result = adapter(source, fetcher=fetcher, limit=source_limit, today=today)
        except Exception as exc:  # zdroj nesmí shodit celý běh
            warnings.append(f"{source['name']}: adaptér selhal — {exc}")
            result = adapters.SourceResult(source_id=source["id"])
        warnings.extend(result.warnings)
        items.extend(result.items)
        minimum = int(source.get("min_items", 0))
        if minimum and len(result.items) < minimum:
            threshold_errors.append(
                f"{source['name']}: očekáváno alespoň {minimum} položek, získáno {len(result.items)}"
            )
        if verbose:
            print(f"{source['name']}: {len(result.items)} položek", file=sys.stderr)
    return items, warnings, threshold_errors


# -------------------------------------------------------------- normalize ---
def refresh_status(item: dict[str, Any], today: dt.date) -> dict[str, Any]:
    """Přepočítá stav podle dnešního data — položky stárnou i bez nového sběru."""
    opening = str(item.get("opening_date") or "")
    closing = str(item.get("closing_date") or item.get("deadline") or "")
    status, status_code = extract.status_for(opening, closing, today=today)
    item["opening_date"] = opening
    item["closing_date"] = closing
    item["deadline"] = closing
    item["status"], item["status_code"] = status, status_code
    return item


def normalize_existing(item: dict[str, Any], sources_by_id: dict[str, dict[str, Any]], today: dt.date) -> dict[str, Any]:
    item = dict(item)
    item.setdefault("applicant_types", [])
    item.setdefault("regions", [])
    item.setdefault("attachments", [])
    item.setdefault("allocation_czk", 0)
    item.setdefault("support_rate_pct", 0)
    item.setdefault("code", "")
    source = sources_by_id.get(str(item.get("source_id", "")))
    if source:
        item["source_name"] = source["name"]
        item.setdefault("for_business", bool(source.get("for_business")))
    item.setdefault("for_business", False)
    return refresh_status(item, today)


def merge_items(previous: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nová data vyhrávají, stará zůstanou, dokud je něco nenahradí."""
    merged: dict[str, dict[str, Any]] = {}
    for entry in previous:
        merged[parsing.canonical_url(str(entry.get("source_url", "")))] = entry
    for entry in fresh:
        merged[parsing.canonical_url(str(entry.get("source_url", "")))] = entry
    merged.pop("", None)
    return list(merged.values())


def prune(items: list[dict[str, Any]], today: dt.date, keep_days: int = KEEP_CLOSED_DAYS) -> list[dict[str, Any]]:
    cutoff = (today - dt.timedelta(days=keep_days)).isoformat()
    kept: list[dict[str, Any]] = []
    for entry in items:
        closing = str(entry.get("closing_date") or "")
        if closing and closing < cutoff:
            continue
        kept.append(entry)
    return kept


def _completeness(entry: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        1 if entry.get("closing_date") else 0,
        1 if entry.get("allocation_czk") else 0,
        len(entry.get("attachments") or []),
        len(str(entry.get("text", ""))),
    )


def dedupe_across_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sloučí tutéž výzvu nabízenou několika portály.

    Unijní výzvy chodí zároveň z portálu EU i z agregátoru DotaceEU. V rámci
    jednoho zdroje se nic neslučuje — stejně pojmenované produkty (třeba
    „Investiční úvěry") jsou tam legitimně různé nabídky.
    """
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in items:
        title = parsing.normalize_spaces(str(entry.get("title", ""))).lower()
        if not title:
            continue
        if title not in best:
            best[title] = entry
            order.append(title)
            continue
        incumbent = best[title]
        if incumbent.get("source_id") == entry.get("source_id"):
            order.append(f"{title}\x00{len(order)}")
            best[order[-1]] = entry
            continue
        if _completeness(entry) > _completeness(incumbent):
            best[title] = entry
    return [best[key] for key in order]


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(entry: dict[str, Any]) -> tuple[int, str, str]:
        status = STATUS_ORDER.get(str(entry.get("status_code")), 9)
        closing = str(entry.get("closing_date") or "9999-12-31")
        return (status, closing, str(entry.get("title", "")))

    return sorted(items, key=key)


def slim(item: dict[str, Any]) -> dict[str, Any]:
    entry = {key: value for key, value in item.items() if key not in ITEM_INTERNAL_KEYS}
    entry["text"] = str(entry.get("text", ""))[:EXPORT_TEXT_CHARS].rstrip()
    entry["attachments"] = list(entry.get("attachments") or [])[:MAX_ATTACHMENTS]
    return entry


def build_stats(items: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "active": sum(1 for i in items if i.get("status_code") == "active"),
        "upcoming": sum(1 for i in items if i.get("status_code") == "upcoming"),
        "completed": sum(1 for i in items if i.get("status_code") == "completed"),
        "unknown": sum(1 for i in items if i.get("status_code") == "unknown"),
        "business": sum(1 for i in items if i.get("for_business")),
        "attachments": sum(len(i.get("attachments") or []) for i in items),
        "sources": len(sources),
    }


# --------------------------------------------------------------- validate ---
def validate_export(export: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for index, entry in enumerate(export.get("items", []), start=1):
        prefix = f"items[{index}]"
        if not entry.get("title"):
            errors.append(f"{prefix}: chybí title")
        elif extract.is_blacklisted_title(str(entry["title"])):
            errors.append(f"{prefix}: titulek {entry['title']!r} je navigační šum")
        if not entry.get("source_url"):
            errors.append(f"{prefix}: chybí source_url")
        if entry.get("status_code") not in STATUS_CODES:
            errors.append(f"{prefix}: neplatný status_code {entry.get('status_code')!r}")
        for field in ("opening_date", "closing_date"):
            value = entry.get(field) or ""
            if value and not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", str(value)):
                errors.append(f"{prefix}: neplatné {field} {value!r}")
        if not isinstance(entry.get("attachments", []), list):
            errors.append(f"{prefix}: attachments musí být seznam")
        if not isinstance(entry.get("applicant_types", []), list):
            errors.append(f"{prefix}: applicant_types musí být seznam")
    return errors


# ------------------------------------------------------------------- i/o ---
def load_current_export(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith(EXPORT_PREFIX):
        raise ValueError(f"{path} nezačíná {EXPORT_PREFIX!r}")
    payload = text[len(EXPORT_PREFIX) :].rstrip()
    return json.loads(payload.rstrip(";"))


def write_export(path: Path, export: dict[str, Any]) -> None:
    path.write_text(
        EXPORT_PREFIX + json.dumps(export, ensure_ascii=False, indent=2) + EXPORT_SUFFIX,
        encoding="utf-8",
    )


# ------------------------------------------------------------------ build ---
def build_export(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    today = args.today or dt.date.today()
    sources = load_sources(args.sources)
    sources_by_id = {source["id"]: source for source in sources}
    warnings: list[str] = []
    previous: list[dict[str, Any]] = []

    if args.preserve_current and args.current_data.exists():
        current = load_current_export(args.current_data)
        previous = [normalize_existing(entry, sources_by_id, today) for entry in current.get("items", [])]
        known = set(sources_by_id)
        # Starší běhy ukládaly i navigační stránky. Nechceme kvůli nim shodit
        # build, ale ani je dál vláčet — při první příležitosti vypadnou.
        previous = [
            entry
            for entry in previous
            if str(entry.get("source_id", "")) in known
            and not extract.is_blacklisted_title(str(entry.get("title", "")))
        ]

    selected = sources
    if getattr(args, "only", ""):
        wanted = {value.strip() for value in args.only.split(",") if value.strip()}
        unknown = wanted - set(sources_by_id)
        if unknown:
            raise ValueError(f"Neznámá id zdrojů: {', '.join(sorted(unknown))}")
        selected = [source for source in sources if source["id"] in wanted]

    fresh: list[dict[str, Any]] = []
    if not args.offline_current:
        with Fetcher(
            timeout=args.timeout,
            delay=args.delay,
            respect_robots=not args.ignore_robots,
        ) as fetcher:
            fresh, source_warnings, threshold_errors = collect_sources(
                selected, fetcher, limit=args.limit_per_source, today=today, verbose=args.verbose
            )
        warnings.extend(source_warnings)
        if threshold_errors and not args.ignore_thresholds:
            raise ValueError("Zdroje nedodaly očekávaný počet položek:\n" + "\n".join(threshold_errors))
        warnings.extend(threshold_errors)

    items = prune(sort_items(dedupe_across_sources(merge_items(previous, fresh))), today)
    exported = [slim(entry) for entry in items]
    export = {
        "generated_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "stats": build_stats(exported, sources),
        "warnings": warnings,
        "items": exported,
        "sources": [source_export(source) for source in sources],
    }
    errors = validate_export(export)
    if errors:
        raise ValueError("Kontrola exportu selhala:\n" + "\n".join(errors[:20]))
    return export, warnings


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sestaví data.js z oficiálních dotačních zdrojů.")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--current-data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--limit-per-source", type=int, default=80)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay", type=float, default=0.7, help="Minimální prodleva mezi požadavky na jeden host.")
    parser.add_argument("--offline-current", action="store_true", help="Bez sítě: jen přepočítá stavy a katalog zdrojů.")
    parser.add_argument("--fresh", action="store_true", help="Nepřebírat položky ze stávajícího data.js.")
    parser.add_argument("--ignore-thresholds", action="store_true", help="Nezastavit build, když zdroj nedodá min_items.")
    parser.add_argument("--ignore-robots", action="store_true")
    parser.add_argument(
        "--only",
        default="",
        help="Sbírat jen uvedená id zdrojů (oddělená čárkou). Ostatní se převezmou ze stávajícího data.js.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    args.preserve_current = not args.fresh
    args.today = None
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        export, warnings = build_export(args)
    except Exception as exc:
        print(f"build_data.py: {exc}", file=sys.stderr)
        return 1
    write_export(args.output, export)
    for warning in warnings:
        print(f"varování: {warning}", file=sys.stderr)
    stats = export["stats"]
    print(
        f"Zapsáno {args.output}: {stats['total']} položek "
        f"({stats['active']} probíhajících, {stats['upcoming']} připravovaných) "
        f"z {stats['sources']} zdrojů."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
