#!/usr/bin/env python3
"""Build the static Dotace Manager export.

The script intentionally uses only the Python standard library. The source
websites differ a lot, so adapters are conservative and return normalized data
instead of trying to mirror every source-specific field.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = ROOT / "sources.json"
DEFAULT_DATA = ROOT / "data.js"
EXPORT_PREFIX = "window.DOTACE_EXPORT = "
EXPORT_SUFFIX = ";\n"
ATTACHMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rtf", ".odt", ".ods")
SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "header", "footer"}
NAVIGATION_PREFIXES = (
    "CZ EN Domů",
    "EN CZ Domů",
    "Domů OP TAK Výzvy Statistiky",
)
MONTHS = {
    "ledna": 1,
    "leden": 1,
    "února": 2,
    "unora": 2,
    "únor": 2,
    "unor": 2,
    "března": 3,
    "brezna": 3,
    "březen": 3,
    "brezen": 3,
    "dubna": 4,
    "duben": 4,
    "května": 5,
    "kvetna": 5,
    "květen": 5,
    "kveten": 5,
    "června": 6,
    "cervna": 6,
    "červen": 6,
    "cerven": 6,
    "července": 7,
    "cervence": 7,
    "červenec": 7,
    "cervenec": 7,
    "srpna": 8,
    "srpen": 8,
    "září": 9,
    "zari": 9,
    "října": 10,
    "rijna": 10,
    "říjen": 10,
    "rijen": 10,
    "listopadu": 11,
    "listopad": 11,
    "prosince": 12,
    "prosinec": 12,
}


@dataclass
class Link:
    url: str
    text: str


@dataclass
class ParsedHtml:
    title: str
    headings: list[str]
    paragraphs: list[str]
    text: str
    links: list[Link]


@dataclass
class SourceResult:
    source_id: str
    items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class BasicHtmlExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.skip_depth = 0
        self.current_link: str | None = None
        self.current_link_text: list[str] = []
        self.current_block: str | None = None
        self.current_block_text: list[str] = []
        self.title_text: list[str] = []
        self.in_title = False
        self.text_parts: list[str] = []
        self.headings: list[str] = []
        self.paragraphs: list[str] = []
        self.links: list[Link] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        attrs_dict = {key.lower(): value for key, value in attrs if value}
        if tag == "a" and attrs_dict.get("href"):
            self.current_link = urllib.parse.urljoin(self.base_url, attrs_dict["href"])
            self.current_link_text = []
        if tag == "title":
            self.in_title = True
        if tag in {"h1", "h2", "h3", "p", "li"}:
            self.current_block = tag
            self.current_block_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "a" and self.current_link:
            label = normalize_spaces(" ".join(self.current_link_text))
            self.links.append(Link(self.current_link, label))
            self.current_link = None
            self.current_link_text = []
        if tag == "title":
            self.in_title = False
        if tag == self.current_block:
            text = normalize_spaces(" ".join(self.current_block_text))
            if text:
                if tag in {"h1", "h2", "h3"}:
                    self.headings.append(text)
                elif len(text) > 20:
                    self.paragraphs.append(text)
            self.current_block = None
            self.current_block_text = []

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = normalize_spaces(data)
        if not text:
            return
        self.text_parts.append(text)
        if self.current_link is not None:
            self.current_link_text.append(text)
        if self.current_block is not None:
            self.current_block_text.append(text)
        if self.in_title:
            self.title_text.append(text)


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def parse_html(content: str, base_url: str) -> ParsedHtml:
    parser = BasicHtmlExtractor(base_url)
    parser.feed(content)
    title = parser.headings[0] if parser.headings else normalize_spaces(" ".join(parser.title_text))
    text = clean_text(" ".join(parser.text_parts))
    return ParsedHtml(
        title=title,
        headings=parser.headings,
        paragraphs=parser.paragraphs,
        text=text,
        links=dedupe_links(parser.links),
    )


def clean_text(value: str, max_chars: int = 2200) -> str:
    value = normalize_spaces(value)
    for prefix in NAVIGATION_PREFIXES:
        if value.startswith(prefix):
            marker = " Agentura pro podnikání a inovace "
            if marker in value:
                value = value.split(marker, 1)[-1]
    value = re.sub(r"(CZ EN Domů .{80,}? Kontakty)\s+", "", value)
    value = normalize_spaces(value)
    return value[:max_chars].rstrip()


def dedupe_links(links: list[Link]) -> list[Link]:
    seen: set[str] = set()
    result: list[Link] = []
    for link in links:
        url = canonical_url(link.url)
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(Link(url, link.text or Path(urllib.parse.urlparse(url).path).name or url))
    return result


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    parsed = parsed._replace(fragment="")
    return urllib.parse.urlunparse(parsed)


def fetch_text(url: str, timeout: int) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DotaceManagerBot/1.0 (+https://github.com/static-export)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        encoding = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(encoding, errors="replace")


def classify_type(title: str, url: str, text: str) -> tuple[str, str]:
    haystack = f"{title} {url} {text[:500]}".lower()
    if any(word in haystack for word in ("webinář", "webinar", "seminář", "seminar", "konference", "/udalosti/")):
        return "Akce", "event"
    if any(word in haystack for word in ("faq", "otázky", "otazky")):
        return "FAQ", "faq"
    if any(word in haystack for word in ("výzva", "vyzva", "dotace", "soutěž", "soutez", "grant", "podpora")):
        return "Dotace", "grant"
    if any(word in haystack for word in ("aktualita", "novinka", "zpráva", "zprava")):
        return "Článek", "article"
    return "Stránka", "page"


def find_deadline(text: str) -> str:
    candidates = parse_dates(text)
    if not candidates:
        return ""
    lower = text.lower()
    scored: list[tuple[int, dt.date]] = []
    for found_date, start in candidates:
        context = lower[max(0, start - 80) : start + 80]
        score = 0
        if any(word in context for word in ("do ", "nejpozději", "nejpozdeji", "příjem", "prijem", "uzávěr", "uzaver", "deadline")):
            score += 10
        if any(word in context for word in ("vyhláš", "vyhlas", "zveřejně", "zverejne")):
            score -= 4
        scored.append((score, found_date))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = scored[0][1]
    return best.isoformat()


def parse_dates(text: str) -> list[tuple[dt.date, int]]:
    dates: list[tuple[dt.date, int]] = []
    for match in re.finditer(r"\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\b", text):
        day, month, year = map(int, match.groups())
        add_date(dates, year, month, day, match.start())
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    for match in re.finditer(rf"\b(\d{{1,2}})\.\s*({month_names})\s+(20\d{{2}})\b", text, flags=re.IGNORECASE):
        day = int(match.group(1))
        month = MONTHS[match.group(2).lower()]
        year = int(match.group(3))
        add_date(dates, year, month, day, match.start())
    return dates


def add_date(result: list[tuple[dt.date, int]], year: int, month: int, day: int, index: int) -> None:
    try:
        result.append((dt.date(year, month, day), index))
    except ValueError:
        return


def status_from_deadline(deadline: str, today: dt.date | None = None) -> tuple[str, str]:
    if not deadline:
        return "Neznámé", "unknown"
    today = today or dt.date.today()
    try:
        deadline_date = dt.date.fromisoformat(deadline)
    except ValueError:
        return "Neznámé", "unknown"
    if deadline_date >= today:
        return "Probíhající", "active"
    return "Ukončené", "completed"


def extract_attachments(parsed: ParsedHtml) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    for link in parsed.links:
        path = urllib.parse.urlparse(link.url).path.lower()
        if not path.endswith(ATTACHMENT_EXTENSIONS):
            continue
        attachments.append({"title": link.text or Path(path).name, "url": link.url, "status": "Čeká"})
    return attachments


def item_from_detail(source: dict[str, Any], url: str, content: str, today: dt.date | None = None) -> dict[str, Any] | None:
    parsed = parse_html(content, url)
    title = normalize_spaces(parsed.title)
    if not title or len(title) < 4:
        return None
    text = parsed.text
    deadline = find_deadline(text)
    status, status_code = status_from_deadline(deadline, today=today)
    item_type, type_code = classify_type(title, url, text)
    summary = parsed.paragraphs[0] if parsed.paragraphs else ""
    return {
        "id": stable_item_id(source["id"], url),
        "title": title,
        "type": item_type,
        "type_code": type_code,
        "status": status,
        "status_code": status_code,
        "deadline": deadline,
        "source_url": canonical_url(url),
        "source_id": source["id"],
        "source_name": source["name"],
        "program": infer_program(source, title, text),
        "summary": clean_text(summary, max_chars=500),
        "text": text,
        "attachments": extract_attachments(parsed),
    }


def stable_item_id(source_id: str, url: str) -> int:
    digest = hashlib.sha1(f"{source_id}:{canonical_url(url)}".encode("utf-8")).hexdigest()
    return int(digest[:10], 16)


def infer_program(source: dict[str, Any], title: str, text: str) -> str:
    haystack = f"{source.get('topics', '')} {title} {text[:300]}".lower()
    for program in ("OP TAK", "OPŽP", "OPZ+", "IROP", "OP JAK", "NPO", "Modernizační fond", "Horizon Europe"):
        if program.lower() in haystack:
            return program
    return ""


def import_source(source: dict[str, Any], limit: int, timeout: int, today: dt.date | None = None) -> SourceResult:
    adapter = source.get("adapter", "html_catalog")
    if adapter == "manual":
        return SourceResult(source_id=source["id"])
    if adapter == "sitemap":
        return import_sitemap(source, limit=limit, timeout=timeout, today=today)
    if adapter in {"html_catalog", "reference_page"}:
        return import_html_catalog(source, limit=limit, timeout=timeout, today=today, reference_only=adapter == "reference_page")
    return SourceResult(source_id=source["id"], warnings=[f"Unknown adapter {adapter!r}"])


def import_sitemap(source: dict[str, Any], limit: int, timeout: int, today: dt.date | None = None) -> SourceResult:
    result = SourceResult(source_id=source["id"])
    urls, warnings = collect_sitemap_urls(source["url"], timeout=timeout)
    result.warnings.extend(f"{source['name']}: {warning}" for warning in warnings)
    if not urls:
        result.warnings.append(f"{source['name']}: sitemap returned no URLs")
        return result
    candidates = [url for url in urls if url_matches_source(url, source)]
    for url in candidates[:limit]:
        try:
            detail = fetch_text(url, timeout=timeout)
            item = item_from_detail(source, url, detail, today=today)
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
            result.warnings.append(f"{source['name']}: detail fetch failed for {url}: {exc}")
            continue
        if item:
            result.items.append(item)
    return result


def collect_sitemap_urls(url: str, timeout: int, depth: int = 0, max_depth: int = 2) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    try:
        xml_text = fetch_text(url, timeout=timeout)
        root = ET.fromstring(xml_text)
    except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
        return [], [f"sitemap fetch failed for {url}: {exc}"]
    locs = [node.text or "" for node in root.iter() if node.tag.endswith("loc")]
    if root.tag.endswith("sitemapindex") and depth < max_depth:
        urls: list[str] = []
        for child_url in locs:
            child_urls, child_warnings = collect_sitemap_urls(child_url, timeout=timeout, depth=depth + 1, max_depth=max_depth)
            urls.extend(child_urls)
            warnings.extend(child_warnings)
        return urls, warnings
    return locs, warnings


def import_html_catalog(
    source: dict[str, Any],
    limit: int,
    timeout: int,
    today: dt.date | None = None,
    reference_only: bool = False,
) -> SourceResult:
    result = SourceResult(source_id=source["id"])
    try:
        catalog = fetch_text(source["url"], timeout=timeout)
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        result.warnings.append(f"{source['name']}: catalog fetch failed: {exc}")
        return result
    parsed = parse_html(catalog, source["url"])
    if reference_only:
        item = item_from_detail(source, source["url"], catalog, today=today)
        if item:
            item["type"] = "Stránka"
            item["type_code"] = "page"
            result.items.append(item)
        return result
    links = [link.url for link in parsed.links if link_matches_source(link, source)]
    if source["url"] not in links:
        links.insert(0, source["url"])
    for url in links[:limit]:
        try:
            detail = fetch_text(url, timeout=timeout)
            item = item_from_detail(source, url, detail, today=today)
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
            result.warnings.append(f"{source['name']}: detail fetch failed for {url}: {exc}")
            continue
        if item and is_relevant_item(item, source):
            result.items.append(item)
    return result


def url_matches_source(url: str, source: dict[str, Any]) -> bool:
    url_lower = url.lower()
    include = source.get("include_url_patterns") or []
    exclude = source.get("exclude_url_patterns") or []
    if include and not any(pattern.lower() in url_lower for pattern in include):
        return False
    if exclude and any(pattern.lower() in url_lower for pattern in exclude):
        return False
    return True


def link_matches_source(link: Link, source: dict[str, Any]) -> bool:
    url = canonical_url(link.url)
    if urllib.parse.urlparse(url).path.lower().endswith(ATTACHMENT_EXTENSIONS):
        return False
    parsed_source = urllib.parse.urlparse(source["url"])
    parsed_link = urllib.parse.urlparse(url)
    if not source.get("allow_external") and parsed_source.netloc and parsed_link.netloc != parsed_source.netloc:
        return False
    if not url_matches_source(url, source):
        return False
    haystack = f"{url} {link.text}".lower()
    keywords = source.get("candidate_keywords") or ["výzva", "vyzva", "dotace", "grant", "soutěž", "soutez"]
    return any(keyword.lower() in haystack for keyword in keywords)


def is_relevant_item(item: dict[str, Any], source: dict[str, Any]) -> bool:
    if source.get("adapter") == "reference_page":
        return True
    haystack = f"{item.get('title', '')} {item.get('text', '')[:500]} {item.get('source_url', '')}".lower()
    keywords = source.get("candidate_keywords") or ["výzva", "vyzva", "dotace", "grant", "soutěž", "soutez"]
    return any(keyword.lower() in haystack for keyword in keywords)


def source_export(source: dict[str, Any]) -> dict[str, str]:
    keys = ["name", "category", "type", "url", "target_groups", "topics", "notes", "integration"]
    return {key: str(source.get(key, "")) for key in keys}


def load_sources(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        sources = json.load(handle)
    if not isinstance(sources, list):
        raise ValueError("sources.json must contain a list")
    for source in sources:
        for key in ("id", "name", "url", "integration", "adapter"):
            if not source.get(key):
                raise ValueError(f"Source missing {key}: {source!r}")
    return sources


def load_current_export(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith(EXPORT_PREFIX):
        raise ValueError(f"{path} does not start with {EXPORT_PREFIX!r}")
    payload = text[len(EXPORT_PREFIX) :]
    if payload.endswith(EXPORT_SUFFIX):
        payload = payload[: -len(EXPORT_SUFFIX)]
    elif payload.endswith(";"):
        payload = payload[:-1]
    return json.loads(payload)


def normalize_existing_item(item: dict[str, Any], sources_by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = dict(item)
    source_url = str(item.get("source_url", ""))
    source = guess_source_for_url(source_url, sources_by_name)
    if source:
        item.setdefault("source_id", source["id"])
        item.setdefault("source_name", source["name"])
        item.setdefault("program", infer_program(source, item.get("title", ""), item.get("text", "")))
    item["attachments"] = list(item.get("attachments") or [])
    return item


def guess_source_for_url(url: str, sources_by_name: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    netloc = urllib.parse.urlparse(url).netloc.lower()
    if "apiagentura.gov.cz" in netloc:
        return sources_by_name.get("API Agentura - OP TAK")
    for source in sources_by_name.values():
        source_netloc = urllib.parse.urlparse(source.get("url", "")).netloc.lower()
        if source_netloc and source_netloc == netloc:
            return source
    return None


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = canonical_url(str(item.get("source_url", ""))) or f"{item.get('source_id')}:{item.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
        deadline = item.get("deadline") or "9999-99-99"
        status = item.get("status_code") or "unknown"
        title = item.get("title") or ""
        return (deadline, status, title)

    return sorted(items, key=sort_key)


def build_stats(items: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "active": sum(1 for item in items if item.get("status_code") == "active"),
        "completed": sum(1 for item in items if item.get("status_code") == "completed"),
        "unknown": sum(1 for item in items if item.get("status_code") == "unknown"),
        "sources": len(sources),
    }


def validate_export(export: dict[str, Any], strict_noise: bool = False) -> list[str]:
    errors: list[str] = []
    for index, item in enumerate(export.get("items", []), start=1):
        prefix = f"items[{index}]"
        if not item.get("title"):
            errors.append(f"{prefix}: missing title")
        if not item.get("source_url"):
            errors.append(f"{prefix}: missing source_url")
        if item.get("status_code") not in {"active", "completed", "unknown"}:
            errors.append(f"{prefix}: invalid status_code {item.get('status_code')!r}")
        deadline = item.get("deadline") or ""
        if deadline and not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", deadline):
            errors.append(f"{prefix}: invalid deadline {deadline!r}")
        if not isinstance(item.get("attachments", []), list):
            errors.append(f"{prefix}: attachments must be a list")
        if strict_noise and str(item.get("text", "")).startswith(NAVIGATION_PREFIXES):
            errors.append(f"{prefix}: text starts with repeated navigation")
    return errors


def write_export(path: Path, export: dict[str, Any]) -> None:
    path.write_text(
        EXPORT_PREFIX + json.dumps(export, ensure_ascii=False, indent=2) + EXPORT_SUFFIX,
        encoding="utf-8",
    )


def build_export(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    sources = load_sources(args.sources)
    sources_by_name = {source["name"]: source for source in sources}
    warnings: list[str] = []
    items: list[dict[str, Any]] = []
    if args.preserve_current and args.current_data.exists():
        current = load_current_export(args.current_data)
        items.extend(normalize_existing_item(item, sources_by_name) for item in current.get("items", []))
    if not args.offline_current:
        for source in sources:
            result = import_source(source, limit=args.limit_per_source, timeout=args.timeout)
            warnings.extend(result.warnings)
            items.extend(result.items)
            if args.verbose:
                print(f"{source['name']}: {len(result.items)} items", file=sys.stderr)
    items = sort_items(dedupe_items(items))
    export = {
        "generated_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "stats": build_stats(items, sources),
        "items": items,
        "sources": [source_export(source) for source in sources],
    }
    errors = validate_export(export, strict_noise=args.strict_noise)
    if errors:
        raise ValueError("Export validation failed:\n" + "\n".join(errors[:20]))
    return export, warnings


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build data.js from official grant sources.")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--current-data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--limit-per-source", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--offline-current", action="store_true", help="Skip network and only rewrite existing items plus source catalog.")
    parser.add_argument("--fresh", action="store_true", help="Do not preserve existing data.js items before importing.")
    parser.add_argument("--strict-noise", action="store_true", help="Fail if imported text starts with known navigation boilerplate.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    args.preserve_current = not args.fresh
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        export, warnings = build_export(args)
    except Exception as exc:
        print(f"build_data.py: {exc}", file=sys.stderr)
        return 1
    write_export(args.output, export)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"Wrote {args.output} with {export['stats']['total']} items from {export['stats']['sources']} sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
