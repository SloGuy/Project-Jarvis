# Capital V3 operator runbook

## Execution boundaries

V3 adds development replay, registered validation, and a shared shadow portfolio.
It does not grant live-capital authority or replace the ongoing paper runners.
Risk modes in the shared coordinator apply to shadow orders only.

The broad market scanner discovers interesting assets. Registered strategies
produce trade candidates. Automatic invention of new strategy implementations
is not established by this code.

## Acceptance

From the repository root with the virtual environment active:

    python scripts/run_v3_acceptance.py

Logs and the result record are written under work/acceptance/.
This is isolated engineering acceptance, not an audit of running services.

## Development replay

Use app.capital.run_evaluation for a single-asset Mean Reversion V2 replay.
Use app.capital.run_shared_shadow for Mean Reversion V2 and Volatility Breakout
sharing cash, allocation limits and risk checks.

The shared runner requires a saved successful shadow allocator snapshot:

    python -m app.capital.run_shared_shadow --help

Its current bound is 360 one-minute ticks. Parameters and market input references
are saved in the output directory. A current allocator snapshot used against old
prices is a development assumption, not historical point-in-time allocation.

Risk modes: normal allows entries and exits, reduce_only blocks entries, and
halted pauses all shadow orders. Halting does not liquidate existing holdings.

Optional --target-volatility-percent scales entries downward using volatility
of observation-to-observation percentage returns. Observations may be irregular.
The target is not annualized. Insufficient return history produces a zero scale.
The most conservative selected-asset scale applies across the strategies.

## Prospective validation

A draft must follow validation_plan.py and contain explicit numeric acceptance
criteria, research bindings, strategy version, provider, asset, policy and
execution manifest. Do not reuse synthetic test criteria as research policy.
Registration assigns created_at; the draft must omit it. The period must start
after registration and contain at most 10,000 one-minute ticks.

    python -m app.capital.validation_admin register --draft PATH
    python -m app.capital.validation_admin status
    python -m app.capital.validation_admin status --plan-id PLAN_ID

Registration checks current research and configuration bindings. The registered
validation runner currently supports Mean Reversion V2 only. A shared shadow
replay is not accepted as a registered multi-strategy validation.

After the period ends:

    python -m app.capital.run_validation PLAN_ID

A claim is single-use. Failed and unfinished attempts retain their reservations.
Completion means replay, verification, analysis and assessment finished.
It does not mean the strategy passed. Registry completion receipts retain result
and assessment hashes.

    python -m app.capital.validation_research PLAN_ID
    python -m app.capital.validation_research PLAN_ID --attach

The first command verifies; --attach writes the verified outcome to its exact
research version without changing status or verdict. Hypothesis revisions clear
the child's validation attachments and preserve the parent's history.

Current data availability is unverified. Numeric performance can pass while the
validation result remains insufficient_evidence. Do not change the availability
flag to manufacture a passing assessment.

Committee requires matching lineage and verified current validation attempts.
Missing/unverifiable evidence stays pending. A measured failed assessment adds a
failed graduation gate. Paper, risk and human-review requirements still apply.

## Recovery and artifact retention

Keep runtime/capital/validation/registry.json and its referenced evaluation
directories together. Keep work/shared_shadow/ directories if their runs matter.
These artifacts are not protected merely because source is committed to Git.

Shadow checkpoints contain recorded inputs, results and configuration fingerprints.
They rebuild confirmation counts, holdings, cash and exit state during verification.
The process_tick API accepts exact retries without duplicating effects. Conflicting
retries and code/configuration drift are rejected.

Do not edit source fingerprints, delete reservations, or rewrite earlier reports
to force recovery. Restore the matching code version in an isolated environment
when inspecting an older checkpoint. No compatibility migration is implied.

A validation process interrupted after claiming can remain running in the registry.
Confirm that no process is still working, preserve partial artifacts, and investigate
before any explicit failure/recovery action. Automatic claim expiry is not implemented.

Filesystem locks and atomic replacement protect cooperative local writers.
Hashes are integrity checks, not signatures or authenticated external timestamps.
Direct database access is outside adapter reservation enforcement.

## Operational checks still required

Inspect service/timer status separately. Passing synthetic tests does not prove
that the scanner or paper timers are active, or that a running service loaded
new source. Service restarts and deployment need a concrete operational review.

Strategy effectiveness, sufficient paper observation, data-availability proof and
untouched validation outcomes remain separate from engineering completion.

## V3 engineering acceptance — 2026-09-08

All 34 consolidated acceptance modules passed.
Record:
work/acceptance/f1a7c034b5144428b7ae7095c96a809f/result.json

Scanner, collector, MR2 paper, MR2 risk, and breakout services
reported successful recent exits. No manual restart was required.

### Paper comparison

Artifact:
work/paper_comparisons/a0fe1b217b834908b9a1edfab6fe1cc2/comparison.json

XMR, portfolio 5, September 7 16:30 to September 8 13:30 UTC:
paper had one entry and one stop-loss exit;
replay had one entry and no exit.

Paper entry: 527.85. Its 5% stop threshold: 501.4575.
Paper exit: 501.42, below that threshold.
Replay entry before costs: 525.46.
Replay stop thresholds: 499.1870 without costs;
499.4365935 using the cost-adjusted fill.
Lowest sampled reference after replay entry: 500.97.
Neither replay stop threshold was crossed.

The exit difference is consistent with different entry prices.
Replay uses fresh confirmation state and fixed decision times.
Exact paper execution parity remains unverified.

The September 4 comparison preceded MR2's September 7 launch
and cannot assess paper agreement.

### Delivery boundary

Implemented and tested:
- Versioned research and evidence attachments.
- Registered validation, assessments, and Committee gates.
- Reproducible replay with costs and benchmark analysis.
- Paper activity comparison.
- Two-strategy shared shadow accounting and allocation limits.
- Risk modes, downward sizing, and checkpoint recovery.

Existing market-candidate scanning continues.
Automatic invention of new strategies is outside this delivery.

Engineering acceptance does not establish profitability.
Historical availability remains unverified.
Sufficient prospective evidence remains outstanding.
This checkpoint grants no live-capital authorization.
