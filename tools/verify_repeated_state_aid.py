"""Replay a real Greek CSV through the importer and repeated-aid rule.

Observed date bounds are diagnostic only; export completeness is not inferred.
Outputs go to a fresh directory, and the input remains unchanged.
"""

import argparse
from collections import Counter
import csv
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from tempfile import mkdtemp

from crawld.anomalies import RepeatedStateAidRuleConfig, assess_repeated_state_aid
from crawld.state_aid import (
    StateAidCoverage, StateAidExportScope, StateAidImportRequest, import_state_aid_csv,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()
    source = args.csv_path.resolve()
    original_hash = sha256(source.read_bytes()).hexdigest()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    dates = [datetime.strptime(row["Ημερομηνία χορήγησης"], "%d/%m/%Y").date()
             for row in rows if row["Ημερομηνία χορήγησης"]]
    scope = StateAidExportScope(
        country="EL", date_from=min(dates).isoformat(), date_to=max(dates).isoformat(),
        measure_filters=(), full_result_export=False,
    )
    description = "User CSV diagnostic; observed date bounds only; original search filters and completeness unverified"
    output = Path(mkdtemp(prefix="crawld-step3-verification-"))
    imported = import_state_aid_csv(StateAidImportRequest(
        csv_path=source,
        source_url="https://webgate.ec.europa.eu/competition/transparency/public?lang=el",
        imported_at=datetime.now(UTC), measure_evidence=(), export_scope=scope,
        coverage=StateAidCoverage(
            description=description, publication_thresholds=("Vary by legal basis",),
            publication_deadlines=("Vary by legal basis",),
        ),
    ), output / "raw")
    assessments = assess_repeated_state_aid(imported.awards, RepeatedStateAidRuleConfig(
        rule_version="repeated-state-aid-v1", observation_start=scope.date_from,
        observation_end=scope.date_to, observation_scope=description,
        coverage_limitations=("Original export filters and completeness unverified",),
    ))
    result = {
        "source_path": str(source), "sha256": original_hash,
        "scope": asdict(scope), "scope_note": description,
        "source_rows": imported.source_row_count,
        "imported_awards": imported.imported_award_count,
        "skipped_rows": len(imported.skipped_rows), "field_gaps": len(imported.field_gaps),
        "duplicate_conflicts": len(imported.duplicate_conflicts),
        "outcomes": dict(Counter(item.outcome.value for item in assessments)),
        "instrument_counts": dict(Counter(item.instrument.value for item in imported.awards)),
        "rrf_status_counts": dict(Counter(item.rrf_linkage.value for item in imported.awards)),
        "raw_path": str(imported.raw_path),
    }
    if sha256(source.read_bytes()).hexdigest() != original_hash:
        raise RuntimeError("Source CSV changed during verification")
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "assessments.json").write_text(json.dumps([asdict(item) for item in assessments], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Verification output: {output}")


if __name__ == "__main__":
    main()
