from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from crawld.cli import _analysis_terms, main
from tests.test_analysis import make_source_snapshot


class AnalyseCliTests(unittest.TestCase):
    def _inputs(self, root: Path) -> tuple[Path, Path]:
        csv_path = root / "aid.csv"
        csv_path.write_text(
            "Award ID,Beneficiary Name,Beneficiary Identifier,Beneficiary Identifier Scheme,"
            "Member State,Aid Element,Currency,Aid Instrument,Aid Measure Reference\n"
            "A-1,Acme,094259216,VAT,EL,500.00,EUR,Grant,SA.12345\n",
            encoding="utf-8",
        )
        config_path = root / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "source_url": "https://example.test/export",
                    "export_scope": {
                        "country": "EL",
                        "date_from": "2024-01-01",
                        "date_to": "2025-12-31",
                        "measure_filters": [],
                        "full_result_export": True,
                    },
                    "coverage": {
                        "description": "Test full export; not exhaustive.",
                        "publication_thresholds": ["varies"],
                        "publication_deadlines": ["varies"],
                    },
                    "measure_evidence": [
                        {
                            "aid_measure_reference": "SA.12345",
                            "rrf_linkage": "linked",
                            "source_name": "Official decision",
                            "source_url": "https://example.test/decision",
                            "source_sha256": "a" * 64,
                            "source_act_id": "SA.12345",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return csv_path, config_path

    def test_offline_skipped_sources_build_and_use_route_specific_periods(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            csv_path, config_path = self._inputs(root)
            output = root / "analyses"
            stdout = StringIO()

            with redirect_stdout(stdout):
                result = main(
                    [
                        "analyse",
                        "--snapshot",
                        str(snapshot),
                        "--state-aid-csv",
                        str(csv_path),
                        "--state-aid-config",
                        str(config_path),
                        "--kimdis-from",
                        "2026-01-01",
                        "--kimdis-to",
                        "2026-11-02",
                        "--skip-kimdis",
                        "--skip-diavgeia",
                        "--rrf-aid-share-threshold",
                        "0.5",
                        "--output-root",
                        str(output),
                    ]
                )

            self.assertEqual(result, 0)
            analysis = next(path for path in output.iterdir() if not path.name.startswith("."))
            connection = sqlite3.connect(analysis / "analysis.sqlite3")
            try:
                period = connection.execute(
                    "SELECT observation_start, observation_end, observation_scope "
                    "FROM anomaly_flags WHERE route='rrf_linked_aid'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(period[:2], ("2024-01-01", "2025-12-31"))
            self.assertIn("country=EL", period[2])

    def test_config_requires_real_boolean_and_valid_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = make_source_snapshot(root)
            csv_path, config_path = self._inputs(root)
            value = json.loads(config_path.read_text(encoding="utf-8"))
            value["export_scope"]["full_result_export"] = "false"
            value["measure_evidence"][0]["source_sha256"] = "not-a-hash"
            config_path.write_text(json.dumps(value), encoding="utf-8")

            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(
                    [
                        "analyse",
                        "--snapshot",
                        str(snapshot),
                        "--state-aid-csv",
                        str(csv_path),
                        "--state-aid-config",
                        str(config_path),
                        "--kimdis-from",
                        "2026-01-01",
                        "--kimdis-to",
                        "2026-11-02",
                        "--skip-kimdis",
                        "--skip-diavgeia",
                        "--rrf-aid-share-threshold",
                        "0.5",
                        "--output-root",
                        str(root / "analyses"),
                    ]
                )

    def test_analysis_terms_do_not_create_missing_snapshot_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory)

            with self.assertRaisesRegex(ValueError, "snapshot database"):
                _analysis_terms(snapshot)

            self.assertFalse((snapshot / "snapshot.sqlite3").exists())


class CollectStateAidEvidenceCliTests(unittest.TestCase):
    def test_local_dataset_lookup_writes_resolved_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "aid.csv"
            csv_path.write_text(
                "Award ID,Beneficiary Name,Aid Element,Aid Measure Reference\n"
                "A-1,Acme,500.00,SA.12345\n",
                encoding="utf-8",
            )
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "source_url": "https://example.test/export",
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
                        "measure_evidence": [],
                    }
                ),
                encoding="utf-8",
            )
            dataset_path = root / "case-data-SA.json"
            dataset_path.write_text(
                json.dumps(
                    {
                        "SA.12345": {
                            "metadata": {
                                "caseTitle": [
                                    "Support under the Recovery and Resilience Facility"
                                ],
                                "caseOriginalTitle": [],
                            },
                            "caseAttachments": [],
                            "decisions": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = root / "evidence"
            stdout = StringIO()

            with redirect_stdout(stdout):
                result = main(
                    [
                        "collect-state-aid-evidence",
                        "--state-aid-csv",
                        str(csv_path),
                        "--state-aid-config",
                        str(config_path),
                        "--commission-dataset",
                        str(dataset_path),
                        "--skip-diavgeia",
                        "--output-root",
                        str(output),
                    ]
                )

            self.assertEqual(result, 0)
            run = next(output.iterdir())
            resolved = json.loads(
                (run / "state-aid-config.resolved.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                resolved["measure_evidence"][0]["aid_measure_reference"],
                "SA.12345",
            )
            self.assertIn("linked: 1", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
