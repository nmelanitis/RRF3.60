"""Command-line interface for crawlD."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from zoneinfo import ZoneInfo

from crawld.analysis import AnalysisArtifactRequest, build_analysis_artifact
from crawld.anomalies import AnomalyConfig
from crawld.diavgeia import DiavgeiaSearchResult, collect_diavgeia_search
from crawld.kimdis import KimdisContractCollectionResult, collect_kimdis_contracts
from crawld.snapshot import SnapshotRequest, collect_snapshot
from crawld.state_aid import (
    AidMeasureEvidence,
    RrfAidLinkage,
    StateAidCoverage,
    StateAidExportScope,
    StateAidImportRequest,
    import_state_aid_csv,
)
from crawld.state_aid_evidence import (
    StateAidEvidenceCollectionRequest,
    collect_state_aid_evidence,
)


ATHENS = ZoneInfo("Europe/Athens")


def _cutoff(value: str | None) -> datetime:
    if value is None:
        return datetime.now(ATHENS)
    try:
        if len(value) == 10:
            parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
            return datetime.combine(parsed_date, time.max, ATHENS)
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "cutoff must be YYYY-MM-DD or an ISO-8601 datetime"
        ) from error
    return parsed.replace(tzinfo=ATHENS) if parsed.tzinfo is None else parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crawld", description="Collect and analyse Greek RRF public records"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser(
        "collect", help="collect a Greece 2.0 catalogue and decision PDF snapshot"
    )
    collect.add_argument(
        "--through",
        metavar="DATE_OR_DATETIME",
        help="cutoff; defaults to the runtime moment in Europe/Athens",
    )
    collect.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/snapshots"),
        help="snapshot parent directory (default: data/snapshots)",
    )
    collect.add_argument(
        "--resume", type=Path, metavar="SNAPSHOT_DIR", help="resume an incomplete snapshot"
    )
    collect_aid_evidence = commands.add_parser(
        "collect-state-aid-evidence",
        help="resolve CSV aid-measure references from cached official evidence",
    )
    collect_aid_evidence.add_argument("--state-aid-csv", type=Path, required=True)
    collect_aid_evidence.add_argument("--state-aid-config", type=Path, required=True)
    collect_aid_evidence.add_argument(
        "--commission-dataset",
        type=Path,
        default=Path("data/cache/commission-state-aid/case-data-SA.json"),
        help=(
            "local Commission JSON cache; downloaded once when absent "
            "(default: data/cache/commission-state-aid/case-data-SA.json)"
        ),
    )
    collect_aid_evidence.add_argument(
        "--skip-diavgeia",
        action="store_true",
        help="skip exact-SA Diavgeia fallback searches and record them as not run",
    )
    collect_aid_evidence.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/state-aid-evidence"),
        help="evidence-run parent directory (default: data/state-aid-evidence)",
    )
    analyse = commands.add_parser(
        "analyse",
        help="build a derived contract/State-aid analysis from a completed snapshot",
    )
    analyse.add_argument("--snapshot", type=Path, required=True)
    analyse.add_argument("--state-aid-csv", type=Path, required=True)
    analyse.add_argument(
        "--state-aid-config",
        type=Path,
        required=True,
        help="JSON export scope, coverage, and official aid-measure evidence",
    )
    analyse.add_argument("--kimdis-from", type=date.fromisoformat, required=True)
    analyse.add_argument("--kimdis-to", type=date.fromisoformat, required=True)
    analyse.add_argument("--pde", action="append", default=[], help="additional PDE code")
    analyse.add_argument("--ops", action="append", default=[], help="additional OPS code")
    analyse.add_argument("--skip-kimdis", action="store_true")
    analyse.add_argument("--skip-diavgeia", action="store_true")
    analyse.add_argument(
        "--rrf-aid-share-threshold",
        type=Decimal,
        required=True,
        help="explicit RRF-aid-route anomaly share, e.g. 0.25",
    )
    analyse.add_argument("--minimum-rrf-aid-eur", type=Decimal, default=Decimal(0))
    analyse.add_argument(
        "--output-root", type=Path, default=Path("data/analyses")
    )
    return parser


def _analysis_terms(snapshot: Path) -> tuple[set[str], set[str]]:
    database = snapshot / "snapshot.sqlite3"
    if not database.is_file():
        raise ValueError(f"Required source snapshot database is missing: {database}")
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        ops = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT ops_code FROM rrf_catalogue_entries "
                "WHERE ops_code IS NOT NULL"
            )
        } if "rrf_catalogue_entries" in tables else set()
        pde = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT pde_code FROM rrf_project_funding_references"
            )
        } if "rrf_project_funding_references" in tables else set()
        return ops, pde
    finally:
        connection.close()


def _state_aid_request(
    csv_path: Path, config_path: Path, imported_at: datetime
) -> StateAidImportRequest:
    value = json.loads(config_path.read_text(encoding="utf-8"))
    scope = value["export_scope"]
    coverage = value["coverage"]
    full_result_export = scope["full_result_export"]
    if type(full_result_export) is not bool:
        raise ValueError("State Aid full_result_export must be a JSON Boolean")
    evidence = tuple(
        AidMeasureEvidence(
            aid_measure_reference=item["aid_measure_reference"],
            rrf_linkage=RrfAidLinkage(item["rrf_linkage"]),
            source_name=item["source_name"],
            source_url=item["source_url"],
            source_sha256=item["source_sha256"],
            source_act_id=item.get("source_act_id"),
        )
        for item in value["measure_evidence"]
    )
    return StateAidImportRequest(
        csv_path=csv_path,
        source_url=value["source_url"],
        imported_at=imported_at,
        measure_evidence=evidence,
        export_scope=StateAidExportScope(
            country=scope["country"],
            date_from=scope.get("date_from"),
            date_to=scope.get("date_to"),
            measure_filters=tuple(scope.get("measure_filters", [])),
            full_result_export=full_result_export,
        ),
        coverage=StateAidCoverage(
            description=coverage["description"],
            publication_thresholds=tuple(coverage["publication_thresholds"]),
            publication_deadlines=tuple(coverage["publication_deadlines"]),
        ),
    )


def _skipped_diavgeia(term: str, generated_at: datetime) -> DiavgeiaSearchResult:
    return DiavgeiaSearchResult(
        query_term=term,
        query_url="https://diavgeia.gov.gr/opendata/search",
        retrieved_at=generated_at,
        acts=(),
        total_reported=None,
        results_complete=None,
        http_status=None,
        byte_count=None,
        content_sha256=None,
        raw_path=None,
        error="search skipped by user",
    )


def _eur_cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal("1")))


def _run_analysis(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    generated_at = datetime.now(UTC)
    try:
        ops_codes, pde_codes = _analysis_terms(args.snapshot)
        ops_codes.update(args.ops)
        pde_codes.update(args.pde)
        with tempfile.TemporaryDirectory(prefix="crawld-analysis-") as directory:
            staging = Path(directory)
            imported = import_state_aid_csv(
                _state_aid_request(
                    args.state_aid_csv, args.state_aid_config, generated_at
                ),
                staging / "state-aid",
            )
            aid_scope = imported.export_scope
            if aid_scope.date_from is None or aid_scope.date_to is None:
                parser.error(
                    "State Aid export scope requires date_from and date_to for anomaly labeling"
                )
            anomaly_config = AnomalyConfig(
                rule_version="1.0",
                observation_start=args.kimdis_from.isoformat(),
                observation_end=args.kimdis_to.isoformat(),
                rrf_aid_share_threshold=args.rrf_aid_share_threshold,
                minimum_rrf_aid_cents=_eur_cents(args.minimum_rrf_aid_eur),
                rrf_aid_observation_start=aid_scope.date_from,
                rrf_aid_observation_end=aid_scope.date_to,
                rrf_aid_scope_description=(
                    f"EC State Aid export; country={aid_scope.country}; "
                    f"dates={aid_scope.date_from}..{aid_scope.date_to}; "
                    f"measures={','.join(aid_scope.measure_filters) or 'all'}; "
                    f"full_result_export={aid_scope.full_result_export}"
                ),
            )
            if args.skip_kimdis:
                targets = pde_codes or {"PDE unavailable in source snapshot"}
                kimdis = tuple(
                    KimdisContractCollectionResult(
                        pde_code=code,
                        date_from=args.kimdis_from,
                        date_to=args.kimdis_to,
                        page_results=(),
                        contracts=(),
                        coverage_complete=False,
                    )
                    for code in sorted(targets)
                )
            else:
                if not pde_codes:
                    parser.error(
                        "No PDE codes in snapshot; supply --pde or use --skip-kimdis"
                    )
                kimdis = tuple(
                    collect_kimdis_contracts(
                        code,
                        args.kimdis_from,
                        args.kimdis_to,
                        staging / "kimdis",
                    )
                    for code in sorted(pde_codes)
                )
            diavgeia_terms = sorted(ops_codes | pde_codes) or ["terms unavailable"]
            diavgeia = tuple(
                _skipped_diavgeia(term, generated_at)
                if args.skip_diavgeia
                else collect_diavgeia_search(term, staging / "diavgeia")
                for term in diavgeia_terms
            )
            result = build_analysis_artifact(
                AnalysisArtifactRequest(
                    source_snapshot=args.snapshot,
                    output_root=args.output_root,
                    generated_at=generated_at,
                    kimdis_collections=kimdis,
                    state_aid_imports=(imported,),
                    diavgeia_searches=diavgeia,
                    anomaly_config=anomaly_config,
                )
            )
    except (
        KeyError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        InvalidOperation,
        sqlite3.Error,
    ) as error:
        parser.error(str(error))
    print(f"analysis: {result.analysis_dir}")
    print(f"status: {result.status}")
    print(f"profiles: {result.profile_count}")
    print(f"anomaly flags: {result.anomaly_flag_count}")
    print(f"source gaps: {result.source_gaps}")
    return 0


def _run_state_aid_evidence(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> int:
    try:
        result = collect_state_aid_evidence(
            StateAidEvidenceCollectionRequest(
                csv_path=args.state_aid_csv,
                config_path=args.state_aid_config,
                output_root=args.output_root,
                commission_dataset_path=args.commission_dataset,
                collected_at=datetime.now(UTC),
                search_diavgeia=not args.skip_diavgeia,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(f"evidence: {result.output_dir}")
    print(f"resolved config: {result.resolved_config_path}")
    print(f"Commission dataset reused: {result.dataset_reused}")
    print(f"SA references: {result.reference_count}")
    print(f"linked: {result.linked_count}")
    print(f"unresolved: {result.unresolved_count}")
    print(f"conflicts: {result.conflict_count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "analyse":
        return _run_analysis(args, parser)
    if args.command == "collect-state-aid-evidence":
        return _run_state_aid_evidence(args, parser)
    if args.command != "collect":
        parser.error(f"unsupported command: {args.command}")

    if args.resume is not None and args.through is not None:
        parser.error("--through cannot be combined with --resume")
    try:
        cutoff = None if args.resume is not None else _cutoff(args.through)
        result = collect_snapshot(
            SnapshotRequest(
                cutoff=cutoff,
                output_root=args.output_root,
                resume_snapshot=args.resume,
            )
        )
    except (argparse.ArgumentTypeError, ValueError) as error:
        parser.error(str(error))
    print(f"snapshot: {result.snapshot_dir}")
    print(f"status: {result.status}")
    print(f"catalogue rows seen: {result.entries_seen}")
    print(f"rows included: {result.entries_included}")
    print(f"rows after cutoff: {result.entries_after_cutoff}")
    print(f"decision URLs found: {result.decision_urls_found}")
    print(f"decisions collected: {result.decisions_collected}")
    print(f"decisions failed: {result.decisions_failed}")
    print(f"funding references extracted: {result.funding_references_extracted}")
    if result.decisions_failed:
        print("PDF data gaps: inspect greece20_decisions in snapshot.sqlite3.",
              file=sys.stderr)
    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
    return 0 if result.status == "complete" else 1

