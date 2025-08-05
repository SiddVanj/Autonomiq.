# canary_ai/main.py (v6.1 + SyntaxError Fixes)
import time
import queue
import threading
import sys
# No Serial needed
import json
import os
import traceback
from collections import deque
from copy import deepcopy
import random # For AI simulation

# Configure logging
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(threadName)s] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import project modules
import analyzer
import dashboard
from self_healer import SelfHealingAgent # Still used for state

# --- Configuration ---
PROCESSING_INTERVAL = 0.5 # Check queue frequently
ANALYSIS_INTERVAL = 2.0   # Run full analysis every 2 seconds
MAX_QUEUE_SIZE = 500

# --- AI Anomaly Detection Setup ---
USE_AI_ANOMALY = True
SEQUENCE_LENGTH = 30
anomaly_threshold = 0.15
anomaly_model = None
metrics_buffer = deque(maxlen=SEQUENCE_LENGTH)
MODEL_FEATURE_ORDER = ['rpm', 'speed', 'temp']

# --- Global State ---
http_data_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE) # Data FROM dashboard endpoint
latest_metrics = {} # {'rpm': V, 'speed': V, 'temp': V}
active_dtcs = set() # Stores CURRENT actual DTCs {'P0123', ...} from hardware POST
current_vin = "UNKNOWN"
stop_event = threading.Event()
healer = None

# --- AI Anomaly Placeholders ---
def load_anomaly_model(path="deepmind_or_other_model.pkl"):
    global anomaly_model, USE_AI_ANOMALY
    if not USE_AI_ANOMALY: logger.info("AI Anomaly Detection is disabled."); return None
    logger.info(f"[AI] Loading anomaly model (Placeholder: {path})...")
    try:
        # --- REPLACE WITH YOUR ACTUAL MODEL LOADING ---
        anomaly_model = "SIMULATED_LOADED_MODEL"
        logger.info("[AI] Anomaly model loaded successfully (Simulated).")
        return anomaly_model
    except Exception as e:
        logger.error(f"[AI] FAILED to load model '{path}': {e}. Disabling AI anomaly detection.")
        USE_AI_ANOMALY = False; return None

def preprocess_sequence_for_ai(sequence_list, feature_order):
    if not USE_AI_ANOMALY or len(sequence_list) < SEQUENCE_LENGTH: return None
    logger.debug(f"[AI Pre] Preparing sequence len {len(sequence_list)}")
    try:
        import numpy as np
    except ImportError: logger.error("[AI Pre] Numpy needed!"); return None
    try:
        processed_data = []
        for metrics_dict in sequence_list:
            features = [float(metrics_dict.get(fname, 0.0)) for fname in feature_order]
            processed_data.append(features)
        num_array = np.array(processed_data, dtype=np.float32)
        # --- REPLACE WITH YOUR ACTUAL SCALING & RESHAPING ---
        scaled = num_array / 100.0 # Placeholder scaling
        return np.expand_dims(scaled, axis=0)
    except Exception as e: logger.error(f"[AI Pre] Error: {e}"); return None

def calculate_anomaly_score(input_sequence_tensor):
    """Gets anomaly score from the specific AI model."""
    global anomaly_model # Global model access

    # Check conditions first
    if not USE_AI_ANOMALY or anomaly_model is None or input_sequence_tensor is None:
        return 0.0

    # If conditions passed, log and proceed
    logger.debug("[AI Infer] Running inference...")
    try:
        # --- REPLACE WITH YOUR ACTUAL MODEL INFERENCE ---
        # score = anomaly_model.predict_anomaly_score(input_sequence_tensor) # Example call
        # Example Simulation:
        score = random.uniform(0.01, 0.1) + (random.uniform(0.1, 0.25) if random.random() < 0.08 else 0)
        score = min(score, 1.0)
        # if score > anomaly_threshold: logger.warning(f"[AI Infer] High Score Simulated: {score:.4f}")
        return score
    except Exception as e:
        logger.error(f"[AI Infer] Error: {e}")
        return 0.0 # Return default score on error

# --- Dummy command send function for Healer ---
def dummy_publish_command(payload_dict):
     logger.warning(f"[Healer Action] Intent: {payload_dict} (Cmd sending N/A)")
     return False

# --- Main Processing Loop ---
def processing_loop():
    global latest_metrics, active_dtcs, current_vin, healer, metrics_buffer

    if not healer: logger.warning("[Processor] Healer not initialized.")
    analysis_count = 0; last_analysis_time = time.time(); processed_payload_count_total = 0
    last_pushed_dashboard_state = {}
    # No longer need derived_analyzer_dtcs if AI Anomaly key added conditionally later

    while not stop_event.is_set():
        # --- <<< Initialize per-iteration flags >>> ---
        dtc_detection_requires_update = False
        metrics_updated_this_cycle = False
        dtcs_changed_this_cycle = False

        try:
            payload_processed_now = 0
            start_process_time = time.time()
            # --- 1. Process Incoming Data from HTTP Queue ---
            while time.time() - start_process_time < PROCESSING_INTERVAL: # Limit processing time slice
                try:
                    payload_data = http_data_queue.get(block=False)
                    processed_payload_count_total += 1; payload_processed_now += 1

                    # --- Process VIN ---
                    vin = payload_data.get("vin")
                    # --- <<< CORRECTED VIN Processing Indentation >>> ---
                    if vin and vin != current_vin:
                        current_vin = vin
                        logger.info(f"VIN updated: {vin}")
                    # --- <<< -------------------------------------- >>> ---

                    # --- Process Metrics & Update AI Buffer ---
                    reading_for_ai = {}; metrics_updated_now = False
                    for key in MODEL_FEATURE_ORDER:
                         if key in payload_data:
                              value = payload_data[key]
                              if value is not None: # Check if value is not None
                                 if latest_metrics.get(key) != value:
                                     latest_metrics[key] = value
                                     metrics_updated_now = True
                                 reading_for_ai[key] = value # Add to AI buffer only if value exists
                    if metrics_updated_now: metrics_updated_this_cycle = True
                    if len(reading_for_ai) == len(MODEL_FEATURE_ORDER): # Add ONLY if reading is complete
                         metrics_buffer.append(reading_for_ai)

                    # --- Process DTCs ---
                    new_dtcs_list = payload_data.get("dtcs", [])
                    new_dtcs_set = set(new_dtcs_list) if isinstance(new_dtcs_list, list) else set()
                    if new_dtcs_set != active_dtcs:
                        logger.info(f"[Processor] Actual DTC change: Old={active_dtcs}, New={new_dtcs_set}")
                        active_dtcs = new_dtcs_set # Update global set
                        dtcs_changed_this_cycle = True # Flag change happened in this cycle

                except queue.Empty: break # No more data this slice
                except json.JSONDecodeError as e: logger.error(f"JSON decode error in queue: {e}")
                except Exception as e: logger.error(f"Error handling queue msg: {e}"); traceback.print_exc()

            # Update dashboard intermediate if only metrics changed
            if metrics_updated_this_cycle and not dtcs_changed_this_cycle:
                current_dashboard_state = dashboard.get_shared_state()
                current_dashboard_state["latest_metrics"] = deepcopy(latest_metrics)
                if current_dashboard_state != last_pushed_dashboard_state:
                     dashboard.update_shared_state(current_dashboard_state)


            # Flag if DTCs changed for the upcoming analysis check
            if dtcs_changed_this_cycle: dtc_detection_requires_update = True


            # --- 2. Periodic Analysis Cycle ---
            current_time = time.time()
            if current_time - last_analysis_time >= ANALYSIS_INTERVAL:
                analysis_count += 1; last_analysis_time = current_time
                logger.info(f"--- Analysis Cycle #{analysis_count} ---")
                metrics_copy = deepcopy(latest_metrics)
                dtcs_copy = active_dtcs.copy()

                # a) AI Anomaly Detection
                current_anomaly_score = 0.0; anomaly_detected_ai = False
                if USE_AI_ANOMALY and len(metrics_buffer) >= SEQUENCE_LENGTH:
                     sequence = list(metrics_buffer); logger.debug(f"Running AI check (Buffer: {len(sequence)})")
                     preprocessed = preprocess_sequence_for_ai(sequence, MODEL_FEATURE_ORDER)
                     if preprocessed is not None: current_anomaly_score = calculate_anomaly_score(preprocessed)
                     anomaly_detected_ai = current_anomaly_score > anomaly_threshold
                     if anomaly_detected_ai: logger.warning(f"*** AI Anomaly! Score: {current_anomaly_score:.4f} ***")
                elif USE_AI_ANOMALY: logger.debug(f"AI Buffer Filling: {len(metrics_buffer)}/{SEQUENCE_LENGTH}")

                # *** Construct dtc set for analyzer ***
                analyzer_dtcs_input = dtcs_copy.copy()
                if anomaly_detected_ai: analyzer_dtcs_input.add("AI_ANOMALY")

                # b) Rule-Based Analysis
                vehicle_state = analyzer.analyze_vehicle_state(metrics_copy, analyzer_dtcs_input)

                # c) Add anomaly score to the result (it's not explicitly returned by analyze_vehicle_state)
                vehicle_state['anomaly_score'] = round(current_anomaly_score, 4) if USE_AI_ANOMALY else -1.0
                vehicle_state['anomaly_detected'] = anomaly_detected_ai
                # Add VIN if available
                vehicle_state['vin'] = current_vin

                # d) Log Healing Intent
                if healer: healer.initiate_heal(vehicle_state)

                # e) Prepare & Push FULL Dashboard State
                final_payload = { "metrics": metrics_copy, "status_info": vehicle_state }
                if final_payload != last_pushed_dashboard_state:
                    logger.info(f"Pushing Analysis Update: Status={vehicle_state.get('status', '?')}, DTCs={len(dtcs_copy)}, Anomaly={anomaly_detected_ai}")
                    dashboard.update_shared_state(final_payload)
                    last_pushed_dashboard_state = deepcopy(final_payload)
                else: logger.debug("Analysis complete, state unchanged.")

                dtc_detection_requires_update = False # Reset flag as full analysis included DTC state

            # If analysis didn't run, but DTCs changed, push intermediate state
            elif dtc_detection_requires_update:
                 logger.info("Pushing intermediate DTC update.")
                 intermediate_state = dashboard.get_shared_state()
                 intermediate_state["latest_metrics"] = deepcopy(latest_metrics)
                 # Regenerate description list for active DTCs
                 intermediate_state["current_status"]["active_dtcs"] = [{"code": code, "desc": analyzer.DTC_DESCRIPTIONS.get(code, "?")} for code in sorted(list(active_dtcs))]
                 # Keep existing status/action string from last analysis
                 if intermediate_state != last_pushed_dashboard_state:
                      dashboard.update_shared_state(intermediate_state)
                      # DO NOT update last_pushed_dashboard_state here
                 dtc_detection_requires_update = False

            time.sleep(0.05) # Yield CPU

        except KeyboardInterrupt: logger.info("Processor stopping."); stop_event.set(); break
        except Exception as e: logger.error(f"Processor loop ERROR: {e}"); traceback.print_exc(); time.sleep(1)

    logger.info("Processor loop finished.")


# --- Main Execution ---
if __name__ == "__main__":
    logger.info("[Main] Starting CANary AI Agent System (HTTP + Anomaly)...")

    # Load AI model placeholder
    anomaly_model = load_anomaly_model()
    # Instantiate Healer with dummy function
    healer = SelfHealingAgent(dummy_publish_command, analyzer.ISSUE_DEFINITIONS)

    # Inject dependencies into Dashboard module
    dashboard.http_data_queue = http_data_queue
    dashboard.analyzer_module = analyzer
    # command send func not passed as it's dummy now
    logger.info("[Main] Components initialized.")

    # Start Threads
    logger.info("[Main] Starting Processing & Dashboard Threads...")
    processing_thread = threading.Thread(target=processing_loop, daemon=True, name="ProcessingThread")
    processing_thread.start()
    flask_thread = threading.Thread(target=dashboard.run_flask_app, daemon=True, name="DashboardThread")
    flask_thread.start()

    logger.info("-" * 30)
    logger.info("[Main] System running. Waiting for POST to /api/v1/vehicle_data")
    logger.info("[Main] Access dashboard at http://localhost:5000")
    logger.info("[Main] Press Ctrl+C to exit.")
    logger.info("-" * 30)

    # Keep alive & handle shutdown
    try:
        while not stop_event.is_set():
            if not processing_thread.is_alive() or not flask_thread.is_alive():
                logger.critical("A worker thread terminated unexpectedly! Shutting down.")
                stop_event.set()
                break # Exit loop if threads die
            time.sleep(2)
    except KeyboardInterrupt: logger.info("\nShutdown signal.")
    finally:
        if not stop_event.is_set(): logger.info("Initiating shutdown..."); stop_event.set()
        logger.info("Waiting for threads...")
        processing_thread.join(timeout=2)
        logger.info("Shutdown complete.")
        sys.exit(0)
