# crawlD

## Withdrawal analysis: Greece 2.0

This repository includes an analysis of **WITHDRAWN projects** from Greece's
Recovery and Resilience Facility (RRF) grant actions under **Greece 2.0**.

- [Read the withdrawal findings, which identified 104 WITHDRAWN projects with a total of €2.905 billion in approved budgets before withdrawal](data/reports/20261113T082105Z-a0835349-withdrawals/withdrawal-findings-presentation.md)
- [Download the full list of all 104 withdrawn projects (CSV)](data/reports/20261113T082105Z-a0835349-withdrawals/all-104-withdrawn-projects.csv)

**Data and reproducibility:** [Zenodo record](https://zenodo.org/records/24249708) · [DOI: 10.5281/zenodo.24249708](https://doi.org/10.5281/zenodo.24249708)

`crawlD` is a local Python application for reconstructing and analysing the
public administrative history of Greece's Recovery and Resilience Facility
(Ταμείο Ανάκαμψης και Ανθεκτικότητας).

The project links official records into RRF actions, public-funding events,
funding recipients, and dated evidence. Its reports will quantify fully and
partially cancelled actions and reconstruct the public funding visible for each
company through two distinct routes:

- public contracts awarded to a contractor; and
- State aid, including grants awarded directly to an aid beneficiary without a
  public contract.

This distinction is essential: RRF money can reach a private company's ΑΦΜ as
an aid award without appearing in KIMDIS. Contract value and aid element remain
separate measures, even when they concern the same company. Findings retain
links to their official evidence; an anomaly is an investigation lead, not
proof of corruption.

## Goals

1. Reconstruct the official record of Greek RRF actions and their projects.
2. Identify fully and partially cancelled actions and quantify the cancelled
   RRF and national public funding supported by explicit evidence.
3. Build a company funding profile, keyed by ΑΦΜ or another official
   beneficiary identifier, containing both public contracts and aid awards.
4. Import a preserved full-result export from the European Commission State Aid
   Transparency Public Search, including companies absent from KIMDIS.
5. Distinguish aid awards officially linked to the RRF from other State aid and
   from awards whose funding source cannot yet be established.
6. Report contract count and current public contract value separately from aid
   award count and aid element. Loans and loan instruments remain outside the
   agreed scope.
7. Surface explainable signals only where the analysis has a defensible
   denominator. The current concentration rule applies to RRF-linked State aid;
   contract values remain visible in profiles without a global share flag.

The agreed source model is:

1. [Greece 2.0](https://greece20.gov.gr/lista-entagmenwn-ergwn/) defines the
   RRF action universe.
2. [KIMDIS](https://cerpp.eprocurement.gov.gr/khmdhs-opendata/help) provides
   the public-procurement and contract route.
3. The European Commission's
   [State Aid Transparency Public Search](https://webgate.ec.europa.eu/competition/transparency/public/search)
   provides the State-aid route, including beneficiary identifiers, aid
   amounts, instruments, granting dates, authorities, objectives, and measure
   references when published. A CSV export contains the complete set of
   published individual State-aid awards returned by its recorded country,
   date and other search filters; it is not a complete register of all aid.
   It contains no public-procurement contracts and is not RRF-specific. Awards
   are classified as RRF-linked when the official measure name explicitly
   identifies the Recovery and Resilience Facility or when authoritative
   evidence establishes the status of their exact aid-measure reference.
   Otherwise they remain unresolved.
4. [Diavgeia](https://diavgeia.gov.gr/api/help) provides administrative acts,
   publication versions and enrichment for both routes. Publication status is
   not a legal cancellation; cancellation requires explicit operative text.

ESIDIS is reserved for manual verification until a sanctioned machine
interface becomes available.

The State Aid Transparency Public Search is an additional evidence source, not
a complete register of every subsidy. European transparency requirements
normally cover individual awards only above the threshold set by the applicable
legal basis, commonly EUR 100,000, and publication can be delayed. Member
States may also publish on another comprehensive State-aid website. De minimis
aid is published through the eAidRegister or qualifying national registers
under a separate regime. Each snapshot must therefore report the searched
sources, thresholds, dates, and known coverage gaps rather than claim that the
company funding profile is exhaustive.

## Current runnable behavior

Collection saves the official Greece 2.0 inclusion catalogue and its selected
decision PDFs into a dated, queryable snapshot:

```shell
uv sync
uv run crawld collect
```

By default, the cutoff is the runtime moment in Europe/Athens. To use an
explicit cutoff or a different output directory:

```shell
uv run crawld collect --through 2026-10-30 --output-root data/snapshots
```

If collection fails, its incomplete snapshot is retained and can be resumed:

```shell
uv run crawld collect --resume data/snapshots/<snapshot-directory>
```

Each completed snapshot contains the original Greece 2.0 HTML, decision PDFs
under `raw/greece20-decisions/<sha256>.pdf`, a SQLite database with catalogue
records, decision outcomes, extracted OPS/ΠΔΕ evidence and provenance, and a JSON
manifest with collection metrics. Only decision URLs from included catalogue
rows are collected; identical URL strings are fetched once per run. Collection
is sequential, so a full catalogue run may take considerable time.

`complete` means the collection run finished, including recording individual
PDF retrieval or parsing failures. Inspect `decisions_failed` and the
`greece20_decisions` table for gaps. A collected PDF with no extracted references
does not establish that the project has no ΠΔΕ. Local persistence failures make
the run incomplete; if even the failure status cannot be saved, an exception
is raised. Completed snapshots cannot be resumed. Resuming an incomplete run
rebuilds its normalized records and retries its sources, retaining raw PDF files.

The cutoff filters the catalogue's inclusion date, not the PDF's contents or
publication history. Undated catalogue rows remain included and counted.
Retrieval dates record when evidence was actually obtained; the tool cannot
reconstruct an unavailable historical version of a PDF from a past cutoff.

For a retained offline example and human alignment checks, see
[the snapshot verification guide](docs/snapshot-integration-verification.md).

Analysis is a separate, derived step. It conservatively classifies collected
Greece 2.0 decisions, searches KIMDIS by each validated ΠΔΕ reference, searches
Diavgeia by OPS/ΠΔΕ, imports a preserved Commission State-aid CSV, resolves
contract amendments, builds identifier-based company profiles, and runs an
explicit RRF-linked State-aid concentration rule:

First resolve the export's exact `SA.XXXXX` references against the official
Commission State Aid case dataset. The first run downloads the roughly 705 MB
JSON dataset to the requested cache path; later runs reuse that file and scan
it with bounded memory:

```shell
uv run crawld collect-state-aid-evidence \
  --state-aid-csv /absolute/path/to/commission-export.csv \
  --state-aid-config /absolute/path/to/state-aid-config.json \
  --commission-dataset data/cache/commission-state-aid/case-data-SA.json \
  --output-root data/state-aid-evidence
```

The command writes a new `state-aid-config.resolved.json`, extracted exact-case
records, downloaded matching English/Greek attachments, and `manifest.json`.
It never changes the input CSV or configuration. It first checks the official
case title, then linked English/Greek case documents, then an exact-SA Diavgeia
search. Use `--skip-diavgeia` for an offline Commission-only run. Missing,
failed, or phrase-free sources remain unresolved; they never become
`not_linked`. A new positive result conflicting with configured `not_linked`
evidence is removed from the generated config on both sides of the conflict so
the subsequent analysis remains unresolved and the manifest exposes the issue.

Then analyse with the generated configuration:

```shell
uv run crawld analyse \
  --snapshot data/snapshots/<completed-snapshot> \
  --state-aid-csv /absolute/path/to/commission-export.csv \
  --state-aid-config data/state-aid-evidence/<run>/state-aid-config.resolved.json \
  --kimdis-from 2024-01-01 \
  --kimdis-to 2026-10-30 \
  --rrf-aid-share-threshold 0.25
```

Replace the example State-aid configuration scope with the actual export
scope. Add reviewed official evidence manually only where needed; automatic
collection adds positive RRF evidence but never invents negative evidence.
The analysis creates a new immutable artifact containing `analysis.sqlite3`,
profile and anomaly CSVs, an HTML report, preserved raw inputs and a manifest
that records source-snapshot hashes. Contract value and aid element remain
separate; neither profiles nor anomaly rules manufacture a combined amount.
Failed, skipped, truncated or indeterminate searches are reported as source
gaps rather than zero results.

The State Aid Transparency award boundary is intentionally a preserved CSV
import: the project does not depend on an undocumented browser endpoint. Exact
measure lookup uses the Commission's separately published daily open JSON case
dataset and preserved official attachments. The importer
first checks the English measure name for `Recovery and Resilience`, then the
Greek measure name for `ΤΑΜΕΙΟ ΑΝΑΚΑΜΨΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ`, and otherwise
uses reviewed evidence for the exact aid-measure reference. A positive measure
name conflicting with reviewed non-RRF evidence remains unresolved. The aid
measure reference is not a ΠΔΕ reference.

For the full step record, automated checks, offline demo and manual alignment
checks, see [the remaining-roadmap verification guide](docs/remaining-roadmap-implementation-and-verification.md).
For the captured live KIMDIS compatibility run, corrections, and reproducible
live verifier, see [the live KIMDIS verification guide](docs/live-kimdis-verification.md).

Run the behavior tests with:

```shell
uv run python -m unittest discover -s tests
```

The implementation remains deliberately conservative: PDF extraction has no
OCR, name-only entities are not merged, incomplete contract amendment chains
remain visible, and State-aid transparency coverage is not described as a
complete register of subsidies.

