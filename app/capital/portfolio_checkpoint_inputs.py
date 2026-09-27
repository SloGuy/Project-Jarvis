"""Prepare current valuation inputs from one accounting audit snapshot."""
import json
from decimal import Decimal

from app.capital.portfolio_daily_returns import utc_timestamp


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate row-image field.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite row-image number.")


def _rows(records):
    result = {}
    for record in records:
        image = json.loads(
            record["row_json"],
            parse_float=Decimal,
            parse_constant=_invalid_constant,
            object_pairs_hook=_unique_fields,
        )
        if not isinstance(image, dict):
            raise ValueError("Expected a row-image object.")
        identifier = image.get("id")
        if type(identifier) is not int or identifier <= 0:
            raise ValueError("Invalid row identity.")
        if (
            type(record["source_row_id"]) is not int
            or record["source_row_id"] != identifier
        ):
            raise ValueError("Row identity differs from its image.")
        if identifier in result:
            raise ValueError("Duplicate row identity.")
        result[identifier] = image
    return result


def build_checkpoint_inputs(*, audit_snapshot, resolved_portfolios):
    """Select registered paper portfolios without another database query.

    resolved_portfolios has the existing _resolve_portfolios mapping shape.
    Missing asset metadata remains missing for downstream coverage checks.
    This adapter does not establish return eligibility or audit continuity.
    """
    if audit_snapshot.get("database_snapshot") != "repeatable_read":
        raise ValueError("A consistent audit snapshot is required.")
    if audit_snapshot.get("database_read_only") is not True:
        raise ValueError("A read-only audit snapshot is required.")
    if audit_snapshot.get("asset_metadata_requested") is not True:
        raise ValueError("Asset metadata must be requested in the snapshot.")
    if not isinstance(resolved_portfolios, dict) or not resolved_portfolios:
        raise ValueError("Explicit resolved portfolios are required.")
    if any(
        type(identifier) is not int or identifier <= 0
        for identifier in resolved_portfolios
    ):
        raise ValueError("Portfolio IDs must be positive integers.")

    current = audit_snapshot["current_rows"]
    portfolios = _rows(current["portfolios"])
    positions = _rows(current["portfolio_positions"])
    assets = _rows(audit_snapshot["asset_rows"])
    selected = []

    for identifier, expected in sorted(resolved_portfolios.items()):
        portfolio = portfolios.get(identifier)
        if portfolio is None:
            raise ValueError("Registered portfolio is absent from the snapshot.")
        if (
            portfolio["name"] != expected["portfolio_name"]
            or portfolio["portfolio_type"] != "paper"
            or portfolio["is_active"] is not True
        ):
            raise ValueError("Registered portfolio identity or eligibility changed.")
        selected.append(portfolio)

    selected_positions = []
    held = set()
    for position in positions.values():
        portfolio_id = position["portfolio_id"]
        if type(portfolio_id) is not int or portfolio_id <= 0:
            raise ValueError("Invalid position portfolio identity.")
        if portfolio_id not in resolved_portfolios:
            continue
        asset_id = position["asset_id"]
        if type(asset_id) is not int or asset_id <= 0:
            raise ValueError("Invalid position asset identity.")
        quantity = position["quantity"]
        if isinstance(quantity, bool):
            raise ValueError("Invalid position quantity.")
        quantity = Decimal(str(quantity))
        if not quantity.is_finite() or quantity < 0:
            raise ValueError("Invalid position quantity.")
        selected_positions.append(position)
        if quantity > 0:
            held.add(asset_id)

    selected_assets = []
    for asset_id in sorted(held):
        asset = assets.get(asset_id)
        if asset is None:
            continue
        # Inactive assets can still have holdings. Preserve their metadata;
        # activity is not a substitute for quote eligibility.
        selected_assets.append(asset)

    return {
        "snapshot_at": utc_timestamp(
            audit_snapshot["sampled_at"]
        ).isoformat(),
        "portfolios": selected,
        "positions": selected_positions,
        "assets": selected_assets,
        "observations": [],
        "transactions": [],
        "audit_installation_id": (
            audit_snapshot["installation"]["installation_id"]
        ),
        "audit_visibility_snapshot": audit_snapshot["visibility_snapshot"],
        "database_snapshot": "repeatable_read",
        "database_read_only": True,
        "historical_completeness_verified": False,
        "execution_authorized": False,
    }
