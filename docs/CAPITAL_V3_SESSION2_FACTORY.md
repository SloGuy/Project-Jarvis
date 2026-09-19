# Capital V3 — Session 2 paper Experiment Factory

## Implemented
- Validated agent submission contract.
- Durable factory requests with unique request keys and research IDs.
- Retry-safe submission and status lookup.
- Eligibility review using the existing Committee validation gate.
- Current strategy/configuration binding checks.
- Digest-bound operator review packets.
- Local interactive operator confirmation.
- Atomic creation of a planned experiment record and inactive paper portfolio.
- Approval snapshot, evidence references, policy and configuration retained.
- HTTP submission, status and review endpoints.

Factory-created experiments are stored in capital_experiment_factory.
Their definitions are included in approval_snapshot and returned by status.
They are not inserted into the legacy running-experiment registry.

## Authority
Requester attribution is not authenticated identity.
Existing agent approval records do not authorize factory creation.
There is no HTTP approval, creation or activation endpoint.

The local operator command requires a terminal and exact packet-digest
confirmation. Terminal/account access is assumed trusted. This is not
protection against arbitrary code running as that operator.

Creation reverifies the packet after confirmation. Changes require a
fresh confirmation. Confirmation expires after ten minutes.

Created portfolios are paper and inactive.
Created experiments are planned, with execution disabled.
No strategy stage changes, trading activation or live authority are granted.
Activation and runner integration remain a separate controlled step.

## API
POST /capital/experiment-factory
GET /capital/experiment-factory/{request_key}
GET /capital/experiment-factory/{request_key}/review

Submission fields: request_key, research_id, requested_by.
The server selects strategy, policy and validation evidence.
One factory record is allowed per research candidate.

## Operator commands
Review only:
  python -m app.capital.experiment_factory_operator REQUEST_KEY

Interactive creation:
  python -m app.capital.experiment_factory_operator REQUEST_KEY --create

If the result of a creation command is uncertain, inspect request status
before retrying. A created record includes its portfolio and experiment IDs.
Do not delete factory records or manually recreate portfolios.

## Verified deployment
The factory table migration was applied.
The Capital router was mounted and jarvis-core.service restarted.
Live status and eligibility endpoints were checked.

Factory tests: 91 passed, including four isolated PostgreSQL tests.
Validation integration tests: 17 passed.
Concurrent submission, concurrent creation and rollback were tested.
Successful creation tests used isolated fixtures, not real BTC approval.

## Real BTC request
Request: capital-v3:btc-hypothesis-2
Research: research_432466f95cde475da71b62ee60a99477
Status: awaiting_review.
Blocked: research is not ready/promising and validation has not passed.
No portfolio or experiment has been created for this request.

The BTC collector had 443 receipts at the last health check.
Its timer was active; source bindings and checkpoint equality passed.
This is a checkpoint, not a guarantee of future collection health.

## Scope limits
Factory eligibility currently supports Mean Reversion V2 only.
Capital-agent registration and orchestration remain Session 4 work.
Existing running experiments and their portfolios were not replaced.
The September 26 validation closeout remains an operator procedure;
see CAPITAL_V3_BTC_REVISION_VALIDATION.md.
