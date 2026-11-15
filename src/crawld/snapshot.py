"""Build a durable snapshot from the official Greece 2.0 catalogue."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Final
from urllib.request import Request, urlopen
from uuid import uuid4

from crawld.decisions import (
    FailedGreece20Decision,
    Greece20DecisionBatchResult,
    collect_greece20_decision_pdfs,
)
from crawld.greece20 import CatalogueEntry, entry_as_storage_dict, parse_catalogue


GREECE20_CATALOGUE_URL: Final = (
    "https://greece20.gov.gr/lista-entagmenwn-ergwn/"
)


@dataclass(frozen=True)
class SnapshotRequest:
    cutoff: datetime | None
    output_root: Path
    resume_snapshot: Path | None = None
    source_url: str = GREECE20_CATALOGUE_URL


@dataclass(frozen=True)
class SnapshotResult:
    snapshot_id: str
    snapshot_dir: Path
    status: str
    entries_seen: int
    entries_included: int
    entries_after_cutoff: int
    error: str | None = None
    decision_urls_found: int = 0
    decisions_collected: int = 0
    decisions_failed: int = 0
    funding_references_extracted: int = 0


def _write_manifest(snapshot_dir: Path, values: dict[str, object]) -> None:
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_manifest(snapshot_dir: Path) -> dict[str, object]:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Snapshot has no manifest: {snapshot_dir}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _connect_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    schema = """
        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id TEXT PRIMARY KEY,
            cutoff TEXT NOT NULL,
            retrieved_at TEXT,
            status TEXT NOT NULL,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS source_retrievals (
            retrieval_id INTEGER PRIMARY KEY,
            snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            http_status INTEGER,
            content_sha256 TEXT,
            byte_count INTEGER,
            raw_path TEXT,
            status TEXT NOT NULL,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS rrf_catalogue_entries (
            entry_id INTEGER PRIMARY KEY,
            snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
            source_row_index INTEGER NOT NULL,
            source_record_key TEXT NOT NULL,
            row_hash TEXT NOT NULL,
            catalogue_sequence TEXT,
            pillar TEXT,
            axis TEXT,
            responsible_ministry TEXT,
            implementing_body TEXT,
            action_code TEXT,
            action_title TEXT,
            inclusion_date TEXT,
            project_title TEXT,
            is_modification INTEGER,
            ops_code TEXT,
            decision_url TEXT,
            action_type TEXT,
            source_budget_cents INTEGER,
            UNIQUE(snapshot_id, source_row_index)
        );

        CREATE INDEX IF NOT EXISTS idx_catalogue_ops_code
            ON rrf_catalogue_entries(snapshot_id, ops_code);
        CREATE INDEX IF NOT EXISTS idx_catalogue_action_code
            ON rrf_catalogue_entries(snapshot_id, action_code);

        CREATE TABLE IF NOT EXISTS collection_metrics (
            snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
            metric_name TEXT NOT NULL,
            metric_value INTEGER NOT NULL,
            PRIMARY KEY(snapshot_id, metric_name)
        );

        CREATE TABLE IF NOT EXISTS greece20_decisions (
            decision_id INTEGER PRIMARY KEY,
            snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
            source_url TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('collected', 'failed')),
            retrieved_at TEXT,
            content_sha256 TEXT,
            byte_count INTEGER,
            raw_path TEXT,
            error TEXT,
            UNIQUE(snapshot_id, source_url)
        );

        CREATE TABLE IF NOT EXISTS rrf_project_funding_references (
            decision_id INTEGER NOT NULL REFERENCES greece20_decisions(decision_id),
            ops_code TEXT NOT NULL,
            pde_code TEXT NOT NULL,
            PRIMARY KEY(decision_id, ops_code, pde_code)
        );
        """
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema)
    except Exception:
        connection.close()
        raise
    return connection


def _store_decisions(
    connection: sqlite3.Connection,
    snapshot_id: str,
    snapshot_dir: Path,
    batch: Greece20DecisionBatchResult,
) -> None:
    for decision in (*batch.collected_decisions, *batch.failed_decisions):
        failed = isinstance(decision, FailedGreece20Decision)
        preserved = decision.preserved_decision if failed else decision
        cursor = connection.execute(
            """
            INSERT INTO greece20_decisions(
                snapshot_id, source_url, status, retrieved_at, content_sha256,
                byte_count, raw_path, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id, decision.source_url,
                "failed" if failed else "collected",
                preserved.retrieved_at.isoformat() if preserved else None,
                preserved.content_sha256 if preserved else None,
                preserved.byte_count if preserved else None,
                str(preserved.raw_path.relative_to(snapshot_dir)) if preserved else None,
                decision.error if failed else None,
            ),
        )
        if not failed:
            connection.executemany(
                """INSERT OR IGNORE INTO rrf_project_funding_references
                   (decision_id, ops_code, pde_code) VALUES (?, ?, ?)""",
                [(cursor.lastrowid, ref.ops_code, ref.pde_code)
                 for ref in decision.funding_references],
            )


def _fetch_source(url: str) -> tuple[bytes, int]:
    request = Request(url, headers={"User-Agent": "crawlD/0.1 (+public-data research)"})
    with urlopen(request, timeout=60) as response:
        return response.read(), int(getattr(response, "status", None) or 200)


def _entries_through_cutoff(
    entries: list[CatalogueEntry], cutoff: datetime
) -> tuple[list[CatalogueEntry], int]:
    included: list[CatalogueEntry] = []
    after_cutoff = 0
    cutoff_date = cutoff.date()
    for entry in entries:
        if entry.inclusion_date is not None and entry.inclusion_date > cutoff_date:
            after_cutoff += 1
        else:
            included.append(entry)
    return included, after_cutoff


def _prepare_snapshot(request: SnapshotRequest, started_at: datetime) -> tuple[Path, str, datetime]:
    if request.resume_snapshot is not None:
        snapshot_dir = request.resume_snapshot.resolve()
        manifest = _read_manifest(snapshot_dir)
        if manifest.get("status") == "complete":
            raise ValueError(f"Snapshot is already complete: {snapshot_dir}")
        snapshot_id = str(manifest["snapshot_id"])
        cutoff = datetime.fromisoformat(str(manifest["cutoff"]))
        return snapshot_dir, snapshot_id, cutoff

    if request.cutoff is None:
        raise ValueError("A cutoff is required for a new snapshot")
    cutoff = request.cutoff
    cutoff_slug = cutoff.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"{cutoff_slug}-{uuid4().hex[:8]}"
    snapshot_dir = (request.output_root / snapshot_id).resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    (snapshot_dir / "raw").mkdir()
    _write_manifest(
        snapshot_dir,
        {
            "snapshot_id": snapshot_id,
            "cutoff": cutoff.isoformat(),
            "started_at": started_at.isoformat(),
            "status": "running",
            "source_url": request.source_url,
        },
    )
    return snapshot_dir, snapshot_id, cutoff


def _reset_snapshot(
    connection: sqlite3.Connection, snapshot_id: str, cutoff: datetime
) -> None:
    connection.execute(
        """
        INSERT INTO snapshots(snapshot_id, cutoff, status)
        VALUES (?, ?, 'running')
        ON CONFLICT(snapshot_id) DO UPDATE SET status='running', error=NULL
        """,
        (snapshot_id, cutoff.isoformat()),
    )
    connection.execute(
        "DELETE FROM rrf_project_funding_references WHERE decision_id IN "
        "(SELECT decision_id FROM greece20_decisions WHERE snapshot_id = ?)",
        (snapshot_id,),
    )
    connection.execute(
        "DELETE FROM greece20_decisions WHERE snapshot_id = ?", (snapshot_id,)
    )
    connection.execute(
        "DELETE FROM source_retrievals WHERE snapshot_id = ?", (snapshot_id,)
    )
    connection.execute(
        "DELETE FROM rrf_catalogue_entries WHERE snapshot_id = ?", (snapshot_id,)
    )
    connection.execute(
        "DELETE FROM collection_metrics WHERE snapshot_id = ?", (snapshot_id,)
    )
    connection.commit()


def _store_metrics(
    connection: sqlite3.Connection, snapshot_id: str, metrics: dict[str, int]
) -> None:
    connection.executemany(
        """INSERT INTO collection_metrics(snapshot_id, metric_name, metric_value)
           VALUES (?, ?, ?) ON CONFLICT(snapshot_id, metric_name)
           DO UPDATE SET metric_value=excluded.metric_value""",
        [(snapshot_id, name, value) for name, value in metrics.items()],
    )


def collect_snapshot(request: SnapshotRequest) -> SnapshotResult:
    """Collect the catalogue and linked decisions; complete may include source gaps.

    Each decision is committed separately. Resume rebuilds normalized records;
    complete snapshots cannot be resumed. If failure status cannot be persisted,
    the storage exception propagates.
    """
    started_at = datetime.now(UTC)
    snapshot_dir, snapshot_id, cutoff = _prepare_snapshot(request, started_at)
    connection = None
    catalogue_stored = False
    catalogue_parsed = False
    catalogue_attempted = False
    metrics = {name: 0 for name in (
        "entries_seen", "entries_included", "entries_after_cutoff",
        "decision_urls_found", "decisions_collected", "decisions_failed",
        "funding_references_extracted",
    )}
    retrieved_at = datetime.now(UTC)
    payload: bytes | None = None
    http_status: int | None = None
    raw_path: Path | None = None
    content_hash: str | None = None
    try:
        connection = _connect_database(snapshot_dir / "snapshot.sqlite3")
        _reset_snapshot(connection, snapshot_id, cutoff)
        catalogue_attempted = True
        payload, http_status = _fetch_source(request.source_url)
        raw_path = snapshot_dir / "raw" / "greece20-included-projects.html"
        raw_path.write_bytes(payload)
        content_hash = sha256(payload).hexdigest()
        html = payload.decode("utf-8")
        entries = parse_catalogue(html)
        included, after_cutoff = _entries_through_cutoff(entries, cutoff)
        catalogue_parsed = True

        connection.execute(
            """
            INSERT INTO source_retrievals(
                snapshot_id, source_name, source_url, retrieved_at, http_status,
                content_sha256, byte_count, raw_path, status
            ) VALUES (?, 'greece20_included_projects', ?, ?, ?, ?, ?, ?, 'complete')
            """,
            (
                snapshot_id,
                request.source_url,
                retrieved_at.isoformat(),
                http_status,
                content_hash,
                len(payload),
                str(raw_path.relative_to(snapshot_dir)),
            ),
        )

        insert_sql = """
            INSERT INTO rrf_catalogue_entries(
                snapshot_id, source_row_index, source_record_key, row_hash,
                catalogue_sequence, pillar, axis, responsible_ministry,
                implementing_body, action_code, action_title, inclusion_date,
                project_title, is_modification, ops_code, decision_url,
                action_type, source_budget_cents
            ) VALUES (
                :snapshot_id, :source_row_index, :source_record_key, :row_hash,
                :catalogue_sequence, :pillar, :axis, :responsible_ministry,
                :implementing_body, :action_code, :action_title, :inclusion_date,
                :project_title, :is_modification, :ops_code, :decision_url,
                :action_type, :source_budget_cents
            )
        """
        for index, entry in enumerate(included, start=1):
            values = entry_as_storage_dict(entry)
            values.update(snapshot_id=snapshot_id, source_row_index=index)
            connection.execute(insert_sql, values)

        sequence_counts = Counter(
            entry.catalogue_sequence
            for entry in included
            if entry.catalogue_sequence is not None
        )
        source_key_counts = Counter(entry.source_record_key for entry in included)
        catalogue_metrics = {
            **metrics,
            "entries_seen": len(entries),
            "entries_included": len(included),
            "entries_after_cutoff": after_cutoff,
            "entries_without_inclusion_date": sum(
                entry.inclusion_date is None for entry in included
            ),
            "entries_without_ops_code": sum(entry.ops_code is None for entry in included),
            "entries_without_decision_url": sum(
                entry.decision_url is None for entry in included
            ),
            "entries_without_catalogue_sequence": sum(
                entry.catalogue_sequence is None for entry in included
            ),
            "entries_without_source_budget": sum(
                entry.source_budget_cents is None for entry in included
            ),
            "entries_with_inclusion_date_before_2021": sum(
                entry.inclusion_date is not None
                and entry.inclusion_date < date(2021, 1, 1)
                for entry in included
            ),
            "duplicate_catalogue_sequence_values": sum(
                count > 1 for count in sequence_counts.values()
            ),
            "duplicate_source_record_keys": sum(
                count > 1 for count in source_key_counts.values()
            ),
        }
        urls = list(dict.fromkeys(
            entry.decision_url for entry in included if entry.decision_url
        ))
        catalogue_metrics["decision_urls_found"] = len(urls)
        _store_metrics(connection, snapshot_id, catalogue_metrics)
        connection.commit()
        metrics = catalogue_metrics
        catalogue_stored = True
        for url in urls:
            batch = collect_greece20_decision_pdfs(
                [url], snapshot_dir / "raw" / "greece20-decisions"
            )
            _store_decisions(connection, snapshot_id, snapshot_dir, batch)
            updated = dict(metrics)
            updated["decisions_collected"] += batch.collected_count
            updated["decisions_failed"] += batch.failed_count
            updated["funding_references_extracted"] = connection.execute(
                "SELECT COUNT(*) FROM rrf_project_funding_references f "
                "JOIN greece20_decisions d USING (decision_id) "
                "WHERE d.snapshot_id = ?", (snapshot_id,),
            ).fetchone()[0]
            _store_metrics(connection, snapshot_id, updated)
            connection.commit()
            metrics = updated
        decision_metrics = {name: metrics[name] for name in (
            "decision_urls_found", "decisions_collected", "decisions_failed",
            "funding_references_extracted",
        )}
        connection.execute(
            "UPDATE snapshots SET retrieved_at = ?, status = 'complete', "
            "error = NULL WHERE snapshot_id = ?",
            (retrieved_at.isoformat(), snapshot_id),
        )
        connection.commit()
        manifest = {
            "snapshot_id": snapshot_id,
            "cutoff": cutoff.isoformat(),
            "started_at": started_at.isoformat(),
            "retrieved_at": retrieved_at.isoformat(),
            "status": "complete",
            "source_url": request.source_url,
            "source_sha256": content_hash,
            "source_bytes": len(payload),
            "metrics": metrics,
        }
        _write_manifest(snapshot_dir, manifest)
        return SnapshotResult(
            snapshot_id=snapshot_id,
            snapshot_dir=snapshot_dir,
            status="complete",
            entries_seen=len(entries),
            entries_included=len(included),
            entries_after_cutoff=after_cutoff,
            **decision_metrics,
        )
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        if connection is not None:
            try:
                connection.rollback()
                durable_metrics = dict(connection.execute(
                    "SELECT metric_name, metric_value FROM collection_metrics "
                    "WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchall())
                if durable_metrics:
                    metrics = durable_metrics
                if catalogue_attempted and not catalogue_stored:
                    connection.execute(
                        """INSERT INTO source_retrievals(
                            snapshot_id, source_name, source_url, retrieved_at,
                            http_status, content_sha256, byte_count, raw_path,
                            status, error
                        ) VALUES (?, 'greece20_included_projects', ?, ?, ?, ?,
                                  ?, ?, ?, ?)""",
                        (snapshot_id, request.source_url, retrieved_at.isoformat(),
                         http_status, content_hash,
                         len(payload) if payload is not None else None,
                         str(raw_path.relative_to(snapshot_dir))
                         if raw_path and raw_path.is_file() else None,
                         "complete" if catalogue_parsed else "failed",
                         None if catalogue_parsed else message),
                    )
                connection.execute(
                    "UPDATE snapshots SET status='incomplete', error=? "
                    "WHERE snapshot_id=?", (message, snapshot_id),
                )
                connection.commit()
            except sqlite3.Error as reporting_error:
                message += f"; database status update failed: {reporting_error}"
        _write_manifest(
            snapshot_dir,
            {
                "snapshot_id": snapshot_id,
                "cutoff": cutoff.isoformat(),
                "started_at": started_at.isoformat(),
                "retrieved_at": retrieved_at.isoformat(),
                "status": "incomplete",
                "source_url": request.source_url,
                "error": message,
                "metrics": metrics,
            },
        )
        return SnapshotResult(
            snapshot_id=snapshot_id,
            snapshot_dir=snapshot_dir,
            status="incomplete",
            **{name: metrics[name] for name in (
                "entries_seen", "entries_included", "entries_after_cutoff",
                "decision_urls_found", "decisions_collected", "decisions_failed",
                "funding_references_extracted",
            )},
            error=message,
        )
    finally:
        if connection is not None:
            connection.close()

