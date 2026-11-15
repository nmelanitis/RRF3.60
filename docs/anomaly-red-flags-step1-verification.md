# Funding red flags — Step 1 verification

## Scope

This step implements the direct-award assessment agreed in
`anomaly-red-flags-plan.md`. It does not implement repeated-contract,
repeated-aid, value-increase, threshold-proximity, or possible-splitting rules.
A direct-award flag is an investigation lead, not a finding of wrongdoing.

The implementation preserves three different outcomes for every observed
logical contract:

- `flagged`: the original KIMDIS record has verified procedure code `6`.
- `not_flagged`: the original record has another officially recognized
  procedure code.
- `unable_to_assess`: the necessary original procedure fact is missing,
  unrecognized, contradictory, or conflicts with another retained procedure
  fact. A version conflict unrelated to procedure remains a limitation and
  does not negate reliable procedure evidence.

All three outcomes are retained in SQLite and CSV. The concise HTML report
shows only `flagged` and `unable_to_assess` rows.

## Work performed

### 1. Verify the real KIMDIS field and mapping

The official KIMDIS documentation identifies `procedureType` code `6` as
`Απευθείας ανάθεση` and code `1` as `Ανοιχτή διαδικασία`. It also documents
codes `2`, `4`, `7`, `11`, `12`, `13`, `18`, and `20`.

Three bounded live requests were made for registration date `2026-10-01`, page
zero:

- Code `6`: HTTP 200, 1,039 matching records over 21 pages. All 50 records on
  the inspected page used an object with string key `6` and label
  `Απευθείας ανάθεση`.
- Code `1`: HTTP 200, 173 matching records over four pages. All 50 inspected
  records used key `1` and label `Ανοιχτή διαδικασία`.
- Unfiltered: the inspected page contained 42 code-`6`, six code-`1`, and two
  code-`12` records. Every observed `procedureType` was an object.

The retained live response hashes were:

- Direct query: `bfca1c96f6e8374c2cd09d2dd1fe5872ccdfef353eea0e44908b0ca077c8fcda`
- Open query: `aa22911ca203258c30f9abfa20fe9002a24174d08e143c3dc59a1e4cdd8dffca`
- Unfiltered query: `9fae497f085e6650f11ae84ad132513f7ef6c7aa5542537f97eb0bbfa76b22e5`

The official documentation describes the response field generically as a
string, but its example and every inspected live record returned a
`{"key": ..., "value": ...}` object. The parser follows the observed object
shape without inferring an alternative schema.

Live validation also checked version semantics rather than assuming every page
record was an original contract. `26SYMV019905423` is a verified code-`1` root
record and returns `not_flagged`. `26SYMV019905456` points to predecessor
`26SYMV019577419`; when assessed without that predecessor it correctly returns
`unable_to_assess` rather than a false negative.

### 2. Preserve procedure facts

`KimdisContract` now preserves the exact source procedure code and label.
The previously retained live open-procedure fixture includes its real
`procedureType` object. A second minimized fixture preserves the verified live
direct-award record `26SYMV019905518`.

### 3. Assess the original contract record

`assess_direct_awards(...)` is the public behavior seam. It emits one
`RedFlagAssessment` per logical contract and uses the original/root KIMDIS
record rather than treating an amendment as a new award.

The classifier uses only the reviewed code mapping. It does not infer a direct
award from titles, labels alone, `assignedContract`, `awardProcedure`, contract
value, or contractor identity.

A recognized code with a missing label remains assessable because the official
mapping establishes the code meaning, but the missing label is recorded as a
limitation. A present label that contradicts the official mapping makes the
assessment inconclusive.

### 4. Preserve output and provenance

Each analysis artifact now includes:

- `red_flag_assessments` in `analysis.sqlite3`;
- `red_flag_assessments.csv` with all three outcomes;
- `kimdis_contract_versions` with structured procedure facts;
- `kimdis_contract_record_sources`, linking each cited KIMDIS reference to the
  retained page hash, source URL, and raw artifact path;
- concise HTML rows for positive and inconclusive assessments; and
- manifest counts for total assessments, positive flags, and inconclusive
  assessments.

The existing State-aid share `anomaly_flags` table and CSV remain unchanged.
Before those outputs are written, each successfully retained raw KIMDIS page is
reparsed. Its actual contract identifiers and declared page count must agree
with the in-memory collection result; otherwise artifact creation stops with a
validation error rather than emitting unsupported evidence.

## Tests performed

The first parser behavior test was run before implementation and failed with:

```text
AttributeError: KimdisContract has no procedure_code
```

It passed after the smallest parser change. Subsequent vertical slices cover:

- a captured live direct-award record;
- verified direct and verified non-direct procedures;
- unknown and missing procedure codes;
- a recognized code with a contradictory label;
- a recognized code with a missing label;
- a missing original record;
- conflicting procedure facts;
- unrelated version conflicts retained as limitations;
- collection gaps retained as limitations rather than negating a supported
  positive assessment;
- rejection of any assessed contract that cannot be reconciled with a
  preserved raw KIMDIS page;
- SQLite, CSV, HTML, manifest, and raw-page provenance integration; and
- compatibility with the existing anomaly output.

The independent review identified two failures after the first green suite:
an assessed contract could be supplied outside the preserved page records, and
an unrelated version conflict made a reliable procedure fact inconclusive.
Regression tests were added first and both failed against the intermediate
implementation. The implementation now rejects the unsupported contract input
and treats unrelated conflicts as limitations.

A second provenance review found that matching only the collection and page
objects was insufficient if both differed from the retained raw response. A
new regression test first demonstrated that failure. Successful pages are now
reparsed from their retained JSON, and any raw/page contract-set or declared
page-count mismatch is rejected. A separate integration test reaches the
declared page-count branch with matching contracts but raw `totalPages=2` and
page metadata `total_pages=1`; it verifies rejection without publication.

Verification results on 2026-11-05:

```text
Focused KIMDIS/anomaly/analysis tests: 53 passed
Full test suite: 129 passed
Python compileall: passed
```

## Manual checks

Run these from the project root.

### A. Re-run the complete automated suite

```bash
uv run python -m unittest discover -s tests -v
```

Expected result: `Ran 129 tests` followed by `OK`.

### B. Inspect the captured live direct-award fact

```bash
jq '.content[0] | {referenceNumber, procedureType, prevReferenceNo}' \
  tests/fixtures/kimdis_contract_direct_award_live_excerpt.json
```

Expected result:

- `referenceNumber` is `26SYMV019905518`;
- `procedureType.key` is `6`;
- `procedureType.value` is `Απευθείας ανάθεση`; and
- `prevReferenceNo` is `null`, so this retained record is an original/root
  record rather than a known amendment.

Then run the captured-live behavior test:

```bash
uv run python -m unittest \
  tests.test_anomalies.AnomalyTests.test_captured_live_direct_award_is_flagged_by_the_public_seam \
  -v
```

Expected result: the single test passes.

### C. Check inconclusive behavior

```bash
uv run python -m unittest \
  tests.test_anomalies.AnomalyTests.test_unrecognized_procedure_code_is_unable_to_assess \
  tests.test_anomalies.AnomalyTests.test_code_label_contradiction_is_unable_to_assess \
  tests.test_anomalies.AnomalyTests.test_missing_original_record_is_unable_to_assess \
  -v
```

Expected result: all three tests pass. These are defensive robustness cases;
missing or malformed procedure objects were not observed in the three live
pages and are not presented as observed source behavior.

### D. Inspect a generated analysis artifact

After running the normal analysis command, substitute its output directory for
`ANALYSIS_DIR` and inspect:

```bash
sqlite3 ANALYSIS_DIR/analysis.sqlite3 \
  "SELECT outcome, subject_identifier, evidence_record_ids, explanation FROM red_flag_assessments ORDER BY subject_identifier;"
```

Confirm that direct awards are `flagged`, recognized other procedures are
`not_flagged`, and incomplete facts are `unable_to_assess`.

For any cited ΑΔΑΜ, verify its structured procedure fact and raw-page link:

```bash
sqlite3 ANALYSIS_DIR/analysis.sqlite3 \
  "SELECT v.reference_number, v.procedure_code, v.procedure_label, s.page_content_sha256, s.source_url, s.raw_path FROM kimdis_contract_versions v JOIN kimdis_contract_record_sources s USING(reference_number) WHERE v.reference_number='REPLACE_WITH_ADAM';"
```

Finally, compare `red_flag_assessments.csv` with `report.html`: the CSV should
retain negative rows, while the HTML should show positive and inconclusive rows
without listing `not_flagged` rows.

## Remaining limitations and next checkpoint

- Live validation covered bounded page-zero responses, not every KIMDIS record.
- The procedure mapping is pinned to the official documentation reviewed for
  this step. A future undocumented code remains `unable_to_assess` until it is
  reviewed.
- This step does not determine legal thresholds or whether an award was lawful.
- The next proposed step is the repeated-contract rule. Its exact interface and
  coverage behavior require a separate alignment checkpoint before coding.
