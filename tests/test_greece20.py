from pathlib import Path
import unittest

from crawld.greece20 import parse_catalogue


FIXTURE = Path(__file__).parent / "fixtures" / "greece20_catalogue.html"


class Greece20CatalogueTests(unittest.TestCase):
    def test_parses_budget_links_and_repeated_ops_rows(self) -> None:
        entries = parse_catalogue(FIXTURE.read_text(encoding="utf-8"))

        self.assertEqual(len(entries), 3)
        self.assertEqual([entry.ops_code for entry in entries[:2]], ["5000001", "5000001"])
        self.assertEqual(entries[0].source_budget_cents, 123456)
        self.assertEqual(entries[0].decision_url, "https://example.test/decision-a.pdf")
        self.assertFalse(entries[0].is_modification)
        self.assertTrue(entries[1].is_modification)
        self.assertNotEqual(entries[0].row_hash, entries[1].row_hash)


if __name__ == "__main__":
    unittest.main()

