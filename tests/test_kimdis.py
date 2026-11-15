from copy import deepcopy
from datetime import UTC, date, datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch
from urllib.error import HTTPError

from crawld.kimdis import (
    ContractMemberValueAllocation,
    ContractResolutionStatus,
    ContractValueBasis,
    KimdisContractSearch,
    KimdisContractPageResult,
    collect_kimdis_contract_page,
    collect_kimdis_contracts,
    parse_kimdis_contracts,
    select_current_contract_versions,
    split_kimdis_search_windows,
)


def contract_record(
    reference: str,
    *,
    previous: str | None = None,
    next_reference: str | None = None,
    value: str = "1240.00",
) -> dict[str, object]:
    return {
        "title": "Σύμβαση δοκιμής",
        "referenceNumber": reference,
        "prevReferenceNo": previous,
        "nextRefNo": next_reference,
        "contractSignedDate": "2026-01-10",
        "cancelled": False,
        "totalCostWithVAT": value,
        "totalCostWithoutVAT": "1000.00",
        "fundingDetails": [
            {
                "publicFundingRefNum": "2024ΤΑ09100006",
                "publicFundingRefOps": "5224393",
            }
        ],
        "contractingDataDetails": {
            "contractingMembersDataList": [
                {
                    "vatNumber": "EL094259216",
                    "greekVatNumber": True,
                    "name": "Ανάδοχος ΑΕ",
                    "country": "GR",
                }
            ]
        },
    }


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


class KimdisTests(unittest.TestCase):
    def test_search_posts_exact_pde_and_explicit_date_window_and_preserves_raw(
        self,
    ) -> None:
        payload = json.dumps(
            {"content": [contract_record("24SYMV000001")], "totalPages": 1},
            ensure_ascii=False,
        ).encode()
        captured: dict[str, object] = {}

        def retrieve(request: object, timeout: int) -> JsonResponse:
            captured["url"] = str(getattr(request, "full_url"))
            captured["body"] = json.loads(getattr(request, "data").decode())
            return JsonResponse(payload)

        search = KimdisContractSearch(
            pde_code="2024ΤΑ09100006",
            date_from=date(2026, 1, 1),
            date_to=date(2026, 6, 29),
            page=0,
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.kimdis.urlopen", side_effect=retrieve
        ):
            result = collect_kimdis_contract_page(search, Path(directory) / "raw")

            self.assertIn("/khmdhs-opendata/contract?page=0", captured["url"])
            self.assertEqual(
                captured["body"],
                {
                    "publicFundingRefNum": "2024ΤΑ09100006",
                    "dateFrom": "2026-01-01",
                    "dateTo": "2026-06-29",
                },
            )
            self.assertEqual(result.content_sha256, sha256(payload).hexdigest())
            self.assertEqual(result.byte_count, len(payload))
            self.assertEqual(result.http_status, 200)
            self.assertEqual(result.raw_path.read_bytes(), payload)
            self.assertEqual(result.retrieved_at.tzinfo, UTC)
            self.assertIsNone(result.error)
            self.assertEqual(result.total_pages, 1)
            self.assertTrue(result.is_last_page)
            contract = result.contracts[0]
            self.assertEqual(contract.reference_number, "24SYMV000001")
            self.assertEqual(contract.pde_codes, ("2024ΤΑ09100006",))
            self.assertEqual(contract.ops_codes, ("5224393",))
            self.assertEqual(contract.contractors[0].identifier.value, "094259216")
            self.assertEqual(contract.total_with_vat_cents, 124_000)

    def test_captured_live_response_excerpt_parses_at_the_collection_seam(self) -> None:
        payload = (
            Path(__file__).parent
            / "fixtures"
            / "kimdis_contract_2024TA09100006_page0_excerpt.json"
        ).read_bytes()
        search = KimdisContractSearch(
            pde_code="2024ΤΑ09100006",
            date_from=date(2026, 1, 1),
            date_to=date(2026, 6, 29),
            page=0,
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.kimdis.urlopen", return_value=JsonResponse(payload)
        ):
            result = collect_kimdis_contract_page(search, Path(directory) / "raw")

        self.assertIsNone(result.error)
        self.assertEqual(result.total_pages, 1)
        self.assertTrue(result.is_last_page)
        self.assertEqual(len(result.contracts), 1)
        contract = result.contracts[0]
        self.assertEqual(contract.reference_number, "26SYMV019324212")
        self.assertEqual(contract.procedure_code, "1")
        self.assertEqual(contract.procedure_label, "Ανοιχτή διαδικασία")
        self.assertEqual(contract.pde_codes, ("2024ΤΑ09100006",))
        self.assertEqual(contract.ops_codes, ("5224393",))
        self.assertEqual(contract.total_with_vat_cents, 661_925_020)
        self.assertEqual(contract.total_without_vat_cents, 533_810_500)
        self.assertFalse(contract.cancelled)
        self.assertEqual(len(contract.contractors), 4)
        self.assertEqual(
            {(item.identifier.scheme, item.identifier.country) for item in contract.contractors if item.identifier},
            {("VAT", "GR")},
        )

    def test_selects_latest_amendment_as_one_current_contract(self) -> None:
        original = contract_record(
            "24SYMV000001", next_reference="25SYMV000002", value="1240.00"
        )
        amendment = contract_record(
            "25SYMV000002", previous="24SYMV000001", value="2480.00"
        )
        records = parse_kimdis_contracts([original, amendment, amendment])

        current = select_current_contract_versions(records)

        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].contract_id, "24SYMV000001")
        self.assertEqual(current[0].current_reference_number, "25SYMV000002")
        self.assertEqual(current[0].current_value_cents, 248_000)
        self.assertIs(current[0].value_basis, ContractValueBasis.TOTAL_WITH_VAT)
        self.assertEqual(current[0].version_count, 2)
        self.assertIs(current[0].resolution_status, ContractResolutionStatus.RESOLVED)

    def test_consortium_members_are_preserved_without_duplicating_contract_value(
        self,
    ) -> None:
        record = contract_record("24SYMV000001")
        members = record["contractingDataDetails"]
        assert isinstance(members, dict)
        member_list = members["contractingMembersDataList"]
        assert isinstance(member_list, list)
        member_list.append(
            {
                "greekVatNumber": True,
                "vatNumber": "099999999",
                "name": "Εταίρος Β",
                "country": "GR",
            }
        )

        contract = parse_kimdis_contracts([record])[0]

        self.assertEqual(len(contract.contractors), 2)
        self.assertEqual(contract.total_with_vat_cents, 124_000)
        self.assertIs(
            contract.member_value_allocation,
            ContractMemberValueAllocation.UNRESOLVED_CONSORTIUM_SHARES,
        )

    def test_live_consortium_member_order_does_not_create_conflicting_facts(
        self,
    ) -> None:
        record = contract_record("26SYMV019324212")
        details = record["contractingDataDetails"]
        assert isinstance(details, dict)
        members = details["contractingMembersDataList"]
        assert isinstance(members, list)
        members.append(
            {
                "country": {"key": "GR", "value": "Ελλάδα"},
                "vatNumber": "800866750",
                "greekVatNumber": False,
                "name": "«ICOMM CALL CENTER IKE»",
            }
        )
        reordered = deepcopy(record)
        reordered_details = reordered["contractingDataDetails"]
        assert isinstance(reordered_details, dict)
        reordered_members = reordered_details["contractingMembersDataList"]
        assert isinstance(reordered_members, list)
        reordered_members.reverse()

        parsed = parse_kimdis_contracts([record, reordered])

        self.assertEqual(len(parsed), 1)
        self.assertEqual(len(parsed[0].contractors), 2)

    def test_greek_vat_boolean_controls_identifier_scheme_not_identifier_value(
        self,
    ) -> None:
        true_record = contract_record("24SYMV000001")
        false_record = contract_record("24SYMV000002")
        details = false_record["contractingDataDetails"]
        assert isinstance(details, dict)
        members = details["contractingMembersDataList"]
        assert isinstance(members, list)
        members[0] = {
            "vatNumber": "094259216",
            "greekVatNumber": False,
            "name": "Foreign-scheme record",
            "country": "GR",
        }

        true_identifier = parse_kimdis_contracts([true_record])[0].contractors[0].identifier
        false_identifier = parse_kimdis_contracts([false_record])[0].contractors[0].identifier

        assert true_identifier is not None
        assert false_identifier is not None
        self.assertEqual(true_identifier.value, "094259216")
        self.assertEqual(true_identifier.scheme, "EL_VAT")
        self.assertEqual(false_identifier.value, "094259216")
        self.assertEqual(false_identifier.scheme, "VAT")

    def test_live_country_object_uses_its_official_country_key(self) -> None:
        record = contract_record("26SYMV019324212")
        details = record["contractingDataDetails"]
        assert isinstance(details, dict)
        members = details["contractingMembersDataList"]
        assert isinstance(members, list) and isinstance(members[0], dict)
        members[0] = {
            "country": {"key": "GR", "value": "Ελλάδα"},
            "vatNumber": "800866750",
            "greekVatNumber": False,
            "name": "«ICOMM CALL CENTER IKE»",
            "transactorDescription": None,
        }

        identifier = parse_kimdis_contracts([record])[0].contractors[0].identifier

        assert identifier is not None
        self.assertEqual(identifier.value, "800866750")
        self.assertEqual(identifier.scheme, "VAT")
        self.assertEqual(identifier.country, "GR")

    def test_invalid_declared_greek_vat_is_unidentified_with_issue(self) -> None:
        record = contract_record("24SYMV000001")
        details = record["contractingDataDetails"]
        assert isinstance(details, dict)
        members = details["contractingMembersDataList"]
        assert isinstance(members, list) and isinstance(members[0], dict)
        members[0]["vatNumber"] = "123"

        contractor = parse_kimdis_contracts([record])[0].contractors[0]

        self.assertIsNone(contractor.identifier)
        self.assertIn("9 digits", contractor.identifier_issue or "")

    def test_cancellation_status_is_parsed_only_from_actual_boolean(self) -> None:
        malformed = contract_record("24SYMV000001")
        malformed["cancelled"] = "false"
        missing = contract_record("24SYMV000002")
        missing.pop("cancelled")

        records = parse_kimdis_contracts([malformed, missing])

        self.assertIsNone(records[0].cancelled)
        self.assertIn("not Boolean", records[0].cancellation_status_issue or "")
        self.assertIsNone(records[1].cancelled)
        self.assertIn("missing", records[1].cancellation_status_issue or "")

    def test_known_missing_successor_keeps_current_value_unresolved(self) -> None:
        original = contract_record(
            "24SYMV000001", next_reference="25SYMV000002", value="1240.00"
        )

        current = select_current_contract_versions(parse_kimdis_contracts([original]))

        self.assertEqual(len(current), 1)
        self.assertIs(
            current[0].resolution_status,
            ContractResolutionStatus.MISSING_SUCCESSOR,
        )
        self.assertIsNone(current[0].current_reference_number)
        self.assertIsNone(current[0].current_value_cents)
        self.assertIsNone(current[0].cancelled)

    def test_missing_predecessor_keeps_known_current_but_marks_history_incomplete(
        self,
    ) -> None:
        amendment = contract_record(
            "25SYMV000002", previous="24SYMV000001", value="2480.00"
        )

        current = select_current_contract_versions(parse_kimdis_contracts([amendment]))

        self.assertIs(current[0].resolution_status, ContractResolutionStatus.RESOLVED)
        self.assertEqual(current[0].current_value_cents, 248_000)
        self.assertFalse(current[0].history_complete)
        self.assertIn("missing predecessor", current[0].history_issue or "")

    def test_conflicting_facts_for_same_reference_are_unresolved(self) -> None:
        first = contract_record("24SYMV000001", value="100.00")
        second = contract_record("24SYMV000001", value="200.00")

        current = select_current_contract_versions(
            parse_kimdis_contracts([first, second])
        )

        self.assertEqual(len(current), 1)
        self.assertIs(
            current[0].resolution_status,
            ContractResolutionStatus.AMBIGUOUS_CHAIN,
        )
        self.assertIsNone(current[0].current_value_cents)
        self.assertFalse(current[0].history_complete)
        self.assertIn("conflicting facts", current[0].history_issue or "")

    def test_sole_name_without_identifier_does_not_receive_full_value(self) -> None:
        record = contract_record("24SYMV000001")
        details = record["contractingDataDetails"]
        assert isinstance(details, dict)
        members = details["contractingMembersDataList"]
        assert isinstance(members, list)
        assert isinstance(members[0], dict)
        members[0].pop("vatNumber")

        contract = parse_kimdis_contracts([record])[0]

        self.assertIs(
            contract.member_value_allocation,
            ContractMemberValueAllocation.NO_IDENTIFIED_CONTRACTOR,
        )

    def test_splits_overlong_period_and_deduplicates_contracts_across_windows(
        self,
    ) -> None:
        windows = split_kimdis_search_windows(
            "2024ΤΑ09100006", date(2026, 1, 1), date(2026, 7, 1)
        )
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].date_to, date(2026, 6, 29))
        self.assertEqual(windows[1].date_from, date(2026, 6, 30))
        contract = parse_kimdis_contracts([contract_record("24SYMV000001")])[0]

        def collect(search: KimdisContractSearch, raw_directory: Path) -> KimdisContractPageResult:
            return KimdisContractPageResult(
                search=search,
                source_url="https://example.test",
                retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
                contracts=(contract,),
                total_pages=1,
                is_last_page=True,
                http_status=200,
                byte_count=2,
                content_sha256="0" * 64,
                raw_path=raw_directory / "fixture.json",
                error=None,
            )

        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.kimdis.collect_kimdis_contract_page", side_effect=collect
        ):
            result = collect_kimdis_contracts(
                "2024ΤΑ09100006",
                date(2026, 1, 1),
                date(2026, 7, 1),
                Path(directory),
            )

        self.assertTrue(result.coverage_complete)
        self.assertEqual(len(result.page_results), 2)
        self.assertEqual(len(result.contracts), 1)

    def test_rejects_single_search_window_longer_than_180_days(self) -> None:
        with self.assertRaisesRegex(ValueError, "180"):
            KimdisContractSearch(
                pde_code="2024ΤΑ09100006",
                date_from=date(2026, 1, 1),
                date_to=date(2026, 6, 30),
                page=0,
            )

    def test_failed_page_is_a_visible_source_gap(self) -> None:
        search = KimdisContractSearch(
            pde_code="2024ΤΑ09100006",
            date_from=date(2026, 1, 1),
            date_to=date(2026, 6, 29),
            page=0,
        )
        url = "https://cerpp.eprocurement.gov.gr/khmdhs-opendata/contract?page=0"
        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.kimdis.urlopen",
            side_effect=HTTPError(url, 503, "Unavailable", None, None),
        ):
            result = collect_kimdis_contract_page(search, Path(directory) / "raw")

        self.assertEqual(result.contracts, ())
        self.assertIsNone(result.raw_path)
        self.assertIn("HTTP Error 503", result.error or "")

    def test_official_no_contracts_404_is_a_preserved_verified_empty_page(self) -> None:
        payload = json.dumps(
            {
                "message": "No contracts found for the given criteria",
                "status": 404,
            }
        ).encode()
        search = KimdisContractSearch(
            pde_code="2024ΤΑ09100006",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 6, 28),
            page=0,
        )
        url = "https://cerpp.eprocurement.gov.gr/khmdhs-opendata/contract?page=0"
        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.kimdis.urlopen",
            side_effect=HTTPError(
                url,
                404,
                "Not Found",
                None,
                BytesIO(payload),
            ),
        ):
            result = collect_kimdis_contract_page(search, Path(directory) / "raw")

            self.assertEqual(result.contracts, ())
            self.assertEqual(result.http_status, 404)
            self.assertEqual(result.total_pages, 0)
            self.assertTrue(result.is_last_page)
            self.assertIsNone(result.error)
            self.assertEqual(result.content_sha256, sha256(payload).hexdigest())
            self.assertEqual(result.raw_path.read_bytes(), payload)

    def test_official_no_contracts_404_after_page_zero_is_a_source_gap(self) -> None:
        payload = json.dumps(
            {
                "message": "No contracts found for the given criteria",
                "status": 404,
            }
        ).encode()
        search = KimdisContractSearch(
            pde_code="2024ΤΑ09100006",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 6, 28),
            page=1,
        )
        url = "https://cerpp.eprocurement.gov.gr/khmdhs-opendata/contract?page=1"
        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.kimdis.urlopen",
            side_effect=HTTPError(
                url,
                404,
                "Not Found",
                None,
                BytesIO(payload),
            ),
        ):
            result = collect_kimdis_contract_page(search, Path(directory) / "raw")

            self.assertEqual(result.http_status, 404)
            self.assertIsNotNone(result.error)
            self.assertIsNone(result.total_pages)
            self.assertIsNone(result.is_last_page)
            self.assertEqual(result.content_sha256, sha256(payload).hexdigest())
            self.assertEqual(result.raw_path.read_bytes(), payload)

    def test_rate_limit_waits_one_minute_then_retries_the_same_page(self) -> None:
        rate_limit_payload = b'{"message":"Too many requests","status":429}'
        success_payload = json.dumps(
            {"content": [contract_record("26SYMV019324212")], "totalPages": 1},
            ensure_ascii=False,
        ).encode()
        search = KimdisContractSearch(
            pde_code="2024ΤΑ09100006",
            date_from=date(2026, 1, 1),
            date_to=date(2026, 6, 29),
            page=0,
        )
        url = "https://cerpp.eprocurement.gov.gr/khmdhs-opendata/contract?page=0"
        outcomes: list[HTTPError | JsonResponse] = [
            HTTPError(
                url,
                429,
                "Too Many Requests",
                None,
                BytesIO(rate_limit_payload),
            ),
            HTTPError(
                url,
                429,
                "Too Many Requests",
                None,
                BytesIO(rate_limit_payload),
            ),
            JsonResponse(success_payload),
        ]
        requests: list[tuple[str, dict[str, object]]] = []

        def retrieve(request: object, timeout: int) -> JsonResponse:
            requests.append(
                (
                    str(getattr(request, "full_url")),
                    json.loads(getattr(request, "data").decode()),
                )
            )
            outcome = outcomes.pop(0)
            if isinstance(outcome, HTTPError):
                raise outcome
            return outcome

        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.kimdis.urlopen", side_effect=retrieve
        ), patch("crawld.kimdis.sleep") as wait:
            raw_directory = Path(directory) / "raw"
            result = collect_kimdis_contract_page(search, raw_directory)

            self.assertIsNone(result.error)
            self.assertEqual(result.http_status, 200)
            self.assertEqual(result.contracts[0].reference_number, "26SYMV019324212")
            self.assertEqual(wait.call_args_list, [call(60), call(60)])
            self.assertEqual(len(requests), 3)
            self.assertEqual(len(set(url for url, _ in requests)), 1)
            self.assertEqual(
                len({json.dumps(body, sort_keys=True) for _, body in requests}), 1
            )
            self.assertEqual(
                (
                    raw_directory
                    / f"kimdis-contracts-{sha256(rate_limit_payload).hexdigest()}.json"
                ).read_bytes(),
                rate_limit_payload,
            )

    def test_other_http_errors_remain_gaps_and_preserve_available_response(self) -> None:
        search = KimdisContractSearch(
            pde_code="2024ΤΑ09100006",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 6, 28),
            page=0,
        )
        url = "https://cerpp.eprocurement.gov.gr/khmdhs-opendata/contract?page=0"
        cases = ((404, b'{"message":"Different not-found response","status":404}'),)
        for status, payload in cases:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory, patch(
                "crawld.kimdis.urlopen",
                side_effect=HTTPError(
                    url,
                    status,
                    "HTTP failure",
                    None,
                    BytesIO(payload),
                ),
            ):
                result = collect_kimdis_contract_page(
                    search, Path(directory) / "raw"
                )

                self.assertEqual(result.http_status, status)
                self.assertIsNotNone(result.error)
                self.assertIsNone(result.total_pages)
                self.assertIsNone(result.is_last_page)
                self.assertEqual(result.content_sha256, sha256(payload).hexdigest())
                self.assertEqual(result.raw_path.read_bytes(), payload)

    def test_local_storage_failure_is_fatal_not_a_source_gap(self) -> None:
        payload = json.dumps({"content": [], "totalPages": 1}).encode()
        search = KimdisContractSearch(
            pde_code="2024ΤΑ09100006",
            date_from=date(2026, 1, 1),
            date_to=date(2026, 6, 29),
            page=0,
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "crawld.kimdis.urlopen", return_value=JsonResponse(payload)
        ):
            blocked = Path(directory) / "not-a-directory"
            blocked.write_text("occupied", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                collect_kimdis_contract_page(search, blocked)


if __name__ == "__main__":
    unittest.main()
