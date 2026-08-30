"""Extrakce strukturovaných parametrů výzvy z textu stránky.

Cílem je odpovědět na otázku „může si o to moje firma požádat a do kdy",
takže vedle termínů hledáme i alokaci, míru podpory, okruh žadatelů a region.
Vše je best-effort: raději prázdné pole než vymyšlená hodnota.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

MONTHS = {
    "ledna": 1, "leden": 1, "února": 2, "unora": 2, "únor": 2, "unor": 2,
    "března": 3, "brezna": 3, "březen": 3, "brezen": 3, "dubna": 4, "duben": 4,
    "května": 5, "kvetna": 5, "květen": 5, "kveten": 5, "června": 6, "cervna": 6,
    "červen": 6, "cerven": 6, "července": 7, "cervence": 7, "červenec": 7,
    "cervenec": 7, "srpna": 8, "srpen": 8, "září": 9, "zari": 9, "října": 10,
    "rijna": 10, "říjen": 10, "rijen": 10, "listopadu": 11, "listopad": 11,
    "prosince": 12, "prosinec": 12,
}
_MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\b")
_WORD_DATE = re.compile(rf"\b(\d{{1,2}})\.\s*({_MONTH_NAMES})\s+(20\d{{2}})\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")

# Štítky termínů ve vrstvách podle spolehlivosti — slabší se použije, jen když
# silnější na stránce není. „Platnost od/do" je u některých portálů skutečné
# okno příjmu žádostí, jinde jen platnost dokumentu, proto až druhá vrstva.
OPENING_LABELS: tuple[tuple[str, ...], ...] = (
    (
        "zahájení příjmu žádostí", "zahajeni prijmu zadosti", "zahájení příjmu",
        "příjem žádostí od", "prijem zadosti od", "žádosti lze podávat od",
        "datum zahájení", "datum zahajeni", "zpřístupnění žádosti",
        "otevření výzvy", "příjem žádostí bude zahájen", "opening date",
    ),
    ("platnost od", "termín pro podání žádosti:"),
)
CLOSING_LABELS: tuple[tuple[str, ...], ...] = (
    (
        "ukončení příjmu žádostí", "ukonceni prijmu zadosti", "ukončení příjmu",
        "příjem žádostí do", "prijem zadosti do", "termín pro podání",
        "termin pro podani", "datum ukončení", "datum ukonceni", "uzávěrka",
        "uzaverka", "nejpozději do", "nejpozdeji do", "podání žádostí do",
        "žádosti lze podávat do", "lhůta pro podání", "deadline", "closing date",
        "konec příjmu",
    ),
    ("platnost do", "do kdy"),
)
# Data u těchto formulací nejsou termínem příjmu žádostí.
NEGATIVE_LABELS = (
    "vyhlášení výzvy", "vyhlaseni vyzvy", "zveřejněn", "zverejnen", "aktualizován",
    "aktualizovan", "účinnost", "ucinnost", "ukončení realizace",
    "datum vydání", "verze", "poslední aktualizace",
)

_RANGE = re.compile(
    r"(?:od\s+)?(\d{1,2}\.\s*\d{1,2}\.\s*20\d{2})\s*(?:-|–|—|až|do)\s*(\d{1,2}\.\s*\d{1,2}\.\s*20\d{2})",
    re.IGNORECASE,
)

_SCALE = {"tis": 1_000, "tisíc": 1_000, "tisic": 1_000, "mil": 1_000_000, "milion": 1_000_000,
          "milionů": 1_000_000, "mld": 1_000_000_000, "miliard": 1_000_000_000, "miliardy": 1_000_000_000}
_AMOUNT = re.compile(
    r"(\d[\d\s .,]{2,20}?)\s*(tis\.?|tisíc|mil\.?|milion[ůy]?|mld\.?|miliard[y]?)?\s*(Kč|CZK|EUR|€)",
    re.IGNORECASE,
)
_ALLOCATION_LABELS = ("alokace", "celková alokace", "plánovaná alokace", "objem prostředků",
                      "finanční alokace", "rozpočet výzvy")
_RATE = re.compile(r"(\d{1,3}(?:[.,]\d)?)\s*%", re.IGNORECASE)
_RATE_LABELS = ("míra podpory", "mira podpory", "výše podpory", "vyse podpory",
                "míra dotace", "podpora až", "dotace až", "způsobilých výdajů", "zpusobilych vydaju")

APPLICANT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("msp", ("malé a střední podnik", "male a stredni podnik", "malý a střední podnik",
             "msp", "sme", "drobn", "mikropodnik", "střední podnik", "stredni podnik")),
    ("velky_podnik", ("velké podnik", "velke podnik", "velký podnik", "velky podnik", "large enterprise")),
    ("osvc", ("osvč", "osvc", "podnikající fyzick", "podnikajici fyzick", "fyzická osoba podnikající")),
    ("vyzkumna_organizace", ("výzkumn", "vyzkumn", "vysoká škola", "vysoke skoly", "univerzit", "vědeck")),
    ("obec_kraj", ("obce", "obcí", "kraje", "krajů", "města", "mest ", "svazky obcí", "dobrovolné svazky")),
    ("nezisk", ("nestátní neziskov", "nezisková organizace", "neziskové organizace", "spolky",
                "obecně prospěšn", "nadace", "církevní")),
    ("skola", ("mateřsk", "základní škol", "střední škol", "školská zařízení", "školy a školská")),
)
BUSINESS_APPLICANTS = {"msp", "velky_podnik", "osvc"}

REGIONS = (
    "Hlavní město Praha", "Středočeský kraj", "Jihočeský kraj", "Plzeňský kraj",
    "Karlovarský kraj", "Ústecký kraj", "Liberecký kraj", "Královéhradecký kraj",
    "Pardubický kraj", "Kraj Vysočina", "Jihomoravský kraj", "Olomoucký kraj",
    "Zlínský kraj", "Moravskoslezský kraj",
)

# Kanonický název programu a formulace, pod kterými se na webech objevuje.
PROGRAMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("OP TAK", ("op tak", "technologie a aplikace pro konkurenceschopnost")),
    ("OP ST", ("op st", "spravedlivá transformace", "spravedliva transformace")),
    ("OPŽP", ("opžp", "opzp", "operační program životní prostředí")),
    ("OPZ+", ("opz+", "opz plus", "zaměstnanost plus")),
    ("IROP", ("irop", "integrovaný regionální operační program")),
    ("OP JAK", ("op jak", "jan amos komenský")),
    ("OP Doprava", ("op doprava", "operační program doprava")),
    ("OP Rybářství", ("op rybářství", "operační program rybářství")),
    ("NPO", ("národní plán obnovy", "narodni plan obnovy", "npo")),
    ("Modernizační fond", ("modernizační fond", "modernizacni fond")),
    ("Horizon Europe", ("horizon europe", "horizont evropa")),
    ("EIC", ("eic accelerator", "european innovation council")),
    ("Digital Europe", ("digital europe", "digitální evropa")),
    ("LIFE", ("programu life", "program life")),
    ("Interreg", ("interreg",)),
    ("TREND", ("program trend",)),
    ("SIGMA", ("program sigma",)),
    ("THÉTA", ("program théta", "program theta")),
    ("DOPRAVA 2030", ("doprava 2030",)),
    ("Nová zelená úsporám", ("nová zelená úsporám", "nova zelena usporam")),
    ("Expanze", ("expanze",)),
    ("Záruka", ("záruka transformace", "portfoliová záruka")),
    ("Fenix", ("program fenix", "strategický program fenix")),
    ("Erasmus+", ("erasmus+", "erasmus plus")),
    ("Creative Europe", ("creative europe", "kreativní evropa")),
)

# Titulky, které nikdy nejsou názvem výzvy — jde o navigaci nebo sekci stránky.
TITLE_BLACKLIST = {
    "dokumenty", "dokumenty k výzvě", "dokumenty opz+", "základní dokumenty",
    "nastavení cookies", "cookies", "harmonogram výzev", "programy a výzvy",
    "výzvy", "vyzvy", "aktuality", "novinky", "kontakty", "kontakt", "úvod",
    "domů", "ais mpo - portál", "szif poskytuje", "na co můžete získat dotaci",
    "na co lze získat dotaci", "na co lze půjčku čerpat", "vybrat dotace dle",
    "přehled výzev", "seznam výzev", "agregátor obsahu", "vyhledávání",
    "mapa stránek", "prohlášení o přístupnosti", "časté dotazy", "faq",
    "jak získat dotaci", "jak na dotaci", "o programu", "ke stažení",
    "detail výzvy", "detail", "přihlášení", "registrace", "vyhledávání",
    "aktuální výzvy", "uzavřené výzvy", "archiv výzev", "dokumenty ke stažení",
}
# Titulky, které jsou jen názvem instituce nebo rozcestníkem.
TITLE_BLACKLIST_SUFFIXES = (" - úvod", " – úvod", " - domů", " – domů", " - homepage")
TITLE_BLACKLIST_PREFIXES = ("agregátor obsahu", "nastavení cookies", "stránka nenalezena", "chyba 404")


def _to_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[dt.date, int]]:
    """Všechna data v textu spolu s pozicí výskytu."""
    found: list[tuple[dt.date, int]] = []
    for match in _NUMERIC_DATE.finditer(text):
        day, month, year = (int(g) for g in match.groups())
        date = _to_date(year, month, day)
        if date:
            found.append((date, match.start()))
    for match in _WORD_DATE.finditer(text):
        date = _to_date(int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1)))
        if date:
            found.append((date, match.start()))
    for match in _ISO_DATE.finditer(text):
        date = _to_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if date:
            found.append((date, match.start()))
    return sorted(found, key=lambda pair: pair[1])


def _date_after_label(
    text: str, lower: str, tiers: tuple[tuple[str, ...], ...], window: int = 160
) -> dt.date | None:
    dates = find_dates(text)
    if not dates:
        return None
    for tier in tiers:
        found = _best_in_tier(lower, dates, tier, window)
        if found:
            return found
    return None


def _best_in_tier(
    lower: str, dates: list[tuple[dt.date, int]], labels: Iterable[str], window: int
) -> dt.date | None:
    best: tuple[int, dt.date] | None = None
    for label in labels:
        start = 0
        while True:
            index = lower.find(label, start)
            if index == -1:
                break
            start = index + 1
            label_end = index + len(label)
            context_start = max(0, index - 60)
            if any(neg in lower[context_start:label_end] for neg in NEGATIVE_LABELS):
                continue
            for date, position in dates:
                if label_end <= position <= label_end + window:
                    distance = position - label_end
                    if best is None or distance < best[0]:
                        best = (distance, date)
                    break
    return best[1] if best else None


def _range_after_label(text: str, lower: str, window: int = 200) -> tuple[dt.date, dt.date] | None:
    """Rozsah zapsaný přímo za štítkem, např. `Termín pro podání: 28. 8. - 16. 10. 2026`."""
    for tier in (*CLOSING_LABELS, *OPENING_LABELS):
        for label in tier:
            index = lower.find(label)
            if index == -1:
                continue
            segment = text[index + len(label) : index + len(label) + window]
            match = _RANGE.search(segment)
            if not match:
                continue
            starts = find_dates(match.group(1))
            ends = find_dates(match.group(2))
            if starts and ends and starts[0][0] <= ends[0][0]:
                return starts[0][0], ends[0][0]
    return None


def extract_period(text: str) -> tuple[str, str]:
    """Vrátí (zahájení, ukončení) příjmu žádostí jako ISO řetězce."""
    if not text:
        return "", ""
    lower = text.lower()
    labelled_range = _range_after_label(text, lower)
    if labelled_range:
        return labelled_range[0].isoformat(), labelled_range[1].isoformat()

    opening = _date_after_label(text, lower, OPENING_LABELS)
    closing = _date_after_label(text, lower, CLOSING_LABELS)

    if not (opening and closing):
        match = _RANGE.search(text)
        if match:
            starts = find_dates(match.group(1))
            ends = find_dates(match.group(2))
            if starts and ends and starts[0][0] <= ends[0][0]:
                opening = opening or starts[0][0]
                closing = closing or ends[0][0]

    if closing and opening and closing < opening:
        opening, closing = closing, opening
    return (opening.isoformat() if opening else "", closing.isoformat() if closing else "")


def status_for(opening: str, closing: str, today: dt.date | None = None) -> tuple[str, str]:
    today = today or dt.date.today()
    open_date = _parse_iso(opening)
    close_date = _parse_iso(closing)
    if close_date and close_date < today:
        return "Ukončené", "completed"
    if open_date and open_date > today:
        return "Připravované", "upcoming"
    if close_date:
        return "Probíhající", "active"
    if open_date:
        return "Probíhající", "active"
    return "Neznámé", "unknown"


def _parse_iso(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


_ALLOCATION_PREFIXED = re.compile(
    r"alokace[^:\n]{0,30}:\s*([\d][\d\s\u00a0.,]{4,20})", re.IGNORECASE
)


def extract_allocation(text: str) -> int:
    """Alokace výzvy v Kč. Částky v eurech se nepřepočítávají — vrací 0."""
    if not text:
        return 0
    prefixed = _ALLOCATION_PREFIXED.search(text)
    if prefixed:
        digits = re.sub(r"[^\d]", "", prefixed.group(1))
        if digits and int(digits) >= 10_000:
            return int(digits)
    lower = text.lower()
    candidates: list[tuple[int, int]] = []
    for match in _AMOUNT.finditer(text):
        currency = match.group(3).lower()
        if currency in {"eur", "€"}:
            continue
        raw = match.group(1).replace(" ", " ").replace(" ", "")
        raw = raw.rstrip(".,")
        if raw.count(",") == 1 and len(raw.split(",")[-1]) <= 2:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(".", "").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        scale_token = (match.group(2) or "").rstrip(".").lower()
        value *= _SCALE.get(scale_token, 1)
        amount = int(value)
        if amount < 10_000:
            continue
        context = lower[max(0, match.start() - 120) : match.start()]
        score = 1 if any(label in context for label in _ALLOCATION_LABELS) else 0
        candidates.append((score, amount))
    if not candidates:
        return 0
    labelled = [amount for score, amount in candidates if score]
    if labelled:
        return max(labelled)
    # Bez štítku „alokace" je částka jen odhad, proto bereme jen věrohodné řády.
    plausible = [amount for _, amount in candidates if amount >= 1_000_000]
    return max(plausible) if plausible else 0


def extract_support_rate(text: str) -> int:
    """Maximální míra podpory v procentech."""
    if not text:
        return 0
    lower = text.lower()
    best = 0
    for match in _RATE.finditer(text):
        try:
            value = int(round(float(match.group(1).replace(",", "."))))
        except ValueError:
            continue
        if not 1 <= value <= 100:
            continue
        context = lower[max(0, match.start() - 90) : match.start() + 40]
        if any(label in context for label in _RATE_LABELS):
            best = max(best, value)
    return best


def extract_applicants(text: str) -> list[str]:
    lower = (text or "").lower()
    found = [key for key, needles in APPLICANT_PATTERNS if any(n in lower for n in needles)]
    return found


def extract_regions(text: str) -> list[str]:
    return [region for region in REGIONS if region.lower() in (text or "").lower()]


def infer_program(text: str, hints: str = "") -> str:
    haystack = f"{hints} {text}".lower()
    for program, aliases in PROGRAMS:
        if any(alias in haystack for alias in aliases):
            return program
    return ""


def classify(title: str, url: str, text: str) -> tuple[str, str]:
    haystack = f"{title} {url} {text[:600]}".lower()
    if any(word in haystack for word in ("webinář", "webinar", "seminář", "seminar", "konference", "/udalosti/", "/akce/")):
        return "Akce", "event"
    if any(word in haystack for word in ("často kladené", "faq", "časté dotazy")):
        return "FAQ", "faq"
    if any(word in haystack for word in ("výzva", "vyzva", "dotace", "soutěž", "soutez", "grant", "podpora", "call for")):
        return "Dotace", "grant"
    if any(word in haystack for word in ("aktualita", "novinka", "tisková zpráva")):
        return "Článek", "article"
    return "Stránka", "page"


_CODE_IN_TEXT = re.compile(r"číslo(?:\s+výzvy)?\s*:\s*([\w./-]{1,20})", re.IGNORECASE)


def extract_code_from_text(text: str) -> str:
    match = _CODE_IN_TEXT.search(text or "")
    return match.group(1).strip(".") if match else ""


def is_blacklisted_title(title: str) -> bool:
    normalized = (title or "").strip().lower().rstrip(".:")
    if not normalized or len(normalized) < 5:
        return True
    if normalized in TITLE_BLACKLIST:
        return True
    if any(normalized.startswith(prefix) for prefix in TITLE_BLACKLIST_PREFIXES):
        return True
    return any(normalized.endswith(suffix) for suffix in TITLE_BLACKLIST_SUFFIXES)
