# Capital V3 — Prospective Accounting Audit

Status: implemented and tested in isolated PostgreSQL schemas.
Not installed in production as of this checkpoint.

## Purpose

Preserve accounting changes prospectively, including rows removed during
portfolio resets. The audit cannot recover previously deleted history.

## Installation design

The migration targets an explicit schema and requires PostgreSQL with psycopg.

In one transaction it:

1. Applies a five-second lock timeout and a sixty-second statement timeout.
2. Acquires ACCESS EXCLUSIVE locks on portfolios, portfolio_positions,
   and portfolio_transactions.
3. Creates separate audit and installation tables.
4. Captures all existing accounting rows as baseline events.
5. Finalizes baseline counts and installs audit triggers.
6. Commits, releasing source-table locks.

Any failure rolls back the installation. Existing audit tables cause refusal;
the migration does not overwrite or upgrade them.

Baseline and trigger coverage include all rows in the three source tables,
not only the portfolios currently registered as experiments.

## Recorded evidence

INSERT, UPDATE, and DELETE events retain before/after row images as JSON text,
source identity, portfolio identities, database transaction ID, and local
database recording time.

JSON financial numbers must be parsed using Decimal.

Audit rows have no foreign keys to source accounting rows, so source deletions
and cascading deletions do not erase the audit.

An audit insert failure fails the accounting statement within its transaction.

## Safeguards and limits

Triggers block source-table TRUNCATE and ordinary modification or deletion of
audit records and installation metadata.

Database owners and administrators can disable or alter these safeguards.
This is operational auditing, not administrator-resistant evidence.

Event sequence IDs and transaction IDs are not commit order.
Recorded timestamps are trigger execution times, not commit times.
A transaction may roll back, and event sequence gaps are expected.

Events preserve changes but do not yet classify reset intent, cash flows,
or historical valuation eligibility.

## Tests completed

With PORTFOLIO_AUDIT_POSTGRES_TEST=1:

- 12 isolated PostgreSQL migration and trigger tests passed.
- 3 migration concurrency tests passed.
- 4 tests using actual accounting models and reset/deposit/withdrawal
  functions passed.

Coverage includes baseline precision, before/after images, reset-style
deletions, cascading deletes, transaction rollback, audit-write failure,
truncation protection, repeat-install refusal, and failed-install rollback.

Concurrency tests exercise writers around installation and simultaneous
installers; they are not a comprehensive production load test.

## Production preflight checkpoint

Database: jarvis_market
Schema: public
Role and accounting-table owner: jarvis
PostgreSQL: 16.14

Estimated source row counts:

- portfolios: 4
- portfolio_positions: 35
- portfolio_transactions: 2,073

No existing audit tables or audit functions were found.
A follow-up check found no other open transactions.
These are point-in-time observations, not guarantees at installation.

## Deployment status

Production installation is deferred until the running BTC validation has
completed its closeout. No production audit triggers have been installed.

After closeout:

- Recheck database state and source integrity.
- Run the controlled migration with --schema public.
- Verify baseline counts and installed triggers.
- Observe an ordinary accounting operation without creating a test trade.
- Record the installation ID and deployment verification.

## Remaining Session 3 work

Install and verify the audit, build evidence readers and continuity checks,
connect qualifying accounting and quote evidence to portfolio returns, and
complete historical risk and portfolio-regime integration.

Audit installation alone does not make historical metrics available.
