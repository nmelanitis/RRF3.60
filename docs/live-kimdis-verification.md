# Live KIMDIS verification

## Scope and result

On 2026-11-03 the KIMDIS collector was tested against the official Open Data
API using the validated project reference:

- Ενάριθμος ΠΔΕ: `2024ΤΑ09100006`
- expected ΟΠΣ: `5224393`
- registration-date coverage: `2024-01-01` through `2026-11-03`
- official documentation: <https://cerpp.eprocurement.gov.gr/khmdhs-opendata/help>

The corrected collection passed. It searched six adjacent bounded windows,
preserved every distinct raw response, parsed one contract, and independently
matched that contract reference against the official KIMDIS PDE index. The
machine-readable result is
[summary.json](../data/live-kimdis-verification/20261103T145100Z/summary.json).
The summary records the query, URLs, HTTP statuses, byte counts, hashes, raw
paths, page results, parsed identifiers, and all verification checks.

The verified contract was `26SYMV019324212`. Its live response contained the
expected ΠΔΕ `2024ΤΑ09100006` and ΟΠΣ `5224393`. The parsed values were
EUR 6,619,250.20 with VAT and EUR 5,338,105.00 without VAT. Four consortium
members were retained without assigning the full contract value to each member.

## Real data versus the earlier synthetic data

The synthetic data got several important fields right: the page has `content`
and `totalPages`; the contract has `referenceNumber`, Boolean `cancelled`, the
two cost fields, ΠΔΕ in `publicFundingRefNum`, ΟΠΣ in `publicFundingRefOps`,
and contractor VAT/name fields.

The live API exposed differences that the synthetic data did not exercise:

1. Page zero of a valid window with no matches is HTTP 404, with the exact JSON
   message `No contracts found for the given criteria`; it is not a 200
   response with an empty `content` list. KIMDIS also returns that message for
   an out-of-range later page, so page number is part of the interpretation.
2. A contractor's `country` is an object such as `{"key":"GR","value":"Ελλάδα"}`,
   not the string `"GR"`.
3. Consortium member order changed between otherwise identical live responses.
   Response order therefore cannot be part of contract identity.
4. `fundingDetails` was one object. The parser already supported both an object
   and the list used by the earlier synthetic fixture.
5. The live record reports `greekVatNumber: false` for four nine-digit VAT
   values whose country key is `GR`. The code preserves these conservatively as
   scheme `VAT`, country `GR`; it does not silently recast them as verified
   `EL_VAT` identifiers.
6. The title describes a modification and names an earlier ΑΔΑΜ, but the
   structured `prevReferenceNo` and `nextRefNo` fields are null. The pipeline
   continues to avoid inferring amendment links from title text.

A minimized captured-response excerpt now protects these real field shapes in
[the KIMDIS fixture](../tests/fixtures/kimdis_contract_2024TA09100006_page0_excerpt.json).

## Corrections

The changes are in [kimdis.py](../src/crawld/kimdis.py):

- the exact official no-contracts 404 on page zero is preserved with
  hash/size/status and represented as a verified empty, complete page;
- the same response on page one or later is a preserved source gap, preventing
  a page that disappeared during pagination from being mistaken for complete
  coverage;
- HTTP 429 preserves the available response, waits 60 seconds, and retries the
  identical page request; repeated 429 responses repeat that wait/retry cycle;
- every other 404, 4xx, malformed response, or transport error remains a visible
  source gap; an available error body is preserved;
- country objects are read from their official `key` field;
- contractors are sorted by stable identifier/name fields, so changing API
  array order cannot create conflicting versions or duplicate contracts.

The opt-in verifier is
[verify_live_kimdis.py](../tools/verify_live_kimdis.py). It runs the real
date-window/page collector, verifies every raw file against its recorded hash
and byte count, checks that every parsed contract contains the requested ΠΔΕ,
and compares collected contract references with the independent official
`/pde?pdeNumber=...` index. A difference fails the run unless the operator
explicitly says the chosen dates are deliberately partial.

## Tests executed

The development cycles first reproduced each defect with a failing test, then
implemented the smallest correction:

- official no-data 404 versus a genuine HTTP failure;
- identical no-data text on page zero versus an out-of-range later page;
- live country-object parsing;
- reordered live consortium members producing one stable fact;
- captured live response parsing through the public page-collection seam;
- unexpected 404 response bodies remaining preserved source gaps;
- two consecutive 429 responses each causing a 60-second wait before the same
  page eventually succeeds.

Commands and results:

```shell
uv run python tools/verify_live_kimdis.py \
  --pde '2024ΤΑ09100006' \
  --from-date 2024-01-01 \
  --to-date 2026-11-03 \
  --output data/live-kimdis-verification/20261103T145100Z
# PASS: 6 pages/windows, 1 parsed contract, 1 PDE-index contract

uv run python -m unittest discover -s tests -v
# PASS: 98 tests

uv run python -m compileall -q src tests tools
# PASS
```

An independent reviewer reproduced the no-data 404, country-object, and member
ordering findings. The reviewer also exercised a broader October 2026 query:
KIMDIS reported 27 pages of 50 records, page numbers advanced correctly, and
the first two pages had no overlapping contract references. Page 27, immediately
after the 27 valid zero-based pages, returned the same no-contracts 404 as an
empty page-zero query. This independently confirmed the pagination behavior
and motivated the page-zero-only empty-result rule.

## Manual verification

Use a new output directory because each run is intended to be immutable. Set
the end date to the day you run the test:

```shell
uv run python tools/verify_live_kimdis.py \
  --pde '2024ΤΑ09100006' \
  --from-date 2024-01-01 \
  --to-date YYYY-MM-DD \
  --output data/live-kimdis-verification/MY-UNIQUE-RUN
```

Expected behavior:

1. The command prints `Live KIMDIS verification: PASS` and exits with status 0.
2. In `summary.json`, `success` and all four checks are `true`.
3. Every page has a raw path, byte count, SHA-256, and no error. Empty windows
   may legitimately have HTTP 404 only on page zero, with `total_pages: 0` and
   `is_last_page: true`. The same response on a later page must be an error and
   make coverage incomplete.
4. Each non-empty contract contains the requested ΠΔΕ. For the currently
   verified record, the ΟΠΣ is `5224393`.
5. `contract_references` under `pde_index` and `contract_collection` are equal.
6. Recalculate a raw hash, for example with `shasum -a 256 <raw-file>`, and
   compare it with the hash in the summary and filename.

Then rerun the deterministic suite:

```shell
uv run python -m unittest discover -s tests -v
```

KIMDIS is refreshed daily, so the number and identity of contracts can change.
The reproducible invariant is not “always exactly one contract”; it is complete
page coverage, intact evidence, exact ΠΔΕ linkage, and agreement with the PDE
index for a date range that covers all records. Requests are sequential. When
KIMDIS returns HTTP 429, the collector preserves its response, pauses for 60
seconds, and retries the same page. If the API responds with 429 again, the
collector repeats the wait rather than silently turning the missing page into
zero results. This intentionally has no retry cap, so a persistently unavailable
rate-limited service keeps the process paused until it accepts the request or
the operator interrupts execution. The live source's missing structured
predecessor link and contradictory Greek-VAT flag remain upstream data-quality
limitations rather than facts the pipeline guesses around.
