import unittest
import os
import sys
import pandas as pd
from datetime import datetime

# Add internal modules to path
sys.path.append(os.path.join(os.path.dirname(__file__), "ml"))
sys.path.append(os.path.join(os.path.dirname(__file__), "data"))

import db
import ml_model
import data_visualization as viz
from app import app


class TestMotorPlatform(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Set up testing environment before running tests."""
        cls.client = app.test_client()
        cls.client.testing = True

        # Ensure the table exists in SQLite if testing on a fresh database
        with db.get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS plc_tags (
                    ts TEXT, set_rpm REAL, rpm REAL, current REAL, 
                    torque REAL, dc_voltage REAL, temperature REAL, vibration REAL
                )
            ''')
        
    def setUp(self):
        # We don't want to wipe the user's actual database if they run this on prod, 
        # but for testing isolation, inserting a specific unique timestamp is safer.
        self.test_ts = datetime.now().isoformat()

    def test_01_db_insertion_and_fetch(self):
        """Test database connectivity, insertion, and latest fetch."""
        with db.get_conn() as conn:
            conn.execute('''
                INSERT INTO plc_tags (ts, set_rpm, rpm, current, torque, dc_voltage, temperature, vibration)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (self.test_ts, 1500, 1495, 10.5, 50.0, 500, 45.0, 1.2))
            
        count = db.record_count()
        self.assertGreaterEqual(count, 1)
        
        df = db.fetch_latest(1)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["rpm"], 1495)

    def test_02_feature_engineering_and_states(self):
        """Test the ML feature engineering pipeline, including ramp-up/down states."""
        data = {
            "ts": [self.test_ts] * 10,
            "set_rpm": [1500] * 10,
            "rpm": [1480] * 10,       # 20 RPM below set_rpm -> is_ramp_up should be True (tol=15)
            "current": [15.0] * 10,
            "torque": [60.0] * 10,
            "dc_voltage": [550] * 10,
            "temperature": [50.0] * 10,
            "vibration": [2.0] * 10
        }
        df = pd.DataFrame(data)
        eng_df = ml_model.engineer_features(df)
        
        # Verify derived columns exist
        self.assertIn("slip", eng_df.columns)
        self.assertIn("is_steady", eng_df.columns)
        self.assertIn("is_ramp_up", eng_df.columns)
        self.assertIn("is_ramp_down", eng_df.columns)
        
        # Verify ramp up logic
        self.assertEqual(eng_df.iloc[0]["is_ramp_up"], 1.0)
        self.assertEqual(eng_df.iloc[0]["is_steady"], 0.0)

    def test_03_sensor_validation_failsafe(self):
        """Test that the live inference intercepts impossible sensor readings."""
        # Motor is ON (rpm > 5) but current and vibration are exactly 0.0
        bad_reading = {
            "set_rpm": 1500, "rpm": 1400, "current": 0.0, 
            "torque": 0.0, "dc_voltage": 0.0, "temperature": 25.0, "vibration": 0.0
        }
        res = ml_model.predict(bad_reading)
        self.assertEqual(res["health"], "Sensor Fault")
        self.assertEqual(res["fault_diagnosis"]["top_fault"], "sensor_failure")

    def test_04_api_live_endpoint(self):
        """Test the Flask /api/live endpoint returns valid visualization payload."""
        response = self.client.get('/api/live?window=1')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        
        self.assertIn("series", data)
        self.assertIn("motor_on", data)
        self.assertIn("timestamp", data)

    def test_05_data_visualization_formatter(self):
        """Test the data visualization formatting functions."""
        df = db.fetch_latest(1)
        if not df.empty:
            df = db.add_derived(df)
            payload = viz.live_payload(df)
            self.assertIn("motor_on", payload)
            
            # test CSV generation
            csv_bytes = viz.to_csv_bytes(df)
            self.assertTrue(len(csv_bytes) > 0)

    def test_06_model_update_state(self):
        """Test that training states persist properly."""
        state = ml_model.load_training_state()
        original_status = state.is_updating
        
        state.is_updating = True
        ml_model.save_training_state(state)
        
        loaded = ml_model.load_training_state()
        self.assertTrue(loaded.is_updating)
        
        # Revert
        loaded.is_updating = original_status
        ml_model.save_training_state(loaded)


if __name__ == '__main__':
    unittest.main(verbosity=2)
