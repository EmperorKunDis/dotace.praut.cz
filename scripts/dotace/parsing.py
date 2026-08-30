"""Převod HTML na normalizovaný dokument.

Původní implementace stála na ručním `html.parser`, který se u jediného
nevyváženého `<svg>` trvale „oslepil" a zbytek stránky zahodil. Používáme
selectolax, který si s reálným HTML poradí a navíc umí CSS selektory, takže
lze cíleně sáhnout po hlavním obsahu místo celého dokumentu.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser, LexborNode

ATTACHMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rtf", ".odt", ".ods", ".csv",
)

# Bloky, které nikdy nenesou obsah výzvy.
BOILERPLATE_TAGS = (
    "script", "style", "noscript", "svg", "template", "iframe",
    "nav", "header", "footer", "aside", "button", "select", "input", "textarea", "dialog",
)
BOILERPLATE_SELECTORS = (
    "[role=navigation]", "[role=banner]", "[role=contentinfo]", "[aria-hidden=true]",
    ".cookies", "#cookies", "#cookies-modal", ".cookie-bar", ".cookieconsent",
    ".breadcrumb", ".breadcrumbs", ".menu", ".navigation", ".navbar", ".sidebar",
    ".skip-link", ".screen-reader-text", ".sr-only", ".social", ".share",
)

# Kandidáti na hlavní obsah, od nejužšího po nejširší.
MAIN_SELECTORS = (
    "main article", "article .entry-content", ".entry-content", ".post-content",
    "main", "[role=main]", "article", "#content", ".content-main", ".main-content",
    "#main", ".page-content", ".article-body",
)

TITLE_SEPARATORS = re.compile(r"\s+[–—|·»]\s+|\s+-\s+")


@dataclass(frozen=True)
class Link:
    url: str
    text: str


@dataclass
class Doc:
    url: str
    title: str = ""
    title_source: str = ""       # "h1" | "og" | "title" | "heading"
    doc_title: str = ""          # syrový obsah <title>
    headings: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    text: str = ""               # text hlavního obsahu
    full_text: str = ""          # text celé stránky bez boilerplate
    links: list[Link] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)


def normalize_spaces(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", value).strip()


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlparse((url or "").strip())
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


def _text_of(node: LexborNode | None) -> str:
    if node is None:
        return ""
    return normalize_spaces(node.text(separator=" "))


def _strip_boilerplate(tree: LexborHTMLParser) -> None:
    for selector in (*BOILERPLATE_TAGS, *BOILERPLATE_SELECTORS):
        for node in tree.css(selector):
            node.decompose()


def _meta_tags(tree: LexborHTMLParser) -> dict[str, str]:
    meta: dict[str, str] = {}
    for node in tree.css("meta"):
        key = node.attributes.get("property") or node.attributes.get("name")
        content = node.attributes.get("content")
        if key and content:
            meta[key.strip().lower()] = normalize_spaces(content)
    return meta


def _pick_main(tree: LexborHTMLParser) -> LexborNode | None:
    best: LexborNode | None = None
    best_len = 0
    for selector in MAIN_SELECTORS:
        for node in tree.css(selector):
            length = len(node.text(separator=" "))
            if length > best_len:
                best, best_len = node, length
        if best_len > 400:
            return best
    return best


def _collect_links(tree: LexborHTMLParser, base_url: str) -> list[Link]:
    seen: set[str] = set()
    links: list[Link] = []
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = canonical_url(urllib.parse.urljoin(base_url, href))
        if not url or url in seen:
            continue
        seen.add(url)
        label = _text_of(node) or (node.attributes.get("title") or "")
        if not label:
            label = Path(urllib.parse.urlparse(url).path).name or url
        links.append(Link(url, normalize_spaces(label)))
    return links


def strip_title_suffix(title: str, suffix: str) -> str:
    """Odstraní z titulku koncovku s názvem webu (typicky z WordPressu)."""
    if not suffix:
        return title
    suffix = suffix.strip()
    if not suffix or len(title) <= len(suffix):
        return title
    trimmed = title[: -len(suffix)].strip()
    trimmed = re.sub(r"[\s–—|·»,-]+$", "", trimmed).strip()
    return trimmed or title


def common_title_suffix(titles: list[str], min_samples: int = 3) -> str:
    """Najde společnou koncovku titulků jednoho zdroje.

    WordPress i další CMS lepí za název stránky jméno webu (`… – Operační
    program Životní prostředí`). Napevno vypsat všechny varianty nejde, ale ze
    vzorku stránek stejného zdroje se společná koncovka spolehlivě odvodí.
    """
    usable = [t for t in {t for t in titles if t} if len(t) > 10]
    if len(usable) < min_samples:
        return ""
    threshold = max(min_samples, int(0.7 * len(usable)))
    reference = max(usable, key=lambda t: sum(1 for o in usable if o.endswith(t[-12:])))
    suffix = ""
    for size in range(len(reference), 5, -1):
        candidate = reference[-size:]
        if not TITLE_SEPARATORS.search(candidate):
            continue
        if sum(1 for t in usable if t.endswith(candidate)) >= threshold:
            suffix = candidate
            break
    if not suffix:
        return ""
    # Ponecháme jen část za posledním oddělovačem, aby se neuřízl obsah.
    match = TITLE_SEPARATORS.search(suffix)
    return suffix[match.start():] if match else suffix


def parse(content: str, base_url: str) -> Doc:
    tree = LexborHTMLParser(content)
    meta = _meta_tags(tree)
    doc_title = _text_of(tree.css_first("title"))
    links = _collect_links(tree, base_url)

    _strip_boilerplate(tree)

    h1 = _text_of(tree.css_first("h1"))
    headings = [t for t in (_text_of(n) for n in tree.css("h1, h2, h3")) if t]
    main = _pick_main(tree)
    body = main if main is not None else tree.body
    paragraphs = _readable_paragraphs(body)

    title, title_source = _resolve_title(h1, meta, doc_title, headings)
    return Doc(
        url=canonical_url(base_url),
        title=title,
        title_source=title_source,
        doc_title=doc_title,
        headings=headings,
        paragraphs=paragraphs,
        text=_text_of(body),
        full_text=_text_of(tree.body),
        links=links,
        meta=meta,
    )


def _readable_paragraphs(body: LexborNode | None) -> list[str]:
    """Odstavce vhodné jako shrnutí — vynechá seznamy odkazů a útržky menu."""
    if body is None:
        return []
    paragraphs: list[str] = []
    for node in body.css("p"):
        text = _text_of(node)
        if len(text) < 40:
            continue
        link_chars = sum(len(_text_of(a)) for a in node.css("a"))
        if link_chars > 0.6 * len(text):
            continue
        paragraphs.append(text)
    return paragraphs


def _resolve_title(h1: str, meta: dict[str, str], doc_title: str, headings: list[str]) -> tuple[str, str]:
    if len(h1) >= 5:
        return h1, "h1"
    og_title = meta.get("og:title", "")
    if len(og_title) >= 5:
        return og_title, "og"
    if len(doc_title) >= 5:
        return doc_title, "title"
    for heading in headings:
        if len(heading) >= 5:
            return heading, "heading"
    return "", ""


def attachments_from(links: list[Link]) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in links:
        path = urllib.parse.urlparse(link.url).path.lower()
        if not path.endswith(ATTACHMENT_EXTENSIONS) or link.url in seen:
            continue
        seen.add(link.url)
        attachments.append(
            {
                "title": link.text or Path(path).name,
                "url": link.url,
                "format": Path(path).suffix.lstrip(".").upper(),
            }
        )
    return attachments
