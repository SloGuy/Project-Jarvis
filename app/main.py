from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from app.docker_status import get_docker_status
from app.health import get_system_health
from app.update_status import get_update_status
from app.service_status import get_service_status
from app.market_status import get_market_status
from app.market_db.recorder import record_market_snapshot
from app.market_db.history import get_market_history
from app.market_db.moves import get_latest_market_moves
from app.market_db.alerts import get_recent_alerts
from app.market_db.intelligence import get_market_intelligence
from app.market_db.trends import get_asset_trend
from app.market_db.backup_status import get_market_backup_status
from app.market_db.operations import get_market_operations
from app.market_db.news_queries import get_recent_market_news


app = FastAPI(
    title="Jarvis Core",
    version="2.2.0",
    description="Local operations and persistent market intelligence API for Jarvis.",
)


@app.get("/")
def root():
    return {
        "name": "Jarvis Core",
        "version": app.version,
        "status": "online",
        "endpoints": {
            "health": "/health",
            "docker": "/docker",
            "updates": "/updates",
            "overview": "/overview",
            "market": "/market",
            "market_snapshot": "/market/snapshot",
            "market_history": "/market/history",
            "market_moves": "/market/moves",
            "market_alerts": "/market/alerts",
            "market_intelligence": "/market/intelligence",
            "market_dashboard": "/market/dashboard",
            "market_trend": "/market/trend",
            "market_backup_status": "/market/backup-status",
            "market_operations": "/market/operations",
        },
    }


@app.get("/health")
def health():
    return get_system_health()


@app.get("/docker")
def docker():
    return get_docker_status()


@app.get("/updates")
def updates(
    force_refresh: bool = Query(
        default=False,
        description="Bypass the update-status cache and run a fresh check.",
    )
):
    return get_update_status(force_refresh=force_refresh)


@app.get("/services")
def services():
    return get_service_status()


@app.get("/overview")
def overview(
    include_updates: bool = Query(
        default=True,
        description="Include Ubuntu update intelligence in the overview.",
    ),
    force_update_refresh: bool = Query(
        default=False,
        description="Bypass the update-status cache when updates are included.",
    ),
):
    response = {
        "system": get_system_health(),
        "docker": get_docker_status(),
        "services": get_service_status(),
    }

    if include_updates:
        response["updates"] = get_update_status(
            force_refresh=force_update_refresh
        )

    return response



@app.get("/market")
def market():
    return get_market_status()

@app.post("/market/snapshot")
def market_snapshot():
    snapshot = get_market_status()
    storage = record_market_snapshot(snapshot)

    return {
        "status": "success",
        "message": "Market snapshot processed.",
        "market_checked_at": snapshot.get("checked_at"),
        "market_status": snapshot.get("status"),
        "cached_market_data": snapshot.get("cached", False),
        "storage": storage,
    }

@app.get("/market/history")
def market_history(
    symbol: str | None = Query(
        default=None,
        min_length=1,
        max_length=20,
        description="Optional asset symbol, such as BTC, SPY, or AAPL.",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of observations to return.",
    ),
):
    return get_market_history(
        symbol=symbol,
        limit=limit,
    )

@app.get("/market/moves")
def market_moves(
    symbol: str | None = Query(
        default=None,
        min_length=1,
        max_length=20,
        description="Optional asset symbol, such as BTC, SPY, or AAPL.",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of assets to return.",
    ),
    minimum_move_percent: float = Query(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Only return moves at or above this absolute percentage.",
    ),
    comparison_minutes: int = Query(
        default=15,
        ge=1,
        le=1440,
        description="Compare against an observation at least this many minutes older.",
    ),
):
    return get_latest_market_moves(
        symbol=symbol,
        limit=limit,
        minimum_move_percent=minimum_move_percent,
        comparison_minutes=comparison_minutes,
    )

@app.get("/market/alerts")
def market_alerts(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of alerts to return.",
    ),
):
    return get_recent_alerts(limit=limit)

@app.get("/market/news")
def market_news(
    symbol: str | None = Query(
        default=None,
        min_length=1,
        max_length=20,
        description="Optional tracked asset symbol, such as AAPL, SPY, or BTC.",
    ),
    limit: int = Query(
        default=25,
        ge=1,
        le=100,
        description="Maximum number of linked news articles to return.",
    ),
):
    return get_recent_market_news(
        symbol=symbol,
        limit=limit,
    )

@app.get("/market/intelligence")
def market_intelligence(
    comparison_minutes: int = Query(
        default=15,
        ge=1,
        le=1440,
        description="Comparison window used to identify market movers.",
    ),
    mover_threshold_percent: float = Query(
        default=0.25,
        ge=0.0,
        le=100.0,
        description="Minimum absolute move required for the movers section.",
    ),
    alert_limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of recent alerts to include.",
    ),
):
    return get_market_intelligence(
        comparison_minutes=comparison_minutes,
        mover_threshold_percent=mover_threshold_percent,
        alert_limit=alert_limit,
    )

@app.get("/market/dashboard", response_class=HTMLResponse)
def market_dashboard():
    dashboard_path = (
        Path(__file__).resolve().parent
        / "templates"
        / "market_dashboard.html"
    )

    return dashboard_path.read_text(encoding="utf-8")

@app.get("/market/trend")
def market_trend(
    symbol: str = Query(
        ...,
        min_length=1,
        max_length=20,
        description="Tracked asset symbol such as BTC, SPY, or AAPL.",
    ),
    hours: int = Query(
        default=24,
        ge=1,
        le=43800,
        description="Historical lookback window in hours.",
    ),
    limit: int = Query(
        default=5000,
        ge=2,
        le=50000,
        description="Maximum number of chart points to return.",
    ),
    all_history: bool = Query(
        default=False,
        description="Return all stored history for the asset.",
    ),
    chart_points: int = Query(
        default=800,
        ge=50,
        le=5000,
        description="Maximum number of rendered chart points.",
    ),
):
    return get_asset_trend(
        symbol=symbol,
        hours=hours,
        limit=limit,
        all_history=all_history,
        chart_points=chart_points,
    )


@app.get("/market/operations")
def market_operations():
    return get_market_operations()



@app.get("/market/backup-status")
def market_backup_status():
    return get_market_backup_status()
