"""
pdf_odds_importer.py

Atlas Betting AI
Importatore quote dal PDF Oddsprinter compatto (14 mercati).

Obiettivi:
- leggere PDF testuali con palinsesto e quote;
- ignorare il codice evento del bookmaker sorgente;
- estrarre data, ora, competizione, squadre e mercati;
- abbinare gli eventi PDF agli eventi Atlas tramite nomi, data, ora e competizione;
- compilare esclusivamente le quote mancanti;
- non sovrascrivere mai le quote già presenti in Atlas.

Compatibilità:
- Python 3.13
- pdfplumber
- rapidfuzz

Installazione dipendenze:
    python -m pip install pdfplumber rapidfuzz
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pdfplumber
from rapidfuzz import fuzz


# ---------------------------------------------------------------------------
# Costanti e mapping mercati
# ---------------------------------------------------------------------------

# Ordine colonne Oddsprinter (header PDF): 1X2 → DC → U/O1.5 → U/O2.5 → U/O3.5 → G/NG.
# Attenzione: U/O 3.5 viene PRIMA di Goal/No Goal (non dopo).
MARKET_COLUMNS: tuple[str, ...] = (
    "1",
    "X",
    "2",
    "1X",
    "12",
    "X2",
    "Under 1.5",
    "Over 1.5",
    "Under 2.5",
    "Over 2.5",
    "Under 3.5",
    "Over 3.5",
    "Goal",
    "No Goal",
)

# Nomi usati attualmente in Atlas.
PDF_TO_ATLAS_MARKET: dict[str, str] = {
    "1": "1",
    "X": "X",
    "2": "2",
    "1X": "1X",
    "12": "12",
    "X2": "X2",
    "Under 1.5": "Under 1.5",
    "Over 1.5": "Over 1.5",
    "Under 2.5": "Under 2.5",
    "Over 2.5": "Over 2.5",
    "Goal": "Goal",
    "No Goal": "No Goal",
    "Under 3.5": "Under 3.5",
    "Over 3.5": "Over 3.5",
}

# Centri orizzontali osservati nel layout Oddsprinter in formato landscape.
# Il parser scala automaticamente le coordinate in base alla larghezza pagina.
BASE_PAGE_WIDTH = 842.0
BASE_COLUMN_CENTERS: tuple[float, ...] = (
    204.8,
    252.6,
    300.4,
    348.2,
    396.0,
    443.8,
    491.6,
    539.4,
    587.2,
    635.0,
    682.8,
    730.6,
    778.4,
    826.2,
)

DATE_PATTERN = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}$")
ODDS_PATTERN = re.compile(r"^\d+(?:[.,]\d+)?$")
EVENT_SEPARATOR_PATTERN = re.compile(r"\s+-\s+")
MULTISPACE_PATTERN = re.compile(r"\s+")

GENERIC_TEAM_TOKENS = {
    "fc",
    "afc",
    "ac",
    "ca",
    "cf",
    "sc",
    "sk",
    "fk",
    "if",
    "bk",
    "club",
    "calcio",
    "football",
    "futbol",
    "futebol",
    "team",
    "women",
    "woman",
    "femminile",
    "fem",
    "u19",
    "u20",
    "u21",
    "u23",
    "res",
    "reserve",
    "reserves",
    "ii",
    "b",
}

COMPETITION_NOISE_TOKENS = {
    "internazionali",
    "international",
    "club",
    "clubs",
    "calcio",
    "football",
    "soccer",
    "maschile",
    "femminile",
}


# ---------------------------------------------------------------------------
# Modelli dati
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PdfOddsEvent:
    page: int
    source_code: str
    date: str
    time: str
    competition: str
    home: str
    away: str
    odds: dict[str, float] = field(default_factory=dict)
    raw_event: str = ""
    row_top: float = 0.0

    @property
    def match_name(self) -> str:
        return f"{self.home} - {self.away}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MatchScore:
    total: float
    home_score: float
    away_score: float
    date_score: float
    time_score: float
    competition_score: float
    reversed_teams: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EventMatch:
    pdf_event: PdfOddsEvent
    atlas_index: int | None
    atlas_event: dict[str, Any] | None
    score: MatchScore
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_event": self.pdf_event.to_dict(),
            "atlas_index": self.atlas_index,
            "atlas_event": self.atlas_event,
            "score": self.score.to_dict(),
            "status": self.status,
        }


@dataclass(slots=True)
class ImportReport:
    atlas_rows: list[dict[str, Any]]
    sources: dict[str, dict[str, Any]]
    matches: list[EventMatch]
    imported_quotes: int
    skipped_existing_quotes: int
    unmatched_events: int
    ambiguous_events: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "atlas_rows": self.atlas_rows,
            "sources": self.sources,
            "matches": [item.to_dict() for item in self.matches],
            "imported_quotes": self.imported_quotes,
            "skipped_existing_quotes": self.skipped_existing_quotes,
            "unmatched_events": self.unmatched_events,
            "ambiguous_events": self.ambiguous_events,
        }


# ---------------------------------------------------------------------------
# Utility generali
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> float | None:
    if value is None:
        return None

    text = str(value).replace(",", ".").strip()

    if not text or not ODDS_PATTERN.match(text):
        return None

    try:
        number = float(text)
    except (TypeError, ValueError):
        return None

    if number <= 1.0:
        return None

    return number


def _clean_text(value: Any) -> str:
    return MULTISPACE_PATTERN.sub(" ", str(value or "").strip())


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(
        char
        for char in normalized
        if not unicodedata.combining(char)
    )


def normalize_name(value: str, remove_generic_tokens: bool = True) -> str:
    text = _strip_accents(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[\(\)\[\]\{\}/\\|:;,.']", " ", text)
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\b(?:women|woman|femminile)\b", " fem ", text)
    text = re.sub(r"\b(?:reserves?|reserve team)\b", " res ", text)
    text = re.sub(r"\bu\s*[- ]?\s*(\d{2})\b", r"u\1", text)
    text = MULTISPACE_PATTERN.sub(" ", text).strip()

    tokens = text.split()

    if remove_generic_tokens:
        filtered = [
            token
            for token in tokens
            if token not in GENERIC_TEAM_TOKENS
        ]

        if filtered:
            tokens = filtered

    return " ".join(tokens)


def normalize_competition(value: str) -> str:
    text = normalize_name(value, remove_generic_tokens=False)
    tokens = [
        token
        for token in text.split()
        if token not in COMPETITION_NOISE_TOKENS
    ]
    return " ".join(tokens)


def split_match_name(value: str) -> tuple[str, str]:
    text = _clean_text(value).strip(" -")

    parts = EVENT_SEPARATOR_PATTERN.split(text, maxsplit=1)

    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()

    # Fallback per testi senza spazi regolari intorno al trattino.
    parts = re.split(r"\s*-\s*", text, maxsplit=1)

    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()

    return text, ""


def parse_atlas_datetime(row: Mapping[str, Any]) -> tuple[str, str]:
    date_value = _clean_text(
        row.get("Data")
        or row.get("date")
        or row.get("data")
    )
    time_value = _clean_text(
        row.get("Ora")
        or row.get("time")
        or row.get("ora")
    )

    if "T" in date_value:
        try:
            parsed = datetime.fromisoformat(
                date_value.replace("Z", "+00:00")
            )
            date_value = parsed.strftime("%d/%m/%Y")
            if not time_value:
                time_value = parsed.strftime("%H:%M")
        except ValueError:
            pass

    return date_value, time_value


def minutes_from_time(value: str) -> int | None:
    text = _clean_text(value)

    if not TIME_PATTERN.match(text):
        return None

    hour, minute = text.split(":", maxsplit=1)

    try:
        return int(hour) * 60 + int(minute)
    except ValueError:
        return None


def circular_minute_difference(first: str, second: str) -> int | None:
    first_minutes = minutes_from_time(first)
    second_minutes = minutes_from_time(second)

    if first_minutes is None or second_minutes is None:
        return None

    difference = abs(first_minutes - second_minutes)
    return min(difference, 1440 - difference)


def atlas_team_names(row: Mapping[str, Any]) -> tuple[str, str]:
    home = _clean_text(
        row.get("home")
        or row.get("Home")
        or row.get("Casa")
        or row.get("Squadra casa")
    )
    away = _clean_text(
        row.get("away")
        or row.get("Away")
        or row.get("Ospite")
        or row.get("Squadra ospite")
    )

    if home and away:
        return home, away

    match_name = _clean_text(
        row.get("Partita")
        or row.get("match")
        or row.get("evento")
    )

    return split_match_name(match_name)


# ---------------------------------------------------------------------------
# Parser PDF Oddsprinter
# ---------------------------------------------------------------------------

class OddsPrinterPdfParser:
    """Parser basato sulle coordinate del PDF testuale Oddsprinter."""

    def __init__(
        self,
        event_x_min: float = 45.0,
        event_x_max: float = 168.0,
        odds_x_min: float = 168.0,
        header_bottom: float = 75.0,
        row_tolerance: float = 1.8,
        column_tolerance: float = 10.5,
    ) -> None:
        self.event_x_min = event_x_min
        self.event_x_max = event_x_max
        self.odds_x_min = odds_x_min
        self.header_bottom = header_bottom
        self.row_tolerance = row_tolerance
        self.column_tolerance = column_tolerance

    def parse(self, pdf_path: str | Path) -> list[PdfOddsEvent]:
        path = Path(pdf_path)

        if not path.exists():
            raise FileNotFoundError(f"PDF non trovato: {path}")

        events: list[PdfOddsEvent] = []
        current_date = ""

        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_events, current_date = self._parse_page(
                    page=page,
                    page_number=page_number,
                    inherited_date=current_date,
                )
                events.extend(page_events)

        return events

    def _parse_page(
        self,
        page: Any,
        page_number: int,
        inherited_date: str,
    ) -> tuple[list[PdfOddsEvent], str]:
        words = page.extract_words(
            x_tolerance=2,
            y_tolerance=2,
            keep_blank_chars=False,
            use_text_flow=False,
        )

        if not words:
            return [], inherited_date

        scale = float(page.width) / BASE_PAGE_WIDTH
        event_x_min = self.event_x_min * scale
        event_x_max = self.event_x_max * scale
        odds_x_min = self.odds_x_min * scale
        header_bottom = self.header_bottom * scale
        column_centers = tuple(
            center * scale
            for center in BASE_COLUMN_CENTERS
        )
        column_tolerance = self.column_tolerance * scale
        column_layout = self._header_columns(words, header_bottom, scale)

        date_on_page = inherited_date

        for word in words:
            text = _clean_text(word.get("text"))
            if (
                word.get("top", 0.0) >= header_bottom
                and word.get("x0", 0.0) < 50 * scale
                and DATE_PATTERN.match(text)
            ):
                date_on_page = text
                break

        row_intervals = self._find_row_intervals(
            page=page,
            words=words,
            header_bottom=header_bottom,
            scale=scale,
        )

        events: list[PdfOddsEvent] = []

        for row_start, row_end, anchor in row_intervals:
            row_top = float(anchor["top"])
            row_words = [
                word
                for word in words
                if row_start + 0.2 * scale
                <= float(word.get("top", 0.0))
                < row_end - 0.2 * scale
            ]

            event = self._parse_row(
                row_words=row_words,
                anchor=anchor,
                page_number=page_number,
                date_value=date_on_page,
                row_top=row_top,
                event_x_min=event_x_min,
                event_x_max=event_x_max,
                odds_x_min=odds_x_min,
                column_centers=column_centers,
                column_tolerance=column_tolerance,
                scale=scale,
                column_layout=column_layout,
            )

            if event is not None:
                events.append(event)

        return events, date_on_page

    @staticmethod
    def _header_columns(words, header_bottom, scale):
        """Read cell boundaries from headers, preserving extra market positions."""
        header = [w for w in words if float(w.get('top', 0)) < header_bottom]
        labels = {'1', 'X', '2', '1X', '12', 'X2'}
        anchor = next((w for w in header if _clean_text(w.get('text')) == '1X'), None)
        if anchor is None:
            return None
        row = sorted((w for w in header
                      if abs(float(w['top']) - float(anchor['top'])) <= 2 * scale
                      and _clean_text(w.get('text')).upper() in labels | {'UNDER', 'OVER', 'G', 'NG'}),
                     key=lambda w: float(w['x0']))
        if not labels.issubset({_clean_text(w.get('text')).upper() for w in row}):
            return None
        centres = [(float(w['x0']) + float(w['x1'])) / 2 for w in row]
        groups = []
        for word in header:
            if _clean_text(word.get('text')).upper() != 'U/O':
                continue
            following = sorted((w for w in header
                                if 0 <= float(w['x0']) - float(word['x1']) < 25 * scale
                                and abs(float(w['top']) - float(word['top'])) <= 2 * scale),
                               key=lambda w: float(w['x0']))
            line = next((_clean_text(w.get('text')).replace(',', '.') for w in following
                         if re.fullmatch(r'\d+[.,]\d+', _clean_text(w.get('text')))), None)
            if line:
                groups.append(((float(word['x0']) + float(word['x1'])) / 2, line))
        layout = []
        for i, (word, centre) in enumerate(zip(row, centres)):
            left = (centres[i - 1] + centre) / 2 if i else centre - (centres[1] - centre) / 2
            right = (centre + centres[i + 1]) / 2 if i + 1 < len(row) else centre + (centre - centres[i - 1]) / 2
            label = _clean_text(word.get('text')).upper()
            market = label if label in labels else {'G': 'Goal', 'NG': 'No Goal'}.get(label)
            if label in ('UNDER', 'OVER') and groups:
                group, line = min(groups, key=lambda item: abs(item[0] - centre))
                if abs(group - centre) <= (right - left) * 1.1:
                    market = ('Under ' if label == 'UNDER' else 'Over ') + line
            layout.append((market, left, right))
        return layout

    def _find_row_intervals(
        self,
        page: Any,
        words: Sequence[Mapping[str, Any]],
        header_bottom: float,
        scale: float,
    ) -> list[tuple[float, float, dict[str, Any]]]:
        """Trova i confini reali delle righe usando la griglia del PDF."""
        horizontal_counts: dict[float, int] = {}

        for edge in page.edges:
            if edge.get("orientation") != "h":
                continue

            top = round(float(edge.get("top", 0.0)), 1)

            if top < header_bottom:
                continue

            horizontal_counts[top] = horizontal_counts.get(top, 0) + 1

        boundaries = sorted(
            top
            for top, count in horizontal_counts.items()
            if count >= len(MARKET_COLUMNS) + 3
        )

        anchors = self._find_row_anchors(
            words=words,
            header_bottom=header_bottom,
            scale=scale,
        )

        intervals: list[tuple[float, float, dict[str, Any]]] = []

        for anchor in anchors:
            anchor_top = float(anchor["top"])
            lower = max(
                (value for value in boundaries if value < anchor_top),
                default=anchor_top - 7.0 * scale,
            )
            upper = min(
                (value for value in boundaries if value > anchor_top),
                default=anchor_top + 12.0 * scale,
            )

            if upper <= lower:
                continue

            intervals.append((lower, upper, anchor))

        return intervals

    def _find_row_anchors(
        self,
        words: Sequence[Mapping[str, Any]],
        header_bottom: float,
        scale: float,
    ) -> list[dict[str, Any]]:
        anchors: list[dict[str, Any]] = []

        for word in words:
            text = _clean_text(word.get("text"))
            x0 = float(word.get("x0", 0.0))
            top = float(word.get("top", 0.0))

            if top <= header_bottom:
                continue

            if 27.0 * scale <= x0 <= 47.0 * scale and TIME_PATTERN.match(text):
                anchors.append(
                    {
                        "top": top,
                        "time": text,
                    }
                )

        anchors.sort(key=lambda item: item["top"])

        deduplicated: list[dict[str, Any]] = []

        for anchor in anchors:
            if (
                deduplicated
                and abs(anchor["top"] - deduplicated[-1]["top"])
                <= self.row_tolerance * scale
            ):
                continue
            deduplicated.append(anchor)

        return deduplicated

    def _parse_row(
        self,
        row_words: Sequence[Mapping[str, Any]],
        anchor: Mapping[str, Any],
        page_number: int,
        date_value: str,
        row_top: float,
        event_x_min: float,
        event_x_max: float,
        odds_x_min: float,
        column_centers: Sequence[float],
        column_tolerance: float,
        scale: float,
        column_layout=None,
    ) -> PdfOddsEvent | None:
        source_code = ""
        time_value = _clean_text(anchor.get("time"))

        for word in row_words:
            text = _clean_text(word.get("text"))
            x0 = float(word.get("x0", 0.0))

            if 8.0 * scale <= x0 < 29.0 * scale and text.isdigit():
                source_code = text
                break

        event_lines: dict[float, list[Mapping[str, Any]]] = {}

        for word in row_words:
            x0 = float(word.get("x0", 0.0))
            x1 = float(word.get("x1", 0.0))

            if x0 < event_x_min or x1 > event_x_max:
                continue

            top = round(float(word.get("top", 0.0)), 1)
            event_lines.setdefault(top, []).append(word)

        line_texts: list[tuple[float, str]] = []

        for top, line_words in event_lines.items():
            ordered = sorted(
                line_words,
                key=lambda item: float(item.get("x0", 0.0)),
            )
            text = _clean_text(
                " ".join(
                    _clean_text(item.get("text"))
                    for item in ordered
                )
            )
            if text:
                line_texts.append((top, text))

        line_texts.sort(key=lambda item: item[0])

        competition = line_texts[0][1] if line_texts else ""
        event_text = " ".join(
            text
            for _, text in line_texts[1:]
        ).strip()

        # Alcuni PDF possono avere competizione ed evento sulla stessa linea.
        if not event_text and competition:
            event_text = competition
            competition = ""

        home, away = split_match_name(event_text)

        if not home or not away:
            return None

        odds: dict[str, float] = {}

        for word in row_words:
            center = (
                float(word.get("x0", 0.0))
                + float(word.get("x1", 0.0))
            ) / 2.0

            if center < odds_x_min:
                continue

            odd = _safe_float(word.get("text"))

            if odd is None:
                continue

            if column_layout is not None:
                market = next((market for market, left, right in column_layout if left <= center < right), None)
                if market in MARKET_COLUMNS:
                    odds[PDF_TO_ATLAS_MARKET.get(market, market)] = odd
                continue

            nearest_index = min(
                range(len(column_centers)),
                key=lambda idx: abs(center - column_centers[idx]),
            )

            if abs(center - column_centers[nearest_index]) > column_tolerance:
                continue

            market = MARKET_COLUMNS[nearest_index]
            atlas_market = PDF_TO_ATLAS_MARKET.get(market, market)
            odds[atlas_market] = odd

        return PdfOddsEvent(
            page=page_number,
            source_code=source_code,
            date=date_value,
            time=time_value,
            competition=competition,
            home=home,
            away=away,
            odds=odds,
            raw_event=event_text,
            row_top=row_top,
        )


def riepilogo_parsing_pdf(
    events: Sequence[PdfOddsEvent],
) -> dict[str, Any]:
    """Restituisce statistiche utili per verificare ogni PDF importato."""
    total_quotes = sum(len(event.odds) for event in events)
    complete_events = sum(
        1 for event in events
        if len(event.odds) == len(MARKET_COLUMNS)
    )
    partial_events = len(events) - complete_events
    missing_by_market = {
        market: sum(
            1 for event in events
            if market not in event.odds
        )
        for market in MARKET_COLUMNS
    }

    return {
        "eventi_trovati": len(events),
        "eventi_completi": complete_events,
        "eventi_parziali": partial_events,
        "quote_riconosciute": total_quotes,
        "celle_vuote": (
            len(events) * len(MARKET_COLUMNS)
            - total_quotes
        ),
        "mercati_mancanti": missing_by_market,
    }


# ---------------------------------------------------------------------------
# Matching eventi
# ---------------------------------------------------------------------------

class AtlasEventMatcher:
    """Abbina eventi PDF ed eventi Atlas senza usare il codice bookmaker."""

    def __init__(
        self,
        auto_threshold: float = 90.0,
        review_threshold: float = 75.0,
        ambiguity_margin: float = 4.0,
        time_tolerance_minutes: int = 120,
    ) -> None:
        self.auto_threshold = auto_threshold
        self.review_threshold = review_threshold
        self.ambiguity_margin = ambiguity_margin
        self.time_tolerance_minutes = time_tolerance_minutes

    def match(
        self,
        pdf_events: Sequence[PdfOddsEvent],
        atlas_rows: Sequence[Mapping[str, Any]],
    ) -> list[EventMatch]:
        results: list[EventMatch] = []
        used_indices: set[int] = set()

        for pdf_event in pdf_events:
            candidates: list[
                tuple[int, Mapping[str, Any], MatchScore]
            ] = []

            for index, atlas_row in enumerate(atlas_rows):
                score = self.score_event(pdf_event, atlas_row)
                candidates.append((index, atlas_row, score))

            candidates.sort(
                key=lambda item: item[2].total,
                reverse=True,
            )

            if not candidates:
                results.append(
                    EventMatch(
                        pdf_event=pdf_event,
                        atlas_index=None,
                        atlas_event=None,
                        score=self._empty_score(),
                        status="non_trovato",
                    )
                )
                continue

            best_index, best_row, best_score = candidates[0]
            second_score = (
                candidates[1][2].total
                if len(candidates) > 1
                else 0.0
            )

            ambiguous = (
                best_score.total >= self.review_threshold
                and best_score.total - second_score
                < self.ambiguity_margin
            )

            if best_score.total >= self.auto_threshold and not ambiguous:
                status = "automatico"
            elif best_score.total >= self.review_threshold:
                status = "da_confermare"
            else:
                status = "non_trovato"

            # Lo stesso evento Atlas non viene assegnato automaticamente due volte.
            if status == "automatico" and best_index in used_indices:
                status = "da_confermare"

            if status == "automatico":
                used_indices.add(best_index)

            results.append(
                EventMatch(
                    pdf_event=pdf_event,
                    atlas_index=(
                        best_index
                        if status != "non_trovato"
                        else None
                    ),
                    atlas_event=(
                        dict(best_row)
                        if status != "non_trovato"
                        else None
                    ),
                    score=best_score,
                    status=status,
                )
            )

        return results

    def score_event(
        self,
        pdf_event: PdfOddsEvent,
        atlas_row: Mapping[str, Any],
    ) -> MatchScore:
        atlas_home, atlas_away = atlas_team_names(atlas_row)
        atlas_date, atlas_time = parse_atlas_datetime(atlas_row)
        atlas_competition = _clean_text(
            atlas_row.get("Manifestazione")
            or atlas_row.get("league")
            or atlas_row.get("competition")
        )

        direct_home = self._team_similarity(
            pdf_event.home,
            atlas_home,
        )
        direct_away = self._team_similarity(
            pdf_event.away,
            atlas_away,
        )
        reverse_home = self._team_similarity(
            pdf_event.home,
            atlas_away,
        )
        reverse_away = self._team_similarity(
            pdf_event.away,
            atlas_home,
        )

        direct_average = (direct_home + direct_away) / 2.0
        reverse_average = (reverse_home + reverse_away) / 2.0
        reversed_teams = reverse_average > direct_average + 6.0

        if reversed_teams:
            home_score = reverse_home
            away_score = reverse_away
        else:
            home_score = direct_home
            away_score = direct_away

        date_score = self._date_score(
            pdf_event.date,
            atlas_date,
        )
        time_score = self._time_score(
            pdf_event.time,
            atlas_time,
        )
        competition_score = self._competition_similarity(
            pdf_event.competition,
            atlas_competition,
        )

        # I nomi delle squadre hanno il peso maggiore.
        total = (
            home_score * 0.36
            + away_score * 0.36
            + date_score * 0.12
            + time_score * 0.10
            + competition_score * 0.06
        )

        reasons = [
            f"casa {home_score:.1f}",
            f"ospite {away_score:.1f}",
            f"data {date_score:.1f}",
            f"ora {time_score:.1f}",
            f"competizione {competition_score:.1f}",
        ]

        if reversed_teams:
            reasons.append("ordine squadre invertito")

        return MatchScore(
            total=round(total, 2),
            home_score=round(home_score, 2),
            away_score=round(away_score, 2),
            date_score=round(date_score, 2),
            time_score=round(time_score, 2),
            competition_score=round(competition_score, 2),
            reversed_teams=reversed_teams,
            reasons=reasons,
        )

    def _team_similarity(self, first: str, second: str) -> float:
        first_normalized = normalize_name(first)
        second_normalized = normalize_name(second)

        if not first_normalized or not second_normalized:
            return 0.0

        scores = (
            fuzz.ratio(first_normalized, second_normalized),
            fuzz.token_sort_ratio(
                first_normalized,
                second_normalized,
            ),
            fuzz.token_set_ratio(
                first_normalized,
                second_normalized,
            ),
            fuzz.partial_ratio(
                first_normalized,
                second_normalized,
            ),
        )

        return float(max(scores))

    def _competition_similarity(
        self,
        first: str,
        second: str,
    ) -> float:
        first_normalized = normalize_competition(first)
        second_normalized = normalize_competition(second)

        if not first_normalized or not second_normalized:
            return 50.0

        return float(
            max(
                fuzz.token_set_ratio(
                    first_normalized,
                    second_normalized,
                ),
                fuzz.token_sort_ratio(
                    first_normalized,
                    second_normalized,
                ),
            )
        )

    @staticmethod
    def _date_score(first: str, second: str) -> float:
        if not first or not second:
            return 50.0

        if first == second:
            return 100.0

        formats = ("%d/%m/%Y", "%Y-%m-%d")

        first_date = None
        second_date = None

        for fmt in formats:
            try:
                first_date = datetime.strptime(first, fmt).date()
                break
            except ValueError:
                continue

        for fmt in formats:
            try:
                second_date = datetime.strptime(second, fmt).date()
                break
            except ValueError:
                continue

        if first_date is None or second_date is None:
            return 0.0

        delta = abs((first_date - second_date).days)

        if delta == 0:
            return 100.0
        if delta == 1:
            return 35.0
        return 0.0

    def _time_score(self, first: str, second: str) -> float:
        difference = circular_minute_difference(first, second)

        if difference is None:
            return 50.0

        if difference <= 5:
            return 100.0
        if difference <= 15:
            return 92.0
        if difference <= 30:
            return 80.0
        if difference <= 60:
            return 60.0
        if difference <= self.time_tolerance_minutes:
            return 35.0
        return 0.0

    @staticmethod
    def _empty_score() -> MatchScore:
        return MatchScore(
            total=0.0,
            home_score=0.0,
            away_score=0.0,
            date_score=0.0,
            time_score=0.0,
            competition_score=0.0,
            reversed_teams=False,
            reasons=[],
        )


# ---------------------------------------------------------------------------
# Import quote mancanti
# ---------------------------------------------------------------------------

def is_valid_existing_odd(value: Any) -> bool:
    return _safe_float(value) is not None


def event_source_key(row: Mapping[str, Any], index: int) -> str:
    event_id = _clean_text(
        row.get("ID Evento")
        or row.get("event_id")
    )
    return event_id or f"manuale:{index}"


def import_missing_quotes(
    atlas_rows: Sequence[Mapping[str, Any]],
    matches: Sequence[EventMatch],
    existing_sources: Mapping[str, Mapping[str, Any]] | None = None,
    accepted_statuses: Iterable[str] = ("automatico",),
    overwrite_existing: bool = False,
    source_name: str = "Oddsprinter PDF",
) -> ImportReport:
    updated_rows = [dict(row) for row in atlas_rows]
    updated_sources: dict[str, dict[str, Any]] = {
        str(key): dict(value)
        for key, value in (existing_sources or {}).items()
    }

    accepted = set(accepted_statuses)
    imported_quotes = 0
    skipped_existing = 0
    unmatched = 0
    ambiguous = 0

    for match in matches:
        if match.status == "non_trovato":
            unmatched += 1
            continue

        if match.status == "da_confermare":
            ambiguous += 1

        if match.status not in accepted:
            continue

        if match.atlas_index is None:
            continue

        row = updated_rows[match.atlas_index]
        source_key = event_source_key(
            row,
            match.atlas_index,
        )
        event_sources = updated_sources.setdefault(
            source_key,
            {},
        )

        for market, odd in match.pdf_event.odds.items():
            if odd <= 1.0:
                continue

            current_value = row.get(market)

            if (
                not overwrite_existing
                and is_valid_existing_odd(current_value)
            ):
                skipped_existing += 1
                continue

            row[market] = float(odd)
            event_sources[market] = {
                "bookmaker": source_name,
                "tipo": "Bookmaker",
                "quota_effettiva": float(odd),
                "origine": "pdf",
                "confidenza_match": match.score.total,
                "pagina_pdf": match.pdf_event.page,
                "evento_pdf": match.pdf_event.match_name,
                "codice_pdf_ignorato": match.pdf_event.source_code,
            }
            imported_quotes += 1

        updated_rows[match.atlas_index] = row
        updated_sources[source_key] = event_sources

    return ImportReport(
        atlas_rows=updated_rows,
        sources=updated_sources,
        matches=list(matches),
        imported_quotes=imported_quotes,
        skipped_existing_quotes=skipped_existing,
        unmatched_events=unmatched,
        ambiguous_events=ambiguous,
    )


# ---------------------------------------------------------------------------
# API pubblica ad alto livello
# ---------------------------------------------------------------------------

def leggi_pdf_quote(
    pdf_path: str | Path,
) -> list[dict[str, Any]]:
    parser = OddsPrinterPdfParser()
    return [
        event.to_dict()
        for event in parser.parse(pdf_path)
    ]


def abbina_eventi_pdf_atlas(
    pdf_events: Sequence[PdfOddsEvent | Mapping[str, Any]],
    atlas_rows: Sequence[Mapping[str, Any]],
    auto_threshold: float = 90.0,
    review_threshold: float = 75.0,
) -> list[EventMatch]:
    normalized_events: list[PdfOddsEvent] = []

    for item in pdf_events:
        if isinstance(item, PdfOddsEvent):
            normalized_events.append(item)
            continue

        normalized_events.append(
            PdfOddsEvent(
                page=int(item.get("page", 0)),
                source_code=_clean_text(
                    item.get("source_code")
                ),
                date=_clean_text(item.get("date")),
                time=_clean_text(item.get("time")),
                competition=_clean_text(
                    item.get("competition")
                ),
                home=_clean_text(item.get("home")),
                away=_clean_text(item.get("away")),
                odds={
                    str(key): float(value)
                    for key, value in dict(
                        item.get("odds", {})
                    ).items()
                    if _safe_float(value) is not None
                },
                raw_event=_clean_text(
                    item.get("raw_event")
                ),
                row_top=float(item.get("row_top", 0.0)),
            )
        )

    matcher = AtlasEventMatcher(
        auto_threshold=auto_threshold,
        review_threshold=review_threshold,
    )
    return matcher.match(
        pdf_events=normalized_events,
        atlas_rows=atlas_rows,
    )


def importa_pdf_in_atlas(
    pdf_path: str | Path,
    atlas_rows: Sequence[Mapping[str, Any]],
    existing_sources: Mapping[str, Mapping[str, Any]] | None = None,
    include_review_matches: bool = False,
    overwrite_existing: bool = False,
    auto_threshold: float = 90.0,
    review_threshold: float = 75.0,
) -> ImportReport:
    parser = OddsPrinterPdfParser()
    pdf_events = parser.parse(pdf_path)

    matcher = AtlasEventMatcher(
        auto_threshold=auto_threshold,
        review_threshold=review_threshold,
    )
    matches = matcher.match(
        pdf_events=pdf_events,
        atlas_rows=atlas_rows,
    )

    accepted_statuses = (
        ("automatico", "da_confermare")
        if include_review_matches
        else ("automatico",)
    )

    return import_missing_quotes(
        atlas_rows=atlas_rows,
        matches=matches,
        existing_sources=existing_sources,
        accepted_statuses=accepted_statuses,
        overwrite_existing=overwrite_existing,
    )


def riepilogo_abbinamenti(
    matches: Sequence[EventMatch],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for match in matches:
        atlas_name = ""

        if match.atlas_event:
            atlas_name = _clean_text(
                match.atlas_event.get("Partita")
                or match.atlas_event.get("match")
            )

        rows.append(
            {
                "Evento PDF": match.pdf_event.match_name,
                "Evento Atlas": atlas_name,
                "Data PDF": match.pdf_event.date,
                "Ora PDF": match.pdf_event.time,
                "Competizione PDF": match.pdf_event.competition,
                "Confidenza %": match.score.total,
                "Stato": match.status,
                "Quote disponibili": len(
                    match.pdf_event.odds
                ),
                "Pagina PDF": match.pdf_event.page,
                "Codice PDF ignorato": (
                    match.pdf_event.source_code
                ),
                "Dettagli": " · ".join(
                    match.score.reasons
                ),
            }
        )

    return rows


__all__ = [
    "PdfOddsEvent",
    "MatchScore",
    "EventMatch",
    "ImportReport",
    "OddsPrinterPdfParser",
    "AtlasEventMatcher",
    "normalize_name",
    "normalize_competition",
    "split_match_name",
    "leggi_pdf_quote",
    "abbina_eventi_pdf_atlas",
    "import_missing_quotes",
    "importa_pdf_in_atlas",
    "riepilogo_abbinamenti",
    "riepilogo_parsing_pdf",
]
