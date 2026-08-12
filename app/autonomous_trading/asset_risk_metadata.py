from dataclasses import dataclass


@dataclass(frozen=True)
class AssetRiskMetadata:
    symbol: str
    sector: str
    correlation_group: str


ASSET_RISK_METADATA = {
    "AAPL": AssetRiskMetadata(
        symbol="AAPL",
        sector="technology",
        correlation_group="us_growth_equity",
    ),
    "TSLA": AssetRiskMetadata(
        symbol="TSLA",
        sector="consumer_cyclical",
        correlation_group="us_growth_equity",
    ),
    "QQQ": AssetRiskMetadata(
        symbol="QQQ",
        sector="diversified_etf",
        correlation_group="us_growth_equity",
    ),
    "SPY": AssetRiskMetadata(
        symbol="SPY",
        sector="diversified_etf",
        correlation_group="us_broad_equity",
    ),
    "DIA": AssetRiskMetadata(
        symbol="DIA",
        sector="diversified_etf",
        correlation_group="us_broad_equity",
    ),
    "BTC": AssetRiskMetadata(
        symbol="BTC",
        sector="crypto",
        correlation_group="crypto_market",
    ),
    "ETH": AssetRiskMetadata(
        symbol="ETH",
        sector="crypto",
        correlation_group="crypto_market",
    ),
    "XRP": AssetRiskMetadata(
        symbol="XRP",
        sector="crypto",
        correlation_group="crypto_market",
    ),
    "XMR": AssetRiskMetadata(
        symbol="XMR",
        sector="crypto",
        correlation_group="crypto_market",
    ),
}


def get_asset_risk_metadata(
    symbol: str,
) -> AssetRiskMetadata | None:
    return ASSET_RISK_METADATA.get(
        symbol.strip().upper()
    )
