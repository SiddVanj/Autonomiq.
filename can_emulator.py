# canary_ai/can_emulator.py
import time
import random
import queue
import threading

# --- Constants ---
# Approximate average iterations per second (based on sleep time)
# avg_sleep = (0.01 + 0.05) / 2 = 0.03s -> ~33 iterations/sec
ITERATIONS_PER_SECOND_ESTIMATE = 30

# Target normal operation time before fault injection (in seconds)
NORMAL_OPERATION_SECONDS = 25 # Let's make it quite long for "gradual"

# Target fault duration (in seconds)
FAULT_DURATION_SECONDS = 10

# Calculate cycle thresholds
FAULT_INJECTION_THRESHOLD = NORMAL_OPERATION_SECONDS * ITERATIONS_PER_SECOND_ESTIMATE
FAULT_DURATION_THRESHOLD = FAULT_DURATION_SECONDS * ITERATIONS_PER_SECOND_ESTIMATE


# Simulate CAN IDs (replace with actual IDs from your emulator/car)
# Example IDs: Engine parameters, Transmission, Body Control Module, ABS
CAN_IDS = {
    0x1F4: 'ENGINE_PARAMS_1', # Example: RPM, Speed
    0x2A0: 'ENGINE_PARAMS_2', # Example: Temp, Fuel Level
    0x3B4: 'TRANSMISSION',
    0x4C1: 'BCM',           # Body Control Module
    0x5D2: 'ABS_STATUS',    # Anti-lock Braking System
    0x7DF: 'DIAGNOSTIC_REQUEST', # Standard OBD-II request
    0x7E8: 'DIAGNOSTIC_RESPONSE' # Standard OBD-II response (contains DTCs sometimes)
}

# Simulate Diagnostic Trouble Codes (DTCs)
DTCS = [
    "P0128", # Coolant Thermostat (Coolant Temperature Below Thermostat Regulating Temperature)
    "P0301", # Cylinder 1 Misfire Detected
    "U0100", # Lost Communication With ECM/PCM "A"
    "C0035", # Left Front Wheel Speed Sensor Circuit Malfunction (Example ABS)
]

class CANEmulator:
    def __init__(self, message_queue):
        self.message_queue = message_queue
        self._running = False
        self._thread = None
        # --- Simulation State ---
        self.rpm = 800
        self.speed = 0
        self.engine_temp = 40.0
        self.fuel_level = 75.0
        self.active_dtc = None
        self.fault_active = False
        # --- Fault Timing Control ---
        # Timer counts cycles *since the last fault event ended* or start
        self.cycles_since_fault_cleared = 0
        # Timer counts cycles *since the current fault started*
        self.cycles_since_fault_start = 0
        # ------------------------

    def _generate_message(self):
        """Generates a single simulated CAN message (id, data_bytes)."""
        can_id = random.choice(list(CAN_IDS.keys()))
        data = bytearray(random.getrandbits(8 * 8).to_bytes(8, 'little')) # 8 random bytes

        # --- Override with simulated values ---
        if can_id == 0x1F4: # ENGINE_PARAMS_1
            self.rpm += random.randint(-50, 50)
            self.rpm = max(600, min(4000, self.rpm))
            self.speed = max(0, int(self.rpm / 40) + random.randint(-2, 2))
            rpm_encoded = int(self.rpm / 0.25)
            data[0] = rpm_encoded & 0xFF
            data[1] = (rpm_encoded >> 8) & 0xFF
            data[2] = min(255, self.speed)

        elif can_id == 0x2A0: # ENGINE_PARAMS_2
            if not self.fault_active or self.active_dtc != "P0128":
                 self.engine_temp += random.uniform(0.05, 0.2)
                 self.engine_temp = min(95.0, self.engine_temp)
            else: # Simulate overheating if P0128 "fault" is active
                 self.engine_temp += random.uniform(0.5, 1.5)
                 self.engine_temp = min(125.0, self.engine_temp)

            self.fuel_level -= random.uniform(0.005, 0.01)
            self.fuel_level = max(0.0, self.fuel_level)
            temp_encoded = int(self.engine_temp) + 40
            data[0] = min(255, temp_encoded)
            fuel_encoded = int(self.fuel_level * 2.55)
            data[1] = min(255, fuel_encoded)

        elif can_id == 0x7E8 and self.active_dtc: # DIAGNOSTIC_RESPONSE
            if self.active_dtc == "P0128":
                data[0] = 0x02; data[1] = 0x01; data[2] = 0x01; data[3] = 0x28
            elif self.active_dtc == "P0301":
                 data[0] = 0x02; data[1] = 0x01; data[2] = 0x03; data[3] = 0x01
            for i in range(4, 8): data[i] = 0x00
        # ------------------------------------

        return (can_id, bytes(data)) # Return as tuple (int, bytes)

    def _run(self):
        """Internal thread function to continuously generate messages."""
        while self._running:
            # --- Fault Injection Logic ---
            if not self.fault_active:
                self.cycles_since_fault_cleared += 1
                # Check if it's time to inject a new fault
                if self.cycles_since_fault_cleared > FAULT_INJECTION_THRESHOLD:
                    self.fault_active = True
                    self.active_dtc = random.choice(["P0128", "P0301"])
                    self.cycles_since_fault_start = 0 # Reset fault duration timer
                    self.cycles_since_fault_cleared = 0 # Reset normal operation timer
                    print(f"[Emulator] Injecting fault, active DTC: {self.active_dtc} (after ~{NORMAL_OPERATION_SECONDS}s normal)")
            else: # Fault is currently active
                 self.cycles_since_fault_start += 1
                 # Check if the fault duration has elapsed
                 if self.cycles_since_fault_start > FAULT_DURATION_THRESHOLD:
                      print(f"[Emulator] Fault duration ({FAULT_DURATION_SECONDS}s) elapsed for {self.active_dtc}. Clearing condition internally.")
                      self.clear_fault() # Fault condition clears itself after duration
            # -----------------------------

            message = self._generate_message()
            try:
                self.message_queue.put(message, timeout=0.1)
            except queue.Full:
                print("[Emulator] Warning: Message queue full, dropping message.")
                pass

            time.sleep(random.uniform(0.01, 0.05)) # Keep the same message rate

    def start(self):
        """Starts the emulator thread."""
        if not self._running:
            self._running = True
            # Reset timers on start
            self.cycles_since_fault_cleared = 0
            self.cycles_since_fault_start = 0
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            print("[Emulator] Started.")

    def stop(self):
        """Stops the emulator thread."""
        self._running = False
        if self._thread:
            self._thread.join()
        print("[Emulator] Stopped.")

    def clear_fault(self):
        """
        Clears the active fault condition.
        Can be called externally (e.g., by analyzer heal command)
        or internally when fault duration expires.
        """
        if self.fault_active:
            print(f"[Emulator] Fault condition for {self.active_dtc} is being cleared.")
            current_dtc = self.active_dtc # Store before clearing
            self.fault_active = False
            self.active_dtc = None
            self.cycles_since_fault_start = 0 # Reset fault duration timer
            self.cycles_since_fault_cleared = 0 # IMPORTANT: Reset normal timer *now*

            # Reset potentially affected values (e.g., temp starts cooling down)
            if current_dtc == "P0128":
                self.engine_temp = max(90.0, self.engine_temp - 10.0) # Start cooling


# Example usage (if run directly)
if __name__ == "__main__":
    q = queue.Queue(maxsize=100)
    emu = CANEmulator(q)
    emu.start()
    print(f"Emulator configured with: Normal Time ~{NORMAL_OPERATION_SECONDS}s, Fault Duration ~{FAULT_DURATION_SECONDS}s")
    try:
        # Run for a while to observe
        time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        emu.stop()