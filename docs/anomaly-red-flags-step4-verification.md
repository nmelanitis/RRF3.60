# Step 4 — contract-value increase

Implementation date: 2026-11-07. Human acceptance is deferred to the final
review, as requested. This document covers only the 50% value-increase rule.

## Intended behavior

Compare the original/root contract with its reliably resolved current record.
Flag when current value is at least 150% of original value, using integer cents
for the threshold decision. Compare the same VAT basis: prefer including VAT
when both records provide it; otherwise use excluding VAT when both provide it.
Never compare a gross value with a net value.

Missing originals, missing current/successor records, conflicting values,
incompatible bases and nonpositive original values are inconclusive. No
percentage is fabricated. An unchanged original/current record is 0% increase.
Cancellation does not turn the published value into zero; limitations remain
visible. Reliable comparisons retain collection-gap limitations.

Every assessment retains the rule/version, observation scope, original/current
values and common basis where available, supporting record IDs and explanation.
The existing SQLite/CSV outputs retain all outcomes; HTML shows positives and
inconclusive assessments; the manifest records per-rule counts.

## Development and coordination

One developer owns production code and tests. Parent prepares retained-data
verification and this document independently. Once code is stable, a reviewer
and validation agent check it in parallel without changing it. Review findings
are handled as targeted corrections, followed by relevant tests.

Targeted TDD slices cover the exact threshold, same-basis fallback, missing and
conflicting evidence, nonpositive values, unchanged contracts, cancellation,
and artifact integration. The initial boundary test failed because the new
public interface was absent, then passed after implementation.

## Reproduce checks

From the project root:

```bash
.venv/bin/python -m unittest tests.test_contract_value_increase -v
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m compileall -q src tests tools
```

Replay retained real KIMDIS responses (when the earlier retained files remain):

```bash
.venv/bin/python tools/verify_contract_value_increases.py \
  /tmp/crawld-kimdis-direct-body.json \
  /tmp/crawld-kimdis-open-body.json \
  /tmp/crawld-kimdis-unfiltered-body.json \
  --date-from 2026-10-01 --date-to 2026-10-01
```

The command prints a fresh output directory containing `summary.json` and
`assessments.json`; original source files are read only. These are retained
live responses, not a fresh API collection.

One real chain is `26SYMV019905416` → `26SYMV019905426` →
`26SYMV019916950`. The last record is absent from the retained pages. Both
observed versions contain €8,190 including VAT and €7,000 excluding VAT, but
that does not establish the current value. Its assessment must be inconclusive.
No complete real positive-increase chain has been established in this sample;
the 50% positive boundary is verified with controlled synthetic inputs.

## Human alignment checks

- Compare €100,000 → €149,999 with €100,000 → €150,000: only the latter flags.
- Confirm the output shows the same VAT basis for both values, and both source IDs.
- Inspect the missing-successor chain above: the result must be inconclusive.
- Confirm an amendment is a version of one contract, not a second contract flag.
- Check that a merged company profile or consortium allocation never supplies
  the values: the rule compares the contract records themselves.

For a generated analysis:

```bash
sqlite3 ANALYSIS_DIR/analysis.sqlite3 "SELECT rule_id, subject_identifier, outcome, observed_facts, evidence_record_ids, limitations FROM red_flag_assessments WHERE rule_id='contract_value_increase';"
```

Compare the rows with `red_flag_assessments.csv`, HTML, and manifest per-rule
counts. Follow evidence IDs through `kimdis_contract_record_sources` to the raw
JSON. A flag is an investigation lead; this rule does not decide legality.
