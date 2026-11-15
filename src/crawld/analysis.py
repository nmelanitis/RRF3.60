"""Build immutable derived analysis artifacts from a completed source snapshot."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from pypdf.errors import PyPdfError
import sqlite3
from typing import Iterable
import unicodedata
from urllib.parse import urlparse
from uuid import uuid4

from crawld.anomalies import (
    AnomalyConfig,
    AnomalyFlag,
    AssessmentOutcome,
    ContractValueIncreaseRuleConfig,
    DirectAwardRuleConfig,
    RepeatedContractRuleConfig,
    RepeatedStateAidRuleConfig,
    RedFlagAssessment,
    assess_direct_awards,
    assess_contract_value_increases,
    assess_repeated_contracts,
    assess_repeated_state_aid,
    generate_anomaly_flags,
)
from crawld.diavgeia import DiavgeiaSearchResult
from crawld.history import classify_greece20_decision, extract_greece20_decision_text
from crawld.kimdis import (
    ContractMemberValueAllocation,
    CurrentContract,
    KimdisContractCollectionResult,
    parse_kimdis_contract_page,
    select_current_contract_versions,
    split_kimdis_search_windows,
)
from crawld.profiles import CompanyFundingProfile, CompanyProfilesResult, build_company_profiles
from crawld.state_aid import AidInstrument, StateAidAward, StateAidImportResult


@dataclass(frozen=True)
class AnalysisArtifactRequest:
    source_snapshot: Path
    output_root: Path
    generated_at: datetime
    kimdis_collections: tuple[KimdisContractCollectionResult, ...]
    state_aid_imports: tuple[StateAidImportResult, ...]
    diavgeia_searches: tuple[DiavgeiaSearchResult, ...]
    anomaly_config: AnomalyConfig


@dataclass(frozen=True)
class AnalysisArtifactResult:
    analysis_id: str
    analysis_dir: Path
    status: str
    profile_count: int
    anomaly_flag_count: int
    source_gaps: int


@dataclass(frozen=True)
class CoverageReportRow:
    source_name: str
    query_description: str
    source_url: str | None
    complete: bool | None
    error: str | None
    coverage_note: str | None


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError(f"Required source snapshot database is missing: {path}")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _preserve_raw(
    source: Path | None,
    raw_directory: Path,
    expected_sha256: str | None,
    expected_byte_count: int | None,
) -> str | None:
    if source is None:
        if expected_sha256 is not None or expected_byte_count is not None:
            raise ValueError(
                "Raw input has declared SHA-256/byte count but no raw path"
            )
        return None
    payload = source.read_bytes()
    content_hash = sha256(payload).hexdigest()
    if expected_sha256 is None or expected_byte_count is None:
        raise ValueError(f"Raw input lacks declared SHA-256 or byte count: {source}")
    if content_hash != expected_sha256:
        raise ValueError(
            f"Raw input SHA-256 mismatch for {source}: "
            f"declared {expected_sha256}, actual {content_hash}"
        )
    if len(payload) != expected_byte_count:
        raise ValueError(
            f"Raw input byte-count mismatch for {source}: "
            f"declared {expected_byte_count}, actual {len(payload)}"
        )
    suffix = source.suffix or ".bin"
    destination = raw_directory / f"{content_hash}{suffix}"
    if destination.exists() and destination.read_bytes() != payload:
        raise ValueError(f"Preserved raw file hash collision: {destination}")
    if not destination.exists():
        destination.write_bytes(payload)
    return str(destination.relative_to(raw_directory.parent))


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE analysis_runs (
            analysis_id TEXT PRIMARY KEY,
            source_snapshot_id TEXT NOT NULL,
            source_snapshot_manifest_sha256 TEXT NOT NULL,
            source_snapshot_database_sha256 TEXT NOT NULL,
            source_cutoff TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            source_gap_count INTEGER NOT NULL
        );
        CREATE TABLE source_coverage (
            coverage_id INTEGER PRIMARY KEY,
            source_name TEXT NOT NULL,
            query_description TEXT NOT NULL,
            source_url TEXT,
            retrieved_at TEXT,
            complete INTEGER,
            error TEXT,
            raw_path TEXT,
            content_sha256 TEXT,
            coverage_note TEXT
        );
        CREATE TABLE greece20_decision_interpretations (
            decision_id INTEGER PRIMARY KEY,
            source_url TEXT NOT NULL,
            role TEXT NOT NULL,
            unresolved_reason TEXT,
            role_evidence_text TEXT,
            role_evidence_page INTEGER,
            role_evidence_start INTEGER,
            role_evidence_end INTEGER
        );
        CREATE TABLE greece20_decision_effects (
            decision_id INTEGER NOT NULL,
            effect TEXT NOT NULL,
            evidence_text TEXT NOT NULL,
            evidence_page INTEGER NOT NULL,
            evidence_start INTEGER NOT NULL,
            evidence_end INTEGER NOT NULL
        );
        CREATE TABLE decision_cancellation_amounts (
            decision_id INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL,
            currency TEXT NOT NULL,
            funding_stream TEXT NOT NULL,
            evidence_text TEXT NOT NULL,
            evidence_page INTEGER NOT NULL,
            evidence_start INTEGER NOT NULL,
            evidence_end INTEGER NOT NULL
        );
        CREATE TABLE project_decision_links (
            ops_code TEXT NOT NULL,
            pde_code TEXT,
            decision_id INTEGER,
            decision_url TEXT NOT NULL,
            link_basis TEXT NOT NULL
        );
        CREATE TABLE diavgeia_acts (
            query_term TEXT NOT NULL,
            ada TEXT NOT NULL,
            subject TEXT,
            publication_status TEXT NOT NULL,
            version_id TEXT,
            document_url TEXT
        );
        CREATE TABLE current_contracts (
            contract_id TEXT PRIMARY KEY,
            current_reference_number TEXT,
            current_value_cents INTEGER,
            value_basis TEXT,
            cancelled INTEGER,
            resolution_status TEXT NOT NULL,
            history_complete INTEGER NOT NULL,
            history_issue TEXT,
            version_count INTEGER NOT NULL
        );
        CREATE TABLE kimdis_pages (
            pde_code TEXT NOT NULL,
            date_from TEXT NOT NULL,
            date_to TEXT NOT NULL,
            page INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            total_pages INTEGER,
            is_last_page INTEGER,
            http_status INTEGER,
            byte_count INTEGER,
            content_sha256 TEXT,
            raw_path TEXT,
            error TEXT
        );
        CREATE TABLE kimdis_contract_versions (
            contract_id TEXT NOT NULL,
            reference_number TEXT NOT NULL,
            previous_reference_number TEXT,
            next_reference_number TEXT,
            procedure_code TEXT,
            procedure_label TEXT
        );
        CREATE TABLE kimdis_contract_record_sources (
            reference_number TEXT NOT NULL,
            page_content_sha256 TEXT,
            source_url TEXT NOT NULL,
            raw_path TEXT
        );
        CREATE TABLE state_aid_measure_evidence (
            aid_measure_reference TEXT NOT NULL,
            rrf_linkage TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_act_id TEXT,
            PRIMARY KEY(aid_measure_reference, rrf_linkage, source_sha256)
        );
        CREATE TABLE state_aid_awards (
            award_id TEXT NOT NULL,
            beneficiary_name TEXT NOT NULL,
            identifier_scheme TEXT,
            identifier_country TEXT,
            identifier_value TEXT,
            beneficiary_identifier_issue TEXT,
            aid_element_cents INTEGER,
            currency TEXT,
            instrument TEXT NOT NULL,
            raw_instrument TEXT,
            granting_date TEXT,
            objective TEXT,
            granting_authority TEXT,
            aid_measure_reference TEXT,
            rrf_linkage TEXT NOT NULL,
            included_in_aggregate INTEGER NOT NULL,
            exclusion_reason TEXT,
            source_url TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            linkage_evidence TEXT NOT NULL,
            measure_evidence_sha256 TEXT
        );
        CREATE TABLE state_aid_import_gaps (
            source_url TEXT NOT NULL,
            gap_kind TEXT NOT NULL,
            source_row_number INTEGER,
            award_id TEXT,
            reason TEXT NOT NULL
        );
        CREATE TABLE company_profiles (
            identifier_scheme TEXT NOT NULL,
            identifier_country TEXT NOT NULL,
            identifier_value TEXT NOT NULL,
            names TEXT NOT NULL,
            contract_count INTEGER NOT NULL,
            active_contract_count INTEGER NOT NULL,
            cancelled_contract_count INTEGER NOT NULL,
            current_contract_value_cents INTEGER NOT NULL,
            unresolved_contract_value_count INTEGER NOT NULL,
            aid_award_count INTEGER NOT NULL,
            aid_amount_eur_cents INTEGER NOT NULL,
            rrf_linked_aid_count INTEGER NOT NULL,
            rrf_linked_aid_amount_eur_cents INTEGER NOT NULL,
            non_rrf_aid_count INTEGER NOT NULL,
            non_rrf_aid_amount_eur_cents INTEGER NOT NULL,
            unresolved_aid_count INTEGER NOT NULL,
            unresolved_aid_amount_eur_cents INTEGER NOT NULL,
            non_eur_aid_count INTEGER NOT NULL,
            missing_aid_amount_or_currency_count INTEGER NOT NULL,
            excluded_loan_count INTEGER NOT NULL,
            excluded_loan_amount_eur_cents INTEGER NOT NULL,
            excluded_other_aid_count INTEGER NOT NULL,
            excluded_other_aid_amount_eur_cents INTEGER NOT NULL,
            contract_ids TEXT NOT NULL,
            aid_award_ids TEXT NOT NULL,
            current_value_contract_ids TEXT NOT NULL,
            rrf_linked_aid_award_ids TEXT NOT NULL,
            rrf_linked_eur_amount_award_ids TEXT NOT NULL,
            PRIMARY KEY(identifier_scheme, identifier_country, identifier_value)
        );
        CREATE TABLE anomaly_flags (
            rule_id TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            route TEXT NOT NULL,
            identifier_scheme TEXT NOT NULL,
            identifier_country TEXT NOT NULL,
            identifier_value TEXT NOT NULL,
            observation_start TEXT NOT NULL,
            observation_end TEXT NOT NULL,
            observation_scope TEXT NOT NULL,
            numerator_cents INTEGER NOT NULL,
            denominator_cents INTEGER NOT NULL,
            observed_share TEXT NOT NULL,
            threshold TEXT NOT NULL,
            evidence_record_ids TEXT NOT NULL,
            explanation TEXT NOT NULL
        );
        CREATE TABLE red_flag_assessments (
            rule_id TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            outcome TEXT NOT NULL,
            route TEXT NOT NULL,
            subject_type TEXT NOT NULL,
            subject_identifier TEXT NOT NULL,
            observation_start TEXT NOT NULL,
            observation_end TEXT NOT NULL,
            observation_scope TEXT NOT NULL,
            rule_source_url TEXT NOT NULL,
            observed_facts TEXT NOT NULL,
            trigger TEXT,
            evidence_record_ids TEXT NOT NULL,
            explanation TEXT NOT NULL,
            limitations TEXT NOT NULL
        );
        """
    )
    return connection


def _store_decision_history(
    target: sqlite3.Connection, source_snapshot: Path
) -> int:
    gaps = 0
    validated_decision_ids: set[int] = set()
    source = _connect_readonly(source_snapshot / "snapshot.sqlite3")
    try:
        tables = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "greece20_decisions" not in tables:
            target.execute(
                "INSERT INTO source_coverage(source_name, query_description, complete, "
                "error, coverage_note) VALUES (?, ?, 0, ?, ?)",
                (
                    "Greece 2.0 decision PDFs",
                    "decision history interpretation",
                    "source snapshot predates decision-PDF collection",
                    "Collect a new snapshot to populate decision roles and effects.",
                ),
            )
            gaps += 1
            decisions = []
        else:
            decision_columns = {
                row[1] for row in source.execute("PRAGMA table_info(greece20_decisions)")
            }
            error_expression = "error" if "error" in decision_columns else "NULL"
            failed_decisions = source.execute(
                f"SELECT decision_id, source_url, {error_expression} "
                "FROM greece20_decisions WHERE status = 'failed' ORDER BY decision_id"
            ).fetchall()
            for decision_id, source_url, error in failed_decisions:
                target.execute(
                    "INSERT INTO source_coverage(source_name, query_description, "
                    "source_url, complete, error, coverage_note) VALUES (?, ?, ?, 0, ?, ?)",
                    (
                        "Greece 2.0 decision PDF",
                        f"failed decision {decision_id}: {source_url}",
                        source_url,
                        error or "decision collection failed without a recorded reason",
                        "Failed source-snapshot decision retained as a coverage gap.",
                    ),
                )
                gaps += 1
            hash_expression = (
                "content_sha256" if "content_sha256" in decision_columns else "NULL"
            )
            byte_expression = (
                "byte_count" if "byte_count" in decision_columns else "NULL"
            )
            decisions = source.execute(
                f"SELECT decision_id, source_url, raw_path, {hash_expression}, "
                f"{byte_expression} FROM greece20_decisions "
                "WHERE status = 'collected' ORDER BY decision_id"
            ).fetchall()
        for decision_id, source_url, raw_path, declared_hash, declared_bytes in decisions:
            try:
                if not raw_path or not declared_hash or declared_bytes is None:
                    raise ValueError("decision raw path/hash/byte count is incomplete")
                relative_path = Path(raw_path)
                if relative_path.is_absolute():
                    raise ValueError("decision raw path must be relative to the snapshot")
                decision_path = (source_snapshot / relative_path).resolve()
                try:
                    decision_path.relative_to(source_snapshot)
                except ValueError as error:
                    raise ValueError(
                        "decision raw path resolves outside the source snapshot"
                    ) from error
                payload = decision_path.read_bytes()
                actual_hash = sha256(payload).hexdigest()
                if actual_hash != declared_hash:
                    raise ValueError(
                        f"decision SHA-256 mismatch: declared {declared_hash}, "
                        f"actual {actual_hash}"
                    )
                if len(payload) != int(declared_bytes):
                    raise ValueError(
                        f"decision byte-count mismatch: declared {declared_bytes}, "
                        f"actual {len(payload)}"
                    )
                text = extract_greece20_decision_text(payload)
                classification = classify_greece20_decision(text)
            except (OSError, ValueError, PyPdfError) as error:
                gaps += 1
                target.execute(
                    "INSERT INTO source_coverage(source_name, query_description, "
                    "source_url, complete, error) VALUES (?, ?, ?, 0, ?)",
                    (
                        "Greece 2.0 decision PDF",
                        f"interpret decision {decision_id}",
                        source_url,
                        f"{type(error).__name__}: {error}",
                    ),
                )
                continue
            validated_decision_ids.add(decision_id)
            role_evidence = classification.role_evidence
            target.execute(
                """INSERT INTO greece20_decision_interpretations VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    source_url,
                    classification.role.value,
                    classification.unresolved_reason,
                    role_evidence.text if role_evidence else None,
                    role_evidence.page_number if role_evidence else None,
                    role_evidence.char_start if role_evidence else None,
                    role_evidence.char_end if role_evidence else None,
                ),
            )
            target.executemany(
                "INSERT INTO greece20_decision_effects VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        decision_id,
                        effect.effect.value,
                        effect.evidence.text,
                        effect.evidence.page_number,
                        effect.evidence.char_start,
                        effect.evidence.char_end,
                    )
                    for effect in classification.effects
                ],
            )
            target.executemany(
                "INSERT INTO decision_cancellation_amounts VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        decision_id,
                        amount.amount_cents,
                        amount.currency,
                        amount.funding_stream.value,
                        amount.evidence.text,
                        amount.evidence.page_number,
                        amount.evidence.char_start,
                        amount.evidence.char_end,
                    )
                    for amount in classification.cancellation_amounts
                ],
            )

        if "rrf_catalogue_entries" in tables:
            target.executemany(
                "INSERT INTO project_decision_links VALUES "
                "(?, NULL, NULL, ?, 'catalogue_row')",
                source.execute(
                    "SELECT ops_code, decision_url FROM rrf_catalogue_entries "
                    "WHERE ops_code IS NOT NULL AND decision_url IS NOT NULL"
                ).fetchall(),
            )
        if "rrf_project_funding_references" in tables:
            pdf_links = source.execute(
                """SELECT f.ops_code, f.pde_code, d.decision_id, d.source_url
                   FROM rrf_project_funding_references f
                   JOIN greece20_decisions d ON d.decision_id = f.decision_id"""
            ).fetchall()
            target.executemany(
                "INSERT INTO project_decision_links VALUES "
                "(?, ?, ?, ?, 'pdf_extracted')",
                [link for link in pdf_links if link[2] in validated_decision_ids],
            )
    finally:
        source.close()
    return gaps


def _store_coverage(
    connection: sqlite3.Connection,
    request: AnalysisArtifactRequest,
    raw_directory: Path,
) -> int:
    gaps = 0
    for result in request.diavgeia_searches:
        raw_path = _preserve_raw(
            result.raw_path,
            raw_directory,
            result.content_sha256,
            result.byte_count,
        )
        raw_provenance_complete = (
            result.raw_path is not None
            and result.content_sha256 is not None
            and result.byte_count is not None
        )
        complete = (
            result.error is None
            and result.results_complete is True
            and raw_provenance_complete
        )
        if not complete:
            gaps += 1
        connection.execute(
            "INSERT INTO source_coverage(source_name, query_description, source_url, "
            "retrieved_at, complete, error, raw_path, content_sha256, coverage_note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "Diavgeia",
                f"free-text term={result.query_term}",
                result.query_url,
                result.retrieved_at.isoformat(),
                int(complete),
                result.error
                or (
                    "successful search lacks complete raw path/hash/size provenance"
                    if not raw_provenance_complete
                    else None
                ),
                raw_path,
                result.content_sha256,
                "Search match is provenance, not proof of project linkage.",
            ),
        )
        connection.executemany(
            "INSERT INTO diavgeia_acts VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    result.query_term,
                    act.ada,
                    act.subject,
                    act.publication_status.value,
                    act.version_id,
                    act.document_url,
                )
                for act in result.acts
            ],
        )
    for collection in request.kimdis_collections:
        expected_windows = {
            (window.date_from, window.date_to)
            for window in split_kimdis_search_windows(
                collection.pde_code, collection.date_from, collection.date_to
            )
        }
        observed_windows = {
            (page.search.date_from, page.search.date_to)
            for page in collection.page_results
        }
        page_sets: dict[tuple[object, object], set[int]] = {}
        totals_by_window: dict[tuple[object, object], set[int | None]] = {}
        errors_by_window: dict[tuple[object, object], bool] = {}
        for page in collection.page_results:
            key = (page.search.date_from, page.search.date_to)
            page_sets.setdefault(key, set()).add(page.search.page)
            totals_by_window.setdefault(key, set()).add(page.total_pages)
            errors_by_window[key] = errors_by_window.get(key, False) or bool(page.error)

        def window_pages_complete(window: tuple[object, object]) -> bool:
            totals = totals_by_window.get(window, set())
            if len(totals) != 1 or None in totals or errors_by_window.get(window, False):
                return False
            total_pages = next(iter(totals))
            assert total_pages is not None
            if total_pages < 0:
                return False
            expected_pages = {0} if total_pages == 0 else set(range(total_pages))
            pages = [
                page
                for page in collection.page_results
                if (page.search.date_from, page.search.date_to) == window
            ]
            if any(
                page.raw_path is None
                or page.content_sha256 is None
                or page.byte_count is None
                for page in pages
            ):
                return False
            if total_pages == 0 and any(page.contracts for page in pages):
                return False
            if any(
                page.is_last_page
                is not (
                    page.search.page == 0
                    if total_pages == 0
                    else page.search.page + 1 >= total_pages
                )
                for page in pages
            ):
                return False
            return page_sets.get(window, set()) == expected_pages

        page_coverage_complete = all(
            window_pages_complete(window) for window in expected_windows
        )
        collection_complete = (
            collection.coverage_complete
            and observed_windows == expected_windows
            and page_coverage_complete
            and all(
                page.search.pde_code == collection.pde_code
                for page in collection.page_results
            )
        )
        if not collection_complete:
            gaps += 1
        page_errors = tuple(
            f"page={page.search.page}: {page.error}"
            for page in collection.page_results
            if page.error
        )
        connection.execute(
            "INSERT INTO source_coverage(source_name, query_description, complete, "
            "error, coverage_note) VALUES (?, ?, ?, ?, ?)",
            (
                "KIMDIS contracts",
                f"PDE={collection.pde_code}; registration dates "
                f"{collection.date_from.isoformat()}..{collection.date_to.isoformat()}",
                int(collection_complete),
                "; ".join(page_errors)
                or (
                    "page provenance does not cover every expected window/page"
                    if not collection_complete
                    else None
                ),
                "Split into adjacent windows of at most 180 days.",
            ),
        )
        for page in collection.page_results:
            page_raw_path = _preserve_raw(
                page.raw_path,
                raw_directory,
                page.content_sha256,
                page.byte_count,
            )
            if page.error is None and page_raw_path is not None:
                preserved_page = raw_directory.parent / page_raw_path
                parsed_contracts, parsed_total_pages = parse_kimdis_contract_page(
                    preserved_page.read_bytes()
                )
                if set(parsed_contracts) != set(page.contracts):
                    raise ValueError(
                        "Preserved raw KIMDIS page records do not match parsed page contracts"
                    )
                if (
                    parsed_total_pages is not None
                    and parsed_total_pages != page.total_pages
                ):
                    raise ValueError(
                        "Preserved raw KIMDIS page count does not match page metadata"
                    )
            connection.execute(
                "INSERT INTO kimdis_pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    collection.pde_code,
                    page.search.date_from.isoformat(),
                    page.search.date_to.isoformat(),
                    page.search.page,
                    page.source_url,
                    page.retrieved_at.isoformat(),
                    page.total_pages,
                    None if page.is_last_page is None else int(page.is_last_page),
                    page.http_status,
                    page.byte_count,
                    page.content_sha256,
                    page_raw_path,
                    page.error,
                ),
            )
            connection.executemany(
                "INSERT INTO kimdis_contract_record_sources VALUES (?, ?, ?, ?)",
                [
                    (
                        contract.reference_number,
                        page.content_sha256,
                        page.source_url,
                        page_raw_path,
                    )
                    for contract in page.contracts
                ],
            )
    for imported in request.state_aid_imports:
        raw_path = _preserve_raw(
            imported.raw_path,
            raw_directory,
            imported.content_sha256,
            imported.byte_count,
        )
        import_gap_count = (
            len(imported.skipped_rows)
            + len(imported.field_gaps)
            + len(imported.duplicate_conflicts)
        )
        export_incomplete = not imported.export_scope.full_result_export
        if import_gap_count or export_incomplete:
            gaps += 1
        scope = imported.export_scope
        connection.execute(
            "INSERT INTO source_coverage(source_name, query_description, source_url, "
            "retrieved_at, complete, error, raw_path, content_sha256, coverage_note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "EC State Aid Transparency",
                f"country={scope.country}; dates={scope.date_from}..{scope.date_to}; "
                f"measures={','.join(scope.measure_filters) or 'all'}; "
                f"full_result_export={scope.full_result_export}",
                imported.source_url,
                imported.imported_at.isoformat(),
                int(import_gap_count == 0 and not export_incomplete),
                (
                    "; ".join(
                        part
                        for part in (
                            f"{import_gap_count} import gaps" if import_gap_count else "",
                            "export is not a full-result export" if export_incomplete else "",
                        )
                        if part
                    )
                    or None
                ),
                raw_path,
                imported.content_sha256,
                imported.coverage_note,
            ),
        )
        for gap_kind, import_gaps in (
            ("skipped_row", imported.skipped_rows),
            ("field_gap", imported.field_gaps),
        ):
            connection.executemany(
                "INSERT INTO state_aid_import_gaps VALUES (?, ?, ?, NULL, ?)",
                [
                    (imported.source_url, gap_kind, gap.source_row_number, gap.reason)
                    for gap in import_gaps
                ],
            )
            connection.executemany(
                "INSERT INTO source_coverage(source_name, query_description, source_url, "
                "complete, error, coverage_note) VALUES "
                "('EC State Aid import', ?, ?, 0, ?, 'Exact import gap retained.')",
                [
                    (
                        f"{gap_kind}; source_row={gap.source_row_number}",
                        imported.source_url,
                        gap.reason,
                    )
                    for gap in import_gaps
                ],
            )
        connection.executemany(
            "INSERT INTO state_aid_import_gaps VALUES (?, 'duplicate_conflict', NULL, ?, ?)",
            [
                (
                    imported.source_url,
                    conflict.award_id,
                    f"{conflict.reason}; rows={','.join(map(str, conflict.source_row_numbers))}",
                )
                for conflict in imported.duplicate_conflicts
            ],
        )
        connection.executemany(
            "INSERT INTO source_coverage(source_name, query_description, source_url, "
            "complete, error, coverage_note) VALUES "
            "('EC State Aid import', ?, ?, 0, ?, 'Conflicting award excluded.')",
            [
                (
                    f"duplicate_conflict; award_id={conflict.award_id}",
                    imported.source_url,
                    f"{conflict.reason}; rows={','.join(map(str, conflict.source_row_numbers))}",
                )
                for conflict in imported.duplicate_conflicts
            ],
        )
    return gaps


def _store_contracts(
    connection: sqlite3.Connection,
    collections: Iterable[KimdisContractCollectionResult],
) -> tuple[CurrentContract, ...]:
    materialized = tuple(collections)
    for collection in materialized:
        collected_records = set(collection.contracts)
        page_records = {
            contract
            for page in collection.page_results
            for contract in page.contracts
        }
        if collected_records != page_records:
            raise ValueError(
                "KIMDIS collection contracts do not match preserved page records"
            )
        records_with_raw_provenance = {
            contract
            for page in collection.page_results
            if (
                page.raw_path is not None
                and page.content_sha256 is not None
                and page.byte_count is not None
            )
            for contract in page.contracts
        }
        if collected_records - records_with_raw_provenance:
            raise ValueError(
                "KIMDIS collection contract lacks a preserved raw page record"
            )
    records = [
        contract
        for collection in materialized
        for contract in collection.contracts
    ]
    current = select_current_contract_versions(records)
    connection.executemany(
        "INSERT INTO current_contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                contract.contract_id,
                contract.current_reference_number,
                contract.current_value_cents,
                contract.value_basis.value if contract.value_basis else None,
                None if contract.cancelled is None else int(contract.cancelled),
                contract.resolution_status.value,
                int(contract.history_complete),
                contract.history_issue,
                contract.version_count,
            )
            for contract in current
        ],
    )
    connection.executemany(
        "INSERT INTO kimdis_contract_versions VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                contract.contract_id,
                version.reference_number,
                version.previous_reference_number,
                version.next_reference_number,
                version.procedure_code,
                version.procedure_label,
            )
            for contract in current
            for version in contract.versions
        ],
    )
    return current


def _store_awards(
    connection: sqlite3.Connection, imports: Iterable[StateAidImportResult]
) -> tuple[StateAidAward, ...]:
    awards: list[StateAidAward] = []
    for imported in imports:
        evidence_records = {
            award.measure_evidence
            for award in imported.awards
            if award.measure_evidence is not None
        }
        connection.executemany(
            "INSERT OR IGNORE INTO state_aid_measure_evidence VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    evidence.aid_measure_reference,
                    evidence.rrf_linkage.value,
                    evidence.source_name,
                    evidence.source_url,
                    evidence.source_sha256,
                    evidence.source_act_id,
                )
                for evidence in sorted(
                    evidence_records,
                    key=lambda item: (
                        item.aid_measure_reference,
                        item.rrf_linkage.value,
                        item.source_sha256,
                    ),
                )
            ],
        )
        for award in imported.awards:
            awards.append(award)
            identifier = award.beneficiary_identifier
            connection.execute(
                "INSERT INTO state_aid_awards VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    award.award_id,
                    award.beneficiary_name,
                    identifier.scheme if identifier else None,
                    identifier.country if identifier else None,
                    identifier.value if identifier else None,
                    award.beneficiary_identifier_issue,
                    award.aid_element_cents,
                    award.currency,
                    award.instrument.value,
                    award.raw_instrument,
                    award.granting_date,
                    award.objective,
                    award.granting_authority,
                    award.aid_measure_reference,
                    award.rrf_linkage.value,
                    int(award.included_in_aid_aggregate),
                    award.exclusion_reason,
                    imported.source_url,
                    award.source_row_number,
                    award.linkage_evidence,
                    award.measure_evidence.source_sha256
                    if award.measure_evidence
                    else None,
                ),
            )
    return tuple(awards)


def _store_profiles(
    connection: sqlite3.Connection, result: CompanyProfilesResult
) -> None:
    connection.executemany(
        "INSERT INTO company_profiles VALUES ("
        + ", ".join("?" for _ in range(len(CompanyFundingProfile.__dataclass_fields__) + 2))
        + ")",
        [
            (
                profile.identifier.scheme,
                profile.identifier.country,
                profile.identifier.value,
                json.dumps(profile.names, ensure_ascii=False),
                profile.contract_count,
                profile.active_contract_count,
                profile.cancelled_contract_count,
                profile.current_contract_value_cents,
                profile.unresolved_contract_value_count,
                profile.aid_award_count,
                profile.aid_amount_eur_cents,
                profile.rrf_linked_aid_count,
                profile.rrf_linked_aid_amount_eur_cents,
                profile.non_rrf_aid_count,
                profile.non_rrf_aid_amount_eur_cents,
                profile.unresolved_aid_count,
                profile.unresolved_aid_amount_eur_cents,
                profile.non_eur_aid_count,
                profile.missing_aid_amount_or_currency_count,
                profile.excluded_loan_count,
                profile.excluded_loan_amount_eur_cents,
                profile.excluded_other_aid_count,
                profile.excluded_other_aid_amount_eur_cents,
                json.dumps(profile.contract_ids, ensure_ascii=False),
                json.dumps(profile.aid_award_ids, ensure_ascii=False),
                json.dumps(profile.current_value_contract_ids, ensure_ascii=False),
                json.dumps(profile.rrf_linked_aid_award_ids, ensure_ascii=False),
                json.dumps(
                    profile.rrf_linked_eur_amount_award_ids, ensure_ascii=False
                ),
            )
            for profile in result.profiles
        ],
    )


def _store_flags(connection: sqlite3.Connection, flags: Iterable[AnomalyFlag]) -> None:
    connection.executemany(
        "INSERT INTO anomaly_flags VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                flag.rule_id,
                flag.rule_version,
                flag.route.value,
                flag.scope_identifier.scheme,
                flag.scope_identifier.country,
                flag.scope_identifier.value,
                flag.observation_start,
                flag.observation_end,
                flag.observation_scope,
                flag.numerator_cents,
                flag.denominator_cents,
                str(flag.observed_share),
                str(flag.threshold),
                json.dumps(flag.evidence_record_ids, ensure_ascii=False),
                flag.explanation,
            )
            for flag in flags
        ],
    )


def _store_red_flag_assessments(
    connection: sqlite3.Connection,
    assessments: Iterable[RedFlagAssessment],
) -> None:
    connection.executemany(
        "INSERT INTO red_flag_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                assessment.rule_id,
                assessment.rule_version,
                assessment.outcome.value,
                assessment.route.value,
                assessment.subject_type.value,
                assessment.subject_identifier,
                assessment.observation_start,
                assessment.observation_end,
                assessment.observation_scope,
                assessment.rule_source_url,
                json.dumps(
                    [asdict(fact) for fact in assessment.observed_facts],
                    ensure_ascii=False,
                ),
                assessment.trigger,
                json.dumps(assessment.evidence_record_ids, ensure_ascii=False),
                assessment.explanation,
                json.dumps(assessment.limitations, ensure_ascii=False),
            )
            for assessment in assessments
        ],
    )


def _store_substantive_gaps(
    connection: sqlite3.Connection,
    contracts: tuple[CurrentContract, ...],
    awards: tuple[StateAidAward, ...],
    profiles: CompanyProfilesResult,
) -> int:
    rows: list[tuple[str, str, str]] = []
    for contract in contracts:
        issues: list[str] = []
        if not contract.history_complete:
            issues.append(contract.history_issue or "contract history incomplete")
        if contract.cancelled is None:
            issues.append("current cancellation status unknown")
        if contract.current_value_cents is None:
            issues.append("current contract value unavailable")
        current_version = next(
            (
                version
                for version in contract.versions
                if version.reference_number == contract.current_reference_number
            ),
            None,
        )
        if (
            current_version is not None
            and current_version.member_value_allocation
            is ContractMemberValueAllocation.UNRESOLVED_CONSORTIUM_SHARES
        ):
            issues.append("consortium member value shares are unresolved")
        if issues:
            rows.append(
                (
                    "KIMDIS contract interpretation",
                    f"contract_id={contract.contract_id}",
                    "; ".join(dict.fromkeys(issues)),
                )
            )
    for award in awards:
        if award.rrf_linkage.value == "unresolved":
            rows.append(
                (
                    "State-aid RRF linkage",
                    f"award_id={award.award_id}",
                    award.linkage_evidence,
                )
            )
        if award.aid_element_cents is None or not (award.currency or "").strip():
            rows.append(
                (
                    "State-aid amount",
                    f"award_id={award.award_id}",
                    "aid element or currency is missing/unparseable",
                )
            )
        if award.instrument is AidInstrument.UNKNOWN:
            rows.append(
                (
                    "State-aid instrument",
                    f"award_id={award.award_id}",
                    award.exclusion_reason
                    or "unknown aid instrument excluded pending policy",
                )
            )
    for contract_id in profiles.contracts_without_identifiers:
        rows.append(
            (
                "Company identity",
                f"contract_id={contract_id}",
                "no official contractor identifier available",
            )
        )
    for contract_id in profiles.contracts_without_current_identifiers:
        rows.append(
            (
                "Company identity",
                f"contract_id={contract_id}",
                "current contract version has no official contractor identifier",
            )
        )
    for contract_id in profiles.contract_conflicts:
        rows.append(
            (
                "Company profile conflict",
                f"contract_id={contract_id}",
                "conflicting current-contract facts excluded from aggregation",
            )
        )
    for award_id in profiles.aid_award_conflicts:
        rows.append(
            (
                "Company profile conflict",
                f"award_id={award_id}",
                "conflicting aid-award facts excluded from aggregation",
            )
        )
    for award in profiles.unmatched_aid_awards:
        rows.append(
            (
                "Company identity",
                f"award_id={award.award_id}",
                award.beneficiary_identifier_issue
                or "no official beneficiary identifier available",
            )
        )
    connection.executemany(
        "INSERT INTO source_coverage(source_name, query_description, complete, error, "
        "coverage_note) VALUES (?, ?, 0, ?, 'Substantive gap excluded or retained explicitly.')",
        rows,
    )
    return len(rows)


def _write_profiles_csv(path: Path, profiles: Iterable[CompanyFundingProfile]) -> None:
    fields = [field for field in CompanyFundingProfile.__dataclass_fields__]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for profile in profiles:
            row = asdict(profile)
            identifier = profile.identifier
            row["identifier"] = f"{identifier.scheme}:{identifier.country}:{identifier.value}"
            for key in ("names", "contract_ids", "aid_award_ids"):
                row[key] = " | ".join(row[key])
            for key in (
                "current_value_contract_ids",
                "rrf_linked_aid_award_ids",
                "rrf_linked_eur_amount_award_ids",
            ):
                row[key] = " | ".join(row[key])
            row = {key: _spreadsheet_safe(value) for key, value in row.items()}
            writer.writerow(row)


def _write_flags_csv(path: Path, flags: Iterable[AnomalyFlag]) -> None:
    fields = [field for field in AnomalyFlag.__dataclass_fields__]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for flag in flags:
            row = asdict(flag)
            identifier = flag.scope_identifier
            row["route"] = flag.route.value
            row["scope_identifier"] = (
                f"{identifier.scheme}:{identifier.country}:{identifier.value}"
            )
            row["observed_share"] = str(flag.observed_share)
            row["threshold"] = str(flag.threshold)
            row["evidence_record_ids"] = " | ".join(flag.evidence_record_ids)
            row = {key: _spreadsheet_safe(value) for key, value in row.items()}
            writer.writerow(row)


def _write_red_flag_assessments_csv(
    path: Path, assessments: Iterable[RedFlagAssessment]
) -> None:
    fields = [field for field in RedFlagAssessment.__dataclass_fields__]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for assessment in assessments:
            row = asdict(assessment)
            row["outcome"] = assessment.outcome.value
            row["route"] = assessment.route.value
            row["subject_type"] = assessment.subject_type.value
            row["observed_facts"] = json.dumps(
                [asdict(fact) for fact in assessment.observed_facts],
                ensure_ascii=False,
            )
            row["evidence_record_ids"] = " | ".join(
                assessment.evidence_record_ids
            )
            row["limitations"] = " | ".join(assessment.limitations)
            row = {key: _spreadsheet_safe(value) for key, value in row.items()}
            writer.writerow(row)


def _spreadsheet_safe(value: object) -> object:
    if isinstance(value, str):
        index = 0
        while index < len(value) and (
            value[index].isspace()
            or unicodedata.category(value[index]) == "Cc"
        ):
            index += 1
        visible = value[index:]
        if visible.startswith(("=", "+", "-", "@")):
            return "'" + value
    return value


def _money(cents: int) -> str:
    return f"€{Decimal(cents) / Decimal(100):,.2f}"


def _source_link(url: str | None) -> str:
    if not url or urlparse(url).scheme not in {"http", "https"}:
        return escape(url or "")
    safe_url = escape(url, quote=True)
    return f'<a href="{safe_url}">{escape(url)}</a>'


def _validate_observation_scopes(request: AnalysisArtifactRequest) -> None:
    contract_period = (
        request.anomaly_config.observation_start,
        request.anomaly_config.observation_end,
    )
    mismatched_contracts = [
        collection.pde_code
        for collection in request.kimdis_collections
        if (
            collection.date_from.isoformat(),
            collection.date_to.isoformat(),
        )
        != contract_period
    ]
    if mismatched_contracts:
        raise ValueError(
            "Contract observation period must exactly match every KIMDIS collection"
        )
    if not request.state_aid_imports:
        return
    scopes = {imported.export_scope for imported in request.state_aid_imports}
    if len(scopes) != 1:
        raise ValueError(
            "State-aid imports must share one identical export scope for aggregation"
        )
    scope = next(iter(scopes))
    if scope.date_from is None or scope.date_to is None:
        raise ValueError("State-aid export scope requires explicit dates")
    expected = (
        request.anomaly_config.rrf_aid_observation_start
        or request.anomaly_config.observation_start,
        request.anomaly_config.rrf_aid_observation_end
        or request.anomaly_config.observation_end,
    )
    if (scope.date_from, scope.date_to) != expected:
        raise ValueError(
            "State-aid observation period must exactly match the imported export scope"
        )


def _normalized_anomaly_config(request: AnalysisArtifactRequest) -> AnomalyConfig:
    config = request.anomaly_config
    if request.state_aid_imports:
        scope = request.state_aid_imports[0].export_scope
        aid_scope = (
            f"EC State Aid export; country={scope.country}; "
            f"dates={scope.date_from}..{scope.date_to}; "
            f"measures={','.join(scope.measure_filters) or 'all'}; "
            f"full_result_export={scope.full_result_export}"
        )
    else:
        aid_scope = "EC State Aid export; none imported"
    return replace(
        config,
        rrf_aid_scope_description=aid_scope,
    )


def _write_html(
    path: Path,
    *,
    source_snapshot_id: str,
    cutoff: str,
    profiles: tuple[CompanyFundingProfile, ...],
    flags: tuple[AnomalyFlag, ...],
    red_flag_assessments: tuple[RedFlagAssessment, ...],
    source_gaps: int,
    state_aid_imports: tuple[StateAidImportResult, ...],
    coverage_rows: tuple[CoverageReportRow, ...],
) -> None:
    profile_rows = "".join(
        "<tr>"
        f"<td>{escape(profile.identifier.value)}</td>"
        f"<td>{escape(' / '.join(profile.names))}</td>"
        f"<td>{profile.contract_count}</td>"
        f"<td>{escape(_money(profile.current_contract_value_cents))}</td>"
        f"<td>{profile.aid_award_count}</td>"
        f"<td>{escape(_money(profile.aid_amount_eur_cents))}</td>"
        f"<td>{profile.unresolved_contract_value_count}</td>"
        f"<td>{profile.unresolved_aid_count}</td>"
        f"<td>{profile.excluded_loan_count}</td>"
        "</tr>"
        for profile in profiles
    )
    flag_rows = "".join(
        "<tr>"
        f"<td>{escape(flag.rule_id)}</td>"
        f"<td>{escape(flag.route.value)}</td>"
        f"<td>{escape(flag.scope_identifier.value)}</td>"
        f"<td>{escape(flag.observation_start)}</td>"
        f"<td>{escape(flag.observation_end)}</td>"
        f"<td>{escape(flag.observation_scope)}</td>"
        f"<td>{escape(f'{flag.observed_share:.2%}')}</td>"
        f"<td>{escape(' / '.join(flag.evidence_record_ids))}</td>"
        f"<td>{escape(flag.explanation)}</td>"
        "</tr>"
        for flag in flags
    )
    red_flag_rows = "".join(
        "<tr>"
        f"<td>{escape(assessment.rule_id)}</td>"
        f"<td>{escape(assessment.outcome.value)}</td>"
        f"<td>{escape(assessment.subject_identifier)}</td>"
        f"<td>{escape(' / '.join(assessment.evidence_record_ids))}</td>"
        f"<td>{escape(assessment.explanation)}</td>"
        f"<td>{escape(' / '.join(assessment.limitations))}</td>"
        "</tr>"
        for assessment in red_flag_assessments
        if assessment.outcome is not AssessmentOutcome.NOT_FLAGGED
    )
    coverage_items = "".join(
        "<li>"
        f"{escape(imported.coverage.description)} Thresholds: "
        f"{escape('; '.join(imported.coverage.publication_thresholds))}. "
        f"Deadlines: {escape('; '.join(imported.coverage.publication_deadlines))}."
        "</li>"
        for imported in state_aid_imports
    )
    source_coverage_rows = "".join(
        "<tr>"
        f"<td>{escape(row.source_name)}</td>"
        f"<td>{escape(row.query_description)}</td>"
        f"<td>{escape('unknown' if row.complete is None else ('yes' if row.complete else 'no'))}</td>"
        f"<td>{escape(row.error or '')}</td>"
        f"<td>{escape(row.coverage_note or '')}</td>"
        f"<td>{_source_link(row.source_url)}</td>"
        "</tr>"
        for row in coverage_rows
    )
    measure_evidence = {
        award.measure_evidence
        for imported in state_aid_imports
        for award in imported.awards
        if award.measure_evidence is not None
    }
    measure_evidence_items = "".join(
        "<li>"
        f"{escape(item.aid_measure_reference)}: {escape(item.rrf_linkage.value)}; "
        f"{escape(item.source_name)}; {_source_link(item.source_url)}; "
        f"SHA-256 {escape(item.source_sha256)}; act {escape(item.source_act_id or '')}"
        "</li>"
        for item in sorted(
            measure_evidence,
            key=lambda evidence: (
                evidence.aid_measure_reference,
                evidence.rrf_linkage.value,
                evidence.source_sha256,
            ),
        )
    )
    path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>crawlD analysis</title></head><body>"
        "<h1>crawlD evidence-backed analysis</h1>"
        f"<p>Source snapshot: {escape(source_snapshot_id)}; cutoff: {escape(cutoff)}; "
        f"source gaps: {source_gaps}.</p>"
        "<p>Contract values and State-aid elements are separate routes and are not added "
        "into a combined funding total. Anomaly flags are investigation leads, not "
        "findings of wrongdoing.</p>"
        "<h2>State-aid publication coverage</h2><ul>" + coverage_items + "</ul>"
        "<h2>Source searches and gaps</h2><table><thead><tr>"
        "<th>Source</th><th>Query</th><th>Complete</th><th>Error</th>"
        "<th>Coverage note</th><th>Source URL</th></tr></thead><tbody>"
        + source_coverage_rows
        + "</tbody></table>"
        "<h2>State-aid measure evidence</h2><ul>"
        + measure_evidence_items
        + "</ul><p>Download <a href='company_profiles.csv'>company_profiles.csv</a> "
        "<a href='anomaly_flags.csv'>anomaly_flags.csv</a>, and "
        "<a href='red_flag_assessments.csv'>red_flag_assessments.csv</a>; full provenance and "
        "unresolved facts are in <a href='analysis.sqlite3'>analysis.sqlite3</a>.</p>"
        "<h2>Company profiles</h2><table><thead><tr>"
        "<th>Identifier</th><th>Names</th><th>Contract count</th>"
        "<th>Current contract value</th><th>Aid award count</th>"
        "<th>Aid element</th><th>Unresolved contract values</th>"
        "<th>Unresolved aid linkage</th><th>Excluded loans</th></tr></thead><tbody>"
        + profile_rows
        + "</tbody></table><h2>Red flags and inconclusive assessments</h2>"
        "<table><thead><tr><th>Rule</th><th>Outcome</th><th>Subject</th>"
        "<th>Evidence IDs</th><th>Explanation</th><th>Limitations</th>"
        "</tr></thead><tbody>"
        + red_flag_rows
        + "</tbody></table><h2>Anomaly flags</h2><table><thead><tr>"
        "<th>Rule</th><th>Route</th><th>Identifier</th><th>Start</th><th>End</th>"
        "<th>Observation scope</th><th>Share</th><th>Evidence IDs</th>"
        "<th>Explanation</th></tr></thead><tbody>"
        + flag_rows
        + "</tbody></table></body></html>\n",
        encoding="utf-8",
    )


def build_analysis_artifact(request: AnalysisArtifactRequest) -> AnalysisArtifactResult:
    """Create a derived artifact; never mutate the completed source snapshot."""
    source_snapshot = request.source_snapshot.resolve()
    manifest_path = source_snapshot / "manifest.json"
    database_path = source_snapshot / "snapshot.sqlite3"
    if not manifest_path.is_file():
        raise ValueError(f"Required source snapshot manifest is missing: {manifest_path}")
    if not database_path.is_file():
        raise ValueError(f"Required source snapshot database is missing: {database_path}")
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("status") != "complete":
        raise ValueError("Analysis requires a complete source snapshot")
    _validate_observation_scopes(request)
    anomaly_config = _normalized_anomaly_config(request)
    output_root = request.output_root.resolve()
    try:
        output_root.relative_to(source_snapshot)
    except ValueError:
        pass
    else:
        raise ValueError("Analysis output root must be outside the source snapshot")
    source_snapshot_id = str(source_manifest["snapshot_id"])
    cutoff = str(source_manifest["cutoff"])
    source_manifest_hash = _sha256(manifest_path)
    source_database_hash = _sha256(database_path)
    analysis_id = (
        request.generated_at.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    output_root.mkdir(parents=True, exist_ok=True)
    analysis_dir = output_root / analysis_id
    staging_dir = output_root / f".{analysis_id}.staging"
    failed_dir = output_root / f".{analysis_id}.failed"
    staging_dir.mkdir(exist_ok=False)
    raw_directory = staging_dir / "raw"
    raw_directory.mkdir()
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(staging_dir / "analysis.sqlite3")
        source_gaps = _store_decision_history(connection, source_snapshot)
        source_gaps += _store_coverage(connection, request, raw_directory)
        contracts = _store_contracts(connection, request.kimdis_collections)
        awards = _store_awards(connection, request.state_aid_imports)
        profile_result = build_company_profiles(contracts, awards)
        source_gaps += _store_substantive_gaps(
            connection, contracts, awards, profile_result
        )
        source_gaps = connection.execute(
            "SELECT COUNT(*) FROM source_coverage WHERE complete = 0"
        ).fetchone()[0]
        flags = generate_anomaly_flags(profile_result.profiles, anomaly_config)
        incomplete_kimdis = connection.execute(
            "SELECT COUNT(*) FROM source_coverage "
            "WHERE source_name='KIMDIS contracts' AND complete=0"
        ).fetchone()[0]
        pde_scope = ",".join(
            sorted({collection.pde_code for collection in request.kimdis_collections})
        ) or "none"
        contract_scope = (
            f"KIMDIS contract searches; PDE={pde_scope}; registration dates "
            f"{anomaly_config.observation_start}..{anomaly_config.observation_end}"
        )
        contract_coverage_limitations = (
            ("KIMDIS collection coverage is incomplete",)
            if incomplete_kimdis
            else ()
        )
        direct_award_assessments = assess_direct_awards(
            contracts,
            DirectAwardRuleConfig(
                rule_version=anomaly_config.rule_version,
                observation_start=anomaly_config.observation_start,
                observation_end=anomaly_config.observation_end,
                observation_scope=contract_scope,
                coverage_limitations=contract_coverage_limitations,
            ),
        )
        repeated_contract_assessments = assess_repeated_contracts(
            contracts,
            RepeatedContractRuleConfig(
                rule_version=anomaly_config.rule_version,
                observation_start=anomaly_config.observation_start,
                observation_end=anomaly_config.observation_end,
                observation_scope=contract_scope,
                minimum_contracts=anomaly_config.repeated_contract_minimum,
                window_days=anomaly_config.repeated_contract_window_days,
                coverage_limitations=contract_coverage_limitations,
            ),
        )
        value_increase_assessments = assess_contract_value_increases(
            contracts,
            ContractValueIncreaseRuleConfig(
                rule_version=anomaly_config.rule_version,
                observation_start=anomaly_config.observation_start,
                observation_end=anomaly_config.observation_end,
                observation_scope=contract_scope,
                coverage_limitations=contract_coverage_limitations,
            ),
        )
        aid_assessments = ()
        if request.state_aid_imports:
            aid_scope = request.state_aid_imports[0].export_scope
            aid_incomplete = any(
                not item.export_scope.full_result_export or item.skipped_rows
                or item.field_gaps or item.duplicate_conflicts
                for item in request.state_aid_imports
            )
            aid_assessments = assess_repeated_state_aid(
                awards,
                RepeatedStateAidRuleConfig(
                    rule_version=anomaly_config.rule_version,
                    observation_start=aid_scope.date_from,
                    observation_end=aid_scope.date_to,
                    observation_scope=anomaly_config.rrf_aid_scope_description,
                    coverage_limitations=("State-aid export or import coverage is incomplete",) if aid_incomplete else (),
                ),
            )
        red_flag_assessments = (
            *direct_award_assessments,
            *repeated_contract_assessments,
            *value_increase_assessments,
            *aid_assessments,
        )
        _store_profiles(connection, profile_result)
        _store_flags(connection, flags)
        _store_red_flag_assessments(connection, red_flag_assessments)
        status = "complete_with_gaps" if source_gaps else "complete"
        connection.execute(
            "INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                analysis_id,
                source_snapshot_id,
                source_manifest_hash,
                source_database_hash,
                cutoff,
                request.generated_at.isoformat(),
                status,
                source_gaps,
            ),
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise sqlite3.DatabaseError(f"Analysis database integrity check failed: {integrity}")
        coverage_rows = tuple(
            CoverageReportRow(
                source_name=row[0],
                query_description=row[1],
                source_url=row[2],
                complete=None if row[3] is None else bool(row[3]),
                error=row[4],
                coverage_note=row[5],
            )
            for row in connection.execute(
                "SELECT source_name, query_description, source_url, complete, error, "
                "coverage_note FROM source_coverage ORDER BY coverage_id"
            )
        )
        connection.close()
        connection = None

        profiles_path = staging_dir / "company_profiles.csv"
        flags_path = staging_dir / "anomaly_flags.csv"
        red_flag_assessments_path = staging_dir / "red_flag_assessments.csv"
        html_path = staging_dir / "report.html"
        _write_profiles_csv(profiles_path, profile_result.profiles)
        _write_flags_csv(flags_path, flags)
        _write_red_flag_assessments_csv(
            red_flag_assessments_path, red_flag_assessments
        )
        _write_html(
            html_path,
            source_snapshot_id=source_snapshot_id,
            cutoff=cutoff,
            profiles=profile_result.profiles,
            flags=flags,
            red_flag_assessments=red_flag_assessments,
            source_gaps=source_gaps,
            state_aid_imports=request.state_aid_imports,
            coverage_rows=coverage_rows,
        )
        if _sha256(manifest_path) != source_manifest_hash or _sha256(
            database_path
        ) != source_database_hash:
            raise RuntimeError("Source snapshot changed while analysis was being built")
        output_files = tuple(
            sorted(
                path
                for path in staging_dir.rglob("*")
                if path.is_file() and path.name != "manifest.json"
            )
        )
        manifest = {
            "analysis_id": analysis_id,
            "status": status,
            "generated_at": request.generated_at.isoformat(),
            "source_snapshot_id": source_snapshot_id,
            "source_snapshot_manifest_sha256": source_manifest_hash,
            "source_snapshot_database_sha256": source_database_hash,
            "source_cutoff": cutoff,
            "source_gaps": source_gaps,
            "profile_count": len(profile_result.profiles),
            "anomaly_flag_count": len(flags),
            "red_flag_assessment_count": len(red_flag_assessments),
            "red_flag_count": sum(
                assessment.outcome is AssessmentOutcome.FLAGGED
                for assessment in red_flag_assessments
            ),
            "red_flag_unable_to_assess_count": sum(
                assessment.outcome is AssessmentOutcome.UNABLE_TO_ASSESS
                for assessment in red_flag_assessments
            ),
            "red_flag_assessment_counts_by_rule": {
                rule_id: {
                    outcome.value: sum(
                        assessment.rule_id == rule_id
                        and assessment.outcome is outcome
                        for assessment in red_flag_assessments
                    )
                    for outcome in AssessmentOutcome
                }
                for rule_id in sorted(
                    {assessment.rule_id for assessment in red_flag_assessments}
                )
            },
            "unresolved": {
                "contracts_without_identifiers": len(
                    profile_result.contracts_without_identifiers
                ),
                "contracts_without_current_identifiers": len(
                    profile_result.contracts_without_current_identifiers
                ),
                "contract_conflicts": len(profile_result.contract_conflicts),
                "aid_award_conflicts": len(profile_result.aid_award_conflicts),
                "unmatched_aid_awards": len(profile_result.unmatched_aid_awards),
            },
            "anomaly_config": {
                **asdict(anomaly_config),
                "rrf_aid_share_threshold": str(
                    anomaly_config.rrf_aid_share_threshold
                ),
            },
            "file_sha256": {
                str(path.relative_to(staging_dir)): _sha256(path)
                for path in output_files
            },
        }
        (staging_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        staging_dir.rename(analysis_dir)
    except Exception:
        if connection is not None:
            connection.close()
        if staging_dir.exists() and not failed_dir.exists():
            staging_dir.rename(failed_dir)
        raise
    return AnalysisArtifactResult(
        analysis_id=analysis_id,
        analysis_dir=analysis_dir,
        status=status,
        profile_count=len(profile_result.profiles),
        anomaly_flag_count=len(flags),
        source_gaps=source_gaps,
    )
