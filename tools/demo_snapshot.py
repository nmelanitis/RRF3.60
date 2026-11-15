"""Build a retained, offline snapshot for human verification.

Run from any directory with the project's Python environment. Prints only the
new snapshot directory, so it can be captured in a shell variable. All generated
inputs and outputs live in a new temporary directory, retained for inspection.
"""

from datetime import datetime
from html import escape
import argparse
from pathlib import Path
import re
from tempfile import mkdtemp
from zoneinfo import ZoneInfo

from crawld.snapshot import SnapshotRequest, collect_snapshot


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a retained offline snapshot for human verification"
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        help="Greece 2.0 decision PDF to use as the collected decision",
    )
    parser.add_argument(
        "--catalogue-ops",
        default="5224393",
        help="OPS displayed in the synthetic catalogue (default: 5224393)",
    )
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    project = Path(__file__).resolve().parents[1]
    fixtures = project / "tests" / "fixtures"
    root = Path(mkdtemp(prefix="crawld-human-check-"))
    pdf = (
        arguments.pdf.expanduser().resolve()
        if arguments.pdf
        else fixtures / "greece20_5224393_inclusion.pdf"
    )
    if not pdf.is_file():
        raise FileNotFoundError(f"Decision PDF not found: {pdf}")
    (root / "good.pdf").write_bytes(pdf.read_bytes())
    (root / "after-cutoff.pdf").write_bytes(pdf.read_bytes())
    (root / "corrupt.pdf").write_bytes(b"")
    cases = [
        ("01/01/2024", "missing.pdf"),
        ("01/01/2024", "good.pdf"),
        ("02/01/2024", "good.pdf"),
        ("03/01/2024", "corrupt.pdf"),
        ("", None),
        ("01/01/2027", "after-cutoff.pdf"),
    ]
    rows = []
    for sequence, (inclusion_date, filename) in enumerate(cases, start=1):
        ops = escape(arguments.catalogue_ops)
        if filename:
            ops = f'<a href="{escape((root / filename).as_uri())}">{ops}</a>'
        cells = [str(sequence), "Demo pillar", "Demo axis", "Demo ministry",
                 "Demo body", "DEMO-ACTION", "Synthetic action", inclusion_date,
                 "Synthetic project", "Όχι", ops, "Επένδυση", "1.000,00"]
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    html = (fixtures / "greece20_catalogue.html").read_text(encoding="utf-8")
    html = re.sub(r"<tbody>.*?</tbody>", "<tbody>" + "\n".join(rows) + "</tbody>",
                  html, flags=re.S)
    catalogue = root / "catalogue.html"
    catalogue.write_text(html, encoding="utf-8")
    result = collect_snapshot(SnapshotRequest(
        cutoff=datetime(2024, 12, 31, 23, 59, tzinfo=ZoneInfo("Europe/Athens")),
        output_root=root / "snapshots", source_url=catalogue.as_uri(),
    ))
    if result.status != "complete":
        raise RuntimeError(f"Demo failed: {result.error}; files at {root}")
    print(result.snapshot_dir)


if __name__ == "__main__":
    main()
