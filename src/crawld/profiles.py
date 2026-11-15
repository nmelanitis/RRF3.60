"""Build company funding profiles from exact official identifiers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable

from crawld.kimdis import (
    CompanyIdentifier,
    ContractMemberValueAllocation,
    ContractResolutionStatus,
    CurrentContract,
    KimdisContract,
)
from crawld.state_aid import AidInstrument, RrfAidLinkage, StateAidAward


@dataclass(frozen=True)
class CompanyFundingProfile:
    identifier: CompanyIdentifier
    names: tuple[str, ...]
    contract_count: int
    active_contract_count: int
    cancelled_contract_count: int
    current_contract_value_cents: int
    unresolved_contract_value_count: int
    aid_award_count: int
    aid_amount_eur_cents: int
    rrf_linked_aid_count: int
    rrf_linked_aid_amount_eur_cents: int
    non_rrf_aid_count: int
    non_rrf_aid_amount_eur_cents: int
    unresolved_aid_count: int
    unresolved_aid_amount_eur_cents: int
    non_eur_aid_count: int
    missing_aid_amount_or_currency_count: int
    excluded_loan_count: int
    excluded_loan_amount_eur_cents: int
    excluded_other_aid_count: int
    excluded_other_aid_amount_eur_cents: int
    contract_ids: tuple[str, ...]
    aid_award_ids: tuple[str, ...]
    current_value_contract_ids: tuple[str, ...]
    rrf_linked_aid_award_ids: tuple[str, ...]
    rrf_linked_eur_amount_award_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompanyProfilesResult:
    profiles: tuple[CompanyFundingProfile, ...]
    unmatched_aid_awards: tuple[StateAidAward, ...]
    contracts_without_identifiers: tuple[str, ...]
    contracts_without_current_identifiers: tuple[str, ...]
    contract_conflicts: tuple[str, ...]
    aid_award_conflicts: tuple[str, ...]


@dataclass
class _ProfileAccumulator:
    identifier: CompanyIdentifier
    names: set[str] = field(default_factory=set)
    contract_ids: set[str] = field(default_factory=set)
    active_contract_ids: set[str] = field(default_factory=set)
    cancelled_contract_ids: set[str] = field(default_factory=set)
    current_contract_value_cents: int = 0
    current_value_contract_ids: set[str] = field(default_factory=set)
    unresolved_contract_value_ids: set[str] = field(default_factory=set)
    aid_award_ids: set[str] = field(default_factory=set)
    aid_amount_eur_cents: int = 0
    rrf_linked_aid_ids: set[str] = field(default_factory=set)
    rrf_linked_eur_amount_award_ids: set[str] = field(default_factory=set)
    rrf_linked_aid_amount_eur_cents: int = 0
    non_rrf_aid_ids: set[str] = field(default_factory=set)
    non_rrf_aid_amount_eur_cents: int = 0
    unresolved_aid_ids: set[str] = field(default_factory=set)
    unresolved_aid_amount_eur_cents: int = 0
    non_eur_aid_ids: set[str] = field(default_factory=set)
    missing_aid_amount_or_currency_ids: set[str] = field(default_factory=set)
    excluded_loan_ids: set[str] = field(default_factory=set)
    excluded_loan_amount_eur_cents: int = 0
    excluded_other_aid_ids: set[str] = field(default_factory=set)
    excluded_other_aid_amount_eur_cents: int = 0


def _key(identifier: CompanyIdentifier) -> tuple[str, str, str]:
    return (identifier.scheme, identifier.country, identifier.value)


def _current_version(contract: CurrentContract) -> KimdisContract | None:
    if contract.current_reference_number is None:
        return None
    return next(
        (
            version
            for version in contract.versions
            if version.reference_number == contract.current_reference_number
        ),
        None,
    )


def _contract_participants(
    contract: CurrentContract,
) -> dict[CompanyIdentifier, set[str]]:
    participants: dict[CompanyIdentifier, set[str]] = {}
    for version in contract.versions:
        for contractor in version.contractors:
            if contractor.identifier is None:
                continue
            names = participants.setdefault(contractor.identifier, set())
            if contractor.name:
                names.add(contractor.name)
    return participants


def _profile(
    profiles: dict[tuple[str, str, str], _ProfileAccumulator],
    identifier: CompanyIdentifier,
) -> _ProfileAccumulator:
    return profiles.setdefault(_key(identifier), _ProfileAccumulator(identifier))


def _add_contract(
    profiles: dict[tuple[str, str, str], _ProfileAccumulator],
    contract: CurrentContract,
) -> bool:
    participants = _contract_participants(contract)
    if not participants:
        return False
    current_version = _current_version(contract)
    current_identifiers = {
        contractor.identifier
        for contractor in current_version.contractors
        if contractor.identifier is not None
    } if current_version else set()
    sole_attribution = (
        current_version is not None
        and current_version.member_value_allocation
        is ContractMemberValueAllocation.SOLE_CONTRACTOR_FULL_VALUE
        and len(current_identifiers) == 1
    )
    for identifier, names in participants.items():
        accumulator = _profile(profiles, identifier)
        accumulator.names.update(names)
        accumulator.contract_ids.add(contract.contract_id)
    if current_version is None:
        for identifier in participants:
            _profile(profiles, identifier).unresolved_contract_value_ids.add(
                contract.contract_id
            )
        return True
    for identifier in current_identifiers:
        accumulator = _profile(profiles, identifier)
        if contract.cancelled is True:
            accumulator.cancelled_contract_ids.add(contract.contract_id)
            continue
        if (
            contract.cancelled is False
            and contract.resolution_status is ContractResolutionStatus.RESOLVED
        ):
            accumulator.active_contract_ids.add(contract.contract_id)
        if (
            contract.cancelled is False
            and contract.resolution_status is ContractResolutionStatus.RESOLVED
            and contract.current_value_cents is not None
            and sole_attribution
        ):
            accumulator.current_contract_value_cents += contract.current_value_cents
            accumulator.current_value_contract_ids.add(contract.contract_id)
        else:
            accumulator.unresolved_contract_value_ids.add(contract.contract_id)
    return True


def _add_award(
    profiles: dict[tuple[str, str, str], _ProfileAccumulator], award: StateAidAward
) -> bool:
    if award.beneficiary_identifier is None:
        return False
    accumulator = _profile(profiles, award.beneficiary_identifier)
    accumulator.names.add(award.beneficiary_name)
    currency = (award.currency or "").upper()
    euro_amount = award.aid_element_cents if currency == "EUR" else None
    if award.aid_element_cents is None or not currency:
        accumulator.missing_aid_amount_or_currency_ids.add(award.award_id)
    elif currency != "EUR":
        accumulator.non_eur_aid_ids.add(award.award_id)
    if not award.included_in_aid_aggregate or award.instrument is AidInstrument.LOAN:
        if award.instrument is AidInstrument.LOAN:
            accumulator.excluded_loan_ids.add(award.award_id)
            if euro_amount is not None:
                accumulator.excluded_loan_amount_eur_cents += euro_amount
        else:
            accumulator.excluded_other_aid_ids.add(award.award_id)
            if euro_amount is not None:
                accumulator.excluded_other_aid_amount_eur_cents += euro_amount
        return True

    accumulator.aid_award_ids.add(award.award_id)
    if euro_amount is not None:
        accumulator.aid_amount_eur_cents += euro_amount
    if award.rrf_linkage is RrfAidLinkage.LINKED:
        accumulator.rrf_linked_aid_ids.add(award.award_id)
        if euro_amount is not None:
            accumulator.rrf_linked_aid_amount_eur_cents += euro_amount
            if euro_amount != 0:
                accumulator.rrf_linked_eur_amount_award_ids.add(award.award_id)
    elif award.rrf_linkage is RrfAidLinkage.NOT_LINKED:
        accumulator.non_rrf_aid_ids.add(award.award_id)
        if euro_amount is not None:
            accumulator.non_rrf_aid_amount_eur_cents += euro_amount
    else:
        accumulator.unresolved_aid_ids.add(award.award_id)
        if euro_amount is not None:
            accumulator.unresolved_aid_amount_eur_cents += euro_amount
    return True


def _freeze(accumulator: _ProfileAccumulator) -> CompanyFundingProfile:
    return CompanyFundingProfile(
        identifier=accumulator.identifier,
        names=tuple(sorted(accumulator.names)),
        contract_count=len(accumulator.contract_ids),
        active_contract_count=len(accumulator.active_contract_ids),
        cancelled_contract_count=len(accumulator.cancelled_contract_ids),
        current_contract_value_cents=accumulator.current_contract_value_cents,
        unresolved_contract_value_count=len(accumulator.unresolved_contract_value_ids),
        aid_award_count=len(accumulator.aid_award_ids),
        aid_amount_eur_cents=accumulator.aid_amount_eur_cents,
        rrf_linked_aid_count=len(accumulator.rrf_linked_aid_ids),
        rrf_linked_aid_amount_eur_cents=(
            accumulator.rrf_linked_aid_amount_eur_cents
        ),
        non_rrf_aid_count=len(accumulator.non_rrf_aid_ids),
        non_rrf_aid_amount_eur_cents=accumulator.non_rrf_aid_amount_eur_cents,
        unresolved_aid_count=len(accumulator.unresolved_aid_ids),
        unresolved_aid_amount_eur_cents=accumulator.unresolved_aid_amount_eur_cents,
        non_eur_aid_count=len(accumulator.non_eur_aid_ids),
        missing_aid_amount_or_currency_count=len(
            accumulator.missing_aid_amount_or_currency_ids
        ),
        excluded_loan_count=len(accumulator.excluded_loan_ids),
        excluded_loan_amount_eur_cents=accumulator.excluded_loan_amount_eur_cents,
        excluded_other_aid_count=len(accumulator.excluded_other_aid_ids),
        excluded_other_aid_amount_eur_cents=(
            accumulator.excluded_other_aid_amount_eur_cents
        ),
        contract_ids=tuple(sorted(accumulator.contract_ids)),
        aid_award_ids=tuple(sorted(accumulator.aid_award_ids)),
        current_value_contract_ids=tuple(
            sorted(accumulator.current_value_contract_ids)
        ),
        rrf_linked_aid_award_ids=tuple(sorted(accumulator.rrf_linked_aid_ids)),
        rrf_linked_eur_amount_award_ids=tuple(
            sorted(accumulator.rrf_linked_eur_amount_award_ids)
        ),
    )


def build_company_profiles(
    contracts: Iterable[CurrentContract], awards: Iterable[StateAidAward]
) -> CompanyProfilesResult:
    """Merge only identical identifier scheme/country/value triples."""
    profiles: dict[tuple[str, str, str], _ProfileAccumulator] = {}
    contracts_without_identifiers: list[str] = []
    contracts_without_current_identifiers: list[str] = []
    contract_groups: dict[str, set[CurrentContract]] = {}
    for contract in contracts:
        contract_groups.setdefault(contract.contract_id, set()).add(contract)
    contract_conflicts = tuple(
        sorted(
            contract_id
            for contract_id, facts in contract_groups.items()
            if len(facts) > 1
        )
    )
    unique_contracts = (
        next(iter(facts))
        for contract_id, facts in sorted(contract_groups.items())
        if contract_id not in contract_conflicts
    )
    for contract in unique_contracts:
        if not _add_contract(profiles, contract):
            contracts_without_identifiers.append(contract.contract_id)
            continue
        current = _current_version(contract)
        if current is not None and not any(
            contractor.identifier is not None for contractor in current.contractors
        ):
            contracts_without_current_identifiers.append(contract.contract_id)
    unmatched_awards: list[StateAidAward] = []
    award_groups: dict[str, dict[StateAidAward, StateAidAward]] = {}
    for award in awards:
        signature = replace(award, source_row_number=0)
        award_groups.setdefault(award.award_id, {}).setdefault(signature, award)
    award_conflicts = tuple(
        sorted(
            award_id
            for award_id, facts in award_groups.items()
            if len(facts) > 1
        )
    )
    unique_awards = (
        next(iter(facts.values()))
        for award_id, facts in sorted(award_groups.items())
        if award_id not in award_conflicts
    )
    for award in unique_awards:
        if not _add_award(profiles, award):
            unmatched_awards.append(award)
    return CompanyProfilesResult(
        profiles=tuple(_freeze(profiles[key]) for key in sorted(profiles)),
        unmatched_aid_awards=tuple(unmatched_awards),
        contracts_without_identifiers=tuple(sorted(contracts_without_identifiers)),
        contracts_without_current_identifiers=tuple(
            sorted(contracts_without_current_identifiers)
        ),
        contract_conflicts=contract_conflicts,
        aid_award_conflicts=award_conflicts,
    )
