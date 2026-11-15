"""Resolve exact State Aid measure references against preserved official evidence."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import re
import ssl
from typing import Any, BinaryIO, Callable, Final
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import certifi
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from crawld.diavgeia import DiavgeiaSearchResult, collect_diavgeia_search
from crawld.state_aid import GREEK_RRF_PHRASES


COMMISSION_STATE_AID_DATASET_URL: Final = (
    "https://compcases-open-data-portal-files-prod.s3.eu-west-1.amazonaws.com/"
    "case-data-SA.json"
)
ENGLISH_RRF_PHRASE: Final = "Recovery and Resilience"
_HTTPS_CONTEXT: Final = ssl.create_default_context(cafile=certifi.where())


@dataclass(frozen=True)
class CommissionCaseExtraction:
    cases: dict[str, dict[str, Any]]
    dataset_sha256: str
    byte_count: int


@dataclass(frozen=True)
class StateAidEvidenceCollectionRequest:
    csv_path: Path
    config_path: Path
    output_root: Path
    commission_dataset_path: Path
    collected_at: datetime
    commission_dataset_url: str = COMMISSION_STATE_AID_DATASET_URL
    search_diavgeia: bool = True


@dataclass(frozen=True)
class StateAidEvidenceCollectionResult:
    output_dir: Path
    resolved_config_path: Path
    manifest_path: Path
    reference_count: int
    linked_count: int
    unresolved_count: int
    conflict_count: int
    dataset_reused: bool


class _ChunkedByteReader:
    def __init__(self, stream: BinaryIO, chunk_size: int) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self._stream = stream
        self._chunk_size = chunk_size
        self._buffer = b""
        self._position = 0
        self._digest = sha256()
        self.byte_count = 0

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()

    def read_byte(self) -> int | None:
        if self._position >= len(self._buffer):
            self._buffer = self._stream.read(self._chunk_size)
            self._position = 0
            if not self._buffer:
                return None
            self._digest.update(self._buffer)
            self.byte_count += len(self._buffer)
        value = self._buffer[self._position]
        self._position += 1
        return value


_JSON_WHITESPACE = frozenset(b" \t\r\n")


def _next_non_whitespace(reader: _ChunkedByteReader) -> int | None:
    value = reader.read_byte()
    while value in _JSON_WHITESPACE:
        value = reader.read_byte()
    return value


def _read_json_string(reader: _ChunkedByteReader) -> bytes:
    result = bytearray(b'"')
    escaped = False
    while True:
        value = reader.read_byte()
        if value is None:
            raise ValueError("Unexpected end of truncated JSON string")
        result.append(value)
        if escaped:
            escaped = False
        elif value == ord("\\"):
            escaped = True
        elif value == ord('"'):
            return bytes(result)


def _read_json_object(
    reader: _ChunkedByteReader, *, capture: bool
) -> bytes | None:
    result = bytearray(b"{") if capture else None
    expected_closers = [ord("}")]
    in_string = False
    escaped = False
    while expected_closers:
        value = reader.read_byte()
        if value is None:
            raise ValueError("Unexpected end of truncated JSON object")
        if result is not None:
            result.append(value)
        if in_string:
            if escaped:
                escaped = False
            elif value == ord("\\"):
                escaped = True
            elif value == ord('"'):
                in_string = False
            continue
        if value == ord('"'):
            in_string = True
        elif value == ord("{"):
            expected_closers.append(ord("}"))
        elif value == ord("["):
            expected_closers.append(ord("]"))
        elif value in {ord("}"), ord("]")}:
            expected = expected_closers.pop()
            if value != expected:
                raise ValueError("Mismatched delimiter in Commission JSON dataset")
    return bytes(result) if result is not None else None


def extract_commission_cases(
    stream: BinaryIO,
    wanted_references: set[str],
    *,
    chunk_size: int = 1024 * 1024,
) -> CommissionCaseExtraction:
    """Extract exact top-level case members while keeping memory use bounded."""
    reader = _ChunkedByteReader(stream, chunk_size)
    if _next_non_whitespace(reader) != ord("{"):
        raise ValueError("Commission State Aid dataset must be a JSON object")
    cases: dict[str, dict[str, Any]] = {}
    next_value = _next_non_whitespace(reader)
    if next_value == ord("}"):
        next_value = _next_non_whitespace(reader)
        if next_value is not None:
            raise ValueError("Unexpected data after Commission JSON object")
        return CommissionCaseExtraction(cases, reader.hexdigest, reader.byte_count)
    while True:
        if next_value != ord('"'):
            raise ValueError("Commission JSON object member must have a string key")
        key_literal = _read_json_string(reader)
        try:
            key = json.loads(key_literal)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid Commission JSON object key") from error
        if not isinstance(key, str):
            raise ValueError("Commission JSON object key must be text")
        if _next_non_whitespace(reader) != ord(":"):
            raise ValueError("Commission JSON object member is missing ':'")
        if _next_non_whitespace(reader) != ord("{"):
            raise ValueError(f"Commission case {key} must be a JSON object")
        payload = _read_json_object(reader, capture=key in wanted_references)
        if payload is not None:
            try:
                parsed = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError(f"Invalid Commission case JSON for {key}") from error
            if not isinstance(parsed, dict):
                raise ValueError(f"Commission case {key} must be a JSON object")
            cases[key] = parsed
        delimiter = _next_non_whitespace(reader)
        if delimiter == ord("}"):
            break
        if delimiter != ord(","):
            raise ValueError("Commission JSON object members must be comma-separated")
        next_value = _next_non_whitespace(reader)
        if next_value is None:
            raise ValueError("Unexpected end of truncated Commission JSON dataset")
    if _next_non_whitespace(reader) is not None:
        raise ValueError("Unexpected data after Commission JSON object")
    return CommissionCaseExtraction(cases, reader.hexdigest, reader.byte_count)


def _normalize_reference(value: object) -> str | None:
    normalized = re.sub(r"\s+", "", str(value or "")).upper()
    return normalized if re.fullmatch(r"SA\.\d+", normalized) else None


def _measure_references(csv_path: Path) -> tuple[str, ...]:
    text = csv_path.read_bytes().decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("State Aid export has no header row")
    aliases = {
        "aid measure reference",
        "reference of the aid measure",
        "sa.number",
        "αριθ. sa",
    }
    column = next(
        (
            name
            for name in reader.fieldnames
            if re.sub(r"\s+", " ", name.strip().casefold()) in aliases
        ),
        None,
    )
    if column is None:
        raise ValueError("State Aid export is missing the aid measure reference column")
    references: set[str] = set()
    invalid: set[str] = set()
    for row in reader:
        raw = str(row.get(column) or "").strip()
        if not raw:
            continue
        normalized = _normalize_reference(raw)
        if normalized is None:
            invalid.add(raw)
        else:
            references.add(normalized)
    if invalid:
        raise ValueError(
            "State Aid export contains invalid SA references: "
            + ", ".join(sorted(invalid)[:5])
        )
    return tuple(sorted(references))


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _rrf_phrase(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    for phrase in (ENGLISH_RRF_PHRASE, *GREEK_RRF_PHRASES):
        if phrase.casefold() in normalized:
            return phrase
    return None


def _contains_exact_reference(text: str, reference: str) -> bool:
    number = reference.removeprefix("SA.")
    pattern = rf"(?<![A-Z0-9])SA\s*\.\s*{re.escape(number)}(?![A-Z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _case_title_match(case: dict[str, Any]) -> tuple[str, str] | None:
    metadata = case.get("metadata")
    if not isinstance(metadata, dict):
        return None
    for field in ("caseTitle", "caseOriginalTitle"):
        for title in _strings(metadata.get(field)):
            phrase = _rrf_phrase(title)
            if phrase is not None:
                return field, phrase
    return None


def _first(value: object) -> str | None:
    values = _strings(value)
    return values[0] if values else None


def _attachment_records(case: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    decisions = case.get("decisions")
    if isinstance(decisions, list):
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            attachments = decision.get("decisionAttachments")
            if isinstance(attachments, list):
                records.extend(item for item in attachments if isinstance(item, dict))
    attachments = case.get("caseAttachments")
    if isinstance(attachments, list):
        records.extend(item for item in attachments if isinstance(item, dict))
    return records


def _official_document(record: dict[str, Any]) -> tuple[str, str | None] | None:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return None
    language = (
        _first(metadata.get("attachmentLanguage"))
        or _first(metadata.get("language"))
        or ""
    ).upper()
    if language not in {"EN", "EL"}:
        return None
    url = _first(metadata.get("attachmentLink"))
    if url is None or not _allowed_commission_url(url):
        return None
    return url, _first(metadata.get("metadataReference"))


def _allowed_commission_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme == "https" and (
        host == "ec.europa.eu" or host.endswith(".ec.europa.eu")
    )


def _allowed_diavgeia_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme == "https" and (
        host == "diavgeia.gov.gr" or host.endswith(".diavgeia.gov.gr")
    )


def _retrieve_document(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "crawlD/0.1 (+public-data research)"})
    with urlopen(request, timeout=90, context=_HTTPS_CONTEXT) as response:
        return response.read()


def _extract_pdf_text(payload: bytes) -> str:
    reader = PdfReader(BytesIO(payload))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _download_dataset(destination: Path, url: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    request = Request(url, headers={"User-Agent": "crawlD/0.1 (+public-data research)"})
    try:
        with urlopen(
            request,
            timeout=120,
            context=_HTTPS_CONTEXT,
        ) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _evidence(
    reference: str,
    source_name: str,
    source_url: str,
    source_sha256: str,
    source_act_id: str | None,
) -> dict[str, object]:
    return {
        "aid_measure_reference": reference,
        "rrf_linkage": "linked",
        "source_name": source_name,
        "source_url": source_url,
        "source_sha256": source_sha256,
        "source_act_id": source_act_id,
    }


def _configured_evidence(config: dict[str, Any]) -> dict[str, dict[str, object]]:
    raw_items = config.get("measure_evidence")
    if not isinstance(raw_items, list):
        raise ValueError("State Aid config measure_evidence must be a list")
    result: dict[str, dict[str, object]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("State Aid measure evidence entries must be objects")
        reference = _normalize_reference(item.get("aid_measure_reference"))
        if reference is None:
            raise ValueError("State Aid config contains an invalid SA reference")
        if reference in result and result[reference] != item:
            raise ValueError(f"Conflicting configured evidence for {reference}")
        result[reference] = item
    return result


def _document_match(
    reference: str,
    case: dict[str, Any],
    raw_documents: Path,
    retrieve_document: Callable[[str], bytes],
    extract_pdf_text: Callable[[bytes], str],
    gaps: list[str],
) -> tuple[dict[str, object], str] | None:
    for record in _attachment_records(case):
        document = _official_document(record)
        if document is None:
            continue
        url, act_id = document
        try:
            payload = retrieve_document(url)
            digest = sha256(payload).hexdigest()
            raw_documents.mkdir(parents=True, exist_ok=True)
            raw_path = raw_documents / f"commission-{digest}.pdf"
            if raw_path.exists() and raw_path.read_bytes() != payload:
                raise ValueError(f"Stored document does not match content hash: {raw_path}")
            if not raw_path.exists():
                raw_path.write_bytes(payload)
            phrase = _rrf_phrase(extract_pdf_text(payload))
        except (OSError, ValueError, PdfReadError) as error:
            gaps.append(f"{url}: {type(error).__name__}: {error}")
            continue
        if phrase is not None:
            return (
                _evidence(
                    reference,
                    "European Commission State Aid case attachment",
                    url,
                    digest,
                    act_id or reference,
                ),
                phrase,
            )
    return None


def _diavgeia_match(
    reference: str,
    raw_directory: Path,
    retrieve_document: Callable[[str], bytes],
    extract_pdf_text: Callable[[bytes], str],
    collect_diavgeia: Callable[[str, Path], DiavgeiaSearchResult],
    gaps: list[str],
) -> tuple[dict[str, object], str] | None:
    result = collect_diavgeia(reference, raw_directory / "searches")
    if result.error is not None:
        gaps.append(f"Diavgeia exact search: {result.error}")
        return None
    if result.results_complete is False:
        gaps.append(
            "Diavgeia exact search returned an incomplete result set"
        )
    for act in result.acts:
        subject = act.subject or ""
        phrase = _rrf_phrase(subject)
        if (
            phrase is not None
            and _contains_exact_reference(subject, reference)
            and result.content_sha256 is not None
        ):
            return (
                _evidence(
                    reference,
                    "Diavgeia exact-SA search result",
                    result.query_url,
                    result.content_sha256,
                    act.ada,
                ),
                phrase,
            )
        if act.document_url is None or not _allowed_diavgeia_url(act.document_url):
            continue
        try:
            payload = retrieve_document(act.document_url)
            digest = sha256(payload).hexdigest()
            documents = raw_directory / "documents"
            documents.mkdir(parents=True, exist_ok=True)
            raw_path = documents / f"diavgeia-{digest}.pdf"
            if raw_path.exists() and raw_path.read_bytes() != payload:
                raise ValueError(f"Stored document does not match content hash: {raw_path}")
            if not raw_path.exists():
                raw_path.write_bytes(payload)
            document_text = extract_pdf_text(payload)
            phrase = _rrf_phrase(document_text)
        except (OSError, ValueError, PdfReadError) as error:
            gaps.append(
                f"{act.document_url}: {type(error).__name__}: {error}"
            )
            continue
        if phrase is not None and _contains_exact_reference(document_text, reference):
            return (
                _evidence(
                    reference,
                    "Diavgeia administrative act",
                    act.document_url,
                    digest,
                    act.ada,
                ),
                phrase,
            )
    return None


def collect_state_aid_evidence(
    request: StateAidEvidenceCollectionRequest,
    *,
    retrieve_document: Callable[[str], bytes] = _retrieve_document,
    extract_pdf_text: Callable[[bytes], str] = _extract_pdf_text,
    collect_diavgeia: Callable[[str, Path], DiavgeiaSearchResult] = collect_diavgeia_search,
) -> StateAidEvidenceCollectionResult:
    """Create a new config enriched only by explicit official RRF evidence."""
    references = _measure_references(request.csv_path)
    config_payload = request.config_path.read_bytes()
    config = json.loads(config_payload.decode("utf-8"))
    if not isinstance(config, dict):
        raise ValueError("State Aid config must be a JSON object")
    configured = _configured_evidence(config)
    dataset_reused = request.commission_dataset_path.is_file()
    if not dataset_reused:
        _download_dataset(
            request.commission_dataset_path, request.commission_dataset_url
        )

    run_key = request.collected_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    input_digest = sha256(request.csv_path.read_bytes() + config_payload).hexdigest()[:8]
    output_dir = request.output_root / f"{run_key}-{input_digest}"
    request.output_root.mkdir(parents=True, exist_ok=True)
    if output_dir.exists():
        raise FileExistsError(f"State Aid evidence output already exists: {output_dir}")
    working_dir = request.output_root / f".{output_dir.name}.staging"
    working_dir.mkdir(parents=False, exist_ok=False)
    raw_cases = working_dir / "raw" / "commission-cases"
    raw_documents = working_dir / "raw" / "documents"
    raw_cases.mkdir(parents=True)

    with request.commission_dataset_path.open("rb") as stream:
        extraction = extract_commission_cases(stream, set(references))

    reference_entries: list[dict[str, object]] = []
    additions: list[dict[str, object]] = []
    linked_count = 0
    unresolved_count = 0
    conflict_count = 0
    conflicting_references: set[str] = set()
    for reference in references:
        case = extraction.cases.get(reference)
        case_path: Path | None = None
        gaps: list[str] = []
        match: tuple[dict[str, object], str] | None = None
        match_basis: str | None = None
        if case is not None:
            case_payload = _canonical_json(case)
            case_path = raw_cases / f"{reference}.json"
            case_path.write_bytes(case_payload)
            title_match = _case_title_match(case)
            if title_match is not None:
                field, phrase = title_match
                match = (
                    _evidence(
                        reference,
                        "European Commission State Aid open dataset case record",
                        request.commission_dataset_url,
                        sha256(case_payload).hexdigest(),
                        reference,
                    ),
                    phrase,
                )
                match_basis = f"{field} contains {phrase}"
            else:
                match = _document_match(
                    reference,
                    case,
                    raw_documents,
                    retrieve_document,
                    extract_pdf_text,
                    gaps,
                )
                if match is not None:
                    match_basis = f"official case attachment contains {match[1]}"
        else:
            gaps.append("exact SA reference absent from Commission dataset")

        if match is None and request.search_diavgeia:
            match = _diavgeia_match(
                reference,
                working_dir / "raw" / "diavgeia",
                retrieve_document,
                extract_pdf_text,
                collect_diavgeia,
                gaps,
            )
            if match is not None:
                match_basis = f"Diavgeia evidence contains {match[1]}"
        elif match is None:
            gaps.append("Diavgeia exact-SA fallback skipped")

        existing = configured.get(reference)
        if match is not None:
            candidate = match[0]
            if existing is not None and existing.get("rrf_linkage") == "not_linked":
                status = "conflict"
                conflict_count += 1
                conflicting_references.add(reference)
                gaps.append("automatic linked evidence conflicts with configured not_linked evidence")
            else:
                status = "linked"
                linked_count += 1
                if existing is None:
                    additions.append(candidate)
        elif existing is not None:
            status = f"existing_{existing.get('rrf_linkage', 'evidence')}"
        else:
            status = "unresolved"
            unresolved_count += 1
        reference_entries.append(
            {
                "aid_measure_reference": reference,
                "status": status,
                "commission_case_found": case is not None,
                "case_path": _relative(case_path, working_dir) if case_path else None,
                "match_basis": match_basis,
                "gaps": gaps,
            }
        )

    resolved = dict(config)
    original_evidence = config.get("measure_evidence")
    assert isinstance(original_evidence, list)
    non_conflicting_evidence = [
        item
        for item in original_evidence
        if isinstance(item, dict)
        and _normalize_reference(item.get("aid_measure_reference"))
        not in conflicting_references
    ]
    resolved["measure_evidence"] = [*non_conflicting_evidence, *additions]
    resolved_config_path = working_dir / "state-aid-config.resolved.json"
    resolved_config_path.write_bytes(_canonical_json(resolved))
    manifest = {
        "schema_version": 1,
        "collected_at": request.collected_at.astimezone(UTC).isoformat(),
        "inputs": {
            "state_aid_csv": str(request.csv_path.resolve()),
            "state_aid_config": str(request.config_path.resolve()),
        },
        "commission_dataset": {
            "source_url": request.commission_dataset_url,
            "cache_path": str(request.commission_dataset_path.resolve()),
            "reused": dataset_reused,
            "sha256": extraction.dataset_sha256,
            "byte_count": extraction.byte_count,
        },
        "search_diavgeia": request.search_diavgeia,
        "counts": {
            "references": len(references),
            "linked": linked_count,
            "unresolved": unresolved_count,
            "conflicts": conflict_count,
        },
        "references": reference_entries,
    }
    manifest_path = working_dir / "manifest.json"
    manifest_path.write_bytes(_canonical_json(manifest))
    os.replace(working_dir, output_dir)
    return StateAidEvidenceCollectionResult(
        output_dir=output_dir,
        resolved_config_path=output_dir / resolved_config_path.name,
        manifest_path=output_dir / manifest_path.name,
        reference_count=len(references),
        linked_count=linked_count,
        unresolved_count=unresolved_count,
        conflict_count=conflict_count,
        dataset_reused=dataset_reused,
    )
