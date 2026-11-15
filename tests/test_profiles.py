from dataclasses import replace
import unittest

from crawld.kimdis import (
    CompanyIdentifier,
    parse_kimdis_contracts,
    select_current_contract_versions,
)
from crawld.profiles import build_company_profiles
from crawld.state_aid import (
    AidInstrument,
    AidMeasureEvidence,
    RrfAidLinkage,
    StateAidAward,
)


def contract_record(reference: str, afms: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "referenceNumber": reference,
        "totalCostWithVAT": "1240.00",
        "cancelled": False,
        "contractingDataDetails": {
            "contractingMembersDataList": [
                {
                    "vatNumber": afm,
                    "greekVatNumber": True,
                    "name": name,
                    "country": "GR",
                }
                for afm, name in afms
            ]
        },
    }


def aid_award(
    award_id: str,
    afm: str,
    name: str,
    amount_cents: int,
    *,
    linkage: RrfAidLinkage = RrfAidLinkage.LINKED,
    instrument: AidInstrument = AidInstrument.GRANT,
) -> StateAidAward:
    loan = instrument is AidInstrument.LOAN
    evidence = AidMeasureEvidence(
        aid_measure_reference="SA.12345",
        rrf_linkage=linkage,
        source_name="Test official source",
        source_url="https://example.test/measure",
        source_sha256="a" * 64,
        source_act_id="SA.12345",
    ) if linkage is not RrfAidLinkage.UNRESOLVED else None
    return StateAidAward(
        award_id=award_id,
        beneficiary_name=name,
        beneficiary_identifier=CompanyIdentifier("EL_VAT", "GR", afm),
        beneficiary_identifier_issue=None,
        aid_element_cents=amount_cents,
        currency="EUR",
        instrument=instrument,
        raw_instrument=instrument.value,
        granting_date="2026-01-01",
        objective="Test",
        granting_authority="Authority",
        aid_measure_reference="SA.12345",
        rrf_linkage=linkage,
        linkage_evidence="test evidence",
        measure_evidence=evidence,
        included_in_aid_aggregate=not loan,
        exclusion_reason="loan instrument excluded by policy" if loan else None,
        source_row_number=2,
    )


class CompanyProfileTests(unittest.TestCase):
    def test_keeps_contract_and_aid_totals_separate(self) -> None:
        contracts = select_current_contract_versions(
            parse_kimdis_contracts(
                [contract_record("24SYMV000001", [("094259216", "Εταιρεία Α")])]
            )
        )
        awards = (aid_award("A-1", "094259216", "Εταιρεία Α", 50_000),)

        profile = build_company_profiles(contracts, awards).profiles[0]

        self.assertEqual(profile.contract_count, 1)
        self.assertEqual(profile.active_contract_count, 1)
        self.assertEqual(profile.current_contract_value_cents, 124_000)
        self.assertEqual(profile.aid_award_count, 1)
        self.assertEqual(profile.aid_amount_eur_cents, 50_000)
        self.assertEqual(profile.rrf_linked_aid_count, 1)
        self.assertEqual(profile.current_value_contract_ids, ("24SYMV000001",))
        self.assertEqual(profile.rrf_linked_aid_award_ids, ("A-1",))
        self.assertEqual(profile.rrf_linked_eur_amount_award_ids, ("A-1",))
        self.assertFalse(hasattr(profile, "combined_funding_total_cents"))

    def test_aid_beneficiary_absent_from_kimdis_still_has_profile(self) -> None:
        result = build_company_profiles(
            (), (aid_award("A-1", "099999999", "Μόνο ενίσχυση", 75_000),)
        )

        self.assertEqual(len(result.profiles), 1)
        self.assertEqual(result.profiles[0].identifier.value, "099999999")
        self.assertEqual(result.profiles[0].contract_count, 0)
        self.assertEqual(result.profiles[0].aid_award_count, 1)

    def test_same_name_with_different_afm_does_not_merge(self) -> None:
        awards = (
            aid_award("A-1", "099999999", "Κοινό Όνομα", 10_000),
            aid_award("A-2", "088888888", "Κοινό Όνομα", 20_000),
        )

        result = build_company_profiles((), awards)

        self.assertEqual(len(result.profiles), 2)
        self.assertEqual(
            {profile.identifier.value for profile in result.profiles},
            {"099999999", "088888888"},
        )

    def test_consortium_members_get_count_but_not_duplicated_full_value(self) -> None:
        contracts = select_current_contract_versions(
            parse_kimdis_contracts(
                [
                    contract_record(
                        "24SYMV000001",
                        [("094259216", "Εταίρος Α"), ("099999999", "Εταίρος Β")],
                    )
                ]
            )
        )

        profiles = build_company_profiles(contracts, ()).profiles

        self.assertEqual(len(profiles), 2)
        for profile in profiles:
            self.assertEqual(profile.contract_count, 1)
            self.assertEqual(profile.current_contract_value_cents, 0)
            self.assertEqual(profile.unresolved_contract_value_count, 1)

    def test_loans_are_counted_as_excluded_not_added_to_aid_totals(self) -> None:
        awards = (
            aid_award(
                "A-1",
                "094259216",
                "Εταιρεία Α",
                5_000_000,
                instrument=AidInstrument.LOAN,
            ),
        )

        profile = build_company_profiles((), awards).profiles[0]

        self.assertEqual(profile.aid_award_count, 0)
        self.assertEqual(profile.aid_amount_eur_cents, 0)
        self.assertEqual(profile.excluded_loan_count, 1)
        self.assertEqual(profile.excluded_loan_amount_eur_cents, 5_000_000)

    def test_replaced_historical_contractor_is_not_marked_active(self) -> None:
        original = contract_record("24SYMV000001", [("094259216", "Παλαιός")])
        original["nextRefNo"] = "25SYMV000002"
        amendment = contract_record("25SYMV000002", [("099999999", "Νέος")])
        amendment["prevReferenceNo"] = "24SYMV000001"
        contracts = select_current_contract_versions(
            parse_kimdis_contracts([original, amendment])
        )

        profiles = build_company_profiles(contracts, ()).profiles
        by_afm = {profile.identifier.value: profile for profile in profiles}

        self.assertEqual(by_afm["094259216"].contract_count, 1)
        self.assertEqual(by_afm["094259216"].active_contract_count, 0)
        self.assertEqual(by_afm["094259216"].current_contract_value_cents, 0)
        self.assertEqual(by_afm["094259216"].current_value_contract_ids, ())
        self.assertEqual(by_afm["099999999"].active_contract_count, 1)
        self.assertEqual(by_afm["099999999"].current_contract_value_cents, 124_000)
        self.assertEqual(
            by_afm["099999999"].current_value_contract_ids,
            ("24SYMV000001",),
        )

    def test_duplicate_inputs_do_not_inflate_amounts(self) -> None:
        contract = select_current_contract_versions(
            parse_kimdis_contracts(
                [contract_record("24SYMV000001", [("094259216", "Εταιρεία Α")])]
            )
        )[0]
        award = aid_award("A-1", "094259216", "Εταιρεία Α", 50_000)

        profile = build_company_profiles(
            (contract, contract), (award, award)
        ).profiles[0]

        self.assertEqual(profile.contract_count, 1)
        self.assertEqual(profile.current_contract_value_cents, 124_000)
        self.assertEqual(profile.aid_award_count, 1)
        self.assertEqual(profile.aid_amount_eur_cents, 50_000)

    def test_conflicting_same_ids_are_surfaced_and_not_aggregated(self) -> None:
        award = aid_award("A-1", "094259216", "Εταιρεία Α", 50_000)
        conflict = replace(award, aid_element_cents=60_000)

        result = build_company_profiles((), (award, conflict))

        self.assertEqual(result.profiles, ())
        self.assertEqual(result.aid_award_conflicts, ("A-1",))

    def test_unknown_exclusion_is_not_mislabeled_as_loan(self) -> None:
        award = replace(
            aid_award("A-1", "094259216", "Εταιρεία Α", 50_000),
            instrument=AidInstrument.UNKNOWN,
            raw_instrument="Mystery",
            included_in_aid_aggregate=False,
            exclusion_reason="unknown aid instrument excluded pending policy",
        )

        profile = build_company_profiles((), (award,)).profiles[0]

        self.assertEqual(profile.excluded_loan_count, 0)
        self.assertEqual(profile.excluded_other_aid_count, 1)

    def test_missing_eur_amount_is_not_reported_as_non_eur(self) -> None:
        award = replace(
            aid_award("A-1", "094259216", "Εταιρεία Α", 50_000),
            aid_element_cents=None,
        )

        profile = build_company_profiles((), (award,)).profiles[0]

        self.assertEqual(profile.non_eur_aid_count, 0)
        self.assertEqual(profile.missing_aid_amount_or_currency_count, 1)

    def test_only_eur_linked_awards_with_amounts_are_anomaly_contributors(self) -> None:
        eur = aid_award("EUR", "094259216", "Company", 100)
        usd = replace(
            aid_award("USD", "094259216", "Company", 999), currency="USD"
        )
        missing = replace(
            aid_award("MISSING", "094259216", "Company", 1),
            aid_element_cents=None,
        )

        profile = build_company_profiles((), (eur, usd, missing)).profiles[0]

        self.assertEqual(
            profile.rrf_linked_aid_award_ids, ("EUR", "MISSING", "USD")
        )
        self.assertEqual(profile.rrf_linked_aid_amount_eur_cents, 100)
        self.assertEqual(profile.rrf_linked_eur_amount_award_ids, ("EUR",))

    def test_current_version_without_identifier_is_visible_coverage_gap(self) -> None:
        original = contract_record("24SYMV000001", [("094259216", "Παλαιός")])
        original["nextRefNo"] = "25SYMV000002"
        amendment = contract_record("25SYMV000002", [("099999999", "Χωρίς ΑΦΜ")])
        amendment["prevReferenceNo"] = "24SYMV000001"
        details = amendment["contractingDataDetails"]
        assert isinstance(details, dict)
        members = details["contractingMembersDataList"]
        assert isinstance(members, list) and isinstance(members[0], dict)
        members[0].pop("vatNumber")
        contracts = select_current_contract_versions(
            parse_kimdis_contracts([original, amendment])
        )

        result = build_company_profiles(contracts, ())

        self.assertEqual(result.contracts_without_current_identifiers, ("24SYMV000001",))


if __name__ == "__main__":
    unittest.main()
