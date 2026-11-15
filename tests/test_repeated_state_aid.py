from dataclasses import replace
import unittest

from crawld.anomalies import (
    AssessmentOutcome, RepeatedStateAidRuleConfig, assess_repeated_state_aid,
)
from crawld.kimdis import CompanyIdentifier
from crawld.state_aid import AidInstrument, AidMeasureEvidence, RrfAidLinkage, StateAidAward


def award(award_id, linkage=RrfAidLinkage.UNRESOLVED):
    measure = "SA.12345" if award_id == "A" else "SA.99999"
    evidence = None if linkage is RrfAidLinkage.UNRESOLVED else AidMeasureEvidence(
        measure, linkage, "Fixture official evidence", "https://example.test/decision", "a" * 64
    )
    return StateAidAward(
        award_id, "Beneficiary", CompanyIdentifier("EL_VAT", "GR", "111111111"),
        None, 10000, "EUR", AidInstrument.GRANT, "Grant", "2026-01-01",
        None, None, measure, linkage, "Fixture linkage", evidence, True, None, 1,
    )


def config(**kwargs):
    return RepeatedStateAidRuleConfig(
        rule_version="1", observation_start="2026-01-01", observation_end="2026-12-31",
        observation_scope="Imported export", **kwargs,
    )


class RepeatedStateAidTests(unittest.TestCase):
    def test_exact_identifiers_stay_separate_and_duplicate_provenance_does_not_conflict(self):
        first = award("A", RrfAidLinkage.LINKED)
        duplicate = replace(first, source_row_number=99, linkage_evidence="Another preserved citation",
                            measure_evidence=replace(first.measure_evidence, source_sha256="b" * 64))
        second = replace(award("B"), beneficiary_identifier=CompanyIdentifier("VAT", "GR", "111111111"))
        results = assess_repeated_state_aid([first, duplicate, second], config())
        self.assertEqual(len(results), 2)
        self.assertEqual({r.subject_identifier for r in results}, {"EL_VAT:GR:111111111", "VAT:GR:111111111"})
        self.assertTrue(all(r.outcome is AssessmentOutcome.NOT_FLAGGED for r in results))
        self.assertEqual({r.evidence_record_ids for r in results}, {("A",), ("B",)})

    def test_conflicts_missing_identity_and_incomplete_scope_cannot_negate_pattern(self):
        for uncertain in (
            [replace(award("B"), beneficiary_identifier=None)],
            [award("B"), replace(award("B"), aid_element_cents=500)],
        ):
            for known_count in (1, 2):
                with self.subTest(uncertain=uncertain, known_count=known_count):
                    known = [award("A")] + ([award("C")] if known_count == 2 else [])
                    results = assess_repeated_state_aid(known + uncertain, config())
                    company = next(r for r in results if r.subject_type.value == "company")
                    self.assertIs(company.outcome, AssessmentOutcome.FLAGGED if known_count == 2 else AssessmentOutcome.UNABLE_TO_ASSESS)
                    self.assertTrue(company.limitations)
                    gap = next(r for r in results if r.subject_type.value == "aid_award")
                    self.assertEqual(gap.evidence_record_ids, ("B",))
                    self.assertIs(gap.outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        partial = assess_repeated_state_aid([award("A")], config(coverage_limitations=("partial export",)))[0]
        self.assertIs(partial.outcome, AssessmentOutcome.UNABLE_TO_ASSESS)

    def test_duplicates_and_excluded_instruments_do_not_inflate_count(self):
        base = award("A")
        result = assess_repeated_state_aid([
            base, replace(base, source_row_number=8),
            replace(award("B"), instrument=AidInstrument.LOAN),
            replace(award("C"), instrument=AidInstrument.UNKNOWN),
            replace(award("D"), included_in_aid_aggregate=False),
        ], config())[0]
        self.assertIs(result.outcome, AssessmentOutcome.NOT_FLAGGED)
        self.assertEqual(result.evidence_record_ids, ("A",))

    def test_two_awards_across_measures_and_all_linkages_count(self):
        for linkage in RrfAidLinkage:
            with self.subTest(linkage=linkage):
                one = assess_repeated_state_aid([award("A")], config())[0]
                two = assess_repeated_state_aid([award("A"), award("B", linkage)], config())[0]
                self.assertIs(one.outcome, AssessmentOutcome.NOT_FLAGGED)
                self.assertIs(two.outcome, AssessmentOutcome.FLAGGED)
                self.assertEqual(two.route.value, "state_aid")
                self.assertEqual(two.evidence_record_ids, ("A", "B"))
                self.assertIn(("award_rrf_linkage:B", linkage.value), [(f.name, f.value) for f in two.observed_facts])
