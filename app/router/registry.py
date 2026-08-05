from app.router.capabilities import (
    AssetCapability,
    MarketCapability,
    OverviewCapability,
    WatchlistCapability,
)


CAPABILITIES = (
    WatchlistCapability(),
    AssetCapability(),
    OverviewCapability(),
    MarketCapability(),
)


CAPABILITY_BY_NAME = {
    capability.name: capability
    for capability in CAPABILITIES
}
