import sqlite3
import time
import random
from datetime import datetime

DB_PATH = r"C:\Dreamz Internship 2\final data w vibration temperature\mydb.db"

def create_table_if_not_exists(conn):
    # Ensure the table is created with all required columns
    conn.execute('''
        CREATE TABLE IF NOT EXISTS plc_tags (
            ts TEXT, 
            set_rpm REAL, 
            rpm REAL, 
            current REAL, 
            torque REAL, 
            dc_voltage REAL, 
            temperature REAL, 
            vibration REAL,
            motor_state INTEGER,
            fault_bit INTEGER,
            run_bit INTEGER,
            stop_bit INTEGER
        )
    ''')
    
    # If the table already exists, let's make sure it has the newly requested columns
    cursor = conn.execute("PRAGMA table_info(plc_tags)")
    columns = [row[1] for row in cursor.fetchall()]
    
    new_columns = ["motor_state", "fault_bit", "run_bit", "stop_bit"]
    for col in new_columns:
        if col not in columns and len(columns) > 0:
            try:
                conn.execute(f"ALTER TABLE plc_tags ADD COLUMN {col} INTEGER DEFAULT 0")
                print(f"Added new column: {col}")
            except Exception as e:
                print(f"Error adding column {col}: {e}")
                
    conn.commit()

def generate_data():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    create_table_if_not_exists(conn)
    
    target_set_rpm = 300.0  # Constant running value
    current_act_rpm = target_set_rpm
    ramp_step = 15.0  # RPM change per 2-second cycle
    
    current_state = 'run'
    state_ticks = 0
    
    print("Starting data generation... Press Ctrl+C to stop.")
    try:
        while True:
            # Keep the state sticky so it stays stopped or running long enough to see the ramp
            if state_ticks <= 0:
                current_state = random.choices(['run', 'stop', 'fault'], weights=[0.85, 0.10, 0.05])[0]
                state_ticks = random.randint(15, 60) # hold state for 15 to 60 cycles (30s to 120s)
            else:
                state_ticks -= 1
                
            state = current_state
            
            run_bit = 1 if state == 'run' else 0
            stop_bit = 1 if state == 'stop' else 0
            fault_bit = 1 if state == 'fault' else 0
            motor_state = 1 if state in ['run', 'fault'] else 0
            
            # Target set_rpm drops to 0 immediately when stopped
            set_rpm = 0.0 if stop_bit == 1 else target_set_rpm
            
            # Smoothly ramp the actual rpm towards the target set_rpm
            if current_act_rpm < set_rpm:
                current_act_rpm += ramp_step
                if current_act_rpm > set_rpm:
                    current_act_rpm = set_rpm
            elif current_act_rpm > set_rpm:
                current_act_rpm -= ramp_step
                if current_act_rpm < set_rpm:
                    current_act_rpm = set_rpm
                    
            # Add small fluctuations only if we are at target and not stopped
            if current_act_rpm == target_set_rpm and stop_bit == 0:
                rpm = round(current_act_rpm + random.uniform(-2.0, 2.0), 1)
            else:
                rpm = round(current_act_rpm, 1)
                
            # Simulate other sensor values based on actual RPM
            if rpm <= 5.0 and stop_bit == 1:
                # Motor is fully stopped
                current = 0.0
                torque = 0.0
                vibration = round(random.uniform(0.0, 0.2), 3)
            else:
                # Motor is running or ramping down/up
                vibration = round(random.uniform(0.5, 3.5), 3)
                current = round(random.uniform(10.0, 50.0), 2)
                torque = round(random.uniform(20.0, 100.0), 2)
                
            temperature = round(random.uniform(30.0, 75.0), 1)
            voltage = round(random.uniform(530.0, 570.0), 1)
            
            ts = datetime.now().isoformat()
            
            # Insert the record into the database
            conn.execute('''
                INSERT INTO plc_tags (
                    ts, set_rpm, rpm, current, torque, dc_voltage, 
                    temperature, vibration, motor_state, fault_bit, run_bit, stop_bit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (ts, set_rpm, rpm, current, torque, voltage, temperature, vibration, motor_state, fault_bit, run_bit, stop_bit))
            
            conn.commit()
            
            print(f"[{ts}] State:{state.upper():<5} | RPM:{rpm:<6} (Set:{set_rpm}) | Curr:{current:<5} | Torq:{torque:<6} | Volt:{voltage:<5} | Temp:{temperature:<4} | Vib:{vibration:<5}")
            
            # Wait for 2 seconds (poll interval)
            time.sleep(2)
            
    except KeyboardInterrupt:
        print("\nStopped data generation.")
    finally:
        conn.close()

if __name__ == "__main__":
    generate_data()
