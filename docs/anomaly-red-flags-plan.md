# Funding red flags: development handoff

Status: **Draft for user review; implementation has not been authorized in this
chat.** The user will transfer the approved plan to a developing chat.

## Goal and guardrails

Produce a concise list of explainable red flags about contract assignment and
funding concentration. A flag is a lead for investigation, not proof of
fraud, corruption, illegal conduct, or intent. Preserve links to the contributing
records, but keep the main output clear and brief.

Focus on direct assignment, repeated contracts or aid awards, proximity to
direct-award or audit thresholds, and possible splitting. The agreed exception
concerning contract implementation is the 50% contract-value increase flag.
Keep contract values and State-aid elements as separate measures. Do not add
authority- or project-level contract concentration calculations or introduce
an overall risk score.

Follow the project instructions in `AGENTS.md` and `skills.md`. Agree each
small development step with the user, implement and verify that step, then
wait for the user's explicit confirmation before starting the next. Obtain the
user's decision for material unspecified behavior. Use existing project
conventions and preserve raw evidence and source provenance.

## Agreed red flags

1. **Direct award.** Flag a contract when reliable source data identifies its
   award procedure as direct assignment. Verify source codes and their meaning;
   missing or unrecognized procedure data means `unable to assess`, not an
   inferred direct or competitive award.
2. **Near a threshold.** Flag an individual contract whose applicable value is
   within 10% below an applicable direct-award or audit threshold, including the
   threshold. The 10% is an initial configurable setting, not a legal standard.
   A related contract is not required.
3. **Possible splitting.** Flag two or more contracts from the same authority
   to the same contractor, signed within an initial configurable 90-day window,
   when each is near the same applicable threshold and their combined relevant
   value exceeds it. If both contracts have CPV codes, require at least one
   matching code. Different known codes prevent this flag; if either contract
   lacks a code, allow the flag and state that the subject could not be
   checked. This simple pattern can miss other splitting arrangements.
4. **Repeated contracts.** Flag a company with five or more distinct contracts
   signed within an initial 90-day window, across observed authorities and
   projects. Count each contract once, using its original signed date; later
   versions are not new contracts. If that date cannot be established, the
   contract's timing is `unable to assess`. Merge overlapping qualifying
   windows into one flag for the company and retain supporting contract IDs.
   The five-contract and 90-day settings are starting values.
5. **Repeated State aid.** Flag a beneficiary with two or more distinct
   in-scope awards over the imported export period, including awards under
   different measures. Count linked, non-linked and unresolved RRF status;
   preserve each contributing award ID and its status for inspection. Continue
   the existing exclusion of loans and unknown instruments from aggregates.
6. **Contract value increase.** Flag an increase of at least 50% from original
   to current value using the same VAT basis. Retain both values and evidence.
   If either value cannot be established reliably, report `unable to assess`.

For threshold checks, maintain a reviewed, source-backed configuration that
identifies the applicable threshold by the facts required by its official rule,
including relevant dates, contract type, authority, funding, and value basis.
Direct-award and audit thresholds are distinct. Verify the applicable official
rule; do not assume an award value is the legally relevant estimate. If the
applicable threshold cannot be established, or an estimate required by that
rule is missing, threshold proximity and splitting are `unable to assess`.
Do not invent legal threshold values; verify and document them before use.

## Coverage and evidence

- Record `unable to assess` and the reason when a rule lacks information needed
  to calculate it reliably. Other rules may run using their own evidence.
- Five observed contracts within 90 days support a positive repeated-contract
  flag even if collection is incomplete; disclose the relevant gap. If fewer
  than five are observed and relevant coverage is incomplete, the result is
  `unable to assess`, not a finding that the pattern is absent.
- Keep distinct official company identifiers and record IDs. Do not merge
  companies by name, treat missing data as zero, or count duplicate contract
  versions or conflicting source records as independent events.
- Record the rule and version, applicable period, observed values and trigger,
  source scope, contributing record IDs, and relevant limitations. Preserve
  provenance in the existing analysis artifacts. Present short explanations.

## Proposed incremental development

Treat this order as a proposal for user approval, not permission to skip the
project's step-by-step checkpoints. Use behavior-focused tests for each step.

1. **Direct awards.** Verify KIMDIS procedure fields and codes against official
   documentation and retained records; preserve the needed fields and add the
   direct-award flag. Done when a verified direct award flags, a verified other
   procedure does not, an unknown procedure is `unable to assess`, and each
   result cites its source record.
2. **Repeated contracts.** Add the five-contract/90-day rule with original
   signed dates and deduplicated versions. Done when four versus five, a date
   just inside versus outside the window, overlapping windows, amendment
   versions, missing dates, and incomplete collection behave as agreed.
3. **Repeated State aid.** Add the two-award rule across the export scope.
   Done when distinct qualifying awards across measures flag, duplicates and
   excluded instruments do not inflate the count, and the result exposes
   contributing IDs and linkage status.
4. **Contract value increase.** Add the 50% comparison using compatible value
   bases. Done when below and at 50%, missing or unreliable original/current
   values, incompatible VAT bases, and nonpositive original values are handled
   without fabricating a percentage.
5. **Threshold proximity.** Research and document official applicable
   direct-award and audit thresholds, then introduce the reviewed threshold
   configuration and the 10% check. Done when the boundary is tested against
   a verified threshold, distinct threshold categories remain distinct, and
   unavailable applicable thresholds or required estimates are inconclusive.
6. **Possible splitting.** Add the agreed same-authority, contractor, time,
   value and CPV checks on top of verified threshold data. Done when matching,
   different and missing CPVs, different authorities or contractors, dates
   outside the window, nonqualifying values and incomplete inputs behave as
   specified and the flag cites its contributing contracts.
7. **Handoff verification.** Demonstrate the concise report and verify that
   flags, inconclusive results, evidence and scope agree across the existing
   output artifacts. Confirm the old State-aid share rule remains configurable
   and that contract and aid values remain separate.

Before each step, agree its exact interface, remaining rule-specific coverage
requirements, and expected output with the user. Use TDD where applicable,
including running the failing behavior test before implementation. Verify with
retained or synthetic inputs when live sources are unavailable; never claim a
live source or legal threshold was verified solely by a simulated test.

## Deferred ideas

Keep these earlier research notes visible, but do not implement them as part of
this plan: contract-value concentration shares, combined contract-plus-aid
amounts, cancellation or general amendment risk checks, authority-level
repetition, sector/region/company-size peers, year-over-year changes,
statistical outliers, and related-company or beneficial-ownership analysis.
Other timing patterns are outside scope beyond the two agreed 90-day checks.
Rule-specific treatment of incomplete sources is included above; more detailed
coverage policies require agreement as the relevant step is developed.

## Starting points and references

- Existing rule and profiles: `src/crawld/anomalies.py`,
  `src/crawld/profiles.py`; analysis output: `src/crawld/analysis.py`.
- KIMDIS parsing: `src/crawld/kimdis.py`; official API documentation:
  https://cerpp.eprocurement.gov.gr/khmdhs-opendata/help
- Retained KIMDIS data show organization, procedure, CPV-related fields and
  funding references can occur, but do not prove their meaning or coverage
  across all contracts. Verify code mappings and field availability.
- European Court of Auditors, procurement red flags:
  https://op.europa.eu/webpub/eca/special-reports/conflict-of-interest-06-2023/en/
- World Bank, contract-splitting indicators:
  https://documents1.worldbank.org/curated/en/309511468156866119/pdf/877290PUB0Frau00Box382147B00PUBLIC0.pdf
- Greek official direct-award procedure, for threshold research:
  https://mitos.gov.gr/index.php/ΔΔ:Διαδικασία_απευθείας_ανάθεσης_σύμβασης_προμηθειών_ή_υπηρεσιών
- Hellenic Court of Audit, pre-contract audit report, for threshold research:
  https://www.elsyn.gr/sites/default/files/Checks/ΕΚΘΕΣΗ%20ΕΛΕΓΧΟΥ%204%20ΠΡΟΣΥΜΒΑΤΙΚΟΣ.pdf
