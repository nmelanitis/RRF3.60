from decimal import Decimal
from datetime import date, timedelta
import json
from pathlib import Path
import unittest

from crawld.anomalies import (
    AnomalyConfig,
    AnomalyRoute,
    AssessmentOutcome,
    DirectAwardRuleConfig,
    RepeatedContractRuleConfig,
    assess_direct_awards,
    assess_repeated_contracts,
    generate_anomaly_flags,
)
from crawld.kimdis import (
    CompanyIdentifier,
    parse_kimdis_contracts,
    select_current_contract_versions,
)
from crawld.profiles import CompanyFundingProfile


def profile(
    afm: str,
    *,
    contract_value: int = 0,
    aid_value: int = 0,
) -> CompanyFundingProfile:
    return CompanyFundingProfile(
        identifier=CompanyIdentifier("EL_VAT", "GR", afm),
        names=(f"Company {afm}",),
        contract_count=1 if contract_value else 0,
        active_contract_count=1 if contract_value else 0,
        cancelled_contract_count=0,
        current_contract_value_cents=contract_value,
        unresolved_contract_value_count=0,
        aid_award_count=1 if aid_value else 0,
        aid_amount_eur_cents=aid_value,
        rrf_linked_aid_count=1 if aid_value else 0,
        rrf_linked_aid_amount_eur_cents=aid_value,
        non_rrf_aid_count=0,
        non_rrf_aid_amount_eur_cents=0,
        unresolved_aid_count=0,
        unresolved_aid_amount_eur_cents=0,
        non_eur_aid_count=0,
        missing_aid_amount_or_currency_count=0,
        excluded_loan_count=0,
        excluded_loan_amount_eur_cents=0,
        excluded_other_aid_count=0,
        excluded_other_aid_amount_eur_cents=0,
        contract_ids=(f"C-{afm}",) if contract_value else (),
        aid_award_ids=(f"A-{afm}",) if aid_value else (),
        current_value_contract_ids=(f"C-{afm}",) if contract_value else (),
        rrf_linked_aid_award_ids=(f"A-{afm}",) if aid_value else (),
        rrf_linked_eur_amount_award_ids=(f"A-{afm}",) if aid_value else (),
    )


def repeated_contract_record(
    reference: str,
    signed_date: str | None,
    *identifiers: str,
    previous: str | None = None,
) -> dict[str, object]:
    return {
        "referenceNumber": reference,
        "prevReferenceNo": previous,
        "contractSignedDate": signed_date,
        "cancelled": False,
        "contractingDataDetails": {
            "contractingMembersDataList": [
                {
                    "vatNumber": identifier,
                    "greekVatNumber": True,
                    "name": f"Company {identifier}",
                    "country": "GR",
                }
                for identifier in identifiers
            ]
        },
    }


def repeated_config(
    *, coverage_limitations: tuple[str, ...] = ()
) -> RepeatedContractRuleConfig:
    return RepeatedContractRuleConfig(
        rule_version="repeated-contracts-v1",
        observation_start="2026-01-01",
        observation_end="2026-12-31",
        observation_scope="KIMDIS PDE searches",
        coverage_limitations=coverage_limitations,
    )


class AnomalyTests(unittest.TestCase):
    def test_repeated_contract_settings_are_configurable_and_validated(self) -> None:
        config = AnomalyConfig(
            rule_version="1.0",
            observation_start="2026-01-01",
            observation_end="2026-12-31",
            rrf_aid_share_threshold=Decimal("0.5"),
            minimum_rrf_aid_cents=0,
            repeated_contract_minimum=3,
            repeated_contract_window_days=30,
        )

        self.assertEqual(config.repeated_contract_minimum, 3)
        self.assertEqual(config.repeated_contract_window_days, 30)
        with self.assertRaisesRegex(ValueError, "Repeated-contract minimum"):
            AnomalyConfig(
                rule_version="1.0",
                observation_start="2026-01-01",
                observation_end="2026-12-31",
                rrf_aid_share_threshold=Decimal("0.5"),
                minimum_rrf_aid_cents=0,
                repeated_contract_minimum=1,
            )
        with self.assertRaisesRegex(ValueError, "Repeated-contract window"):
            AnomalyConfig(
                rule_version="1.0",
                observation_start="2026-01-01",
                observation_end="2026-12-31",
                rrf_aid_share_threshold=Decimal("0.5"),
                minimum_rrf_aid_cents=0,
                repeated_contract_window_days=0,
            )

    def test_captured_live_repeated_contracts_flag_exact_official_identifier(self) -> None:
        payload = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "kimdis_repeated_contracts_live_excerpt.json"
            ).read_text(encoding="utf-8")
        )

        assessments = assess_repeated_contracts(
            select_current_contract_versions(
                parse_kimdis_contracts(payload["content"])
            ),
            repeated_config(),
        )

        self.assertEqual(len(assessments), 1)
        self.assertIs(assessments[0].outcome, AssessmentOutcome.FLAGGED)
        self.assertEqual(assessments[0].subject_identifier, "VAT:GR:082525697")
        self.assertEqual(
            assessments[0].evidence_record_ids,
            (
                "26SYMV019903858",
                "26SYMV019905246",
                "26SYMV019905349",
                "26SYMV019905379",
                "26SYMV019905387",
            ),
        )

    def test_captured_live_amendment_chain_counts_original_contract_once(self) -> None:
        payload = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "kimdis_contract_amendment_chain_live_excerpt.json"
            ).read_text(encoding="utf-8")
        )

        assessment = assess_repeated_contracts(
            select_current_contract_versions(
                parse_kimdis_contracts(payload["content"])
            ),
            repeated_config(),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.NOT_FLAGGED)
        self.assertEqual(assessment.subject_identifier, "VAT:GR:998347888")
        self.assertEqual(assessment.evidence_record_ids, ("26SYMV019905416",))
        self.assertEqual(
            dict((fact.name, fact.value) for fact in assessment.observed_facts),
            {"observed_dated_contract_count": "1"},
        )

    def test_five_contracts_flag_while_four_do_not(self) -> None:
        start = date(2026, 1, 1)

        four = select_current_contract_versions(
            parse_kimdis_contracts(
                [
                    repeated_contract_record(
                        f"26SYMV{i:06d}",
                        (start + timedelta(days=i)).isoformat(),
                        "111111111",
                    )
                    for i in range(4)
                ]
            )
        )
        five = select_current_contract_versions(
            parse_kimdis_contracts(
                [
                    repeated_contract_record(
                        f"26SYMV{i:06d}",
                        (start + timedelta(days=i)).isoformat(),
                        "111111111",
                    )
                    for i in range(5)
                ]
            )
        )

        four_assessment = assess_repeated_contracts(four, repeated_config())[0]
        five_assessment = assess_repeated_contracts(five, repeated_config())[0]

        self.assertIs(four_assessment.outcome, AssessmentOutcome.NOT_FLAGGED)
        self.assertIs(five_assessment.outcome, AssessmentOutcome.FLAGGED)
        self.assertEqual(
            five_assessment.evidence_record_ids,
            tuple(f"26SYMV{i:06d}" for i in range(5)),
        )
        self.assertEqual(
            dict((fact.name, fact.value) for fact in five_assessment.observed_facts),
            {
                "contract_count": "5",
                "window_start": "2026-01-01",
                "window_end": "2026-01-05",
                "window_days_inclusive": "5",
            },
        )

    def test_ninetieth_calendar_day_is_inside_and_next_day_is_outside(self) -> None:
        records: list[dict[str, object]] = []
        for identifier, final_offset in (("111111111", 89), ("222222222", 90)):
            offsets = (0, 20, 40, 60, final_offset)
            records.extend(
                repeated_contract_record(
                    f"{identifier}-{index}",
                    (date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
                    identifier,
                )
                for index, offset in enumerate(offsets)
            )

        assessments = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts(records)),
            repeated_config(),
        )
        by_company = {item.subject_identifier: item for item in assessments}

        self.assertIs(
            by_company["EL_VAT:GR:111111111"].outcome,
            AssessmentOutcome.FLAGGED,
        )
        self.assertIs(
            by_company["EL_VAT:GR:222222222"].outcome,
            AssessmentOutcome.NOT_FLAGGED,
        )

    def test_overlapping_qualifying_windows_merge_all_contributing_contracts(self) -> None:
        records = [
            repeated_contract_record(
                f"26SYMV{i:06d}",
                (date(2026, 1, 1) + timedelta(days=i * 10)).isoformat(),
                "111111111",
            )
            for i in range(10)
        ]

        assessments = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts(records)),
            repeated_config(),
        )

        self.assertEqual(len(assessments), 1)
        self.assertIs(assessments[0].outcome, AssessmentOutcome.FLAGGED)
        self.assertEqual(
            assessments[0].evidence_record_ids,
            tuple(f"26SYMV{i:06d}" for i in range(10)),
        )
        self.assertEqual(
            dict((fact.name, fact.value) for fact in assessments[0].observed_facts),
            {
                "contract_count": "10",
                "window_start": "2026-01-01",
                "window_end": "2026-04-01",
                "window_days_inclusive": "91",
            },
        )

    def test_missing_root_signed_date_makes_otherwise_negative_company_unable(self) -> None:
        records = [
            repeated_contract_record(
                f"26SYMV{i:06d}",
                (
                    None
                    if i == 4
                    else "not-a-date"
                    if i == 3
                    else date(2026, 1, i + 1).isoformat()
                ),
                "111111111",
            )
            for i in range(5)
        ]

        assessment = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts(records)),
            repeated_config(),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        self.assertEqual(
            assessment.evidence_record_ids,
            tuple(f"26SYMV{i:06d}" for i in range(5)),
        )
        self.assertIn("missing or invalid original signed date", assessment.limitations)
        self.assertEqual(
            dict((fact.name, fact.value) for fact in assessment.observed_facts),
            {
                "observed_dated_contract_count": "3",
                "contracts_missing_valid_signed_date": "2",
            },
        )

    def test_missing_other_date_does_not_erase_observed_positive(self) -> None:
        records = [
            repeated_contract_record(
                f"DATED-{index}",
                date(2026, 1, index + 1).isoformat(),
                "111111111",
            )
            for index in range(5)
        ]
        records.append(repeated_contract_record("UNDATED", None, "111111111"))

        assessment = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts(records)),
            repeated_config(),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.FLAGGED)
        self.assertEqual(
            assessment.evidence_record_ids,
            tuple(f"DATED-{index}" for index in range(5)),
        )
        self.assertIn("missing or invalid original signed date", assessment.limitations)

    def test_incomplete_collection_cannot_negate_but_does_not_erase_positive(self) -> None:
        records = [
            repeated_contract_record(
                f"111-{i}", date(2026, 1, i + 1).isoformat(), "111111111"
            )
            for i in range(4)
        ] + [
            repeated_contract_record(
                f"222-{i}", date(2026, 1, i + 1).isoformat(), "222222222"
            )
            for i in range(5)
        ]
        limitation = "KIMDIS collection coverage is incomplete"

        assessments = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts(records)),
            repeated_config(coverage_limitations=(limitation,)),
        )
        by_company = {item.subject_identifier: item for item in assessments}

        self.assertIs(
            by_company["EL_VAT:GR:111111111"].outcome,
            AssessmentOutcome.UNABLE_TO_ASSESS,
        )
        self.assertIs(
            by_company["EL_VAT:GR:222222222"].outcome,
            AssessmentOutcome.FLAGGED,
        )
        self.assertIn(limitation, by_company["EL_VAT:GR:111111111"].limitations)
        self.assertIn(limitation, by_company["EL_VAT:GR:222222222"].limitations)

    def test_non_overlapping_qualifying_clusters_produce_separate_flags(self) -> None:
        offsets = (0, 1, 2, 3, 4, 200, 201, 202, 203, 204)
        records = [
            repeated_contract_record(
                f"26SYMV{i:06d}",
                (date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
                "111111111",
            )
            for i, offset in enumerate(offsets)
        ]

        assessments = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts(records)),
            repeated_config(),
        )

        self.assertEqual(len(assessments), 2)
        self.assertTrue(
            all(item.outcome is AssessmentOutcome.FLAGGED for item in assessments)
        )
        self.assertEqual(
            tuple(item.evidence_record_ids for item in assessments),
            (
                tuple(f"26SYMV{i:06d}" for i in range(5)),
                tuple(f"26SYMV{i:06d}" for i in range(5, 10)),
            ),
        )

    def test_amendment_version_never_adds_a_contract_event(self) -> None:
        records = [
            repeated_contract_record(
                f"ROOT-{i}", date(2026, 1, i + 1).isoformat(), "111111111"
            )
            for i in range(4)
        ]
        records[0]["nextRefNo"] = "AMENDMENT-1"
        records.append(
            repeated_contract_record(
                "AMENDMENT-1",
                "2026-01-05",
                "111111111",
                previous="ROOT-0",
            )
        )

        assessment = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts(records)),
            repeated_config(),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.NOT_FLAGGED)
        self.assertEqual(
            dict((fact.name, fact.value) for fact in assessment.observed_facts),
            {"observed_dated_contract_count": "4"},
        )
        self.assertNotIn("AMENDMENT-1", assessment.evidence_record_ids)

    def test_identifiers_stay_distinct_and_each_consortium_member_gets_one_event(self) -> None:
        records = [
            repeated_contract_record(
                f"CONSORTIUM-{i}",
                date(2026, 1, i + 1).isoformat(),
                "111111111",
                "222222222",
            )
            for i in range(5)
        ] + [
            repeated_contract_record(
                "OTHER-1", "2026-01-06", "333333333"
            )
        ]

        assessments = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts(records)),
            repeated_config(),
        )
        by_company = {item.subject_identifier: item for item in assessments}

        self.assertEqual(set(by_company), {
            "EL_VAT:GR:111111111",
            "EL_VAT:GR:222222222",
            "EL_VAT:GR:333333333",
        })
        self.assertIs(
            by_company["EL_VAT:GR:111111111"].outcome,
            AssessmentOutcome.FLAGGED,
        )
        self.assertIs(
            by_company["EL_VAT:GR:222222222"].outcome,
            AssessmentOutcome.FLAGGED,
        )
        self.assertIs(
            by_company["EL_VAT:GR:333333333"].outcome,
            AssessmentOutcome.NOT_FLAGGED,
        )
        self.assertEqual(
            by_company["EL_VAT:GR:111111111"].evidence_record_ids,
            tuple(f"CONSORTIUM-{i}" for i in range(5)),
        )

    def test_same_value_with_different_identifier_schemes_stays_distinct(self) -> None:
        greek = repeated_contract_record("GREEK-1", "2026-01-01", "111111111")
        other = repeated_contract_record("OTHER-1", "2026-01-01", "111111111")
        member = other["contractingDataDetails"]["contractingMembersDataList"][0]
        member["greekVatNumber"] = False

        assessments = assess_repeated_contracts(
            select_current_contract_versions(
                parse_kimdis_contracts([greek, other])
            ),
            repeated_config(),
        )

        self.assertEqual(
            {item.subject_identifier for item in assessments},
            {"EL_VAT:GR:111111111", "VAT:GR:111111111"},
        )
        self.assertTrue(
            all(item.outcome is AssessmentOutcome.NOT_FLAGGED for item in assessments)
        )

    def test_missing_root_does_not_assign_amendment_to_a_company(self) -> None:
        amendment = repeated_contract_record(
            "AMENDMENT-1",
            "2026-01-05",
            "111111111",
            previous="MISSING-ROOT",
        )

        assessments = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts([amendment])),
            repeated_config(),
        )

        self.assertEqual(len(assessments), 1)
        self.assertIs(assessments[0].outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        self.assertEqual(assessments[0].subject_type.value, "contract")
        self.assertEqual(assessments[0].subject_identifier, "MISSING-ROOT")
        self.assertEqual(assessments[0].evidence_record_ids, ("AMENDMENT-1",))
        self.assertIn("original", assessments[0].explanation)

    def test_unattributed_contract_gap_makes_otherwise_negative_company_inconclusive(self) -> None:
        records = [
            repeated_contract_record(
                f"ROOT-{index}",
                date(2026, 1, index + 1).isoformat(),
                "111111111",
            )
            for index in range(4)
        ]
        records.append(
            repeated_contract_record(
                "AMENDMENT-1",
                "2026-01-05",
                "111111111",
                previous="MISSING-ROOT",
            )
        )

        assessments = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts(records)),
            repeated_config(),
        )
        by_subject = {
            (item.subject_type.value, item.subject_identifier): item
            for item in assessments
        }

        company = by_subject[("company", "EL_VAT:GR:111111111")]
        self.assertIs(company.outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        self.assertTrue(
            any("could not be assigned" in item for item in company.limitations)
        )
        self.assertIs(
            by_subject[("contract", "MISSING-ROOT")].outcome,
            AssessmentOutcome.UNABLE_TO_ASSESS,
        )

    def test_partial_consortium_identity_preserves_events_and_discloses_gap(self) -> None:
        for count, expected in ((4, AssessmentOutcome.UNABLE_TO_ASSESS), (5, AssessmentOutcome.FLAGGED)):
            with self.subTest(count=count):
                records = [repeated_contract_record(f"A-{i}", "2026-01-01", "111111111") for i in range(count)]
                consortium = repeated_contract_record("CONSORTIUM", "2026-01-02", "222222222")
                consortium["contractingDataDetails"]["contractingMembersDataList"].append({"name": "Unidentified member"})
                records.append(consortium)
                assessments = assess_repeated_contracts(
                    select_current_contract_versions(parse_kimdis_contracts(records)), repeated_config()
                )
                companies = {a.subject_identifier: a for a in assessments if a.subject_type.value == "company"}
                self.assertIs(companies["EL_VAT:GR:111111111"].outcome, expected)
                self.assertTrue(companies["EL_VAT:GR:111111111"].limitations)
                self.assertEqual(companies["EL_VAT:GR:222222222"].evidence_record_ids, ("CONSORTIUM",))
                self.assertIs(companies["EL_VAT:GR:222222222"].outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
                gap = next(a for a in assessments if a.subject_type.value == "contract")
                self.assertEqual(gap.evidence_record_ids, ("CONSORTIUM",))
                self.assertIn("unidentified", gap.explanation)

    def test_root_without_official_contractor_identifier_is_inconclusive(self) -> None:
        record = repeated_contract_record("ROOT-1", "2026-01-05")

        assessments = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts([record])),
            repeated_config(),
        )

        self.assertEqual(len(assessments), 1)
        self.assertIs(assessments[0].outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        self.assertEqual(assessments[0].subject_type.value, "contract")
        self.assertEqual(assessments[0].subject_identifier, "ROOT-1")
        self.assertEqual(assessments[0].evidence_record_ids, ("ROOT-1",))
        self.assertIn("official contractor identifier", assessments[0].explanation)

    def test_conflicting_root_facts_do_not_select_an_arbitrary_company_or_date(self) -> None:
        records = [
            repeated_contract_record("ROOT-1", "2026-01-05", "111111111"),
            repeated_contract_record("ROOT-1", "2026-02-05", "222222222"),
        ]

        assessments = assess_repeated_contracts(
            select_current_contract_versions(parse_kimdis_contracts(records)),
            repeated_config(),
        )

        self.assertEqual(len(assessments), 1)
        self.assertIs(assessments[0].outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        self.assertEqual(assessments[0].subject_type.value, "contract")
        self.assertEqual(assessments[0].subject_identifier, "ROOT-1")
        self.assertEqual(assessments[0].evidence_record_ids, ("ROOT-1",))
        self.assertIn("conflicting original contract facts", assessments[0].explanation)

    def test_captured_live_direct_award_is_flagged_by_the_public_seam(self) -> None:
        payload = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "kimdis_contract_direct_award_live_excerpt.json"
            ).read_text(encoding="utf-8")
        )
        records = parse_kimdis_contracts(payload["content"])

        assessment = assess_direct_awards(
            select_current_contract_versions(records),
            DirectAwardRuleConfig(
                rule_version="direct-award-v1",
                observation_start="2026-10-01",
                observation_end="2026-10-01",
                observation_scope="Captured live KIMDIS page-zero response",
            ),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.FLAGGED)
        self.assertEqual(assessment.subject_identifier, "26SYMV019905518")
        self.assertEqual(assessment.evidence_record_ids, ("26SYMV019905518",))
        self.assertEqual(
            dict((fact.name, fact.value) for fact in assessment.observed_facts),
            {"procedure_code": "6", "procedure_label": "Απευθείας ανάθεση"},
        )

    def test_verified_direct_award_is_flagged_once_with_source_record(self) -> None:
        records = parse_kimdis_contracts(
            [
                {
                    "referenceNumber": "24SYMV000001",
                    "procedureType": {
                        "key": "6",
                        "value": "Απευθείας ανάθεση",
                    },
                    "cancelled": False,
                }
            ]
        )

        assessments = assess_direct_awards(
            select_current_contract_versions(records),
            DirectAwardRuleConfig(
                rule_version="direct-award-v1",
                observation_start="2024-01-01",
                observation_end="2026-11-05",
                observation_scope="KIMDIS PDE searches",
            ),
        )

        self.assertEqual(len(assessments), 1)
        assessment = assessments[0]
        self.assertIs(assessment.outcome, AssessmentOutcome.FLAGGED)
        self.assertIs(assessment.route, AnomalyRoute.CONTRACT)
        self.assertEqual(assessment.subject_identifier, "24SYMV000001")
        self.assertEqual(assessment.evidence_record_ids, ("24SYMV000001",))
        self.assertEqual(
            dict((fact.name, fact.value) for fact in assessment.observed_facts),
            {"procedure_code": "6", "procedure_label": "Απευθείας ανάθεση"},
        )
        self.assertIn("investigation lead", assessment.explanation)

    def test_unrecognized_procedure_code_is_unable_to_assess(self) -> None:
        records = parse_kimdis_contracts(
            [
                {
                    "referenceNumber": "24SYMV000002",
                    "procedureType": {"key": "999", "value": "Νέα διαδικασία"},
                    "cancelled": False,
                }
            ]
        )

        assessment = assess_direct_awards(
            select_current_contract_versions(records),
            DirectAwardRuleConfig(
                rule_version="direct-award-v1",
                observation_start="2024-01-01",
                observation_end="2026-11-05",
                observation_scope="KIMDIS PDE searches",
            ),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        self.assertIn("unrecognized", assessment.explanation)

    def test_code_label_contradiction_is_unable_to_assess(self) -> None:
        records = parse_kimdis_contracts(
            [
                {
                    "referenceNumber": "24SYMV000003",
                    "procedureType": {"key": "6", "value": "Ανοιχτή διαδικασία"},
                    "cancelled": False,
                }
            ]
        )

        assessment = assess_direct_awards(
            select_current_contract_versions(records),
            DirectAwardRuleConfig(
                rule_version="direct-award-v1",
                observation_start="2024-01-01",
                observation_end="2026-11-05",
                observation_scope="KIMDIS PDE searches",
            ),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        self.assertIn("contradicts", assessment.explanation)

    def test_recognized_code_without_label_is_assessed_with_limitation(self) -> None:
        records = parse_kimdis_contracts(
            [
                {
                    "referenceNumber": "24SYMV000004",
                    "procedureType": {"key": "6", "value": None},
                    "cancelled": False,
                }
            ]
        )

        assessment = assess_direct_awards(
            select_current_contract_versions(records),
            DirectAwardRuleConfig(
                rule_version="direct-award-v1",
                observation_start="2024-01-01",
                observation_end="2026-11-05",
                observation_scope="KIMDIS PDE searches",
            ),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.FLAGGED)
        self.assertIn("procedure label is missing", assessment.limitations)

    def test_missing_original_record_is_unable_to_assess(self) -> None:
        records = parse_kimdis_contracts(
            [
                {
                    "referenceNumber": "25SYMV000006",
                    "prevReferenceNo": "24SYMV000005",
                    "procedureType": {
                        "key": "6",
                        "value": "Απευθείας ανάθεση",
                    },
                    "cancelled": False,
                }
            ]
        )

        assessments = assess_direct_awards(
            select_current_contract_versions(records),
            DirectAwardRuleConfig(
                rule_version="direct-award-v1",
                observation_start="2024-01-01",
                observation_end="2026-11-05",
                observation_scope="KIMDIS PDE searches",
            ),
        )

        self.assertEqual(len(assessments), 1)
        self.assertIs(assessments[0].outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        self.assertIn("original", assessments[0].explanation)
        self.assertEqual(assessments[0].evidence_record_ids, ("25SYMV000006",))

    def test_conflicting_version_procedures_are_unable_to_assess(self) -> None:
        records = parse_kimdis_contracts(
            [
                {
                    "referenceNumber": "24SYMV000007",
                    "nextRefNo": "25SYMV000008",
                    "procedureType": {"key": "1", "value": "Ανοιχτή διαδικασία"},
                    "cancelled": False,
                },
                {
                    "referenceNumber": "25SYMV000008",
                    "prevReferenceNo": "24SYMV000007",
                    "procedureType": {
                        "key": "6",
                        "value": "Απευθείας ανάθεση",
                    },
                    "cancelled": False,
                },
            ]
        )

        assessment = assess_direct_awards(
            select_current_contract_versions(records),
            DirectAwardRuleConfig(
                rule_version="direct-award-v1",
                observation_start="2024-01-01",
                observation_end="2026-11-05",
                observation_scope="KIMDIS PDE searches",
            ),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.UNABLE_TO_ASSESS)
        self.assertIn("conflicting", assessment.explanation)
        self.assertEqual(
            assessment.evidence_record_ids,
            ("24SYMV000007", "25SYMV000008"),
        )

    def test_collection_gap_is_a_limitation_not_a_negation(self) -> None:
        records = parse_kimdis_contracts(
            [
                {
                    "referenceNumber": "24SYMV000009",
                    "procedureType": {
                        "key": "6",
                        "value": "Απευθείας ανάθεση",
                    },
                    "cancelled": False,
                }
            ]
        )
        limitation = "KIMDIS collection coverage is incomplete"

        assessment = assess_direct_awards(
            select_current_contract_versions(records),
            DirectAwardRuleConfig(
                rule_version="direct-award-v1",
                observation_start="2024-01-01",
                observation_end="2026-11-05",
                observation_scope="KIMDIS PDE searches",
                coverage_limitations=(limitation,),
            ),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.FLAGGED)
        self.assertIn(limitation, assessment.limitations)

    def test_unrelated_version_conflict_does_not_negate_verified_procedure(self) -> None:
        base = {
            "referenceNumber": "24SYMV000010",
            "procedureType": {"key": "6", "value": "Απευθείας ανάθεση"},
            "cancelled": False,
        }
        records = parse_kimdis_contracts(
            [
                {**base, "totalCostWithVAT": "100.00"},
                {**base, "totalCostWithVAT": "200.00"},
            ]
        )

        assessment = assess_direct_awards(
            select_current_contract_versions(records),
            DirectAwardRuleConfig(
                rule_version="direct-award-v1",
                observation_start="2024-01-01",
                observation_end="2026-11-05",
                observation_scope="KIMDIS PDE searches",
            ),
        )[0]

        self.assertIs(assessment.outcome, AssessmentOutcome.FLAGGED)
        self.assertTrue(
            any(
                limitation.startswith("conflicting facts for references")
                for limitation in assessment.limitations
            )
        )

    def test_contract_values_do_not_generate_global_share_flags(self) -> None:
        profiles = (
            profile("111111111", contract_value=80_000),
            profile("222222222", contract_value=20_000),
        )
        config = AnomalyConfig(
            rule_version="1.0",
            observation_start="2026-01-01",
            observation_end="2026-12-31",
            rrf_aid_share_threshold=Decimal("0.50"),
            minimum_rrf_aid_cents=0,
        )

        self.assertEqual(generate_anomaly_flags(profiles, config), ())

    def test_flags_cite_only_records_that_contribute_to_the_numerator(self) -> None:
        mixed = profile("111111111", contract_value=100, aid_value=100)
        mixed = CompanyFundingProfile(
            **{
                **mixed.__dict__,
                "contract_ids": ("CURRENT", "HISTORICAL"),
                "aid_award_ids": ("RRF", "NON-RRF"),
                "current_value_contract_ids": ("CURRENT",),
                "rrf_linked_aid_award_ids": ("RRF",),
                "rrf_linked_eur_amount_award_ids": ("RRF-EUR",),
            }
        )
        config = AnomalyConfig(
            rule_version="1.0",
            observation_start="2026-01-01",
            observation_end="2026-12-31",
            rrf_aid_share_threshold=Decimal("0.5"),
            minimum_rrf_aid_cents=0,
        )

        by_route = {flag.route: flag for flag in generate_anomaly_flags((mixed,), config)}

        self.assertEqual(
            by_route[AnomalyRoute.RRF_LINKED_AID].evidence_record_ids, ("RRF-EUR",)
        )

    def test_aid_route_uses_its_own_observation_period_and_scope(self) -> None:
        config = AnomalyConfig(
            rule_version="1.0",
            observation_start="2026-01-01",
            observation_end="2026-12-31",
            rrf_aid_share_threshold=Decimal("0.5"),
            minimum_rrf_aid_cents=0,
            rrf_aid_observation_start="2024-01-01",
            rrf_aid_observation_end="2025-12-31",
            rrf_aid_scope_description="EC export country=EL; measures=all",
        )

        by_route = {
            flag.route: flag
            for flag in generate_anomaly_flags(
                (profile("111111111", contract_value=100, aid_value=100),), config
            )
        }

        aid = by_route[AnomalyRoute.RRF_LINKED_AID]
        self.assertEqual(aid.observation_start, "2024-01-01")
        self.assertEqual(aid.observation_end, "2025-12-31")
        self.assertEqual(aid.observation_scope, "EC export country=EL; measures=all")

    def test_rejects_implicit_or_invalid_thresholds(self) -> None:
        with self.assertRaises(ValueError):
            AnomalyConfig(
                rule_version="1.0",
                observation_start="2026-01-01",
                observation_end="2026-12-31",
                rrf_aid_share_threshold=Decimal("1.1"),
                minimum_rrf_aid_cents=0,
            )


if __name__ == "__main__":
    unittest.main()
