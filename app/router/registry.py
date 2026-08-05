from app.router.capabilities import (
    AssetCapability,
    MarketCapability,
    OverviewCapability,
)


CAPABILITIES = (
    AssetCapability(),
    OverviewCapability(),
    MarketCapability(),
)


CAPABILITY_BY_NAME = {
    capability.name: capability
    for capability in CAPABILITIES
}
