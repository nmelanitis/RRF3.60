"""Collect official Greece 2.0 decision PDFs with provenance."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, urlopen

from pypdf.errors import PyPdfError

from crawld.funding import (
    RrfProjectFundingReference,
    extract_rrf_project_funding_references,
)


@dataclass(frozen=True)
class PreservedGreece20Decision:
    """One preserved decision PDF with retrieval provenance."""

    source_url: str
    retrieved_at: datetime
    content_sha256: str
    byte_count: int
    raw_path: Path


@dataclass(frozen=True)
class CollectedGreece20Decision(PreservedGreece20Decision):
    """One preserved decision PDF and the facts extracted from it."""

    funding_references: tuple[RrfProjectFundingReference, ...]


@dataclass(frozen=True)
class FailedGreece20Decision:
    """One decision source that could not be retrieved or interpreted."""

    source_url: str
    error: str
    preserved_decision: PreservedGreece20Decision | None = None


class Greece20DecisionRetrievalError(Exception):
    """Raised when a decision source cannot be retrieved."""


class Greece20DecisionInterpretationError(Exception):
    """Raised when a preserved decision cannot be interpreted."""

    def __init__(
        self, preserved_decision: PreservedGreece20Decision, cause: PyPdfError
    ) -> None:
        self.preserved_decision = preserved_decision
        super().__init__(f"{type(cause).__name__}: {cause}")


@dataclass(frozen=True)
class Greece20DecisionBatchResult:
    """Successful and failed results from one deduplicated collection batch."""

    collected_decisions: tuple[CollectedGreece20Decision, ...]
    failed_decisions: tuple[FailedGreece20Decision, ...]

    @property
    def source_count(self) -> int:
        return self.collected_count + self.failed_count

    @property
    def collected_count(self) -> int:
        return len(self.collected_decisions)

    @property
    def failed_count(self) -> int:
        return len(self.failed_decisions)


def collect_greece20_decision_pdf(
    source_url: str, raw_directory: Path
) -> CollectedGreece20Decision:
    """Retrieve and preserve one decision PDF before extracting its identifiers."""
    retrieved_at = datetime.now(UTC)
    try:
        request = Request(
            source_url,
            headers={"User-Agent": "crawlD/0.1 (+public-data research)"},
        )
        with urlopen(request, timeout=60) as response:
            payload = response.read()
    except (HTTPException, OSError, ValueError) as error:
        raise Greece20DecisionRetrievalError(
            f"{type(error).__name__}: {error}"
        ) from error

    content_hash = sha256(payload).hexdigest()
    raw_directory.mkdir(parents=True, exist_ok=True)
    raw_path = raw_directory / f"{content_hash}.pdf"
    if raw_path.exists() and raw_path.read_bytes() != payload:
        raise ValueError(f"Stored decision does not match its content hash: {raw_path}")
    if not raw_path.exists():
        raw_path.write_bytes(payload)

    preserved = PreservedGreece20Decision(
        source_url=source_url,
        retrieved_at=retrieved_at,
        content_sha256=content_hash,
        byte_count=len(payload),
        raw_path=raw_path,
    )
    try:
        references = tuple(extract_rrf_project_funding_references(payload))
    except PyPdfError as error:
        raise Greece20DecisionInterpretationError(preserved, error) from error
    return CollectedGreece20Decision(
        source_url=preserved.source_url,
        retrieved_at=preserved.retrieved_at,
        content_sha256=preserved.content_sha256,
        byte_count=preserved.byte_count,
        raw_path=preserved.raw_path,
        funding_references=references,
    )


def collect_greece20_decision_pdfs(
    source_urls: Iterable[str], raw_directory: Path
) -> Greece20DecisionBatchResult:
    """Collect each unique Greece 2.0 decision URL without aborting the batch."""
    collected: list[CollectedGreece20Decision] = []
    failed: list[FailedGreece20Decision] = []
    for source_url in dict.fromkeys(source_urls):
        try:
            collected.append(collect_greece20_decision_pdf(source_url, raw_directory))
        except Greece20DecisionInterpretationError as error:
            failed.append(
                FailedGreece20Decision(
                    source_url=source_url,
                    error=str(error),
                    preserved_decision=error.preserved_decision,
                )
            )
        except Greece20DecisionRetrievalError as error:
            failed.append(
                FailedGreece20Decision(
                    source_url=source_url,
                    error=str(error),
                )
            )
    return Greece20DecisionBatchResult(
        collected_decisions=tuple(collected),
        failed_decisions=tuple(failed),
    )
