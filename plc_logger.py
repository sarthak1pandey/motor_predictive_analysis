import snap7
from snap7.util import get_real
import sqlite3
import time
from datetime import datetime

PLC_IP = '192.168.2.5'
DB_PATH = r'C:\Dreamz Internship 2\final data w vibration temperature\mydb.db'

try:
    client = snap7.client.Client()
    client.connect(PLC_IP, 0, 0)
    print("PLC connected:", client.get_connected())
except Exception as e:
    print("Connection error:", e)
    exit()

db = sqlite3.connect(DB_PATH)
cur = db.cursor()

def read_real(start):
    data = client.read_area(snap7.types.Areas.MK, 0, start, 4)
    return get_real(data, 0)

print("Starting loop...")
while True:
    try:
        set_rpm     = read_real(10)
        rpm         = read_real(18)
        current     = read_real(14)
        torque      = read_real(22)
        dc_voltage  = read_real(26)
        temperature = read_real(30)
        vibration   = read_real(104)

        if rpm > 0:
            cur.execute('''INSERT INTO plc_tags 
                (ts, set_rpm, rpm, current, torque, dc_voltage, motor_state, vibration, temperature)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (datetime.now().isoformat(), set_rpm, rpm, current, torque, dc_voltage,
                 'on' if rpm > 10 else 'off', vibration, temperature))
            db.commit()
            print(f"{datetime.now()} | SetRPM:{set_rpm:.1f} | RPM:{rpm:.1f} | Current:{current:.2f} | Torque:{torque:.3f} | DC:{dc_voltage:.1f} | Vib:{vibration:.3f} | Temp:{temperature:.1f}")
    except Exception as e:
        print("Read error:", e)
    time.sleep(1)