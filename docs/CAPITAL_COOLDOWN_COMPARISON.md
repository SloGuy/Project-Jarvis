# Capital cooldown comparison

## Implemented behavior

The Mean Reversion V2 development replay supports an optional
60-minute same-asset re-entry cooldown after a completed trade
with negative realized profit after simulated fees and slippage.

The cooldown is disabled by default.

Only the intervention account's own completed losses start its
cooldown. New entries are blocked strictly before the expiry time;
entry is allowed at the exact expiry time if existing entry and
risk rules permit it.

Original confirmation rules continue during the cooldown.
Protective exits remain unchanged.

## Comparison

Baseline and intervention accounts start independently with the
same capital, policy, snapshots, decision times, fees and slippage.

The provider connector reads a completed version-two validation
and its sealed receipt collection. It reconstructs quote history
from embedded receipts and checks the retained registry checkpoint.

The connector does not claim plans, write registry state, change
research candidates, or execute database-backed trades.

This comparison currently supports Mean Reversion V2 only.

## Configuration and verification

The comparison manifest fingerprints simulation sources, provider
sources, comparison sources, policy, costs, arithmetic settings,
and cooldown rules.

Offline verification reconstructs decision inputs and both accounts.
It compares saved snapshots, provenance, events, fills, summaries,
and metadata against the reconstruction.

Expected configuration and collection references must be supplied
from independently retained records, not copied from the report
being verified.

Hashes establish consistency; they do not independently authenticate
provider data or archive source files.

## Validation performed

Marked analysis: 8 tests.
Comparison assessment: 13 tests.
Cooldown behavior: 8 tests.
Comparison behavior: 9 tests.
Configuration manifest: 11 tests.
Provider orchestration: 10 tests.
Real receipt integration: 5 tests.
Serialized packet verification: 10 tests.

The combined regression run also passed position simulation,
signal replay, fresh confirmations, replay manifests, trade research,
Capital worker trade research, and validation test groups.

Real receipt integration uses temporary storage. It verifies that
original quote files can be removed without preventing embedded
history reconstruction.

## Remaining work

A prospective comparison requires its own registration before
collection begins, including the source manifest, asset universe,
period, schedule, costs, benchmark, full acceptance criteria, and
sample-size and inconclusive-result rules.

Marked equity, return, sampled drawdown, benchmark comparison and
data-quality analysis are implemented and tested against the existing
verified replay analysis.

Development assessment preserves the source acceptance criteria.
Both accounts require sufficient samples. The intervention must meet
the source performance criteria and supplied profit-factor minimum,
and its profit factor must strictly exceed the baseline.

Missing marks, undefined profit factors, inadequate samples and
unverified availability remain insufficient evidence. Failed checks
remain visible when evidence is insufficient.

A development criteria pass does not become a validated strategy pass.
Prospective registration and autonomous assessment persistence remain
to be implemented. Realized profit factor excludes open positions.

The full-period runtime and operational collection cadence remain
to be checked.

A reproducible development comparison does not establish that the
cooldown improves performance or authorize a strategy change.

## Production boundary

Production remains on its original validation sources.

The existing version-one validation must finish evaluation,
verified evidence attachment and review under its pinned environment
before source replacement. Preserve that environment and its evidence
for later verification.

No promotion or live-capital authority is granted by this work.
