# canary_ai/main.py
import time
import queue
import threading
import sys
# Removed Paho MQTT import
import json
import os
import traceback

# Import project modules
import decoder
import analyzer
import dashboard # Dashboard now receives data via HTTP
from self_healer import SelfHealingAgent # Agent is still useful, but CANNOT send commands easily
# Removed VERIFY_STATUS_FAILED as it's less relevant without command verification loop

# --- Configuration ---
PROCESSING_INTERVAL = 1.0
ANALYSIS_INTERVAL = 2.0
# REQUEST_INTERVAL Removed - Python backend no longer requests data
MAX_QUEUE_SIZE = 500
# MQTT Configuration REMOVED

# --- Global State ---
# Queue for DATA received by Flask endpoint from HTTP POSTs
http_data_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
latest_metrics = {}
active_dtcs = set()
stop_event = threading.Event()
# serial_connection REMOVED
healer = None

# --- <<< MQTT/SERIAL Setup and Command Functions REMOVED >>> ---

# --- Main Processing Loop (Gets data from internal queue) ---
def processing_loop():
    global latest_metrics, active_dtcs, healer

    # Healer is less functional now, as it cannot send commands easily
    # You might repurpose it or simplify it significantly.
    # For now, we'll keep it but comment out command sending parts.
    if not healer:
        print("[Processor] Warning: SelfHealingAgent not initialized (less effective in HTTP mode).")
        # Don't exit, analysis is still useful

    last_analysis_time = time.time()
    processed_msg_count = 0
    analysis_count = 0

    while not stop_event.is_set():
        try:
            # --- 1. Process Incoming Data from HTTP POST Queue ---
            # (This queue is filled by the /api/v1/can_data endpoint in dashboard.py)
            can_data_processed_this_cycle = 0
            metrics_updated_this_cycle = False
            dtcs_changed_this_cycle = False
            start_process_time = time.time()

            # Process messages from the internal queue
            while time.time() - start_process_time < PROCESSING_INTERVAL :
                try:
                    # <<< Get data from the HTTP queue >>>
                    payload_str = http_data_queue.get(block=False)
                    processed_msg_count += 1
                    can_data_processed_this_cycle += 1

                    msg_data = json.loads(payload_str)
                    can_id = msg_data.get("id")
                    can_bytes_list = msg_data.get("data")

                    if can_id is not None and can_bytes_list is not None:
                        data_bytes = bytes(can_bytes_list)
                        decoded_data = decoder.decode_message(can_id, data_bytes)

                        if decoded_data:
                            for name, metric_info in decoded_data.get('metrics', {}).items():
                                if name not in latest_metrics or latest_metrics[name]['value'] != metric_info['value']:
                                     latest_metrics[name] = metric_info
                                     metrics_updated_this_cycle = True

                            new_dtcs = set(decoded_data.get('dtcs', []))
                            added_dtcs = new_dtcs - active_dtcs
                            if added_dtcs:
                                print(f"[Processor] New DTCs detected: {added_dtcs}")
                                active_dtcs.update(added_dtcs)
                                dtcs_changed_this_cycle = True
                            # TODO: How to detect DTC removal if ESP only POSTs data?
                            # Requires ESP to explicitly POST a "no DTCs" message.

                except queue.Empty:
                    break # No more data in queue right now
                except json.JSONDecodeError: print(f"[Processor] JSON decode error from HTTP queue: {payload_str}")
                except Exception as e: print(f"[Processor] Error handling HTTP queue msg: {e}")

            # Update dashboard metrics if they changed
            if metrics_updated_this_cycle:
                current_dashboard_state = dashboard.get_shared_state()
                dashboard.update_shared_state(metrics=latest_metrics.copy(), status=current_dashboard_state['current_status'])

            if dtcs_changed_this_cycle:
                dtc_detection_requires_update = True # Flag for analysis cycle


            # --- 2. Periodic Analysis ---
            current_time = time.time()
            if current_time - last_analysis_time >= ANALYSIS_INTERVAL:
                analysis_count += 1
                print(f"--- Analysis Cycle #{analysis_count} ({processed_msg_count} msgs processed) ---")
                last_analysis_time = current_time
                metrics_copy = latest_metrics.copy()
                dtcs_copy = active_dtcs.copy()

                # --- Healer checks/verification are mostly moot if we can't send commands back easily ---
                # verification_results = healer.check_verification(metrics_copy, dtcs_copy)

                # Analyze Current State
                vehicle_state = analyzer.analyze_vehicle_state(metrics_copy, dtcs_copy)

                # --- Attempt to Initiate Heal - WILL FAIL TO SEND COMMAND ---
                # heal_status = healer.initiate_heal(vehicle_state) # Agent can't send commands
                # Log that a heal *would* be attempted if command path existed
                action_info = vehicle_state.get("action", {})
                issue_key = action_info.get("key")
                if action_info.get("action_type") == "SELF_HEAL_ATTEMPT" and issue_key and action_info.get("self_heal_possible"):
                      if healer and healer._can_attempt_heal(issue_key): # Check cooldown
                          print(f"[Processor] NOTE: Self-heal required for '{issue_key}', but cannot send command via HTTP.")
                          # Optionally record that we wanted to heal:
                          # healer.heal_last_initiated_time[issue_key] = time.time()

                # Update Dashboard with analysis result (without heal command effects)
                dashboard.update_shared_state(metrics=metrics_copy, status=vehicle_state)
                dtc_detection_requires_update = False

            elif dtc_detection_requires_update:
                 # Update dashboard status part if only DTCs changed
                 current_dashboard_state = dashboard.get_shared_state()
                 current_dashboard_state['current_status']['active_dtcs'] = sorted(list(active_dtcs))
                 dashboard.update_shared_state(metrics=latest_metrics.copy(), status=current_dashboard_state['current_status'])
                 dtc_detection_requires_update = False


            time.sleep(0.1) # Small sleep to yield CPU

        except KeyboardInterrupt: print("[Processor] Stopping."); stop_event.set(); break
        except Exception as e:
            print(f"[Processor] UNEXPECTED ERROR in loop: {e}")
            traceback.print_exc(); time.sleep(1)

    print("[Processor] Loop finished.")

# --- Main Execution ---
if __name__ == "__main__":
    print("[Main] Starting CANary AI Agent System (HTTP Mode)...")

    # --- MQTT/Serial Setup REMOVED ---

    # Instantiate the SelfHealingAgent - Pass None for publish func or a placeholder lambda
    # It won't be able to actually send commands now.
    # healer = SelfHealingAgent(lambda payload: print(f"[Healer-WARN] Tried to send {payload}, but no publish func."), analyzer.ISSUE_DEFINITIONS)
    # Or initialize it as None and handle checks in processing_loop
    healer = None
    print("[Main] NOTE: Self-Healing agent actions are disabled in HTTP-only mode.")


    # --- Dependency Injection for Dashboard ---
    # Dashboard needs the data queue and the analyzer module
    dashboard.http_data_queue = http_data_queue # Pass the queue
    dashboard.analyzer_module = analyzer
    # dashboard.healer_instance = healer # Less useful now
    # dashboard.mqtt_publish_func = None # Not needed
    print("[Main] Components initialized.")
    print("-" * 30)

    # Start the main processing thread (handles data FROM the queue)
    print("[Main] Starting Processing Thread...")
    processing_thread = threading.Thread(target=processing_loop, daemon=True, name="ProcessingThread")
    processing_thread.start()

    # Start the Flask dashboard thread (handles incoming HTTP requests)
    print("[Main] Starting Dashboard Thread...")
    flask_thread = threading.Thread(target=dashboard.run_flask_app, daemon=True, name="DashboardThread")
    flask_thread.start()
    print("-" * 30)
    print("[Main] System running... Waiting for data via HTTP POST to /api/v1/can_data")
    print("[Main] Access dashboard at http://localhost:5000 (or your IP)")
    print("[Main] Press Ctrl+C to exit.")
    print("-" * 30)


    try:
        while not stop_event.is_set():
            if not processing_thread.is_alive():
                 print("[Main] FATAL Error: Processing thread died!")
                 stop_event.set()
            if not flask_thread.is_alive():
                 print("[Main] FATAL Error: Flask dashboard thread died!")
                 stop_event.set()
            time.sleep(2)
    except KeyboardInterrupt: print("\n[Main] Shutdown signal received.")
    finally:
        if not stop_event.is_set(): print("[Main] Initiating shutdown..."); stop_event.set()
        # No MQTT client to stop
        print("[Main] Waiting for processing thread...")
        processing_thread.join(timeout=2)
        print("[Main] Shutdown complete.")
        sys.exit(0)
