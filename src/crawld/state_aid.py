"""Import preserved official State Aid Transparency award exports."""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from hashlib import sha256
from io import StringIO
from pathlib import Path
import re

from crawld.kimdis import CompanyIdentifier


DEFAULT_COVERAGE_NOTE = (
    "State Aid Transparency publication thresholds and deadlines vary by legal "
    "basis; this imported export is not exhaustive of all aid."
)
GREEK_RRF_PHRASES = (
    "ΤΑΜΕΙΟ ΑΝΑΚΑΜΨΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ",
    "ΤΑΜΕΙΟ ΑΝΑΠΤΥΞΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ",
)


class AidInstrument(StrEnum):
    GRANT = "grant"
    LOAN = "loan"
    GUARANTEE = "guarantee"
    TAX_ADVANTAGE = "tax_advantage"
    OTHER = "other"
    UNKNOWN = "unknown"


class RrfAidLinkage(StrEnum):
    LINKED = "linked"
    NOT_LINKED = "not_linked"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class AidMeasureEvidence:
    aid_measure_reference: str
    rrf_linkage: RrfAidLinkage
    source_name: str
    source_url: str
    source_sha256: str
    source_act_id: str | None = None

    def __post_init__(self) -> None:
        if self.rrf_linkage is RrfAidLinkage.UNRESOLVED:
            raise ValueError("Measure evidence must establish linked or not_linked")
        if not self.source_url or not self.source_sha256:
            raise ValueError("Measure evidence requires source URL and SHA-256")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.source_sha256):
            raise ValueError("Measure evidence SHA-256 must contain 64 hexadecimal characters")


@dataclass(frozen=True)
class StateAidExportScope:
    country: str
    date_from: str | None
    date_to: str | None
    measure_filters: tuple[str, ...]
    full_result_export: bool

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z]{2,3}", self.country.strip()):
            raise ValueError("State Aid export country must be a 2- or 3-letter code")
        if type(self.full_result_export) is not bool:
            raise ValueError("State Aid full_result_export must be a JSON Boolean")
        if (self.date_from is None) != (self.date_to is None):
            raise ValueError("State Aid export date_from and date_to must be supplied together")
        if self.date_from is not None and self.date_to is not None:
            try:
                start = date.fromisoformat(self.date_from)
                end = date.fromisoformat(self.date_to)
            except ValueError as error:
                raise ValueError("State Aid export dates must use YYYY-MM-DD") from error
            if end < start:
                raise ValueError("State Aid export date_to precedes date_from")


@dataclass(frozen=True)
class StateAidCoverage:
    description: str
    publication_thresholds: tuple[str, ...]
    publication_deadlines: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("State Aid coverage description is required")
        if not self.publication_thresholds or not self.publication_deadlines:
            raise ValueError("State Aid thresholds and deadlines must be recorded")


@dataclass(frozen=True)
class StateAidAward:
    award_id: str
    beneficiary_name: str
    beneficiary_identifier: CompanyIdentifier | None
    beneficiary_identifier_issue: str | None
    aid_element_cents: int | None
    currency: str | None
    instrument: AidInstrument
    raw_instrument: str | None
    granting_date: str | None
    objective: str | None
    granting_authority: str | None
    aid_measure_reference: str | None
    rrf_linkage: RrfAidLinkage
    linkage_evidence: str
    measure_evidence: AidMeasureEvidence | None
    included_in_aid_aggregate: bool
    exclusion_reason: str | None
    source_row_number: int

    def __post_init__(self) -> None:
        if self.rrf_linkage is RrfAidLinkage.UNRESOLVED:
            if self.measure_evidence is not None:
                raise ValueError("Unresolved aid linkage cannot carry resolved evidence")
            return
        if self.measure_evidence is None:
            raise ValueError("Resolved aid linkage requires official measure evidence")
        if self.measure_evidence.rrf_linkage is not self.rrf_linkage:
            raise ValueError("Award linkage conflicts with measure evidence")
        if (
            self.aid_measure_reference is None
            or _measure_key(self.aid_measure_reference)
            != _measure_key(self.measure_evidence.aid_measure_reference)
        ):
            raise ValueError("Award measure reference conflicts with measure evidence")


@dataclass(frozen=True)
class StateAidImportRequest:
    csv_path: Path
    source_url: str
    imported_at: datetime
    measure_evidence: tuple[AidMeasureEvidence, ...]
    export_scope: StateAidExportScope
    coverage: StateAidCoverage


@dataclass(frozen=True)
class StateAidImportGap:
    source_row_number: int
    reason: str


@dataclass(frozen=True)
class StateAidDuplicateConflict:
    award_id: str
    source_row_numbers: tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class StateAidImportResult:
    source_url: str
    imported_at: datetime
    awards: tuple[StateAidAward, ...]
    source_row_count: int
    imported_award_count: int
    skipped_rows: tuple[StateAidImportGap, ...]
    field_gaps: tuple[StateAidImportGap, ...]
    duplicate_conflicts: tuple[StateAidDuplicateConflict, ...]
    export_scope: StateAidExportScope
    coverage: StateAidCoverage
    coverage_note: str
    content_sha256: str
    byte_count: int
    raw_path: Path


_HEADER_ALIASES = {
    "award id": "award_id",
    "award identifier": "award_id",
    "ref-no.": "award_id",
    "beneficiary name": "beneficiary_name",
    "name of beneficiary": "beneficiary_name",
    "name of the beneficiary": "beneficiary_name",
    "beneficiary identifier": "beneficiary_identifier",
    "beneficiary's identifier": "beneficiary_identifier",
    "national id": "beneficiary_identifier",
    "beneficiary identifier scheme": "identifier_scheme",
    "identifier scheme": "identifier_scheme",
    "member state": "country",
    "country": "country",
    "aid element": "aid_element",
    "aid element, expressed as full amount": "aid_element",
    "aid amount": "aid_element",
    "currency": "currency",
    "aid instrument": "aid_instrument",
    "date of granting": "granting_date",
    "granting date": "granting_date",
    "aid objective": "objective",
    "objective": "objective",
    "objectives of the aid": "objective",
    "granting authority": "granting_authority",
    "granting authority name": "granting_authority",
    "aid measure reference": "aid_measure_reference",
    "reference of the aid measure": "aid_measure_reference",
    "sa.number": "aid_measure_reference",
    "aid measure name": "aid_measure_name_en",
    "name of the aid measure": "aid_measure_name_en",
    "aid measure title [en]": "aid_measure_name_en",
    "aid measure title": "aid_measure_name_el",
    "αριθ. αναφ.": "award_id",
    "εθνικό αναγνωριστικό ταυτότητας": "beneficiary_identifier",
    "επωνυμία του δικαιούχου": "beneficiary_name",
    "δικαιούχος σε άλλο κράτος μέλος": "country",
    "στοιχείο ενίσχυσης, εκπεφρασμένο ως ακέραιο ποσό": "aid_element",
    "νόμισμα": "currency",
    "μέσο ενίσχυσης": "aid_instrument",
    "ημερομηνία χορήγησης": "granting_date",
    "στόχος της ενίσχυσης": "objective",
    "επωνυμία της χορηγούσας αρχής": "granting_authority",
    "αριθ. sa": "aid_measure_reference",
    "ονομασία του μέτρου ενίσχυσης [en]": "aid_measure_name_en",
    "ονομασία του μέτρου ενίσχυσης": "aid_measure_name_el",
}
_HEADER_ALIASES = {
    re.sub(r"\s+", " ", alias.strip().casefold()): canonical
    for alias, canonical in _HEADER_ALIASES.items()
}


def _header(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().casefold())
    return _HEADER_ALIASES.get(normalized, normalized.replace(" ", "_"))


def _optional(value: object) -> str | None:
    result = str(value or "").strip()
    return result or None


def _amount_cents(value: object) -> int | None:
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        amount = Decimal(text)
        if not amount.is_finite():
            return None
        return int(
            (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _country(value: object) -> str:
    country = str(value or "").strip().upper()
    aliases = {
        "EL": "GR",
        "GRC": "GR",
        "GREECE": "GR",
        "ΕΛΛΑΔΑ": "GR",
        "ΕΛΛΆΔΑ": "GR",
        "ΓΕΡΜΑΝΙΑ": "DE",
        "ΓΕΡΜΑΝΊΑ": "DE",
        "ΗΝΩΜΕΝΟ ΒΑΣΙΛΕΙΟ": "GB",
        "ΗΝΩΜΈΝΟ ΒΑΣΊΛΕΙΟ": "GB",
        "ΠΟΛΩΝΙΑ": "PL",
        "ΠΟΛΩΝΊΑ": "PL",
    }
    return aliases.get(country, country)


def _beneficiary_identifier(
    row: dict[str, str]
) -> tuple[CompanyIdentifier | None, str | None]:
    value = _optional(row.get("beneficiary_identifier"))
    if value is None:
        return None, "beneficiary identifier missing"
    country = _country(row.get("country"))
    scheme = str(row.get("identifier_scheme") or "").strip().upper()
    if not scheme and country == "GR":
        scheme = "EL_VAT"
    if scheme in {"VAT", "VAT NUMBER", "ΑΦΜ", "AFM", "EL_VAT"} and country == "GR":
        compact = re.sub(r"[\s.-]", "", value.upper())
        if compact.startswith("EL"):
            compact = compact[2:]
        if not re.fullmatch(r"\d{9}", compact):
            return None, "Greek VAT identifier must contain exactly 9 digits"
        return CompanyIdentifier("EL_VAT", "GR", compact), None
    if not scheme:
        scheme = "STATE_AID_BENEFICIARY_ID"
    return CompanyIdentifier(scheme=scheme, country=country, value=value), None


def _instrument(value: object) -> AidInstrument:
    text = re.sub(r"[\s_-]+", " ", str(value or "").strip().casefold())
    mappings = {
        AidInstrument.LOAN: {
            "loan",
            "loan/ repayable advances",
            "soft loan",
            "zero interest loan",
            "repayable advance",
            "repayable advances",
            "δάνειο",
            "δάνειο/επιστρεπτέες προκαταβολές",
        },
        AidInstrument.GRANT: {
            "grant",
            "direct grant",
            "direct grant/ interest rate subsidy",
            "subsidy",
            "επιχορήγηση",
            "επιχορήγηση/επιδότηση επιτοκίου",
            "άμεση επιχορήγηση",
        },
        AidInstrument.GUARANTEE: {
            "guarantee",
            "guarantee (where appropriate with a reference to the commission "
            "decision (10))",
            "loan guarantee",
            "εγγύηση",
            "εγγύηση (κατά περίπτωση, με παραπομπή στην απόφαση της επιτροπής(10))",
        },
        AidInstrument.TAX_ADVANTAGE: {
            "tax advantage",
            "tax advantage or tax exemption",
            "tax exemption",
            "tax reduction",
            "φορολογικό πλεονέκτημα",
            "φορολογικό πλεονέκτημα ή φορολογική απαλλαγή",
        },
        AidInstrument.OTHER: {
            "other",
            "provision of risk finance",
            "παροχή χρηματοδότησης επιχειρηματικού κινδύνου",
        },
    }
    for instrument, labels in mappings.items():
        normalized_labels = {
            re.sub(r"[\s_-]+", " ", label.strip().casefold()) for label in labels
        }
        if text in normalized_labels:
            return instrument
    return AidInstrument.UNKNOWN


def _measure_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _contains_phrase(value: str | None, phrase: str) -> bool:
    if value is None:
        return False
    normalized = re.sub(r"\s+", " ", value).strip().casefold()
    return phrase.casefold() in normalized


def _linkage(
    measure_reference: str | None,
    measure_name_en: str | None,
    measure_name_el: str | None,
    evidence_by_measure: dict[str, AidMeasureEvidence],
    source_url: str,
    source_sha256: str,
) -> tuple[RrfAidLinkage, str, AidMeasureEvidence | None]:
    configured_evidence = (
        evidence_by_measure.get(_measure_key(measure_reference))
        if measure_reference
        else None
    )
    if _contains_phrase(measure_name_en, "Recovery and Resilience"):
        if not measure_reference:
            return (
                RrfAidLinkage.UNRESOLVED,
                "English measure name explicitly references Recovery and Resilience "
                "but the source row has no aid measure reference",
                None,
            )
        if (
            configured_evidence is not None
            and configured_evidence.rrf_linkage is RrfAidLinkage.NOT_LINKED
        ):
            return (
                RrfAidLinkage.UNRESOLVED,
                "English measure name explicitly references Recovery and Resilience "
                "but conflicts with not_linked evidence from "
                f"{configured_evidence.source_name} ({configured_evidence.source_url})",
                None,
            )
        evidence = AidMeasureEvidence(
            aid_measure_reference=measure_reference,
            rrf_linkage=RrfAidLinkage.LINKED,
            source_name="State Aid Transparency CSV English measure name",
            source_url=source_url,
            source_sha256=source_sha256,
        )
        return (
            RrfAidLinkage.LINKED,
            f'English measure name explicitly references Recovery and Resilience: '
            f'"{measure_name_en}"',
            evidence,
        )
    greek_rrf_phrase = next(
        (
            phrase
            for phrase in GREEK_RRF_PHRASES
            if _contains_phrase(measure_name_el, phrase)
        ),
        None,
    )
    if greek_rrf_phrase is not None:
        if not measure_reference:
            return (
                RrfAidLinkage.UNRESOLVED,
                f"Greek measure name explicitly references {greek_rrf_phrase} "
                "but the source row has no aid measure reference",
                None,
            )
        if (
            configured_evidence is not None
            and configured_evidence.rrf_linkage is RrfAidLinkage.NOT_LINKED
        ):
            return (
                RrfAidLinkage.UNRESOLVED,
                f"Greek measure name explicitly references {greek_rrf_phrase} "
                "but conflicts with not_linked evidence from "
                f"{configured_evidence.source_name} ({configured_evidence.source_url})",
                None,
            )
        evidence = AidMeasureEvidence(
            aid_measure_reference=measure_reference,
            rrf_linkage=RrfAidLinkage.LINKED,
            source_name="State Aid Transparency CSV Greek measure name",
            source_url=source_url,
            source_sha256=source_sha256,
        )
        return (
            RrfAidLinkage.LINKED,
            f'Greek measure name explicitly references {greek_rrf_phrase}: '
            f'"{measure_name_el}"',
            evidence,
        )
    if not measure_reference:
        return (
            RrfAidLinkage.UNRESOLVED,
            "no aid measure reference in source row",
            None,
        )
    evidence = configured_evidence
    if evidence is not None:
        return (
            evidence.rrf_linkage,
            f"exact aid measure reference {measure_reference} supported by "
            f"{evidence.source_name} ({evidence.source_url})",
            evidence,
        )
    return (
        RrfAidLinkage.UNRESOLVED,
        f"aid measure reference {measure_reference} has no configured official evidence",
        None,
    )


def _parse_awards(
    payload: bytes, request: StateAidImportRequest
) -> tuple[
    tuple[StateAidAward, ...],
    int,
    tuple[StateAidImportGap, ...],
    tuple[StateAidImportGap, ...],
    tuple[StateAidDuplicateConflict, ...],
]:
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("State Aid export has no header row")
    reader.fieldnames = [_header(name) for name in reader.fieldnames]
    required = {"beneficiary_name", "aid_element", "aid_measure_reference"}
    missing = required.difference(reader.fieldnames)
    if missing:
        raise ValueError(
            "State Aid export is missing required columns: " + ", ".join(sorted(missing))
        )
    evidence_by_measure: dict[str, AidMeasureEvidence] = {}
    for evidence in request.measure_evidence:
        key = _measure_key(evidence.aid_measure_reference)
        existing = evidence_by_measure.get(key)
        if existing is not None and existing != evidence:
            raise ValueError(
                f"Conflicting evidence for aid measure {evidence.aid_measure_reference}"
            )
        evidence_by_measure[key] = evidence
    awards_by_id: dict[str, StateAidAward] = {}
    signatures_by_id: dict[str, set[StateAidAward]] = {}
    rows_by_id: dict[str, list[int]] = {}
    skipped_rows: list[StateAidImportGap] = []
    field_gaps: list[StateAidImportGap] = []
    source_row_count = 0
    source_sha256 = sha256(payload).hexdigest()
    for row_number, row in enumerate(reader, start=2):
        source_row_count += 1
        beneficiary_name = str(row.get("beneficiary_name") or "").strip()
        if not beneficiary_name:
            skipped_rows.append(
                StateAidImportGap(row_number, "beneficiary name missing; row skipped")
            )
            continue
        measure = _optional(row.get("aid_measure_reference"))
        measure_name_en = _optional(row.get("aid_measure_name_en"))
        measure_name_el = _optional(row.get("aid_measure_name_el"))
        linkage, linkage_evidence, measure_evidence = _linkage(
            measure,
            measure_name_en,
            measure_name_el,
            evidence_by_measure,
            request.source_url,
            source_sha256,
        )
        raw_instrument = _optional(row.get("aid_instrument"))
        instrument = _instrument(raw_instrument)
        excluded = instrument in {AidInstrument.LOAN, AidInstrument.UNKNOWN}
        identifier, identifier_issue = _beneficiary_identifier(row)
        raw_amount = _optional(row.get("aid_element"))
        amount = _amount_cents(raw_amount)
        if raw_amount is not None and amount is None:
            field_gaps.append(
                StateAidImportGap(row_number, "aid element could not be parsed")
            )
        award_id = _optional(row.get("award_id")) or sha256(
            repr(sorted(row.items())).encode("utf-8")
        ).hexdigest()[:16]
        exclusion_reason = None
        if instrument is AidInstrument.LOAN:
            exclusion_reason = "loan instrument excluded by policy"
        elif instrument is AidInstrument.UNKNOWN:
            exclusion_reason = "unknown aid instrument excluded pending policy"
        award = StateAidAward(
            award_id=award_id,
            beneficiary_name=beneficiary_name,
            beneficiary_identifier=identifier,
            beneficiary_identifier_issue=identifier_issue,
            aid_element_cents=amount,
            currency=_optional(row.get("currency")),
            instrument=instrument,
            raw_instrument=raw_instrument,
            granting_date=_optional(row.get("granting_date")),
            objective=_optional(row.get("objective")),
            granting_authority=_optional(row.get("granting_authority")),
            aid_measure_reference=measure,
            rrf_linkage=linkage,
            linkage_evidence=linkage_evidence,
            measure_evidence=measure_evidence,
            included_in_aid_aggregate=not excluded,
            exclusion_reason=exclusion_reason,
            source_row_number=row_number,
        )
        signature = replace(award, source_row_number=0)
        awards_by_id.setdefault(award_id, award)
        signatures_by_id.setdefault(award_id, set()).add(signature)
        rows_by_id.setdefault(award_id, []).append(row_number)
    conflicts = tuple(
        StateAidDuplicateConflict(
            award_id=award_id,
            source_row_numbers=tuple(rows_by_id[award_id]),
            reason="same official award ID has conflicting source facts",
        )
        for award_id in sorted(signatures_by_id)
        if len(signatures_by_id[award_id]) > 1
    )
    conflict_ids = {conflict.award_id for conflict in conflicts}
    awards = tuple(
        awards_by_id[award_id]
        for award_id in sorted(awards_by_id)
        if award_id not in conflict_ids
    )
    return (
        awards,
        source_row_count,
        tuple(skipped_rows),
        tuple(field_gaps),
        conflicts,
    )


def import_state_aid_csv(
    request: StateAidImportRequest, raw_directory: Path
) -> StateAidImportResult:
    """Preserve and parse one official public-search CSV export."""
    payload = request.csv_path.read_bytes()
    content_hash = sha256(payload).hexdigest()
    raw_directory.mkdir(parents=True, exist_ok=True)
    raw_path = raw_directory / f"state-aid-{content_hash}.csv"
    if raw_path.exists() and raw_path.read_bytes() != payload:
        raise ValueError(f"Stored export does not match content hash: {raw_path}")
    if not raw_path.exists():
        raw_path.write_bytes(payload)
    awards, source_row_count, skipped_rows, field_gaps, conflicts = _parse_awards(
        payload, request
    )
    return StateAidImportResult(
        source_url=request.source_url,
        imported_at=request.imported_at,
        awards=awards,
        source_row_count=source_row_count,
        imported_award_count=len(awards),
        skipped_rows=skipped_rows,
        field_gaps=field_gaps,
        duplicate_conflicts=conflicts,
        export_scope=request.export_scope,
        coverage=request.coverage,
        coverage_note=request.coverage.description,
        content_sha256=content_hash,
        byte_count=len(payload),
        raw_path=raw_path,
    )
