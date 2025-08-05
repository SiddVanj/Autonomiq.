# canary_ai/dashboard.py
import time
import queue
import json
import threading
import os
from flask import Flask, render_template, Response, request, jsonify # <<< Ensure request, jsonify imported

# --- Shared State ---
shared_state = {
    "latest_metrics": {},
    "current_status": { "status": "INITIALIZING", "issues": [], "action": {"type": "NONE", "instructions": "Waiting for data..."} },
    "state_lock": threading.Lock()
}
sse_queue = queue.Queue()
# --- Queue for received HTTP data ---
http_data_queue = None # This will be set by main.py

# --- Flask App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'default-insecure-key')

# --- Global references Set by main.py ---
# serial_send_func REMOVED
# mqtt_publish_func REMOVED
analyzer_module = None    # Still needed for manual trigger logic? Maybe not.

# --- State Update/Get Functions --- (No changes needed)
def update_shared_state(metrics=None, status=None): # ... (implementation unchanged) ...
def get_shared_state(): # ... (implementation unchanged) ...


# --- Flask Routes ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/stream')
def stream(): # ... (event_stream implementation unchanged) ...

# --- <<< NEW HTTP ENDPOINT FOR RECEIVING DATA >>> ---
@app.route('/api/v1/can_data', methods=['POST'])
def receive_can_data():
    """Receives CAN data JSON payload via HTTP POST from the hardware."""
    global http_data_queue
    if not http_data_queue:
        print("[API] Error: HTTP Data Queue not initialized.")
        return jsonify({"status": "error", "message": "Internal server error (queue missing)"}), 500

    if not request.is_json:
        return jsonify({"status": "error", "message": "Request body must be JSON"}), 400

    try:
        data_json = request.get_json()
        # print(f"[API] Received POST data: {data_json}") # Verbose log

        # Basic validation (can add more)
        if not isinstance(data_json, dict) or 'id' not in data_json or 'data' not in data_json:
             return jsonify({"status": "error", "message": "Invalid JSON format"}), 400

        # --- Put RAW JSON string onto the queue for processing_loop ---
        # This keeps the API handler lightweight
        http_data_queue.put(json.dumps(data_json), block=False)

        return jsonify({"status": "success", "message": "Data queued for processing"}), 202 # Accepted

    except queue.Full:
        print("[API] Warning: Processing queue full. Rejecting data.")
        return jsonify({"status": "error", "message": "Server busy, queue full"}), 503 # Service Unavailable
    except Exception as e:
        print(f"[API] Error processing /api/v1/can_data: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": "Internal server error"}), 500
# --- <<< END NEW HTTP ENDPOINT >>> ---


# --- Trigger Heal Route (REMOVED / DISABLED) ---
# Without a way to easily command the ESP, this isn't useful.
# @app.route('/trigger_heal', methods=['POST'])
# def trigger_heal():
#     return jsonify({"status": "error", "message": "Manual heal triggers disabled in HTTP mode"}), 405 # Method Not Allowed


# --- Run Flask App Function ---
def run_flask_app():
    print(f"[Dashboard] Starting Flask server (HTTP Mode) - POST endpoint at /api/v1/can_data")
    print(f"[Dashboard] Accessible on http://<Your-IP-Address>:5000")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# --- Standalone Test ---
if __name__ == "__main__": # ... (implementation unchanged) ...
