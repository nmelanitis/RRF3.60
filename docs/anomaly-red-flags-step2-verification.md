# Step 2: repeated contracts — implementation and verification

Verified on 2026-11-06. Step 2 is complete; later anomaly rules are not included.
User acceptance testing is deferred until the end, as requested.

## Delivered behavior

`assess_repeated_contracts` in `src/crawld/anomalies.py` groups original
contracts by exact official identifier (scheme, country, value). Defaults are
five distinct contracts in 90 consecutive calendar days: a date span of 89
days qualifies, while 90 does not. Settings are configurable through
`AnomalyConfig.repeated_contract_minimum` and `repeated_contract_window_days`.
This is the agreed investigative heuristic, not a legal or KIMDIS rule.

Original signed dates determine timing. Amendments do not add events.
Each identified consortium member receives one event for the contract.
Overlapping qualifying event windows merge; separated clusters remain separate.
A merged cluster may span more than 90 days because its constituent qualifying
windows overlap; its explanation and observed dates disclose that span.

Missing/invalid dates make a company's otherwise-negative assessment
`unable_to_assess`. Missing roots, conflicting root facts, wholly missing
identifiers and partially unidentified consortium members have contract-level
inconclusive results. Unresolved attribution also limits company assessments:
below-threshold observations remain inconclusive; supported positives remain
flagged with the gap disclosed. Companies are never merged by name.

The analysis integration preserves all outcomes in `red_flag_assessments`
in SQLite and CSV, displays positives and inconclusive results in HTML, and
records per-rule outcome counts and settings in the manifest. Source records
remain traceable through the existing raw-page provenance tables.

## Implementation slices and checks

| Slice | Checks performed | Result |
| --- | --- | --- |
| Core count and dates | Four/five contracts; day 89/day 90; original dates | Passed |
| Window grouping | Overlapping windows; separated clusters | Passed |
| Identity and versions | Amendments; exact identifier schemes; consortium members | Passed |
| Missing evidence | Missing/invalid dates; missing roots; conflicting roots; missing identifiers; incomplete collection | Passed |
| Real response replay | Five real root contracts; real root/amendment pair | Passed |
| Artifact integration | SQLite/CSV/HTML/manifest; non-default three-contract/30-day settings | Passed |
| Reviewer correction | Partial consortium uncertainty; negative becomes inconclusive, positive retains limitation, identified member retains event | Failed before fix, passed after fix |

Developer used targeted red/green tests. The final reviewer regression was
observed failing twice: four contracts incorrectly returned `not_flagged`, and
five contracts omitted the identity limitation. Both assertions pass after the
correction. Independent review approved the final correction; independent
validation passed its 11-check matrix and compared all retained fixture fields
with the original real responses.

Final full suite: **148 tests passed**, 4.843 seconds.
Compilation: `.venv/bin/python -m compileall -q src tests tools` passed.
The `uv run` compilation attempt encountered a cache permission error; the
same compilation succeeded directly through the configured project interpreter.

## Real-data evidence and limits

These checks replay retained live responses; no fresh API call was made in
Step 2. The responses were collected during Step 1. Minimized fixtures preserve
source fields without manufacturing company/date values.

`tests/fixtures/kimdis_repeated_contracts_live_excerpt.json` contains five
original contracts for `VAT:GR:082525697`, signed on 2026-10-01:

- `26SYMV019903858`
- `26SYMV019905246`
- `26SYMV019905349`
- `26SYMV019905379`
- `26SYMV019905387`

The public rule flags these exact five IDs. Original retained response SHA-256:
`bfca1c96f6e8374c2cd09d2dd1fe5872ccdfef353eea0e44908b0ca077c8fcda`.

`tests/fixtures/kimdis_contract_amendment_chain_live_excerpt.json` contains
root `26SYMV019905416` and amendment `26SYMV019905426` for
`VAT:GR:998347888`. The rule counts one contract and cites the root.

Boundary and malformed-input tests deliberately use synthetic records; those
are robustness checks, not claims about observed source prevalence. Bounded
retained pages do not establish complete KIMDIS coverage. Observation metadata
describes registration-date collection scope; the event windows use signed dates.

## Human verification at the final acceptance checkpoint

From the project root, rerun:

```bash
uv run python -m unittest discover -s tests -q
```

Expected at this checkpoint: 148 tests and `OK` (later steps may add tests).

Inspect the real positive fixture manually:

```bash
jq '.content[] | {referenceNumber, prevReferenceNo, contractSignedDate, contractingDataDetails}' tests/fixtures/kimdis_repeated_contracts_live_excerpt.json
```

Confirm five distinct root IDs, no predecessor, the same exact contractor
identifier and dates inside the window. Then run:

```bash
uv run python -m unittest -v \
  tests.test_anomalies.AnomalyTests.test_captured_live_repeated_contracts_flag_exact_official_identifier \
  tests.test_anomalies.AnomalyTests.test_captured_live_amendment_chain_counts_original_contract_once \
  tests.test_anomalies.AnomalyTests.test_ninetieth_calendar_day_is_inside_and_next_day_is_outside \
  tests.test_anomalies.AnomalyTests.test_partial_consortium_identity_preserves_events_and_discloses_gap \
  tests.test_analysis.AnalysisArtifactTests.test_repeated_contract_assessment_uses_config_and_is_persisted
```

Expected: five tests pass. For a generated analysis, inspect:

```bash
sqlite3 ANALYSIS_DIR/analysis.sqlite3 "SELECT subject_type, subject_identifier, outcome, observed_facts, evidence_record_ids, limitations FROM red_flag_assessments WHERE rule_id='repeated_contracts';"
```

Compare these rows with CSV and HTML. CSV includes all outcomes; HTML includes
flagged and inconclusive rows. Inspect manifest
`red_flag_assessment_counts_by_rule.repeated_contracts` and `anomaly_config`.
For each supporting root ID, follow `kimdis_contract_record_sources.raw_path`
and verify the signed date and contractor identifier in the preserved JSON.

Alignment questions for final acceptance: Is five/90 useful? Does combining
overlapping windows give the intended concise report? Should unresolved identity
coverage remain conservatively inconclusive across the observed scope? These
are documented behavior, not claims of wrongdoing.
