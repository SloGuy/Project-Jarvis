# Capital trade diagnostics and advisory research

## Purpose

Detect weak realized profit factor in running paper experiments, preserve
a diagnostic snapshot, and generate a constrained advisory research proposal.

This extension does not repair a strategy automatically. It does not change
trading rules, existing research candidates, validation plans, or live authority.

## Workflow

1. Existing queued research revisions retain worker priority.
2. When revision work is not pending, inspect running paper experiments.
3. Resolve the experiment's active paper portfolio and actual risk policy.
4. Read closed journals and recheck experiment configuration.
5. Queue research when the sample meets the committee trade minimum and
   realized profit factor is below its minimum.
6. Process at most one diagnostic proposal per worker cycle.
7. Persist the diagnosis, predefined design, model inputs, objective and proposal.

Requests are stored under `trade_research_requests` in research state.
One request is created per experiment configuration and trigger.
Repeated cycles preserve its original diagnostic snapshot.

## Research design

The current predefined option compares an unchanged baseline with a
60-minute same-asset re-entry cooldown after a losing closed trade.

The losing outcome must have been available before the entry decision.
Protective exits and other risk rules remain unchanged.
Variants require separate accounts and identical prospective inputs and costs.

The cooldown comparison is not implemented or registered.
A saved proposal explicitly remains `validation_ready: false`.
Model commentary is not scientifically validated.

## Failures and limitations

Failed requests are not silently retried. Interrupted running requests block
processing and require inspection before recovery.

Incomplete queries cannot trigger new research requests.
Mixed-strategy journals and configuration changes are rejected.
Saved completed proposals, designs and model context are checked on later cycles.

Journal hashes identify content; they do not prove authenticity.
Serialized numbers may have reduced precision.
Reads are not a simultaneous database snapshot.
Journal summaries cannot reconstruct intervening prices or establish causation.
The diagnostic configuration fingerprint is not a complete execution manifest.
Future comparisons require pinned sources and full registered acceptance criteria.

## Validation performed

The diagnostic, collector, queue, runner, cycle, worker, design and integrity
test groups passed individually.

Existing research regressions passed before the design constraint was added.
Real PostgreSQL/Ollama smoke runs used read-only database transactions and
temporary research state. The constrained run saved both strategy proposals.

Production services and production research state were not changed.

## Remaining work

Run combined regressions and verify a clean checkout before deployment.
Preserve the original environment for the current registered validation.
Implement and test the cooldown comparison before registering future evidence.
Measure full-window replay performance and verify collection cadence.
