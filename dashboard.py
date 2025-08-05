# canary_ai/dashboard.py
# No changes required from the previous refactored version that expected a single 'update' type.

import queue
import json
import threading
from flask import Flask, render_template, Response, request, jsonify
from copy import deepcopy
import analyzer  # Import analyzer module at file level

# Import the scheduling agent and marketplace agent
from utils.LLMOps.scheduler import SchedulingAgent
from utils.LLMOps.marketplace import MarketplaceAgent
from utils.LLMOps.diy_repair import DIYRepairAgent

# --- Shared State (Internal - only for get_shared_state) ---
internal_shared_state = {
    "latest_metrics": {},
    "current_status": { "status": "INITIALIZING", "issues": [], "action": {"type": "NONE"}, "active_dtcs": [], "resolved_dtcs_this_cycle": [] },
    "state_lock": threading.Lock()
}
sse_queue = queue.Queue()

# --- Flask App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here_final_revised_v7' # Increment secret

# --- Global references ---
database_reader = None
database_reader_ref = None # Add a separate reference variable
analyzer_module = analyzer  # Set analyzer_module reference
scheduler_instance = SchedulingAgent()
marketplace_instance = MarketplaceAgent()
diy_repair_instance = DIYRepairAgent()

# --- update_shared_state handles COMBINED updates ---
def update_shared_state(full_state_payload):
    """
    Queues the complete state payload prepared by main.py under 'update' type.
    Also updates internal state mainly for initialization purposes.
    :param full_state_payload: Dict containing {'metrics': {...}, 'status_info': {...}}
    """
    if not isinstance(full_state_payload, dict): return

    # Update internal state using deepcopy to prevent aliasing
    with internal_shared_state["state_lock"]:
        if 'metrics' in full_state_payload: internal_shared_state["latest_metrics"] = deepcopy(full_state_payload['metrics'])
        if 'status_info' in full_state_payload: internal_shared_state["current_status"] = deepcopy(full_state_payload['status_info'])

    # Queue the single combined payload
    sse_payload = { "type": "update", "payload": deepcopy(full_state_payload) }
    sse_queue.put(sse_payload)

def get_shared_state():
    """Safely get a deep copy of the internal shared state DATA (for main.py init)."""
    with internal_shared_state["state_lock"]:
        # Explicitly copy only the data dictionaries using deepcopy
        state_data_copy = {
            "latest_metrics": deepcopy(internal_shared_state["latest_metrics"]),
            "current_status": deepcopy(internal_shared_state["current_status"])
        }
        return state_data_copy

# --- Flask Routes ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/stream')
def stream():
    """SSE stream for combined updates."""
    def event_stream():
        while True:
            try: update = sse_queue.get(timeout=60); yield f"data: {json.dumps(update)}\n\n"
            except queue.Empty: yield ": keepalive\n\n"
            except GeneratorExit: print("[SSE] Client disconnected"); break
            except Exception as e: print(f"[SSE] Error: {e}"); break
    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/trigger_heal', methods=['POST'])
def trigger_heal():
    """Endpoint for dashboard button to request self-heal for a specific DTC."""
    global database_reader, database_reader_ref, analyzer_module
    
    # Ensure database_reader is correctly referencing database_reader_ref
    if database_reader_ref and not database_reader:
        database_reader = database_reader_ref
        print("[Dashboard] Set database_reader from database_reader_ref")
    
    data = request.get_json(); issue_key = data.get('issue_key'); dtc_code = data.get('dtc_code')
    if not issue_key and dtc_code: issue_key = f"DTC_{dtc_code}"
    elif not issue_key: return jsonify({"status": "error", "message": "Missing 'issue_key' or 'dtc_code'"}), 400
    
    print(f"[Dashboard] Heal request received for: {issue_key}")
    print(f"[Dashboard] analyzer_module is {'configured' if analyzer_module else 'NOT configured'}")
    print(f"[Dashboard] database_reader is {'available' if database_reader else 'NOT available'}")
    
    if not analyzer_module: 
        print("[Dashboard] ERROR: analyzer_module is None")
        return jsonify({"status": "error", "message": "Analyzer not configured"}), 500
    
    # Extract the DTC code from the issue key if available
    dtc_code_from_key = issue_key.split('_')[-1] if issue_key.startswith("DTC_") else dtc_code
    
    # Ensure the DTC is removed from active_dtcs in database_reader
    if database_reader and dtc_code_from_key and hasattr(database_reader, 'active_dtcs'):
        print(f"[Dashboard] database_reader active_dtcs before: {database_reader.active_dtcs}")
        
        if dtc_code_from_key in database_reader.active_dtcs:
            database_reader.active_dtcs.remove(dtc_code_from_key)
            print(f"[Dashboard] Removed {dtc_code_from_key} from active_dtcs.")
        else:
            print(f"[Dashboard] WARNING: {dtc_code_from_key} not in active_dtcs: {database_reader.active_dtcs}")
            
        # Add to heal_requested_codes to ensure it gets properly tracked
        if hasattr(database_reader, 'heal_requested_codes'):
            database_reader.heal_requested_codes.add(dtc_code_from_key)
            print(f"[Dashboard] Added {dtc_code_from_key} to heal_requested_codes.")
            
        # Add to last_cleared_dtcs to prevent it from being re-added
        if hasattr(database_reader, 'last_cleared_dtcs'):
            database_reader.last_cleared_dtcs.add(dtc_code_from_key)
            print(f"[Dashboard] Added {dtc_code_from_key} to last_cleared_dtcs.")
        
        print(f"[Dashboard] database_reader active_dtcs after: {database_reader.active_dtcs}")
    else:
        if not database_reader:
            print("[Dashboard] ERROR: database_reader is None")
        elif not dtc_code_from_key:
            print("[Dashboard] ERROR: dtc_code_from_key is None")
        elif not hasattr(database_reader, 'active_dtcs'):
            print("[Dashboard] ERROR: database_reader does not have active_dtcs attribute")
            
    # Call analyzer's function for healing
    try:
        success_attempted = analyzer_module.attempt_self_heal(issue_key, database_reader=database_reader)
        print(f"[Dashboard] Heal attempt result: {success_attempted}")
    except Exception as e:
        print(f"[Dashboard] ERROR during attempt_self_heal: {e}")
        return jsonify({"status": "error", "message": f"Error during heal attempt: {str(e)}"}), 500
        
    msg = f"Self-heal attempt for {issue_key} " + ("initiated. Awaiting confirmation." if success_attempted else "not performed or not possible.")
    # Return success based on whether the *attempt* was validly initiated
    return jsonify({"status": "success" if success_attempted else "info", "message": msg})

@app.route('/get_appointments', methods=['POST'])
def get_appointments():
    """Endpoint to get mechanic appointment recommendations."""
    global scheduler_instance
    data = request.get_json()
    issue_key = data.get('issue_key')
    dtcs = data.get('dtcs', [])
    severity = data.get('severity', 'WARNING')
    
    if not issue_key:
        return jsonify({"status": "error", "message": "Missing 'issue_key'"}), 400
    
    # Get latest metrics
    with internal_shared_state["state_lock"]:
        metrics = deepcopy(internal_shared_state["latest_metrics"])
    
    # Get appointment recommendations
    appointments = scheduler_instance.get_recommendations(issue_key, dtcs, metrics, severity)
    formatted_appointments = scheduler_instance.format_appointments_for_gui()
    
    # Send appointment options via SSE
    sse_payload = {
        "type": "appointments",
        "payload": formatted_appointments
    }
    sse_queue.put(sse_payload)
    
    return jsonify({
        "status": "success",
        "message": f"Found {len(appointments)} appointment options",
        "appointments": formatted_appointments
    })

@app.route('/book_appointment', methods=['POST'])
def book_appointment():
    """Endpoint to book a mechanic appointment."""
    global scheduler_instance
    data = request.get_json()
    appointment_id = data.get('appointment_id')
    
    if not appointment_id:
        return jsonify({"status": "error", "message": "Missing 'appointment_id'"}), 400
    
    # Book the appointment
    booked = scheduler_instance.book_appointment(appointment_id)
    
    if not booked:
        return jsonify({"status": "error", "message": f"Failed to book appointment {appointment_id}"}), 400
    
    booking_status = scheduler_instance.get_booking_status()
    
    # Send booking confirmation via SSE
    sse_payload = {
        "type": "booking_confirmation",
        "payload": {
            "status": booking_status,
            "appointment": booked
        }
    }
    sse_queue.put(sse_payload)
    
    return jsonify({
        "status": "success",
        "message": booking_status,
        "appointment": booked
    })

@app.route('/search_parts', methods=['POST'])
def search_parts():
    """Endpoint to start searching for replacement parts."""
    global marketplace_instance
    data = request.get_json()
    part_name = data.get('part_name')
    dtc_code = data.get('dtc_code')
    
    if not part_name:
        return jsonify({"status": "error", "message": "Missing 'part_name'"}), 400
    
    # Get vehicle information
    vehicle_info = data.get('vehicle_info', {})
    
    # If vehicle info not provided, use default values
    if not vehicle_info:
        vehicle_info = {
            "year": "2018",
            "make": "Toyota",
            "model": "Camry",
            "engine": "2.5L 4-cylinder",
            "transmission": "Automatic"
        }
    
    # Start the search process
    result = marketplace_instance.start_search(vehicle_info, part_name, dtc_code)
    
    # Send initial search status via SSE
    sse_payload = {
        "type": "parts_search_started",
        "payload": {
            "status": result.get("status"),
            "message": result.get("message"),
            "step": result.get("step", "")
        }
    }
    sse_queue.put(sse_payload)
    
    # Start a background thread to continue the search process
    threading.Thread(target=_continue_parts_search, args=(marketplace_instance,), daemon=True).start()
    
    return jsonify({
        "status": "success",
        "message": "Parts search started",
        "search_status": result
    })

def _continue_parts_search(marketplace_agent):
    """Background worker to continue the parts search process and send updates."""
    result = {"status": "in_progress"}
    
    while result.get("status") == "in_progress":
        # Continue the search process
        result = marketplace_agent._continue_search()
        
        # Send search progress via SSE
        sse_payload = {
            "type": "parts_search_progress",
            "payload": {
                "status": result.get("status"),
                "message": result.get("message"),
                "step": result.get("step", "")
            }
        }
        sse_queue.put(sse_payload)
        
        # If search is complete, send recommendations
        if result.get("status") == "complete":
            formatted_recommendations = marketplace_agent.format_recommendations_for_display()
            summary = marketplace_agent.get_recommendations_summary()
            
            sse_payload = {
                "type": "parts_recommendations",
                "payload": {
                    "recommendations": formatted_recommendations,
                    "summary": summary
                }
            }
            sse_queue.put(sse_payload)
        
        # Short delay between steps
        threading.Event().wait(2)  # 2 second delay

@app.route('/get_part_details', methods=['GET'])
def get_part_details():
    """Endpoint to get the current part search details and status."""
    global marketplace_instance
    
    if marketplace_instance.current_step == "initialize":
        return jsonify({
            "status": "not_started",
            "message": "No part search has been started yet"
        })
    
    if marketplace_instance.current_step == "complete":
        formatted_recommendations = marketplace_instance.format_recommendations_for_display()
        summary = marketplace_instance.get_recommendations_summary()
        
        return jsonify({
            "status": "complete",
            "message": "Part search complete",
            "recommendations": formatted_recommendations,
            "summary": summary
        })
    
    return jsonify({
        "status": "in_progress",
        "message": f"Part search in progress (step: {marketplace_instance.current_step})",
        "step": marketplace_instance.current_step
    })

@app.route('/start_diy_repair', methods=['POST'])
def start_diy_repair():
    """Endpoint to start a DIY repair session."""
    global diy_repair_instance
    data = request.get_json()
    dtc_codes = data.get('dtc_codes', [])
    symptoms = data.get('symptoms', 'Check engine light is on')
    
    if not dtc_codes:
        return jsonify({"status": "error", "message": "Missing 'dtc_codes'"}), 400
    
    # Get vehicle information
    vehicle_info = data.get('vehicle_info', {})
    
    # If vehicle info not provided, use default values
    if not vehicle_info:
        vehicle_info = {
            "year": "2018",
            "make": "Toyota",
            "model": "Camry",
            "engine": "2.5L 4-cylinder",
            "transmission": "Automatic"
        }
    
    # Create issue info
    issue_info = {
        "dtc_codes": dtc_codes,
        "symptoms": symptoms
    }
    
    # Start the repair session
    result = diy_repair_instance.start_session(vehicle_info, issue_info)
    
    # Send initial instructions via SSE
    sse_payload = {
        "type": "diy_repair_started",
        "payload": {
            "status": result.get("status"),
            "message": result.get("message"),
            "attempt": result.get("attempt", 1),
            "max_attempts": result.get("max_attempts", 5),
            "conversation": diy_repair_instance.format_chat_for_display()
        }
    }
    sse_queue.put(sse_payload)
    
    return jsonify({
        "status": "success",
        "message": "DIY repair session started",
        "attempt": result.get("attempt", 1),
        "max_attempts": result.get("max_attempts", 5)
    })

@app.route('/send_diy_message', methods=['POST'])
def send_diy_message():
    """Endpoint to process a user message during DIY repair."""
    global diy_repair_instance, database_reader
    data = request.get_json()
    message = data.get('message', '')
    image_data = data.get('image_data')
    
    if not message and not image_data:
        return jsonify({"status": "error", "message": "Missing both 'message' and 'image_data'"}), 400
    
    # Get current DTCs from database_reader or use provided ones
    current_dtcs = []
    if database_reader and hasattr(database_reader, 'active_dtc') and database_reader.active_dtc:
        current_dtcs = [database_reader.active_dtc]
    elif 'current_dtcs' in data:
        current_dtcs = data.get('current_dtcs', [])
    
    # Process the user message with optional image
    result = diy_repair_instance.process_user_message(message, current_dtcs, image_data)
    
    # Get updated conversation history
    conversation = diy_repair_instance.format_chat_for_display()
    
    # Send response via SSE
    sse_payload = {
        "type": "diy_repair_message",
        "payload": {
            "status": result.get("status"),
            "message": result.get("message"),
            "attempt": result.get("attempt", 1),
            "max_attempts": result.get("max_attempts", 5),
            "conversation": conversation
        }
    }
    sse_queue.put(sse_payload)
    
    return jsonify({
        "status": "success",
        "message": "Message processed",
        "agent_response": result.get("message"),
        "repair_status": result.get("status")
    })

def start(db_reader=None):
    """Start the Flask server"""
    global database_reader_ref, database_reader, analyzer_module
    database_reader_ref = db_reader
    database_reader = db_reader  # Ensure both references are set
    
    # Ensure analyzer_module is properly set
    if analyzer_module is None:
        print("[Dashboard] WARNING: Explicitly setting analyzer_module reference")
        import analyzer as analyzer_import
        analyzer_module = analyzer_import
    
    print("[Dashboard] Starting Flask server on http://0.0.0.0:5000")
    print(f"[Dashboard] analyzer_module is {'configured' if analyzer_module else 'NOT configured'}")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# --- Standalone Run ---
if __name__ == "__main__": print("[Dashboard] Running in standalone mode."); start()