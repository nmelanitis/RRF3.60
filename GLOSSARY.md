# RRF Public-Record Analysis

This context reconstructs the official history of Greek Recovery and
Resilience Facility funding reaching companies through public contracts and
direct aid awards.

## Language

**RRF action**:
An officially approved investment or reform in the Greek Recovery and
Resilience Facility, identified by its action code.
_Avoid_: Programme, announcement

**RRF project**:
A funded project or subproject included under an RRF action, normally identified
by an ΟΠΣ ΤΑ code.
_Avoid_: Action, contract

**Administrative act**:
An official decision published by a public authority, normally identified in
Diavgeia by an ΑΔΑ.
_Avoid_: Document, action

**Diavgeia publication status**:
The current publication lifecycle state of a Diavgeia record and its versions.
It does not by itself establish the administrative act's legal effect.
_Avoid_: Cancellation, withdrawal of an RRF project

**Greece 2.0 decision role**:
The role a collected Greece 2.0 decision plays in the project history:
inclusion, modification, or unresolved. Its role is recorded separately from
any explicit cancellation effect.
_Avoid_: Treating every collected decision as an inclusion decision

**Legal effect**:
An effect imposed by the operative text of an administrative act, such as full
or partial cancellation. It requires exact text evidence.
_Avoid_: Inferring effect from filename, budget change, citation, or publication status

**RRF project funding reference**:
The ΟΠΣ ΤΑ and ΠΔΕ references explicitly connecting an RRF project to
national public-funding records. In code this is `RrfProjectFundingReference`.
_Avoid_: Aid measure reference

**Aid measure reference**:
The identifier of the State-aid scheme or legal measure under which an award
is granted. It is not an OPS or ΠΔΕ code.
_Avoid_: RRF project funding reference

**Contract**:
A legally distinct public contract or separately awarded lot, including its
dated amendments and status changes.
_Avoid_: Award document, procurement action

**Contractor**:
A legal entity or consortium awarded a contract, identified by ΑΦΜ when
available.
_Avoid_: Aid beneficiary, implementing authority

**Aid beneficiary**:
A legal entity receiving State aid directly rather than payment under a public
contract, identified by ΑΦΜ or another official beneficiary identifier when
available.
_Avoid_: Contractor, implementing authority

**Funding recipient**:
A company observed as a contractor, an aid beneficiary, or both; the term does
not erase the route by which each amount was awarded.
_Avoid_: Contractor when aid is meant, beneficiary when a contract is meant

**Aid award**:
An individual State-aid award reported for one aid beneficiary, with its aid
element, instrument, granting date, authority, objective, and measure reference.
_Avoid_: Contract, payment

**Aid element**:
The published economic advantage of a State-aid award. It is not necessarily
the nominal financing, guarantee face value, cash disbursement, or payment.
_Avoid_: Loan principal, contract value, money received

**RRF-linked aid award**:
An aid award connected to the RRF by an explicit RRF statement in its official
State Aid Transparency measure name, or by authoritative evidence for its exact
aid measure reference. Conflicting official evidence leaves the linkage
unresolved.
_Avoid_: Aid inferred to be RRF-funded from company name, date, or amount alone

**State-aid evidence collection**:
An immutable lookup run that extracts exact `SA.XXXXX` records from a locally
cached official Commission case dataset, preserves any official documents used,
and writes a new resolved configuration plus a provenance manifest. Missing or
failed lookups remain unresolved; the run does not create negative evidence.
_Avoid_: Live browser search, editing the award CSV, treating absence as non-RRF

**Company funding profile**:
The evidence-linked contract awards and aid awards observed for one company,
grouped by funding route and official identifier.
_Avoid_: Money received, exhaustive public-funding total

**Current contract value**:
The value on the resolvable current record in a KIMDIS amendment chain. If the
successor is missing, the value remains unresolved; a known current value can
still carry an incomplete-history warning when a predecessor is missing.
_Avoid_: Payments, original award value when superseded

**Official identifier merge**:
Cross-source identity matching only when identifier scheme, country, and value
all match exactly. Similar names are retained as separate entities.
_Avoid_: Name-only merge

**Cancellation**:
An official decision whose legal effect cancels, withdraws, removes, or
terminates an RRF action or RRF project in full.
_Avoid_: Revoked upload, inactivity, missing record

**Partial cancellation**:
An official cancellation affecting an identifiable part of an RRF action or
RRF project while another part continues or completes.
_Avoid_: Budget reduction, amendment

**Snapshot**:
An immutable evidence set collected through a recorded cutoff, together with
the conclusions reproducible from that evidence.
_Avoid_: Current database, live view

**Derived analysis artifact**:
A new immutable output built from a completed snapshot, recording that
snapshot's manifest and database hashes while leaving the source unchanged.
_Avoid_: Editing or enriching the source snapshot in place

**Source gap**:
A failed, skipped, truncated, incomplete, or indeterminate source query or
import. It means coverage is incomplete, not that the source contains zero
matching records.
_Avoid_: Verified zero, absence of an event

**Anomaly flag**:
An explainable, evidence-linked pattern that warrants investigation but does
not establish corruption or intent.
_Avoid_: Corruption finding, violation

