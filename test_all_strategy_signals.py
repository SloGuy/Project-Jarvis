from sqlalchemy import select

from app.autonomous_trading.momentum_strategy import (
    evaluate_momentum_strategy,
)
from app.market_db.database import SessionLocal
from app.market_db.models import MarketAsset


with SessionLocal() as session:
    symbols = session.scalars(
        select(MarketAsset.symbol)
        .where(
            MarketAsset.is_active.is_(True),
        )
        .order_by(MarketAsset.symbol)
    ).all()


print()
print("=== MOMENTUM STRATEGY WATCHLIST ===")
print()

for symbol in symbols:
    candidate = evaluate_momentum_strategy(
        symbol=symbol,
    )

    print(
        f"{candidate.symbol:6} "
        f"{candidate.action.value.upper():4} "
        f"confidence={candidate.confidence_percent}% "
        f"target={candidate.suggested_position_percent}%"
    )

    print(
        "  ",
        candidate.rationale,
    )

print()
print(
    "IMPORTANT: Strategy evaluation only. "
    "No proposals or trades were created."
)
