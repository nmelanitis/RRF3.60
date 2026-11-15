"""Parse the official Greece 2.0 included-project catalogue."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from typing import Final


CATALOGUE_HEADERS: Final[tuple[str, ...]] = (
    "Α/Α",
    "ΠΥΛΩΝΑΣ",
    "ΑΞΟΝΑΣ",
    "ΥΠΟΥΡΓΕΙΟ ΕΥΘΥΝΗΣ",
    "ΦΟΡΕΑΣ ΥΛΟΠΟΙΗΣΗΣ",
    "ΚΩΔΙΚΟΣ ΔΡΑΣΗΣ",
    "ΤΙΤΛΟΣ ΔΡΑΣΗΣ",
    "ΗΜ/ΝΙΑ ΕΝΤΑΞΗΣ",
    "ΕΡΓΟ",
    "ΤΡΟΠΟΠΟΙΗΣΗ",
    "ΚΩΔΙΚΟΣ ΟΠΣ",
    "ΤΥΠΟΣ ΔΡΑΣΗΣ",
    "ΠΡΟΫΠΟΛΟΓΙΣΜΟΣ",
)


class CatalogueFormatError(ValueError):
    """Raised when the official catalogue no longer matches its known shape."""


@dataclass(frozen=True)
class CatalogueEntry:
    catalogue_sequence: str | None
    pillar: str | None
    axis: str | None
    responsible_ministry: str | None
    implementing_body: str | None
    action_code: str | None
    action_title: str | None
    inclusion_date: date | None
    project_title: str | None
    is_modification: bool | None
    ops_code: str | None
    decision_url: str | None
    action_type: str | None
    source_budget_cents: int | None
    row_hash: str

    @property
    def source_record_key(self) -> str:
        """Return the best stable source key without merging repeated OPS rows."""
        return self.decision_url or f"row-sha256:{self.row_hash}"


@dataclass(frozen=True)
class _Cell:
    text: str
    links: tuple[str, ...]


def _normalise_text(value: str) -> str | None:
    normalised = re.sub(r"\s+", " ", value).strip()
    return normalised or None


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError as error:
        raise CatalogueFormatError(f"Unexpected inclusion date: {value!r}") from error


def _parse_modification(value: str | None) -> bool | None:
    if value is None:
        return None
    folded = value.casefold()
    if folded in {"ναι", "yes"}:
        return True
    if folded in {"όχι", "οχι", "no"}:
        return False
    raise CatalogueFormatError(f"Unexpected modification marker: {value!r}")


def _parse_budget_cents(value: str | None) -> int | None:
    if value is None:
        return None
    compact = re.sub(r"[\s€]", "", value).replace(".", "").replace(",", ".")
    try:
        amount = Decimal(compact)
    except InvalidOperation as error:
        raise CatalogueFormatError(f"Unexpected budget: {value!r}") from error
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise CatalogueFormatError(f"Budget has sub-cent precision: {value!r}")
    return int(cents)


def _entry_from_cells(headers: list[str], cells: list[_Cell]) -> CatalogueEntry:
    if len(cells) != len(headers):
        raise CatalogueFormatError(
            f"Catalogue row has {len(cells)} cells; expected {len(headers)}"
        )
    row = dict(zip(headers, cells, strict=True))

    def text(header: str) -> str | None:
        return _normalise_text(row[header].text)

    decision_links = row["ΚΩΔΙΚΟΣ ΟΠΣ"].links
    values = {
        "catalogue_sequence": text("Α/Α"),
        "pillar": text("ΠΥΛΩΝΑΣ"),
        "axis": text("ΑΞΟΝΑΣ"),
        "responsible_ministry": text("ΥΠΟΥΡΓΕΙΟ ΕΥΘΥΝΗΣ"),
        "implementing_body": text("ΦΟΡΕΑΣ ΥΛΟΠΟΙΗΣΗΣ"),
        "action_code": text("ΚΩΔΙΚΟΣ ΔΡΑΣΗΣ"),
        "action_title": text("ΤΙΤΛΟΣ ΔΡΑΣΗΣ"),
        "inclusion_date": _parse_date(text("ΗΜ/ΝΙΑ ΕΝΤΑΞΗΣ")),
        "project_title": text("ΕΡΓΟ"),
        "is_modification": _parse_modification(text("ΤΡΟΠΟΠΟΙΗΣΗ")),
        "ops_code": text("ΚΩΔΙΚΟΣ ΟΠΣ"),
        "decision_url": decision_links[0] if decision_links else None,
        "action_type": text("ΤΥΠΟΣ ΔΡΑΣΗΣ"),
        "source_budget_cents": _parse_budget_cents(text("ΠΡΟΫΠΟΛΟΓΙΣΜΟΣ")),
    }
    canonical = {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in values.items()
    }
    row_hash = sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return CatalogueEntry(**values, row_hash=row_hash)


class _CatalogueParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_catalogue = False
        self.table_section: str | None = None
        self.in_row = False
        self.cell_tag: str | None = None
        self.cell_text: list[str] = []
        self.cell_links: list[str] = []
        self.row_cells: list[_Cell] = []
        self.row_uses_headers = False
        self.headers: list[str] = []
        self.entries: list[CatalogueEntry] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "table_2":
            self.in_catalogue = True
            return
        if not self.in_catalogue:
            return
        if tag in {"thead", "tbody", "tfoot"}:
            self.table_section = tag
        elif tag == "tr" and self.table_section in {"thead", "tbody"}:
            self.in_row = True
            self.row_cells = []
            self.row_uses_headers = False
        elif self.in_row and tag in {"th", "td"}:
            self.cell_tag = tag
            self.cell_text = []
            self.cell_links = []
            self.row_uses_headers = self.row_uses_headers or tag == "th"
        elif self.cell_tag is not None and tag == "a" and attributes.get("href"):
            self.cell_links.append(attributes["href"] or "")

    def handle_data(self, data: str) -> None:
        if self.in_catalogue and self.cell_tag is not None:
            self.cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_catalogue:
            return
        if self.cell_tag == tag:
            self.row_cells.append(
                _Cell("".join(self.cell_text), tuple(self.cell_links))
            )
            self.cell_tag = None
            self.cell_text = []
            self.cell_links = []
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if not self.row_cells:
                return
            if self.row_uses_headers:
                self.headers = [
                    _normalise_text(cell.text) or "" for cell in self.row_cells
                ]
            else:
                if not self.headers:
                    raise CatalogueFormatError("Catalogue data appeared before headers")
                self.entries.append(_entry_from_cells(self.headers, self.row_cells))
        elif tag in {"thead", "tbody", "tfoot"}:
            self.table_section = None
        elif tag == "table":
            self.in_catalogue = False


def parse_catalogue(html: str) -> list[CatalogueEntry]:
    """Parse all rows from the official included-project catalogue."""
    parser = _CatalogueParser()
    parser.feed(html)
    parser.close()
    if not parser.headers:
        raise CatalogueFormatError("Could not find the Greece 2.0 catalogue table")
    if tuple(parser.headers) != CATALOGUE_HEADERS:
        raise CatalogueFormatError(
            "Catalogue headers changed: " + json.dumps(parser.headers, ensure_ascii=False)
        )
    return parser.entries


def entry_as_storage_dict(entry: CatalogueEntry) -> dict[str, object]:
    """Convert an entry into SQLite-friendly primitive values."""
    values = asdict(entry)
    values["inclusion_date"] = (
        entry.inclusion_date.isoformat() if entry.inclusion_date else None
    )
    values["is_modification"] = (
        int(entry.is_modification) if entry.is_modification is not None else None
    )
    values["source_record_key"] = entry.source_record_key
    return values

