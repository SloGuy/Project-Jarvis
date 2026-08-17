from dataclasses import dataclass
from app.market_universe_sources.stocks import (
    get_broad_stock_candidates,
)


@dataclass(frozen=True)
class MarketUniverseAsset:
    symbol: str
    name: str
    asset_type: str
    provider_id: str

    tracking_tier: str = "deep"

    live_enabled: bool = True
    snapshot_enabled: bool = True
    history_enabled: bool = True
    news_enabled: bool = True
    intelligence_enabled: bool = True

    autonomous_trading_allowed: bool = False

    aliases: tuple[str, ...] = ()
    news_terms: tuple[str, ...] = ()


def broad_market_asset(
    *,
    symbol: str,
    name: str,
    asset_type: str,
    provider_id: str,
    aliases: tuple[str, ...] = (),
    news_terms: tuple[str, ...] = (),
) -> MarketUniverseAsset:
    return MarketUniverseAsset(
        symbol=symbol,
        name=name,
        asset_type=asset_type,
        provider_id=provider_id,
        tracking_tier="broad",
        live_enabled=False,
        snapshot_enabled=True,
        history_enabled=False,
        news_enabled=False,
        intelligence_enabled=True,
        autonomous_trading_allowed=False,
        aliases=aliases,
        news_terms=news_terms,
    )


MARKET_UNIVERSE = (
    MarketUniverseAsset(
        symbol="SPY",
        name="SPDR S&P 500 ETF",
        asset_type="stock",
        provider_id="SPY",
        autonomous_trading_allowed=True,
        aliases=(
            "spy",
            "s&p 500",
            "s&p500",
        ),
        news_terms=(
            "S&P 500",
            "SPDR S&P 500 ETF",
        ),
    ),
    MarketUniverseAsset(
        symbol="QQQ",
        name="Invesco QQQ",
        asset_type="stock",
        provider_id="QQQ",
        autonomous_trading_allowed=True,
        aliases=(
            "qqq",
            "nasdaq 100",
            "nasdaq-100",
        ),
        news_terms=(
            "Nasdaq 100",
            "Nasdaq-100",
            "Invesco QQQ",
        ),
    ),
    MarketUniverseAsset(
        symbol="DIA",
        name="SPDR Dow Jones ETF",
        asset_type="stock",
        provider_id="DIA",
        autonomous_trading_allowed=True,
        aliases=(
            "dia",
            "dow jones",
            "dow",
        ),
        news_terms=(
            "Dow Jones",
            "Dow Jones Industrial Average",
        ),
    ),
    MarketUniverseAsset(
        symbol="TSLA",
        name="Tesla",
        asset_type="stock",
        provider_id="TSLA",
        autonomous_trading_allowed=True,
        aliases=(
            "tsla",
            "tesla",
        ),
        news_terms=(
            "Tesla",
            "Tesla Inc",
        ),
    ),
    MarketUniverseAsset(
        symbol="AAPL",
        name="Apple",
        asset_type="stock",
        provider_id="AAPL",
        autonomous_trading_allowed=True,
        aliases=(
            "aapl",
            "apple",
        ),
        news_terms=(
            "Apple",
            "Apple Inc",
        ),
    ),
    MarketUniverseAsset(
        symbol="NVDA",
        name="NVIDIA",
        asset_type="stock",
        provider_id="NVDA",
        autonomous_trading_allowed=False,
        aliases=(
            "nvda",
            "nvidia",
        ),
        news_terms=(
            "NVIDIA",
            "Nvidia",
            "NVIDIA Corporation",
        ),
    ),
    MarketUniverseAsset(
        symbol="BTC",
        name="Bitcoin",
        asset_type="crypto",
        provider_id="bitcoin",
        autonomous_trading_allowed=True,
        aliases=(
            "btc",
            "bitcoin",
        ),
        news_terms=(
            "Bitcoin",
        ),
    ),
    MarketUniverseAsset(
        symbol="ETH",
        name="Ethereum",
        asset_type="crypto",
        provider_id="ethereum",
        autonomous_trading_allowed=True,
        aliases=(
            "eth",
            "ethereum",
        ),
        news_terms=(
            "Ethereum",
            "Ether",
        ),
    ),
    MarketUniverseAsset(
        symbol="XMR",
        name="Monero",
        asset_type="crypto",
        provider_id="monero",
        autonomous_trading_allowed=True,
        aliases=(
            "xmr",
            "monero",
        ),
        news_terms=(
            "Monero",
        ),
    ),
    MarketUniverseAsset(
        symbol="XRP",
        name="XRP",
        asset_type="crypto",
        provider_id="ripple",
        autonomous_trading_allowed=True,
        aliases=(
            "xrp",
            "ripple",
        ),
        news_terms=(
            "Ripple",
            "Ripple Labs",
        ),
    ),
    broad_market_asset(
        symbol="SOL",
        name="Solana",
        asset_type="crypto",
        provider_id="solana",
        aliases=(
            "sol",
            "solana",
        ),
    ),
) + tuple(
    broad_market_asset(
        symbol=asset.symbol,
        name=asset.name,
        asset_type="stock",
        provider_id=asset.symbol,
        aliases=(
            asset.symbol.lower(),
            asset.name.lower(),
        ),
    )
    for asset in get_broad_stock_candidates(
        use_provider=True,
    )
    if asset.symbol not in {
        "SPY",
        "QQQ",
        "DIA",
        "TSLA",
        "AAPL",
        "NVDA",
    }
)


ASSET_BY_SYMBOL = {
    asset.symbol: asset
    for asset in MARKET_UNIVERSE
}


def get_market_assets(
    *,
    asset_type: str | None = None,
    tracking_tier: str | None = None,
) -> tuple[MarketUniverseAsset, ...]:
    assets = MARKET_UNIVERSE

    if asset_type is not None:
        assets = tuple(
            asset
            for asset in assets
            if asset.asset_type == asset_type
        )

    if tracking_tier is not None:
        assets = tuple(
            asset
            for asset in assets
            if asset.tracking_tier == tracking_tier
        )

    return tuple(assets)


def get_snapshot_stock_symbols() -> tuple[str, ...]:
    return tuple(
        asset.symbol
        for asset in MARKET_UNIVERSE
        if (
            asset.asset_type == "stock"
            and asset.snapshot_enabled
        )
    )


def get_deep_snapshot_stock_symbols() -> tuple[str, ...]:
    return tuple(
        asset.symbol
        for asset in MARKET_UNIVERSE
        if (
            asset.asset_type == "stock"
            and asset.tracking_tier == "deep"
            and asset.snapshot_enabled
        )
    )


def get_live_stock_symbols() -> tuple[str, ...]:
    return tuple(
        asset.symbol
        for asset in MARKET_UNIVERSE
        if (
            asset.asset_type == "stock"
            and asset.live_enabled
        )
    )


def get_crypto_provider_map() -> dict[str, str]:
    return {
        asset.provider_id: asset.symbol
        for asset in MARKET_UNIVERSE
        if (
            asset.asset_type == "crypto"
            and asset.snapshot_enabled
        )
    }


def get_historical_crypto_provider_map() -> dict[str, str]:
    return {
        asset.provider_id: asset.symbol
        for asset in MARKET_UNIVERSE
        if (
            asset.asset_type == "crypto"
            and asset.history_enabled
        )
    }


def get_news_aliases() -> dict[str, list[str]]:
    return {
        asset.symbol: list(asset.news_terms)
        for asset in MARKET_UNIVERSE
        if asset.news_enabled
    }


def get_asset_aliases() -> dict[
    str,
    tuple[str, ...],
]:
    return {
        asset.symbol: asset.aliases
        for asset in MARKET_UNIVERSE
    }


def get_asset_names() -> dict[str, str]:
    return {
        asset.symbol: asset.name
        for asset in MARKET_UNIVERSE
    }


def get_historical_stock_symbols() -> tuple[str, ...]:
    return tuple(
        asset.symbol
        for asset in MARKET_UNIVERSE
        if (
            asset.asset_type == "stock"
            and asset.history_enabled
        )
    )


def get_autonomous_trading_symbols() -> tuple[str, ...]:
    return tuple(
        asset.symbol
        for asset in MARKET_UNIVERSE
        if asset.autonomous_trading_allowed
    )


def get_market_universe_snapshot() -> dict:
    """
    Return the current Jarvis market universe in a
    JSON-serializable structure.

    This describes tracking and trading permissions.
    It does not fetch market data.
    """

    assets = []

    for asset in MARKET_UNIVERSE:
        assets.append(
            {
                "symbol": asset.symbol,
                "name": asset.name,
                "asset_type": asset.asset_type,
                "provider_id": asset.provider_id,
                "tracking_tier": asset.tracking_tier,
                "live_enabled": asset.live_enabled,
                "snapshot_enabled": (
                    asset.snapshot_enabled
                ),
                "history_enabled": (
                    asset.history_enabled
                ),
                "news_enabled": asset.news_enabled,
                "intelligence_enabled": (
                    asset.intelligence_enabled
                ),
                "autonomous_trading_allowed": (
                    asset.autonomous_trading_allowed
                ),
                "aliases": list(asset.aliases),
                "news_terms": list(
                    asset.news_terms
                ),
            }
        )

    stocks = [
        asset["symbol"]
        for asset in assets
        if asset["asset_type"] == "stock"
    ]

    crypto = [
        asset["symbol"]
        for asset in assets
        if asset["asset_type"] == "crypto"
    ]

    deep = [
        asset["symbol"]
        for asset in assets
        if asset["tracking_tier"] == "deep"
    ]

    return {
        "status": "success",
        "asset_count": len(assets),
        "assets": assets,
        "groups": {
            "stocks": stocks,
            "crypto": crypto,
            "deep": deep,
            "live": list(
                get_live_stock_symbols()
            ),
            "snapshot_stocks": list(
                get_snapshot_stock_symbols()
            ),
            "historical_stocks": list(
                get_historical_stock_symbols()
            ),
            "autonomous_trading": list(
                get_autonomous_trading_symbols()
            ),
        },
    }
