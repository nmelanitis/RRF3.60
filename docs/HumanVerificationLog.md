# Human Verification Log

This document records the implementation changes, live-source checks, and
human verification completed for the crawlD analysis workflow through
2026-11-04.

## Major changes

### Live KIMDIS testing and verification

- Queried the official KIMDIS API using exact ΠΔΕ references.
- Verified date-window splitting, pagination, amendment data, contract values,
  contractor VAT numbers, consortium members, OPS references, and ΠΔΕ
  references.
- Corrected handling of official `404` no-contract responses so legitimate
  empty windows do not make coverage incomplete.
- Confirmed that source failures remain visible coverage gaps.

### European Commission State Aid Transparency CSV integration

- Added support for the current Greek column names.
- Mapped `Εθνικό αναγνωριστικό ταυτότητας` to Greek VAT/AFM after
  external verification.
- Preferred the Greek beneficiary name over the English version.
- Verified beneficiary, identifier, aid element, currency, instrument,
  granting date, authority, and `SA.XXXXX` reference parsing.
- Confirmed that loans remain visible but are excluded from aid aggregates.
- Confirmed that guarantees retain their published aid element rather than
  being treated as their nominal financing value.
- Confirmed that aid-only companies are imported independently of KIMDIS
  contractors.

### European Commission State Aid case dataset integration

- Integrated the Commission's daily State Aid case JSON dataset. This is not
  the award database: individual awards still come from the State Aid
  Transparency CSV.
- Implemented bounded-memory streaming lookup over the approximately 705 MB
  dataset without loading the complete file into memory.
- Implemented automatic download, local caching, and reuse.
- Scanned the complete dataset and found all `228/228` exact `SA.XXXXX`
  references from the supplied CSV.
- Preserved dataset byte count and SHA-256 provenance.
- Added lookup of linked English and Greek Commission case attachments and
  PDFs.
- Added immutable evidence manifests and resolved State-aid configuration
  output.

### Automated RRF-linkage evidence collection

- Searches English measure names for `Recovery and Resilience`.
- Searches Greek measure names and official PDFs for both:
  - `ΤΑΜΕΙΟ ΑΝΑΚΑΜΨΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ`
  - `ΤΑΜΕΙΟ ΑΝΑΠΤΥΞΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ`, the reviewed Commission
    misnomer.
- Supports exact-SA Diavgeia fallback searches when available.
- Leaves retrieval failures, missing phrases, and skipped sources
  `unresolved`.
- Does not treat absence of positive evidence as automatic `not_linked`
  evidence.
- Records conflicting positive and negative evidence as `conflict` rather
  than silently selecting one result.

## Minor changes

### Anomaly model correction

- Removed the unrealistic global contract-value share anomaly.
- Kept contract values available in company profiles without producing a
  global contract-route concentration flag.
- Limited the current anomaly rule to qualifying RRF-linked State-aid
  elements.
- Added source-specific observation dates and scope to flags.
- Recorded the company identifier, numerator, denominator, threshold, and
  contributing award IDs for each flag.
- Confirmed that anomaly flags are investigation leads, not findings of
  wrongdoing.

### End-to-end integrity and provenance verification

- Verified the source snapshot manifest and database hashes.
- Verified every generated SQLite, CSV, HTML, and raw file against the
  analysis manifest.
- Confirmed that skipped Diavgeia collection is recorded as a source gap
  rather than zero results.
- Confirmed that anomaly evidence IDs resolve to qualifying State-aid awards.
- Confirmed that incomplete builds cannot be published as normal completed
  analyses.

### HTTPS certificate handling

- Added `certifi` and an explicit trusted HTTPS context.
- Fixed Python's macOS `CERTIFICATE_VERIFY_FAILED` errors for Commission PDFs.
- Kept certificate verification and hostname verification enabled; no
  insecure bypass was introduced.

### Parsing, identity, and evidence details

- Added Greek and English CSV header aliases for Commission exports.
- Added exact company identity handling using the tuple
  `(identifier_scheme, identifier_country, identifier_value)`.
- Prevented company-name-only identity merges.
- Preserved opaque non-VAT identifiers rather than rewriting them as VAT
  numbers.
- Kept `aid_measure_reference`, ΠΔΕ, OPS, and VAT identifiers as separate
  concepts.
- Distinguished `catalogue_row` and `pdf_extracted` Greece 2.0 linkage
  assertions.
- Improved coverage reporting for incomplete KIMDIS, Diavgeia, State-aid,
  and document-retrieval operations.

### Documentation and tests

- Documented what the Commission CSV contains and does not contain.
- Documented the Commission export link and State-aid evidence collection.
- Added human-verification commands, known limitations, and unresolved-source
  policies.
- Expanded the automated suite to 116 passing tests.

## Verified live examples

- Retrieved KIMDIS contract `26SYMV019324212` and manually compared it with
  the live response.
- Linked `SA.111393` through an official Commission decision containing
  `Recovery and Resilience`.
- Linked `SA.120841` through its official Commission attachment containing the
  reviewed Greek misnomer.
- Left `SA.118154` unresolved under the positive-evidence-only policy rather
  than automatically labeling it `not_linked`.

## Remaining live-source limitation

The live Diavgeia query was deliberately skipped while its search service was
under maintenance. The analysis records this as an incomplete source gap. It
does not interpret the skipped query as evidence that no matching act exists.
