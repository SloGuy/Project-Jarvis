# Capital V3 — BTC revision validation

## Registered run
- Research: research_432466f95cde475da71b62ee60a99477
- Hypothesis version: 2
- Strategy: mean_reversion_v2, registry version 2.0
- Plan: validation_6882a2ce057446cca482f94bb743f353
- Registered SHA256: df563b200266389c4de5b02aa9996679b94b7bc85d68a69eba8fcf860bc80789
- Window: 2026-09-20T02:00:00+00:00 through 2026-09-26T02:00:00+00:00, end exclusive.
- Initial collection SHA256: 56799b6a3dbd52229e51ee7e683255ce05a1afdeadd73237691e139d62cab485
- Source baseline at registration: a5ec994.

The source manifest distinguishes the corrected implementation.
The strategy registry version remains 2.0.

## Criteria
At least 30 completed trades; return >= 0%; excess return >= 0
percentage points; maximum marked drawdown <= 5%; stale ticks <= 5%;
unusable regular ticks <= 10%.

Fees and slippage: 5 bps each per fill.
Benchmark: initial 10% BTC and 90% cash.
A six-day window does not guarantee a sufficient trade sample.

## Launch verification
The collector timer was active. Its post-run hook logged capture 1.
Registry status: registered. Collection status: collecting.
Current source binding and store/registry checkpoint equality passed.
This confirms initial capture only, not continued collection success.

Hook:
  /etc/systemd/system/jarvis-market-collector.service.d/v3-btc-revision-validation.conf

The hook captures this plan after each market-collector run.
Its leading minus preserves collector operation if capture fails.
Check witness logs and receipt progress separately from service success.

## Evidence boundaries
The parent research_55f2dffb297a received an INCONCLUSIVE review
and was archived during revision. Its historical evidence is retained.
The child starts without inherited assessments or recommendations.

Do not modify pinned simulation or witness-processing sources during
collection and evaluation. Investigate mismatches rather than replacing
hashes or bypassing verification.

Live capital is disabled. Human approval remains required.
No experiment or promotion authority was created.

## After the window ends
1. Confirm server UTC time is after September 26, 2026, 02:00 UTC.
2. Inspect plan state, witness logs, receipt progress and integrity.
3. Inspect and remove only this plan's named collector drop-in.
   Reload systemd and let any in-flight collector invocation finish.
   Keep the ordinary market collector and timer running.
4. Seal the collection using validation_collection seal PLAN_ID.
5. If the plan is still registered, execute using run_validation PLAN_ID.
   Never blindly rerun a running, completed, or failed plan.
6. Verify the saved completion packet with validation_research PLAN_ID.
7. Attach verified evidence, record its advisory recommendation, and
   review Committee results through the supported functions.
8. Document the actual outcome, limitations, and hook cleanup.

No end-of-window evaluation or cleanup automation has been installed.
After the deadline, capture attempts will fail until the hook is removed.
