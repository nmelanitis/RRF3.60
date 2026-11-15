"""Deterministic, explainable anomaly-flag rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from crawld.kimdis import CompanyIdentifier, ContractResolutionStatus, CurrentContract
from crawld.profiles import CompanyFundingProfile
from crawld.state_aid import AidInstrument, StateAidAward


class AnomalyRoute(StrEnum):
    CONTRACT = "contract"
    STATE_AID = "state_aid"
    RRF_LINKED_AID = "rrf_linked_aid"


class AssessmentOutcome(StrEnum):
    FLAGGED = "flagged"
    NOT_FLAGGED = "not_flagged"
    UNABLE_TO_ASSESS = "unable_to_assess"


class AssessmentSubject(StrEnum):
    CONTRACT = "contract"
    COMPANY = "company"
    AID_AWARD = "aid_award"


@dataclass(frozen=True)
class RedFlagFact:
    name: str
    value: str


@dataclass(frozen=True)
class DirectAwardRuleConfig:
    rule_version: str
    observation_start: str
    observation_end: str
    observation_scope: str
    coverage_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rule_version.strip():
            raise ValueError("Direct-award rule version is required")
        if self.observation_end < self.observation_start:
            raise ValueError("Direct-award observation end precedes start")
        if not self.observation_scope.strip():
            raise ValueError("Direct-award observation scope is required")


@dataclass(frozen=True)
class RepeatedContractRuleConfig:
    rule_version: str
    observation_start: str
    observation_end: str
    observation_scope: str
    minimum_contracts: int = 5
    window_days: int = 90
    coverage_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rule_version.strip():
            raise ValueError("Repeated-contract rule version is required")
        if self.observation_end < self.observation_start:
            raise ValueError("Repeated-contract observation end precedes start")
        if not self.observation_scope.strip():
            raise ValueError("Repeated-contract observation scope is required")
        if self.minimum_contracts < 2:
            raise ValueError("Repeated-contract minimum must be at least two")
        if self.window_days < 1:
            raise ValueError("Repeated-contract window must be positive")


@dataclass(frozen=True)
class RepeatedStateAidRuleConfig:
    rule_version: str
    observation_start: str
    observation_end: str
    observation_scope: str
    coverage_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rule_version.strip() or not self.observation_scope.strip():
            raise ValueError("Repeated-aid version and scope are required")
        if date.fromisoformat(self.observation_end) < date.fromisoformat(self.observation_start):
            raise ValueError("Repeated-aid observation end precedes start")


@dataclass(frozen=True)
class ContractValueIncreaseRuleConfig:
    rule_version: str
    observation_start: str
    observation_end: str
    observation_scope: str
    coverage_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rule_version.strip() or not self.observation_scope.strip():
            raise ValueError("Value-increase version and scope are required")
        if date.fromisoformat(self.observation_end) < date.fromisoformat(self.observation_start):
            raise ValueError("Value-increase observation end precedes start")


@dataclass(frozen=True)
class RedFlagAssessment:
    rule_id: str
    rule_version: str
    outcome: AssessmentOutcome
    route: AnomalyRoute
    subject_type: AssessmentSubject
    subject_identifier: str
    observation_start: str
    observation_end: str
    observation_scope: str
    rule_source_url: str
    observed_facts: tuple[RedFlagFact, ...]
    trigger: str | None
    evidence_record_ids: tuple[str, ...]
    explanation: str
    limitations: tuple[str, ...]


KIMDIS_PROCEDURE_MAPPING_SOURCE = (
    "https://cerpp.eprocurement.gov.gr/khmdhs-opendata/help"
)
KIMDIS_PROCEDURE_LABELS = {
    "1": "Ανοιχτή διαδικασία",
    "2": "Κλειστή διαδικασία",
    "4": "Ανταγωνιστικός διάλογος",
    "6": "Απευθείας ανάθεση",
    "7": "Ανταγωνιστική διαδικασία με διαπραγμάτευση",
    "11": "Σύμπραξη καινοτομίας",
    "12": "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση",
    "13": "Διαπραγμάτευση με προηγούμενη προκήρυξη διαγωνισμού (αρ.266)",
    "18": "Διαδικασία άρθρου 128 του ν.4412/16",
    "20": "Διαδικασία άρθρου 119 του ν.4412/16",
}
KIMDIS_DIRECT_AWARD_PROCEDURE_CODE = "6"
REPEATED_CONTRACT_RULE_SOURCE = (
    "docs/anomaly-red-flags-plan.md#repeated-contracts"
)


@dataclass(frozen=True)
class AnomalyConfig:
    rule_version: str
    observation_start: str
    observation_end: str
    rrf_aid_share_threshold: Decimal
    minimum_rrf_aid_cents: int
    repeated_contract_minimum: int = 5
    repeated_contract_window_days: int = 90
    rrf_aid_observation_start: str | None = None
    rrf_aid_observation_end: str | None = None
    rrf_aid_scope_description: str = "EC State Aid export scope"

    def __post_init__(self) -> None:
        if not self.rule_version.strip():
            raise ValueError("Anomaly rule version is required")
        if self.observation_end < self.observation_start:
            raise ValueError("Anomaly observation end precedes start")
        if (self.rrf_aid_observation_start is None) != (
            self.rrf_aid_observation_end is None
        ):
            raise ValueError("RRF-aid observation start and end must be supplied together")
        if (
            self.rrf_aid_observation_start is not None
            and self.rrf_aid_observation_end is not None
            and self.rrf_aid_observation_end < self.rrf_aid_observation_start
        ):
            raise ValueError("RRF-aid observation end precedes start")
        if self.rrf_aid_share_threshold <= 0 or self.rrf_aid_share_threshold > 1:
            raise ValueError(
                "rrf_aid_share_threshold must be greater than 0 and at most 1"
            )
        if self.minimum_rrf_aid_cents < 0:
            raise ValueError("Anomaly minimum value cannot be negative")
        if self.repeated_contract_minimum < 2:
            raise ValueError("Repeated-contract minimum must be at least two")
        if self.repeated_contract_window_days < 1:
            raise ValueError("Repeated-contract window must be positive")


@dataclass(frozen=True)
class AnomalyFlag:
    rule_id: str
    rule_version: str
    route: AnomalyRoute
    scope_identifier: CompanyIdentifier
    observation_start: str
    observation_end: str
    observation_scope: str
    numerator_cents: int
    denominator_cents: int
    observed_share: Decimal
    threshold: Decimal
    evidence_record_ids: tuple[str, ...]
    explanation: str


def assess_direct_awards(
    contracts: Iterable[CurrentContract], config: DirectAwardRuleConfig
) -> tuple[RedFlagAssessment, ...]:
    """Assess each logical KIMDIS contract from its original source record."""
    assessments: list[RedFlagAssessment] = []
    for contract in contracts:
        original = next(
            (
                version
                for version in contract.versions
                if version.reference_number == contract.contract_id
                and version.previous_reference_number is None
            ),
            None,
        )
        if original is None:
            evidence_ids = tuple(
                sorted({version.reference_number for version in contract.versions})
            )
            assessments.append(
                RedFlagAssessment(
                    rule_id="direct_award",
                    rule_version=config.rule_version,
                    outcome=AssessmentOutcome.UNABLE_TO_ASSESS,
                    route=AnomalyRoute.CONTRACT,
                    subject_type=AssessmentSubject.CONTRACT,
                    subject_identifier=contract.contract_id,
                    observation_start=config.observation_start,
                    observation_end=config.observation_end,
                    observation_scope=config.observation_scope,
                    rule_source_url=KIMDIS_PROCEDURE_MAPPING_SOURCE,
                    observed_facts=(),
                    trigger=None,
                    evidence_record_ids=evidence_ids,
                    explanation=(
                        "The original KIMDIS contract record is missing, so its award "
                        "procedure cannot be assessed from a later version."
                    ),
                    limitations=tuple(
                        dict.fromkeys(
                            (
                                *config.coverage_limitations,
                                *(
                                    (contract.history_issue,)
                                    if contract.history_issue is not None
                                    else ()
                                ),
                            )
                        )
                    ),
                )
            )
            continue
        code = original.procedure_code
        label = original.procedure_label
        observed_facts = tuple(
            RedFlagFact(name, value)
            for name, value in (
                ("procedure_code", code),
                ("procedure_label", label),
            )
            if value is not None
        )
        is_direct = code == KIMDIS_DIRECT_AWARD_PROCEDURE_CODE
        recognized = code in KIMDIS_PROCEDURE_LABELS
        label_contradicts = (
            recognized
            and label is not None
            and label != KIMDIS_PROCEDURE_LABELS[code]
        )
        conflicting_versions = tuple(
            version
            for version in contract.versions
            if version is not original
            and version.procedure_code is not None
            and (
                version.procedure_code != code
                or (
                    version.procedure_label is not None
                    and version.procedure_label
                    != KIMDIS_PROCEDURE_LABELS.get(version.procedure_code)
                )
            )
        )
        chain_ambiguous = (
            contract.resolution_status is ContractResolutionStatus.AMBIGUOUS_CHAIN
        )
        outcome = (
            AssessmentOutcome.UNABLE_TO_ASSESS
            if not recognized or label_contradicts or conflicting_versions
            else (
                AssessmentOutcome.FLAGGED
                if is_direct
                else AssessmentOutcome.NOT_FLAGGED
            )
        )
        assessments.append(
            RedFlagAssessment(
                rule_id="direct_award",
                rule_version=config.rule_version,
                outcome=outcome,
                route=AnomalyRoute.CONTRACT,
                subject_type=AssessmentSubject.CONTRACT,
                subject_identifier=contract.contract_id,
                observation_start=config.observation_start,
                observation_end=config.observation_end,
                observation_scope=config.observation_scope,
                rule_source_url=KIMDIS_PROCEDURE_MAPPING_SOURCE,
                observed_facts=observed_facts,
                trigger=(
                    f"KIMDIS procedure code {code} identifies direct assignment"
                    if outcome is AssessmentOutcome.FLAGGED
                    else None
                ),
                evidence_record_ids=tuple(
                    sorted(
                        {
                            original.reference_number,
                            *(version.reference_number for version in conflicting_versions),
                        }
                    )
                ),
                explanation=(
                    "KIMDIS contract versions contain conflicting award-procedure "
                    "facts, so direct assignment cannot be assessed."
                    if conflicting_versions
                    else (
                        "The KIMDIS procedure label contradicts the official meaning "
                        "of its code, so direct assignment cannot be assessed."
                        if label_contradicts
                        else (
                            "The KIMDIS procedure code is missing or unrecognized, so "
                            "direct assignment cannot be assessed."
                            if not recognized
                            else (
                                "KIMDIS identifies the original contract procedure as "
                                "direct assignment. This is an investigation lead, not "
                                "a finding of wrongdoing."
                                if is_direct
                                else "KIMDIS identifies a recognized non-direct procedure."
                            )
                        )
                    )
                ),
                limitations=tuple(
                    dict.fromkeys(
                        (
                            *config.coverage_limitations,
                            *(
                                ("procedure label is missing",)
                                if recognized and label is None
                                else ()
                            ),
                            *(
                                (contract.history_issue,)
                                if chain_ambiguous and contract.history_issue is not None
                                else ()
                            ),
                        )
                    )
                ),
            )
        )
    return tuple(sorted(assessments, key=lambda item: item.subject_identifier))


def _company_subject(identifier: CompanyIdentifier) -> str:
    return f"{identifier.scheme}:{identifier.country}:{identifier.value}"


def assess_repeated_contracts(
    contracts: Iterable[CurrentContract], config: RepeatedContractRuleConfig
) -> tuple[RedFlagAssessment, ...]:
    """Assess repeated logical contracts by exact official contractor identifier."""
    events: dict[CompanyIdentifier, list[tuple[date, str]]] = {}
    missing_dates: dict[CompanyIdentifier, set[str]] = {}
    assessments: list[RedFlagAssessment] = []
    unattributed_contract_count = 0
    for contract in contracts:
        originals = tuple(
            version
            for version in contract.versions
            if version.reference_number == contract.contract_id
            and version.previous_reference_number is None
        )
        if not originals:
            unattributed_contract_count += 1
            assessments.append(
                RedFlagAssessment(
                    rule_id="repeated_contracts",
                    rule_version=config.rule_version,
                    outcome=AssessmentOutcome.UNABLE_TO_ASSESS,
                    route=AnomalyRoute.CONTRACT,
                    subject_type=AssessmentSubject.CONTRACT,
                    subject_identifier=contract.contract_id,
                    observation_start=config.observation_start,
                    observation_end=config.observation_end,
                    observation_scope=config.observation_scope,
                    rule_source_url=REPEATED_CONTRACT_RULE_SOURCE,
                    observed_facts=(),
                    trigger=None,
                    evidence_record_ids=tuple(
                        sorted(
                            {
                                version.reference_number
                                for version in contract.versions
                            }
                        )
                    ),
                    explanation=(
                        "The original contract record is missing, so its original "
                        "contractor and signed date cannot be used in the "
                        "repeated-contract assessment."
                    ),
                    limitations=tuple(
                        dict.fromkeys(
                            (
                                *config.coverage_limitations,
                                *(
                                    (contract.history_issue,)
                                    if contract.history_issue is not None
                                    else ()
                                ),
                            )
                        )
                    ),
                )
            )
            continue
        if len(originals) > 1:
            unattributed_contract_count += 1
            assessments.append(
                RedFlagAssessment(
                    rule_id="repeated_contracts",
                    rule_version=config.rule_version,
                    outcome=AssessmentOutcome.UNABLE_TO_ASSESS,
                    route=AnomalyRoute.CONTRACT,
                    subject_type=AssessmentSubject.CONTRACT,
                    subject_identifier=contract.contract_id,
                    observation_start=config.observation_start,
                    observation_end=config.observation_end,
                    observation_scope=config.observation_scope,
                    rule_source_url=REPEATED_CONTRACT_RULE_SOURCE,
                    observed_facts=(
                        RedFlagFact("conflicting_original_fact_count", str(len(originals))),
                    ),
                    trigger=None,
                    evidence_record_ids=tuple(
                        sorted({version.reference_number for version in originals})
                    ),
                    explanation=(
                        "The conflicting original contract facts prevent selecting a "
                        "reliable original contractor and signed date for the "
                        "repeated-contract assessment."
                    ),
                    limitations=tuple(
                        dict.fromkeys(
                            (
                                *config.coverage_limitations,
                                *(
                                    (contract.history_issue,)
                                    if contract.history_issue is not None
                                    else ()
                                ),
                            )
                        )
                    ),
                )
            )
            continue
        original = originals[0]
        identifiers = {
            contractor.identifier
            for contractor in original.contractors
            if contractor.identifier is not None
        }
        if not identifiers:
            unattributed_contract_count += 1
            assessments.append(
                RedFlagAssessment(
                    rule_id="repeated_contracts",
                    rule_version=config.rule_version,
                    outcome=AssessmentOutcome.UNABLE_TO_ASSESS,
                    route=AnomalyRoute.CONTRACT,
                    subject_type=AssessmentSubject.CONTRACT,
                    subject_identifier=contract.contract_id,
                    observation_start=config.observation_start,
                    observation_end=config.observation_end,
                    observation_scope=config.observation_scope,
                    rule_source_url=REPEATED_CONTRACT_RULE_SOURCE,
                    observed_facts=(),
                    trigger=None,
                    evidence_record_ids=(original.reference_number,),
                    explanation=(
                        "The original contract has no official contractor identifier, "
                        "so it cannot be assigned to a company for the "
                        "repeated-contract assessment."
                    ),
                    limitations=tuple(
                        dict.fromkeys(
                            (
                                *config.coverage_limitations,
                                *(
                                    contractor.identifier_issue
                                    for contractor in original.contractors
                                    if contractor.identifier_issue is not None
                                ),
                            )
                        )
                    ),
                )
            )
            continue
        if any(member.identifier is None for member in original.contractors):
            unattributed_contract_count += 1
            assessments.append(
                RedFlagAssessment(
                    rule_id="repeated_contracts",
                    rule_version=config.rule_version,
                    outcome=AssessmentOutcome.UNABLE_TO_ASSESS,
                    route=AnomalyRoute.CONTRACT,
                    subject_type=AssessmentSubject.CONTRACT,
                    subject_identifier=contract.contract_id,
                    observation_start=config.observation_start,
                    observation_end=config.observation_end,
                    observation_scope=config.observation_scope,
                    rule_source_url=REPEATED_CONTRACT_RULE_SOURCE,
                    observed_facts=(),
                    trigger=None,
                    evidence_record_ids=(original.reference_number,),
                    explanation="The original contract has unidentified consortium members; identified members retain their contract event.",
                    limitations=config.coverage_limitations,
                )
            )
        try:
            signed_date = (
                date.fromisoformat(original.signed_date)
                if original.signed_date is not None
                else None
            )
        except ValueError:
            signed_date = None
        for identifier in identifiers:
            events.setdefault(identifier, [])
            if signed_date is None:
                missing_dates.setdefault(identifier, set()).add(contract.contract_id)
            else:
                events[identifier].append((signed_date, contract.contract_id))

    company_scope_limitations = tuple(
        dict.fromkeys(
            (
                *config.coverage_limitations,
                *(
                    (
                        f"{unattributed_contract_count} observed contract(s) could not "
                        "be assigned reliably to an original contractor",
                    )
                    if unattributed_contract_count
                    else ()
                ),
            )
        )
    )
    for identifier, company_events in events.items():
        ordered = sorted(set(company_events))
        company_missing_dates = tuple(sorted(missing_dates.get(identifier, set())))
        evidence_limitations = (
            ("missing or invalid original signed date",)
            if company_missing_dates
            else ()
        )
        qualifying_ranges: list[tuple[int, int]] = []
        for start_index, (window_start, _) in enumerate(ordered):
            end_index = start_index
            while (
                end_index + 1 < len(ordered)
                and (ordered[end_index + 1][0] - window_start).days
                < config.window_days
            ):
                end_index += 1
            if end_index - start_index + 1 >= config.minimum_contracts:
                qualifying_ranges.append((start_index, end_index))
        if not qualifying_ranges:
            assessments.append(
                RedFlagAssessment(
                    rule_id="repeated_contracts",
                    rule_version=config.rule_version,
                    outcome=(
                        AssessmentOutcome.UNABLE_TO_ASSESS
                        if company_missing_dates or company_scope_limitations
                        else AssessmentOutcome.NOT_FLAGGED
                    ),
                    route=AnomalyRoute.CONTRACT,
                    subject_type=AssessmentSubject.COMPANY,
                    subject_identifier=_company_subject(identifier),
                    observation_start=config.observation_start,
                    observation_end=config.observation_end,
                    observation_scope=config.observation_scope,
                    rule_source_url=REPEATED_CONTRACT_RULE_SOURCE,
                    observed_facts=tuple(
                        RedFlagFact(name, value)
                        for name, value in (
                            ("observed_dated_contract_count", str(len(ordered))),
                            (
                                "contracts_missing_valid_signed_date",
                                str(len(company_missing_dates)),
                            ),
                        )
                        if name != "contracts_missing_valid_signed_date"
                        or company_missing_dates
                    ),
                    trigger=None,
                    evidence_record_ids=tuple(
                        sorted({*(event[1] for event in ordered), *company_missing_dates})
                    ),
                    explanation=(
                        "Repeated-contract timing cannot be ruled out because relevant "
                        "contract dates or collection coverage are incomplete."
                        if company_missing_dates or company_scope_limitations
                        else (
                            f"No observed {config.window_days}-day window contains "
                            f"{config.minimum_contracts} distinct contracts for this "
                            "official contractor identifier."
                        )
                    ),
                    limitations=tuple(
                        dict.fromkeys(
                            (*company_scope_limitations, *evidence_limitations)
                        )
                    ),
                )
            )
            continue
        merged_ranges: list[tuple[int, int]] = []
        for start_index, end_index in qualifying_ranges:
            if merged_ranges and start_index <= merged_ranges[-1][1]:
                previous_start, previous_end = merged_ranges[-1]
                merged_ranges[-1] = (previous_start, max(previous_end, end_index))
            else:
                merged_ranges.append((start_index, end_index))
        for start_index, end_index in merged_ranges:
            qualifying = tuple(ordered[start_index : end_index + 1])
            window_start = qualifying[0][0]
            window_end = qualifying[-1][0]
            assessments.append(
                RedFlagAssessment(
                    rule_id="repeated_contracts",
                    rule_version=config.rule_version,
                    outcome=AssessmentOutcome.FLAGGED,
                    route=AnomalyRoute.CONTRACT,
                    subject_type=AssessmentSubject.COMPANY,
                    subject_identifier=_company_subject(identifier),
                    observation_start=config.observation_start,
                    observation_end=config.observation_end,
                    observation_scope=config.observation_scope,
                    rule_source_url=REPEATED_CONTRACT_RULE_SOURCE,
                    observed_facts=(
                        RedFlagFact("contract_count", str(len(qualifying))),
                        RedFlagFact("window_start", window_start.isoformat()),
                        RedFlagFact("window_end", window_end.isoformat()),
                        RedFlagFact(
                            "window_days_inclusive",
                            str((window_end - window_start).days + 1),
                        ),
                    ),
                    trigger=(
                        f"At least {config.minimum_contracts} distinct contracts within "
                        f"{config.window_days} consecutive calendar days"
                    ),
                    evidence_record_ids=tuple(event[1] for event in qualifying),
                    explanation=(
                        f"This official contractor identifier has {len(qualifying)} "
                        f"contributing observed contracts from {window_start.isoformat()} "
                        f"through {window_end.isoformat()} across overlapping qualifying "
                        "windows. This is an investigation lead, not a finding of "
                        "wrongdoing."
                    ),
                    limitations=tuple(
                        dict.fromkeys(
                            (*company_scope_limitations, *evidence_limitations)
                        )
                    ),
                )
            )
    return tuple(
        sorted(
            assessments,
            key=lambda item: (item.subject_identifier, item.evidence_record_ids),
        )
    )


def assess_contract_value_increases(
    contracts: Iterable[CurrentContract], config: ContractValueIncreaseRuleConfig
) -> tuple[RedFlagAssessment, ...]:
    """Compare original and current contract amounts on a common VAT basis."""
    results: list[RedFlagAssessment] = []
    for contract in contracts:
        originals = tuple(v for v in contract.versions if v.reference_number == contract.contract_id and not v.previous_reference_number)
        currents = tuple(v for v in contract.versions if v.reference_number == contract.current_reference_number)
        facts: list[RedFlagFact] = []
        limitations = list(config.coverage_limitations)
        if contract.history_issue:
            limitations.append(contract.history_issue)
        if contract.cancelled:
            limitations.append("The current contract is cancelled; the comparison retains its recorded source value")
        reason = None
        flagged = False
        evidence = tuple(dict.fromkeys(v.reference_number for v in contract.versions))
        if len(originals) != 1:
            reason = "An unambiguous original contract record is unavailable"
        elif contract.resolution_status is not ContractResolutionStatus.RESOLVED or len(currents) != 1:
            reason = "The current contract version cannot be reliably resolved"
        else:
            original, current = originals[0], currents[0]
            evidence = tuple(dict.fromkeys((original.reference_number, current.reference_number)))
            if original.total_with_vat_cents is not None and current.total_with_vat_cents is not None:
                original_value, current_value = original.total_with_vat_cents, current.total_with_vat_cents
                basis = "total_cost_with_vat"
            elif original.total_without_vat_cents is not None and current.total_without_vat_cents is not None:
                original_value, current_value = original.total_without_vat_cents, current.total_without_vat_cents
                basis = "total_cost_without_vat"
            else:
                original_value = current_value = None
                basis = None
                reason = "Original and current values are unavailable on a common VAT basis"
            if basis is not None:
                facts.extend((RedFlagFact("original_value_cents", str(original_value)),
                              RedFlagFact("current_value_cents", str(current_value)),
                              RedFlagFact("value_basis", basis)))
                if original_value <= 0 or current_value < 0:
                    reason = "The original value must be positive and the current value nonnegative"
                else:
                    flagged = 2 * current_value >= 3 * original_value
                    percent = Decimal(current_value - original_value) * 100 / Decimal(original_value)
                    facts.append(RedFlagFact("increase_percent", str(percent)))
        results.append(RedFlagAssessment(
            rule_id="contract_value_increase", rule_version=config.rule_version,
            outcome=(AssessmentOutcome.UNABLE_TO_ASSESS if reason else
                     AssessmentOutcome.FLAGGED if flagged else AssessmentOutcome.NOT_FLAGGED),
            route=AnomalyRoute.CONTRACT, subject_type=AssessmentSubject.CONTRACT,
            subject_identifier=contract.contract_id,
            observation_start=config.observation_start, observation_end=config.observation_end,
            observation_scope=config.observation_scope,
            rule_source_url="docs/anomaly-red-flags-plan.md",
            observed_facts=tuple(facts),
            trigger="Current value is at least 50% above original value" if flagged else None,
            evidence_record_ids=evidence,
            explanation=reason or "Original and current contract values compared on the same VAT basis. This is an investigation lead, not a finding of wrongdoing.",
            limitations=tuple(dict.fromkeys(limitations)),
        ))
    return tuple(sorted(results, key=lambda item: item.subject_identifier))


def assess_repeated_state_aid(
    awards: Iterable[StateAidAward], config: RepeatedStateAidRuleConfig
) -> tuple[RedFlagAssessment, ...]:
    """Count distinct qualifying awards over the supplied imported export scope."""
    groups: dict[CompanyIdentifier, dict[str, StateAidAward]] = {}
    by_award: dict[str, dict[tuple, StateAidAward]] = {}
    for award in awards:
        signature = (
            award.beneficiary_identifier, award.aid_element_cents, award.currency,
            award.instrument, award.granting_date, award.objective,
            award.granting_authority, award.aid_measure_reference, award.rrf_linkage,
            award.included_in_aid_aggregate,
        )
        by_award.setdefault(award.award_id, {})[signature] = award
    results = []
    gaps = []
    for award_id, facts in sorted(by_award.items()):
        candidates = tuple(facts.values())
        if not any(a.included_in_aid_aggregate and a.instrument not in (AidInstrument.LOAN, AidInstrument.UNKNOWN) for a in candidates):
            continue
        award = candidates[0]
        reason = (
            "Conflicting award facts prevent a reliable count"
            if len(candidates) > 1 else
            "Official beneficiary identifier is missing"
            if award.beneficiary_identifier is None else None
        )
        if reason:
            gaps.append(f"{award_id}: {reason}")
            results.append(RedFlagAssessment(
                rule_id="repeated_state_aid", rule_version=config.rule_version,
                outcome=AssessmentOutcome.UNABLE_TO_ASSESS, route=AnomalyRoute.STATE_AID,
                subject_type=AssessmentSubject.AID_AWARD, subject_identifier=award_id,
                observation_start=config.observation_start, observation_end=config.observation_end,
                observation_scope=config.observation_scope, rule_source_url="docs/anomaly-red-flags-plan.md",
                observed_facts=(), trigger=None, evidence_record_ids=(award_id,),
                explanation=reason, limitations=config.coverage_limitations,
            ))
            continue
        groups.setdefault(award.beneficiary_identifier, {})[award.award_id] = award
    limitations = tuple(dict.fromkeys((*config.coverage_limitations, *gaps)))
    for identifier, observed in sorted(groups.items(), key=lambda item: _company_subject(item[0])):
        selected = tuple(observed[key] for key in sorted(observed))
        flagged = len(selected) >= 2
        results.append(RedFlagAssessment(
            rule_id="repeated_state_aid", rule_version=config.rule_version,
            outcome=(AssessmentOutcome.FLAGGED if flagged else
                     AssessmentOutcome.UNABLE_TO_ASSESS if limitations else AssessmentOutcome.NOT_FLAGGED),
            route=AnomalyRoute.STATE_AID, subject_type=AssessmentSubject.COMPANY,
            subject_identifier=_company_subject(identifier),
            observation_start=config.observation_start, observation_end=config.observation_end,
            observation_scope=config.observation_scope,
            rule_source_url="docs/anomaly-red-flags-plan.md",
            observed_facts=(RedFlagFact("award_count", str(len(selected))), *(
                RedFlagFact(f"award_rrf_linkage:{item.award_id}", item.rrf_linkage.value) for item in selected
            )),
            trigger="At least two distinct qualifying aid awards" if flagged else None,
            evidence_record_ids=tuple(item.award_id for item in selected),
            explanation=f"Observed {len(selected)} distinct qualifying aid awards over the imported export period. This is an investigation lead, not a finding of wrongdoing.",
            limitations=limitations,
        ))
    return tuple(results)


def _rrf_linked_aid_flags(
    profiles: tuple[CompanyFundingProfile, ...],
    config: AnomalyConfig,
) -> list[AnomalyFlag]:
    amounts = [profile.rrf_linked_aid_amount_eur_cents for profile in profiles]
    threshold = config.rrf_aid_share_threshold
    minimum = config.minimum_rrf_aid_cents
    observation_start = config.rrf_aid_observation_start or config.observation_start
    observation_end = config.rrf_aid_observation_end or config.observation_end
    denominator = sum(amounts)
    if denominator <= 0:
        return []
    flags: list[AnomalyFlag] = []
    for profile, numerator in zip(profiles, amounts, strict=True):
        share = Decimal(numerator) / Decimal(denominator)
        if numerator < minimum or share < threshold:
            continue
        flags.append(
            AnomalyFlag(
                rule_id="rrf_linked_aid_element_share",
                rule_version=config.rule_version,
                route=AnomalyRoute.RRF_LINKED_AID,
                scope_identifier=profile.identifier,
                observation_start=observation_start,
                observation_end=observation_end,
                observation_scope=config.rrf_aid_scope_description,
                numerator_cents=numerator,
                denominator_cents=denominator,
                observed_share=share,
                threshold=threshold,
                evidence_record_ids=profile.rrf_linked_eur_amount_award_ids,
                explanation=(
                    f"This company represents {share:.2%} of the observed "
                    "RRF-linked aid route total, at or above the configured "
                    f"{threshold:.2%} threshold. "
                    "This is an investigation lead, not a finding of wrongdoing."
                ),
            )
        )
    return flags


def generate_anomaly_flags(
    profiles: Iterable[CompanyFundingProfile], config: AnomalyConfig
) -> tuple[AnomalyFlag, ...]:
    """Run the RRF-linked State-aid share rule over company profiles."""
    materialized = tuple(profiles)
    flags = _rrf_linked_aid_flags(materialized, config)
    return tuple(
        sorted(
            flags,
            key=lambda flag: (
                flag.route.value,
                -flag.observed_share,
                flag.scope_identifier.value,
            ),
        )
    )
