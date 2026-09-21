# Capital V3 Session 3 — Portfolio Intelligence

Status: partial implementation, deployed for read-only diagnostics.
Verified on 2026-09-21.

## Delivered

- Consistent PostgreSQL REPEATABLE READ, READ ONLY portfolio snapshots.
- Explicit paper-portfolio identity and eligibility checks.
- Stored-provider selection with observation-age limits.
- Indicative current valuations with explicit missing-price coverage.
- Per-portfolio concentration where valuation coverage is complete.
- Combined concentration withheld when any portfolio is incomplete.
- Shared-holdings detection independent of complete price coverage.
- GET /capital/portfolio-intelligence.
- Dashboard panel below Capital Governance.
- Separately sampled market-regime context.

## Historical calculation components

Implemented and unit tested:

- Recorded transaction effects, including fees and external cash flows.
- Backward balance reconstruction from a current snapshot.
- Completed UTC daily returns with gap and cash-flow exclusions.
- Pairwise correlation on exact shared daily intervals.
- Segmented drawdowns and simultaneous drawdown counts.
- Variance contributions using a common sample and explicit weights.

These components are not connected to an eligible production return series.
The service explicitly reports historical metrics as unavailable.

## Evidence limitations

The market recorder does not supply a provider quote timestamp.
PriceObservation.observed_at therefore defaults to database insertion time
for observations written through that recorder. Recent stored observations
do not establish fresh underlying market quotes.

Portfolio reset deletes transactions and positions without retaining a
reset record. Surviving transactions alone cannot prove full historical
coverage.

Current values remain indicative. They are not automatically accepted by
the eligible daily-return calculation.

Provider selection is explicit and currently covers inspected assets.
New assets require a reviewed provider mapping; no fallback is inferred.

Market regime is separate context. Portfolio regime exposure and
strategy-level regime attribution are not implemented by this service.

## Observed deployment results

- Four existing paper experiment portfolios resolved.
- Three portfolios had complete indicative valuations.
- Primary Portfolio was incomplete because CTVA lacked recent observations.
- CTVA's latest inspected stored observation was dated 2026-09-03.
- Combined concentration was correctly unavailable.
- SPY was shared by Primary Portfolio and both mean-reversion portfolios.
- DIA was shared by both mean-reversion portfolios.
- Historical correlation, drawdown overlap, and risk contribution were
  explicitly unavailable.

These are point-in-time observations, not permanent portfolio properties.

## Verification

- 193 tests passed using:
  PYTHONPATH="$PWD" python -m unittest discover -s tests -p 'test_portfolio_*.py'
- Real database reader and valuation smoke checks succeeded.
- Mounted GET endpoint succeeded against real data.
- Deployed HTTP endpoint succeeded after web-service startup.
- Dashboard rendering inspected in a browser screenshot.
- git diff --check reported no issues.
- BTC collection health check reported 2,035 receipts, with pinned sources
  and checkpoint passing before deployment.
- Factory review remained blocked by research and validation requirements.

Unit tests include mocked database and API checks. They are not a complete
PostgreSQL concurrency or browser automation test suite.

## Authority boundaries

No allocation changes, portfolio creation, experiment activation, or trading
authorization is provided by Portfolio Intelligence.

Live-capital authority remains disabled. Human approval remains required.
Pinned BTC validation sources and collection configuration were not changed.

Only jarvis-core.service was restarted for deployment.

## Remaining work

- Establish suitable price-time provenance and historical coverage evidence.
- Connect eligible daily-return histories to the historical metric engines.
- Verify production sample coverage and risk-weight assumptions.
- Implement portfolio regime exposure with explicit methodology.
- Complete integration and failure testing for those capabilities.

Session 3 is not fully complete. Current exposure diagnostics are deployed;
historical risk reporting remains blocked by evidence and integration gaps.
Agent orchestration and final V3 hardening remain separate later milestones.
