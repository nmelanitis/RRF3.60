# Greece 2.0 decision PDFs in snapshots: implementation and human verification

This document covers the agreed snapshot integration step. It connects the
previously validated PDF collector to durable evidence storage. It does not
claim completion of the wider company funding or cancellation analysis goals.

## Expected behavior and goal alignment

The snapshot contains catalogue rows, the decisions linked from those rows,
and extracted OPS/ΠΔΕ evidence. A decision remains identifiable by source URL
and content hash. Extracted identifiers retain their decision link. Catalogue
OPS values are preserved independently: a PDF/catalogue discrepancy is not
silently resolved by overwriting either source.

`RrfProjectFundingReference` is an RRF project's ΠΔΕ reference, and
`CollectedGreece20Decision` is a Greece 2.0 decision. These names do not imply
State-aid measures or general Diavgeia acts. No company recipient, contract,
aid award, payment, cancellation or anomaly is inferred in this step.

`complete` means the run finished and recorded its source gaps. It does not
mean every PDF was retrieved, every identifier extracted, or every funding
source searched. A local persistence failure makes the run `incomplete` where
that status can be saved; otherwise an exception surfaces the failure.

## Small steps followed and checks

Tests use `collect_snapshot(SnapshotRequest)` and the CLI. SQLite, the JSON
manifest and raw files are delivered outputs, so their contents are part of
the behavior being verified. Normal processing uses real local files and the
real PDF parser; HTTP and filesystem/database failures are simulated at the
external operations. No live Greece 2.0 download was used for this verification.

| Step | Implementation | Tests and result |
| --- | --- | --- |
| 1. Persist selected PDF evidence | Select distinct URLs from included catalogue rows; save bytes by SHA-256; store decisions and OPS/ΠΔΕ references in SQLite. | New test initially failed because result counts did not exist; passed after integration. Checks one shared URL, two retained catalogue rows, cutoff exclusion, byte equality, timestamp, hash, size, path, independent PDF OPS and manifest counts. |
| 2. Make failures and recovery durable | Commit catalogue and each decision separately; roll back the current failed write; retain prior successes; reset child records before parents on resume. | New storage test initially failed because the result reported zero included rows; DB-open test initially raised without updating the manifest. Both pass after changes. Resume produces two decision records and two evidence references without duplicates or foreign-key violations. |
| 3. Verify source gaps | Preserve corrupt bytes with their provenance; record missing sources; keep successfully parsed PDFs with zero identifiers. | Missing-first/valid-later/corrupt-last integration test passes: 3 URLs, 1 collected, 2 failed, 1 reference. Blank PDFs produce collected decisions with zero references. Completed snapshots reject resume. These were additional coverage tests and passed on their first run. |
| 4. Expose results through CLI | Print URL, collected, failed and reference counts; report PDF gaps on stderr. | CLI test failed before output was added, then passed. A completed run with source gaps returns exit code 0 and visibly reports the gaps. |
| 5. Address independent review | Publish counts only after commit; distinguish successful catalogue retrieval/parsing from database failure; close a connection if schema setup fails. | Reviewer reproduced rolled-back catalogue rows being reported as included. Regression checks now confirm committed counts; a separate failing regression exposed the incorrect catalogue failure label and passed after correction. A second-reference-write failure retains exactly the first decision/reference. |
| 6. Deliver reproducible human checks | Update README and provide `tools/demo_snapshot.py` plus this guide. | The offline demonstration was executed successfully and retained for inspection. Commands and expected results below permit a separate human check. |

Implementation: [snapshot.py](../src/crawld/snapshot.py),
[CLI](../src/crawld/cli.py), [PDF collection](../src/crawld/decisions.py),
[identifier extraction](../src/crawld/funding.py).
Tests: [snapshot behavior tests](../tests/test_snapshot.py),
[batch collector tests](../tests/test_decisions.py),
[identifier test](../tests/test_funding.py).

## 1. Reproduce the automated checks

From the project directory:

```sh
cd /Users/nme/Documents/pythonProjects/crawlD
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests tools
```

Use `uv sync` first if the project environment is missing. The tests do not
need internet access once dependencies are installed. Expected: every test
reports `ok`, the suite ends with `OK`, and compilation prints no errors.

For focused recovery and transaction checks:

```sh
.venv/bin/python -m unittest -v \
  tests.test_snapshot.SnapshotTests.test_storage_failure_retains_progress_and_resume_rebuilds_without_duplicates \
  tests.test_snapshot.SnapshotTests.test_resume_reset_failure_reports_the_still_committed_counts \
  tests.test_snapshot.SnapshotTests.test_catalogue_database_failure_reports_only_committed_counts \
  tests.test_snapshot.SnapshotTests.test_reference_write_failure_rolls_back_only_current_decision
```

These are repeatable automated fault-injection checks, not independent manual
proof of behavior under every operating-system or SQLite failure.

## 2. Create evidence you can inspect yourself

```sh
CRAWLD_DEMO="$(.venv/bin/python tools/demo_snapshot.py)"
export CRAWLD_DEMO
printf '%s\n' "$CRAWLD_DEMO"
```

The script creates a new temporary directory and leaves all generated inputs
and outputs there. It does not modify existing snapshots or original fixtures.
It uses a synthetic catalogue and a copy of the previously validated official
PDF fixture. The same PDF is deliberately linked twice. Other rows refer to a
missing file, an empty corrupt file, no PDF link, and a post-cutoff PDF.

Open the printed snapshot folder in Finder. Its parent-of-parent contains the
synthetic `catalogue.html`, `good.pdf`, `corrupt.pdf` and `after-cutoff.pdf`.
Open that catalogue and count its six rows yourself. `missing.pdf` should not
exist. This is a test dataset, not a representation of six real projects.

## 3. Check cutoff, counts and gaps

```sh
.venv/bin/python -m json.tool "$CRAWLD_DEMO/manifest.json"
sqlite3 -header -column "$CRAWLD_DEMO/snapshot.sqlite3" \
  "SELECT metric_name, metric_value FROM collection_metrics ORDER BY metric_name;"
sqlite3 -header -column "$CRAWLD_DEMO/snapshot.sqlite3" \
  "SELECT source_url, status, byte_count, error FROM greece20_decisions ORDER BY decision_id;"
```

Expected:

| Item | Value |
| --- | --- |
| status | complete |
| entries_seen | 6 |
| entries_included | 5 |
| entries_after_cutoff | 1 |
| entries_without_inclusion_date | 1 |
| entries_without_decision_url | 1 |
| decision_urls_found | 3 |
| decisions_collected | 1 |
| decisions_failed | 2 |
| funding_references_extracted | 1 |

The missing file is failed without raw bytes or a retrieval timestamp. The
empty file is failed with byte count 0, hash, retrieval timestamp and raw path.
The good file is collected. Error wording for missing files can vary by OS.
There must be no decision record for `after-cutoff.pdf`.

Human alignment question: does “complete with two explicitly reported PDF
gaps” convey enough information for your intended research workflow? An absent
source must never become evidence of no funding or cancellation.

## 4. Verify both catalogue rows still link to the one decision

```sh
sqlite3 -header -column "$CRAWLD_DEMO/snapshot.sqlite3" \
  "SELECT c.catalogue_sequence, c.ops_code AS catalogue_ops, d.status,
          f.ops_code AS pdf_ops, f.pde_code
   FROM rrf_catalogue_entries c
   LEFT JOIN greece20_decisions d
     ON d.snapshot_id=c.snapshot_id AND d.source_url=c.decision_url
   LEFT JOIN rrf_project_funding_references f ON f.decision_id=d.decision_id
   ORDER BY c.source_row_index;"
```

Expected: five catalogue rows. Rows 2 and 3 link to the same collected decision
and show OPS `5224393` / ΠΔΕ `2024ΤΑ09100006`. Row 5 has no linked decision.
This join repeats the same evidence reference twice; it is still only one row
in `rrf_project_funding_references`. Do not sum joined rows as distinct projects
or funding awards.

Human alignment question: is it clear that catalogue rows can be versions or
repeated references, while a PDF URL is collected once per run?

## 5. Open the preserved PDF and verify its provenance

```sh
sqlite3 -header -column "$CRAWLD_DEMO/snapshot.sqlite3" \
  "SELECT source_url, retrieved_at, content_sha256, byte_count, raw_path
   FROM greece20_decisions WHERE status='collected';"
.venv/bin/python - <<'PY'
from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import sqlite3

root = Path(os.environ['CRAWLD_DEMO'])
original = Path('tests/fixtures/greece20_5224393_inclusion.pdf').read_bytes()
with closing(sqlite3.connect(root / 'snapshot.sqlite3')) as db:
    digest, size, relative_path = db.execute(
        "SELECT content_sha256, byte_count, raw_path "
        "FROM greece20_decisions WHERE status='collected'"
    ).fetchone()
payload = (root / relative_path).read_bytes()
print('Open this PDF:', root / relative_path)
print('Exact original bytes:', payload == original)
print('Recorded hash matches:', sha256(payload).hexdigest() == digest)
print('Recorded size matches:', len(payload) == size)
PY
```

Expected: all three checks print `True`. Open the printed PDF in Preview,
search for `5224393`, and locate the ΠΔΕ reference in its text. Confirm the
identifiers yourself against the SQLite result. The retrieval time should be
the current run time, even though the catalogue cutoff is in 2024.

Human alignment question: does this provide a clear path from an extracted
identifier back to the exact official evidence you need to inspect?

## 6. Check a real decision of your choice, without collecting the full site

For additional independent verification, use a Greece 2.0 decision PDF you
download manually and whose OPS/ΠΔΕ you can read. Supply its absolute path and
the OPS you want the synthetic catalogue to display:

```sh
CRAWLD_REAL_CHECK="$(.venv/bin/python tools/demo_snapshot.py \
  --pdf /absolute/path/to/your-greece20-decision.pdf \
  --catalogue-ops 1234567)"
export CRAWLD_REAL_CHECK
printf '%s\n' "$CRAWLD_REAL_CHECK"
sqlite3 -header -column "$CRAWLD_REAL_CHECK/snapshot.sqlite3" \
  "SELECT d.status, f.ops_code, f.pde_code
   FROM greece20_decisions d
   LEFT JOIN rrf_project_funding_references f USING (decision_id)
   WHERE d.status='collected';"
```

Replace the PDF path and `1234567` with your values. The helper creates a new
temporary snapshot, leaving existing data untouched. Compare the stored OPS and
ΠΔΕ to your own reading of the PDF, and report any mismatch.

A valid PDF with no unambiguous extractable pair is collected with zero
references. Scanned PDFs have no OCR support. Multiple OPS codes are left
unresolved. Zero references is not a verified absence of a ΠΔΕ reference.

## Limits and decisions to validate before the next feature

- The cutoff uses catalogue inclusion dates. Undated rows are included and
  counted. It does not filter facts inside PDFs by date or recover historical
  versions of files currently served at those URLs.
- Exact URL strings are deduplicated. Different URLs serving identical bytes
  get separate decision records but share one raw file. Relative URLs are not
  resolved by the existing parser.
- PDFs are downloaded sequentially. There is no OCR, automatic retry policy,
  progress display or configurable PDF subset in the production CLI yet.
- Resume rebuilds normalized records and re-fetches sources. It is not a
  skip-successes retry. Existing complete snapshots remain unchanged; create a
  new snapshot to retry their gaps. Unreferenced raw PDF files can remain after
  an interrupted or resumed attempt; consult SQLite for recorded evidence.
- Raw PDF writes are not atomic. A filesystem that cannot persist the failure
  status can leave a run marked running; the exception is the failure signal.
- A failure before catalogue persistence reports zero committed catalogue
  counts. An incomplete run can have found URLs greater than collected plus
  failed: the remainder were not durably processed.
- Contract counts/value and State-aid counts/amounts remain separate future
  concepts. This step supports their future linkage using OPS/ΠΔΕ; it does not
  search KIMDIS, State Aid Transparency or Diavgeia, classify cancellations,
  identify companies by ΑΦΜ, or infer payments.

Please report any disagreement about these meanings, especially completeness,
cutoff interpretation, unresolved identifiers and evidence linkage. Those are
alignment decisions; a green automated suite alone cannot decide them for you.
