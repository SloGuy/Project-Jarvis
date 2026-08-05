from app.router.capabilities import (
    AssetCapability,
    MarketCapability,
    NewsCapability,
    OverviewCapability,
    WatchlistCapability,
    PortfolioCapability,
)


CAPABILITIES = (
    WatchlistCapability(),
    NewsCapability(),
    PortfolioCapability(),
    AssetCapability(),
    OverviewCapability(),
    MarketCapability(),
)


CAPABILITY_BY_NAME = {
    capability.name: capability
    for capability in CAPABILITIES
}
