"""Conservative, evidence-linked interpretation of Greece 2.0 decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
import re
import unicodedata

from pypdf import PdfReader


class DecisionRole(StrEnum):
    """The explicit role of a Greece 2.0 act in a project history."""

    INCLUSION = "inclusion"
    MODIFICATION = "modification"
    UNRESOLVED = "unresolved"


class DecisionEffect(StrEnum):
    """An explicit legal effect stated by a Greece 2.0 act."""

    FULL_CANCELLATION = "full_cancellation"
    PARTIAL_CANCELLATION = "partial_cancellation"


class FundingStream(StrEnum):
    """The funding stream explicitly attached to a monetary effect."""

    RRF = "rrf"
    NATIONAL = "national"
    OTHER = "other"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class TextEvidence:
    source: str
    text: str
    char_start: int
    char_end: int
    page_number: int


@dataclass(frozen=True)
class DecisionEffectEvidence:
    effect: DecisionEffect
    evidence: TextEvidence


@dataclass(frozen=True)
class CancellationAmount:
    amount_cents: int
    currency: str
    funding_stream: FundingStream
    evidence: TextEvidence


@dataclass(frozen=True)
class Greece20DecisionClassification:
    role: DecisionRole
    role_evidence: TextEvidence | None
    effects: tuple[DecisionEffectEvidence, ...]
    cancellation_amounts: tuple[CancellationAmount, ...]
    unresolved_reason: str | None = None


_SUBJECT = re.compile(
    r"\bΘΕΜΑ\s*:\s*(.+?)(?=\n\s*ΑΠΟΦΑΣΗ\b)",
    re.IGNORECASE | re.DOTALL,
)
_OPERATIVE = re.compile(
    r"\bΑΠΟΦΑΣ[ΙΊ]Ζ(?:ΟΥΜΕ|ΕΤΑΙ)\b(.{0,700})",
    re.IGNORECASE | re.DOTALL,
)
_EURO_AMOUNT = re.compile(
    r"(?:μερικ(?:ή|ής|ώς)\s+|ολικ(?:ή|ής|ώς)\s+)?"
    r"ανάκληση\s+ποσ(?:ό|ού)\s+(?:ύψους\s+)?"
    r"([0-9][0-9.\s]*(?:,[0-9]{1,2})?)\s*(?:€|ευρώ)",
    re.IGNORECASE,
)


def extract_greece20_decision_text(pdf_bytes: bytes) -> str:
    """Extract text from a preserved Greece 2.0 PDF."""
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\f".join(page.extract_text() or "" for page in reader.pages)


def _searchable(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    unaccented = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return unaccented.replace("ς", "σ")


def _text_evidence(
    text: str, source: str, char_start: int, char_end: int
) -> TextEvidence:
    while char_start < char_end and text[char_start].isspace():
        char_start += 1
    while char_end > char_start and text[char_end - 1].isspace():
        char_end -= 1
    return TextEvidence(
        source=source,
        text=text[char_start:char_end],
        char_start=char_start,
        char_end=char_end,
        page_number=text.count("\f", 0, char_start) + 1,
    )


def _evidence_regions(text: str) -> list[TextEvidence]:
    regions: list[TextEvidence] = []
    subject = _SUBJECT.search(text)
    if subject:
        regions.append(_text_evidence(text, "subject", *subject.span(1)))
    operative = _OPERATIVE.search(text)
    if operative:
        regions.append(
            _text_evidence(text, "operative_clause", *operative.span(0))
        )
    return regions


def _first_matching(
    regions: list[TextEvidence], patterns: tuple[str, ...]
) -> TextEvidence | None:
    for region in regions:
        searchable = _searchable(region.text)
        if any(re.search(pattern, searchable) for pattern in patterns):
            return region
    return None


def _role_evidence(
    regions: list[TextEvidence], role: DecisionRole
) -> TextEvidence | None:
    for region in regions:
        searchable = _searchable(region.text).strip()
        if role is DecisionRole.MODIFICATION:
            if region.source == "subject":
                pattern = r"^(?:\d+\w*\s+)?τροποποιηση\b"
            else:
                pattern = r"^αποφασιζουμε\s+(?:την\s+)?τροποποιηση\b"
        else:
            if region.source == "subject":
                pattern = r"^(?:αποφαση\s+)?ενταξη\b"
            else:
                pattern = r"^αποφασιζουμε\s+την\s+ενταξη\b"
        if re.search(pattern, searchable):
            return region
    return None


def _effect_evidence(
    regions: list[TextEvidence], effect: DecisionEffect
) -> TextEvidence | None:
    for region in regions:
        searchable = _searchable(region.text).strip()
        if effect is DecisionEffect.PARTIAL_CANCELLATION:
            if region.source == "subject":
                patterns = (
                    r"^μερικ\w*\s+ανακληση\b",
                    r"\bκαι\s+μερικ\w*\s+ανακληση\b",
                )
            else:
                patterns = (
                    r"^αποφασιζουμε\s+(?:τη|την)\s+μερικ\w*\s+ανακληση\b",
                    r"^αποφασιζεται\s+η\s+μερικ\w*\s+ανακληση\b",
                )
        else:
            if region.source == "subject":
                patterns = (
                    r"^(?:ολικ\w*\s+)?ανακληση\b",
                    r"^απενταξη\b",
                )
            else:
                patterns = (
                    r"^αποφασιζουμε\s+την\s+(?:ολικ\w*\s+)?ανακληση\b",
                    r"^αποφασιζουμε\s+την\s+απενταξη\b",
                    r"^αποφασιζεται\s+η\s+(?:ολικ\w*\s+)?ανακληση\b",
                )
        if any(re.search(pattern, searchable) for pattern in patterns):
            return region
    return None


def _amount_cents(value: str) -> int:
    normalized = re.sub(r"[.\s]", "", value).replace(",", ".")
    whole, separator, fraction = normalized.partition(".")
    fraction = (fraction + "00")[:2] if separator else "00"
    return int(whole) * 100 + int(fraction)


def _funding_stream(evidence_text: str) -> FundingStream:
    searchable = re.sub(r"\s+", " ", _searchable(evidence_text))
    if any(
        phrase in searchable
        for phrase in (
            "ταμειο ανακαμψησ",
            "ταμειου ανακαμψησ",
            "μηχανισμο ανακαμψησ",
            "μηχανισμου ανακαμψησ",
        )
    ):
        return FundingStream.RRF
    if "εθνικ" in searchable and "πορ" in searchable:
        return FundingStream.NATIONAL
    return FundingStream.UNRESOLVED


def _cancellation_amounts(
    source_text: str, cancellation_regions: list[TextEvidence],
) -> tuple[CancellationAmount, ...]:
    amounts: list[CancellationAmount] = []
    seen: set[tuple[int, FundingStream]] = set()
    for region in cancellation_regions:
        matches = list(_EURO_AMOUNT.finditer(region.text))
        for index, match in enumerate(matches):
            amount = _amount_cents(match.group(1))
            next_match_start = (
                matches[index + 1].start() if index + 1 < len(matches) else len(region.text)
            )
            sentence_end = region.text.find(".", match.end(), next_match_start)
            local_end = sentence_end if sentence_end >= 0 else next_match_start
            clause = region.text[match.start():local_end]
            stream = _funding_stream(clause)
            key = (amount, stream)
            if key in seen:
                continue
            seen.add(key)
            evidence = _text_evidence(
                source_text,
                "cancellation_amount_clause",
                region.char_start + match.start(),
                region.char_start + local_end,
            )
            amounts.append(
                CancellationAmount(
                    amount_cents=amount,
                    currency="EUR",
                    funding_stream=stream,
                    evidence=evidence,
                )
            )
    return tuple(amounts)


def classify_greece20_decision(text: str) -> Greece20DecisionClassification:
    """Classify only explicit subject or operative-clause evidence.

    Citations elsewhere in a decision, filenames, catalogue flags, and budget
    differences are deliberately outside this classifier.
    """
    regions = _evidence_regions(text)
    modification = _role_evidence(regions, DecisionRole.MODIFICATION)
    inclusion = _role_evidence(regions, DecisionRole.INCLUSION)
    if modification is not None:
        role = DecisionRole.MODIFICATION
        role_evidence = modification
        unresolved_reason = None
    elif inclusion is not None:
        role = DecisionRole.INCLUSION
        role_evidence = inclusion
        unresolved_reason = None
    else:
        role = DecisionRole.UNRESOLVED
        role_evidence = None
        unresolved_reason = "no explicit role evidence"

    partial = _effect_evidence(regions, DecisionEffect.PARTIAL_CANCELLATION)
    full = _effect_evidence(regions, DecisionEffect.FULL_CANCELLATION)
    effects: list[DecisionEffectEvidence] = []
    cancellation_regions: list[TextEvidence] = []
    if partial is not None:
        effects.append(
            DecisionEffectEvidence(DecisionEffect.PARTIAL_CANCELLATION, partial)
        )
        cancellation_regions.extend(regions)
    elif full is not None:
        effects.append(DecisionEffectEvidence(DecisionEffect.FULL_CANCELLATION, full))
        cancellation_regions.extend(regions)

    return Greece20DecisionClassification(
        role=role,
        role_evidence=role_evidence,
        effects=tuple(effects),
        cancellation_amounts=_cancellation_amounts(text, cancellation_regions),
        unresolved_reason=unresolved_reason,
    )
