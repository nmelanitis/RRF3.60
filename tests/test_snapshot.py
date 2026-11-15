from datetime import datetime
from contextlib import closing, redirect_stdout, redirect_stderr
from io import BytesIO, StringIO
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from crawld.snapshot import SnapshotRequest, collect_snapshot
from crawld.cli import main
from pypdf import PdfWriter


FIXTURE = Path(__file__).parent / "fixtures" / "greece20_catalogue.html"
ATHENS = ZoneInfo("Europe/Athens")
PDF_FIXTURE = FIXTURE.with_name("greece20_5224393_inclusion.pdf")


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source_root = Path(temporary.name)
        source = self.source_root / "catalogue.html"
        html = FIXTURE.read_text(encoding="utf-8")
        for name in ("decision-a.pdf", "decision-a2.pdf"):
            pdf = self.source_root / name
            pdf.write_bytes(PDF_FIXTURE.read_bytes())
            html = html.replace(f"https://example.test/{name}", pdf.as_uri())
        source.write_text(html, encoding="utf-8")
        self.source_url = source.as_uri()

    def test_cli_reports_pdf_gaps_and_success_exit_status(self):
        (self.source_root / "decision-a.pdf").unlink()
        payload = (self.source_root / "catalogue.html").read_bytes()
        stdout, stderr = StringIO(), StringIO()
        with patch("crawld.snapshot.urlopen", return_value=BytesIO(payload)), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["collect", "--through", "2024-12-31", "--output-root",
                         str(self.source_root / "snapshots")])
        self.assertEqual(code, 0)
        self.assertIn("status: complete", stdout.getvalue())
        self.assertIn("decision URLs found: 2", stdout.getvalue())
        self.assertIn("decisions collected: 1", stdout.getvalue())
        self.assertIn("decisions failed: 1", stdout.getvalue())
        self.assertIn("funding references extracted: 1", stdout.getvalue())
        self.assertIn("data gaps", stderr.getvalue())

    def test_source_gaps_are_persisted_and_run_completes(self):
        (self.source_root / "decision-a.pdf").unlink()
        corrupt = self.source_root / "corrupt.pdf"
        corrupt.write_bytes(b"")
        source = self.source_root / "catalogue.html"
        html = source.read_text(encoding="utf-8")
        row = re.search(r"<tbody>\s*(<tr>.*?</tr>)", html, re.S).group(1)
        html = html.replace("</tbody>", row.replace("decision-a.pdf", "corrupt.pdf")
                            + "</tbody>")
        source.write_text(html, encoding="utf-8")
        result = collect_snapshot(SnapshotRequest(
            cutoff=datetime(2024, 12, 31, tzinfo=ATHENS),
            output_root=self.source_root / "snapshots", source_url=self.source_url,
        ))
        self.assertEqual(result.status, "complete")
        self.assertEqual((result.decision_urls_found, result.decisions_collected,
                          result.decisions_failed, result.funding_references_extracted),
                         (3, 1, 2, 1))
        with closing(sqlite3.connect(result.snapshot_dir / "snapshot.sqlite3")) as db:
            db.row_factory = sqlite3.Row
            rows = {r["source_url"]: r for r in db.execute(
                "SELECT * FROM greece20_decisions"
            )}
            metrics = dict(db.execute(
                "SELECT metric_name, metric_value FROM collection_metrics"
            ).fetchall())
        missing = rows[(self.source_root / "decision-a.pdf").as_uri()]
        self.assertEqual(missing["status"], "failed")
        self.assertTrue(missing["error"])
        self.assertIsNone(missing["raw_path"])
        invalid = rows[corrupt.as_uri()]
        self.assertEqual(invalid["status"], "failed")
        self.assertIn("EmptyFileError", invalid["error"])
        self.assertEqual(invalid["byte_count"], 0)
        self.assertEqual(invalid["content_sha256"], sha256(b"").hexdigest())
        self.assertEqual((result.snapshot_dir / invalid["raw_path"]).read_bytes(), b"")
        manifest = json.loads((result.snapshot_dir / "manifest.json").read_text())
        self.assertEqual(manifest["metrics"], metrics)
        with self.assertRaisesRegex(ValueError, "already complete"):
            collect_snapshot(SnapshotRequest(
                cutoff=None, output_root=self.source_root,
                resume_snapshot=result.snapshot_dir, source_url=self.source_url,
            ))

    def test_pdf_without_identifiers_is_collected_with_no_funding_reference(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        for name in ("decision-a.pdf", "decision-a2.pdf"):
            writer.write(self.source_root / name)
        result = collect_snapshot(SnapshotRequest(
            cutoff=datetime(2024, 12, 31, tzinfo=ATHENS),
            output_root=self.source_root / "snapshots", source_url=self.source_url,
        ))
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.decisions_collected, 2)
        self.assertEqual(result.funding_references_extracted, 0)

    def test_storage_failure_retains_progress_and_resume_rebuilds_without_duplicates(self):
        (self.source_root / "decision-a2.pdf").write_bytes(b"")
        original_write = Path.write_bytes

        def write_bytes(path, payload):
            if path.parent.name == "greece20-decisions" and payload == b"":
                raise PermissionError("simulated PDF storage failure")
            return original_write(path, payload)

        with patch.object(Path, "write_bytes", write_bytes):
            result = collect_snapshot(SnapshotRequest(
                cutoff=datetime(2024, 12, 31, tzinfo=ATHENS),
                output_root=self.source_root / "snapshots",
                source_url=self.source_url,
            ))
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.entries_included, 2)
        self.assertEqual(result.decision_urls_found, 2)
        self.assertEqual(result.decisions_collected, 1)
        self.assertEqual(result.decisions_failed, 0)
        with closing(sqlite3.connect(result.snapshot_dir / "snapshot.sqlite3")) as db:
            self.assertEqual(db.execute(
                "SELECT status FROM source_retrievals"
            ).fetchall(), [("complete",)])
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM greece20_decisions"
            ).fetchone()[0], 1)
        manifest = json.loads((result.snapshot_dir / "manifest.json").read_text())
        self.assertEqual(manifest["metrics"]["decisions_collected"], 1)

        (self.source_root / "decision-a2.pdf").write_bytes(PDF_FIXTURE.read_bytes())
        resumed = collect_snapshot(SnapshotRequest(
            cutoff=None, output_root=self.source_root / "snapshots",
            resume_snapshot=result.snapshot_dir, source_url=self.source_url,
        ))
        self.assertEqual(resumed.snapshot_id, result.snapshot_id)
        self.assertEqual(resumed.status, "complete")
        self.assertEqual(resumed.decisions_collected, 2)
        self.assertEqual(resumed.funding_references_extracted, 2)
        with closing(sqlite3.connect(resumed.snapshot_dir / "snapshot.sqlite3")) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM rrf_project_funding_references"
            ).fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_resume_reset_failure_reports_the_still_committed_counts(self):
        (self.source_root / "decision-a2.pdf").write_bytes(b"")
        original_write = Path.write_bytes

        def write_bytes(path, payload):
            if path.parent.name == "greece20-decisions" and payload == b"":
                raise PermissionError("simulated PDF storage failure")
            return original_write(path, payload)

        with patch.object(Path, "write_bytes", write_bytes):
            incomplete = collect_snapshot(SnapshotRequest(
                cutoff=datetime(2024, 12, 31, tzinfo=ATHENS),
                output_root=self.source_root / "snapshots",
                source_url=self.source_url,
            ))
        self.assertEqual(incomplete.decisions_collected, 1)

        class FailingResetConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql.startswith("DELETE FROM greece20_decisions"):
                    raise sqlite3.OperationalError("simulated reset failure")
                return super().execute(sql, parameters)

        connect = sqlite3.connect
        with patch("crawld.snapshot.sqlite3.connect", side_effect=lambda path:
                   connect(path, factory=FailingResetConnection)):
            result = collect_snapshot(SnapshotRequest(
                cutoff=None,
                output_root=self.source_root / "snapshots",
                resume_snapshot=incomplete.snapshot_dir,
                source_url=self.source_url,
            ))

        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.entries_included, 2)
        self.assertEqual(result.decision_urls_found, 2)
        self.assertEqual(result.decisions_collected, 1)
        self.assertEqual(result.funding_references_extracted, 1)
        with closing(connect(result.snapshot_dir / "snapshot.sqlite3")) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM greece20_decisions"
            ).fetchone()[0], 1)
            stored = dict(db.execute(
                "SELECT metric_name, metric_value FROM collection_metrics"
            ).fetchall())
        manifest = json.loads((result.snapshot_dir / "manifest.json").read_text())
        self.assertEqual(manifest["metrics"], stored)

    def test_database_open_failure_marks_manifest_incomplete(self):
        with patch("crawld.snapshot.sqlite3.connect",
                   side_effect=sqlite3.OperationalError("database unavailable")):
            result = collect_snapshot(SnapshotRequest(
                cutoff=datetime(2024, 12, 31, tzinfo=ATHENS),
                output_root=self.source_root / "snapshots", source_url=self.source_url,
            ))
        self.assertEqual(result.status, "incomplete")
        self.assertIn("database unavailable", result.error)
        manifest = json.loads((result.snapshot_dir / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "incomplete")

    def test_catalogue_database_failure_reports_only_committed_counts(self):
        class FailingMetricsConnection(sqlite3.Connection):
            def executemany(self, sql, parameters):
                if "INSERT INTO collection_metrics" in sql:
                    raise sqlite3.OperationalError("simulated metrics write failure")
                return super().executemany(sql, parameters)

        connect = sqlite3.connect
        with patch("crawld.snapshot.sqlite3.connect", side_effect=lambda path:
                   connect(path, factory=FailingMetricsConnection)):
            result = collect_snapshot(SnapshotRequest(
                cutoff=datetime(2024, 12, 31, tzinfo=ATHENS),
                output_root=self.source_root / "snapshots", source_url=self.source_url,
            ))
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.entries_included, 0)
        self.assertEqual(result.decision_urls_found, 0)
        with closing(connect(result.snapshot_dir / "snapshot.sqlite3")) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM rrf_catalogue_entries"
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT status, error FROM source_retrievals"
            ).fetchall(), [("complete", None)])
        manifest = json.loads((result.snapshot_dir / "manifest.json").read_text())
        self.assertEqual(manifest["metrics"]["entries_included"], 0)

    def test_reference_write_failure_rolls_back_only_current_decision(self):
        class FailingReferenceConnection(sqlite3.Connection):
            reference_writes = 0

            def executemany(self, sql, parameters):
                if "INSERT OR IGNORE INTO rrf_project_funding_references" in sql:
                    self.reference_writes += 1
                    if self.reference_writes == 2:
                        raise sqlite3.OperationalError("simulated reference failure")
                return super().executemany(sql, parameters)

        connect = sqlite3.connect
        with patch("crawld.snapshot.sqlite3.connect", side_effect=lambda path:
                   connect(path, factory=FailingReferenceConnection)):
            result = collect_snapshot(SnapshotRequest(
                cutoff=datetime(2024, 12, 31, tzinfo=ATHENS),
                output_root=self.source_root / "snapshots", source_url=self.source_url,
            ))
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.decisions_collected, 1)
        self.assertEqual(result.funding_references_extracted, 1)
        with closing(connect(result.snapshot_dir / "snapshot.sqlite3")) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM greece20_decisions"
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM rrf_project_funding_references"
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT metric_value FROM collection_metrics "
                "WHERE metric_name='decisions_collected'"
            ).fetchone()[0], 1)

    def test_snapshot_persists_unique_decisions_and_pdf_funding_evidence(self) -> None:
        source = self.source_root / "catalogue.html"
        html = source.read_text(encoding="utf-8")
        html = html.replace("decision-a2.pdf", "decision-a.pdf")
        html = html.replace(
            '<td>5000002</td>',
            '<td><a href="https://example.test/after-cutoff.pdf">5000002</a></td>',
        )
        source.write_text(html, encoding="utf-8")
        result = collect_snapshot(SnapshotRequest(
            cutoff=datetime(2024, 12, 31, tzinfo=ATHENS),
            output_root=self.source_root / "snapshots",
            source_url=self.source_url,
        ))
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.decision_urls_found, 1)
        self.assertEqual(result.decisions_collected, 1)
        self.assertEqual(result.decisions_failed, 0)
        self.assertEqual(result.funding_references_extracted, 1)
        with closing(sqlite3.connect(result.snapshot_dir / "snapshot.sqlite3")) as db:
            decisions = db.execute(
                "SELECT source_url, status, retrieved_at, content_sha256, "
                "byte_count, raw_path, error FROM greece20_decisions"
            ).fetchall()
            references = db.execute(
                "SELECT d.source_url, f.ops_code, f.pde_code "
                "FROM rrf_project_funding_references f "
                "JOIN greece20_decisions d USING (decision_id)"
            ).fetchall()
        url = (self.source_root / "decision-a.pdf").as_uri()
        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertEqual(decision[:2], (url, "collected"))
        self.assertIsNotNone(datetime.fromisoformat(decision[2]).tzinfo)
        payload = PDF_FIXTURE.read_bytes()
        digest = sha256(payload).hexdigest()
        self.assertEqual(decision[3:5], (digest, len(payload)))
        self.assertEqual(decision[5], f"raw/greece20-decisions/{digest}.pdf")
        self.assertIsNone(decision[6])
        self.assertEqual((result.snapshot_dir / decision[5]).read_bytes(), payload)
        # Keep PDF evidence even when the synthetic catalogue OPS differs.
        self.assertEqual(references, [(url, "5224393", "2024ΤΑ09100006")])
        manifest = json.loads((result.snapshot_dir / "manifest.json").read_text())
        for name, expected in {
            "decision_urls_found": 1, "decisions_collected": 1,
            "decisions_failed": 0, "funding_references_extracted": 1,
        }.items():
            self.assertEqual(manifest["metrics"][name], expected)

    def test_collects_rows_through_cutoff_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = collect_snapshot(
                SnapshotRequest(
                    cutoff=datetime(2024, 12, 31, 23, 59, tzinfo=ATHENS),
                    output_root=Path(directory),
                    source_url=self.source_url,
                )
            )

            self.assertEqual(result.status, "complete")
            self.assertEqual(result.entries_seen, 3)
            self.assertEqual(result.entries_included, 2)
            self.assertEqual(result.entries_after_cutoff, 1)
            self.assertTrue(
                (result.snapshot_dir / "raw" / "greece20-included-projects.html").is_file()
            )
            manifest = json.loads(
                (result.snapshot_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["metrics"]["entries_included"], 2)
            self.assertEqual(manifest["metrics"]["entries_without_source_budget"], 0)
            self.assertEqual(manifest["metrics"]["duplicate_source_record_keys"], 0)

            with closing(sqlite3.connect(result.snapshot_dir / "snapshot.sqlite3")) as database:
                rows = database.execute(
                    "SELECT ops_code, source_budget_cents "
                    "FROM rrf_catalogue_entries ORDER BY entry_id"
                ).fetchall()
                source = database.execute(
                    "SELECT status, content_sha256, byte_count FROM source_retrievals"
                ).fetchone()
            self.assertEqual(rows, [("5000001", 123456), ("5000001", 200000)])
            self.assertEqual(source[0], "complete")
            self.assertEqual(len(source[1]), 64)
            self.assertGreater(source[2], 0)

    def test_incomplete_snapshot_can_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            failed = collect_snapshot(
                SnapshotRequest(
                    cutoff=datetime(2024, 12, 31, tzinfo=ATHENS),
                    output_root=output_root,
                    source_url=(output_root / "missing.html").as_uri(),
                )
            )
            self.assertEqual(failed.status, "incomplete")
            self.assertTrue((failed.snapshot_dir / "snapshot.sqlite3").is_file())

            resumed = collect_snapshot(
                SnapshotRequest(
                    cutoff=None,
                    output_root=output_root,
                    resume_snapshot=failed.snapshot_dir,
                    source_url=self.source_url,
                )
            )
            self.assertEqual(resumed.snapshot_id, failed.snapshot_id)
            self.assertEqual(resumed.status, "complete")
            self.assertEqual(resumed.entries_included, 2)

    def test_parse_failure_preserves_download_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            invalid_source = output_root / "invalid.html"
            invalid_source.write_text("<html>not the catalogue</html>", encoding="utf-8")

            result = collect_snapshot(
                SnapshotRequest(
                    cutoff=datetime(2024, 12, 31, tzinfo=ATHENS),
                    output_root=output_root,
                    source_url=invalid_source.as_uri(),
                )
            )

            self.assertEqual(result.status, "incomplete")
            with closing(sqlite3.connect(result.snapshot_dir / "snapshot.sqlite3")) as database:
                source = database.execute(
                    "SELECT status, content_sha256, byte_count, raw_path FROM source_retrievals"
                ).fetchone()
            self.assertEqual(source[0], "failed")
            self.assertEqual(len(source[1]), 64)
            self.assertGreater(source[2], 0)
            self.assertEqual(source[3], "raw/greece20-included-projects.html")


if __name__ == "__main__":
    unittest.main()
