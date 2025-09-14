# canary_ai/dashboard.py
import time
import queue
import json
import threading
import os
import traceback
from flask import Flask, render_template, Response, request, jsonify
from copy import deepcopy

# --- Shared State ---
shared_state = {
    "latest_metrics": {},
    "current_status": {
        "status": "INITIALIZING",
        "issues": [],
        "action": {"type": "NONE", "instructions": "Waiting for data..." },
        "active_dtcs": [], # List of {code, desc} dicts for UI
        "anomaly_score": -1.0, # Add placeholder for AI score
        "anomaly_detected": False
    },
    "state_lock": threading.Lock()
}
# Queue for SSE communication with browser clients
sse_queue = queue.Queue()
# Queue for DATA received from hardware via HTTP POST, passed to main.py
http_data_queue = None # IMPORTANT: Initialized and passed by main.py

# --- Expected API Key ---
EXPECTED_API_KEY = os.environ.get("DEVICE_API_KEY", "*****")

# --- Flask App Setup ---
app = Flask(__name__, template_folder='../templates') # Point to templates folder correctly
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'change-this-in-production-v9')

# --- Global references Set by main.py ---
analyzer_module = None # Used to access ISSUE_DEFINITIONS / DTC_DESCRIPTIONS
command_send_func = None # Needed for manual trigger placeholder if re-enabled

# --- State Update/Get Functions ---
def update_shared_state(payload_for_dashboard):
    """ Safely updates the display state and notifies SSE stream if changed. """
    global shared_state
    if not isinstance(payload_for_dashboard, dict): return # Ignore invalid updates
    with shared_state["state_lock"]:
        # Check if state actually changed before putting on queue
        if shared_state != payload_for_dashboard:
            shared_state = deepcopy(payload_for_dashboard) # Store a copy
            sse_payload = {"type": "update", "payload": payload_for_dashboard}
            try: sse_queue.put_nowait(sse_payload)
            except queue.Full: print("[SSE Warning] Update queue full!")

def get_shared_state():
    """ Safely retrieves a deep copy of the current display state. """
    with shared_state["state_lock"]:
        return deepcopy(shared_state)

# --- Flask Routes ---
@app.route('/')
def index():
    """ Serves the main index.html template. """
    try: return render_template('index.html')
    except Exception as e: print(f"[Dashboard Error] Render failed: {e}"); return "Error loading page.", 500

@app.route('/stream')
def stream():
    """ Server-Sent Events stream. Sends initial state then pushes updates. """
    def event_stream():
        try: # Send initial state immediately
            current_state = get_shared_state()
            init_payload = {"type": "initial_state", "payload": current_state}
            yield f"data: {json.dumps(init_payload)}\n\n"
            print("[SSE] Sent initial state to client.")
        except Exception as e: print(f"[SSE] Error sending initial state: {e}")
        # Listen for updates
        while True:
            try:
                update = sse_queue.get(timeout=45) # Block waiting for update
                yield f"data: {json.dumps(update)}\n\n"
            except queue.Empty: yield ": keepalive\n\n" # Send comment to keep connection alive
            except GeneratorExit: print("[SSE] Client disconnected."); break
            except Exception as e: print(f"[SSE] Stream Error: {e}"); traceback.print_exc(); break
    return Response(event_stream(), mimetype='text/event-stream')


# --- API Endpoint for Hardware Data (Structured JSON) ---
@app.route('/api/v1/vehicle_data', methods=['POST'])
def receive_vehicle_data():
    """ Receives {vin, speed[], rpm[], temp[], dtcs[]} via POST. Validates, queues. """
    global http_data_queue
    if not http_data_queue: return jsonify({"error": "Internal queue error"}), 500

    # 1. Auth & Type Check
    if request.headers.get("X-DEVICE-API-KEY") != EXPECTED_API_KEY: return jsonify({"error": "Unauthorized"}), 401
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 415

    # 2. Validate & Queue Data
    try:
        data = request.get_json()
        if not isinstance(data, dict): raise ValueError("Payload must be JSON object")
        required = ["vin", "speed", "rpm", "temp", "dtcs"]
        if not all(k in data and isinstance(data[k], list if k != 'vin' else str) for k in required):
             raise ValueError(f"Missing/invalid keys/types. Need: {required} (str,list,list,list,list)")

        # Put RAW received dictionary onto queue for main.py processing
        # No need to extract latest value here, main.py will handle processing payload
        http_data_queue.put(data, block=False)
        return jsonify({"status": "success", "message": "Data queued"}), 202

    except queue.Full: return jsonify({"error": "Server busy, queue full"}), 503
    except ValueError as e: return jsonify({"error": f"{e}"}), 400
    except Exception as e: print(f"[API Error] {e}"); traceback.print_exc(); return jsonify({"error": "Server processing error"}), 500

# --- Manual Heal Trigger (Commented Out/Non-functional in this mode) ---
# @app.route('/trigger_heal', methods=['POST'])
# def trigger_heal():
#     # global command_send_func, analyzer_module
#     # ... Logic would extract DTC, find heal_command in definitions ...
#     # ... Call command_send_func if available ...
#     return jsonify({"status": "info", "message": "Manual trigger N/A in HTTP mode"}), 501

# --- Run Flask App ---
def run_flask_app():
    host = "0.0.0.0"
    port = 5000
    print(f"[Dashboard] Starting Flask server...")
    print(f"      Dashboard URL -> http://127.0.0.1:{port}")
    print(f"      API Endpoint --> http://<Your-Computer-IP>:{port}/api/v1/vehicle_data")
    try: app.run(host=host, port=port, debug=False, use_reloader=False)
    except Exception as e: print(f"[Dashboard][FATAL] Server error: {e}")

if __name__ == "__main__":
     print("[Dashboard] Running standalone for UI testing.")
     http_data_queue = queue.Queue(maxsize=100) # Dummy queue for API to accept POSTs
     run_flask_app()
