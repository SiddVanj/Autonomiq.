import os
import mysql.connector
from flask import Flask, request, jsonify
import json
import logging
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room

# --- Basic Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- CORS Configuration ---
# Allow the frontend origin for the existing HTTP endpoint
CORS(app, resources={
    r"/send_heal_command": {"origins": "http://172.26.2.12:8080"}
})

# --- Socket.IO Initialization ---
# Allow all origins for Socket.IO initially for easier ESP32 connection.
# For production, you might want to restrict this if possible, though non-browser clients
# like ESP32 might not send an Origin header, making '*' often necessary.
# async_mode='gevent' is recommended for production with Gunicorn/gevent
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- Environment Variable Configuration ---
DB_HOST = os.environ.get('DB_HOST')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_NAME = os.environ.get('DB_NAME')
DB_PORT = os.environ.get('DB_PORT', '3306')
EXPECTED_API_KEY = os.environ.get('DEVICE_API_KEY')

# --- Global Store for Connected Devices ---
# Simple dictionary to map vehicle_id -> socketio session ID (sid)
# NOTE: In a multi-worker setup (like multiple Gunicorn workers), this simple dict
# won't work directly. You'd need an external store like Redis or rely on
# Socket.IO's built-in room features with a message queue (like Redis or Kafka).
# For a single worker or development, this is sufficient.
connected_devices = {}

# --- Database Connection ---
def get_db_connection():
    """Establishes database connection using environment variables."""
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        logging.error("Error: Database environment variables (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME) not fully set.")
        return None

    db_config = {
        'user': DB_USER,
        'password': DB_PASSWORD,
        'host': DB_HOST,
        'database': DB_NAME,
        'port': DB_PORT,
        'raise_on_warnings': True,
        'connection_timeout': 10 # seconds
    }
    try:
        logging.info(f"Attempting DB connection to host: {DB_HOST}:{DB_PORT}, db: {DB_NAME}")
        conn = mysql.connector.connect(**db_config)
        logging.info("Database connection successful.")
        return conn
    except mysql.connector.Error as err:
        logging.error(f"Error connecting to database: {err}")
        return None

# --- Security Check Function ---
def check_api_key():
    """Checks for the presence and validity of the API key in the request header."""
    api_key = request.headers.get('X-DEVICE-API-KEY')
    if not EXPECTED_API_KEY:
        logging.error("CRITICAL: DEVICE_API_KEY is not configured on the server.")
        return False, jsonify({"status": "error", "message": "Server configuration error"}), 500

    if not api_key or api_key != EXPECTED_API_KEY:
        logging.warning(f"Unauthorized access attempt. Provided key prefix: {api_key[:4] if api_key else 'None'}")
        return False, jsonify({"status": "error", "message": "Unauthorized"}), 401

    return True, None, None # Authorized, no error response needed

# --- HTTP API Endpoints ---
@app.route('/update_health', methods=['POST'])
def update_vehicle_health():
    """Receives vehicle health data and calls the stored procedure."""

    logging.info("Received /update_health Request Body: %s", request.get_data(as_text=True))

    # --- Security Check ---
    authorized, error_response, status_code = check_api_key()
    if not authorized:
        return error_response, status_code
    
    # --- Data Validation ---
    if not request.is_json:
        logging.warning("Received non-JSON request for /update_health")
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400

    data = request.get_json()
    logging.info(f"Received data for /update_health: {data}")

    required_fields = ['vin', 'speed', 'rpm', 'temp', 'dtcs']
    missing = [field for field in required_fields if field not in data]
    if missing:
        logging.warning(f"Request /update_health missing fields: {', '.join(missing)}")
        return jsonify({"status": "error", "message": f"Missing fields: {', '.join(missing)}"}), 400

    vin = data.get('vin')
    speed_data = data.get('speed')
    rpm_data = data.get('rpm')
    temp_data = data.get('temp')
    dtc_data = data.get('dtcs')

    if not isinstance(vin, str) or len(vin) > 17:
         logging.warning(f"Invalid VIN format for /update_health: {vin}")
         return jsonify({"status": "error", "message": "Invalid VIN format"}), 400

    # --- Database Operation ---
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        if conn is None:
             return jsonify({"status": "error", "message": "Database connection failed"}), 500

        cursor = conn.cursor()
        args = [
            vin,
            json.dumps(speed_data),
            json.dumps(rpm_data),
            json.dumps(temp_data),
            json.dumps(dtc_data)
        ]

        logging.info(f"Calling stored procedure 'update_vehicle_health_by_vin' for VIN: {vin}")
        cursor.callproc('update_vehicle_health_by_vin', args)
        conn.commit()

        logging.info(f"Successfully updated health for VIN: {vin}")
        return jsonify({"status": "success", "message": "Vehicle health updated"}), 200

    except mysql.connector.Error as err:
        logging.error(f"Database error during stored procedure call: {err}")
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": f"Database error: {err}"}), 500
    except Exception as e:
        logging.error(f"An unexpected server error occurred in /update_health: {e}", exc_info=True)
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": f"Server error: {e}"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected():
            conn.close()
            logging.info("Database connection closed.")


@app.route('/send_heal_command', methods=['POST'])
def send_heal_command_to_device():
    """Receives a 'heal' command and forwards it to the specified device via WebSocket."""
    logging.info("Received /send_heal_command Request Body: %s", request.get_data(as_text=True))

    # --- Security Check ---
    # authorized, error_response, status_code = check_api_key()
    # if not authorized:
    #     return error_response, status_code

    # --- Data Validation ---
    if not request.is_json:
        logging.warning("Received non-JSON request for /send_heal_command")
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400

    data = request.get_json()
    logging.info(f"Received data for /send_heal_command: {data}")

    required_fields = ['dtc', 'action', 'timestamp', 'vehicle_id']
    missing = [field for field in required_fields if field not in data]
    if missing:
        logging.warning(f"Request /send_heal_command missing fields: {', '.join(missing)}")
        return jsonify({"status": "error", "message": f"Missing fields: {', '.join(missing)}"}), 400

    vehicle_id = data.get('vehicle_id')
    if not isinstance(vehicle_id, str) or not vehicle_id:
        logging.warning("Invalid or missing vehicle_id in /send_heal_command request")
        return jsonify({"status": "error", "message": "Invalid or missing vehicle_id"}), 400

    # --- WebSocket Forwarding ---
    target_sid = connected_devices.get(vehicle_id)

    if not target_sid:
        logging.warning(f"Cannot send command: Device '{vehicle_id}' not connected via WebSocket.")
        # 404 might be more appropriate if the device is expected but not found
        return jsonify({"status": "error", "message": f"Device '{vehicle_id}' not connected"}), 404

    try:
        # Emit a specific event (e.g., 'heal_command') to the target device's room (sid)
        logging.info(f"Forwarding heal command to vehicle_id '{vehicle_id}' (sid: {target_sid})")
        socketio.emit('heal_command', data, room=target_sid)
        logging.info(f"Successfully emitted heal_command to {vehicle_id}")
        return jsonify({"status": "success", "message": "Command sent to device"}), 200
    except Exception as e:
        logging.error(f"Error emitting WebSocket message to {vehicle_id} (sid: {target_sid}): {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to send command via WebSocket"}), 500


# --- WebSocket Event Handlers ---

@socketio.on('connect')
def handle_connect():
    """Handles new WebSocket connections."""
    logging.info(f"WebSocket client connected: {request.sid}")
    # We don't know the vehicle_id yet, wait for registration message

@socketio.on('disconnect')
def handle_disconnect():
    """Handles WebSocket disconnections."""
    logging.info(f"WebSocket client disconnected: {request.sid}")
    # Find and remove the disconnected device from our tracking dictionary
    disconnected_vehicle_id = None
    for vehicle_id, sid in connected_devices.items():
        if sid == request.sid:
            disconnected_vehicle_id = vehicle_id
            break
    if disconnected_vehicle_id:
        del connected_devices[disconnected_vehicle_id]
        logging.info(f"Device '{disconnected_vehicle_id}' removed from connected list.")
    else:
        logging.warning(f"Disconnected sid {request.sid} was not registered to a vehicle_id.")

@socketio.on('register_device')
def handle_register_device(data):
    """Handles device registration message after connection."""
    # Expecting data like: {'vehicle_id': 'some_unique_id_or_vin'}
    if isinstance(data, dict) and 'vehicle_id' in data:
        vehicle_id = data['vehicle_id']
        sid = request.sid
        # Check if this vehicle_id is already connected (e.g., reconnect attempt)
        if vehicle_id in connected_devices:
            old_sid = connected_devices[vehicle_id]
            logging.warning(f"Device '{vehicle_id}' re-registering. Old sid: {old_sid}, New sid: {sid}")
            # Optionally, you could force disconnect the old session here if needed
        connected_devices[vehicle_id] = sid
        # Use sid as the room name for direct targeting
        # join_room(sid) # Client is implicitly in their own room identified by sid
        logging.info(f"Device registered: vehicle_id='{vehicle_id}', sid='{sid}'")
        # Optionally send confirmation back to device
        emit('registration_ack', {'status': 'success', 'vehicle_id': vehicle_id}, room=sid)
    else:
        logging.warning(f"Invalid registration data from {request.sid}: {data}")
        # Optionally send an error back
        emit('registration_ack', {'status': 'error', 'message': 'Invalid registration data format. Expecting {"vehicle_id": "your_id"}'}, room=request.sid)


# --- Run the App ---
if __name__ == '__main__':
    try:
        from dotenv import load_dotenv
        logging.info("Loading environment variables from .env file for local run")
        load_dotenv()
        DB_HOST = os.environ.get('DB_HOST', 'localhost')
        DB_USER = os.environ.get('DB_USER')
        DB_PASSWORD = os.environ.get('DB_PASSWORD')
        DB_NAME = os.environ.get('DB_NAME')
        DB_PORT = os.environ.get('DB_PORT', '3306')
        EXPECTED_API_KEY = os.environ.get('DEVICE_API_KEY')
    except ImportError:
        logging.info(".env file not found or python-dotenv not installed (expected in Docker/Cloud Run).")
        pass

    if not EXPECTED_API_KEY:
        logging.warning("CRITICAL: DEVICE_API_KEY not found in environment. Endpoints are insecure.")

    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Starting Flask-SocketIO server on host 0.0.0.0 port {port}")
    # Use socketio.run() instead of app.run()
    # Set debug=False for stability testing/production
    # Use gevent or eventlet for better concurrency with WebSockets
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False) # use_reloader=False is important with gevent/eventlet