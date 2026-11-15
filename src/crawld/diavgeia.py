"""Official Diavgeia Open Data adapter with preserved query provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DIAVGEIA_OPEN_DATA_URL: Final = "https://diavgeia.gov.gr/opendata"


class DiavgeiaPublicationStatus(StrEnum):
    """Publication lifecycle state; this is not the act's legal effect."""

    PUBLISHED = "published"
    REVOKED = "revoked"
    PENDING_REVOCATION = "pending_revocation"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiavgeiaAdministrativeAct:
    ada: str
    protocol_number: str | None
    subject: str | None
    issue_date_ms: int | None
    organization_id: str | None
    decision_type_id: str | None
    publication_status: DiavgeiaPublicationStatus
    version_id: str | None
    document_url: str | None


@dataclass(frozen=True)
class DiavgeiaActVersion:
    version_id: str
    publication_status: DiavgeiaPublicationStatus
    observed_at_ms: int | None


@dataclass(frozen=True)
class DiavgeiaSearchResult:
    query_term: str
    query_url: str
    retrieved_at: datetime
    acts: tuple[DiavgeiaAdministrativeAct, ...]
    total_reported: int | None
    results_complete: bool | None
    http_status: int | None
    byte_count: int | None
    content_sha256: str | None
    raw_path: Path | None
    error: str | None


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _publication_status(value: object) -> DiavgeiaPublicationStatus:
    normalized = str(value or "").casefold()
    try:
        return DiavgeiaPublicationStatus(normalized)
    except ValueError:
        return DiavgeiaPublicationStatus.UNKNOWN


def parse_diavgeia_act(value: dict[str, Any]) -> DiavgeiaAdministrativeAct:
    """Parse one administrative act without interpreting its legal effect."""
    return DiavgeiaAdministrativeAct(
        ada=str(value.get("ada") or ""),
        protocol_number=_optional_string(value.get("protocolNumber")),
        subject=_optional_string(value.get("subject")),
        issue_date_ms=_optional_int(value.get("issueDate")),
        organization_id=_optional_string(value.get("organizationId")),
        decision_type_id=_optional_string(value.get("decisionTypeId")),
        publication_status=_publication_status(value.get("status")),
        version_id=_optional_string(value.get("versionId")),
        document_url=_optional_string(
            value.get("documentUrl") or value.get("documentUrlOriginal")
        ),
    )


def parse_diavgeia_version_log(value: object) -> tuple[DiavgeiaActVersion, ...]:
    """Parse publication versions while retaining their source lifecycle status."""
    if isinstance(value, dict):
        raw_versions = value.get("versions", [])
    else:
        raw_versions = value
    if not isinstance(raw_versions, list):
        return ()
    versions: list[DiavgeiaActVersion] = []
    for raw in raw_versions:
        if not isinstance(raw, dict) or raw.get("versionId") is None:
            continue
        versions.append(
            DiavgeiaActVersion(
                version_id=str(raw["versionId"]),
                publication_status=_publication_status(raw.get("status")),
                observed_at_ms=_optional_int(
                    raw.get("timestamp") or raw.get("createdAt")
                ),
            )
        )
    return tuple(versions)


def _search_url(query_term: str) -> str:
    parameters = urlencode(
        {"term": query_term, "status": "all", "size": 500},
        encoding="utf-8",
    )
    return f"{DIAVGEIA_OPEN_DATA_URL}/search?{parameters}"


def _parse_search_payload(
    payload: bytes,
) -> tuple[tuple[DiavgeiaAdministrativeAct, ...], int | None]:
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Diavgeia search response must be a JSON object")
    raw_acts = value.get("decisions", [])
    if not isinstance(raw_acts, list):
        raise ValueError("Diavgeia search response decisions must be a list")
    acts = tuple(
        parse_diavgeia_act(raw)
        for raw in raw_acts
        if isinstance(raw, dict) and raw.get("ada")
    )
    info = value.get("info")
    total = _optional_int(info.get("total")) if isinstance(info, dict) else None
    return acts, total


def collect_diavgeia_search(
    query_term: str, raw_directory: Path
) -> DiavgeiaSearchResult:
    """Run and preserve one official free-text search.

    A match is search provenance only. Callers must establish project linkage
    from independent identifier or act evidence.
    """
    retrieved_at = datetime.now(UTC)
    query_url = _search_url(query_term)
    content_hash: str | None = None
    raw_path: Path | None = None
    try:
        request = Request(
            query_url,
            headers={"User-Agent": "crawlD/0.1 (+public-data research)"},
        )
        with urlopen(request, timeout=60) as response:
            payload = response.read()
            http_status = int(getattr(response, "status", None) or 200)
    except (OSError, ValueError) as error:
        return DiavgeiaSearchResult(
            query_term=query_term,
            query_url=query_url,
            retrieved_at=retrieved_at,
            acts=(),
            total_reported=None,
            results_complete=None,
            http_status=None,
            byte_count=None,
            content_sha256=None,
            raw_path=None,
            error=f"{type(error).__name__}: {error}",
        )

    content_hash = sha256(payload).hexdigest()
    raw_directory.mkdir(parents=True, exist_ok=True)
    raw_path = raw_directory / f"diavgeia-search-{content_hash}.json"
    if raw_path.exists() and raw_path.read_bytes() != payload:
        raise ValueError(f"Stored response does not match content hash: {raw_path}")
    if not raw_path.exists():
        raw_path.write_bytes(payload)

    try:
        acts, total = _parse_search_payload(payload)
        complete = total is not None and total <= len(acts)
        return DiavgeiaSearchResult(
            query_term=query_term,
            query_url=query_url,
            retrieved_at=retrieved_at,
            acts=acts,
            total_reported=total,
            results_complete=complete if total is not None else None,
            http_status=http_status,
            byte_count=len(payload),
            content_sha256=content_hash,
            raw_path=raw_path,
            error=None,
        )
    except (ValueError, json.JSONDecodeError) as error:
        return DiavgeiaSearchResult(
            query_term=query_term,
            query_url=query_url,
            retrieved_at=retrieved_at,
            acts=(),
            total_reported=None,
            results_complete=None,
            http_status=http_status,
            byte_count=len(payload),
            content_sha256=content_hash,
            raw_path=raw_path,
            error=f"{type(error).__name__}: {error}",
        )
