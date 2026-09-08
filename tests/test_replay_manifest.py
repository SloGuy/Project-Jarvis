import copy
from decimal import localcontext
import unittest

from app.capital.replay_manifest import (
    capture_replay_manifest, verify_replay_manifest,
)


class ManifestTests(unittest.TestCase):
    def test_current_configuration_matches(self):
        verify_replay_manifest(capture_replay_manifest())

    def test_changed_constant_rejected(self):
        saved = copy.deepcopy(capture_replay_manifest())
        saved["required_confirmations"] += 1
        with self.assertRaisesRegex(ValueError, "configuration mismatch"):
            verify_replay_manifest(saved)

    def test_decimal_precision_change_rejected(self):
        saved = capture_replay_manifest()
        with localcontext() as context:
            context.prec += 1
            with self.assertRaisesRegex(ValueError, "decimal_context"):
                verify_replay_manifest(saved)

    def test_source_change_rejected(self):
        saved = copy.deepcopy(capture_replay_manifest())
        name = next(iter(saved["source_sha256"]))
        saved["source_sha256"][name] = "changed"
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            verify_replay_manifest(saved)


if __name__ == "__main__":
    unittest.main()
