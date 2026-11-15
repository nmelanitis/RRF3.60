import csv
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from crawld.state_aid import (
    AidMeasureEvidence,
    AidInstrument,
    RrfAidLinkage,
    StateAidCoverage,
    StateAidExportScope,
    StateAidImportRequest,
    import_state_aid_csv,
)


CSV_TEXT = """Award ID,Beneficiary Name,Beneficiary Identifier,Beneficiary Identifier Scheme,Member State,Aid Element,Currency,Aid Instrument,Date of Granting,Aid Objective,Granting Authority,Aid Measure Reference
AWARD-1,Εταιρεία Εκτός ΚΗΜΔΗΣ,099999999,VAT,EL,100000.00,EUR,Grant,2026-01-15,Digital transition,Authority A,SA.12345
AWARD-2,Δανειολήπτης ΑΕ,094259216,VAT,EL,500000.00,EUR,Loan,2026-02-01,Investment,Authority B,SA.12345
AWARD-3,Άλλη Εταιρεία,088888888,VAT,EL,25000.00,EUR,Grant,2026-02-02,Energy,Authority C,SA.99999
"""


GREEK_HEADERS = (
    "Αριθ. αναφ.",
    "Εθνικό αναγνωριστικό ταυτότητας",
    "Επωνυμία του δικαιούχου",
    "Επωνυμία του δικαιούχου [EN]",
    "Δικαιούχος σε άλλο κράτος μέλος",
    "Στοιχείο ενίσχυσης, εκπεφρασμένο ως ακέραιο ποσό",
    "Νόμισμα",
    "Μέσο ενίσχυσης",
    "Ημερομηνία χορήγησης",
    "Στόχος της ενίσχυσης",
    "Επωνυμία της χορηγούσας αρχής",
    "Αριθ. SA",
)


CURRENT_COMMISSION_HEADERS = (
    "Country",
    "Another Beneficiary Member State",
    "Aid Measure Title",
    "Aid Measure Title [EN]",
    "SA.Number",
    "Ref-no.",
    "National ID",
    "Name of the beneficiary",
    "Name of the beneficiary [EN]",
    "Beneficiary Type",
    "Region",
    "Sector (NACE)",
    "Aid Instrument",
    "Aid Instrument [EN]",
    "Objectives of the Aid",
    "Objectives of the Aid [EN]",
    "Nominal Amount, expressed as full amount",
    "Aid element, expressed as full amount",
    "Currency",
    "Date of granting",
    "Granting Authority Name",
    "Granting Authority Name [EN]",
    "Published Date",
    "Entrusted Entity",
    "Financial Intermediaries",
    "Third country outside of the EU",
)


def current_commission_csv_text() -> str:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CURRENT_COMMISSION_HEADERS)
    instruments = (
        "Direct grant/ Interest rate subsidy",
        "Loan/ Repayable advances",
        "Repayable advances",
        "Other",
        "Tax advantage or tax exemption",
        "Provision of risk finance",
        "Guarantee (where appropriate with a reference to the Commission "
        "decision (10))",
    )
    for number, instrument in enumerate(instruments, start=1):
        writer.writerow(
            [
                "Greece",
                "",
                "Περιφερειακή επενδυτική ενίσχυση",
                "Regional investment aid",
                "SA.12345",
                f"CURRENT-{number}",
                "094259216",
                f"Example Company {number}",
                "",
                "SME",
                "Attica",
                "62",
                instrument,
                "",
                "Regional development",
                "",
                "100.00",
                "25.00",
                "EUR",
                "01/11/2026",
                "Ministry of Development",
                "",
                "02/11/2026",
                "",
                "",
                "",
            ]
        )
    return output.getvalue()


def greek_csv_text() -> str:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(GREEK_HEADERS)
    base = [
        "094316669",
        "Metlen Energy & Metals A.E.",
        "Do not prefer the English name",
        "Ελλάδα",
        "100.50",
        "EUR",
    ]
    instruments = (
        "Επιχορήγηση/Επιδότηση επιτοκίου",
        "Δάνειο/Επιστρεπτέες προκαταβολές",
        "Παροχή χρηματοδότησης επιχειρηματικού κινδύνου",
        "Άμεση επιχορήγηση",
        "Φορολογικό πλεονέκτημα ή φορολογική απαλλαγή",
        "Εγγύηση (κατά περίπτωση, με παραπομπή στην απόφαση της Επιτροπής(10))",
    )
    for number, instrument in enumerate(instruments, start=1):
        writer.writerow(
            [
                f"TM-{number}",
                *base,
                instrument,
                "01/11/2026",
                "Περιφερειακή ανάπτυξη",
                "Υπουργείο Ανάπτυξης",
                "SA.12345",
            ]
        )
    writer.writerow(
        [
            "TM-7",
            "DE183016365",
            "Foreign beneficiary",
            "",
            "Γερμανία",
            "200.00",
            "EUR",
            "Άμεση επιχορήγηση",
            "02/11/2026",
            "Έρευνα",
            "Υπουργείο Ανάπτυξης",
            "SA.99999",
        ]
    )
    writer.writerow(
        [
            "TM-8",
            "94316669",
            "Invalid Greek VAT",
            "",
            "Ελλάδα",
            "300.00",
            "EUR",
            "Άμεση επιχορήγηση",
            "03/11/2026",
            "Έρευνα",
            "Υπουργείο Ανάπτυξης",
            "SA.99999",
        ]
    )
    return output.getvalue()


def measure_evidence(
    reference: str, linkage: RrfAidLinkage
) -> AidMeasureEvidence:
    return AidMeasureEvidence(
        aid_measure_reference=reference,
        rrf_linkage=linkage,
        source_name="Official measure decision",
        source_url=f"https://competition-cases.ec.europa.eu/{reference}",
        source_sha256="a" * 64,
        source_act_id=reference,
    )


def import_request(
    source: Path, *, include_non_rrf: bool = False
) -> StateAidImportRequest:
    evidence = [measure_evidence("SA.12345", RrfAidLinkage.LINKED)]
    if include_non_rrf:
        evidence.append(measure_evidence("SA.99999", RrfAidLinkage.NOT_LINKED))
    return StateAidImportRequest(
        csv_path=source,
        source_url="https://webgate.ec.europa.eu/competition/transparency/public",
        imported_at=datetime(2026, 11, 2, tzinfo=UTC),
        measure_evidence=tuple(evidence),
        export_scope=StateAidExportScope(
            country="EL",
            date_from="2024-01-01",
            date_to="2026-11-02",
            measure_filters=(),
            full_result_export=True,
        ),
        coverage=StateAidCoverage(
            description="Full Greece result export; not exhaustive of all aid.",
            publication_thresholds=("Threshold varies by legal basis",),
            publication_deadlines=("Deadline varies by legal basis",),
        ),
    )


class StateAidTests(unittest.TestCase):
    def test_explicit_english_rrf_measure_name_links_without_configured_evidence(
        self,
    ) -> None:
        csv_text = (
            "Award ID,Beneficiary Name,Beneficiary Identifier,Member State,"
            "Aid Element,Currency,Aid Instrument,Aid Measure Reference,"
            "Aid Measure Name\n"
            "RRF-1,Example Company,094259216,EL,100.00,EUR,Grant,SA.77777,"
            "Loan support under the Recovery and Resilience Facility\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "rrf-name.csv"
            source.write_text(csv_text, encoding="utf-8")
            request = replace(import_request(source), measure_evidence=())
            result = import_state_aid_csv(request, root / "raw")

        award = result.awards[0]
        self.assertIs(award.rrf_linkage, RrfAidLinkage.LINKED)
        self.assertIn("Recovery and Resilience", award.linkage_evidence)
        assert award.measure_evidence is not None
        self.assertEqual(
            award.measure_evidence.source_sha256,
            sha256(csv_text.encode("utf-8")).hexdigest(),
        )

    def test_greek_rrf_measure_name_is_used_when_english_name_does_not_match(
        self,
    ) -> None:
        csv_text = (
            "Award ID,Beneficiary Name,Beneficiary Identifier,Member State,"
            "Aid Element,Currency,Aid Instrument,Aid Measure Reference,"
            "Ονομασία του μέτρου ενίσχυσης [EN],"
            "Ονομασία του μέτρου ενίσχυσης\n"
            "RRF-EL-1,Example Company,094259216,Ελλάδα,100.00,EUR,"
            "Άμεση επιχορήγηση,SA.77778,Recovery and Health Fund,"
            "ΤΑΜΕΙΟ ΑΝΑΚΑΜΨΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ - Υποέργο δοκιμής\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "greek-rrf-name.csv"
            source.write_text(csv_text, encoding="utf-8")
            request = replace(import_request(source), measure_evidence=())
            result = import_state_aid_csv(request, root / "raw")

        award = result.awards[0]
        self.assertIs(award.rrf_linkage, RrfAidLinkage.LINKED)
        self.assertIn(
            "ΤΑΜΕΙΟ ΑΝΑΚΑΜΨΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ",
            award.linkage_evidence,
        )

    def test_greek_rrf_misnomer_in_measure_name_establishes_linkage(self) -> None:
        csv_text = (
            "Award ID,Beneficiary Name,Beneficiary Identifier,Member State,"
            "Aid Element,Currency,Aid Instrument,Aid Measure Reference,"
            "Ονομασία του μέτρου ενίσχυσης [EN],"
            "Ονομασία του μέτρου ενίσχυσης\n"
            "RRF-EL-2,Example Company,094259216,Ελλάδα,100.00,EUR,"
            "Άμεση επιχορήγηση,SA.77781,Regional investment aid,"
            "ΤΑΜΕΙΟ ΑΝΑΠΤΥΞΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ - Υποέργο δοκιμής\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "greek-rrf-misnomer.csv"
            source.write_text(csv_text, encoding="utf-8")
            request = replace(import_request(source), measure_evidence=())
            result = import_state_aid_csv(request, root / "raw")

        award = result.awards[0]
        self.assertIs(award.rrf_linkage, RrfAidLinkage.LINKED)
        self.assertIn(
            "ΤΑΜΕΙΟ ΑΝΑΠΤΥΞΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ",
            award.linkage_evidence,
        )

    def test_unrelated_recovery_word_does_not_establish_rrf_linkage(self) -> None:
        csv_text = (
            "Award ID,Beneficiary Name,Beneficiary Identifier,Member State,"
            "Aid Element,Currency,Aid Instrument,Aid Measure Reference,"
            "Aid Measure Name,Ονομασία του μέτρου ενίσχυσης\n"
            "NON-RRF-1,Example Company,094259216,EL,100.00,EUR,Grant,SA.77779,"
            "Recovery of the Minoic palace,Αποκατάσταση Μινωικού ανακτόρου\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unrelated-recovery.csv"
            source.write_text(csv_text, encoding="utf-8")
            request = replace(import_request(source), measure_evidence=())
            result = import_state_aid_csv(request, root / "raw")

        self.assertIs(result.awards[0].rrf_linkage, RrfAidLinkage.UNRESOLVED)

    def test_positive_measure_name_conflicting_with_sa_evidence_is_unresolved(
        self,
    ) -> None:
        csv_text = (
            "Award ID,Beneficiary Name,Beneficiary Identifier,Member State,"
            "Aid Element,Currency,Aid Instrument,Aid Measure Reference,"
            "Aid Measure Name\n"
            "CONFLICT-1,Example Company,094259216,EL,100.00,EUR,Grant,SA.77780,"
            "Support under the Recovery and Resilience Facility\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "conflicting-evidence.csv"
            source.write_text(csv_text, encoding="utf-8")
            request = replace(
                import_request(source),
                measure_evidence=(
                    measure_evidence("SA.77780", RrfAidLinkage.NOT_LINKED),
                ),
            )
            result = import_state_aid_csv(request, root / "raw")

        award = result.awards[0]
        self.assertIs(award.rrf_linkage, RrfAidLinkage.UNRESOLVED)
        self.assertIsNone(award.measure_evidence)
        self.assertIn("conflicts", award.linkage_evidence)

    def test_imports_current_greek_commission_export_shape(self) -> None:
        csv_text = greek_csv_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "greek-tam-export.csv"
            source.write_text(csv_text, encoding="utf-8-sig")
            result = import_state_aid_csv(import_request(source), root / "raw")

        awards = {award.award_id: award for award in result.awards}
        metlen = awards["TM-1"]
        assert metlen.beneficiary_identifier is not None
        self.assertEqual(metlen.beneficiary_name, "Metlen Energy & Metals A.E.")
        self.assertEqual(metlen.beneficiary_identifier.scheme, "EL_VAT")
        self.assertEqual(metlen.beneficiary_identifier.country, "GR")
        self.assertEqual(metlen.beneficiary_identifier.value, "094316669")
        self.assertEqual(metlen.aid_element_cents, 10_050)
        self.assertEqual(metlen.granting_date, "01/11/2026")
        self.assertEqual(metlen.objective, "Περιφερειακή ανάπτυξη")
        self.assertEqual(metlen.granting_authority, "Υπουργείο Ανάπτυξης")
        self.assertEqual(metlen.aid_measure_reference, "SA.12345")
        self.assertEqual(
            [awards[f"TM-{number}"].instrument for number in range(1, 7)],
            [
                AidInstrument.GRANT,
                AidInstrument.LOAN,
                AidInstrument.OTHER,
                AidInstrument.GRANT,
                AidInstrument.TAX_ADVANTAGE,
                AidInstrument.GUARANTEE,
            ],
        )
        foreign = awards["TM-7"].beneficiary_identifier
        assert foreign is not None
        self.assertEqual(foreign.scheme, "STATE_AID_BENEFICIARY_ID")
        self.assertEqual(foreign.country, "DE")
        self.assertEqual(foreign.value, "DE183016365")
        self.assertIsNone(awards["TM-8"].beneficiary_identifier)
        self.assertIn(
            "9 digits", awards["TM-8"].beneficiary_identifier_issue or ""
        )

    def test_imports_current_commission_export_headers_and_instruments(self) -> None:
        csv_text = current_commission_csv_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "current-commission-export.csv"
            source.write_text(csv_text, encoding="utf-8")
            result = import_state_aid_csv(import_request(source), root / "raw")

        awards = {award.award_id: award for award in result.awards}
        self.assertEqual(result.source_row_count, 7)
        self.assertEqual(result.imported_award_count, 7)
        self.assertEqual(result.skipped_rows, ())
        self.assertEqual(result.field_gaps, ())
        self.assertEqual(result.duplicate_conflicts, ())
        self.assertEqual(
            [awards[f"CURRENT-{number}"].instrument for number in range(1, 8)],
            [
                AidInstrument.GRANT,
                AidInstrument.LOAN,
                AidInstrument.LOAN,
                AidInstrument.OTHER,
                AidInstrument.TAX_ADVANTAGE,
                AidInstrument.OTHER,
                AidInstrument.GUARANTEE,
            ],
        )
        first = awards["CURRENT-1"]
        self.assertEqual(first.beneficiary_name, "Example Company 1")
        self.assertEqual(first.aid_element_cents, 2_500)
        self.assertEqual(first.aid_measure_reference, "SA.12345")
        self.assertEqual(first.objective, "Regional development")
        self.assertEqual(first.granting_authority, "Ministry of Development")

    def test_imports_full_export_and_links_only_by_exact_measure_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tam-export.csv"
            source.write_text(CSV_TEXT, encoding="utf-8")
            result = import_state_aid_csv(
                import_request(source, include_non_rrf=True),
                root / "raw",
            )

            self.assertEqual(len(result.awards), 3)
            self.assertEqual(result.content_sha256, sha256(CSV_TEXT.encode()).hexdigest())
            self.assertEqual(result.raw_path.read_text(encoding="utf-8"), CSV_TEXT)
            self.assertEqual(result.source_row_count, 3)
            self.assertEqual(result.imported_award_count, 3)
            self.assertEqual(result.skipped_rows, ())
            self.assertIn("not exhaustive", result.coverage_note)
            absent_from_kimdis = result.awards[0]
            self.assertEqual(absent_from_kimdis.beneficiary_name, "Εταιρεία Εκτός ΚΗΜΔΗΣ")
            self.assertEqual(absent_from_kimdis.beneficiary_identifier.value, "099999999")
            self.assertEqual(absent_from_kimdis.aid_measure_reference, "SA.12345")
            self.assertIs(absent_from_kimdis.rrf_linkage, RrfAidLinkage.LINKED)
            self.assertIn("exact aid measure reference", absent_from_kimdis.linkage_evidence)
            self.assertEqual(
                absent_from_kimdis.measure_evidence.source_sha256, "a" * 64
            )
            self.assertFalse(hasattr(absent_from_kimdis, "pde_code"))
            self.assertIs(result.awards[2].rrf_linkage, RrfAidLinkage.NOT_LINKED)

    def test_loans_are_retained_but_excluded_from_reported_aid_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tam-export.csv"
            source.write_text(CSV_TEXT, encoding="utf-8")
            result = import_state_aid_csv(
                import_request(source),
                root / "raw",
            )

        loan = result.awards[1]
        self.assertIs(loan.instrument, AidInstrument.LOAN)
        self.assertFalse(loan.included_in_aid_aggregate)
        self.assertEqual(loan.exclusion_reason, "loan instrument excluded by policy")
        self.assertEqual(loan.aid_element_cents, 50_000_000)

    def test_unknown_measure_reference_remains_unresolved_not_not_linked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tam-export.csv"
            source.write_text(CSV_TEXT, encoding="utf-8")
            result = import_state_aid_csv(
                import_request(source),
                root / "raw",
            )

        self.assertIs(result.awards[2].rrf_linkage, RrfAidLinkage.UNRESOLVED)

    def test_local_storage_failure_is_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tam-export.csv"
            source.write_text(CSV_TEXT, encoding="utf-8")
            blocked = root / "not-a-directory"
            blocked.write_text("occupied", encoding="utf-8")
            request = import_request(source)

            with self.assertRaises(FileExistsError):
                import_state_aid_csv(request, blocked)

    def test_opaque_non_vat_identifier_is_not_rewritten(self) -> None:
        csv_text = CSV_TEXT.replace(
            "099999999,VAT,EL", "ELABC123,LEI,EL", 1
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tam-export.csv"
            source.write_text(csv_text, encoding="utf-8")
            result = import_state_aid_csv(import_request(source), root / "raw")

        identifier = result.awards[0].beneficiary_identifier
        assert identifier is not None
        self.assertEqual(identifier.scheme, "LEI")
        self.assertEqual(identifier.value, "ELABC123")

    def test_invalid_greek_vat_cannot_cross_route_merge(self) -> None:
        csv_text = CSV_TEXT.replace("099999999,VAT,EL", "123,VAT,EL", 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tam-export.csv"
            source.write_text(csv_text, encoding="utf-8")
            result = import_state_aid_csv(import_request(source), root / "raw")

        self.assertIsNone(result.awards[0].beneficiary_identifier)
        self.assertIn("9 digits", result.awards[0].beneficiary_identifier_issue or "")

    def test_duplicate_awards_deduplicate_identical_and_surface_conflicts(self) -> None:
        header, first, second, third = CSV_TEXT.splitlines()
        conflicting = first.replace("100000.00", "200000.00")
        csv_text = "\n".join([header, first, first, second, third, conflicting, ""])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tam-export.csv"
            source.write_text(csv_text, encoding="utf-8")
            result = import_state_aid_csv(import_request(source), root / "raw")

        self.assertEqual({award.award_id for award in result.awards}, {"AWARD-2", "AWARD-3"})
        self.assertEqual(len(result.duplicate_conflicts), 1)
        self.assertEqual(result.duplicate_conflicts[0].award_id, "AWARD-1")
        self.assertEqual(result.duplicate_conflicts[0].source_row_numbers, (2, 3, 6))

    def test_explicit_instrument_mapping_excludes_unknown_and_repayable_advance(self) -> None:
        csv_text = CSV_TEXT.replace("Grant,2026-01-15", "Repayable advance,2026-01-15", 1)
        csv_text = csv_text.replace("Loan,2026-02-01", "Loan guarantee,2026-02-01", 1)
        csv_text = csv_text.replace("Grant,2026-02-02", "Mystery instrument,2026-02-02", 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tam-export.csv"
            source.write_text(csv_text, encoding="utf-8")
            result = import_state_aid_csv(import_request(source), root / "raw")

        self.assertIs(result.awards[0].instrument, AidInstrument.LOAN)
        self.assertFalse(result.awards[0].included_in_aid_aggregate)
        self.assertIs(result.awards[1].instrument, AidInstrument.GUARANTEE)
        self.assertTrue(result.awards[1].included_in_aid_aggregate)
        self.assertIs(result.awards[2].instrument, AidInstrument.UNKNOWN)
        self.assertFalse(result.awards[2].included_in_aid_aggregate)

    def test_non_finite_amount_is_kept_as_visible_field_gap(self) -> None:
        csv_text = CSV_TEXT.replace("100000.00", "NaN", 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tam-export.csv"
            source.write_text(csv_text, encoding="utf-8")
            result = import_state_aid_csv(import_request(source), root / "raw")

        self.assertIsNone(result.awards[0].aid_element_cents)
        self.assertEqual(result.skipped_rows, ())
        self.assertEqual(len(result.field_gaps), 1)
        self.assertIn("aid element", result.field_gaps[0].reason)

    def test_resolved_linkage_cannot_be_constructed_without_matching_evidence(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "requires official measure evidence"):
            from crawld.state_aid import StateAidAward

            StateAidAward(
                award_id="A-1",
                beneficiary_name="Test",
                beneficiary_identifier=None,
                beneficiary_identifier_issue="missing",
                aid_element_cents=1,
                currency="EUR",
                instrument=AidInstrument.GRANT,
                raw_instrument="Grant",
                granting_date=None,
                objective=None,
                granting_authority=None,
                aid_measure_reference="SA.12345",
                rrf_linkage=RrfAidLinkage.LINKED,
                linkage_evidence="unsupported",
                measure_evidence=None,
                included_in_aid_aggregate=True,
                exclusion_reason=None,
                source_row_number=2,
            )


if __name__ == "__main__":
    unittest.main()
