"""Registered comparisons use real bindings and receipt storage."""

import copy
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest

import test_cooldown_registration as fixtures

from app.capital import autonomy_trade_research as queue
from app.capital import autonomy_validation_collection as dispatch
from app.capital import research_store
from app.capital import validation_provider_capture as capture
from app.capital import validation_provider_collection as collection
from app.capital import validation_registry as registry
from app.capital.quote_provenance import make_quote_provenance
from app.capital.quote_provenance_store import save_quote_provenance


class CooldownCollectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CooldownRegistrationTests(
            methodName="test_plan_and_contract_are_saved_together"
        )
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.row = self.fixture.register()
        self.plan_id = self.row["plan_id"]
        self.plan = self.row["envelope"]["plan"]
        self.start = datetime.fromisoformat(self.plan["start"])
        self.bound_at = self.start - timedelta(minutes=30)

        self.clock = self.fixture.mock(
            registry, "now_utc", return_value=self.bound_at
        )
        self.fixture.mock(
            collection, "authorize_collection", return_value=None
        )
        self.fixture.mock(
            capture, "authorize_collection", return_value=None
        )
        self.fixture.mock(
            collection, "check_current_binding", return_value=None
        )
        self.fixture.mock(
            capture, "check_current_binding", return_value=None
        )
        self.fixture.mock(dispatch, "_authorize", return_value=None)
        self.fixture.mock(
            dispatch, "read_operating_policy",
            return_value=SimpleNamespace(enabled=True),
        )
        self.provider = self.fixture.mock(
            capture, "capture_plan_quote", side_effect=self.make_quote
        )

    def retained(self):
        return registry.get_plan(self.plan_id)

    def bind(self):
        self.clock.return_value = self.bound_at
        return capture.capture_provider_cycle(self.plan_id)

    def make_quote(self, plan, directory):
        captured_at = self.clock.return_value - timedelta(seconds=1)
        record = make_quote_provenance(
            asset_id=plan["asset_id"],
            symbol="BTC",
            asset_type="crypto",
            provider="CoinGecko REST",
            price_usd="60000",
            provider_timestamp=int(
                (captured_at - timedelta(seconds=10)).timestamp()
            ),
            captured_at=captured_at,
        )
        return save_quote_provenance(
            directory=directory, record=record
        )

    def change_terms(self):
        with registry.locked_state(write=True) as state:
            row = state["plans"][self.plan_id]
            row["cooldown_contract"]["contract"]["scope"] = "changed"

    def change_origin(self):
        with research_store.locked_research_state(write=True) as state:
            request = state[queue.STORE_KEY][self.fixture.request_id]
            request["proposal"]["rationale"] = "Changed after registration."

    def test_valid_comparison_binds_without_fetching(self):
        result = self.bind()
        self.assertEqual(result["status"], "bound")
        retained = self.retained()
        self.assertEqual(
            retained["provider_collection"]["bound_at"],
            self.bound_at.isoformat(),
        )
        self.assertEqual(
            retained["provider_collection"]["store_checkpoint"]["count"],
            0,
        )
        self.provider.assert_not_called()
        self.assertEqual(
            retained["cooldown_contract"],
            self.row["cooldown_contract"],
        )

    def test_valid_capture_appends_one_real_receipt(self):
        self.bind()
        self.clock.return_value = self.bound_at + timedelta(seconds=10)
        result = capture.capture_provider_cycle(self.plan_id)
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["receipt_count"], 1)
        retained = self.retained()
        self.assertEqual(
            retained["provider_collection"]["store_checkpoint"]["count"],
            1,
        )
        self.provider.assert_called_once()
        collection.validate_provider_collection(retained)

    def test_changed_terms_prevent_binding(self):
        self.change_terms()
        before = self.fixture.registry_bytes()
        with self.assertRaises(ValueError):
            self.bind()
        self.assertEqual(self.fixture.registry_bytes(), before)
        self.assertNotIn("provider_collection", self.retained())
        self.provider.assert_not_called()

    def test_missing_terms_prevent_binding(self):
        with registry.locked_state(write=True) as state:
            del state["plans"][self.plan_id]["cooldown_contract"]
        with self.assertRaises(ValueError):
            self.bind()
        self.assertNotIn("provider_collection", self.retained())
        self.provider.assert_not_called()

    def test_changed_origin_prevents_binding(self):
        self.change_origin()
        with self.assertRaises(ValueError):
            self.bind()
        self.assertNotIn("provider_collection", self.retained())
        self.provider.assert_not_called()

    def test_changed_terms_prevent_fetch(self):
        self.bind()
        self.change_terms()
        self.clock.return_value = self.bound_at + timedelta(seconds=10)
        before = self.fixture.registry_bytes()
        with self.assertRaises(ValueError):
            capture.capture_provider_cycle(self.plan_id)
        self.assertEqual(self.fixture.registry_bytes(), before)
        self.provider.assert_not_called()

    def test_origin_change_during_fetch_prevents_append(self):
        self.bind()
        self.clock.return_value = self.bound_at + timedelta(seconds=10)
        before = copy.deepcopy(self.retained()["provider_collection"])

        def fetch(plan, directory):
            saved = self.make_quote(plan, directory)
            self.change_origin()
            return saved

        self.provider.side_effect = fetch
        with self.assertRaises(ValueError):
            capture.capture_provider_cycle(self.plan_id)
        self.assertEqual(
            self.retained()["provider_collection"], before
        )
        self.provider.assert_called_once()

    def test_binding_at_contract_creation_creates_no_store(self):
        self.clock.return_value = datetime.fromisoformat(
            self.row["cooldown_contract"]["contract"]["created_at"]
        )
        with self.assertRaises(ValueError):
            collection.bind_provider_collection(self.plan_id)
        self.assertNotIn("provider_collection", self.retained())
        root = registry.DIRECTORY / "provider_collections"
        self.assertFalse(root.exists())

    def test_dispatch_routes_comparison_to_provider_collector(self):
        result = dispatch._advance(self.plan_id)
        self.assertEqual(result["status"], "bound")
        self.provider.assert_not_called()

    def test_collection_cycle_selects_comparison_owner(self):
        result = dispatch.run_collection_cycle()
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["outcomes"]), 1)
        self.assertEqual(
            result["outcomes"][0]["plan_id"], self.plan_id
        )
        self.assertEqual(result["outcomes"][0]["status"], "bound")
        self.assertFalse(result["live_capital_authorized"])

    def test_dispatch_reports_changed_contract_as_blocked(self):
        self.change_terms()
        result = dispatch.run_collection_cycle()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["outcomes"][0]["error_type"], "ValueError"
        )
        self.provider.assert_not_called()
        self.assertNotIn("provider_collection", self.retained())


if __name__ == "__main__":
    unittest.main()
