"""
ml_model.py  —  Phase 6 rewrite
Real sensor schema: rpm, current, torque, dc_voltage, temperature, vibration
Derived at feature-engineering time: motor_on, mechanical_power_proxy, torque_current_ratio
7 fault types (Rotor/Slip dropped — no slip column or rated RPM available)
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import joblib

# ── Config ────────────────────────────────────────────────────────────────────

RAW_COLUMNS = ["set_rpm", "rpm", "current", "torque", "dc_voltage", "temperature", "vibration"]

FEATURE_COLUMNS = [
    # raw
    "set_rpm", "rpm", "current", "torque", "dc_voltage", "temperature", "vibration",
    # derived
    "motor_on", "mechanical_power_proxy", "torque_current_ratio", "slip",
    # rolling stats (window=10)
    "rpm_mean10", "rpm_std10", "current_mean10", "current_std10",
    "vibration_mean10", "vibration_std10", "temperature_rate10",
]

MODEL_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
MODEL_PATH = os.path.join(MODEL_DIR, "isolation_forest.joblib")
SCALER_PATH = os.path.join(MODEL_DIR, "scaler.joblib")
TRAINING_STATE_PATH = os.path.join(MODEL_DIR, "training_state.json")
FEATURE_STATS_PATH = os.path.join(MODEL_DIR, "feature_stats.json")

THRESHOLD_WARNING  = 0.35
THRESHOLD_ANOMALY  = 0.60
THRESHOLD_CRITICAL = 0.80

os.makedirs(MODEL_DIR, exist_ok=True)

# ── Training state ────────────────────────────────────────────────────────────

@dataclass
class TrainingState:
    duration_hours: int
    started_at: str
    completed: bool = False
    completed_at: Optional[str] = None
    records_collected: int = 0

    def to_dict(self):
        return asdict(self)


def load_training_state() -> TrainingState:
    if os.path.exists(TRAINING_STATE_PATH):
        with open(TRAINING_STATE_PATH) as f:
            data = json.load(f)
            if "duration_days" in data:
                del data["duration_days"]
                data["duration_hours"] = 3
        return TrainingState(**data)
    state = TrainingState(duration_hours=3, started_at=datetime.now().isoformat())
    save_training_state(state)
    return state


def save_training_state(state: TrainingState) -> None:
    with open(TRAINING_STATE_PATH, "w") as f:
        json.dump(state.to_dict(), f, indent=2)


def training_progress(state: TrainingState, db_latest_ts=None) -> dict:
    """
    Progress anchored to DB latest timestamp — avoids negative-elapsed-time
    bug when sensor data timestamps are ahead of wall clock.
    """
    started = datetime.fromisoformat(state.started_at)
    completion_date = started + timedelta(hours=state.duration_hours)

    # anchor to DB time if provided, else wall clock
    now = db_latest_ts if db_latest_ts else datetime.now()
    elapsed = (now - started).total_seconds()
    total = timedelta(hours=state.duration_hours).total_seconds()
    pct = max(0.0, min(100.0, (elapsed / total) * 100))

    return {
        "status": "MODEL_DEPLOYED" if state.completed else "MACHINE_UNDER_TRAINING",
        "duration_hours": state.duration_hours,
        "started_at": state.started_at,
        "completion_date": completion_date.isoformat(),
        "progress_pct": round(pct, 2),
        "records_collected": state.records_collected,
        "ready_to_train": pct >= 100 and not state.completed,
    }

# ── Feature engineering ───────────────────────────────────────────────────────

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Accepts df with RAW_COLUMNS + 'ts'. Returns df with all FEATURE_COLUMNS.
    NaN rows (from rolling windows at the start) are dropped.
    """
    df = df.copy().sort_values("ts").reset_index(drop=True)

    df["motor_on"] = (df["rpm"] > 5).astype(float)
    df["mechanical_power_proxy"] = (df["torque"].abs() * df["rpm"]).round(4)
    # guard div-by-zero when current ≈ 0
    df["torque_current_ratio"] = (
        df["torque"] / df["current"].replace(0, np.nan)
    ).fillna(0).round(4)
    df["slip"] = (df["set_rpm"] - df["rpm"]).abs().round(4)

    w = 10
    df["rpm_mean10"]         = df["rpm"].rolling(w).mean()
    df["rpm_std10"]          = df["rpm"].rolling(w).std().fillna(0)
    df["current_mean10"]     = df["current"].rolling(w).mean()
    df["current_std10"]      = df["current"].rolling(w).std().fillna(0)
    df["vibration_mean10"]   = df["vibration"].rolling(w).mean()
    df["vibration_std10"]    = df["vibration"].rolling(w).std().fillna(0)
    df["temperature_rate10"] = df["temperature"].diff(w).fillna(0)

    return df.dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)


class RollingReadingBuffer:
    """
    Keeps the last `size` raw readings so rolling features can be computed
    for a single incoming live point without reprocessing the full DB.
    """
    def __init__(self, size: int = 20):
        self._buf: list[dict] = []
        self._size = size

    def push(self, reading: dict):
        self._buf.append(reading)
        if len(self._buf) > self._size:
            self._buf.pop(0)

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame(self._buf)


_live_buffer = RollingReadingBuffer(size=20)

# ── Training ──────────────────────────────────────────────────────────────────

def train_isolation_forest(
    df: pd.DataFrame,
    n_estimators: int = 200,
    contamination: float = "auto",
    random_state: int = 42,
) -> dict:
    missing = [c for c in RAW_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Training data missing columns: {missing}")
    if "ts" not in df.columns:
        df = df.copy()
        df["ts"] = pd.date_range(end=datetime.now(), periods=len(df), freq="2s").astype(str)

    engineered = engineer_features(df)
    if len(engineered) < 50:
        raise ValueError("Too few rows after feature engineering (need ≥ 50).")

    X = engineered[FEATURE_COLUMNS].to_numpy(dtype=float)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=random_state,
        n_jobs=-1,
    )
    model.fit(X_scaled)

    joblib.dump(model, MODEL_PATH)
    joblib.dump(scaler, SCALER_PATH)

    # persist feature stats for schema validation
    stats = {col: {"mean": float(engineered[col].mean()), "std": float(engineered[col].std())}
             for col in FEATURE_COLUMNS}
    with open(FEATURE_STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)

    state = load_training_state()
    state.completed = True
    state.completed_at = datetime.now().isoformat()
    save_training_state(state)

    return {
        "model_path": MODEL_PATH,
        "n_samples": len(engineered),
        "n_features": len(FEATURE_COLUMNS),
        "trained_at": state.completed_at,
    }


def model_is_trained() -> bool:
    return os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH)

# ── Inference ─────────────────────────────────────────────────────────────────

_cached_model = None
_cached_scaler = None


def _load_artifacts():
    global _cached_model, _cached_scaler
    if _cached_model is None or _cached_scaler is None:
        _cached_model = joblib.load(MODEL_PATH)
        _cached_scaler = joblib.load(SCALER_PATH)
    return _cached_model, _cached_scaler


def score_to_health(score_0_1: float) -> str:
    if score_0_1 < THRESHOLD_WARNING:
        return "Healthy"
    if score_0_1 < THRESHOLD_ANOMALY:
        return "Warning"
    if score_0_1 < THRESHOLD_CRITICAL:
        return "Anomaly Detected"
    return "Critical Condition"


def _raw_to_normalized(raw_score: float) -> float:
    normalized = 1 - ((raw_score + 0.3) / 0.6)
    return float(np.clip(normalized, 0.0, 1.0))


def predict(reading: dict) -> dict:
    """Score a single live reading (dict with RAW_COLUMNS keys)."""
    if not model_is_trained():
        return {
            "score": None,
            "health": "Machine Under Training",
            "fault_diagnosis": None,
            "error": "Model not yet trained.",
        }

    _live_buffer.push(reading)
    buf_df = _live_buffer.to_df()

    if "ts" not in buf_df.columns:
        buf_df["ts"] = pd.date_range(end=datetime.now(), periods=len(buf_df), freq="2s").astype(str)

    try:
        engineered = engineer_features(buf_df)
    except Exception:
        engineered = pd.DataFrame()

    if engineered.empty:
        # fallback: use raw features only (first few readings before window fills)
        row = {c: reading.get(c, 0.0) for c in RAW_COLUMNS}
        row["motor_on"] = float(row["rpm"] > 5)
        row["mechanical_power_proxy"] = abs(row["torque"]) * row["rpm"]
        row["torque_current_ratio"] = row["torque"] / (row["current"] or 1)
        row["slip"] = abs(row.get("set_rpm", 0.0) - row["rpm"])
        for col in ["rpm_mean10","rpm_std10","current_mean10","current_std10",
                    "vibration_mean10","vibration_std10","temperature_rate10"]:
            row[col] = row.get(col.split("_")[0], 0.0)
        feat_row = row
    else:
        feat_row = engineered.iloc[-1].to_dict()

    model, scaler = _load_artifacts()
    x = np.array([[feat_row.get(c, 0.0) for c in FEATURE_COLUMNS]], dtype=float)
    x_scaled = scaler.transform(x)
    raw = model.decision_function(x_scaled)[0]
    normalized = _raw_to_normalized(raw)

    # build engineered reading dict for fault diagnosis
    eng_reading = {c: feat_row.get(c, reading.get(c, 0.0)) for c in FEATURE_COLUMNS}

    return {
        "score": round(normalized, 4),
        "health": score_to_health(normalized),
        "raw_decision_function": round(float(raw), 5),
        "fault_diagnosis": diagnose_fault(eng_reading),
    }


def predict_batch(df: pd.DataFrame) -> pd.DataFrame:
    if not model_is_trained():
        raise RuntimeError("Model not trained yet")
    if "ts" not in df.columns:
        df = df.copy()
        df["ts"] = pd.date_range(end=datetime.now(), periods=len(df), freq="2s").astype(str)

    engineered = engineer_features(df)
    model, scaler = _load_artifacts()
    X = engineered[FEATURE_COLUMNS].to_numpy(dtype=float)
    X_scaled = scaler.transform(X)
    raw = model.decision_function(X_scaled)
    normalized = np.clip(1 - ((raw + 0.3) / 0.6), 0.0, 1.0)

    out = engineered.copy()
    out["anomaly_score"] = normalized
    out["health"] = [score_to_health(s) for s in normalized]
    return out

# ── Fault diagnosis ───────────────────────────────────────────────────────────

FAULT_TYPES = [
    "bearing_shaft",
    "overload",
    "coupling_slip",
    "voltage_supply",
    "winding_overcurrent",
    "thermal_overload",
    "mechanical_imbalance",
    "slip_fault",
]

def _zscore(value: float, mean: float, std: float) -> float:
    return abs(value - mean) / (std + 1e-9)


def diagnose_fault(reading: dict) -> dict:
    """
    Rule-based z-score fault diagnosis against engineered features.
    Returns top fault type + confidence for all 7 types.
    """
    if not os.path.exists(FEATURE_STATS_PATH):
        return {"top_fault": None, "scores": {}}

    with open(FEATURE_STATS_PATH) as f:
        stats = json.load(f)

    def z(col):
        if col not in stats or col not in reading:
            return 0.0
        return _zscore(float(reading[col]), stats[col]["mean"], stats[col]["std"])

    vibration_z  = z("vibration")
    vib_std_z    = z("vibration_std10")
    current_z    = z("current")
    cur_std_z    = z("current_std10")
    torque_z     = z("torque")
    voltage_z    = z("dc_voltage")
    temp_z       = z("temperature")
    temp_rate_z  = z("temperature_rate10")
    power_z      = z("mechanical_power_proxy")
    tcr_z        = z("torque_current_ratio")
    slip_z       = z("slip")

    scores = {
        "bearing_shaft":       0.5 * vibration_z + 0.3 * vib_std_z + 0.2 * current_z,
        "overload":            0.4 * current_z   + 0.3 * power_z   + 0.3 * torque_z,
        "coupling_slip":       0.5 * tcr_z       + 0.3 * torque_z  + 0.2 * vib_std_z,
        "voltage_supply":      0.6 * voltage_z   + 0.2 * current_z + 0.2 * power_z,
        "winding_overcurrent": 0.5 * cur_std_z   + 0.3 * current_z + 0.2 * temp_z,
        "thermal_overload":    0.5 * temp_z      + 0.3 * temp_rate_z + 0.2 * current_z,
        "mechanical_imbalance": 0.4 * vib_std_z  + 0.4 * vibration_z + 0.2 * power_z,
        "slip_fault":          0.6 * slip_z      + 0.2 * tcr_z       + 0.2 * current_z,
    }

    # normalise to [0, 1]
    max_s = max(scores.values()) or 1.0
    norm = {k: round(v / max_s, 4) for k, v in scores.items()}
    top = max(norm, key=norm.get)

    return {
        "top_fault": top,
        "confidence": norm[top],
        "scores": norm,
    }


def reset_training() -> None:
    """Wipe all ML artifacts and state to start fresh."""
    if os.path.exists(MODEL_DIR):
        shutil.rmtree(MODEL_DIR, ignore_errors=True)
        os.makedirs(MODEL_DIR, exist_ok=True)
    global _cached_model, _cached_scaler
    _cached_model = None
    _cached_scaler = None
