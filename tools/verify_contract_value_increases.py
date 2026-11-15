"""Replay retained KIMDIS JSON pages through the contract-value rule."""

import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from tempfile import mkdtemp

from crawld.anomalies import ContractValueIncreaseRuleConfig, assess_contract_value_increases
from crawld.kimdis import parse_kimdis_contract_page, select_current_contract_versions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pages", nargs="+", type=Path)
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    args = parser.parse_args()
    records, sources = [], []
    for path in args.pages:
        payload = path.read_bytes()
        parsed, _ = parse_kimdis_contract_page(payload)
        records.extend(parsed)
        sources.append({"path": str(path.resolve()), "sha256": sha256(payload).hexdigest()})
    contracts = select_current_contract_versions(records)
    assessments = assess_contract_value_increases(contracts, ContractValueIncreaseRuleConfig(
        rule_version="contract-value-increase-v1",
        observation_start=args.date_from, observation_end=args.date_to,
        observation_scope="Explicitly supplied retained KIMDIS pages; not a complete collection",
        coverage_limitations=("Retained page sample; collection completeness not established",),
    ))
    output = Path(mkdtemp(prefix="crawld-step4-verification-"))
    report = {"sources": sources, "records": len(records), "logical_contracts": len(contracts),
              "outcomes": dict(Counter(a.outcome.value for a in assessments))}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "assessments.json").write_text(json.dumps([asdict(a) for a in assessments], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Verification output: {output}")


if __name__ == "__main__":
    main()
