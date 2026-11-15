from datetime import UTC
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from crawld.decisions import (
    collect_greece20_decision_pdf,
    collect_greece20_decision_pdfs,
)
from crawld.funding import RrfProjectFundingReference


FIXTURE = Path(__file__).parent / "fixtures" / "greece20_5224393_inclusion.pdf"


class PdfResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "PdfResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self) -> bytes:
        return self.payload


class DecisionCollectionTests(unittest.TestCase):
    def test_preserves_decision_with_provenance_and_funding_references(self) -> None:
        source_bytes = FIXTURE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            result = collect_greece20_decision_pdf(
                FIXTURE.as_uri(), Path(directory) / "raw"
            )

            self.assertEqual(result.source_url, FIXTURE.as_uri())
            self.assertEqual(result.content_sha256, sha256(source_bytes).hexdigest())
            self.assertEqual(result.byte_count, len(source_bytes))
            self.assertEqual(result.retrieved_at.tzinfo, UTC)
            self.assertEqual(result.raw_path.read_bytes(), source_bytes)
            self.assertEqual(
                result.funding_references,
                (
                    RrfProjectFundingReference(
                        ops_code="5224393",
                        pde_code="2024ΤΑ09100006",
                    ),
                ),
            )

    def test_batch_deduplicates_urls_and_preserves_successes_after_failure(
        self,
    ) -> None:
        source_bytes = FIXTURE.read_bytes()
        corrupt_bytes = b""
        malformed_url = ""
        decision_url = "https://example.test/decision.pdf"
        missing_url = "https://example.test/missing.pdf"
        corrupt_url = "https://example.test/corrupt.pdf"
        requested_urls: list[str] = []

        def retrieve(request: object, timeout: int) -> PdfResponse:
            source_url = str(getattr(request, "full_url"))
            requested_urls.append(source_url)
            if source_url == missing_url:
                raise HTTPError(source_url, 404, "Not Found", None, None)
            if source_url == corrupt_url:
                return PdfResponse(corrupt_bytes)
            return PdfResponse(source_bytes)

        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.decisions.urlopen", side_effect=retrieve
        ):
            result = collect_greece20_decision_pdfs(
                [
                    malformed_url,
                    missing_url,
                    decision_url,
                    decision_url,
                    corrupt_url,
                ],
                Path(directory) / "raw",
            )

            self.assertEqual(requested_urls, [missing_url, decision_url, corrupt_url])
            self.assertEqual(result.source_count, 4)
            self.assertEqual(result.collected_count, 1)
            self.assertEqual(result.failed_count, 3)
            self.assertEqual(result.collected_decisions[0].source_url, decision_url)
            self.assertEqual(
                result.collected_decisions[0].raw_path.read_bytes(), source_bytes
            )
            malformed_failure = result.failed_decisions[0]
            self.assertEqual(malformed_failure.source_url, malformed_url)
            self.assertIn("ValueError", malformed_failure.error)
            self.assertIsNone(malformed_failure.preserved_decision)

            self.assertEqual(result.failed_decisions[1].source_url, missing_url)
            self.assertIn("HTTP Error 404", result.failed_decisions[1].error)
            self.assertIsNone(result.failed_decisions[1].preserved_decision)

            parse_failure = result.failed_decisions[2]
            self.assertEqual(parse_failure.source_url, corrupt_url)
            self.assertIsNotNone(parse_failure.preserved_decision)
            assert parse_failure.preserved_decision is not None
            self.assertEqual(
                parse_failure.preserved_decision.content_sha256,
                sha256(corrupt_bytes).hexdigest(),
            )
            self.assertEqual(parse_failure.preserved_decision.byte_count, 0)
            self.assertEqual(
                parse_failure.preserved_decision.raw_path.read_bytes(), corrupt_bytes
            )

    def test_batch_does_not_hide_local_storage_failure(self) -> None:
        source_bytes = FIXTURE.read_bytes()
        decision_url = "https://example.test/decision.pdf"

        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.decisions.urlopen", return_value=PdfResponse(source_bytes)
        ):
            blocked_path = Path(directory) / "not-a-directory"
            blocked_path.write_text("occupied", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                collect_greece20_decision_pdfs([decision_url], blocked_path)


if __name__ == "__main__":
    unittest.main()
