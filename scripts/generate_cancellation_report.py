"""Generate a cancellation report from a preserved crawlD analysis artifact."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from crawld.kimdis import KimdisContract, parse_kimdis_contract_page


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def _money(cents: int) -> str:
    return f"{Decimal(cents) / 100:,.2f}"


def _percentage(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0000"
    return f"{Decimal(numerator) * 100 / Decimal(denominator):.4f}"


def _metric_set(connection: sqlite3.Connection, *, complete_history: bool) -> dict[str, Any]:
    history_clause = "AND history_complete = 1" if complete_history else ""
    row = connection.execute(
        f"""
        SELECT
            COUNT(*) AS denominator_contracts,
            SUM(cancelled) AS cancelled_contracts,
            SUM(current_value_cents) AS denominator_value_cents,
            SUM(CASE WHEN cancelled = 1 THEN current_value_cents ELSE 0 END)
                AS cancelled_value_cents
        FROM current_contracts
        WHERE resolution_status = 'resolved'
          AND cancelled IS NOT NULL
          AND current_value_cents IS NOT NULL
          {history_clause}
        """
    ).fetchone()
    result = dict(row)
    result["cancelled_contract_percent"] = _percentage(
        result["cancelled_contracts"], result["denominator_contracts"]
    )
    result["cancelled_value_percent"] = _percentage(
        result["cancelled_value_cents"], result["denominator_value_cents"]
    )
    return result


def _source_coverage(connection: sqlite3.Connection) -> dict[str, Any]:
    kimdis = connection.execute(
        """
        SELECT
            COUNT(*) AS pde_codes,
            SUM(CASE WHEN complete = 1 THEN 1 ELSE 0 END) AS complete_pde_codes,
            SUM(CASE WHEN complete = 0 THEN 1 ELSE 0 END) AS incomplete_pde_codes
        FROM source_coverage
        WHERE source_name = 'KIMDIS contracts'
        """
    ).fetchone()
    pages = connection.execute(
        """
        SELECT
            COUNT(*) AS requests,
            SUM(CASE WHEN error IS NULL OR error = '' THEN 1 ELSE 0 END)
                AS successful_or_verified_empty,
            SUM(CASE WHEN error IS NOT NULL AND error <> '' THEN 1 ELSE 0 END)
                AS failed_requests
        FROM kimdis_pages
        """
    ).fetchone()
    return {**dict(kimdis), **dict(pages)}


def _exclusions(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS logical_contracts_seen,
            SUM(CASE WHEN resolution_status = 'ambiguous_chain' THEN 1 ELSE 0 END)
                AS ambiguous_chains,
            SUM(CASE WHEN resolution_status = 'missing_successor' THEN 1 ELSE 0 END)
                AS missing_successors,
            SUM(CASE WHEN resolution_status = 'resolved' AND history_complete = 0
                     THEN 1 ELSE 0 END) AS resolved_incomplete_histories,
            SUM(CASE WHEN resolution_status = 'resolved' AND cancelled IS NULL
                     THEN 1 ELSE 0 END) AS resolved_unknown_cancellation_status,
            SUM(CASE WHEN resolution_status = 'resolved' AND current_value_cents IS NULL
                     THEN 1 ELSE 0 END) AS resolved_unknown_values
        FROM current_contracts
        """
    ).fetchone()
    return dict(row)


def _complete_pde_codes(connection: sqlite3.Connection) -> set[str]:
    result: set[str] = set()
    for row in connection.execute(
        """
        SELECT query_description
        FROM source_coverage
        WHERE source_name = 'KIMDIS contracts' AND complete = 1
        """
    ):
        match = re.match(r"PDE=([^;]+);", row["query_description"])
        if match:
            result.add(match.group(1))
    return result


def _contract_record(
    analysis_dir: Path,
    raw_path: str,
    reference_number: str,
    cache: dict[Path, dict[str, KimdisContract]],
) -> KimdisContract:
    path = analysis_dir / raw_path
    if path not in cache:
        contracts, _ = parse_kimdis_contract_page(path.read_bytes())
        cache[path] = {contract.reference_number: contract for contract in contracts}
    try:
        return cache[path][reference_number]
    except KeyError as error:
        raise RuntimeError(f"{reference_number} not found in {path}") from error


def _top_cancelled_contracts(
    connection: sqlite3.Connection,
    analysis_dir: Path,
    complete_pde_codes: set[str],
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            c.contract_id,
            c.current_reference_number,
            c.current_value_cents,
            c.value_basis,
            c.history_complete,
            s.page_content_sha256,
            s.source_url,
            s.raw_path
        FROM current_contracts AS c
        JOIN kimdis_contract_record_sources AS s
          ON s.reference_number = c.current_reference_number
        WHERE c.resolution_status = 'resolved'
          AND c.cancelled = 1
          AND c.current_value_cents IS NOT NULL
        ORDER BY c.current_value_cents DESC, c.current_reference_number
        LIMIT 100
        """
    ).fetchall()
    cache: dict[Path, dict[str, KimdisContract]] = {}
    results: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        contract = _contract_record(
            analysis_dir,
            row["raw_path"],
            row["current_reference_number"],
            cache,
        )
        contractors = []
        for contractor in contract.contractors:
            identifier = asdict(contractor.identifier) if contractor.identifier else None
            contractors.append(
                {
                    "name": contractor.name,
                    "identifier": identifier,
                    "identifier_issue": contractor.identifier_issue,
                }
            )
        if not contract.pde_codes:
            coverage = "unknown_no_pde_on_record"
        elif all(code in complete_pde_codes for code in contract.pde_codes):
            coverage = "complete"
        else:
            coverage = "incomplete"
        results.append(
            {
                "rank": rank,
                "contract_id": row["contract_id"],
                "current_reference_number": row["current_reference_number"],
                "title": contract.title,
                "signed_date": contract.signed_date,
                "current_value_cents": row["current_value_cents"],
                "current_value_eur": _money(row["current_value_cents"]),
                "value_basis": row["value_basis"],
                "history_complete": bool(row["history_complete"]),
                "procedure_code": contract.procedure_code,
                "procedure_label": contract.procedure_label,
                "contractors": contractors,
                "pde_codes": list(contract.pde_codes),
                "ops_codes": list(contract.ops_codes),
                "kimdis_query_coverage": coverage,
                "cancellation_date": contract.cancellation_date,
                "cancellation_reason": contract.cancellation_reason,
                "cancellation_ada": contract.cancellation_ada,
                "decision_related_ada": contract.decision_related_ada,
                "diavgeia_ada": contract.diavgeia_ada,
                "source_url": row["source_url"],
                "raw_path": str((analysis_dir / row["raw_path"]).resolve()),
                "page_content_sha256": row["page_content_sha256"],
            }
        )
    if len(results) != 100:
        raise RuntimeError(f"expected 100 ranked contracts, found {len(results)}")
    return results


def _markdown_escape(value: object) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _contractor_text(contractors: list[dict[str, Any]]) -> str:
    parts = []
    for contractor in contractors:
        identifier = contractor["identifier"]
        suffix = f" ({identifier['value']})" if identifier else ""
        parts.append(f"{contractor['name'] or 'Unnamed'}{suffix}")
    return "; ".join(parts) or "—"


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    primary = report["primary_metrics"]
    strict = report["complete_history_sensitivity"]
    coverage = report["kimdis_source_coverage"]
    exclusions = report["exclusions"]
    lines = [
        "# RRF-linked KIMDIS contract cancellation report",
        "",
        f"Source analysis: `{report['source_analysis_id']}`",
        "",
        "## Headline results",
        "",
        (
            f"- Contract cancellation rate: **{primary['cancelled_contract_percent']}%** "
            f"({primary['cancelled_contracts']:,} / "
            f"{primary['denominator_contracts']:,} resolved current contracts)."
        ),
        (
            f"- Value cancellation rate: **{primary['cancelled_value_percent']}%** "
            f"(€{_money(primary['cancelled_value_cents'])} / "
            f"€{_money(primary['denominator_value_cents'])})."
        ),
        "- All values in the calculation use KIMDIS `totalCostWithVAT`.",
        "",
        "These are observed rates for the resolved RRF-linked KIMDIS records in this run, "
        "not a complete-population estimate for every RRF contract. "
        f"Only {coverage['complete_pde_codes']:,} of {coverage['pde_codes']:,} PDE-code "
        "searches completed without a request gap.",
        "",
        "## Coverage and exclusions",
        "",
        f"- Logical contract chains seen: {exclusions['logical_contracts_seen']:,}",
        f"- Included in headline denominator: {primary['denominator_contracts']:,}",
        f"- Ambiguous chains excluded: {exclusions['ambiguous_chains']:,}",
        f"- Chains with a missing successor excluded: {exclusions['missing_successors']:,}",
        (
            "- Resolved records with incomplete histories: "
            f"{exclusions['resolved_incomplete_histories']:,} "
            "(included in the headline rate and isolated below as a sensitivity check)"
        ),
        (
            "- Resolved records with unknown cancellation status: "
            f"{exclusions['resolved_unknown_cancellation_status']:,}"
        ),
        f"- Resolved records with unknown value: {exclusions['resolved_unknown_values']:,}",
        (
            f"- KIMDIS requests: {coverage['requests']:,}; successful or verified empty: "
            f"{coverage['successful_or_verified_empty']:,}; failed: "
            f"{coverage['failed_requests']:,}"
        ),
        "",
        "## Complete-history sensitivity",
        "",
        (
            f"Requiring a complete amendment history produces **"
            f"{strict['cancelled_contract_percent']}%** cancelled contracts "
            f"({strict['cancelled_contracts']:,} / {strict['denominator_contracts']:,}) "
            f"and **{strict['cancelled_value_percent']}%** cancelled value "
            f"(€{_money(strict['cancelled_value_cents'])} / "
            f"€{_money(strict['denominator_value_cents'])})."
        ),
        "",
        "## Top 100 cancelled contracts by current value",
        "",
        (
            "The ranking uses explicitly cancelled, resolved current records. "
            "`History complete` and `PDE search` expose evidence limitations per row."
        ),
        "",
        "| Rank | KIMDIS reference | Value incl. VAT (€) | Contractors | PDE / OPS | "
        "Cancellation date | ADA | History complete | PDE search | Cancellation reason | Evidence |",
        "|---:|---|---:|---|---|---|---|:---:|---|---|---|",
    ]
    for item in report["top_100_cancelled_contracts"]:
        funding = ", ".join(item["pde_codes"] + item["ops_codes"]) or "—"
        evidence = f"`{item['raw_path']}` (SHA-256 `{item['page_content_sha256']}`)"
        cells = [
            item["rank"],
            item["current_reference_number"],
            item["current_value_eur"],
            _contractor_text(item["contractors"]),
            funding,
            item["cancellation_date"],
            item["cancellation_ada"],
            "yes" if item["history_complete"] else "no",
            item["kimdis_query_coverage"],
            item["cancellation_reason"],
            evidence,
        ]
        lines.append("| " + " | ".join(_markdown_escape(cell) for cell in cells) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = _arguments()
    analysis_dir = args.analysis_dir.resolve()
    output_dir = args.output_dir.resolve()
    database = analysis_dir / "analysis.sqlite3"
    if not database.is_file():
        raise SystemExit(f"analysis database not found: {database}")
    output_dir.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        complete_pde_codes = _complete_pde_codes(connection)
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "source_analysis_id": analysis_dir.name,
            "source_analysis_dir": str(analysis_dir),
            "methodology": {
                "headline_contract_rate": (
                    "cancelled resolved current contracts with known value/status divided "
                    "by all resolved current contracts with known value/status"
                ),
                "headline_value_rate": (
                    "current value of cancelled contracts divided by current value of all "
                    "resolved contracts with known value/status"
                ),
                "state_aid_amounts_included": False,
            },
            "primary_metrics": _metric_set(connection, complete_history=False),
            "complete_history_sensitivity": _metric_set(
                connection, complete_history=True
            ),
            "kimdis_source_coverage": _source_coverage(connection),
            "exclusions": _exclusions(connection),
            "top_100_cancelled_contracts": _top_cancelled_contracts(
                connection, analysis_dir, complete_pde_codes
            ),
        }
    finally:
        connection.close()

    (output_dir / "cancellation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(report, output_dir / "cancellation-report.md")


if __name__ == "__main__":
    main()
