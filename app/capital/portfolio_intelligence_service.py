"""Read-only portfolio intelligence with explicit evidence limitations."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text

from app.capital.portfolio_concentration import analyze_concentration
from app.capital.portfolio_intelligence_reader import read_portfolio_inputs
from app.capital.portfolio_provenance_valuation import value_provenance_snapshot


PROVENANCE_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "runtime"
    / "capital"
    / "quote_provenance"
)

PROVIDERS = {
    ("stock", "SPY"): "Finnhub REST",
    ("stock", "QQQ"): "Finnhub REST",
    ("stock", "DIA"): "Finnhub REST",
    ("stock", "TSLA"): "Finnhub REST",
    ("stock", "AAPL"): "Finnhub REST",
    ("stock", "NVDA"): "Finnhub REST",
    ("stock", "CHTR"): "Finnhub REST",
    ("stock", "CTVA"): "Finnhub REST",
    ("crypto", "BTC"): "CoinGecko REST",
    ("crypto", "ETH"): "CoinGecko REST",
    ("crypto", "XMR"): "CoinGecko REST",
    ("crypto", "XRP"): "CoinGecko REST",
    ("crypto", "SOL"): "CoinGecko REST",
}


def _resolve_portfolios():
    from app.capital.experiment_registry import list_experiments
    from app.market_db.database import engine
    from app.market_db.models import Portfolio

    experiments = list_experiments()
    names = [experiment.portfolio_name for experiment in experiments]
    if not names or len(names) != len(set(names)):
        raise ValueError("Experiment portfolio names must be nonempty and unique.")

    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = connection.execute(
                select(
                    Portfolio.id,
                    Portfolio.name,
                    Portfolio.portfolio_type,
                ).where(
                    Portfolio.name.in_(names),
                    Portfolio.is_active.is_(True),
                )
            ).mappings().all()

    resolved = {}
    for experiment in experiments:
        matches = [
            row for row in rows
            if row["name"] == experiment.portfolio_name
        ]
        if len(matches) != 1 or matches[0]["portfolio_type"] != "paper":
            raise ValueError(
                "Each experiment must resolve to exactly one active "
                "paper portfolio."
            )
        resolved[matches[0]["id"]] = {
            "experiment_id": experiment.experiment_id,
            "strategy_name": experiment.strategy_name,
            "portfolio_name": experiment.portfolio_name,
        }
    return resolved


def _market_context():
    from app.capital.market_regime import get_market_regime

    return get_market_regime()


def get_portfolio_intelligence():
    from app.capital.portfolio_sampled_status import get_sampled_status
    resolved = _resolve_portfolios()
    snapshot = read_portfolio_inputs(
        portfolio_ids=sorted(resolved),
        quote_window_start=datetime.now(timezone.utc),
    )

    actual_ids = [row["id"] for row in snapshot["portfolios"]]
    if (
        len(actual_ids) != len(set(actual_ids))
        or set(actual_ids) != set(resolved)
    ):
        raise ValueError("Portfolio snapshot does not match the requested set.")

    for row in snapshot["portfolios"]:
        expected = resolved[row["id"]]
        if (
            row["name"] != expected["portfolio_name"]
            or row["portfolio_type"] != "paper"
            or row["is_active"] is not True
        ):
            raise ValueError("Portfolio identity or eligibility changed.")

    provider_by_asset = {
        asset["id"]: PROVIDERS[(asset["asset_type"], asset["symbol"])]
        for asset in snapshot["assets"]
        if (asset["asset_type"], asset["symbol"]) in PROVIDERS
    }
    valuations = value_provenance_snapshot(
        snapshot=snapshot,
        directory=PROVENANCE_DIRECTORY,
        provider_by_asset=provider_by_asset,
        maximum_provider_age=timedelta(minutes=20),
        maximum_capture_age=timedelta(minutes=2),
    )
    concentration = analyze_concentration(valuations)

    for row in valuations["portfolios"]:
        row.update(resolved[row["portfolio_id"]])
    for row in concentration["portfolios"]:
        row.update(resolved[row["portfolio_id"]])

    blockers = [
        "Current provenance valuations do not establish verified daily boundaries.",
        "Complete accounting and external-flow coverage for return intervals "
        "has not been established by this service.",
        "No eligible verified daily-return series is supplied by this service.",
    ]

    return {
        "schema_version": 1,
        "status": "partial",
        "snapshot_at": snapshot["snapshot_at"],
        "scope": "registered_experiment_paper_portfolios",
        "valuations": valuations,
        "concentration": concentration,
        "sampled_history": get_sampled_status(resolved_portfolios=resolved),
        "historical_metrics": {
            name: {
                "status": "unavailable",
                "reasons": list(blockers),
            }
            for name in (
                "correlation",
                "drawdown_overlap",
                "risk_contribution",
            )
        },
        "market_context": {
            "scope": "market_regime_not_portfolio_regime_exposure",
            "same_database_snapshot": False,
            "sampled_at": datetime.now(timezone.utc).isoformat(),
            "report": _market_context(),
        },
        "limitations": [
            "Current values are indicative saved-provenance estimates.",
            "Provider and capture ages are checked without exchange calendars.",
            "Filesystem quotes and database balances are not one transaction.",
            "Combined concentration requires complete values for all portfolios.",
            "Provider coverage is explicit; unknown assets remain uncovered.",
            "Historical metric engines exist but current evidence is ineligible.",
            "Market regime is separate context, not strategy regime attribution.",
        ],
        "database_writes": False,
        "execution_authorized": False,
        "allocation_authority": False,
        "live_capital_authority": False,
        "human_approval_required": True,
    }
