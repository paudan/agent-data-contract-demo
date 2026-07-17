import unittest
import asyncio
import json
import os
import shutil
import sys
import tempfile

# Ensure parent directory is in path so we can import the agents and simulation modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from simulation import SimulationCoordinator, STRATEGY_MAP, EXPECTED_SUCCESS

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


class TestAgentContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.coordinator = SimulationCoordinator(num_calls=5, delay_factor=0.0, stream=False)

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_compatible_start(self):
        """Test case where data is completely compatible at the start."""
        data = [
            {"timestamp": "2026-07-15T12:00:00Z", "store_id": 1, "product_id": 101, "price": 19.99, "on_promotion": True, "historical_sales": 150.0},
            {"timestamp": "2026-07-15T13:00:00Z", "store_id": 1, "product_id": 102, "price": 5.49, "on_promotion": False, "historical_sales": 320.0}
        ]
        success, calls, response = self.run_async(
            self.coordinator.run_scenario("Test Compatible", data, "standard")
        )
        self.assertTrue(success)
        self.assertEqual(calls, 1)
        self.assertEqual(response["status"], "success")

        # Verify forecasts
        for record in response["data"]:
            self.assertEqual(record["forecast"], 1.0)

    def test_missing_fields_all(self):
        """Test case where there are missing fields in all of the provided data."""
        data = [
            {"timestamp": "2026-07-15T12:00:00Z", "product_id": 101, "price": 19.99, "on_promotion": True, "historical_sales": 150.0},
            {"timestamp": "2026-07-15T13:00:00Z", "product_id": 102, "price": 5.49, "on_promotion": False, "historical_sales": 320.0}
        ]
        success, calls, response = self.run_async(
            self.coordinator.run_scenario("Test Missing All", data, "standard")
        )
        self.assertTrue(success)
        self.assertEqual(calls, 2)
        self.assertEqual(response["status"], "success")

    def test_missing_fields_some(self):
        """Test case where there are missing fields in some of the records."""
        data = [
            {"timestamp": "2026-07-15T12:00:00Z", "store_id": 1, "product_id": 101, "price": 19.99, "on_promotion": True, "historical_sales": 150.0},
            {"timestamp": "2026-07-15T13:00:00Z", "store_id": 1, "price": 5.49, "on_promotion": False, "historical_sales": 320.0}
        ]
        success, calls, response = self.run_async(
            self.coordinator.run_scenario("Test Missing Some", data, "standard")
        )
        self.assertTrue(success)
        self.assertEqual(calls, 2)
        self.assertEqual(response["status"], "success")

    def test_redundant_fields(self):
        """Test case where there are redundant fields which are not present in data schema."""
        data = [
            {"timestamp": "2026-07-15T12:00:00Z", "store_id": 1, "product_id": 101, "price": 19.99, "on_promotion": True, "historical_sales": 150.0, "redundant_feature": "abc"},
            {"timestamp": "2026-07-15T13:00:00Z", "store_id": 1, "product_id": 102, "price": 5.49, "on_promotion": False, "historical_sales": 320.0, "another_redundant": 123}
        ]
        success, calls, response = self.run_async(
            self.coordinator.run_scenario("Test Redundant", data, "standard")
        )
        self.assertTrue(success)
        self.assertEqual(calls, 2)
        self.assertEqual(response["status"], "success")

    def test_invalid_types(self):
        """Test case where the values are not of valid type."""
        data = [
            {"timestamp": "2026-07-15T12:00:00Z", "store_id": "1", "product_id": 101, "price": "19.99", "on_promotion": "true", "historical_sales": 150.0},
            {"timestamp": "2026-07-15T13:00:00Z", "store_id": 1, "product_id": 102, "price": 5.49, "on_promotion": False, "historical_sales": 320.0}
        ]
        success, calls, response = self.run_async(
            self.coordinator.run_scenario("Test Invalid Types", data, "standard")
        )
        self.assertTrue(success)
        self.assertEqual(calls, 2)
        self.assertEqual(response["status"], "success")

    def test_nan_values(self):
        """Test case where some of the values are NaN (missing)."""
        data = [
            {"timestamp": "2026-07-15T12:00:00Z", "store_id": 1, "product_id": 101, "price": "NaN", "on_promotion": True, "historical_sales": 150.0},
            {"timestamp": "2026-07-15T13:00:00Z", "store_id": 1, "product_id": 102, "price": None, "on_promotion": False, "historical_sales": 320.0}
        ]
        success, calls, response = self.run_async(
            self.coordinator.run_scenario("Test NaN", data, "standard")
        )
        self.assertTrue(success)
        self.assertEqual(calls, 2)
        self.assertEqual(response["status"], "success")

    def test_supplier_fails_to_fix_errors_continuously_but_makes_it(self):
        """Test case where errors are gradually fixed and passes on the limit (NUM_CALLS = 5)."""
        gradual_fix_data = [
            {
                "timestamp": "2026-07-15T12:00:00Z",
                "store_id": "1",
                "price": -10.0,
                "on_promotion": True,
                "historical_sales": 150.0,
                "redundant_feature": "abc",
            },
            {"timestamp": "2026-07-15T13:00:00Z", "store_id": 1, "product_id": 102, "price": 5.49, "on_promotion": False, "historical_sales": 320.0}
        ]
        success, calls, response = self.run_async(
            self.coordinator.run_scenario("Test Gradual Fix", gradual_fix_data, "gradual")
        )
        self.assertTrue(success)
        self.assertEqual(calls, 5)
        self.assertEqual(response["status"], "success")

    def test_supplier_fails_to_fix_all_errors_until_limit(self):
        """5 real attempts, then the supplier's own turn-budget-exceeded notice (call 6)."""
        unfixable_data = [
            {"timestamp": "2026-07-15T12:00:00Z", "store_id": 1, "product_id": 101, "price": -5.0, "on_promotion": True, "historical_sales": 150.0},
            {"timestamp": "2026-07-15T13:00:00Z", "store_id": 1, "product_id": 102, "price": 5.49, "on_promotion": False, "historical_sales": 320.0}
        ]
        success, calls, response = self.run_async(
            self.coordinator.run_scenario("Test Fail Fix", unfixable_data, "partial")
        )
        self.assertFalse(success)
        self.assertEqual(calls, 6)
        self.assertEqual(response["status"], "max_turns_exceeded")


class TestAllSimulationScenariosCovered(unittest.TestCase):
    """Guarantees every scenario declared in simulation.py's STRATEGY_MAP is
    exercised over the real A2A protocol, and that STRATEGY_MAP stays in
    sync with the scenario files actually present under data/."""

    @classmethod
    def setUpClass(cls):
        cls.coordinator = SimulationCoordinator(num_calls=5, delay_factor=0.0, stream=False)

    def test_strategy_map_matches_data_directory(self):
        data_files = {f for f in os.listdir(DATA_DIR) if f.endswith(".json")}
        self.assertEqual(
            set(STRATEGY_MAP.keys()), data_files,
            "simulation.py's STRATEGY_MAP is out of sync with data/*.json scenario files"
        )
        self.assertEqual(set(EXPECTED_SUCCESS.keys()), data_files)

    def test_every_scenario_matches_expected_outcome(self):
        for scenario_file, strategy in STRATEGY_MAP.items():
            with self.subTest(scenario=scenario_file, strategy=strategy):
                with open(os.path.join(DATA_DIR, scenario_file)) as f:
                    data = json.load(f)

                success, calls, response = asyncio.run(
                    self.coordinator.run_scenario(scenario_file, data, strategy)
                )

                expected = EXPECTED_SUCCESS[scenario_file]
                self.assertEqual(
                    success, expected,
                    f"scenario {scenario_file} expected success={expected} but got {success} "
                    f"(calls={calls}, status={response.get('status')})"
                )
                # +1 for the supplier's turn-budget-exceeded notice, if any.
                self.assertLessEqual(calls, self.coordinator.num_calls + 1)


class TestMessageLogging(unittest.TestCase):
    """Covers the per-scenario A2A message logging (request_N.json /
    response_N.json) used to let a reviewer inspect the negotiation later."""

    @classmethod
    def setUpClass(cls):
        cls.coordinator = SimulationCoordinator(num_calls=5, delay_factor=0.0, stream=False)

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="a2a-msg-test-")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_request_and_response_messages_are_written_per_turn(self):
        scenario_file = "02_missing_all.json"
        with open(os.path.join(DATA_DIR, scenario_file)) as f:
            data = json.load(f)

        scenario_dir = os.path.join(self.tmp_dir, "02_missing_all")
        success, calls, response = asyncio.run(
            self.coordinator.run_scenario(
                scenario_file, data, STRATEGY_MAP[scenario_file], message_log_dir=scenario_dir
            )
        )
        self.assertTrue(success)
        self.assertGreaterEqual(calls, 1)

        for turn in range(1, calls + 1):
            request_path = os.path.join(scenario_dir, f"request_{turn}.json")
            response_path = os.path.join(scenario_dir, f"response_{turn}.json")
            self.assertTrue(os.path.exists(request_path), f"missing {request_path}")
            self.assertTrue(os.path.exists(response_path), f"missing {response_path}")

            with open(request_path) as f:
                request_payload = json.load(f)
            self.assertIn("parts", request_payload)

            with open(response_path) as f:
                response_payload = json.load(f)
            self.assertIn("status", response_payload)

        # No stray files for turns beyond the ones actually used.
        self.assertFalse(os.path.exists(os.path.join(scenario_dir, f"request_{calls + 1}.json")))

    def test_rerunning_a_scenario_does_not_remove_existing_files(self):
        scenario_file = "01_compatible.json"
        with open(os.path.join(DATA_DIR, scenario_file)) as f:
            data = json.load(f)

        scenario_dir = os.path.join(self.tmp_dir, "01_compatible")
        os.makedirs(scenario_dir, exist_ok=True)

        # Simulate a reviewer's own file living alongside the logged messages.
        marker_path = os.path.join(scenario_dir, "reviewer_notes.txt")
        with open(marker_path, "w") as f:
            f.write("keep me")

        for _ in range(2):
            success, calls, _ = asyncio.run(
                self.coordinator.run_scenario(
                    scenario_file, data, STRATEGY_MAP[scenario_file], message_log_dir=scenario_dir
                )
            )
            self.assertTrue(success)

        self.assertTrue(os.path.exists(marker_path), "rerunning a scenario must not wipe reviewer files")
        with open(marker_path) as f:
            self.assertEqual(f.read(), "keep me")

    def test_no_message_dir_means_no_files_written(self):
        scenario_file = "01_compatible.json"
        with open(os.path.join(DATA_DIR, scenario_file)) as f:
            data = json.load(f)

        # message_log_dir defaults to None: logging is opt-in.
        success, calls, _ = asyncio.run(
            self.coordinator.run_scenario(scenario_file, data, STRATEGY_MAP[scenario_file])
        )
        self.assertTrue(success)
        self.assertEqual(os.listdir(self.tmp_dir), [])


if __name__ == "__main__":
    unittest.main()
