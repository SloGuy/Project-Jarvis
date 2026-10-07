# Capital provider-time validation

## Purpose

Future prospective validations use provider timestamps, local capture
times, and receipt logging times to reconstruct inputs visible before
each decision.

This release adds version-two plans. Existing version-one plans retain
their legacy collection and verification route.

## Behavior

- Freshness limits and input-source fingerprints are registered in advance.
- Collection binds before the validation window.
- Receipts form a chain whose checkpoint is retained in the registry.
- Repeated provider timestamps do not inflate observation history.
- Missing or stale latest quotes do not fall back to older quotes.
- Sealed evidence is required before a single-use claim.
- Saved packets embed the records needed for offline reconstruction.
- Assessment and outcome attachment compare evidence with the registry.
- Workers advance sealed provider collections through evaluation.
- Assessment remains advisory and grants no promotion authority.

The six-day window, 30-trade minimum, costs, benchmark, and numeric
performance criteria retain their existing values.

## Verification

The combined validation suite passed 246 tests in development.

Additional position simulation, signal replay, fresh-confirmation,
persistent confirmation lifecycle, replay manifest, replay analysis,
legacy witness, and observation-witness checks passed.

Integration tests exercise real replay, verification, analysis, and
assessment with temporary evidence and registry storage. External
database connections are blocked during regression runs.

## Release status

Implementation is tested in the development worktree.

A clean checkout must pass regression checks before release acceptance.
Live provider collection and full six-day replay runtime have not yet
been measured for this version.

## Production transition

The active version-one validation requires its original pinned sources.
Preserve its source tree, matching environment, registry, receipts, and
evaluation packets.

Complete and verify its evaluation, outcome attachment, and research
review under that original environment before replacing its sources.

Register future version-two plans only after the release source tree
and collection schedule are finalized. Monitor actual capture cadence,
provider age, missing timestamps, and retained checkpoint consistency.

This development release does not change production services or
authorize live capital.
