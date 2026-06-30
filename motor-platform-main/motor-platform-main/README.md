# Motor Predictive Maintenance Platform — Phase 6

## Structure

```
motor-platform/
├── app.py                      Flask backend — REST API, SQLite-backed
├── db.py                       SQLite data layer (replaces AWS/in-memory)
├── requirements.txt
├── ml/
│   └── ml_model.py             Isolation Forest + 9-fault rule-based diagnosis
├── data/
│   └── data_visualization.py   Chart-ready JSON payloads (real schema)
└── frontend/
    ├── index.html              Live / History / ML pages + account dropdown
    ├── style.css               Dark industrial theme + Phase 6 additions
    └── app.js                  Real-schema param cards, fault bars, settings
```

## Real sensor schema

`ts, rpm, current, torque, dc_voltage, temperature, vibration`

Derived at query time:
- `motor_on` = rpm > 5
- `power` = dc_voltage × current (labeled DERIVED in UI)
- `torque` is signed — negative = regenerative/braking

## Run locally

```bash
cd motor-platform
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000

## Seeding with real data

```python
import pandas as pd
import db

df = pd.read_csv("your_sensor_export.csv")
# columns: ts (or time), rpm, current, torque, dc_voltage, temperature, vibration
db.seed_from_dataframe(df)
```

## API endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/live` | latest reading + rolling window |
| `GET /api/history?range=1d\|7d\|30d` | historical series + stats |
| `GET /api/history/export?range=7d` | CSV download |
| `GET /api/ml/status` | training progress |
| `POST /api/ml/configure` `{"duration_days": 15}` | change training window |
| `POST /api/ml/train` | trigger Isolation Forest fit |
| `GET /api/ml/live-score` | score latest reading + fault diagnosis |
| `GET /api/ml/anomaly-history` | backtest recent data |
| `GET /api/ml/fault-diagnosis` | 7-fault breakdown for latest reading |
| `GET /api/settings` | get motor settings |
| `POST /api/settings` | update motor_id, location, alert_email, poll_interval_s |

## Fault types diagnosed

1. Bearing / Shaft wear
2. Overload
3. Coupling Slip
4. Voltage Supply anomaly
5. Winding Overcurrent
6. Thermal Overload
7. Mechanical Imbalance

## Known clock-skew handling

Training progress is anchored to the database's own latest timestamp,
not wall clock. If real deployment data has future-dated timestamps,
progress still computes correctly.

Motor
Vibration 
current
torque
temperature
voltage
set rpm
act rpm
machine on/off state 

OEE system level data come from plc 
fault time --> fault bit
run time --> run bit
stop time --> stop bit
will be add on the app as a saparate page for OEE
# Over any time window [t_start, t_end]:

planned_time   = shift_seconds_in_window          # from settings.json

actual_run     = COUNT(rows WHERE run_bit=1 AND fault_bit=0) × poll_interval
fault_time     = COUNT(rows WHERE fault_bit=1)    × poll_interval
total_run_time = COUNT(rows WHERE run_bit=1)      × poll_interval

availability   = actual_run / planned_time

performance    = AVG(rpm / set_rpm)               # filtered: run_bit=1, fault_bit=0, set_rpm > 0

quality        = actual_run / total_run_time      # proxy; or 1.0 if no defect signal

OEE            = availability × performance × quality


