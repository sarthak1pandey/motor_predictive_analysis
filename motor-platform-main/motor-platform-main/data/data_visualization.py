"""
data_visualization.py  —  Phase 6 rewrite
Real schema: rpm, current, torque, dc_voltage, temperature, vibration
Derived: power = dc_voltage * current  (flagged as derived in payload)
"""

from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd

# ── Parameter metadata ────────────────────────────────────────────────────────

PARAM_META = {
    "rpm":         {"label": "Speed",        "unit": "RPM",   "color": "#4FC1E0", "derived": False},
    "set_rpm":     {"label": "Set Speed",    "unit": "RPM",   "color": "#4FA1E0", "derived": False},
    "current":     {"label": "Current",      "unit": "A",     "color": "#F2A93B", "derived": False},
    "torque":      {"label": "Torque",       "unit": "N·m",   "color": "#5FD3A6", "derived": False},
    "dc_voltage":  {"label": "DC Bus V",     "unit": "V",     "color": "#9D8DF1", "derived": False},
    "temperature": {"label": "Temperature",  "unit": "°C",    "color": "#E58FB3", "derived": False},
    "vibration":   {"label": "Vibration",    "unit": "mm/s",  "color": "#F2545D", "derived": False},
    "power":       {"label": "Power",        "unit": "kW",    "color": "#FFD166", "derived": True},
    "slip":        {"label": "Slip",         "unit": "%",     "color": "#A2E4B8", "derived": True},
}

RESAMPLE_TARGET_POINTS = 500


def _add_power(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "power" not in df.columns:
        df["power"] = (df["dc_voltage"] * df["current"] / 1000.0).round(3)
    return df


# ── Live payload ──────────────────────────────────────────────────────────────

def live_payload(df: pd.DataFrame) -> dict:
    df = _add_power(df.sort_values("ts"))
    latest = df.iloc[-1]
    motor_on = bool(latest.get("motor_on", int(latest["rpm"] > 5)))

    series = {}
    for key, meta in PARAM_META.items():
        if key not in df.columns:
            continue
        series[key] = {
            "label":   meta["label"],
            "unit":    meta["unit"],
            "color":   meta["color"],
            "derived": meta["derived"],
            "points": [
                {"time": str(t), "value": round(float(v), 3)}
                for t, v in zip(df["ts"], df[key])
                if pd.notna(v)
            ],
            "latest": round(float(latest[key]), 3) if pd.notna(latest.get(key)) else None,
        }

    return {
        "motor_on": motor_on,
        "timestamp": str(latest["ts"]),
        "series": series,
    }


# ── Historical payload ────────────────────────────────────────────────────────

def historical_payload(df: pd.DataFrame, params: Optional[list[str]] = None) -> dict:
    df = _add_power(df.sort_values("ts"))
    params = params or list(PARAM_META.keys())
    params = [p for p in params if p in df.columns]

    step = max(1, len(df) // RESAMPLE_TARGET_POINTS)
    sampled = df.iloc[::step]

    series = {}
    stats = {}
    for key in params:
        meta = PARAM_META.get(key, {"label": key, "unit": "", "color": "#888", "derived": False})
        series[key] = {
            "label":   meta["label"],
            "unit":    meta["unit"],
            "color":   meta["color"],
            "derived": meta.get("derived", False),
            "points": [
                {"time": str(t), "value": round(float(v), 3)}
                for t, v in zip(sampled["ts"], sampled[key])
                if pd.notna(v)
            ],
        }
        col = df[key].dropna()
        stats[key] = {
            "avg": round(float(col.mean()), 3) if len(col) else None,
            "min": round(float(col.min()),  3) if len(col) else None,
            "max": round(float(col.max()),  3) if len(col) else None,
        }

    return {"series": series, "stats": stats, "n_raw_points": len(df)}


# ── Anomaly payload ───────────────────────────────────────────────────────────

def anomaly_payload(scored_df: pd.DataFrame) -> dict:
    scored_df = scored_df.sort_values("ts")
    ts_col = "ts" if "ts" in scored_df.columns else "time"
    return {
        "points": [
            {"time": str(t), "score": round(float(s), 4), "health": h}
            for t, s, h in zip(scored_df[ts_col], scored_df["anomaly_score"], scored_df["health"])
        ],
        "latest_score": round(float(scored_df.iloc[-1]["anomaly_score"]), 4),
        "latest_health": scored_df.iloc[-1]["health"],
    }


# ── CSV export ────────────────────────────────────────────────────────────────

def to_csv_bytes(df: pd.DataFrame) -> bytes:
    df = _add_power(df)
    return df.to_csv(index=False).encode("utf-8")
