from dataclasses import replace
import unittest

from crawld.anomalies import (
    AssessmentOutcome, ContractValueIncreaseRuleConfig, assess_contract_value_increases,
)
from crawld.kimdis import parse_kimdis_contracts, select_current_contract_versions


def config():
    return ContractValueIncreaseRuleConfig("1", "2026-01-01", "2026-12-31", "Observed KIMDIS scope")


def contracts(original=100, current=150, **kwargs):
    root = {"referenceNumber": "ROOT", "totalCostWithVAT": original, "cancelled": False}
    latest = {"referenceNumber": "CURRENT", "prevReferenceNo": "ROOT", "totalCostWithVAT": current, "cancelled": False}
    latest.update(kwargs)
    return select_current_contract_versions(parse_kimdis_contracts([root, latest]))


class ContractValueIncreaseTests(unittest.TestCase):
    def test_exact_fifty_percent_boundary_and_evidence(self):
        for current, expected in ((149.99, AssessmentOutcome.NOT_FLAGGED), (150, AssessmentOutcome.FLAGGED), (151, AssessmentOutcome.FLAGGED)):
            with self.subTest(current=current):
                result = assess_contract_value_increases(contracts(current=current), config())[0]
                self.assertIs(result.outcome, expected)
                self.assertEqual(result.evidence_record_ids, ("ROOT", "CURRENT"))
                facts = {fact.name: fact.value for fact in result.observed_facts}
                self.assertEqual(facts["original_value_cents"], "10000")
                self.assertEqual(facts["value_basis"], "total_cost_with_vat")

    def test_unreliable_values_and_chains_are_inconclusive_without_percentage(self):
        cases = [contracts(original=0), contracts(original=-1), contracts(current=-1),
                 contracts(original=None), contracts(current=None), contracts(nextRefNo="MISSING")]
        cases.append(select_current_contract_versions(parse_kimdis_contracts([
            {"referenceNumber": "CURRENT", "prevReferenceNo": "MISSING", "totalCostWithVAT": 150}
        ])))
        cases.append(select_current_contract_versions(parse_kimdis_contracts([
            {"referenceNumber": "ROOT", "totalCostWithVAT": 100},
            {"referenceNumber": "ROOT", "totalCostWithVAT": 110},
            {"referenceNumber": "CURRENT", "prevReferenceNo": "ROOT", "totalCostWithVAT": 150},
        ])))
        cases.append(select_current_contract_versions(parse_kimdis_contracts([
            {"referenceNumber": "ROOT", "totalCostWithVAT": 100},
            {"referenceNumber": "CURRENT", "prevReferenceNo": "ROOT", "totalCostWithVAT": 150},
            {"referenceNumber": "CURRENT", "prevReferenceNo": "ROOT", "totalCostWithVAT": 160},
        ])))
        for case in cases:
            with self.subTest(case=case):
                result = assess_contract_value_increases(case, config())[0]
                self.assertIs(result.outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
                self.assertNotIn("increase_percent", {f.name for f in result.observed_facts})

    def test_uses_common_without_vat_basis_and_rejects_mixed_basis(self):
        for root, current, expected in (
            ({"totalCostWithVAT": 124, "totalCostWithoutVAT": 100}, {"totalCostWithoutVAT": 150}, AssessmentOutcome.FLAGGED),
            ({"totalCostWithVAT": 100}, {"totalCostWithoutVAT": 150}, AssessmentOutcome.UNABLE_TO_ASSESS),
        ):
            records = [{"referenceNumber": "ROOT", **root}, {"referenceNumber": "CURRENT", "prevReferenceNo": "ROOT", **current}]
            result = assess_contract_value_increases(select_current_contract_versions(parse_kimdis_contracts(records)), config())[0]
            self.assertIs(result.outcome, expected)
            if expected is AssessmentOutcome.FLAGGED:
                facts = {f.name: f.value for f in result.observed_facts}
                self.assertEqual(facts["value_basis"], "total_cost_without_vat")
                self.assertEqual(facts["increase_percent"], "50")

    def test_prefers_with_vat_when_both_bases_exist(self):
        records = [
            {"referenceNumber": "ROOT", "totalCostWithVAT": 124, "totalCostWithoutVAT": 100},
            {"referenceNumber": "CURRENT", "prevReferenceNo": "ROOT", "totalCostWithVAT": 185.99, "totalCostWithoutVAT": 150},
        ]
        result = assess_contract_value_increases(select_current_contract_versions(parse_kimdis_contracts(records)), config())[0]
        self.assertIs(result.outcome, AssessmentOutcome.NOT_FLAGGED)
        self.assertEqual({f.name: f.value for f in result.observed_facts}["value_basis"], "total_cost_with_vat")

    def test_missing_successor_is_inconclusive_even_when_retained_values_are_equal(self):
        result = assess_contract_value_increases(contracts(original=81900, current=81900, nextRefNo="MISSING"), config())[0]
        self.assertIs(result.outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        self.assertNotIn("increase_percent", {f.name for f in result.observed_facts})

    def test_unchanged_root_and_cancellation_use_recorded_amounts(self):
        root = select_current_contract_versions(parse_kimdis_contracts([
            {"referenceNumber": "ROOT", "totalCostWithVAT": 100, "cancelled": False}
        ]))
        result = assess_contract_value_increases(root, config())[0]
        self.assertIs(result.outcome, AssessmentOutcome.NOT_FLAGGED)
        self.assertEqual(result.evidence_record_ids, ("ROOT",))
        self.assertIn(("increase_percent", "0"), [(f.name, f.value) for f in result.observed_facts])
        result = assess_contract_value_increases(contracts(cancelled=True), replace(config(), coverage_limitations=("partial search",)))[0]
        self.assertIs(result.outcome, AssessmentOutcome.FLAGGED)
        self.assertIn("partial search", result.limitations)
        self.assertTrue(any("cancelled" in item for item in result.limitations))

