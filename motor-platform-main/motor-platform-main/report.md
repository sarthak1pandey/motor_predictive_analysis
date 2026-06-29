# Motor Predictive Maintenance Platform — Complete Project Report

**Version:** Phase 6  
**Stack:** Python · Flask · SQLite · Scikit-learn · Snap7 · HTML/CSS/JS  
**Architecture:** On-premises, zero-cloud, self-sustaining

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Complete Pipeline Flow](#2-complete-pipeline-flow)
3. [File-by-File Breakdown](#3-file-by-file-breakdown)
   - 3.1 [plc_logger.py — Hardware Data Acquisition](#31-plc_loggerpy--hardware-data-acquisition)
   - 3.2 [db.py — Data Layer](#32-dbpy--data-layer)
   - 3.3 [ml/ml_model.py — Intelligence Layer](#33-mlml_modelpy--intelligence-layer)
   - 3.4 [app.py — Web Server & Orchestrator](#34-apppy--web-server--orchestrator)
   - 3.5 [data/data_visualization.py — Presentation Layer](#35-datadata_visualizationpy--presentation-layer)
   - 3.6 [frontend/ — Dashboard UI](#36-frontend--dashboard-ui)
   - 3.7 [test.py — Automated Test Suite](#37-testpy--automated-test-suite)
4. [ML Model Deep Dive](#4-ml-model-deep-dive)
5. [Long-Term Stability Architecture](#5-long-term-stability-architecture)
6. [Sensor Schema](#6-sensor-schema)

---

## 1. Project Overview

This platform provides **real-time predictive maintenance** for an industrial motor controlled by a Siemens PLC (G120/CU240BE). The system continuously reads live sensor telemetry from the PLC every second, stores it in a local SQLite database, and runs an Isolation Forest machine learning model to detect anomalies and diagnose specific fault types — all without any dependency on cloud infrastructure.

**Key Capabilities:**
- Live motor health scoring (anomaly score 0.0 – 1.0)
- 9-class fault diagnosis: bearing shaft, motor overload, coupling slip, voltage supply, winding overcurrent, thermal overload, thermal stress, mechanical imbalance, slip fault
- Motor phase detection: Ramp-Up, Steady, Ramp-Down states
- Sensor failure detection
- Rolling 3-month model retraining with zero downtime
- Automatic database downsampling and retention
- Web dashboard for real-time, historical, and predictive analytics

---

## 2. Complete Pipeline Flow

```
Physical Motor
      │
      ▼
[PLC — Siemens G120]      ← reads drive parameters (RPM, Current, Torque, Voltage, etc.)
      │  (Snap7 S7 protocol over Ethernet)
      ▼
[plc_logger.py]            ← polls PLC every 1 second, writes rows to SQLite
      │
      ▼
[mydb.db — SQLite]         ← WAL-mode database, stores all raw sensor rows
      │
      ├──────────────────────────────────────┐
      ▼                                      ▼
[db.py — fetch_latest/range]         [db.run_maintenance()]
      │                                      │
      │                              (daily: deletes >90d,
      │                               downsamples >7d to 1-min avg)
      ▼
[ml_model.engineer_features()]   ← derives slip%, state flags, rolling stats, thermal features
      │
      ▼
[IsolationForest.predict()]      ← anomaly score 0.0 → 1.0
      │
      ├── score < 0.35 → Healthy
      ├── score < 0.60 → Warning
      ├── score < 0.80 → Anomaly Detected
      └── score ≥ 0.80 → Critical Condition
      │
      ▼
[diagnose_fault()]               ← Z-score weighted rules → top fault type + confidence
      │
      ▼
[app.py Flask API]               ← serves JSON to frontend
      │
      ▼
[frontend/index.html + app.js]   ← renders live charts, health score, fault bars
```

---

## 3. File-by-File Breakdown

---

### 3.1 `plc_logger.py` — Hardware Data Acquisition

**Location:** `c:\Dreamz Internship 2\final data w vibration temperature\plc_logger.py`  
**Runs:** Independently, always-on process on the local machine  
**Role:** The physical bridge between the real motor and the software platform.

#### How it works:
1. Uses the **Snap7** library to connect to the Siemens PLC via Ethernet (`192.168.2.5`) using the S7 protocol.
2. Reads **7 sensor channels** from the PLC's Marker Memory (`MK`) area at specific byte offsets every 1 second using `read_area()` and `get_real()`:

| Channel       | PLC Byte Offset | Data Type |
|---------------|-----------------|-----------|
| `set_rpm`     | MK10            | REAL      |
| `current`     | MK14            | REAL      |
| `rpm`         | MK18            | REAL      |
| `torque`      | MK22            | REAL      |
| `dc_voltage`  | MK26            | REAL      |
| `temperature` | MK30            | REAL      |
| `vibration`   | MK104           | REAL      |

3. Only logs a row to SQLite when `rpm > 0` (motor is active or spinning down).
4. Each row is written with an ISO 8601 timestamp (`datetime.now().isoformat()`).
5. Prints a live console summary every second for operator monitoring.

> **Note:** `plc_logger.py` runs as a standalone process completely separate from the Flask web server. It is the only process with direct hardware access.

---

### 3.2 `db.py` — Data Layer

**Location:** `motor-platform-main/db.py`  
**Role:** All SQLite read/write operations. The single point of contact between the app and the database file.

#### Key Functions:

| Function | Purpose |
|---|---|
| `get_conn()` | Context manager. Opens an SQLite connection with WAL journal mode (safe for concurrent reads) and NORMAL sync. Commits and closes automatically. |
| `fetch_latest(n)` | Returns the `n` most recent rows, sorted ascending by timestamp. Used for live charts and live ML scoring. |
| `fetch_range(days)` | Returns all rows within the last `days` days, anchored to the latest DB timestamp (not wall clock) to avoid time sync bugs. Used for historical analytics. |
| `fetch_all_for_training()` | Returns the complete dataset for initial Isolation Forest training. |
| `add_derived(df)` | Adds runtime-calculated columns to any fetched DataFrame: `motor_on` (rpm > 5), `power` (dc_voltage × current / 1000), `slip` ((set_rpm − rpm) / set_rpm × 100). |
| `record_count()` | Returns total row count. Used to track training data accumulation progress. |
| `db_latest_ts()` | Returns the most recent timestamp in the DB. Used by the ML model to anchor training progress calculations. |
| `run_maintenance()` | **Long-term stability function.** Deletes all rows older than 90 days. Downsamples all rows older than 7 days into 1-minute averages using a SQLite TEMP table strategy to keep database size manageable indefinitely. |
| `reset_database()` | Deletes all rows (used when resetting the entire platform from the UI). |

#### Slip Formula:
```
slip (%) = (set_rpm − rpm) / set_rpm × 100
```
A positive slip means the motor is running slower than commanded (normal). A negative slip means overspeed.

---

### 3.3 `ml/ml_model.py` — Intelligence Layer

**Location:** `motor-platform-main/ml/ml_model.py`  
**Role:** Feature engineering, anomaly detection model training/inference, fault diagnosis, and model lifecycle management.

---

#### A. Feature Engineering (`engineer_features`)

Accepts a raw DataFrame and transforms it into a rich feature matrix with **22 columns**:

| Feature | Source | Description |
|---|---|---|
| `set_rpm`, `rpm`, `current`, `torque`, `dc_voltage`, `temperature`, `vibration` | Raw sensors | Direct PLC readings |
| `motor_on` | Derived | 1 if rpm > 5, else 0 |
| `mechanical_power_proxy` | Derived | `abs(torque) × rpm` |
| `torque_current_ratio` | Derived | `torque / current` (0 if current ≈ 0) |
| `slip` | Derived | `(set_rpm − rpm) / set_rpm × 100` |
| `thermal_overload` | Derived | `(rpm × temperature) × 0.001` when rpm > 1 |
| `thermal_stress` | Derived | `(temperature − threshold) × 0.01` when rpm > 1 |
| `motor_overload` | Derived | `abs(torque) × current × 0.0005` when rpm > 1 |
| `rpm_mean10`, `rpm_std10` | Rolling (window=10) | Rolling mean and std deviation of RPM |
| `current_mean10`, `current_std10` | Rolling (window=10) | Rolling mean and std deviation of current |
| `vibration_mean10`, `vibration_std10` | Rolling (window=10) | Rolling mean and std deviation of vibration |
| `temperature_rate10` | Rolling (window=10) | Rate of temperature change over 10 samples |
| `is_ramp_up` | State flag | 1 if set_rpm > 0 and rpm < set_rpm − 15 |
| `is_ramp_down` | State flag | 1 if set_rpm = 0 and rpm > 5, OR rpm > set_rpm + 15 |
| `is_steady` | State flag | 1 if set_rpm > 0 and abs(rpm − set_rpm) ≤ 15 |

The state flags are crucial — they prevent the model from mistaking a normal startup ramp from a real `slip_fault`.

---

#### B. Isolation Forest Training (`train_isolation_forest`)

The Isolation Forest is an **unsupervised anomaly detection** algorithm. It does not require labeled fault data. Instead, it learns what "normal" looks like and isolates anything that deviates.

**Training flow:**
1. Receives a DataFrame of historical readings (last 30 days for updates, full DB for initial training).
2. Runs `engineer_features()` to build the full 22-column feature matrix.
3. Fits a `StandardScaler` to normalize all features.
4. Trains an `IsolationForest` with 200 estimators.
5. Saves the model to `artifacts/isolation_forest.joblib` (or `_staging` if it's a background update).
6. Saves per-feature mean/std stats to `artifacts/feature_stats.json` for use by the fault diagnosis engine.
7. Updates `training_state.json` to mark the model as completed/updating.

**Staging Pipeline (zero-downtime updates):**
- When triggered as a **background update** (`is_update=True`), the new model is saved to `isolation_forest_staging.joblib`.
- The live model continues serving all inference until `swap_staging_model()` is called 2 hours later.
- `swap_staging_model()` atomically replaces the live model files with the staged ones using `os.replace()` (atomic on all platforms), then clears the in-memory cache to force a reload.

---

#### C. Live Inference (`predict`)

**Flow for each incoming reading:**

1. **Sensor validation:** If the motor is spinning (rpm > 5) but both `current` and `vibration` are exactly `0.0`, the sensors are faulty. Returns `"Sensor Fault"` immediately without hitting the ML model.

2. **Rolling buffer:** The incoming reading is pushed to a 20-row in-memory `RollingReadingBuffer`. This allows rolling window features to be computed for a single live point without querying the full database.

3. **Feature engineering:** Runs `engineer_features()` on the buffer. If the buffer hasn't filled yet (first 10 readings), uses a fallback that computes non-rolling features only.

4. **Inference:** Passes the engineered feature vector through the `StandardScaler` and then the `IsolationForest.decision_function()`.

5. **Score normalization:** Converts the raw Isolation Forest score (typically −0.3 to +0.3) to a normalized 0–1 health score:
   ```
   normalized = 1 − ((raw_score + 0.3) / 0.6)
   normalized = clip(normalized, 0.0, 1.0)
   ```
   A higher score means more anomalous.

6. **Health label:** Assigned by threshold:
   - `< 0.35` → **Healthy**
   - `0.35–0.60` → **Warning**
   - `0.60–0.80` → **Anomaly Detected**
   - `≥ 0.80` → **Critical Condition**

7. **Fault diagnosis:** Calls `diagnose_fault()` on the same engineered reading.

---

#### D. Fault Diagnosis (`diagnose_fault`)

Uses **Z-score weighted rules** against the training baseline stored in `feature_stats.json`.

For each engineered feature, a Z-score is computed:
```
z = |value − training_mean| / (training_std + ε)
```

Each of the 9 fault types has a weighted formula:

| Fault Type | Formula |
|---|---|
| `bearing_shaft` | `0.5×vibration_z + 0.3×vib_std_z + 0.2×current_z` |
| `motor_overload` | `0.6×motor_overload_z + 0.2×current_z + 0.2×torque_z` |
| `coupling_slip` | `0.5×tcr_z + 0.3×torque_z + 0.2×vib_std_z` |
| `voltage_supply` | `0.6×voltage_z + 0.2×current_z + 0.2×power_z` |
| `winding_overcurrent` | `0.5×cur_std_z + 0.3×current_z + 0.2×temp_z` |
| `thermal_overload` | `0.6×thermal_overload_z + 0.2×temp_z + 0.2×temp_rate_z` |
| `thermal_stress` | `0.8×thermal_stress_z + 0.2×temp_z` |
| `mechanical_imbalance` | `0.4×vib_std_z + 0.4×vibration_z + 0.2×power_z` |
| `slip_fault` | `0.6×slip_z + 0.2×tcr_z + 0.2×current_z` (**suppressed during ramp-up/down**) |

All scores are normalized to [0, 1] and the highest-scoring fault is returned as `top_fault`.

---

### 3.4 `app.py` — Web Server & Orchestrator

**Location:** `motor-platform-main/app.py`  
**Role:** Flask web server that exposes the REST API, manages settings, and runs the background maintenance loop.

#### API Endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the frontend `index.html` |
| `/api/live` | GET | Returns last `window` readings with derived columns for live charts |
| `/api/history` | GET | Returns downsampled historical data for 1d / 7d / 30d range |
| `/api/history/export` | GET | Downloads historical data as CSV |
| `/api/ml/status` | GET | Returns training progress, record count, and model status |
| `/api/ml/train` | POST | Manually triggers model training (if training window is complete) |
| `/api/ml/live-score` | GET | Returns live anomaly score, health label, and thermal metrics |
| `/api/ml/anomaly-history` | GET | Returns scored anomaly history for the chart (last 80 readings) |
| `/api/ml/predict-history` | GET | Returns full ML prediction rows for historical analysis |
| `/api/ml/fault-diagnosis` | GET | Returns current fault type breakdown with scores |
| `/api/settings` | GET/POST | Read or update motor settings and alert thresholds |
| `/api/db/reset` | POST | Wipes DB and ML artifacts, resets to fresh state |

#### Background Maintenance Loop:

A **daemon thread** starts when Flask boots and runs every 60 seconds:

```
Every 60s:
  1. db.run_maintenance()        ← Clean database (delete >90d, downsample >7d)
  2. if model is_updating for >2 hours:
       ml_model.swap_staging_model()   ← Promote new model to live
  3. elif model last_trained >90 days ago:
       fetch last 30 days of data
       ml_model.train_isolation_forest(df, is_update=True)  ← Train in background
```

---

### 3.5 `data/data_visualization.py` — Presentation Layer

**Location:** `motor-platform-main/data/data_visualization.py`  
**Role:** Transforms Pandas DataFrames into structured JSON payloads the frontend JavaScript can directly consume.

| Function | Description |
|---|---|
| `live_payload(df)` | Formats last N readings into a series dict with `points`, `latest`, `label`, `unit`, `color` per parameter. Also includes `motor_on` flag and current timestamp. |
| `historical_payload(df)` | Downsamples to max 500 points for chart performance, computes `avg / min / max` stats per parameter for the stats panel. |
| `anomaly_payload(scored_df)` | Formats ML-scored DataFrame into `{time, score, health}` point arrays for the anomaly chart. |
| `to_csv_bytes(df)` | Serializes the DataFrame to UTF-8 CSV bytes for the export endpoint. |

**PARAM_META dictionary** defines the metadata for every display parameter: label, unit, color, and whether it is a raw sensor or a derived calculation.

---

### 3.6 `frontend/` — Dashboard UI

**Location:** `motor-platform-main/frontend/`  
**Files:** `index.html`, `style.css`, `app.js`  
**Role:** Single-page application for the operator dashboard.

**Pages:**
- **Live Monitoring** — Real-time parameter cards with SVG sparkline charts, motor on/off status pill, threshold alert highlighting. Polls `/api/live` every 2 seconds.
- **Historical Analytics** — Single-parameter time series chart + correlation scatter chart. Selectable 1d/7d/30d range.
- **Predictive Maintenance (ML)** — Training progress bar, live health score gauge, anomaly score time series (X-axis: time, Y-axis: score 0–1 with threshold lines), fault probability bar chart.
- **Thresholds** — Editable min/max thresholds for each sensor parameter, persisted to `settings.json`.
- **Settings** — Motor ID, location, alert email, poll interval.

---

### 3.7 `test.py` — Automated Test Suite

**Location:** `motor-platform-main/test.py`  
**Framework:** Python `unittest`  
**Role:** Validates all major backend components without manual testing.

| Test | What it validates |
|---|---|
| `test_01_db_insertion_and_fetch` | SQLite table creation, row insertion, and `fetch_latest()` retrieval |
| `test_02_feature_engineering_and_states` | `engineer_features()` correctly computes `is_ramp_up`, `is_steady`, `is_ramp_down` flags |
| `test_03_sensor_validation_failsafe` | `predict()` returns `"Sensor Fault"` when motor is on but sensors read 0 |
| `test_04_api_live_endpoint` | Flask `/api/live` returns HTTP 200 with correct JSON structure |
| `test_05_data_visualization_formatter` | `live_payload()` and CSV export produce valid output |
| `test_06_model_update_state` | `is_updating` flag persists correctly in `training_state.json` |

Run with: `python test.py`

---

## 4. ML Model Deep Dive

### Training Timeline

```
Day 0          Hour 1                  Month 1               Month 3
  │               │                       │                       │
  ▼               ▼                       ▼                       ▼
Start         Data window             Auto-train              Background
logging       complete →              Isolation Forest        retrain on
              train initial           on full DB data         last 30 days
              model                                           (staging model)
                                                              │
                                                          2-hour window
                                                              │
                                                          Swap to live
```

### Anomaly Score Interpretation

| Score Range | Health Label | Meaning |
|---|---|---|
| 0.00 – 0.34 | ✅ Healthy | Motor operating normally within baseline |
| 0.35 – 0.59 | ⚠️ Warning | Slight deviation, monitor closely |
| 0.60 – 0.79 | 🔴 Anomaly Detected | Significant deviation, investigate |
| 0.80 – 1.00 | 🆘 Critical Condition | Immediate attention required |

### What the Model Learns

The Isolation Forest trains on **normal operation data** (first 1 hour by default). It creates an internal tree structure that can efficiently identify readings that are isolated quickly — meaning they are far from the normal cluster. The model learns all three operating states (ramp-up, steady, ramp-down) as separate normal clusters.

---

## 5. Long-Term Stability Architecture

Three mechanisms ensure the platform runs reliably for years without manual intervention:

### 5.1 Rolling Model Retraining (Data Drift)
- Every 90 days, the background thread automatically triggers a new training run on the last 30 days of downsampled data.
- The new model trains into a **staging slot** — inference on the live model is uninterrupted.
- After 2 hours (an observation window), the staging model is atomically promoted to live.

### 5.2 Database Maintenance (Storage Bloat)
- **Purge:** Rows older than 90 days are permanently deleted.
- **Downsample:** Rows older than 7 days are averaged to 1-minute resolution using a `CREATE TEMP TABLE ... GROUP BY minute` SQL strategy.
- This keeps the database at a stable, bounded size even after years of 2-second logging.

### 5.3 Sensor Failure Detection
- Before every live inference, the reading is validated.
- If `motor_on = 1` (rpm > 5) but both `current = 0.0` AND `vibration = 0.0` simultaneously, a physical sensor disconnect is detected.
- Returns `"Sensor Fault"` immediately, preventing false anomaly alerts.

---

## 6. Sensor Schema

All data is stored in the `plc_tags` SQLite table with the following schema:

| Column | Type | Unit | Source |
|---|---|---|---|
| `ts` | TEXT (ISO 8601) | — | Timestamp |
| `set_rpm` | REAL | RPM | PLC MK10 — commanded speed |
| `rpm` | REAL | RPM | PLC MK18 — actual speed |
| `current` | REAL | A | PLC MK14 — motor current |
| `torque` | REAL | N·m | PLC MK22 — motor torque |
| `dc_voltage` | REAL | V | PLC MK26 — DC bus voltage |
| `temperature` | REAL | °C | PLC MK30 — motor temperature |
| `vibration` | REAL | mm/s | PLC MK104 — vibration sensor |

**Derived at query time (not stored):**

| Column | Formula |
|---|---|
| `motor_on` | `rpm > 5` |
| `power` | `dc_voltage × current / 1000` (kW) |
| `slip` | `(set_rpm − rpm) / set_rpm × 100` (%) |

---

*Report generated: 2026-06-29 | Motor Predictive Maintenance Platform — Phase 6*
