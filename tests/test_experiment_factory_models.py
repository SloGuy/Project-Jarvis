"""Factory request contract tests; no database or runtime state."""
import unittest

from pydantic import ValidationError

from app.capital.experiment_factory_models import ExperimentFactoryRequest


class ExperimentFactoryRequestTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "request_key": "research-agent:request-1",
            "research_id": "research_test",
            "requested_by": "research-agent",
        }

    def test_request_round_trip(self):
        request = ExperimentFactoryRequest(**self.data)
        self.assertEqual(request.model_dump(), self.data)
        restored = ExperimentFactoryRequest.model_validate_json(
            request.model_dump_json()
        )
        self.assertEqual(restored, request)

    def test_authority_and_configuration_fields_are_rejected(self):
        for field, value in (
            ("approved", True),
            ("approved_by", "operator"),
            ("execution_mode", "live"),
            ("strategy_name", "arbitrary_strategy"),
            ("policy", {}),
            ("validation_plan_id", "selected_good_result"),
            ("portfolio_id", 1),
            ("starting_capital_usd", 1000000),
            ("status", "created"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    ExperimentFactoryRequest(**self.data, **{field: value})

    def test_missing_fields_are_rejected(self):
        for field in self.data:
            with self.subTest(field=field):
                values = dict(self.data)
                del values[field]
                with self.assertRaises(ValidationError):
                    ExperimentFactoryRequest(**values)

    def test_invalid_values_are_rejected(self):
        for field, value in (
            ("request_key", ""),
            ("request_key", "../request"),
            ("request_key", "x" * 101),
            ("research_id", "unknown"),
            ("research_id", "research_"),
            ("research_id", "research_" + "x" * 100),
            ("requested_by", "   "),
            ("requested_by", "x" * 121),
            ("requested_by", 123),
        ):
            with self.subTest(field=field, value=value):
                values = {**self.data, field: value}
                with self.assertRaises(ValidationError):
                    ExperimentFactoryRequest(**values)

    def test_whitespace_is_normalized(self):
        request = ExperimentFactoryRequest(**{
            key: f"  {value}  " for key, value in self.data.items()
        })
        self.assertEqual(request.model_dump(), self.data)

    def test_request_is_immutable(self):
        request = ExperimentFactoryRequest(**self.data)
        with self.assertRaises(ValidationError):
            request.research_id = "research_other"


if __name__ == "__main__":
    unittest.main()
