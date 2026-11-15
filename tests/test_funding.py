from pathlib import Path
import unittest

from crawld.funding import (
    RrfProjectFundingReference,
    extract_rrf_project_funding_references,
)


FIXTURE = Path(__file__).parent / "fixtures" / "greece20_5224393_inclusion.pdf"


class RrfProjectFundingReferenceTests(unittest.TestCase):
    def test_extracts_ops_and_pde_codes_from_inclusion_decision(self) -> None:
        references = extract_rrf_project_funding_references(FIXTURE.read_bytes())

        self.assertEqual(
            references,
            [
                RrfProjectFundingReference(
                    ops_code="5224393",
                    pde_code="2024ΤΑ09100006",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
