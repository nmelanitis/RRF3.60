# Step 3 — repeated State-aid awards

Implementation and verification date: 2026-11-06.

## Behavior delivered

`assess_repeated_state_aid(awards, config)` in `src/crawld/anomalies.py`
flags an exact official beneficiary identifier with two or more distinct
qualifying awards over the imported export period, including different measures.
Linked, non-linked and unresolved RRF statuses all count. Every contributing ID
and its linkage status are preserved in the assessment facts and evidence IDs.
The route is `state_aid`; this does not change the existing RRF-linked aid-share rule.

Loans, unknown instruments and records excluded by the existing importer policy
do not count. Repeated rows of the same award do not inflate counts, including
rows with different provenance/citations but the same material award facts.
Conflicting material facts are excluded and produce award-level inconclusive
results. Missing beneficiary identifiers also produce award-level inconclusive
results; no name matching occurs. These gaps make otherwise-negative beneficiary
results inconclusive while supported positives retain the limitations.

SQLite and CSV preserve all outcomes. HTML displays flagged and inconclusive
results. Manifest counts are broken down by rule. Aid assessments use the imported
aid-export period and aid-specific coverage, independent of KIMDIS observation
dates or gaps. The count is scoped to the imported export, not exhaustive aid.

## Small steps and tests

| Step | Demonstrated check | Result |
| --- | --- | --- |
| Public rule | One award versus two across measures, including every RRF status | Initial missing-interface failure, then green |
| Exclusions and deduplication | Loan, unknown and excluded records cannot inflate count; repeated source rows count once | Initially incorrectly flagged; corrected and green |
| Incomplete evidence | Conflicting facts, missing identity and partial scope preserve uncertainty; positives survive gaps | Five initial failed assertions, then green |
| Artifact integration | Separate export dates, per-award statuses, SQLite/CSV/HTML/manifest | Initially no assessment row; corrected and green |
| Identity/provenance | Same value under different schemes stays separate; duplicate provenance remains one award | Passed |
| Export coverage | A full export with one award is negative; partial export is inconclusive | Passed |

Focused rule/artifact modules: **26 tests passed**.
Final full repository suite: **154 tests passed in 5.365 seconds**.
Compilation: `.venv/bin/python -m compileall -q src tests tools` passed.
These counts describe this checkpoint; later steps can add tests.

Independent code review approved Step 3 with no actionable findings and ran all
six focused rule/artifact checks successfully. Independent validation replayed
the actual CSV, confirmed the counts and unchanged SHA-256, checked raw
instrument counts, and passed the same six checks. Validation did not finish a
separate reconstruction of every beneficiary's evidence-ID set directly from
raw rows; that stronger check is not claimed as completed.

## Actual user-supplied CSV verification

Input (read-only):
`/Users/nme/Downloads/Transparency_export_13577395_2026-11-03_4432580034483662314.csv`.

SHA-256: `e9bc5d6e60ad7082d3fc3d32d4ff5d82126622da65fe929c55b3fdfc8e9e0b5a`.

The existing real importer read 7,454 rows/awards, 26 columns and 228 measures,
with zero skipped rows, field gaps or duplicate conflicts. Instruments:
6,418 grants, 794 loans, 233 other, three guarantees and six tax advantages.
There are 6,637 identified qualifying awards. An independent grouping/count
produced **1,253 beneficiaries with at least two awards**, matching the rule's
**1,253 flagged results**. There are **3,353 inconclusive results**, including
award-level missing identifiers and below-threshold beneficiaries under an
unverified export scope. Original input hash remained unchanged.

Observed award dates range from 2024-01-11 to 2026-09-29. These are inferred
data bounds, **not verified export search dates**. The diagnostic records
`full_result_export=False`, and its scope/limitations disclose this distinction.
Without additional measure evidence, the importer reports 1,922 linked and
5,532 unresolved awards. No non-linked status is invented.

This is an actual CSV import and rule replay, not a fresh download. A previous
live portal inspection encountered an export form requesting personal details;
no personal data were submitted. The supplied file removes the need to guess
the export schema. It does not establish complete public-aid coverage.

## Reproducible human checks

Run the complete suite from the project directory:

```bash
.venv/bin/python -m unittest discover -s tests -q
```

Replay your actual file without modifying it:

```bash
.venv/bin/python tools/verify_repeated_state_aid.py \
  '/Users/nme/Downloads/Transparency_export_13577395_2026-11-03_4432580034483662314.csv'
```

Inspect the tool's `summary.json`, `assessments.json`, and preserved raw copy in
its printed output directory. Expected: 7,454 awards, 1,253 positives and 3,353
inconclusive results with unverified-scope limitations. Select a positive
beneficiary, locate its cited IDs in the original CSV, and confirm each is a
distinct qualifying award for the same official identifier. Check that its
statuses agree with the persisted facts. Review a loan and confirm it does not
contribute; review a one-award beneficiary and confirm the incomplete-scope
limitation prevents a false absence claim.

Focused checks:

```bash
.venv/bin/python -m unittest -v tests.test_repeated_state_aid \
  tests.test_analysis.AnalysisArtifactTests.test_repeated_state_aid_uses_export_scope_and_persists_linkages \
  tests.test_analysis.AnalysisArtifactTests.test_repeated_state_aid_negative_depends_on_aid_export_coverage
```

For a generated analysis, inspect `red_flag_assessments.csv` or run:

```bash
sqlite3 ANALYSIS_DIR/analysis.sqlite3 "SELECT subject_identifier, outcome, observation_start, observation_end, evidence_record_ids, observed_facts, limitations FROM red_flag_assessments WHERE rule_id='repeated_state_aid';"
```

Follow award IDs to `state_aid_awards` for source URL/row and linkage evidence.
Compare manifest `red_flag_assessment_counts_by_rule.repeated_state_aid` with
the CSV, and confirm HTML shows the positive/inconclusive assessments.

For final user alignment: confirm that two awards across measures and all RRF
statuses are the intended investigative signal, and that loans/unknown
instruments remain excluded. No monetary totals are combined with contracts.
