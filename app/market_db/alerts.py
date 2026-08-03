from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.market_db.database import SessionLocal
from app.market_db.models import MarketAlert
from app.market_db.moves import get_latest_market_moves


def _severity(move_percent: float) -> str:
    absolute_move = abs(move_percent)

    if absolute_move >= 3.0:
        return "critical"
    if absolute_move >= 1.5:
        return "high"
    if absolute_move >= 0.75:
        return "medium"

    return "low"


def detect_and_store_alerts(
    comparison_minutes: int = 15,
    threshold_percent: float = 0.75,
    cooldown_minutes: int = 60,
) -> dict:
    analysis = get_latest_market_moves(
        comparison_minutes=comparison_minutes,
        minimum_move_percent=threshold_percent,
    )

    created = []
    skipped_duplicates = 0
    skipped_cooldowns = 0

    cooldown_started_at = (
        datetime.now(timezone.utc)
        - timedelta(minutes=cooldown_minutes)
    )

    with SessionLocal() as session:
        for move in analysis["moves"]:
            move_percent = move["interval_change_percent"]

            observed_at = datetime.fromisoformat(
                move["latest_observed_at"]
            )

            duplicate = session.scalar(
                select(MarketAlert.id).where(
                    MarketAlert.symbol == move["symbol"],
                    MarketAlert.alert_type == "price_move",
                    MarketAlert.observed_at
                    == observed_at,
                    MarketAlert.comparison_minutes
                    == comparison_minutes,
                )
            )

            if duplicate is not None:
                skipped_duplicates += 1
                continue

            direction = move["direction"]
            severity = _severity(move_percent)

            recent_alert = session.scalar(
                select(MarketAlert)
                .where(
                    MarketAlert.symbol == move["symbol"],
                    MarketAlert.alert_type == "price_move",
                    MarketAlert.comparison_minutes
                    == comparison_minutes,
                    MarketAlert.created_at
                    >= cooldown_started_at,
                )
                .order_by(MarketAlert.created_at.desc())
                .limit(1)
            )

            if recent_alert is not None:
                recent_direction = (
                    "up"
                    if recent_alert.move_percent > 0
                    else "down"
                )

                severity_rank = {
                    "low": 1,
                    "medium": 2,
                    "high": 3,
                    "critical": 4,
                }

                same_direction = (
                    recent_direction == direction
                )

                severity_increased = (
                    severity_rank[severity]
                    > severity_rank[recent_alert.severity]
                )

                if same_direction and not severity_increased:
                    skipped_cooldowns += 1
                    continue

            alert = MarketAlert(
                symbol=move["symbol"],
                asset_type=move["asset_type"],
                alert_type="price_move",
                severity=severity,
                message=(
                    f'{move["symbol"]} moved {abs(move_percent):.3f}% '
                    f'{direction} over approximately '
                    f'{comparison_minutes} minutes.'
                ),
                price_usd=Decimal(
                    str(move["latest_price_usd"])
                ),
                move_percent=Decimal(str(move_percent)),
                comparison_minutes=comparison_minutes,
                observed_at=observed_at,
            )

            session.add(alert)

            created.append(
                {
                    "symbol": move["symbol"],
                    "severity": severity,
                    "move_percent": move_percent,
                    "message": alert.message,
                }
            )

        session.commit()

    return {
        "threshold_percent": threshold_percent,
        "comparison_minutes": comparison_minutes,
        "alerts_created": len(created),
        "duplicates_skipped": skipped_duplicates,
        "cooldown_minutes": cooldown_minutes,
        "cooldowns_skipped": skipped_cooldowns,
        "alerts": created,
    }


def get_recent_alerts(limit: int = 100) -> dict:
    with SessionLocal() as session:
        rows = session.scalars(
            select(MarketAlert)
            .order_by(MarketAlert.created_at.desc())
            .limit(limit)
        ).all()

    return {
        "returned": len(rows),
        "alerts": [
            {
                "id": alert.id,
                "symbol": alert.symbol,
                "asset_type": alert.asset_type,
                "alert_type": alert.alert_type,
                "severity": alert.severity,
                "message": alert.message,
                "price_usd": float(alert.price_usd),
                "move_percent": float(alert.move_percent),
                "comparison_minutes": alert.comparison_minutes,
                "observed_at": alert.observed_at.isoformat(),
                "created_at": alert.created_at.isoformat(),
            }
            for alert in rows
        ],
    }
