# Capital V3 — BTC hypothesis 2 validation closeout

Closed: 2026-09-27 UTC.

## Registered attempt

- Research: research_432466f95cde475da71b62ee60a99477
- Hypothesis version: 2
- Strategy: mean_reversion_v2, version 2.0
- Plan: validation_6882a2ce057446cca482f94bb743f353
- Window: 2026-09-20T02:00:00+00:00 to
  2026-09-26T02:00:00+00:00, end exclusive.
- Plan SHA256:
  df563b200266389c4de5b02aa9996679b94b7bc85d68a69eba8fcf860bc80789
- Sealed collection SHA256:
  ff24bc13230b6dbb8fce176dc383f4129d6c2195a84b4b57555f76e4bdbcd320
- Sealed receipts: 4,992
- Packet: work/evaluations/6948642dbe8440bb946542e8e152cb03

## Verified outcome

The registered run completed. Saved replay verification and assessment
recomputation passed. The assessment is insufficient_evidence.

| Criterion | Actual | Requirement | Outcome |
| --- | --- | --- | --- |
| Completed trades | 15 | At least 30 | Insufficient |
| Specified-cost return | -0.106217072% | At least 0% | Failed |
| Excess return | -0.468111320 percentage points | At least 0 | Failed |
| Marked drawdown | 0.427195753% | At most 5% | Passed |
| Stale decision ticks | 1.747685185% | At most 5% | Passed |
| Unusable regular ticks | 0% | At most 10% | Passed |

The insufficient sample does not erase the failed return criteria.
This attempt does not establish strategy viability.

Report SHA256:
719fc103df5cb226b0d7dd99f7220c512fb61de75f4776f532d999ac91ca98ed

Assessment SHA256:
420b76d34ed018ded884b7669c9894f309201b22fc3a1d5fcdc3163a90d6f430

## Research and governance

The verified assessment and REVISE recommendation were persisted.
Research was reviewed as INCONCLUSIVE / REVISION_REQUIRED.
Existing assessments and recommendations were preserved.

The factory request capital-v3:btc-hypothesis-2 remains awaiting_review.
Its registered-validation gate is pending for insufficient evidence.
Research is not ready and promising; factory creation remains blocked.
No portfolio or experiment was created by this request.

Human approval remains required. Creation, execution, and live-capital
authority remain false.

## Collection cleanup and checks

The expired v3-btc-revision-validation.conf collector drop-in was removed.
Systemd was reloaded and the in-flight collector was confirmed idle.
The market-collector and quote-provenance timers remained active.

Collection was sealed before executing the registered run.
The final health check confirmed completed/sealed state, 4,992 receipts,
current source bindings, and matching receipt checkpoints.

A factory review returned HTTP 200 in 32.16 seconds, exceeding the
diagnostic client's former 30-second timeout. That client timeout was
raised to 180 seconds; verification requirements were unchanged.
The subsequent complete health check passed.

## Limitations and next steps

The saved packet's limitations remain applicable, including sampled
drawdown, stale/carry-forward marks, unmatched benchmark timing and risk,
and unverified historical availability and live execution parity.

This closeout does not extend the window, relax criteria, authorize
another attempt, or promote the strategy. Any further prospective
validation requires a separately reviewed and registered plan.
