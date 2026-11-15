from pathlib import Path
import unittest

from crawld.history import (
    DecisionEffect,
    DecisionRole,
    FundingStream,
    classify_greece20_decision,
    extract_greece20_decision_text,
)


FIXTURES = Path(__file__).parent / "fixtures"


class DecisionHistoryTests(unittest.TestCase):
    def test_classifies_inclusion_fixture_from_explicit_subject(self) -> None:
        text = extract_greece20_decision_text(
            (FIXTURES / "greece20_5224393_inclusion.pdf").read_bytes()
        )

        classification = classify_greece20_decision(text)

        self.assertIs(classification.role, DecisionRole.INCLUSION)
        self.assertIsNotNone(classification.role_evidence)
        assert classification.role_evidence is not None
        self.assertIn("Ένταξη Έργου", classification.role_evidence.text)
        self.assertEqual(classification.effects, ())

    def test_keeps_modification_role_and_partial_cancellation_effect_separate(
        self,
    ) -> None:
        text = """
        ΘΕΜΑ: 2η Τροποποίηση της απόφασης ένταξης και μερική ανάκληση του έργου.
        ΑΠΟΦΑΣΗ
        Αποφασίζουμε τη μερική ανάκληση ποσού 1.234,56 ευρώ από τη χρηματοδότηση
        του Ταμείου Ανάκαμψης και Ανθεκτικότητας.
        """

        classification = classify_greece20_decision(text)

        self.assertIs(classification.role, DecisionRole.MODIFICATION)
        self.assertEqual(
            [effect.effect for effect in classification.effects],
            [DecisionEffect.PARTIAL_CANCELLATION],
        )
        self.assertEqual(classification.cancellation_amounts[0].amount_cents, 123_456)
        self.assertEqual(classification.cancellation_amounts[0].currency, "EUR")
        self.assertIs(
            classification.cancellation_amounts[0].funding_stream,
            FundingStream.RRF,
        )

    def test_does_not_infer_cancellation_from_cited_prior_act(self) -> None:
        text = """
        ΘΕΜΑ: Ένταξη του έργου δοκιμής.
        Έχοντας υπόψη την απόφαση Χ, η οποία ανακλήθηκε το 2024.
        Αποφασίζουμε την ένταξη του έργου.
        """

        classification = classify_greece20_decision(text)

        self.assertIs(classification.role, DecisionRole.INCLUSION)
        self.assertEqual(classification.effects, ())
        self.assertEqual(classification.cancellation_amounts, ())

    def test_unresolved_when_only_filename_would_imply_cancellation(self) -> None:
        classification = classify_greece20_decision(
            "Κείμενο χωρίς θέμα ή διατακτικό."
        )

        self.assertIs(classification.role, DecisionRole.UNRESOLVED)
        self.assertEqual(classification.effects, ())
        self.assertEqual(classification.unresolved_reason, "no explicit role evidence")

    def test_cancellation_only_act_has_effect_without_inclusion_role(self) -> None:
        text = """
        ΘΕΜΑ: Ανάκληση της απόφασης ένταξης του έργου.
        ΑΠΟΦΑΣΗ
        Αποφασίζουμε την ολική ανάκληση της απόφασης ένταξης.
        """

        classification = classify_greece20_decision(text)

        self.assertIs(classification.role, DecisionRole.UNRESOLVED)
        self.assertEqual(
            [effect.effect for effect in classification.effects],
            [DecisionEffect.FULL_CANCELLATION],
        )

    def test_rejected_cancellation_request_is_not_a_legal_effect(self) -> None:
        text = """
        ΘΕΜΑ: Απόρριψη αιτήματος απένταξης του έργου.
        ΑΠΟΦΑΣΗ
        Αποφασίζουμε την απόρριψη του αιτήματος απένταξης.
        """

        classification = classify_greece20_decision(text)

        self.assertEqual(classification.effects, ())

    def test_cancellation_evidence_has_page_and_character_locator(self) -> None:
        text = (
            "Σελίδα πρώτη.\f"
            "ΘΕΜΑ: Ανάκληση της απόφασης ένταξης.\nΑΠΟΦΑΣΗ\n"
            "Αποφασίζουμε την ολική ανάκληση της απόφασης ένταξης."
        )

        classification = classify_greece20_decision(text)

        evidence = classification.effects[0].evidence
        self.assertEqual(evidence.page_number, 2)
        self.assertEqual(text[evidence.char_start : evidence.char_end], evidence.text)

    def test_does_not_treat_general_budget_amount_as_cancelled_amount(self) -> None:
        text = """
        ΘΕΜΑ: Τροποποίηση της απόφασης ένταξης.
        Ο συνολικός προϋπολογισμός του έργου ανέρχεται σε 2.000.000,00 ευρώ.
        Αποφασίζουμε την τροποποίηση της απόφασης ένταξης.
        """

        classification = classify_greece20_decision(text)

        self.assertIs(classification.role, DecisionRole.MODIFICATION)
        self.assertEqual(classification.effects, ())
        self.assertEqual(classification.cancellation_amounts, ())

    def test_does_not_use_new_budget_as_cancelled_amount(self) -> None:
        text = """
        ΘΕΜΑ: Τροποποίηση και μερική ανάκληση του έργου.
        ΑΠΟΦΑΣΗ
        Αποφασίζουμε τη μερική ανάκληση του φυσικού αντικειμένου.
        Ο νέος συνολικός προϋπολογισμός είναι ποσού 2.000.000 ευρώ.
        """

        classification = classify_greece20_decision(text)

        self.assertEqual(classification.cancellation_amounts, ())

    def test_assigns_each_explicit_cancellation_amount_to_its_local_stream(
        self,
    ) -> None:
        text = """
        ΘΕΜΑ: Τροποποίηση και μερική ανάκληση χρηματοδότησης.
        ΑΠΟΦΑΣΗ
        Αποφασίζουμε τη μερική ανάκληση ποσού 100,00 ευρώ από το Ταμείο
        Ανάκαμψης. Επίσης τη μερική ανάκληση ποσού 50,00 ευρώ από εθνικούς πόρους.
        """

        classification = classify_greece20_decision(text)

        self.assertEqual(
            [amount.amount_cents for amount in classification.cancellation_amounts],
            [10_000, 5_000],
        )
        self.assertEqual(
            [amount.funding_stream for amount in classification.cancellation_amounts],
            [FundingStream.RRF, FundingStream.NATIONAL],
        )


if __name__ == "__main__":
    unittest.main()
