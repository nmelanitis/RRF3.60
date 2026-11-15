"""Official KIMDIS contract search and contract-version interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import re
from time import sleep
from typing import Any, Final, Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen


KIMDIS_OPEN_DATA_URL: Final = (
    "https://cerpp.eprocurement.gov.gr/khmdhs-opendata/contract"
)
KIMDIS_RATE_LIMIT_WAIT_SECONDS: Final = 60


class ContractValueBasis(StrEnum):
    TOTAL_WITH_VAT = "total_cost_with_vat"
    TOTAL_WITHOUT_VAT = "total_cost_without_vat"


class ContractResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    MISSING_SUCCESSOR = "missing_successor"
    AMBIGUOUS_CHAIN = "ambiguous_chain"


class ContractMemberValueAllocation(StrEnum):
    SOLE_CONTRACTOR_FULL_VALUE = "sole_contractor_full_value"
    UNRESOLVED_CONSORTIUM_SHARES = "unresolved_consortium_shares"
    NO_IDENTIFIED_CONTRACTOR = "no_identified_contractor"


@dataclass(frozen=True)
class CompanyIdentifier:
    scheme: str
    country: str
    value: str


@dataclass(frozen=True)
class KimdisContractor:
    name: str | None
    identifier: CompanyIdentifier | None
    identifier_issue: str | None = None
    role: str = "contractor"


@dataclass(frozen=True)
class KimdisContract:
    reference_number: str
    previous_reference_number: str | None
    next_reference_number: str | None
    title: str | None
    signed_date: str | None
    procedure_code: str | None
    procedure_label: str | None
    cancelled: bool | None
    cancellation_status_issue: str | None
    cancellation_date: str | None
    cancellation_reason: str | None
    cancellation_ada: str | None
    decision_related_ada: str | None
    diavgeia_ada: str | None
    total_with_vat_cents: int | None
    total_without_vat_cents: int | None
    pde_codes: tuple[str, ...]
    ops_codes: tuple[str, ...]
    contractors: tuple[KimdisContractor, ...]
    member_value_allocation: ContractMemberValueAllocation


@dataclass(frozen=True)
class CurrentContract:
    contract_id: str
    current_reference_number: str | None
    current_value_cents: int | None
    value_basis: ContractValueBasis | None
    cancelled: bool | None
    version_count: int
    versions: tuple[KimdisContract, ...]
    resolution_status: ContractResolutionStatus
    history_complete: bool
    history_issue: str | None


@dataclass(frozen=True)
class KimdisContractSearch:
    pde_code: str
    date_from: date
    date_to: date
    page: int = 0

    def __post_init__(self) -> None:
        if self.date_to < self.date_from:
            raise ValueError("KIMDIS date_to must not precede date_from")
        if (self.date_to - self.date_from).days + 1 > 180:
            raise ValueError("A KIMDIS search window cannot exceed 180 days")
        if self.page < 0:
            raise ValueError("KIMDIS page cannot be negative")


@dataclass(frozen=True)
class KimdisContractPageResult:
    search: KimdisContractSearch
    source_url: str
    retrieved_at: datetime
    contracts: tuple[KimdisContract, ...]
    total_pages: int | None
    is_last_page: bool | None
    http_status: int | None
    byte_count: int | None
    content_sha256: str | None
    raw_path: Path | None
    error: str | None


@dataclass(frozen=True)
class KimdisContractCollectionResult:
    pde_code: str
    date_from: date
    date_to: date
    page_results: tuple[KimdisContractPageResult, ...]
    contracts: tuple[KimdisContract, ...]
    coverage_complete: bool


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _coded_value(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    return _optional_string(value.get("key")), _optional_string(value.get("value"))


def _money_cents(value: object) -> int | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        decimal_value = Decimal(text)
        if not decimal_value.is_finite():
            return None
        return int(
            (decimal_value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _normalise_pde(value: object) -> str | None:
    text = re.sub(r"\s+", "", str(value or "")).upper()
    text = text.replace("T", "Τ").replace("A", "Α")
    return text or None


def _normalise_ops(value: object) -> str | None:
    match = re.search(r"\d{6,10}", str(value or ""))
    return match.group(0) if match else None


def _identifier(
    member: dict[str, Any]
) -> tuple[CompanyIdentifier | None, str | None]:
    country_value = member.get("country")
    if isinstance(country_value, dict):
        country = str(country_value.get("key") or "").strip().upper()
    else:
        country = str(country_value or "").strip().upper()
    greek_vat = member.get("greekVatNumber") is True
    raw_value = member.get("vatNumber")
    if raw_value is None:
        return None, "contractor VAT identifier missing"
    text = re.sub(r"[\s.-]", "", str(raw_value).upper())
    if text.startswith("EL"):
        text = text[2:]
        country = "GR"
    if greek_vat and re.fullmatch(r"\d{9}", text):
        return CompanyIdentifier("EL_VAT", "GR", text), None
    if greek_vat:
        return None, "declared Greek VAT identifier must contain exactly 9 digits"
    if text:
        return CompanyIdentifier("VAT", country, text), None
    return None, "contractor VAT identifier missing"


def _optional_bool(value: object) -> tuple[bool | None, str | None]:
    if isinstance(value, bool):
        return value, None
    if value is None:
        return None, "cancellation status missing"
    return None, f"cancellation status is not Boolean: {value!r}"


def _contractors(value: object) -> tuple[KimdisContractor, ...]:
    if not isinstance(value, dict):
        return ()
    members = value.get("contractingMembersDataList", [])
    if not isinstance(members, list):
        return ()
    results: list[KimdisContractor] = []
    seen: set[tuple[str | None, str | None]] = set()
    for member in members:
        if not isinstance(member, dict):
            continue
        identifier, identifier_issue = _identifier(member)
        name = _optional_string(member.get("name"))
        key = (identifier.value if identifier else None, name)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            KimdisContractor(
                name=name,
                identifier=identifier,
                identifier_issue=identifier_issue,
            )
        )
    return tuple(
        sorted(
            results,
            key=lambda contractor: (
                contractor.identifier.scheme if contractor.identifier else "",
                contractor.identifier.country if contractor.identifier else "",
                contractor.identifier.value if contractor.identifier else "",
                contractor.name or "",
                contractor.identifier_issue or "",
            ),
        )
    )


def _funding_identifiers(value: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if isinstance(value, dict):
        details = [value]
    elif isinstance(value, list):
        details = [item for item in value if isinstance(item, dict)]
    else:
        details = []
    pde_codes: list[str] = []
    ops_codes: list[str] = []
    for detail in details:
        pde = _normalise_pde(detail.get("publicFundingRefNum"))
        ops = _normalise_ops(detail.get("publicFundingRefOps"))
        if pde and pde not in pde_codes:
            pde_codes.append(pde)
        if ops and ops not in ops_codes:
            ops_codes.append(ops)
    return tuple(pde_codes), tuple(ops_codes)


def parse_kimdis_contract(value: dict[str, Any]) -> KimdisContract | None:
    """Parse a record returned by the KIMDIS contract endpoint."""
    reference = _optional_string(value.get("referenceNumber"))
    if reference is None:
        return None
    pde_codes, ops_codes = _funding_identifiers(value.get("fundingDetails"))
    procedure_code, procedure_label = _coded_value(value.get("procedureType"))
    cancelled, cancellation_status_issue = _optional_bool(value.get("cancelled"))
    contractors = _contractors(value.get("contractingDataDetails"))
    identified_contractors = [
        contractor for contractor in contractors if contractor.identifier is not None
    ]
    if len(contractors) == 1 and len(identified_contractors) == 1:
        allocation = ContractMemberValueAllocation.SOLE_CONTRACTOR_FULL_VALUE
    elif identified_contractors:
        allocation = ContractMemberValueAllocation.UNRESOLVED_CONSORTIUM_SHARES
    else:
        allocation = ContractMemberValueAllocation.NO_IDENTIFIED_CONTRACTOR
    return KimdisContract(
        reference_number=reference,
        previous_reference_number=_optional_string(value.get("prevReferenceNo")),
        next_reference_number=_optional_string(value.get("nextRefNo")),
        title=_optional_string(value.get("title")),
        signed_date=_optional_string(value.get("contractSignedDate")),
        procedure_code=procedure_code,
        procedure_label=procedure_label,
        cancelled=cancelled,
        cancellation_status_issue=cancellation_status_issue,
        cancellation_date=_optional_string(value.get("cancellationDate")),
        cancellation_reason=_optional_string(value.get("cancellationReason")),
        cancellation_ada=_optional_string(value.get("cancellationADA")),
        decision_related_ada=_optional_string(value.get("decisionRelatedAda")),
        diavgeia_ada=_optional_string(value.get("diavgeiaADA")),
        total_with_vat_cents=_money_cents(value.get("totalCostWithVAT")),
        total_without_vat_cents=_money_cents(value.get("totalCostWithoutVAT")),
        pde_codes=pde_codes,
        ops_codes=ops_codes,
        contractors=contractors,
        member_value_allocation=allocation,
    )


def parse_kimdis_contracts(values: Iterable[object]) -> tuple[KimdisContract, ...]:
    records: list[KimdisContract] = []
    seen: set[KimdisContract] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        contract = parse_kimdis_contract(value)
        if contract is not None and contract not in seen:
            seen.add(contract)
            records.append(contract)
    return tuple(records)


def _root_reference(
    record: KimdisContract, by_reference: dict[str, KimdisContract]
) -> str:
    current = record
    seen = {record.reference_number}
    while current.previous_reference_number:
        previous = current.previous_reference_number
        if previous in seen:
            return min(seen)
        seen.add(previous)
        if previous not in by_reference:
            return previous
        current = by_reference[previous]
    return current.reference_number


def select_current_contract_versions(
    records: Iterable[KimdisContract],
) -> tuple[CurrentContract, ...]:
    """Collapse explicit amendment chains into one current contract fact."""
    facts_by_reference: dict[str, set[KimdisContract]] = {}
    for record in records:
        facts_by_reference.setdefault(record.reference_number, set()).add(record)
    by_reference = {
        reference: sorted(facts, key=repr)[0]
        for reference, facts in facts_by_reference.items()
    }
    conflicting_references = {
        reference for reference, facts in facts_by_reference.items() if len(facts) > 1
    }
    groups: dict[str, list[KimdisContract]] = {}
    for record in by_reference.values():
        groups.setdefault(_root_reference(record, by_reference), []).append(record)

    results: list[CurrentContract] = []
    for root, versions in sorted(groups.items()):
        group_conflicts = sorted(
            version.reference_number
            for version in versions
            if version.reference_number in conflicting_references
        )
        missing_predecessors = sorted(
            {
                version.previous_reference_number
                for version in versions
                if version.previous_reference_number
                and version.previous_reference_number not in by_reference
            }
        )
        references_with_successor = {
            version.previous_reference_number
            for version in versions
            if version.previous_reference_number
        }
        leaves = [
            version
            for version in versions
            if version.reference_number not in references_with_successor
        ]
        if group_conflicts or len(leaves) != 1:
            current = None
            resolution_status = ContractResolutionStatus.AMBIGUOUS_CHAIN
        else:
            candidate = leaves[0]
            if (
                candidate.next_reference_number
                and candidate.next_reference_number not in by_reference
            ):
                current = None
                resolution_status = ContractResolutionStatus.MISSING_SUCCESSOR
            else:
                current = candidate
                resolution_status = ContractResolutionStatus.RESOLVED
        if current is None:
            current_value = None
            value_basis = None
        elif current.total_with_vat_cents is not None:
            current_value = current.total_with_vat_cents
            value_basis = ContractValueBasis.TOTAL_WITH_VAT
        elif current.total_without_vat_cents is not None:
            current_value = current.total_without_vat_cents
            value_basis = ContractValueBasis.TOTAL_WITHOUT_VAT
        else:
            current_value = None
            value_basis = None
        results.append(
            CurrentContract(
                contract_id=root,
                current_reference_number=(current.reference_number if current else None),
                current_value_cents=current_value,
                value_basis=value_basis,
                cancelled=current.cancelled if current else None,
                version_count=sum(
                    len(facts_by_reference[version.reference_number])
                    for version in versions
                ),
                versions=tuple(
                    sorted(
                        (
                            fact
                            for version in versions
                            for fact in facts_by_reference[version.reference_number]
                        ),
                        key=lambda item: (item.reference_number, repr(item)),
                    )
                ),
                resolution_status=resolution_status,
                history_complete=not missing_predecessors and (
                    resolution_status is ContractResolutionStatus.RESOLVED
                ),
                history_issue=(
                    "conflicting facts for references: " + ", ".join(group_conflicts)
                    if group_conflicts
                    else (
                        "missing predecessor versions: " + ", ".join(missing_predecessors)
                        if missing_predecessors
                        else (
                            f"current chain status: {resolution_status.value}"
                            if resolution_status is not ContractResolutionStatus.RESOLVED
                            else None
                        )
                    )
                ),
            )
        )
    return tuple(results)


def split_kimdis_search_windows(
    pde_code: str, date_from: date, date_to: date
) -> tuple[KimdisContractSearch, ...]:
    """Split a registration-date range into adjacent official 180-day windows."""
    if date_to < date_from:
        raise ValueError("KIMDIS date_to must not precede date_from")
    windows: list[KimdisContractSearch] = []
    window_start = date_from
    while window_start <= date_to:
        window_end = min(window_start + timedelta(days=179), date_to)
        windows.append(
            KimdisContractSearch(
                pde_code=pde_code,
                date_from=window_start,
                date_to=window_end,
                page=0,
            )
        )
        window_start = window_end + timedelta(days=1)
    return tuple(windows)


def parse_kimdis_contract_page(
    payload: bytes,
) -> tuple[tuple[KimdisContract, ...], int | None]:
    """Parse the contract records and declared page count from a raw response."""
    value = json.loads(payload.decode("utf-8"))
    if isinstance(value, list):
        return parse_kimdis_contracts(value), None
    if not isinstance(value, dict):
        raise ValueError("KIMDIS contract response must be an object or list")
    content = value.get("content", value.get("contracts", []))
    if not isinstance(content, list):
        raise ValueError("KIMDIS contract response content must be a list")
    total_raw = value.get("totalPages")
    try:
        total_pages = int(total_raw) if total_raw is not None else None
    except (TypeError, ValueError):
        total_pages = None
    return parse_kimdis_contracts(content), total_pages


def _is_verified_empty_response(payload: bytes, http_status: int) -> bool:
    if http_status != 404:
        return False
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(value, dict)
        and value.get("status") == 404
        and value.get("message") == "No contracts found for the given criteria"
    )


def _preserve_response(payload: bytes, raw_directory: Path) -> tuple[str, Path]:
    content_hash = sha256(payload).hexdigest()
    raw_directory.mkdir(parents=True, exist_ok=True)
    raw_path = raw_directory / f"kimdis-contracts-{content_hash}.json"
    if raw_path.exists() and raw_path.read_bytes() != payload:
        raise ValueError(f"Stored response does not match content hash: {raw_path}")
    if not raw_path.exists():
        raw_path.write_bytes(payload)
    return content_hash, raw_path


def collect_kimdis_contract_page(
    search: KimdisContractSearch, raw_directory: Path
) -> KimdisContractPageResult:
    """POST one explicitly bounded KIMDIS contract search and preserve its response."""
    source_url = f"{KIMDIS_OPEN_DATA_URL}?page={search.page}"
    body = json.dumps(
        {
            "publicFundingRefNum": search.pde_code,
            "dateFrom": search.date_from.isoformat(),
            "dateTo": search.date_to.isoformat(),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    while True:
        retrieved_at = datetime.now(UTC)
        response_error: str | None = None
        try:
            request = Request(
                source_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "crawlD/0.1 (+public-data research)",
                },
                method="POST",
            )
            with urlopen(request, timeout=60) as response:
                payload = response.read()
                http_status = int(getattr(response, "status", None) or 200)
        except HTTPError as error:
            payload = error.read() or b""
            http_status = error.code
            response_error = f"{type(error).__name__}: {error}"
            if http_status == 429:
                if payload:
                    _preserve_response(payload, raw_directory)
                sleep(KIMDIS_RATE_LIMIT_WAIT_SECONDS)
                continue
        except (OSError, ValueError) as error:
            return KimdisContractPageResult(
                search=search,
                source_url=source_url,
                retrieved_at=retrieved_at,
                contracts=(),
                total_pages=None,
                is_last_page=None,
                http_status=None,
                byte_count=None,
                content_sha256=None,
                raw_path=None,
                error=f"{type(error).__name__}: {error}",
            )
        break

    if response_error is not None and not payload:
        return KimdisContractPageResult(
            search=search,
            source_url=source_url,
            retrieved_at=retrieved_at,
            contracts=(),
            total_pages=None,
            is_last_page=None,
            http_status=http_status,
            byte_count=0,
            content_sha256=None,
            raw_path=None,
            error=response_error,
        )

    content_hash, raw_path = _preserve_response(payload, raw_directory)

    if response_error is not None:
        if search.page == 0 and _is_verified_empty_response(payload, http_status):
            return KimdisContractPageResult(
                search=search,
                source_url=source_url,
                retrieved_at=retrieved_at,
                contracts=(),
                total_pages=0,
                is_last_page=True,
                http_status=http_status,
                byte_count=len(payload),
                content_sha256=content_hash,
                raw_path=raw_path,
                error=None,
            )
        return KimdisContractPageResult(
            search=search,
            source_url=source_url,
            retrieved_at=retrieved_at,
            contracts=(),
            total_pages=None,
            is_last_page=None,
            http_status=http_status,
            byte_count=len(payload),
            content_sha256=content_hash,
            raw_path=raw_path,
            error=response_error,
        )

    try:
        contracts, total_pages = parse_kimdis_contract_page(payload)
        return KimdisContractPageResult(
            search=search,
            source_url=source_url,
            retrieved_at=retrieved_at,
            contracts=contracts,
            total_pages=total_pages,
            is_last_page=(search.page + 1 >= total_pages)
            if total_pages is not None
            else None,
            http_status=http_status,
            byte_count=len(payload),
            content_sha256=content_hash,
            raw_path=raw_path,
            error=None,
        )
    except (ValueError, json.JSONDecodeError) as error:
        return KimdisContractPageResult(
            search=search,
            source_url=source_url,
            retrieved_at=retrieved_at,
            contracts=(),
            total_pages=None,
            is_last_page=None,
            http_status=http_status,
            byte_count=len(payload),
            content_sha256=content_hash,
            raw_path=raw_path,
            error=f"{type(error).__name__}: {error}",
        )


def collect_kimdis_contracts(
    pde_code: str,
    date_from: date,
    date_to: date,
    raw_directory: Path,
) -> KimdisContractCollectionResult:
    """Collect every page across safe registration-date windows and deduplicate."""
    page_results: list[KimdisContractPageResult] = []
    contracts: list[KimdisContract] = []
    seen: set[KimdisContract] = set()
    coverage_complete = True
    for window in split_kimdis_search_windows(pde_code, date_from, date_to):
        page = 0
        while True:
            search = KimdisContractSearch(
                pde_code=window.pde_code,
                date_from=window.date_from,
                date_to=window.date_to,
                page=page,
            )
            result = collect_kimdis_contract_page(search, raw_directory)
            page_results.append(result)
            for contract in result.contracts:
                if contract not in seen:
                    seen.add(contract)
                    contracts.append(contract)
            if result.error is not None:
                coverage_complete = False
                break
            if result.is_last_page is True:
                break
            if result.total_pages is None:
                coverage_complete = False
                break
            page += 1
    return KimdisContractCollectionResult(
        pde_code=pde_code,
        date_from=date_from,
        date_to=date_to,
        page_results=tuple(page_results),
        contracts=tuple(contracts),
        coverage_complete=coverage_complete,
    )
