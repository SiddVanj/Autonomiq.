# canary_ai/can_emulator.py
# Keep the previous version which includes the last_cleared_dtc_internal attribute
# Ensure NORMAL_OPERATION_SECONDS = 5
import time
import random
import queue
import threading
import math

try:
    import analyzer
    SIMULATED_DTCS = list(analyzer.DTC_DESCRIPTIONS.keys())
    if not SIMULATED_DTCS: SIMULATED_DTCS = ["P0128", "P0301"]
    print(f"[Emulator] Will simulate DTCs based on analyzer.py: {SIMULATED_DTCS}")
except Exception as e:
     print(f"[Emulator] WARNING: Error importing analyzer/DTCs: {e}. Using fallback.")
     SIMULATED_DTCS = ["P0128", "P0301"]

ITERATIONS_PER_SECOND_ESTIMATE = 30
NORMAL_OPERATION_SECONDS = 5
FAULT_DURATION_SECONDS = 15
FAULT_INJECTION_THRESHOLD = NORMAL_OPERATION_SECONDS * ITERATIONS_PER_SECOND_ESTIMATE
FAULT_DURATION_THRESHOLD = FAULT_DURATION_SECONDS * ITERATIONS_PER_SECOND_ESTIMATE
RELEVANT_CAN_IDS = { 37: 'ENGINE_STATE', 180: 'SPEED', 380: 'ENGINE_RPM' }
DIAGNOSTIC_RESPONSE_ID = 0x7E8

class CANEmulator:
    def __init__(self, message_queue):
        self.message_queue = message_queue; self._running = False; self._thread = None
        self.engine_rpm = 800.0; self.vehicle_speed = 0.0; self.coolant_temp = 40.0
        self._target_rpm = 800.0; self._target_speed = 0.0; self._accel_rate = 150.0
        self._decel_rate = 200.0; self._speed_factor = 0.05
        self.active_dtc = None; self.fault_active = False
        self.cycles_since_fault_cleared = 0; self.cycles_since_fault_start = 0
        self.last_cleared_dtc_internal = None

    def _update_simulation_state(self):
        # Update RPM with randomization
        if self.engine_rpm < 700: self._target_rpm = random.uniform(750, 850)
        elif self.engine_rpm > 3500: self._target_rpm = random.uniform(1500, 2500)
        else:
             if random.random() < 0.1: self._target_rpm = random.uniform(750, 3500)
        if self.engine_rpm < self._target_rpm: self.engine_rpm += self._accel_rate * random.uniform(0.5, 1.0)
        elif self.engine_rpm > self._target_rpm: self.engine_rpm -= self._decel_rate * random.uniform(0.5, 1.0)
        self.engine_rpm += random.uniform(-20, 20); self.engine_rpm = max(600.0, min(4500.0, self.engine_rpm))
        
        # Update speed with more variability to ensure non-zero values
        self._target_speed = self.engine_rpm * self._speed_factor * random.uniform(0.8, 1.2)
        self.vehicle_speed += (self._target_speed - self.vehicle_speed) * 0.2  # Increased acceleration factor
        self.vehicle_speed = max(5.0, self.vehicle_speed)  # Ensure minimum speed of 5.0
        
        # Temperature simulation
        base_temp_increase = 0.1 + (self.engine_rpm / 10000.0)
        if not self.fault_active or self.active_dtc != "P0128":
            self.coolant_temp += base_temp_increase * random.uniform(0.5, 1.5)
            if self.coolant_temp > 95.0: self.coolant_temp -= random.uniform(0.0, 0.2)
            self.coolant_temp = min(98.0, self.coolant_temp)
        else: self.coolant_temp += random.uniform(0.5, 1.5); self.coolant_temp = min(125.0, self.coolant_temp)
        self.coolant_temp = max(30.0, self.coolant_temp)

    def _encode_message_data(self, can_id):
        data = bytearray(8)
        try:
            if can_id == 37: # STEER_ANGLE_SENSOR message
                temp_val = int(max(0, min(255, self.coolant_temp)))
                data[6] = temp_val
                
                # Fix steering angle data encoding - use simple 0-180 degree range
                steer_angle = int(90 + 30 * math.sin(time.time()))  # 60-120 degree range
                steer_rate = int(5 * math.cos(time.time()))  # -5 to +5 deg/s range
                
                # Properly encode as unsigned values
                data[0] = steer_angle & 0xFF  # LSB
                data[1] = 0  # MSB (keeping value under 255 for simplicity)
                data[2] = (steer_rate + 128) & 0xFF  # Offset to make unsigned (0-255)
                
            elif can_id == 180: # SPEED message
                # Ensure speed value is properly encoded and non-zero
                speed_value = max(15.0, self.vehicle_speed)  # Enforce minimum display speed
                speed_raw = int(speed_value * 100)  # Convert to proper scale factor
                data[3] = speed_raw & 0xFF  # LSB
                data[4] = (speed_raw >> 8) & 0xFF  # MSB
                
            elif can_id == 380: # ENGINE_RPM message
                rpm_val = int(max(0, self.engine_rpm))
                data[0] = rpm_val & 0xFF
                data[1] = (rpm_val >> 8) & 0xFF
                
            elif can_id == DIAGNOSTIC_RESPONSE_ID and self.active_dtc:
                code = self.active_dtc; prefix_map = {'P': 0x00, 'C': 0x40, 'B': 0x80, 'U': 0xC0}
                prefix_val = 0x00; num_part_str = ""
                if code and len(code) > 1: prefix = code[0].upper(); prefix_val = prefix_map.get(prefix, 0x00); num_part_str = code[1:]
                byte2 = prefix_val; byte3 = 0x00
                if len(num_part_str) == 4:
                    try: byte2 = prefix_val | int(num_part_str[0:2], 16); byte3 = int(num_part_str[2:4], 16)
                    except ValueError: pass
                data[0]=0x02; data[1]=0x01; data[2]=byte2; data[3]=byte3
        except Exception as e: print(f"[Emulator] Error encoding ID {hex(can_id)}: {e}"); return bytearray(8)
        return data

    def _generate_message(self):
        possible_ids = list(RELEVANT_CAN_IDS.keys())
        if self.fault_active and self.active_dtc:
            if random.random() < 0.2: can_id = DIAGNOSTIC_RESPONSE_ID
            else: can_id = random.choice(possible_ids)
        else: can_id = random.choice(possible_ids)
        data_bytes = self._encode_message_data(can_id)
        return (can_id, bytes(data_bytes))

    def _run(self):
        while self._running:
            self._update_simulation_state()
            self.last_cleared_dtc_internal = None
            if not self.fault_active:
                self.cycles_since_fault_cleared += 1
                if self.cycles_since_fault_cleared > FAULT_INJECTION_THRESHOLD:
                    self.fault_active = True; self.active_dtc = random.choice(SIMULATED_DTCS) if SIMULATED_DTCS else None
                    self.cycles_since_fault_start = 0; self.cycles_since_fault_cleared = 0
                    if self.active_dtc: print(f"\n[Emulator] === Injecting fault: {self.active_dtc} ===\n")
            else:
                 self.cycles_since_fault_start += 1
                 if self.cycles_since_fault_start > FAULT_DURATION_THRESHOLD:
                      print(f"\n[Emulator] === Fault Duration Expired ({FAULT_DURATION_SECONDS}s) for {self.active_dtc} ===\n")
                      self.clear_fault()
            message = self._generate_message()
            try: self.message_queue.put(message, timeout=0.1)
            except queue.Full: pass
            time.sleep(random.uniform(0.01, 0.05))

    def start(self):
        if not self._running:
            print("[Emulator] Starting..."); self._running = True; self.engine_rpm = 800.0; self.vehicle_speed = 0.0; self.coolant_temp = 40.0
            self.active_dtc = None; self.fault_active = False; self.cycles_since_fault_cleared = 0; self.cycles_since_fault_start = 0
            self._target_rpm = 800.0; self._target_speed = 0.0; self.last_cleared_dtc_internal = None
            self._thread = threading.Thread(target=self._run, daemon=True); self._thread.start()
            print(f"[Emulator] Started. Normal op: ~{NORMAL_OPERATION_SECONDS}s, Fault duration: ~{FAULT_DURATION_SECONDS}s.")

    def stop(self):
        if self._running: print("[Emulator] Stopping..."); self._running = False;
        if self._thread and self._thread.is_alive(): self._thread.join(timeout=1.0); print("[Emulator] Stopped.")

    def clear_fault(self):
        if self.fault_active:
            dtc_that_was_active = self.active_dtc; print(f"[Emulator] --- Clearing internal fault state for {dtc_that_was_active} ---")
            self.fault_active = False; self.active_dtc = None; self.last_cleared_dtc_internal = dtc_that_was_active # STORE CLEARED CODE
            self.cycles_since_fault_start = 0; self.cycles_since_fault_cleared = 0
            if dtc_that_was_active == "P0128": self.coolant_temp = max(90.0, self.coolant_temp - 5.0)

if __name__ == "__main__": pass