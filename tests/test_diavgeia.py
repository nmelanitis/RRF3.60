from datetime import UTC
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from crawld.diavgeia import (
    DiavgeiaPublicationStatus,
    collect_diavgeia_search,
    parse_diavgeia_version_log,
)


class JsonResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.status = 200

    def __enter__(self) -> "JsonResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self) -> bytes:
        return self.payload


class DiavgeiaTests(unittest.TestCase):
    def test_search_preserves_query_raw_response_and_publication_lifecycle(
        self,
    ) -> None:
        payload = json.dumps(
            {
                "decisions": [
                    {
                        "ada": "ΑΒΓΔ-123",
                        "protocolNumber": "123/2026",
                        "subject": "Τροποποίηση πράξης",
                        "issueDate": 1_798_761_600_000,
                        "organizationId": "100013990",
                        "decisionTypeId": "Β.1.3",
                        "status": "revoked",
                        "versionId": 4,
                        "documentUrl": "https://diavgeia.gov.gr/doc/ΑΒΓΔ-123",
                    }
                ],
                "info": {"total": 1},
            },
            ensure_ascii=False,
        ).encode()
        requests: list[str] = []

        def retrieve(request: object, timeout: int) -> JsonResponse:
            requests.append(str(getattr(request, "full_url")))
            return JsonResponse(payload)

        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.diavgeia.urlopen", side_effect=retrieve
        ):
            result = collect_diavgeia_search(
                "2024ΤΑ09100006", Path(directory) / "raw"
            )

            self.assertIn("/opendata/search?", requests[0])
            self.assertIn("term=2024", requests[0])
            self.assertEqual(result.query_term, "2024ΤΑ09100006")
            self.assertEqual(result.content_sha256, sha256(payload).hexdigest())
            self.assertEqual(result.byte_count, len(payload))
            self.assertEqual(result.http_status, 200)
            self.assertEqual(result.raw_path.read_bytes(), payload)
            self.assertEqual(result.retrieved_at.tzinfo, UTC)
            self.assertIsNone(result.error)
            self.assertEqual(result.total_reported, 1)
            self.assertTrue(result.results_complete)
            self.assertEqual(result.acts[0].ada, "ΑΒΓΔ-123")
            self.assertIs(
                result.acts[0].publication_status,
                DiavgeiaPublicationStatus.REVOKED,
            )
            self.assertEqual(result.acts[0].version_id, "4")
            self.assertFalse(hasattr(result.acts[0], "legal_effect"))

    def test_failed_search_is_a_visible_source_gap(self) -> None:
        url = "https://diavgeia.gov.gr/opendata/search"
        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.diavgeia.urlopen",
            side_effect=HTTPError(url, 503, "Unavailable", None, None),
        ):
            result = collect_diavgeia_search("5224393", Path(directory) / "raw")

        self.assertEqual(result.acts, ())
        self.assertIsNone(result.raw_path)
        self.assertIn("HTTP Error 503", result.error or "")

    def test_truncated_search_result_is_marked_incomplete(self) -> None:
        payload = json.dumps(
            {
                "decisions": [{"ada": "ΑΒΓΔ-123", "status": "published"}],
                "info": {"total": 501},
            },
            ensure_ascii=False,
        ).encode()
        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.diavgeia.urlopen", return_value=JsonResponse(payload)
        ):
            result = collect_diavgeia_search("5224393", Path(directory) / "raw")

        self.assertFalse(result.results_complete)
        self.assertEqual(result.total_reported, 501)

    def test_local_storage_failure_is_fatal_not_a_source_gap(self) -> None:
        payload = json.dumps({"decisions": [], "info": {"total": 0}}).encode()
        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.diavgeia.urlopen", return_value=JsonResponse(payload)
        ):
            blocked = Path(directory) / "not-a-directory"
            blocked.write_text("occupied", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                collect_diavgeia_search("5224393", blocked)

    def test_version_log_keeps_versions_as_publication_events(self) -> None:
        versions = parse_diavgeia_version_log(
            {
                "versions": [
                    {"versionId": 1, "status": "published", "timestamp": 1000},
                    {"versionId": 2, "status": "revoked", "timestamp": 2000},
                ]
            }
        )

        self.assertEqual([version.version_id for version in versions], ["1", "2"])
        self.assertEqual(
            [version.publication_status for version in versions],
            [
                DiavgeiaPublicationStatus.PUBLISHED,
                DiavgeiaPublicationStatus.REVOKED,
            ],
        )
        self.assertEqual([version.observed_at_ms for version in versions], [1000, 2000])


if __name__ == "__main__":
    unittest.main()
