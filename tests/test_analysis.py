from datetime import UTC, date, datetime
from contextlib import closing
from dataclasses import replace
import csv
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from crawld.analysis import (
    AnalysisArtifactRequest,
    _spreadsheet_safe,
    build_analysis_artifact,
)
from crawld.anomalies import AnomalyConfig
from crawld.diavgeia import DiavgeiaSearchResult
from crawld.kimdis import (
    KimdisContractCollectionResult,
    KimdisContractPageResult,
    KimdisContractSearch,
    parse_kimdis_contracts,
    split_kimdis_search_windows,
)
from crawld.state_aid import (
    AidMeasureEvidence,
    RrfAidLinkage,
    StateAidCoverage,
    StateAidExportScope,
    StateAidImportRequest,
    import_state_aid_csv,
)


FIXTURE = Path(__file__).parent / "fixtures" / "greece20_5224393_inclusion.pdf"


def make_source_snapshot(root: Path) -> Path:
    snapshot = root / "source-snapshot"
    raw = snapshot / "raw" / "decisions"
    raw.mkdir(parents=True)
    pdf_path = raw / "decision.pdf"
    pdf_path.write_bytes(FIXTURE.read_bytes())
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "snapshot_id": "snapshot-1",
                "cutoff": "2026-10-30T23:59:59+02:00",
                "status": "complete",
            }
        ),
        encoding="utf-8",
    )
    connection = sqlite3.connect(snapshot / "snapshot.sqlite3")
    connection.executescript(
        """
        CREATE TABLE greece20_decisions (
            decision_id INTEGER PRIMARY KEY,
            source_url TEXT,
            status TEXT,
            raw_path TEXT,
            content_sha256 TEXT,
            byte_count INTEGER
        );
        CREATE TABLE rrf_project_funding_references (
            decision_id INTEGER, ops_code TEXT, pde_code TEXT
        );
        CREATE TABLE rrf_catalogue_entries (
            entry_id INTEGER PRIMARY KEY,
            ops_code TEXT,
            decision_url TEXT
        );
        """
    )
    source_url = "https://greece20.gov.gr/decision.pdf"
    connection.execute(
        "INSERT INTO greece20_decisions VALUES (1, ?, 'collected', ?, ?, ?)",
        (
            source_url,
            str(pdf_path.relative_to(snapshot)),
            sha256(FIXTURE.read_bytes()).hexdigest(),
            len(FIXTURE.read_bytes()),
        ),
    )
    connection.execute(
        "INSERT INTO rrf_project_funding_references VALUES (1, '5224393', '2024ΤΑ09100006')"
    )
    connection.execute(
        "INSERT INTO rrf_catalogue_entries VALUES (1, '5224393', ?)",
        (source_url,),
    )
    connection.commit()
    connection.close()
    return snapshot


def make_state_aid_import(root: Path):
    source = root / "state-aid.csv"
    source.write_text(
        "Award ID,Beneficiary Name,Beneficiary Identifier,Beneficiary Identifier Scheme,Member State,Aid Element,Currency,Aid Instrument,Aid Measure Reference\n"
        "A-1,<Acme & Sons>,094259216,VAT,EL,500.00,EUR,Grant,SA.12345\n",
        encoding="utf-8",
    )
    return import_state_aid_csv(
        StateAidImportRequest(
            csv_path=source,
            source_url="https://webgate.ec.europa.eu/competition/transparency/public",
            imported_at=datetime(2026, 11, 2, tzinfo=UTC),
            measure_evidence=(
                AidMeasureEvidence(
                    aid_measure_reference="SA.12345",
                    rrf_linkage=RrfAidLinkage.LINKED,
                    source_name="Commission case decision",
                    source_url="https://competition-cases.ec.europa.eu/SA.12345",
                    source_sha256="a" * 64,
                    source_act_id="SA.12345",
                ),
            ),
            export_scope=StateAidExportScope(
                country="EL",
                date_from="2024-01-01",
                date_to="2026-11-02",
                measure_filters=(),
                full_result_export=True,
            ),
            coverage=StateAidCoverage(
                description="Full Greece export; not exhaustive of all aid.",
                publication_thresholds=("Varies by legal basis",),
                publication_deadlines=("Varies by legal basis",),
            ),
        ),
        root / "staging-raw",
    )


class AnalysisArtifactTests(unittest.TestCase):
    def test_repeated_state_aid_negative_depends_on_aid_export_coverage(self):
        for full_export, expected in ((True, "not_flagged"), (False, "unable_to_assess")):
            with self.subTest(full_export=full_export), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                request = self._request(root, make_source_snapshot(root))
                imported = request.state_aid_imports[0]
                imported = replace(imported, export_scope=replace(imported.export_scope, full_result_export=full_export))
                result = build_analysis_artifact(replace(request, state_aid_imports=(imported,)))
                with closing(sqlite3.connect(result.analysis_dir / "analysis.sqlite3")) as connection:
                    outcome = connection.execute("SELECT outcome FROM red_flag_assessments WHERE rule_id='repeated_state_aid'").fetchone()[0]
                self.assertEqual(outcome, expected)

    def test_repeated_state_aid_uses_export_scope_and_persists_linkages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root, make_source_snapshot(root))
            imported = request.state_aid_imports[0]
            csv_path = root / "state-aid.csv"
            csv_path.write_text(csv_path.read_text().replace(
                "A-1,<Acme & Sons>,094259216,VAT,EL,500.00,EUR,Grant,SA.12345\n",
                "A-1,<Acme & Sons>,094259216,VAT,EL,500.00,EUR,Grant,SA.12345\n"
                "A-2,<Acme & Sons>,094259216,VAT,EL,300.00,EUR,Grant,SA.99999\n"
            ))
            imported = import_state_aid_csv(StateAidImportRequest(
                csv_path=csv_path, source_url=imported.source_url,
                imported_at=imported.imported_at,
                measure_evidence=tuple(a.measure_evidence for a in imported.awards if a.measure_evidence),
                export_scope=imported.export_scope, coverage=imported.coverage,
            ), root / "second-raw")
            request = replace(request, state_aid_imports=(imported,), anomaly_config=replace(
                request.anomaly_config, observation_start="2026-01-01",
                rrf_aid_observation_start="2024-01-01", rrf_aid_observation_end="2026-11-02",
            ))
            result = build_analysis_artifact(request)
            with closing(sqlite3.connect(result.analysis_dir / "analysis.sqlite3")) as connection:
                row = connection.execute("SELECT outcome, route, observation_start, observation_end, evidence_record_ids, observed_facts FROM red_flag_assessments WHERE rule_id='repeated_state_aid'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[:4], ("flagged", "state_aid", "2024-01-01", "2026-11-02"))
            self.assertEqual(json.loads(row[4]), ["A-1", "A-2"])
            facts = {f["name"]: f["value"] for f in json.loads(row[5])}
            self.assertEqual(facts["award_rrf_linkage:A-1"], "linked")
            self.assertEqual(facts["award_rrf_linkage:A-2"], "unresolved")
            with (result.analysis_dir / "red_flag_assessments.csv").open() as stream:
                self.assertEqual(sum(r["rule_id"] == "repeated_state_aid" for r in csv.DictReader(stream)), 1)
            self.assertIn("repeated_state_aid", (result.analysis_dir / "report.html").read_text())
            manifest = json.loads((result.analysis_dir / "manifest.json").read_text())
            self.assertEqual(manifest["red_flag_assessment_counts_by_rule"]["repeated_state_aid"]["flagged"], 1)

    def _request(self, root: Path, snapshot: Path, *, output_root: Path | None = None):
        return AnalysisArtifactRequest(
            source_snapshot=snapshot,
            output_root=output_root or root / "analyses",
            generated_at=datetime(2026, 11, 2, tzinfo=UTC),
            kimdis_collections=(),
            state_aid_imports=(make_state_aid_import(root),),
            diavgeia_searches=(),
            anomaly_config=AnomalyConfig(
                rule_version="1.0",
                observation_start="2024-01-01",
                observation_end="2026-11-02",
                rrf_aid_share_threshold=Decimal("0.5"),
                minimum_rrf_aid_cents=0,
            ),
        )

    def test_missing_source_database_is_rejected_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            (snapshot / "snapshot.sqlite3").unlink()

            with self.assertRaisesRegex(ValueError, "snapshot database"):
                build_analysis_artifact(self._request(root, snapshot))

            self.assertFalse((snapshot / "snapshot.sqlite3").exists())

    def test_direct_award_assessment_is_persisted_with_raw_page_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            payload = json.dumps(
                {
                    "content": [
                        {
                            "referenceNumber": "24SYMV000001",
                            "procedureType": {
                                "key": "6",
                                "value": "Απευθείας ανάθεση",
                            },
                            "cancelled": False,
                        }
                    ],
                    "totalPages": 1,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            raw = root / "kimdis-direct.json"
            raw.write_bytes(payload)
            search = KimdisContractSearch(
                pde_code="2024ΤΑ09100006",
                date_from=date(2026, 6, 1),
                date_to=date(2026, 11, 2),
            )
            contracts = parse_kimdis_contracts(json.loads(payload)["content"])
            page = KimdisContractPageResult(
                search=search,
                source_url="https://cerpp.eprocurement.gov.gr/khmdhs-opendata/contract?page=0",
                retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
                contracts=contracts,
                total_pages=1,
                is_last_page=True,
                http_status=200,
                byte_count=len(payload),
                content_sha256=sha256(payload).hexdigest(),
                raw_path=raw,
                error=None,
            )
            collection = KimdisContractCollectionResult(
                pde_code=search.pde_code,
                date_from=search.date_from,
                date_to=search.date_to,
                page_results=(page,),
                contracts=contracts,
                coverage_complete=True,
            )
            request = replace(
                self._request(root, snapshot),
                kimdis_collections=(collection,),
                anomaly_config=replace(
                    self._request(root, snapshot).anomaly_config,
                    observation_start="2026-06-01",
                    observation_end="2026-11-02",
                    rrf_aid_observation_start="2024-01-01",
                    rrf_aid_observation_end="2026-11-02",
                ),
            )

            result = build_analysis_artifact(request)

            connection = sqlite3.connect(result.analysis_dir / "analysis.sqlite3")
            try:
                assessment = connection.execute(
                    "SELECT outcome, subject_identifier, evidence_record_ids "
                    "FROM red_flag_assessments"
                ).fetchone()
                version = connection.execute(
                    "SELECT procedure_code, procedure_label "
                    "FROM kimdis_contract_versions"
                ).fetchone()
                provenance = connection.execute(
                    "SELECT page_content_sha256, source_url, raw_path "
                    "FROM kimdis_contract_record_sources"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(
                assessment,
                ("flagged", "24SYMV000001", '["24SYMV000001"]'),
            )
            self.assertEqual(version, ("6", "Απευθείας ανάθεση"))
            self.assertEqual(provenance[0], sha256(payload).hexdigest())
            self.assertIn("khmdhs-opendata/contract", provenance[1])
            self.assertTrue(provenance[2].startswith("raw/"))
            with (result.analysis_dir / "red_flag_assessments.csv").open(
                encoding="utf-8", newline=""
            ) as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["outcome"], "flagged")
            self.assertEqual(row["subject_identifier"], "24SYMV000001")
            html = (result.analysis_dir / "report.html").read_text(encoding="utf-8")
            self.assertIn("Red flags and inconclusive assessments", html)
            self.assertIn("24SYMV000001", html)
            manifest = json.loads(
                (result.analysis_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["red_flag_assessment_count"], 4)
            self.assertEqual(manifest["red_flag_count"], 1)
            self.assertEqual(manifest["red_flag_unable_to_assess_count"], 2)
            self.assertEqual(
                manifest["red_flag_assessment_counts_by_rule"]["direct_award"],
                {"flagged": 1, "not_flagged": 0, "unable_to_assess": 0},
            )

    def test_contract_value_increase_is_persisted_with_both_source_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            content = [
                {"referenceNumber": "ROOT", "totalCostWithVAT": 100, "cancelled": False},
                {"referenceNumber": "CURRENT", "prevReferenceNo": "ROOT", "totalCostWithVAT": 150, "cancelled": False},
            ]
            payload = json.dumps({"content": content, "totalPages": 1}).encode()
            raw = root / "kimdis-increase.json"
            raw.write_bytes(payload)
            search = KimdisContractSearch(pde_code="2024ΤΑ09100006", date_from=date(2026, 6, 1), date_to=date(2026, 11, 2))
            contracts = parse_kimdis_contracts(content)
            page = KimdisContractPageResult(
                search=search, source_url="https://cerpp.eprocurement.gov.gr/khmdhs-opendata/contract?page=0",
                retrieved_at=datetime(2026, 11, 2, tzinfo=UTC), contracts=contracts,
                total_pages=1, is_last_page=True, http_status=200, byte_count=len(payload),
                content_sha256=sha256(payload).hexdigest(), raw_path=raw, error=None,
            )
            collection = KimdisContractCollectionResult(
                pde_code=search.pde_code, date_from=search.date_from, date_to=search.date_to,
                page_results=(page,), contracts=contracts, coverage_complete=True,
            )
            base = self._request(root, snapshot)
            result = build_analysis_artifact(replace(
                base, kimdis_collections=(collection,), anomaly_config=replace(
                    base.anomaly_config, observation_start="2026-06-01", observation_end="2026-11-02",
                    rrf_aid_observation_start="2024-01-01", rrf_aid_observation_end="2026-11-02",
                ),
            ))
            with sqlite3.connect(result.analysis_dir / "analysis.sqlite3") as connection:
                row = connection.execute("SELECT outcome, evidence_record_ids, observed_facts FROM red_flag_assessments WHERE rule_id='contract_value_increase'").fetchone()
                sources = connection.execute("SELECT COUNT(*) FROM kimdis_contract_record_sources").fetchone()[0]
            connection.close()
            self.assertIsNotNone(row)
            self.assertEqual(row[:2], ("flagged", '["ROOT", "CURRENT"]'))
            facts = {fact["name"]: fact["value"] for fact in json.loads(row[2])}
            self.assertEqual(facts["original_value_cents"], "10000")
            self.assertEqual(facts["current_value_cents"], "15000")
            self.assertEqual(facts["increase_percent"], "50")
            self.assertEqual(sources, 2)
            with (result.analysis_dir / "red_flag_assessments.csv").open() as stream:
                rows = [row for row in csv.DictReader(stream) if row["rule_id"] == "contract_value_increase"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["outcome"], "flagged")
            self.assertIn("contract_value_increase", (result.analysis_dir / "report.html").read_text())
            manifest = json.loads((result.analysis_dir / "manifest.json").read_text())
            self.assertEqual(manifest["red_flag_assessment_counts_by_rule"]["contract_value_increase"], {"flagged": 1, "not_flagged": 0, "unable_to_assess": 0})

    def test_repeated_contract_assessment_uses_config_and_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            content = [
                {
                    "referenceNumber": f"26SYMV00000{index}",
                    "contractSignedDate": f"2026-10-0{index}",
                    "procedureType": {
                        "key": "1",
                        "value": "Ανοιχτή διαδικασία",
                    },
                    "cancelled": False,
                    "contractingDataDetails": {
                        "contractingMembersDataList": [
                            {
                                "country": {"key": "GR", "value": "Ελλάδα"},
                                "vatNumber": "082525697",
                                "greekVatNumber": False,
                                "name": "Example company",
                            }
                        ]
                    },
                }
                for index in range(1, 4)
            ]
            payload = json.dumps(
                {"content": content, "totalPages": 1}, ensure_ascii=False
            ).encode("utf-8")
            raw = root / "kimdis-repeated.json"
            raw.write_bytes(payload)
            search = KimdisContractSearch(
                pde_code="2024ΤΑ09100006",
                date_from=date(2026, 6, 1),
                date_to=date(2026, 11, 2),
            )
            contracts = parse_kimdis_contracts(content)
            page = KimdisContractPageResult(
                search=search,
                source_url="https://cerpp.eprocurement.gov.gr/khmdhs-opendata/contract?page=0",
                retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
                contracts=contracts,
                total_pages=1,
                is_last_page=True,
                http_status=200,
                byte_count=len(payload),
                content_sha256=sha256(payload).hexdigest(),
                raw_path=raw,
                error=None,
            )
            collection = KimdisContractCollectionResult(
                pde_code=search.pde_code,
                date_from=search.date_from,
                date_to=search.date_to,
                page_results=(page,),
                contracts=contracts,
                coverage_complete=True,
            )
            base_request = self._request(root, snapshot)
            request = replace(
                base_request,
                kimdis_collections=(collection,),
                anomaly_config=replace(
                    base_request.anomaly_config,
                    observation_start="2026-06-01",
                    observation_end="2026-11-02",
                    rrf_aid_observation_start="2024-01-01",
                    rrf_aid_observation_end="2026-11-02",
                    repeated_contract_minimum=3,
                    repeated_contract_window_days=30,
                ),
            )

            result = build_analysis_artifact(request)

            connection = sqlite3.connect(result.analysis_dir / "analysis.sqlite3")
            try:
                repeated = connection.execute(
                    "SELECT outcome, subject_type, subject_identifier, "
                    "evidence_record_ids FROM red_flag_assessments "
                    "WHERE rule_id='repeated_contracts'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(
                repeated,
                (
                    "flagged",
                    "company",
                    "VAT:GR:082525697",
                    '["26SYMV000001", "26SYMV000002", "26SYMV000003"]',
                ),
            )
            with (result.analysis_dir / "red_flag_assessments.csv").open(
                encoding="utf-8", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(
                sum(row["rule_id"] == "repeated_contracts" for row in rows), 1
            )
            html = (result.analysis_dir / "report.html").read_text(encoding="utf-8")
            self.assertIn("Red flags and inconclusive assessments", html)
            self.assertIn("<th>Subject</th>", html)
            self.assertIn("repeated_contracts", html)
            manifest = json.loads(
                (result.analysis_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["red_flag_assessment_counts_by_rule"]["repeated_contracts"],
                {"flagged": 1, "not_flagged": 0, "unable_to_assess": 0},
            )
            self.assertEqual(
                manifest["anomaly_config"]["repeated_contract_minimum"], 3
            )
            self.assertEqual(
                manifest["anomaly_config"]["repeated_contract_window_days"], 30
            )

    def test_contract_without_preserved_page_record_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            payload = b'{"content": [], "totalPages": 1}'
            raw = root / "kimdis-empty.json"
            raw.write_bytes(payload)
            contracts = parse_kimdis_contracts(
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
            search = KimdisContractSearch(
                pde_code="2024ΤΑ09100006",
                date_from=date(2026, 6, 1),
                date_to=date(2026, 11, 2),
            )
            page = KimdisContractPageResult(
                search=search,
                source_url="https://example.test/kimdis?page=0",
                retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
                contracts=(),
                total_pages=1,
                is_last_page=True,
                http_status=200,
                byte_count=len(payload),
                content_sha256=sha256(payload).hexdigest(),
                raw_path=raw,
                error=None,
            )
            collection = KimdisContractCollectionResult(
                pde_code=search.pde_code,
                date_from=search.date_from,
                date_to=search.date_to,
                page_results=(page,),
                contracts=contracts,
                coverage_complete=True,
            )
            base_request = self._request(root, snapshot)
            request = replace(
                base_request,
                kimdis_collections=(collection,),
                anomaly_config=replace(
                    base_request.anomaly_config,
                    observation_start="2026-06-01",
                    observation_end="2026-11-02",
                    rrf_aid_observation_start="2024-01-01",
                    rrf_aid_observation_end="2026-11-02",
                ),
            )

            with self.assertRaisesRegex(ValueError, "preserved page records"):
                build_analysis_artifact(request)

            published = [
                path
                for path in (root / "analyses").iterdir()
                if not path.name.startswith(".")
            ]
            self.assertEqual(published, [])

    def test_raw_page_count_mismatch_is_rejected_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            payload = json.dumps(
                {
                    "content": [
                        {
                            "referenceNumber": "24SYMV000011",
                            "procedureType": {
                                "key": "6",
                                "value": "Απευθείας ανάθεση",
                            },
                            "cancelled": False,
                        }
                    ],
                    "totalPages": 2,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            raw = root / "kimdis-page-count-mismatch.json"
            raw.write_bytes(payload)
            contracts = parse_kimdis_contracts(json.loads(payload)["content"])
            search = KimdisContractSearch(
                pde_code="2024ΤΑ09100006",
                date_from=date(2026, 6, 1),
                date_to=date(2026, 11, 2),
            )
            page = KimdisContractPageResult(
                search=search,
                source_url="https://example.test/kimdis?page=0",
                retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
                contracts=contracts,
                total_pages=1,
                is_last_page=True,
                http_status=200,
                byte_count=len(payload),
                content_sha256=sha256(payload).hexdigest(),
                raw_path=raw,
                error=None,
            )
            collection = KimdisContractCollectionResult(
                pde_code=search.pde_code,
                date_from=search.date_from,
                date_to=search.date_to,
                page_results=(page,),
                contracts=contracts,
                coverage_complete=True,
            )
            base_request = self._request(root, snapshot)
            request = replace(
                base_request,
                kimdis_collections=(collection,),
                anomaly_config=replace(
                    base_request.anomaly_config,
                    observation_start="2026-06-01",
                    observation_end="2026-11-02",
                    rrf_aid_observation_start="2024-01-01",
                    rrf_aid_observation_end="2026-11-02",
                ),
            )

            with self.assertRaisesRegex(ValueError, "page count"):
                build_analysis_artifact(request)

            published = [
                path
                for path in (root / "analyses").iterdir()
                if not path.name.startswith(".")
            ]
            self.assertEqual(published, [])

    def test_page_contract_without_matching_raw_record_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            payload = b'{"content": [], "totalPages": 1}'
            raw = root / "kimdis-empty.json"
            raw.write_bytes(payload)
            contracts = parse_kimdis_contracts(
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
            search = KimdisContractSearch(
                pde_code="2024ΤΑ09100006",
                date_from=date(2026, 6, 1),
                date_to=date(2026, 11, 2),
            )
            page = KimdisContractPageResult(
                search=search,
                source_url="https://example.test/kimdis?page=0",
                retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
                contracts=contracts,
                total_pages=1,
                is_last_page=True,
                http_status=200,
                byte_count=len(payload),
                content_sha256=sha256(payload).hexdigest(),
                raw_path=raw,
                error=None,
            )
            collection = KimdisContractCollectionResult(
                pde_code=search.pde_code,
                date_from=search.date_from,
                date_to=search.date_to,
                page_results=(page,),
                contracts=contracts,
                coverage_complete=True,
            )
            base_request = self._request(root, snapshot)
            request = replace(
                base_request,
                kimdis_collections=(collection,),
                anomaly_config=replace(
                    base_request.anomaly_config,
                    observation_start="2026-06-01",
                    observation_end="2026-11-02",
                    rrf_aid_observation_start="2024-01-01",
                    rrf_aid_observation_end="2026-11-02",
                ),
            )

            with self.assertRaisesRegex(ValueError, "raw KIMDIS page"):
                build_analysis_artifact(request)

            published = [
                path
                for path in (root / "analyses").iterdir()
                if not path.name.startswith(".")
            ]
            self.assertEqual(published, [])

    def test_output_root_cannot_be_inside_source_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)

            with self.assertRaisesRegex(ValueError, "outside the source snapshot"):
                build_analysis_artifact(
                    self._request(root, snapshot, output_root=snapshot / "analyses")
                )

    def test_rejects_analysis_periods_that_mislabel_imported_source_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            request = self._request(root, snapshot)
            request = replace(
                request,
                anomaly_config=replace(
                    request.anomaly_config,
                    rrf_aid_observation_start="2025-01-01",
                    rrf_aid_observation_end="2025-12-31",
                ),
            )

            with self.assertRaisesRegex(ValueError, "State-aid observation period"):
                build_analysis_artifact(request)

    def test_failed_report_write_never_publishes_final_analysis_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)

            with patch("crawld.analysis._write_html", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    build_analysis_artifact(self._request(root, snapshot))

            published = [
                path
                for path in (root / "analyses").iterdir()
                if not path.name.startswith(".")
            ]
            self.assertEqual(published, [])

    def test_failed_source_decision_and_partial_export_are_coverage_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            connection = sqlite3.connect(snapshot / "snapshot.sqlite3")
            connection.execute(
                "INSERT INTO greece20_decisions VALUES (2, ?, 'failed', NULL, NULL, NULL)",
                ("https://greece20.gov.gr/failed.pdf",),
            )
            connection.commit()
            connection.close()
            imported = make_state_aid_import(root)
            imported = replace(
                imported,
                export_scope=replace(imported.export_scope, full_result_export=False),
            )
            request = replace(
                self._request(root, snapshot),
                state_aid_imports=(imported,),
                diavgeia_searches=(
                    DiavgeiaSearchResult(
                        query_term="5224393",
                        query_url="https://example.test/diavgeia",
                        retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
                        acts=(),
                        total_reported=None,
                        results_complete=None,
                        http_status=200,
                        byte_count=None,
                        content_sha256=None,
                        raw_path=None,
                        error=None,
                    ),
                ),
            )

            result = build_analysis_artifact(request)

            connection = sqlite3.connect(result.analysis_dir / "analysis.sqlite3")
            try:
                rows = connection.execute(
                    "SELECT source_name, query_description, complete, error "
                    "FROM source_coverage ORDER BY coverage_id"
                ).fetchall()
            finally:
                connection.close()
            self.assertTrue(any("failed.pdf" in row[1] for row in rows))
            self.assertTrue(
                any(row[0] == "EC State Aid Transparency" and row[2] == 0 for row in rows)
            )
            self.assertTrue(any(row[0] == "Diavgeia" and row[2] == 0 for row in rows))

    def test_builds_derived_artifact_without_modifying_source_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            source_hashes_before = {
                path.name: sha256(path.read_bytes()).hexdigest()
                for path in (snapshot / "manifest.json", snapshot / "snapshot.sqlite3")
            }
            contract_value = {
                "referenceNumber": "24SYMV000001",
                "totalCostWithVAT": "1240.00",
                "cancelled": False,
                "fundingDetails": {
                    "publicFundingRefNum": "2024ΤΑ09100006",
                    "publicFundingRefOps": "5224393",
                },
                "contractingDataDetails": {
                    "contractingMembersDataList": [
                        {
                            "vatNumber": "094259216",
                            "greekVatNumber": True,
                            "name": "<Acme & Sons>",
                            "country": "GR",
                        }
                    ]
                },
            }
            contract = parse_kimdis_contracts([contract_value])[0]
            kimdis_pages_list: list[KimdisContractPageResult] = []
            for index, window in enumerate(
                split_kimdis_search_windows(
                    "2024ΤΑ09100006", date(2024, 1, 1), date(2026, 11, 2)
                )
            ):
                page_payload = json.dumps(
                    {
                        "content": [contract_value] if index == 0 else [],
                        "totalPages": 1,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                page_path = root / f"kimdis-page-{index}.json"
                page_path.write_bytes(page_payload)
                kimdis_pages_list.append(
                    KimdisContractPageResult(
                        search=window,
                        source_url=f"https://example.test/kimdis?page={index}",
                        retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
                        contracts=(contract,) if index == 0 else (),
                        total_pages=1,
                        is_last_page=True,
                        http_status=200,
                        byte_count=len(page_payload),
                        content_sha256=sha256(page_payload).hexdigest(),
                        raw_path=page_path,
                        error=None,
                    )
                )
            kimdis_pages = tuple(kimdis_pages_list)
            kimdis = KimdisContractCollectionResult(
                pde_code="2024ΤΑ09100006",
                date_from=date(2024, 1, 1),
                date_to=date(2026, 11, 2),
                page_results=kimdis_pages,
                contracts=(contract,),
                coverage_complete=True,
            )
            diavgeia = DiavgeiaSearchResult(
                query_term="5224393",
                query_url="https://diavgeia.gov.gr/opendata/search?term=5224393",
                retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
                acts=(),
                total_reported=None,
                results_complete=None,
                http_status=None,
                byte_count=None,
                content_sha256=None,
                raw_path=None,
                error="HTTPError: maintenance",
            )
            request = AnalysisArtifactRequest(
                source_snapshot=snapshot,
                output_root=root / "analyses",
                generated_at=datetime(2026, 11, 2, tzinfo=UTC),
                kimdis_collections=(kimdis,),
                state_aid_imports=(make_state_aid_import(root),),
                diavgeia_searches=(diavgeia,),
                anomaly_config=AnomalyConfig(
                    rule_version="1.0",
                    observation_start="2024-01-01",
                    observation_end="2026-11-02",
                    rrf_aid_share_threshold=Decimal("0.5"),
                    minimum_rrf_aid_cents=0,
                ),
            )

            result = build_analysis_artifact(request)

            source_hashes_after = {
                path.name: sha256(path.read_bytes()).hexdigest()
                for path in (snapshot / "manifest.json", snapshot / "snapshot.sqlite3")
            }
            self.assertEqual(source_hashes_after, source_hashes_before)
            self.assertEqual(result.status, "complete_with_gaps")
            for filename in (
                "analysis.sqlite3",
                "company_profiles.csv",
                "anomaly_flags.csv",
                "report.html",
                "manifest.json",
            ):
                self.assertTrue((result.analysis_dir / filename).is_file())

            connection = sqlite3.connect(result.analysis_dir / "analysis.sqlite3")
            try:
                link_bases = {
                    row[0]
                    for row in connection.execute(
                        "SELECT link_basis FROM project_decision_links"
                    )
                }
                role = connection.execute(
                    "SELECT role FROM greece20_decision_interpretations"
                ).fetchone()[0]
                profile = connection.execute(
                    "SELECT current_contract_value_cents, aid_amount_eur_cents "
                    "FROM company_profiles"
                ).fetchone()
                profile_columns = [
                    "contract_count",
                    "active_contract_count",
                    "cancelled_contract_count",
                    "current_contract_value_cents",
                    "unresolved_contract_value_count",
                    "aid_award_count",
                    "aid_amount_eur_cents",
                    "rrf_linked_aid_count",
                    "rrf_linked_aid_amount_eur_cents",
                    "non_rrf_aid_count",
                    "non_rrf_aid_amount_eur_cents",
                    "unresolved_aid_count",
                    "unresolved_aid_amount_eur_cents",
                    "non_eur_aid_count",
                    "missing_aid_amount_or_currency_count",
                    "excluded_loan_count",
                    "excluded_loan_amount_eur_cents",
                    "excluded_other_aid_count",
                    "excluded_other_aid_amount_eur_cents",
                ]
                sqlite_metrics = connection.execute(
                    f"SELECT {','.join(profile_columns)} FROM company_profiles"
                ).fetchone()
                measure_evidence = connection.execute(
                    "SELECT source_url, source_sha256, source_act_id "
                    "FROM state_aid_measure_evidence"
                ).fetchone()
                award_evidence = connection.execute(
                    "SELECT linkage_evidence, measure_evidence_sha256, raw_instrument, "
                    "granting_date, objective, granting_authority, "
                    "beneficiary_identifier_issue "
                    "FROM state_aid_awards"
                ).fetchone()
                kimdis_page = connection.execute(
                    "SELECT source_url, content_sha256, raw_path FROM kimdis_pages"
                ).fetchone()
                incomplete_coverage_count = connection.execute(
                    "SELECT COUNT(*) FROM source_coverage WHERE complete=0"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(link_bases, {"catalogue_row", "pdf_extracted"})
            self.assertEqual(role, "inclusion")
            self.assertEqual(profile, (124_000, 50_000))
            with (result.analysis_dir / "company_profiles.csv").open(
                encoding="utf-8", newline=""
            ) as stream:
                csv_profile = next(csv.DictReader(stream))
            self.assertEqual(
                {column: int(csv_profile[column]) for column in profile_columns},
                dict(zip(profile_columns, sqlite_metrics, strict=True)),
            )
            self.assertEqual(
                measure_evidence,
                (
                    "https://competition-cases.ec.europa.eu/SA.12345",
                    "a" * 64,
                    "SA.12345",
                ),
            )
            self.assertIn("exact aid measure reference", award_evidence[0])
            self.assertEqual(award_evidence[1], "a" * 64)
            self.assertEqual(award_evidence[2], "Grant")
            self.assertEqual(award_evidence[3:], (None, None, None, None))
            self.assertEqual(kimdis_page[0], "https://example.test/kimdis?page=0")
            self.assertTrue(kimdis_page[2].startswith("raw/"))
            self.assertEqual(
                kimdis_page[1],
                sha256((result.analysis_dir / kimdis_page[2]).read_bytes()).hexdigest(),
            )
            html = (result.analysis_dir / "report.html").read_text(encoding="utf-8")
            self.assertIn("&lt;Acme &amp; Sons&gt;", html)
            self.assertNotIn("<Acme & Sons>", html)
            self.assertIn("Source searches and gaps", html)
            self.assertIn("free-text term=5224393", html)
            self.assertIn("HTTPError: maintenance", html)
            self.assertIn("2024-01-01", html)
            self.assertNotIn("KIMDIS contracts; registration dates=", html)
            self.assertIn("EC State Aid export; country=EL", html)
            manifest = json.loads(
                (result.analysis_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["source_snapshot_id"], "snapshot-1")
            self.assertEqual(manifest["source_gaps"], 1)
            self.assertEqual(result.source_gaps, incomplete_coverage_count)
            self.assertIn("analysis.sqlite3", manifest["file_sha256"])
            self.assertIn(kimdis_page[2], manifest["file_sha256"])
            self.assertTrue(
                any(
                    path.startswith("raw/") and path.endswith(".csv")
                    for path in manifest["file_sha256"]
                )
            )

    def test_csv_neutralizes_formula_leading_public_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            imported = make_state_aid_import(root)
            award = replace(imported.awards[0], beneficiary_name=" \t=2+2")
            request = replace(
                self._request(root, snapshot),
                state_aid_imports=(replace(imported, awards=(award,)),),
            )

            result = build_analysis_artifact(request)

            with (result.analysis_dir / "company_profiles.csv").open(
                encoding="utf-8", newline=""
            ) as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["names"], "' \t=2+2")

    def test_tampered_source_pdf_is_not_interpreted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            pdf = snapshot / "raw" / "decisions" / "decision.pdf"
            pdf.write_bytes(pdf.read_bytes() + b"tampered")

            result = build_analysis_artifact(self._request(root, snapshot))

            connection = sqlite3.connect(result.analysis_dir / "analysis.sqlite3")
            try:
                role_count = connection.execute(
                    "SELECT COUNT(*) FROM greece20_decision_interpretations"
                ).fetchone()[0]
                errors = [
                    row[0]
                    for row in connection.execute(
                        "SELECT error FROM source_coverage WHERE complete=0"
                    )
                ]
            finally:
                connection.close()
            self.assertEqual(role_count, 0)
            self.assertTrue(any("SHA-256" in (error or "") for error in errors))
            connection = sqlite3.connect(result.analysis_dir / "analysis.sqlite3")
            try:
                link_bases = {
                    row[0]
                    for row in connection.execute(
                        "SELECT link_basis FROM project_decision_links"
                    )
                }
            finally:
                connection.close()
            self.assertEqual(link_bases, {"catalogue_row"})

    def test_source_pdf_path_cannot_escape_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            connection = sqlite3.connect(snapshot / "snapshot.sqlite3")
            connection.execute(
                "UPDATE greece20_decisions SET raw_path=? WHERE decision_id=1",
                (str(FIXTURE.resolve()),),
            )
            connection.commit()
            connection.close()

            result = build_analysis_artifact(self._request(root, snapshot))

            connection = sqlite3.connect(result.analysis_dir / "analysis.sqlite3")
            try:
                role_count = connection.execute(
                    "SELECT COUNT(*) FROM greece20_decision_interpretations"
                ).fetchone()[0]
                error = connection.execute(
                    "SELECT error FROM source_coverage WHERE complete=0"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(role_count, 0)
            self.assertIn("relative", error)

    def test_tampered_imported_raw_input_aborts_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            request = self._request(root, snapshot)
            imported = request.state_aid_imports[0]
            imported.raw_path.write_bytes(imported.raw_path.read_bytes() + b"tampered")

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                build_analysis_artifact(request)

            self.assertFalse(
                any(
                    not path.name.startswith(".")
                    for path in (root / "analyses").iterdir()
                )
            )

    def test_empty_kimdis_page_zero_is_complete_with_raw_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            request = self._request(root, snapshot)
            raw = root / "empty-kimdis.json"
            raw.write_bytes(b"{}")
            collection = KimdisContractCollectionResult(
                pde_code="2024ΤΑ09100006",
                date_from=date(2024, 1, 1),
                date_to=date(2024, 6, 28),
                page_results=(
                    KimdisContractPageResult(
                        search=KimdisContractSearch(
                            pde_code="2024ΤΑ09100006",
                            date_from=date(2024, 1, 1),
                            date_to=date(2024, 6, 28),
                        ),
                        source_url="https://example.test/kimdis?page=0",
                        retrieved_at=datetime(2026, 11, 3, tzinfo=UTC),
                        contracts=(),
                        total_pages=0,
                        is_last_page=True,
                        http_status=200,
                        byte_count=2,
                        content_sha256=sha256(b"{}").hexdigest(),
                        raw_path=raw,
                        error=None,
                    ),
                ),
                contracts=(),
                coverage_complete=True,
            )
            request = replace(
                request,
                kimdis_collections=(collection,),
                anomaly_config=replace(
                    request.anomaly_config,
                    observation_end="2024-06-28",
                    rrf_aid_observation_start="2024-01-01",
                    rrf_aid_observation_end="2026-11-02",
                ),
            )

            result = build_analysis_artifact(request)

            connection = sqlite3.connect(result.analysis_dir / "analysis.sqlite3")
            try:
                complete = connection.execute(
                    "SELECT complete FROM source_coverage "
                    "WHERE source_name='KIMDIS contracts'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(complete, 1)
            self.assertEqual(result.status, "complete")

    def test_successful_diavgeia_without_raw_provenance_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            request = replace(
                self._request(root, snapshot),
                diavgeia_searches=(
                    DiavgeiaSearchResult(
                        query_term="5224393",
                        query_url="https://example.test/diavgeia",
                        retrieved_at=datetime(2026, 11, 3, tzinfo=UTC),
                        acts=(),
                        total_reported=0,
                        results_complete=True,
                        http_status=200,
                        byte_count=None,
                        content_sha256=None,
                        raw_path=None,
                        error=None,
                    ),
                ),
            )

            result = build_analysis_artifact(request)

            connection = sqlite3.connect(result.analysis_dir / "analysis.sqlite3")
            try:
                complete, error = connection.execute(
                    "SELECT complete, error FROM source_coverage "
                    "WHERE source_name='Diavgeia'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(complete, 0)
            self.assertIn("raw path/hash/size", error)

    def test_kimdis_missing_raw_is_incomplete_and_raw_mismatch_is_rejected(self) -> None:
        for case, raw_path, content_hash, byte_count, contracts in (
            ("missing_raw", None, None, None, ()),
            ("zero_pages_with_content", "raw", sha256(b"{}").hexdigest(), 2, ("contract",)),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                snapshot = make_source_snapshot(root)
                request = self._request(root, snapshot)
                raw = root / "kimdis.json"
                raw.write_bytes(b"{}")
                parsed_contracts = (
                    parse_kimdis_contracts(
                        [
                            {
                                "referenceNumber": "24SYMV000001",
                                "cancelled": False,
                            }
                        ]
                    )
                    if contracts
                    else ()
                )
                page = KimdisContractPageResult(
                    search=KimdisContractSearch(
                        pde_code="2024ΤΑ09100006",
                        date_from=date(2024, 1, 1),
                        date_to=date(2024, 6, 28),
                    ),
                    source_url="https://example.test/kimdis?page=0",
                    retrieved_at=datetime(2026, 11, 3, tzinfo=UTC),
                    contracts=parsed_contracts,
                    total_pages=0,
                    is_last_page=True,
                    http_status=200,
                    byte_count=byte_count,
                    content_sha256=content_hash,
                    raw_path=raw if raw_path else None,
                    error=None,
                )
                collection = KimdisContractCollectionResult(
                    pde_code="2024ΤΑ09100006",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 6, 28),
                    page_results=(page,),
                    contracts=parsed_contracts,
                    coverage_complete=True,
                )
                request = replace(
                    request,
                    kimdis_collections=(collection,),
                    anomaly_config=replace(
                        request.anomaly_config,
                        observation_end="2024-06-28",
                        rrf_aid_observation_start="2024-01-01",
                        rrf_aid_observation_end="2026-11-02",
                    ),
                )

                if case == "zero_pages_with_content":
                    with self.assertRaisesRegex(ValueError, "raw KIMDIS page"):
                        build_analysis_artifact(request)
                    self.assertFalse(
                        any(
                            not path.name.startswith(".")
                            for path in (root / "analyses").iterdir()
                        )
                    )
                    continue

                result = build_analysis_artifact(request)

                connection = sqlite3.connect(result.analysis_dir / "analysis.sqlite3")
                try:
                    complete = connection.execute(
                        "SELECT complete FROM source_coverage "
                        "WHERE source_name='KIMDIS contracts'"
                    ).fetchone()[0]
                finally:
                    connection.close()
                self.assertEqual(complete, 0)

    def test_spreadsheet_safety_handles_unicode_space_and_control_prefixes(self) -> None:
        for prefix in ("\x00", "\x1f", "\u00a0", "\u2003"):
            value = prefix + "=1+1"
            self.assertEqual(_spreadsheet_safe(value), "'" + value)

    def test_partial_diavgeia_and_kimdis_raw_provenance_is_rejected(self) -> None:
        for source_name in ("diavgeia", "kimdis"):
            with self.subTest(source_name=source_name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                snapshot = make_source_snapshot(root)
                request = self._request(root, snapshot)
                raw = root / f"{source_name}.json"
                raw.write_bytes(b"{}")
                if source_name == "diavgeia":
                    request = replace(
                        request,
                        diavgeia_searches=(
                            DiavgeiaSearchResult(
                                query_term="5224393",
                                query_url="https://example.test/diavgeia",
                                retrieved_at=datetime(2026, 11, 3, tzinfo=UTC),
                                acts=(),
                                total_reported=0,
                                results_complete=True,
                                http_status=200,
                                byte_count=None,
                                content_sha256=None,
                                raw_path=raw,
                                error=None,
                            ),
                        ),
                    )
                    expected_error = "lacks declared"
                else:
                    page = KimdisContractPageResult(
                        search=KimdisContractSearch(
                            pde_code="2024ΤΑ09100006",
                            date_from=date(2024, 1, 1),
                            date_to=date(2024, 6, 28),
                        ),
                        source_url="https://example.test/kimdis?page=0",
                        retrieved_at=datetime(2026, 11, 3, tzinfo=UTC),
                        contracts=(),
                        total_pages=0,
                        is_last_page=True,
                        http_status=200,
                        byte_count=2,
                        content_sha256=sha256(b"{}").hexdigest(),
                        raw_path=None,
                        error=None,
                    )
                    request = replace(
                        request,
                        kimdis_collections=(
                            KimdisContractCollectionResult(
                                pde_code="2024ΤΑ09100006",
                                date_from=date(2024, 1, 1),
                                date_to=date(2024, 6, 28),
                                page_results=(page,),
                                contracts=(),
                                coverage_complete=True,
                            ),
                        ),
                        anomaly_config=replace(
                            request.anomaly_config,
                            observation_end="2024-06-28",
                            rrf_aid_observation_start="2024-01-01",
                            rrf_aid_observation_end="2026-11-02",
                        ),
                    )
                    expected_error = "no raw path"

                with self.assertRaisesRegex(ValueError, expected_error):
                    build_analysis_artifact(request)


if __name__ == "__main__":
    unittest.main()
