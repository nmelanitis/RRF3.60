from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from crawld.diavgeia import (
    DiavgeiaAdministrativeAct,
    DiavgeiaPublicationStatus,
    DiavgeiaSearchResult,
)
from crawld.state_aid_evidence import (
    StateAidEvidenceCollectionRequest,
    collect_state_aid_evidence,
    extract_commission_cases,
)


COMMISSION_URL = "https://example.test/case-data-SA.json"


def _case(title: str, attachment_url: str | None = None) -> dict[str, object]:
    attachments: list[dict[str, object]] = []
    if attachment_url is not None:
        attachments.append(
            {
                "metadata": {
                    "attachmentLink": [attachment_url],
                    "attachmentLanguage": ["EN"],
                    "metadataReference": ["ATT-1"],
                }
            }
        )
    return {
        "metadata": {
            "caseNumber": ["unused"],
            "caseTitle": [title],
            "caseOriginalTitle": [],
        },
        "caseAttachments": attachments,
        "decisions": [],
    }


def _write_csv(path: Path, references: tuple[str, ...]) -> None:
    rows = [
        "Award ID,Beneficiary Name,Aid Element,Aid Measure Reference",
        *(
            f"A-{number},Company {number},100.00,{reference}"
            for number, reference in enumerate(references, start=1)
        ),
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_config(path: Path, evidence: list[dict[str, object]] | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "source_url": "https://example.test/tam",
                "export_scope": {
                    "country": "EL",
                    "date_from": "2024-01-01",
                    "date_to": "2026-11-04",
                    "measure_filters": [],
                    "full_result_export": True,
                },
                "coverage": {
                    "description": "Test export",
                    "publication_thresholds": ["varies"],
                    "publication_deadlines": ["varies"],
                },
                "measure_evidence": evidence or [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class CommissionDatasetExtractionTests(unittest.TestCase):
    def test_extracts_only_exact_requested_members_across_small_chunks(self) -> None:
        dataset = json.dumps(
            {
                "SA.1": {"text": "brace } and quote \\\" stay in the string"},
                "SA.10": {"text": "must not match SA.1"},
                "SA.2": {"nested": [{"value": "✓"}]},
            },
            ensure_ascii=False,
        ).encode("utf-8")

        result = extract_commission_cases(
            BytesIO(dataset), {"SA.1", "SA.2", "SA.404"}, chunk_size=7
        )

        self.assertEqual(set(result.cases), {"SA.1", "SA.2"})
        self.assertEqual(result.cases["SA.2"]["nested"][0]["value"], "✓")
        self.assertEqual(result.dataset_sha256, sha256(dataset).hexdigest())
        self.assertEqual(result.byte_count, len(dataset))

    def test_rejects_a_truncated_dataset_instead_of_silently_using_partial_data(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "truncated|Unexpected end"):
            extract_commission_cases(
                BytesIO(b'{"SA.1":{"caseTitle":["Recovery and Resilience"]'),
                {"SA.1"},
                chunk_size=5,
            )


class StateAidEvidenceCollectionTests(unittest.TestCase):
    def test_accepts_current_commission_sa_number_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "case-data-SA.json"
            csv_path.write_text(
                "Ref-no.,Name of the beneficiary,"
                '"Aid element, expressed as full amount",SA.Number\n'
                "CURRENT-1,Example Company,100.00,SA.110\n",
                encoding="utf-8",
            )
            _write_config(config_path)
            dataset_path.write_text(
                json.dumps({"SA.110": _case("Regional investment aid")}),
                encoding="utf-8",
            )

            result = collect_state_aid_evidence(
                StateAidEvidenceCollectionRequest(
                    csv_path=csv_path,
                    config_path=config_path,
                    output_root=root / "output",
                    commission_dataset_path=dataset_path,
                    commission_dataset_url=COMMISSION_URL,
                    collected_at=datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
                    search_diavgeia=False,
                )
            )

        self.assertEqual(result.reference_count, 1)
        self.assertEqual(result.unresolved_count, 1)

    def test_downloads_missing_dataset_with_trusted_https_and_reuses_it(self) -> None:
        dataset_payload = json.dumps(
            {
                "SA.110": _case(
                    "Greek support under the Recovery and Resilience Facility"
                )
            }
        ).encode("utf-8")

        def serve_dataset_with_trusted_context(
            request: object,
            timeout: int,
            context: ssl.SSLContext | None = None,
        ) -> BytesIO:
            if (
                context is None
                or context.verify_mode != ssl.CERT_REQUIRED
                or not context.check_hostname
            ):
                raise URLError("CERTIFICATE_VERIFY_FAILED")
            return BytesIO(dataset_payload)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "cache" / "case-data-SA.json"
            _write_csv(csv_path, ("SA.110",))
            _write_config(config_path)
            first_request = StateAidEvidenceCollectionRequest(
                csv_path=csv_path,
                config_path=config_path,
                output_root=root / "output",
                commission_dataset_path=dataset_path,
                commission_dataset_url=COMMISSION_URL,
                collected_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                search_diavgeia=False,
            )

            with patch(
                "crawld.state_aid_evidence.urlopen",
                side_effect=serve_dataset_with_trusted_context,
            ):
                first_result = collect_state_aid_evidence(first_request)

            self.assertFalse(first_result.dataset_reused)
            self.assertEqual(dataset_path.read_bytes(), dataset_payload)
            self.assertEqual(first_result.linked_count, 1)

            second_request = replace(
                first_request,
                collected_at=datetime(2026, 11, 4, 12, 1, tzinfo=UTC),
            )
            with patch(
                "crawld.state_aid_evidence.urlopen",
                side_effect=AssertionError("cached dataset should be reused"),
            ):
                second_result = collect_state_aid_evidence(second_request)

            self.assertTrue(second_result.dataset_reused)
            self.assertEqual(second_result.linked_count, 1)

    def test_links_by_official_case_title_and_preserves_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "cache" / "case-data-SA.json"
            dataset_path.parent.mkdir()
            _write_csv(csv_path, ("SA.111", "SA.404"))
            _write_config(config_path)
            original_config = config_path.read_bytes()
            dataset_path.write_text(
                json.dumps(
                    {
                        "SA.111": _case(
                            "Greek support under the Recovery and Resilience Facility"
                        ),
                        "SA.999": _case("Unrelated measure"),
                    }
                ),
                encoding="utf-8",
            )

            result = collect_state_aid_evidence(
                StateAidEvidenceCollectionRequest(
                    csv_path=csv_path,
                    config_path=config_path,
                    output_root=root / "output",
                    commission_dataset_path=dataset_path,
                    commission_dataset_url=COMMISSION_URL,
                    collected_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                    search_diavgeia=False,
                )
            )

            resolved = json.loads(result.resolved_config_path.read_text())
            manifest = json.loads(result.manifest_path.read_text())
            evidence = resolved["measure_evidence"]
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["aid_measure_reference"], "SA.111")
            self.assertEqual(evidence[0]["rrf_linkage"], "linked")
            self.assertEqual(evidence[0]["source_act_id"], "SA.111")
            case_path = result.output_dir / manifest["references"][0]["case_path"]
            self.assertTrue(case_path.is_file())
            self.assertEqual(
                evidence[0]["source_sha256"], sha256(case_path.read_bytes()).hexdigest()
            )
            self.assertEqual(config_path.read_bytes(), original_config)
            self.assertEqual(result.linked_count, 1)
            self.assertEqual(result.unresolved_count, 1)

    def test_uses_english_or_greek_official_attachment_text_as_fallback(self) -> None:
        attachment_url = "https://ec.europa.eu/competition/decision.pdf"
        pdf_payload = b"test-pdf"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "case-data-SA.json"
            _write_csv(csv_path, ("SA.222",))
            _write_config(config_path)
            dataset_path.write_text(
                json.dumps({"SA.222": _case("Unrelated title", attachment_url)}),
                encoding="utf-8",
            )

            result = collect_state_aid_evidence(
                StateAidEvidenceCollectionRequest(
                    csv_path=csv_path,
                    config_path=config_path,
                    output_root=root / "output",
                    commission_dataset_path=dataset_path,
                    commission_dataset_url=COMMISSION_URL,
                    collected_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                    search_diavgeia=False,
                ),
                retrieve_document=lambda url: pdf_payload,
                extract_pdf_text=lambda payload: (
                    "This measure supports the Recovery and Resilience Facility"
                ),
            )

            resolved = json.loads(result.resolved_config_path.read_text())
            evidence = resolved["measure_evidence"][0]
            self.assertEqual(evidence["source_url"], attachment_url)
            self.assertEqual(evidence["source_sha256"], sha256(pdf_payload).hexdigest())
            self.assertEqual(result.linked_count, 1)

    def test_greek_rrf_misnomer_in_official_pdf_establishes_linkage(self) -> None:
        attachment_url = "https://ec.europa.eu/competition/decision.pdf"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "case-data-SA.json"
            _write_csv(csv_path, ("SA.224",))
            _write_config(config_path)
            dataset_path.write_text(
                json.dumps({"SA.224": _case("Unrelated title", attachment_url)}),
                encoding="utf-8",
            )

            result = collect_state_aid_evidence(
                StateAidEvidenceCollectionRequest(
                    csv_path=csv_path,
                    config_path=config_path,
                    output_root=root / "output",
                    commission_dataset_path=dataset_path,
                    commission_dataset_url=COMMISSION_URL,
                    collected_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                    search_diavgeia=False,
                ),
                retrieve_document=lambda url: b"official-pdf",
                extract_pdf_text=lambda payload: (
                    "If co-financed by Community funds "
                    "ΤΑΜΕΙΟ ΑΝΑΠΤΥΞΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ"
                ),
            )

            manifest = json.loads(result.manifest_path.read_text())
            self.assertEqual(result.linked_count, 1)
            self.assertIn(
                "ΤΑΜΕΙΟ ΑΝΑΠΤΥΞΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ",
                manifest["references"][0]["match_basis"],
            )

    def test_official_attachment_https_uses_an_explicit_trusted_ca_context(
        self,
    ) -> None:
        attachment_url = "https://ec.europa.eu/competition/decision.pdf"

        def require_trusted_context(
            request: object,
            timeout: int,
            context: ssl.SSLContext | None = None,
        ) -> BytesIO:
            if (
                context is None
                or context.verify_mode != ssl.CERT_REQUIRED
                or not context.check_hostname
            ):
                raise URLError("CERTIFICATE_VERIFY_FAILED")
            return BytesIO(b"official-pdf")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "case-data-SA.json"
            _write_csv(csv_path, ("SA.223",))
            _write_config(config_path)
            dataset_path.write_text(
                json.dumps({"SA.223": _case("Unrelated title", attachment_url)}),
                encoding="utf-8",
            )

            with patch(
                "crawld.state_aid_evidence.urlopen",
                side_effect=require_trusted_context,
            ):
                result = collect_state_aid_evidence(
                    StateAidEvidenceCollectionRequest(
                        csv_path=csv_path,
                        config_path=config_path,
                        output_root=root / "output",
                        commission_dataset_path=dataset_path,
                        commission_dataset_url=COMMISSION_URL,
                        collected_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                        search_diavgeia=False,
                    ),
                    extract_pdf_text=lambda payload: (
                        "This measure supports the Recovery and Resilience Facility"
                    ),
                )

            self.assertEqual(result.linked_count, 1)
            self.assertEqual(result.unresolved_count, 0)

    def test_does_not_overwrite_conflicting_not_linked_evidence(self) -> None:
        existing = {
            "aid_measure_reference": "SA.333",
            "rrf_linkage": "not_linked",
            "source_name": "Reviewed official decision",
            "source_url": "https://example.test/decision",
            "source_sha256": "a" * 64,
            "source_act_id": "SA.333",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "case-data-SA.json"
            _write_csv(csv_path, ("SA.333",))
            _write_config(config_path, [existing])
            dataset_path.write_text(
                json.dumps(
                    {"SA.333": _case("Recovery and Resilience Facility support")}
                ),
                encoding="utf-8",
            )

            result = collect_state_aid_evidence(
                StateAidEvidenceCollectionRequest(
                    csv_path=csv_path,
                    config_path=config_path,
                    output_root=root / "output",
                    commission_dataset_path=dataset_path,
                    commission_dataset_url=COMMISSION_URL,
                    collected_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                    search_diavgeia=False,
                )
            )

            resolved = json.loads(result.resolved_config_path.read_text())
            manifest = json.loads(result.manifest_path.read_text())
            self.assertEqual(resolved["measure_evidence"], [])
            self.assertEqual(
                json.loads(config_path.read_text())["measure_evidence"], [existing]
            )
            self.assertEqual(result.conflict_count, 1)
            self.assertEqual(manifest["references"][0]["status"], "conflict")

    def test_unrelated_or_absent_cases_remain_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "case-data-SA.json"
            _write_csv(csv_path, ("SA.444", "SA.555"))
            _write_config(config_path)
            dataset_path.write_text(
                json.dumps({"SA.444": _case("Regional transition fund")}),
                encoding="utf-8",
            )

            result = collect_state_aid_evidence(
                StateAidEvidenceCollectionRequest(
                    csv_path=csv_path,
                    config_path=config_path,
                    output_root=root / "output",
                    commission_dataset_path=dataset_path,
                    commission_dataset_url=COMMISSION_URL,
                    collected_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                    search_diavgeia=False,
                )
            )

            resolved = json.loads(result.resolved_config_path.read_text())
            self.assertEqual(resolved["measure_evidence"], [])
            self.assertEqual(result.unresolved_count, 2)

    def test_uses_exact_sa_diavgeia_subject_only_after_commission_fallback(self) -> None:
        search_hash = "b" * 64

        def diavgeia_search(term: str, raw_directory: Path) -> DiavgeiaSearchResult:
            self.assertEqual(term, "SA.666")
            return DiavgeiaSearchResult(
                query_term=term,
                query_url="https://diavgeia.gov.gr/opendata/search?term=SA.666",
                retrieved_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                acts=(
                    DiavgeiaAdministrativeAct(
                        ada="TEST-ADA",
                        protocol_number=None,
                        subject=(
                            "SA.666 - Χρηματοδότηση από το "
                            "ΤΑΜΕΙΟ ΑΝΑΚΑΜΨΗΣ ΚΑΙ ΑΝΘΕΚΤΙΚΟΤΗΤΑΣ"
                        ),
                        issue_date_ms=None,
                        organization_id=None,
                        decision_type_id=None,
                        publication_status=DiavgeiaPublicationStatus.PUBLISHED,
                        version_id=None,
                        document_url=None,
                    ),
                ),
                total_reported=1,
                results_complete=True,
                http_status=200,
                byte_count=100,
                content_sha256=search_hash,
                raw_path=raw_directory / "search.json",
                error=None,
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "case-data-SA.json"
            _write_csv(csv_path, ("SA.666",))
            _write_config(config_path)
            dataset_path.write_text("{}", encoding="utf-8")

            result = collect_state_aid_evidence(
                StateAidEvidenceCollectionRequest(
                    csv_path=csv_path,
                    config_path=config_path,
                    output_root=root / "output",
                    commission_dataset_path=dataset_path,
                    commission_dataset_url=COMMISSION_URL,
                    collected_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                ),
                collect_diavgeia=diavgeia_search,
            )

            resolved = json.loads(result.resolved_config_path.read_text())
            evidence = resolved["measure_evidence"][0]
            self.assertEqual(evidence["source_name"], "Diavgeia exact-SA search result")
            self.assertEqual(evidence["source_sha256"], search_hash)
            self.assertEqual(evidence["source_act_id"], "TEST-ADA")

    def test_diavgeia_rrf_phrase_without_exact_sa_reference_does_not_link(self) -> None:
        def broad_search(term: str, raw_directory: Path) -> DiavgeiaSearchResult:
            return DiavgeiaSearchResult(
                query_term=term,
                query_url="https://diavgeia.gov.gr/opendata/search?term=SA.667",
                retrieved_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                acts=(
                    DiavgeiaAdministrativeAct(
                        ada="UNRELATED-ADA",
                        protocol_number=None,
                        subject="Recovery and Resilience Facility - SA.999999",
                        issue_date_ms=None,
                        organization_id=None,
                        decision_type_id=None,
                        publication_status=DiavgeiaPublicationStatus.PUBLISHED,
                        version_id=None,
                        document_url=None,
                    ),
                ),
                total_reported=1,
                results_complete=True,
                http_status=200,
                byte_count=100,
                content_sha256="c" * 64,
                raw_path=raw_directory / "search.json",
                error=None,
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "case-data-SA.json"
            _write_csv(csv_path, ("SA.667",))
            _write_config(config_path)
            dataset_path.write_text("{}", encoding="utf-8")

            result = collect_state_aid_evidence(
                StateAidEvidenceCollectionRequest(
                    csv_path=csv_path,
                    config_path=config_path,
                    output_root=root / "output",
                    commission_dataset_path=dataset_path,
                    commission_dataset_url=COMMISSION_URL,
                    collected_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                ),
                collect_diavgeia=broad_search,
            )

            self.assertEqual(result.linked_count, 0)
            self.assertEqual(result.unresolved_count, 1)

    def test_diavgeia_failure_is_a_gap_not_negative_evidence(self) -> None:
        def failed_search(term: str, raw_directory: Path) -> DiavgeiaSearchResult:
            return DiavgeiaSearchResult(
                query_term=term,
                query_url="https://diavgeia.gov.gr/opendata/search",
                retrieved_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                acts=(),
                total_reported=None,
                results_complete=None,
                http_status=None,
                byte_count=None,
                content_sha256=None,
                raw_path=None,
                error="maintenance",
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "awards.csv"
            config_path = root / "config.json"
            dataset_path = root / "case-data-SA.json"
            _write_csv(csv_path, ("SA.777",))
            _write_config(config_path)
            dataset_path.write_text("{}", encoding="utf-8")

            result = collect_state_aid_evidence(
                StateAidEvidenceCollectionRequest(
                    csv_path=csv_path,
                    config_path=config_path,
                    output_root=root / "output",
                    commission_dataset_path=dataset_path,
                    commission_dataset_url=COMMISSION_URL,
                    collected_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
                ),
                collect_diavgeia=failed_search,
            )

            manifest = json.loads(result.manifest_path.read_text())
            self.assertEqual(result.unresolved_count, 1)
            self.assertEqual(manifest["references"][0]["status"], "unresolved")
            self.assertTrue(
                any("maintenance" in gap for gap in manifest["references"][0]["gaps"])
            )


if __name__ == "__main__":
    unittest.main()
