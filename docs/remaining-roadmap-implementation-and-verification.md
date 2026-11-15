# Remaining roadmap: implementation, automated tests, and human verification

This document covers the steps implemented after OPS/ΠΔΕ extraction and
snapshot persistence were validated. It explains what the code now does, what
was tested in each step, and what a human should inspect to detect drift from
the intended research behavior.

The central constraint is evidence separation. A Greece 2.0 decision, a
Diavgeia publication event, a KIMDIS contract, and a State-aid award are
different facts. Contract value and aid element are never added into one total.
A completed source snapshot remains immutable; analysis creates a separate
derived artifact that records the source snapshot hashes.

## Steps implemented and automated checks

| Step | Delivered behavior | Automated checks |
| --- | --- | --- |
| 1. Greece 2.0 decision history | Classifies an act's role (`inclusion`, `modification`, `unresolved`) separately from explicit effects (`full_cancellation`, `partial_cancellation`). Cancellation amounts require a local grammatical link and retain funding stream plus page/character evidence locators. | Inclusion fixture; modification plus partial cancellation; full cancellation without false inclusion role; rejected request; cited prior cancellation; unrelated new budget; mixed RRF/national amounts; filename-only unresolved; locator integrity. |
| 2. Diavgeia | Preserves free-text query, URL, time, status, hash, byte count and raw JSON. Administrative-act publication status/version is not treated as legal cancellation. Remote and truncated results are visible gaps; local persistence failures are fatal. | Published/revoked lifecycle; version log; remote failure; result truncation; raw/hash provenance; storage failure. |
| 3. KIMDIS | Searches contracts by exact `publicFundingRefNum` (ΠΔΕ), uses explicit registration-date windows of at most 180 days, paginates, deduplicates windows/pages, preserves contractor identifiers and resolves amendment chains conservatively. | 180/181-day boundary; multi-window dedupe; official Boolean Greek-AFM mapping; invalid AFM; consortium allocation; missing successor; missing predecessor; tri-state cancellation; storage/source failures. |
| 4. State Aid Transparency | Imports a preserved official CSV export without depending on KIMDIS companies. `aid_measure_reference` remains distinct from ΠΔΕ. RRF linkage can be established by an explicit RRF phrase in the official measure name or by authoritative evidence for the exact measure reference. Loans and unknown instruments remain visible but outside aggregates. | Aid-only beneficiary; English and Greek RRF measure names; conflicting evidence; linked/not-linked/unresolved; opaque identifier preservation; invalid Greek AFM; identical/conflicting duplicate IDs; repayable advance, guarantee and unknown instrument policy; non-finite amount gap; raw/hash provenance. |
| 5. Company profiles | Merges only exact `(identifier scheme, country, value)` triples. Keeps contract count/value and aid count/element separate. Historical contractors are not marked current; consortium value shares remain unresolved. | Same name/different AFM; aid-only profile; replaced contractor; duplicate/conflicting inputs; consortium; missing current identifier; loan vs other exclusions; missing amount vs non-EUR. |
| 6. Anomaly flags | Runs an explicit, versioned RRF-linked State-aid share rule over the recorded export scope. Contract values remain in company profiles but do not use one global contract-route denominator. Each aid flag has numerator, denominator, threshold, evidence IDs and explanation. | Contract-only profiles emit no global share flag; RRF-aid threshold boundary; required/valid configuration; contributing evidence IDs; investigation-lead wording. |
| 7. Derived artifact and reports | Produces `analysis.sqlite3`, profile/flag CSVs, escaped HTML, raw evidence copies and `manifest.json`, all referencing immutable source snapshot hashes. Persists catalogue and PDF-extracted project links as separate assertions. | End-to-end artifact; source snapshot hash unchanged; SQLite facts; CSV/HTML output; HTML escaping; manifest hashes; explicit source-gap status. |
| 8. Independent review | A standing reviewer repeatedly ran focused/full suites and adversarial examples. Findings were fixed before later layers used the affected facts. | False cancellation/inclusion, unrelated amount, Diavgeia storage failure, KIMDIS 180-day truncation, Boolean AFM mapping, incomplete amendments, unsupported linkage, duplicate inflation and current-vs-historical attribution all received regression tests. |
| 9. Final integration hardening | Opens completed snapshot databases read-only, confines and verifies source PDF paths/hashes/sizes, rejects outputs inside the source snapshot, publishes artifacts by atomic staging rename, excludes conflicting KIMDIS references, uses source-specific observation periods and actual-value contributor IDs, persists full page/award/measure/import provenance, verifies and hashes raw outputs, neutralizes spreadsheet formulas, and validates configuration types/dates/hashes. | Missing database cannot be created accidentally; escaped/tampered source PDF; tampered imported raw input; injected report failure; same-reference conflict; empty KIMDIS page; mixed historical/non-RRF/non-EUR evidence; partial exports and indeterminate searches; exact gap count; CSV/SQLite metric equivalence; raw manifest hashes; whitespace-prefixed formula text; offline CLI. |

Implementation: [history.py](../src/crawld/history.py),
[diavgeia.py](../src/crawld/diavgeia.py),
[kimdis.py](../src/crawld/kimdis.py),
[state_aid.py](../src/crawld/state_aid.py),
[profiles.py](../src/crawld/profiles.py),
[anomalies.py](../src/crawld/anomalies.py),
[analysis.py](../src/crawld/analysis.py), and [CLI](../src/crawld/cli.py).
Behavior tests are in the correspondingly named files under [`tests/`](../tests/).

## 1. Reproduce the automated checks

From the project directory:

```sh
cd /Users/nme/Documents/pythonProjects/crawlD
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests tools
```

Expected at the time of this document: 97 tests, all `ok`, ending with `OK`.
Compilation prints nothing. The automated suite uses retained fixtures and
simulated responses, so it does not prove that an external service is currently
available or that every future export layout is supported.

## 2. Build a retained offline result you can inspect

```sh
CRAWLD_ANALYSIS_DEMO="$(.venv/bin/python tools/demo_analysis.py)"
export CRAWLD_ANALYSIS_DEMO
printf '%s\n' "$CRAWLD_ANALYSIS_DEMO"
```

The script creates a new temporary directory and leaves it in place. All
KIMDIS and State-aid records in this demo are synthetic alignment data; the
Greece 2.0 PDF is the retained official fixture already used for OPS/ΠΔΕ
verification. Diavgeia is deliberately skipped and recorded as a source gap.

Inspect the manifest and profiles:

```sh
.venv/bin/python -m json.tool "$CRAWLD_ANALYSIS_DEMO/manifest.json"
sqlite3 -header -column "$CRAWLD_ANALYSIS_DEMO/analysis.sqlite3" \
  "SELECT identifier_value, contract_count, current_contract_value_cents,
          aid_award_count, aid_amount_eur_cents, excluded_loan_count
   FROM company_profiles ORDER BY identifier_value;"
```

Expected:

| AFM | Contracts | Current contract value cents | Included aid awards | Aid element cents | Excluded loans |
| --- | ---: | ---: | ---: | ---: | ---: |
| `088888888` | 0 | 0 | 0 | 0 | 1 |
| `094259216` | 1 | 124000 | 1 | 50000 | 0 |
| `099999999` | 0 | 0 | 1 | 90000 | 0 |

This is the most important alignment check: `099999999` appears even though it
has no KIMDIS contract; the loan is visible but excluded; and the company in
both routes has two separate amounts, not `174000` presented as one total.

## 3. Verify decision role, effect and linkage evidence

```sh
sqlite3 -header -column "$CRAWLD_ANALYSIS_DEMO/analysis.sqlite3" \
  "SELECT decision_id, role, unresolved_reason, role_evidence_page,
          role_evidence_start, role_evidence_end
   FROM greece20_decision_interpretations;"
sqlite3 -header -column "$CRAWLD_ANALYSIS_DEMO/analysis.sqlite3" \
  "SELECT ops_code, pde_code, decision_url, link_basis
   FROM project_decision_links ORDER BY link_basis;"
```

Expected role: `inclusion`. The same project/decision is linked through two
independent assertions: `catalogue_row` and `pdf_extracted`. They must not be
silently collapsed into a stronger claim. The demo has no cancellation effect.

For a decision you believe is a cancellation, call the classifier on its saved
PDF and then open the PDF at the reported page:

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
from crawld.history import classify_greece20_decision, extract_greece20_decision_text

pdf = Path('/absolute/path/to/decision.pdf')
result = classify_greece20_decision(extract_greece20_decision_text(pdf.read_bytes()))
print(result)
PY
```

Human check: confirm the operative words really impose the effect. A filename,
budget drop, revoked Diavgeia upload, cited prior act, or rejected request must
not become a cancellation. A stated new budget must not become a cancelled
amount.

## 4. Verify KIMDIS behavior manually

The live command searches KIMDIS using each ΠΔΕ from the snapshot. It splits
the requested interval into adjacent windows of no more than 180 inclusive
days and traverses all reported pages. KIMDIS dates here are registration dates.

After a live run, inspect `source_coverage` and preserved `raw/*.json`:

```sh
sqlite3 -header -column /path/to/analysis.sqlite3 \
  "SELECT query_description, complete, error, coverage_note
   FROM source_coverage WHERE source_name='KIMDIS contracts';"
```

Open a raw request/response record and confirm:

- the query uses `publicFundingRefNum`, not the State-aid measure reference;
- the exact ΠΔΕ is the one you validated in the Greece 2.0 PDF;
- every date window is at most 180 days and adjacent to the next;
- `vatNumber` contains the AFM and `greekVatNumber` is a Boolean flag;
- a later `nextRefNo`/`prevReferenceNo` record is the current amendment;
- a missing successor leaves current value unresolved;
- a missing predecessor leaves a known current value visible but history incomplete;
- a consortium does not assign the full value to every member.

The official KIMDIS help currently documents daily refresh and a limit of 350
requests per minute. HTTP 429 responses are preserved; collection waits 60
seconds and retries the same page, repeating as necessary. Other service
failures must appear as coverage gaps, not zero contracts.

## 5. Verify State Aid Transparency with your own export

Use the Commission's public search to export all Greece results for the
recorded scope, not only the companies already found in KIMDIS. Preserve the
CSV unchanged. Copy [the example configuration](state-aid-config.example.json)
and record the actual search scope and legal-basis coverage notes.

Resolve exact aid-measure references before analysis:

```sh
.venv/bin/crawld collect-state-aid-evidence \
  --state-aid-csv /absolute/path/to/commission-export.csv \
  --state-aid-config /absolute/path/to/state-aid-config.json \
  --commission-dataset data/cache/commission-state-aid/case-data-SA.json \
  --output-root data/state-aid-evidence
```

The Commission cache is downloaded once (currently about 705 MB) and reused.
The scanner extracts only exact `SA.XXXXX` members without loading the complete
dataset in memory. For each unresolved case it checks linked English/Greek
documents and then performs an exact-SA Diavgeia search. During Diavgeia
maintenance, use `--skip-diavgeia`; the skipped fallback remains a coverage
limitation. Inspect the generated `manifest.json`, especially `unresolved`,
`conflict`, and per-reference `gaps`. The input files must remain unchanged.

Use the generated `state-aid-config.resolved.json` below. Manually reviewed
evidence can still be added; its hash must be the SHA-256 of the preserved
source.

Run:

```sh
.venv/bin/crawld analyse \
  --snapshot /absolute/path/to/completed-snapshot \
  --state-aid-csv /absolute/path/to/commission-export.csv \
  --state-aid-config /absolute/path/to/state-aid-config.resolved.json \
  --kimdis-from 2024-01-01 \
  --kimdis-to 2026-10-30 \
  --rrf-aid-share-threshold 0.25 \
  --output-root data/analyses
```

The aid threshold above is an example, not an agreed analytical judgment.
Choose and record a value that matches your intended investigation. Use `--skip-kimdis`
or `--skip-diavgeia` only for a deliberate partial run; the result records the
skip as a gap.

Human checks:

1. Select three CSV rows and compare beneficiary ID, aid element, instrument,
   date, authority and measure reference to `state_aid_awards`.
2. Confirm at least one aid-only company appears if the export contains one.
3. Confirm loans/repayable advances are present with an exclusion reason and
   absent from `aid_amount_eur_cents`.
4. Confirm guarantees remain guarantees; their published aid element is not
   the guarantee's nominal face value.
5. Confirm an English measure name containing `Recovery and Resilience` is
   `linked`; otherwise confirm the Greek measure name containing
   `ΤΑΜΕΙΟ ΑΝΑΚΑΜΨΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ` is `linked`.
6. Confirm an unrelated use of `recovery` stays unresolved. A positive measure
   name conflicting with configured `not_linked` evidence must also remain
   `unresolved`.
7. Confirm an unknown measure stays `unresolved`; absence from the configured
   RRF evidence set is not proof that it is non-RRF.
8. Confirm an opaque non-VAT identifier is unchanged. Invalid Greek AFM values
   must not merge with KIMDIS.
9. In the evidence manifest, confirm every automatic `linked` result cites an
   exact Commission case record, official case attachment, or exact-SA
   Diavgeia result containing an approved phrase. Absence and retrieval errors
   must remain `unresolved`; conflicting positive/negative evidence must be
   `conflict` and produce no resolved config entry.

The Commission states that publication thresholds and deadlines vary by legal
basis. In many cases awards above EUR 100,000 are published, usually within six
months, but exceptions apply. Therefore no report should claim the export is a
complete register of every subsidy.

## 6. Verify profiles, flags and output agreement

```sh
sqlite3 -header -column "$CRAWLD_ANALYSIS_DEMO/analysis.sqlite3" \
  "SELECT rule_id, route, identifier_value, numerator_cents,
          denominator_cents, observed_share, threshold, explanation
   FROM anomaly_flags ORDER BY route;"
open "$CRAWLD_ANALYSIS_DEMO/report.html"
```

Check each flag by hand: divide numerator by denominator and compare it with
the threshold in `manifest.json`. RRF-aid denominators must contain only
included EUR aid elements whose RRF linkage has official evidence. Contract
values must remain visible in company profiles but must not produce a global
contract-share flag. The report must call an aid flag an investigation lead.

Compare `company_profiles.csv`, the SQLite `company_profiles` table and the
HTML table for several companies. Counts and amounts must agree. HTML must
display literal company names safely; it must not execute markup embedded in a
name. CSV text whose first non-whitespace/control character is `=`, `+`, `-`,
or `@` is prefixed with an apostrophe so opening the report in spreadsheet
software does not execute a public-data value as a formula.

## 7. Verify immutability and provenance

The derived manifest records the source snapshot manifest and database hashes.
Recompute them:

```sh
shasum -a 256 /path/to/source-snapshot/manifest.json
shasum -a 256 /path/to/source-snapshot/snapshot.sqlite3
.venv/bin/python -m json.tool /path/to/analysis/manifest.json
```

Expected: hashes match. Re-running analysis creates another directory and does
not alter the completed snapshot. Raw State-aid, KIMDIS and Diavgeia inputs are
copied into the derived artifact by content hash.

Also verify that every file under the analysis `raw/` directory appears in the
manifest's `file_sha256` map and that recalculating its SHA-256 gives the same
value. Before interpretation/copying, the implementation also checks each
source PDF, KIMDIS page, Diavgeia response and State-aid export against its
declared SHA-256 and byte count. Source PDF paths must remain inside the source
snapshot. A failed PDF validation also suppresses its PDF-extracted OPS/ΠΔΕ
assertion while leaving an independent catalogue-row link visible. Successful
KIMDIS/Diavgeia coverage requires preserved raw provenance. The source snapshot database is opened read-only; an analysis output
root inside the source snapshot is rejected.

## 8. Verify source gaps and atomic publication

Inspect all gaps, not only the count:

```sh
sqlite3 -header -column "$CRAWLD_ANALYSIS_DEMO/analysis.sqlite3" \
  "SELECT source_name, query_description, complete, error, coverage_note
   FROM source_coverage ORDER BY coverage_id;"
```

Expected in the offline demo: the deliberately skipped Diavgeia query is
`complete=0` and its error is visible. For a live or deliberately partial run,
confirm the same behavior for a failed Greece 2.0 PDF, truncated/indeterminate
Diavgeia search, incomplete KIMDIS page/history, partial State-aid export,
unresolved RRF linkage, identity gap, or conflicting official ID.
The manifest `source_gaps` number equals the count of rows where
`source_coverage.complete=0`; it is not a separate approximate group count. A
legitimate empty KIMDIS page (`page=0`, `total_pages=0`) is complete when its
raw provenance is present and the request window is otherwise covered.

Successful artifacts appear only after their SQLite, CSV, HTML, raw files,
integrity check and manifest have all completed. A failed build is retained as
a hidden `.failed` staging directory for diagnosis; it is never published
under the normal analysis ID. This can be reproduced by the automated
`test_failed_report_write_never_publishes_final_analysis_directory` regression
test without risking real data.

## 9. Verify aid-anomaly evidence and source dates

```sh
sqlite3 -header -column "$CRAWLD_ANALYSIS_DEMO/analysis.sqlite3" \
  "SELECT route, observation_start, observation_end, observation_scope,
          numerator_cents, denominator_cents, evidence_record_ids
   FROM anomaly_flags ORDER BY route;"
```

There must be no contract-route anomaly flags. For RRF-aid flags, every
evidence ID must identify an included, parsed, nonzero EUR aid element with
official RRF linkage; non-EUR, missing-amount, non-RRF, unresolved and excluded
awards must be absent. KIMDIS dates and
State-aid export dates are recorded independently and may legitimately differ.

## Remaining limitations and alignment questions

- PDF text extraction has no OCR. Scanned decisions remain unresolved.
- Cancellation phrases and amounts are deliberately conservative. A missed
  formulation is preferable to inventing a legal effect, but real examples may
  justify adding reviewed patterns.
- Diavgeia currently warns that search may be limited to ADA during technical
  maintenance. A failed OPS/ΠΔΕ search is a gap, never evidence of no act.
- The State Aid Transparency award interface has no documented public API used
  here; awards enter through a preserved CSV export. Exact measure lookup uses
  the Commission's separate daily open JSON case dataset.
- CSV header aliases cover the documented concepts and current test shapes;
  a changed export can require a reviewed alias update.
- Cross-route identity requires an exact official identifier tuple. Name-only
  similarities are not merged.
- The anomaly rule currently covers only RRF-linked State-aid concentration.
  It does not establish corruption, intent, overpayment or illegality. A future
  contract anomaly requires a defensible comparison group rather than one
  global denominator across all collected KIMDIS contracts.

Please verify especially these intended meanings: legal cancellation versus
publication revocation, aid measure reference versus ΠΔΕ, current contract
value versus payments, aid element versus nominal financing, and source gap
versus a verified zero. Those are the highest-risk places for goal drift.
