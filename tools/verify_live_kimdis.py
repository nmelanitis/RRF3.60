"""Run an opt-in live KIMDIS collection and independent PDE-index cross-check."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from crawld.kimdis import collect_kimdis_contracts


KIMDIS_PDE_URL = "https://cerpp.eprocurement.gov.gr/khmdhs-opendata/pde"


def _collect_pde_index(
    pde_code: str, raw_directory: Path
) -> tuple[str, int, str, Path, dict[str, object]]:
    source_url = f"{KIMDIS_PDE_URL}?{urlencode({'pdeNumber': pde_code})}"
    request = Request(
        source_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "crawlD-live-verification/0.1 (+public-data research)",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = response.read()
            http_status = int(getattr(response, "status", None) or 200)
    except HTTPError as error:
        payload = error.read()
        http_status = error.code
    content_hash = sha256(payload).hexdigest()
    raw_directory.mkdir(parents=True, exist_ok=True)
    raw_path = raw_directory / f"kimdis-pde-{content_hash}.json"
    raw_path.write_bytes(payload)
    if http_status != 200:
        raise RuntimeError(
            f"KIMDIS PDE lookup returned HTTP {http_status}; raw response: {raw_path}"
        )
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("KIMDIS PDE lookup response must be an object")
    for field in ("requests", "notices", "contracts", "payments"):
        items = value.get(field)
        if not isinstance(items, list) or not all(
            isinstance(item, str) for item in items
        ):
            raise ValueError(f"KIMDIS PDE lookup field {field!r} must be a string list")
    return source_url, http_status, content_hash, raw_path, value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect live KIMDIS contracts by PDE and cross-check their references "
            "against the independent KIMDIS PDE index. Use a new output directory "
            "for every run."
        )
    )
    parser.add_argument("--pde", required=True, help="Exact Ενάριθμος ΠΔΕ")
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-pde-index-difference",
        action="store_true",
        help=(
            "Do not fail when the date-bounded contract search differs from the "
            "all-date PDE index; use only for deliberately partial date ranges."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output: Path = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    raw_directory = output / "raw"

    source_url, pde_status, pde_hash, pde_raw, pde_index = _collect_pde_index(
        args.pde, raw_directory
    )
    collection = collect_kimdis_contracts(
        args.pde,
        args.from_date,
        args.to_date,
        raw_directory,
    )

    raw_integrity = all(
        page.raw_path is not None
        and page.raw_path.is_file()
        and page.content_sha256 == sha256(page.raw_path.read_bytes()).hexdigest()
        and page.byte_count == page.raw_path.stat().st_size
        for page in collection.page_results
    )
    collected_references = sorted(
        {contract.reference_number for contract in collection.contracts}
    )
    indexed_references = sorted(set(pde_index["contracts"]))
    every_contract_matches_pde = all(
        args.pde in contract.pde_codes for contract in collection.contracts
    )
    index_matches = collected_references == indexed_references
    checks = {
        "coverage_complete": collection.coverage_complete,
        "raw_integrity": raw_integrity,
        "every_contract_matches_requested_pde": every_contract_matches_pde,
        "contract_references_match_pde_index": index_matches,
    }
    success = all(
        value
        for name, value in checks.items()
        if name != "contract_references_match_pde_index"
    ) and (index_matches or args.allow_pde_index_difference)

    summary = {
        "verification_version": 1,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "query": {
            "pde_code": args.pde,
            "date_from": args.from_date.isoformat(),
            "date_to": args.to_date.isoformat(),
        },
        "checks": checks,
        "success": success,
        "pde_index": {
            "source_url": source_url,
            "http_status": pde_status,
            "content_sha256": pde_hash,
            "raw_path": str(pde_raw.relative_to(output)),
            "contract_references": indexed_references,
        },
        "contract_collection": {
            "contract_references": collected_references,
            "page_count": len(collection.page_results),
            "contract_count": len(collection.contracts),
            "pages": [
                {
                    "date_from": page.search.date_from.isoformat(),
                    "date_to": page.search.date_to.isoformat(),
                    "page": page.search.page,
                    "source_url": page.source_url,
                    "http_status": page.http_status,
                    "byte_count": page.byte_count,
                    "content_sha256": page.content_sha256,
                    "raw_path": (
                        str(page.raw_path.relative_to(output))
                        if page.raw_path is not None
                        else None
                    ),
                    "contract_count": len(page.contracts),
                    "total_pages": page.total_pages,
                    "is_last_page": page.is_last_page,
                    "error": page.error,
                }
                for page in collection.page_results
            ],
            "contracts": [
                {
                    "reference_number": contract.reference_number,
                    "pde_codes": list(contract.pde_codes),
                    "ops_codes": list(contract.ops_codes),
                    "contractor_count": len(contract.contractors),
                    "identified_contractor_count": sum(
                        contractor.identifier is not None
                        for contractor in contract.contractors
                    ),
                    "identifier_schemes_and_countries": sorted(
                        {
                            (
                                contractor.identifier.scheme,
                                contractor.identifier.country,
                            )
                            for contractor in contract.contractors
                            if contractor.identifier is not None
                        }
                    ),
                    "cancelled": contract.cancelled,
                    "total_with_vat_cents": contract.total_with_vat_cents,
                    "total_without_vat_cents": contract.total_without_vat_cents,
                }
                for contract in collection.contracts
            ],
        },
    }
    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Live KIMDIS verification: {'PASS' if success else 'FAIL'}")
    print(f"Summary: {summary_path}")
    print(f"Pages: {len(collection.page_results)}")
    print(f"Contracts: {len(collection.contracts)}")
    print(f"PDE index contracts: {len(indexed_references)}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
