"""
app.py  —  Phase 6 rewrite
Flask backend: SQLite-backed, zero AWS references.
Real schema: ts, rpm, current, torque, dc_voltage, temperature, vibration
"""

import sys
import os
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory, Response
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), "ml"))
sys.path.append(os.path.join(os.path.dirname(__file__), "data"))

import db
import ml_model
import data_visualization as viz
import json

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")

# ── Bootstrap ─────────────────────────────────────────────────────────────────

# ── Static frontend ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(FRONTEND_DIR, path)

# ── Live Monitoring ────────────────────────────────────────────────────────────

@app.route("/api/live")
def api_live():
    window = request.args.get("window", default=60, type=int)
    recent = db.fetch_latest(n=window)
    recent = db.add_derived(recent)
    recent["ts"] = recent["ts"].astype(str)
    return jsonify(viz.live_payload(recent))


# ── Historical Analytics ───────────────────────────────────────────────────────

@app.route("/api/history")
def api_history():
    range_id = request.args.get("range", "7d")
    days = {"1d": 1, "7d": 7, "30d": 30}.get(range_id, 7)
    subset = db.fetch_range(days=days)
    if subset.empty:
        return jsonify({"series": {}, "stats": {}, "n_raw_points": 0})
    subset = db.add_derived(subset)
    subset["ts"] = subset["ts"].astype(str)

    params_arg = request.args.get("params")
    params = params_arg.split(",") if params_arg else None
    return jsonify(viz.historical_payload(subset, params=params))


@app.route("/api/history/export")
def api_history_export():
    range_id = request.args.get("range", "7d")
    days = {"1d": 1, "7d": 7, "30d": 30}.get(range_id, 7)
    subset = db.fetch_range(days=days)
    subset = db.add_derived(subset)
    csv_bytes = viz.to_csv_bytes(subset)
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=motor-history-{range_id}.csv"},
    )


# ── ML / Predictive Maintenance ────────────────────────────────────────────────

@app.route("/api/ml/status")
def api_ml_status():
    state = ml_model.load_training_state()
    state.records_collected = db.record_count()
    ml_model.save_training_state(state)
    latest_ts = db.db_latest_ts()
    return jsonify(ml_model.training_progress(state, db_latest_ts=latest_ts))





@app.route("/api/ml/train", methods=["POST"])
def api_ml_train():
    state = ml_model.load_training_state()
    latest_ts = db.db_latest_ts()
    progress = ml_model.training_progress(state, db_latest_ts=latest_ts)

    if not progress["ready_to_train"] and not state.completed:
        return jsonify({"error": "training window not yet complete", **progress}), 400

    df = db.fetch_all_for_training()
    result = ml_model.train_isolation_forest(df)
    return jsonify(result)


@app.route("/api/ml/live-score")
def api_ml_live_score():
    latest_df = db.fetch_latest(n=1)
    if latest_df.empty:
        return jsonify({"score": None, "health": "No data", "fault_diagnosis": None})
    latest_df = db.add_derived(latest_df)
    latest = latest_df.iloc[-1]
    reading = {col: float(latest[col]) for col in ml_model.RAW_COLUMNS if col in latest}
    result = ml_model.predict(reading)
    # Add computed thermal metrics from engineered data (if present)
    result.update({
        "thermal_overload": float(latest.get("thermal_overload", 0.0)),
        "thermal_stress": float(latest.get("thermal_stress", 0.0)),
        "motor_overload": float(latest.get("motor_overload", 0.0)),
    })
    return jsonify(result)


@app.route("/api/ml/anomaly-history")
def api_ml_anomaly_history():
    if not ml_model.model_is_trained():
        return jsonify({"points": [], "latest_score": None, "latest_health": "Machine Under Training"})
    recent = db.fetch_latest(n=80)
    if recent.empty:
        return jsonify({"points": [], "latest_score": None, "latest_health": "No data"})
    recent = db.add_derived(recent)
    try:
        scored = ml_model.predict_batch(recent)
        scored["ts"] = scored["ts"].astype(str)
        return jsonify(viz.anomaly_payload(scored))
    except Exception as e:
        return jsonify({"error": str(e), "points": []}), 500

@app.route("/api/ml/predict-history")
def api_ml_predict_history():
    """Return predictive maintenance inference for recent historical data.
    Includes thermal_overload, thermal_stress, motor_overload calculated by the model.
    """
    if not ml_model.model_is_trained():
        return jsonify({"error": "Model not trained"}), 400
    n = request.args.get("n", default=80, type=int)
    recent = db.fetch_latest(n=n)
    if recent.empty:
        return jsonify({"rows": []})
    recent = db.add_derived(recent)
    try:
        scored = ml_model.predict_batch(recent)
        # Convert to list of dicts for JSON response
        rows = scored.to_dict(orient="records")
        return jsonify({"rows": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Fault diagnosis (dedicated endpoint) ──────────────────────────────────────

@app.route("/api/ml/fault-diagnosis")
def api_fault_diagnosis():
    """Latest reading → fault type breakdown."""
    latest_df = db.fetch_latest(n=20)
    if latest_df.empty:
        return jsonify({"top_fault": None, "scores": {}})
    latest_df = db.add_derived(latest_df)

    # run through feature engineering for z-score context
    if "ts" not in latest_df.columns:
        latest_df["ts"] = pd.date_range(end=datetime.utcnow(), periods=len(latest_df), freq="2s").astype(str)
    try:
        eng = ml_model.engineer_features(latest_df)
        reading = eng.iloc[-1].to_dict() if not eng.empty else latest_df.iloc[-1].to_dict()
    except Exception:
        reading = latest_df.iloc[-1].to_dict()

    return jsonify(ml_model.diagnose_fault(reading))


# ── Settings ───────────────────────────────────────────────────────────────────

_settings = {
    "motor_id": "M-014",
    "location": "Plant Floor A",
    "poll_interval_s": 2,
    "alert_email": "",
    "thresholds": {
        "rpm":         {"min": 0, "max": 270},
        "current":     {"min": 0, "max": 80},
        "torque":      {"min": -140, "max": 140},
        "dc_voltage":  {"min": 500, "max": 600},
        "temperature": {"min": 20, "max": 80},
        "vibration":   {"min": 0, "max": 6},
        "power":       {"min": 0, "max": 50},
        "slip":        {"min": 0, "max": 20}
    }
}

if os.path.exists(SETTINGS_PATH):
    try:
        with open(SETTINGS_PATH, "r") as f:
            _saved = json.load(f)
            # Basic merge to handle nested thresholds
            for k, v in _saved.items():
                if k == "thresholds" and isinstance(v, dict):
                    for tk, tv in v.items():
                        if tk in _settings["thresholds"]:
                            _settings["thresholds"][tk].update(tv)
                else:
                    _settings[k] = v
    except Exception as e:
        print(f"Error loading settings: {e}")

def save_settings():
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(_settings, f, indent=2)
    except Exception as e:
        print(f"Error saving settings: {e}")

# Sync the ML model's thermal stress threshold (P605) with the UI setting on startup
ml_model.set_p605_threshold(float(_settings["thresholds"]["temperature"]["max"]))

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify(_settings)

@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    body = request.get_json(force=True)
    
    # Handle top-level primitive values
    _settings.update({k: v for k, v in body.items() if k in _settings and k != "thresholds"})
    
    # Handle nested thresholds safely
    if "thresholds" in body and isinstance(body["thresholds"], dict):
        for param, limits in body["thresholds"].items():
            if param in _settings["thresholds"] and isinstance(limits, dict):
                if "min" in limits:
                    _settings["thresholds"][param]["min"] = float(limits["min"])
                if "max" in limits:
                    _settings["thresholds"][param]["max"] = float(limits["max"])
                    
    # Sync the ML model's thermal stress threshold (P605) whenever settings are updated
    ml_model.set_p605_threshold(float(_settings["thresholds"]["temperature"]["max"]))
                    
    save_settings()
    return jsonify(_settings)


@app.route("/api/db/reset", methods=["POST"])
def api_db_reset():
    db.reset_database()
    ml_model.reset_training()
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
