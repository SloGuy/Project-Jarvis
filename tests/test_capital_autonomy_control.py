"""Isolated tests for persistent Capital operating controls."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.capital.autonomy_control import (
    read_operating_policy,
    update_operating_policy,
)


class CapitalAutonomyControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "controls"

    def read(self):
        return read_operating_policy(directory=self.root)

    def update(self, **settings):
        return update_operating_policy(
            directory=self.root,
            **settings,
        )

    def write_raw(self, raw):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "control.json").write_text(raw, encoding="utf-8")

    def test_missing_controls_are_disabled_without_writes(self):
        policy = self.read()
        self.assertFalse(policy.enabled)
        self.assertFalse(policy.paused)
        self.assertEqual(policy.execution_mode, "paper")
        self.assertFalse(self.root.exists())

    def test_enable_survives_reload(self):
        saved = self.update(enabled=True)
        self.assertEqual(saved, self.read())
        self.assertTrue(self.read().enabled)

    def test_pause_preserves_enabled_setting(self):
        self.update(enabled=True)
        self.update(paused=True)
        policy = self.read()
        self.assertTrue(policy.enabled)
        self.assertTrue(policy.paused)

    def test_disable_preserves_pause(self):
        self.update(enabled=True, paused=True)
        self.update(enabled=False)
        policy = self.read()
        self.assertFalse(policy.enabled)
        self.assertTrue(policy.paused)

    def test_resume_preserves_enabled_setting(self):
        self.update(enabled=True, paused=True)
        self.update(paused=False)
        self.assertTrue(self.read().enabled)
        self.assertFalse(self.read().paused)

    def test_omitted_settings_preserve_existing_values(self):
        original = self.update(enabled=True, paused=True)
        self.assertEqual(self.update(), original)

    def test_invalid_flags_do_not_create_controls(self):
        for field in ("enabled", "paused"):
            for value in (0, 1, "true", [], {}):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        self.update(**{field: value})
        self.assertFalse(self.root.exists())

    def test_corrupt_file_is_not_overwritten(self):
        self.write_raw("{broken")
        with self.assertRaises(ValueError):
            self.update(enabled=True)
        self.assertEqual(
            (self.root / "control.json").read_text(),
            "{broken",
        )

    def test_duplicate_fields_are_rejected(self):
        self.write_raw(
            '{"schema_version":1,"enabled":false,"enabled":true,'
            '"paused":false,"execution_mode":"paper"}'
        )
        with self.assertRaises(ValueError):
            self.read()

    def test_unknown_fields_are_rejected(self):
        self.update()
        path = self.root / "control.json"
        payload = json.loads(path.read_text())
        payload["override_evidence"] = True
        path.write_text(json.dumps(payload))
        with self.assertRaises(ValueError):
            self.read()

    def test_live_mode_is_rejected(self):
        self.write_raw(json.dumps({
            "schema_version": 1,
            "enabled": True,
            "paused": False,
            "execution_mode": "live",
        }))
        with self.assertRaises(ValueError):
            self.read()

    def test_boolean_schema_version_is_rejected(self):
        self.write_raw(json.dumps({
            "schema_version": True,
            "enabled": True,
            "paused": False,
            "execution_mode": "paper",
        }))
        with self.assertRaises(ValueError):
            self.read()

    def test_replace_failure_preserves_previous_controls(self):
        original = self.update(enabled=True)
        with patch(
            "app.capital.autonomy_control.os.replace",
            side_effect=OSError("Replacement failed"),
        ):
            with self.assertRaises(OSError):
                self.update(paused=True)
        self.assertEqual(self.read(), original)
        self.assertEqual(list(self.root.glob(".control-*.tmp")), [])

    def test_read_error_does_not_fall_back_to_enabled(self):
        self.update(enabled=True)
        with patch.object(
            Path,
            "read_text",
            side_effect=PermissionError("Cannot read"),
        ):
            with self.assertRaises(PermissionError):
                self.read()


if __name__ == "__main__":
    unittest.main()
