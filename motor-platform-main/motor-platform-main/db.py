"""
db.py
SQLite data layer — replaces the in-memory buffer and all AWS/Timestream references.

Real sensor schema: ts, rpm, current, torque, dc_voltage, temperature, vibration
Derived at query time: power = dc_voltage * current  (labeled "derived" in UI)
motor_on derived from: rpm > 5
"""

import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

DB_PATH = r"C:\Dreamz Internship 2\final data w vibration temperature\mydb.db"

SENSOR_COLUMNS = ["ts", "set_rpm", "rpm", "current", "torque", "dc_voltage", "temperature", "vibration"]


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── queries ───────────────────────────────────────────────────────────────────

def fetch_latest(n: int = 60) -> pd.DataFrame:
    """Return the n most recent rows, ascending by ts."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM plc_tags ORDER BY ts DESC LIMIT ?", (n,)
        ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"])
    return df.sort_values("ts").reset_index(drop=True)


def fetch_range(days: int = 7) -> pd.DataFrame:
    """Return all rows in the last `days` days, using DB-anchored cutoff."""
    latest_ts = _db_latest_ts()
    if latest_ts is None:
        return pd.DataFrame()
    cutoff = latest_ts - timedelta(days=days)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM plc_tags WHERE ts >= ? ORDER BY ts",
            (cutoff.isoformat(),),
        ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"])
    return df


def fetch_all_for_training() -> pd.DataFrame:
    """Return complete dataset for Isolation Forest training."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM plc_tags ORDER BY ts").fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"])
    return df


def record_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM plc_tags").fetchone()[0]


def _db_latest_ts() -> Optional[datetime]:
    """Latest timestamp in the database (used to anchor all time calculations)."""
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(ts) FROM plc_tags").fetchone()[0]
    return pd.to_datetime(row) if row else None


def db_latest_ts() -> Optional[datetime]:
    return _db_latest_ts()


# ── derived columns ───────────────────────────────────────────────────────────

def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Add motor_on and power columns derived from raw sensor columns."""
    df = df.copy()
    df["motor_on"] = (df["rpm"] > 5).astype(int)
    df["power"] = ((df["dc_voltage"] * df["current"]) / 1000.0).round(3)
    df["slip"] = (df["set_rpm"] - df["rpm"]).abs().round(4)
    return df


def reset_database() -> None:
    """Clear all records from plc_tags."""
    with get_conn() as conn:
        conn.execute("DELETE FROM plc_tags")


