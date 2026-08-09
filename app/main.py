from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
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
from app.market_db.move_explainer import explain_market_move
from app.market_db.paper_trading import (
    PaperTradingError,
    buy_asset,
    deposit_cash,
    sell_asset,
    withdraw_cash,
)
from app.market_db.portfolio_queries import get_portfolio_summary
from app.market_db.portfolio_insights import get_portfolio_insight
from app.market_db.portfolio_explainer import explain_portfolio
from app.router_api import router as lightweight_router


app = FastAPI(
    title="Jarvis Core",
    version="2.2.0",
    description="Local operations and persistent market intelligence API for Jarvis.",
)

app.include_router(lightweight_router)


from pydantic import BaseModel


class CashRequest(BaseModel):
    amount_usd: float
    portfolio_id: int | None = None
    notes: str | None = None


class TradeRequest(BaseModel):
    symbol: str
    quantity: float
    portfolio_id: int | None = None
    fees_usd: float = 0.0
    notes: str | None = None


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



@app.get("/overview/compact")
def overview_compact(
    include_updates: bool = Query(
        default=True,
        description="Include compact Ubuntu update intelligence.",
    ),
    force_update_refresh: bool = Query(
        default=False,
        description="Bypass the update-status cache when updates are included.",
    ),
):
    system = get_system_health()
    docker = get_docker_status()
    services = get_service_status()

    system_details = system.get("system", {})
    cpu = system.get("cpu", {})
    memory = system.get("memory", {})
    swap = system.get("swap", {})
    disk = system.get("disk", {})
    docker_summary = docker.get("summary", {})
    service_counts = services.get("counts", {})

    response = {
        "status": (
            "healthy"
            if all(
                section.get("status") == "healthy"
                for section in (system, docker, services)
            )
            else "attention"
        ),
        "hostname": system_details.get("hostname"),
        "uptime_seconds": system_details.get("uptime_seconds"),
        "cpu_percent": cpu.get("percent_used"),
        "cpu_temperature_celsius": (
            cpu.get("temperature", {}).get("celsius")
            if cpu.get("temperature", {}).get("available")
            else None
        ),
        "memory_percent": memory.get("percent_used"),
        "swap_percent": swap.get("percent_used"),
        "disk_percent": disk.get("percent_used"),
        "docker": {
            "status": docker.get("status"),
            "running_containers": docker_summary.get("running"),
            "total_containers": docker_summary.get("total_containers"),
        },
        "services": {
            "status": services.get("status"),
            "healthy": service_counts.get("healthy"),
            "monitored": service_counts.get("monitored"),
            "critical_failures": service_counts.get("critical_failures"),
            "warnings": service_counts.get("warnings"),
            "summary": services.get("summary"),
        },
    }

    if include_updates:
        updates = get_update_status(force_refresh=force_update_refresh)
        ubuntu = updates.get("ubuntu", {})
        reboot = updates.get("reboot", {})

        response["updates"] = {
            "status": updates.get("status"),
            "available": ubuntu.get("updates_available"),
            "security": ubuntu.get("security_updates"),
            "regular": ubuntu.get("regular_updates"),
            "reboot_required": reboot.get("required"),
            "summary": updates.get("summary"),
        }

    return response


@app.get("/overview/summary")
def overview_summary(
    include_updates: bool = Query(
        default=True,
        description="Include Ubuntu update intelligence in the summary.",
    ),
    force_update_refresh: bool = Query(
        default=False,
        description="Bypass the update-status cache when updates are included.",
    ),
):
    system = get_system_health()
    docker = get_docker_status()
    services = get_service_status()

    cpu = system.get("cpu", {})
    memory = system.get("memory", {})
    disk = system.get("disk", {})
    docker_summary = docker.get("summary", {})
    service_counts = services.get("counts", {})

    section_statuses = (
        system.get("status"),
        docker.get("status"),
        services.get("status"),
    )

    overall = (
        "Healthy"
        if all(status == "healthy" for status in section_statuses)
        else "Attention required"
    )

    lines = [
        f"Overall: {overall}",
        (
            f"CPU: {cpu.get('percent_used')}% | "
            f"Memory: {memory.get('percent_used')}% | "
            f"Disk: {disk.get('percent_used')}%"
        ),
        (
            f"Docker: {docker_summary.get('running')}/"
            f"{docker_summary.get('total_containers')} running | "
            f"Services: {service_counts.get('healthy')}/"
            f"{service_counts.get('monitored')} healthy"
        ),
    ]

    if include_updates:
        updates = get_update_status(force_refresh=force_update_refresh)
        ubuntu = updates.get("ubuntu", {})
        reboot = updates.get("reboot", {})

        lines.extend(
            [
                (
                    f"Updates: {ubuntu.get('updates_available')} available, "
                    f"{ubuntu.get('security_updates')} security"
                ),
                (
                    "Reboot: Required"
                    if reboot.get("required")
                    else "Reboot: Not required"
                ),
            ]
        )

    return {
        "status": system.get("status"),
        "summary": "\n".join(lines),
    }

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


@app.get("/market/explain")
def market_explain(
    symbol: str = Query(
        ...,
        min_length=1,
        max_length=20,
        description="Tracked asset symbol such as AAPL, TSLA, BTC, or SPY.",
    ),
    comparison_minutes: int = Query(
        default=1440,
        ge=1,
        le=43800,
        description="Price comparison interval in minutes.",
    ),
    news_lookback_hours: int = Query(
        default=72,
        ge=1,
        le=720,
        description="How far back to search for linked news.",
    ),
    news_limit: int = Query(
        default=25,
        ge=1,
        le=100,
        description="Maximum number of linked articles to consider.",
    ),
):
    return explain_market_move(
        symbol=symbol,
        comparison_minutes=comparison_minutes,
        news_lookback_hours=news_lookback_hours,
        news_limit=news_limit,
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

@app.get("/market/portfolio")
def market_portfolio(
    portfolio_id: int | None = Query(default=None),
    transaction_limit: int = Query(default=20, ge=1, le=100),
):
    return get_portfolio_summary(
        portfolio_id=portfolio_id,
        transaction_limit=transaction_limit,
    )


@app.get("/market/portfolio/insight")
def market_portfolio_insight(
    portfolio_id: int | None = Query(default=None),
):
    return get_portfolio_insight(
        portfolio_id=portfolio_id,
    )


@app.get("/market/portfolio/explain")
def market_portfolio_explain(
    portfolio_id: int | None = Query(default=None),
    use_llm: bool = Query(default=False),
):
    return explain_portfolio(
        portfolio_id=portfolio_id,
        use_llm=use_llm,
    )



@app.post("/market/portfolio/deposit")
def market_portfolio_deposit(request: CashRequest):
    try:
        return deposit_cash(
            amount_usd=request.amount_usd,
            portfolio_id=request.portfolio_id,
            notes=request.notes,
        )
    except PaperTradingError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


@app.post("/market/portfolio/withdraw")
def market_portfolio_withdraw(request: CashRequest):
    try:
        return withdraw_cash(
            amount_usd=request.amount_usd,
            portfolio_id=request.portfolio_id,
            notes=request.notes,
        )
    except PaperTradingError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


@app.post("/market/portfolio/buy")
def market_portfolio_buy(request: TradeRequest):
    try:
        return buy_asset(
            symbol=request.symbol,
            quantity=request.quantity,
            portfolio_id=request.portfolio_id,
            fees_usd=request.fees_usd,
            notes=request.notes,
        )
    except PaperTradingError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


@app.post("/market/portfolio/sell")
def market_portfolio_sell(request: TradeRequest):
    try:
        return sell_asset(
            symbol=request.symbol,
            quantity=request.quantity,
            portfolio_id=request.portfolio_id,
            fees_usd=request.fees_usd,
            notes=request.notes,
        )
    except PaperTradingError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
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
