# Capital V3 — Prospective Quote Provenance

Status: separate prospective collector deployed on 2026-09-25.

## Purpose

Preserve provider-reported quote time separately from local response-processing
time. Existing PriceObservation rows are not rewritten or reclassified.

This addresses one input-evidence gap in Portfolio Intelligence. It does not
complete historical portfolio accounting or make Session 3 complete.

## Records and storage

Records contain asset identity, provider, price, original provider timestamp,
normalized provider time, and local capture time.

Missing provider timestamps remain missing. Future provider timestamps and
invalid identities or prices are rejected.

Files are stored under:

runtime/capital/quote_provenance/

Content-derived filenames, atomic hard-link publication, file fsync, and
directory fsync provide local integrity and publication checks. Identical
records are verified rather than overwritten.

Hashes do not establish authenticity, first persistence time, or completeness.
The store is not a signed event log. Coverage scans are not transactional.

## Collection scope

The collector resolves registered paper experiment portfolios and selects
their unique positive holdings.

- Shared holdings are collected once per cycle.
- Crypto identities must match the fetcher's explicit provider-ID mapping.
- Crypto assets use one CoinGecko batch request.
- Stocks use individual Finnhub REST requests.
- Unsupported holdings and failed captures are reported explicitly.
- Holdings may change after the cycle's initial snapshot.

The collector reuses individual REST fetchers, not the live-cache fallback.

## Scheduling

Service: jarvis-quote-provenance.service
Timer: jarvis-quote-provenance.timer

The timer runs 60 seconds after the previous service invocation finishes.
A nonblocking process lock prevents overlapping scheduled-runner cycles.

Runner exit codes:

- 0: captured all selected assets, or no holdings.
- 1: partial capture or failure.
- 2: another cycle holds the lock.

Successful capture does not imply timestamp eligibility.
Partial publication may remain after a storage failure.

The one-shot per-asset operator is a separate diagnostic command and does not
participate in the scheduled runner's cycle lock.

## Eligibility and coverage

Current diagnostic checks use:

- Maximum provider age: 20 minutes.
- Maximum local capture age: 2 minutes.
- No captures after the measurement time.
- Missing provider timestamps are ineligible.
- Conflicting latest captures are ambiguous.
- No fallback to an older eligible capture when the latest is ineligible.

These are elapsed-time checks without exchange-calendar treatment.
An old stock quote remains old even if captured recently.

## Verification

177 provenance tests passed before service installation.

Real checks confirmed:

- CTVA preserved provider time 20:00 UTC separately from later local capture.
- CTVA remained ineligible because the provider quote was too old.
- A BTC capture passed saved-file integrity and timestamp eligibility.
- A full cycle captured six unique held assets using one crypto batch and
  one stock request.
- The installed service completed successfully and the timer scheduled
  subsequent runs.
- A coverage scan verified 38 records: five crypto holdings eligible,
  CTVA ineligible, and no unsupported holdings.

These counts and holdings describe a deployment checkpoint, not fixed state.

At the same checkpoint, BTC validation had 4,875 receipts, with pinned sources
and checkpoint passing. Factory review remained correctly blocked.

## Boundaries

No writes to the trading database, legacy observations, or validation receipts.
No experiment activation, allocation change, or live-capital authority.

Existing BTC validation records and pinned sources remain separate.

Provenance eligibility does not establish complete ledger history, provider
authenticity, or permission to trade.

## Remaining work

- Preserve portfolio accounting history prospectively.
- Record collection failures and coverage over time durably.
- Address store growth and coverage-scan cost before extended operation.
- Define session-aware valuation rules where appropriate.
- Connect qualifying provenance and accounting evidence to portfolio returns.
- Complete historical risk and portfolio-regime integration.

## Operations

Inspect recent runs:

sudo journalctl -u jarvis-quote-provenance.service -n 100 --no-pager

Inspect schedule:

systemctl list-timers --all --no-pager jarvis-quote-provenance.timer

To disable future collection:

sudo systemctl disable --now jarvis-quote-provenance.timer

Disabling the timer does not stop an already-running service invocation.
Preserve existing evidence files when stopping collection.
