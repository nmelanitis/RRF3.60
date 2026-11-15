"""Build a retained offline end-to-end analysis for human verification."""

from datetime import UTC, date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
import re
from tempfile import mkdtemp
from zoneinfo import ZoneInfo

from crawld.analysis import AnalysisArtifactRequest, build_analysis_artifact
from crawld.anomalies import AnomalyConfig
from crawld.diavgeia import DiavgeiaSearchResult
from crawld.kimdis import (
    KimdisContractCollectionResult,
    KimdisContractPageResult,
    parse_kimdis_contracts,
    split_kimdis_search_windows,
)
from crawld.snapshot import SnapshotRequest, collect_snapshot
from crawld.state_aid import (
    AidMeasureEvidence,
    RrfAidLinkage,
    StateAidCoverage,
    StateAidExportScope,
    StateAidImportRequest,
    import_state_aid_csv,
)


def _source_snapshot(root: Path, project: Path) -> Path:
    fixtures = project / "tests" / "fixtures"
    pdf = fixtures / "greece20_5224393_inclusion.pdf"
    cells = [
        "1",
        "Demo pillar",
        "Demo axis",
        "Demo ministry",
        "Demo body",
        "DEMO-ACTION",
        "Synthetic action",
        "01/01/2024",
        "Synthetic project",
        "Όχι",
        f'<a href="{escape(pdf.as_uri())}">5224393</a>',
        "Επένδυση",
        "1.000,00",
    ]
    row = "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
    html = (fixtures / "greece20_catalogue.html").read_text(encoding="utf-8")
    html = re.sub(r"<tbody>.*?</tbody>", f"<tbody>{row}</tbody>", html, flags=re.S)
    catalogue = root / "catalogue.html"
    catalogue.write_text(html, encoding="utf-8")
    result = collect_snapshot(
        SnapshotRequest(
            cutoff=datetime(
                2026, 10, 30, 23, 59, tzinfo=ZoneInfo("Europe/Athens")
            ),
            output_root=root / "snapshots",
            source_url=catalogue.as_uri(),
        )
    )
    if result.status != "complete":
        raise RuntimeError(f"Snapshot demo failed: {result.error}")
    return result.snapshot_dir


def _state_aid(root: Path, generated_at: datetime):
    csv_path = root / "state-aid.csv"
    csv_path.write_text(
        "Award ID,Beneficiary Name,Beneficiary Identifier,Beneficiary Identifier "
        "Scheme,Member State,Aid Element,Currency,Aid Instrument,Aid Measure Reference\n"
        "A-1,Contract and aid company,094259216,VAT,EL,500.00,EUR,Grant,SA.12345\n"
        "A-2,Aid-only company,099999999,VAT,EL,900.00,EUR,Grant,SA.12345\n"
        "A-3,Excluded loan company,088888888,VAT,EL,10000.00,EUR,Loan,SA.12345\n",
        encoding="utf-8",
    )
    return import_state_aid_csv(
        StateAidImportRequest(
            csv_path=csv_path,
            source_url="https://webgate.ec.europa.eu/competition/transparency/public",
            imported_at=generated_at,
            measure_evidence=(
                AidMeasureEvidence(
                    aid_measure_reference="SA.12345",
                    rrf_linkage=RrfAidLinkage.LINKED,
                    source_name="Synthetic official-evidence placeholder for demo",
                    source_url="https://competition-cases.ec.europa.eu/SA.12345",
                    source_sha256="a" * 64,
                    source_act_id="SA.12345",
                ),
            ),
            export_scope=StateAidExportScope(
                country="EL",
                date_from="2024-01-01",
                date_to="2026-10-30",
                measure_filters=(),
                full_result_export=True,
            ),
            coverage=StateAidCoverage(
                description=(
                    "Synthetic full-result demo export; not real or exhaustive aid data."
                ),
                publication_thresholds=("Real thresholds vary by legal basis",),
                publication_deadlines=("Real deadlines vary by legal basis",),
            ),
        ),
        root / "staging" / "state-aid",
    )


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    root = Path(mkdtemp(prefix="crawld-analysis-check-"))
    generated_at = datetime.now(UTC)
    snapshot = _source_snapshot(root, project)
    contract = parse_kimdis_contracts(
        [
            {
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
                            "name": "Contract and aid company",
                            "country": "GR",
                        }
                    ]
                },
            }
        ]
    )[0]
    kimdis_raw = root / "staging" / "kimdis-page.json"
    kimdis_raw.parent.mkdir(parents=True, exist_ok=True)
    kimdis_raw.write_bytes(b"{}")
    kimdis = KimdisContractCollectionResult(
        pde_code="2024ΤΑ09100006",
        date_from=date(2024, 1, 1),
        date_to=date(2026, 10, 30),
        page_results=tuple(
            KimdisContractPageResult(
                search=window,
                source_url=f"https://example.invalid/synthetic-kimdis?window={index}",
                retrieved_at=generated_at,
                contracts=(contract,) if index == 0 else (),
                total_pages=1,
                is_last_page=True,
                http_status=200,
                byte_count=2,
                content_sha256=(
                    "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
                ),
                raw_path=kimdis_raw,
                error=None,
            )
            for index, window in enumerate(
                split_kimdis_search_windows(
                    "2024ΤΑ09100006", date(2024, 1, 1), date(2026, 10, 30)
                )
            )
        ),
        contracts=(contract,),
        coverage_complete=True,
    )
    diavgeia = DiavgeiaSearchResult(
        query_term="5224393",
        query_url="https://diavgeia.gov.gr/opendata/search",
        retrieved_at=generated_at,
        acts=(),
        total_reported=None,
        results_complete=None,
        http_status=None,
        byte_count=None,
        content_sha256=None,
        raw_path=None,
        error="offline demo: Diavgeia intentionally not queried",
    )
    result = build_analysis_artifact(
        AnalysisArtifactRequest(
            source_snapshot=snapshot,
            output_root=root / "analyses",
            generated_at=generated_at,
            kimdis_collections=(kimdis,),
            state_aid_imports=(_state_aid(root, generated_at),),
            diavgeia_searches=(diavgeia,),
            anomaly_config=AnomalyConfig(
                rule_version="1.0",
                observation_start="2024-01-01",
                observation_end="2026-10-30",
                rrf_aid_share_threshold=Decimal("0.50"),
                minimum_rrf_aid_cents=0,
            ),
        )
    )
    print(result.analysis_dir)


if __name__ == "__main__":
    main()
