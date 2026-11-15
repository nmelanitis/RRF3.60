# Direct-award red flag: implementation and verification

This is Step 1 of the approved funding-red-flags plan. It assesses each
logical KIMDIS contract once, from its original/root source record. The result
is an investigation lead, not a finding of illegality, wrongdoing, or intent.

## Implemented behavior

KIMDIS `procedureType.key` and `procedureType.value` are preserved on every
parsed contract version. The procedure mapping is pinned to the official
[KIMDIS Open Data help](https://cerpp.eprocurement.gov.gr/khmdhs-opendata/help):

| Code | Official label | Direct-award result |
| --- | --- | --- |
| `1` | Ανοιχτή διαδικασία | not flagged |
| `2` | Κλειστή διαδικασία | not flagged |
| `4` | Ανταγωνιστικός διάλογος | not flagged |
| `6` | Απευθείας ανάθεση | flagged |
| `7` | Ανταγωνιστική διαδικασία με διαπραγμάτευση | not flagged |
| `11` | Σύμπραξη καινοτομίας | not flagged |
| `12` | Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση | not flagged |
| `13` | Διαπραγμάτευση με προηγούμενη προκήρυξη διαγωνισμού (αρ.266) | not flagged |
| `18` | Διαδικασία άρθρου 128 του ν.4412/16 | not flagged |
| `20` | Διαδικασία άρθρου 119 του ν.4412/16 | not flagged |

A missing, non-object, or unrecognized procedure is `unable_to_assess`. A
present label that differs from the official label for its code is also
inconclusive. A recognized code with a missing label remains assessable and
records that omission as a limitation.

Amendments do not become new awards. The rule uses the original/root record;
a missing root or conflicting procedure facts across versions is
`unable_to_assess`. A conflict in unrelated version facts is disclosed as a
limitation but does not negate a consistent, reliable procedure fact.
Incomplete collection coverage is handled the same way.

All outcomes (`flagged`, `not_flagged`, and `unable_to_assess`) are stored in
`red_flag_assessments` and `red_flag_assessments.csv`. The concise HTML report
shows flags and inconclusive assessments, omitting routine negative rows.
`anomaly_flags` remains unchanged for the existing State-aid share rule.

The analysis database also stores:

- `kimdis_contract_versions`, containing the structured procedure facts; and
- `kimdis_contract_record_sources`, linking each record reference to the
  preserved raw-page SHA-256, source URL, and artifact-relative raw path.

The manifest records assessment, flagged, and inconclusive counts.

## Automated verification

Development followed red to green through the public parser, assessment, and
analysis-artifact seams. Tests cover a verified direct procedure, recognized
non-direct procedures, missing/unrecognized data, code/label contradiction,
missing labels, missing original records, conflicting and ambiguous versions,
collection gaps, SQLite/CSV/HTML/manifest output, and record-to-raw-page
provenance.

The minimized fixture
[`kimdis_contract_direct_award_live_excerpt.json`](../tests/fixtures/kimdis_contract_direct_award_live_excerpt.json)
comes from a retained live KIMDIS page-zero response and contains only the
fields needed to reproduce code `6`, label `Απευθείας ανάθεση`, and the source
record reference. The competitive code `1` fixture remains in
[`kimdis_contract_2024TA09100006_page0_excerpt.json`](../tests/fixtures/kimdis_contract_2024TA09100006_page0_excerpt.json).
The official documentation establishes the complete mapping above; only codes
`1` and `6` have been exercised by retained live labels in this step, and a
code-`12` object shape was observed live. The other official labels are not
claimed as live examples.

Reproduce the automated checks:

```sh
cd /Users/nme/Documents/pythonProjects/crawlD
uv run python -m unittest tests.test_kimdis tests.test_anomalies tests.test_analysis
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests tools
```

Expected for this step: the full suite ends with `OK`; compilation prints
nothing.

## Manual verification on an analysis artifact

Run the normal `crawld analyse` command with live KIMDIS collection enabled,
then set `ANALYSIS_DB` to the generated database path.

```sh
ANALYSIS_DB=/absolute/path/to/analysis.sqlite3

sqlite3 -header -column "$ANALYSIS_DB" \
  "SELECT rule_id, outcome, subject_identifier, observed_facts,
          evidence_record_ids, explanation, limitations
   FROM red_flag_assessments
   ORDER BY subject_identifier;"

sqlite3 -header -column "$ANALYSIS_DB" \
  "SELECT contract_id, reference_number, procedure_code, procedure_label
   FROM kimdis_contract_versions
   ORDER BY contract_id, reference_number;"

sqlite3 -header -column "$ANALYSIS_DB" \
  "SELECT reference_number, page_content_sha256, source_url, raw_path
   FROM kimdis_contract_record_sources
   ORDER BY reference_number;"
```

For one result, verify all of the following:

1. `subject_identifier` is the logical root contract ID and appears only once.
2. `evidence_record_ids` identifies the source version used or the conflicting
   versions that prevented assessment.
3. The code and label exactly match the preserved raw JSON. Code `6` flags;
   the recognized codes in the table above do not.
4. The source row's SHA-256 and raw path resolve to the same preserved page.
   Recalculate it with `shasum -a 256 /absolute/path/to/raw-file.json`.
5. A missing/unrecognized procedure or code/label disagreement is
   `unable_to_assess`, never silently `not_flagged`.
6. `report.html` contains flagged and inconclusive rows, while
   `red_flag_assessments.csv` contains all three outcomes.
7. `manifest.json` counts agree with the CSV and SQLite rows.

This check validates the procedure classification and provenance. It does not
establish that a direct award was unlawful or that every contract in Greece is
inside the chosen ΠΔΕ/date collection scope.
