from app.router.capabilities import (
    MarketCapability,
    OverviewCapability,
)


CAPABILITIES = (
    OverviewCapability(),
    MarketCapability(),
)


CAPABILITY_BY_NAME = {
    capability.name: capability
    for capability in CAPABILITIES
}
