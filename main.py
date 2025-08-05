# canary_ai/main.py
import time
import queue
import threading
import sys
import cantools
import os
import traceback
import json
import logging
from copy import deepcopy # For ensuring true copies of state

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import project modules
from database_reader import DatabaseReader
import decoder
import analyzer # Import the analyzer module to access DTC_DESCRIPTIONS
import dashboard

# --- Configuration ---
PROCESSING_INTERVAL = 1.0 # Analyze data every X seconds
MAX_QUEUE_SIZE = 200
DBC_FILE = 'toyota_prius_2010_pt.dbc'

# Database configuration
DB_CONFIG = {
    'user_id': 1,  # Default user ID
    'db_user': 'root',  # Update with your database username
    'db_password': '@ut0n0m1(',  # Update with your database password
    'db_name': 'vehicle_data',  # Update with your database name
    'db_host': '104.198.19.122',  # Update with your database host
    'gemini_api_key': 'AIzaSyDprfMUXwiuOh9hKc3qXbIeQYjJxcpgsLE'  # Add your Gemini API key here
}

# --- Global State ---
message_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
# The dashboard already has its own healing endpoint using trigger_heal
# manual_heal_requests_queue = queue.Queue() # Queue for heal requests from dashboard
latest_metrics = {} # Stores current metric values {sig_name: {value: V, unit: U}}
active_dtcs = set() # Stores current active DTC codes (strings) e.g. {"P0128"}
# ---> Track codes confirmed healed by valid action <---
codes_confirmed_healed_session = set()
# ---> Track codes pending confirmation from database reader <---
pending_heal_confirmation = set() # Store DTC codes {"P0128", ...}
dtcs_sent_to_frontend = set() # Track which DTCs have already been sent to the frontend
stop_event = threading.Event()
db = None # Loaded DBC database object
# Flag to indicate if data was loaded from database already
database_data_loaded = False

# --- Main Processing Loop ---
def processing_loop(database_reader_ref, cantools_db):
    """
    Refactored loop: Process messages -> Check heal confirmation -> Analyze -> Initiate Heal -> Push state.
    """
    global latest_metrics, active_dtcs, codes_confirmed_healed_session, pending_heal_confirmation, dtcs_sent_to_frontend, database_data_loaded
    if not cantools_db: 
        logger.error("Cantools DB not loaded.")
        return

    last_analysis_time = time.time()
    
    # Initialize with default values in case dashboard state isn't fully ready
    previous_analysis_result = {
        "summary": "Initializing system...",
        "active_dtcs": [],
        "resolved_dtcs_this_cycle": []
    }
    
    try:
        initial_state_data = dashboard.get_shared_state()
        if 'current_status' in initial_state_data:
            previous_analysis_result = deepcopy(initial_state_data['current_status'])
        if 'latest_metrics' in initial_state_data:
            latest_metrics = deepcopy(initial_state_data['latest_metrics'])
        logger.info("Loaded initial state from dashboard.")
    except Exception as e:
        logger.warning(f"Could not get dashboard state, using defaults: {e}")
        # We'll continue with the default values set above
    
    last_pushed_state = {} # Track the last complete state pushed

    while not stop_event.is_set():
        # Reset per-cycle tracking
        resolved_dtcs_info_this_cycle = []
        metrics_updated_this_cycle = False
        dtcs_changed_this_cycle = False # Tracks if the *active_dtc* set changed
        analysis_performed_this_cycle = False
        # Simplified state changed flags
        state_changed_flags = {'metrics': False, 'dtcs': False, 'analysis': False}
        final_analysis_result_this_cycle = previous_analysis_result # Default to previous if no analysis run

        # --- Start of the main processing block for one loop iteration ---
        try: # <<< Outer try block starts here

            # --- 1. Process incoming messages & Update Globals ---
            messages_processed_this_cycle = 0
            max_msgs_per_cycle = 100
            newly_detected_dtcs_this_cycle = set()
            metrics_in_this_batch = {}

            # Only load database data once
            if not database_data_loaded and database_reader_ref:
                # Load DTC data from database once
                if database_reader_ref.active_dtcs:
                    active_dtcs.update(database_reader_ref.active_dtcs)
                    dtcs_changed_this_cycle = True
                    state_changed_flags['dtcs'] = True
                # Load metric data from database once
                for name, metric_info in database_reader_ref.current_metrics.items():
                    latest_metrics[name] = metric_info
                    metrics_updated_this_cycle = True
                    state_changed_flags['metrics'] = True
                database_data_loaded = True
                logger.info("Initial database data loaded")

            while not message_queue.empty() and messages_processed_this_cycle < max_msgs_per_cycle:
                messages_processed_this_cycle += 1
                can_id, data_bytes = message_queue.get_nowait()
                logger.debug(f"Processing CAN message: ID={hex(can_id)}, data={data_bytes.hex()}")
                decoded_data = decoder.decode_message(cantools_db, can_id, data_bytes)
                logger.debug(f"Decoded result: {decoded_data}")

                if decoded_data:
                    new_metrics = decoded_data.get('metrics')
                    if new_metrics:
                        logger.debug(f"New metrics: {new_metrics}")
                        for name, metric_info in new_metrics.items():
                            if latest_metrics.get(name) != metric_info:
                                latest_metrics[name] = metric_info
                                metrics_updated_this_cycle = True; state_changed_flags['metrics'] = True
                                logger.debug(f"Updated metric: {name}={metric_info}")
                                
                # Print all metrics whenever any metric changes
                if metrics_updated_this_cycle:
                    print("\n===== METRIC UPDATE =====")
                    for name, metric_info in latest_metrics.items():
                        if isinstance(metric_info, dict) and 'value' in metric_info:
                            value = metric_info['value']
                            unit = metric_info.get('unit', '')
                            print(f"{name}: {value} {unit}")
                    print("=========================\n")

                    new_dtc_codes = decoded_data.get('dtcs', [])
                    if new_dtc_codes:
                        added_dtcs = set(new_dtc_codes) - active_dtcs
                        if added_dtcs:
                            active_dtcs.update(added_dtcs)
                            dtcs_changed_this_cycle = True; state_changed_flags['dtcs'] = True
                            codes_confirmed_healed_session.difference_update(added_dtcs)
                            pending_heal_confirmation.difference_update(added_dtcs)
                            # Remove from resolved this cycle if it just reappeared
                            resolved_dtcs_info_this_cycle = [
                                item for item in resolved_dtcs_info_this_cycle
                                if item['code'] not in added_dtcs
                            ]

            # --- 2. Check for Heal Confirmation from Database Reader ---
            # Only move DTCs when user clicks the self-heal button
            if database_reader_ref and database_reader_ref.last_cleared_dtcs:
                for code_cleared_by_db in database_reader_ref.last_cleared_dtcs:
                    if code_cleared_by_db in pending_heal_confirmation:
                        logger.info(f"Confirmed heal via database: {code_cleared_by_db}.")
                        # Remove from pending, add to confirmed healed
                        pending_heal_confirmation.discard(code_cleared_by_db)
                        codes_confirmed_healed_session.add(code_cleared_by_db)
                        # Add to resolved list for UI display
                        resolved_dtcs_info_this_cycle.append({
                            "code": code_cleared_by_db,
                            "desc": analyzer.DTC_DESCRIPTIONS.get(code_cleared_by_db, "Unknown Code"),
                            "resolution": "Self-Healed (Confirmed)"
                        })
                        # Ensure code is removed from active DTCs
                        if code_cleared_by_db in active_dtcs:
                            active_dtcs.discard(code_cleared_by_db)
                            dtcs_changed_this_cycle = True
                        # Flag state change for dashboard update
                        state_changed_flags['dtcs'] = True
                # Clear the set in database reader after processing
                database_reader_ref.last_cleared_dtcs.clear()

            # --- 3. Check if any manual healing happened via dashboard without confirmation ---
            # This section has been modified to not automatically update active_dtcs from database
            # Only check pending heal confirmations from user-initiated self-heal requests

            # Ensure we continuously get new DTCs from the database
            if database_reader_ref and database_reader_ref.active_dtcs:
                # First make sure any healed DTCs are removed from active_dtcs
                healed_dtcs = set()
                healed_dtcs.update(pending_heal_confirmation)
                healed_dtcs.update(codes_confirmed_healed_session)
                healed_dtcs.update(database_reader_ref.last_cleared_dtcs)
                
                # Remove any DTCs that should be healed
                for healed_code in healed_dtcs:
                    if healed_code in active_dtcs:
                        logger.info(f"Removing healed DTC {healed_code} from active_dtcs.")
                        active_dtcs.discard(healed_code)
                        dtcs_changed_this_cycle = True
                        state_changed_flags['dtcs'] = True
                
                # Then add any new DTCs from the database
                for dtc in database_reader_ref.active_dtcs:
                    # Don't add DTCs that are being healed or were previously healed
                    if dtc not in active_dtcs and dtc not in healed_dtcs:
                        logger.warning(f"Database has active_dtc={dtc} not in our tracking. Adding.")
                        active_dtcs.add(dtc)
                        dtcs_changed_this_cycle = True
                        state_changed_flags['dtcs'] = True

            # When dashboard triggers a heal via analyzer.attempt_self_heal, 
            # add to pending_heal_confirmation if not already tracked
            if database_reader_ref and hasattr(database_reader_ref, 'heal_requested_codes'):
                for heal_requested_code in database_reader_ref.heal_requested_codes:
                    # Check if this is a new request we haven't seen
                    if heal_requested_code:
                        logger.info(f"Tracking heal request for {heal_requested_code} from dashboard.")
                        
                        # Always remove from active_dtcs when healing is requested
                        if heal_requested_code in active_dtcs:
                            active_dtcs.discard(heal_requested_code)
                            logger.info(f"Removed {heal_requested_code} from active_dtcs.")
                            dtcs_changed_this_cycle = True
                            state_changed_flags['dtcs'] = True
                        
                        # Add to pending confirmation if not already there
                        if heal_requested_code not in pending_heal_confirmation:
                            pending_heal_confirmation.add(heal_requested_code)
                            
                            # Add to resolved list with "Heal Attempted" status
                            resolved_dtcs_info_this_cycle.append({
                                "code": heal_requested_code,
                                "desc": analyzer.DTC_DESCRIPTIONS.get(heal_requested_code, "Unknown Code"),
                                "resolution": "Heal Attempted"
                            })
                
                # Clear heal requests after processing
                database_reader_ref.heal_requested_codes.clear()

            # --- 4. Periodic Analysis & Potential Auto-Heal Initiation ---
            current_time = time.time()
            analysis_due = current_time - last_analysis_time >= PROCESSING_INTERVAL
            idle_analysis_due = (not analysis_due and messages_processed_this_cycle == 0 and
                                current_time - last_analysis_time >= PROCESSING_INTERVAL * 2)

            if analysis_due or idle_analysis_due or dtcs_changed_this_cycle: # Also analyze if DTCs changed
                analysis_performed_this_cycle = True
                state_changed_flags['analysis'] = True # Analysis itself means potential state change
                last_analysis_time = current_time

                # Analyze using current state
                logger.debug(f"Analysis - Metrics: {latest_metrics}, DTCs: {active_dtcs}")
                current_analysis_result = analyzer.analyze_vehicle_state(
                    latest_metrics, active_dtcs, previous_analysis_result
                )
                final_analysis_result_this_cycle = current_analysis_result # Update result

                # Check for *auto* self-heal actions
                action_info = current_analysis_result.get("action", {})
                issue_key_for_heal = action_info.get("key")
                if (action_info.get("action_type") == "SELF_HEAL_ATTEMPT" and issue_key_for_heal and
                    analyzer.ISSUE_DEFINITIONS.get(issue_key_for_heal, {}).get("self_heal_possible", False)):

                    dtc_code_to_heal = issue_key_for_heal.split('_')[-1] if issue_key_for_heal.startswith("DTC_") else None

                    # *** Important: Only AUTO-initiate heal if not already active, pending manual, or healed ***
                    if dtc_code_to_heal and dtc_code_to_heal in active_dtcs and \
                       dtc_code_to_heal not in codes_confirmed_healed_session and \
                       dtc_code_to_heal not in pending_heal_confirmation: # Check prevents interfering with manual heals
                        heal_attempt_action_ok = analyzer.attempt_self_heal(issue_key_for_heal, database_reader=database_reader_ref)
                        if heal_attempt_action_ok:
                            logger.info(f"Auto-heal initiated for {dtc_code_to_heal}. Awaiting confirmation.")
                            # For AUTO-heal, just add to pending. Don't modify active/resolved yet.
                            pending_heal_confirmation.add(dtc_code_to_heal)
                            # Do NOT set dtcs_changed_this_cycle or state_flags['dtcs'] here for auto-initiation itself

                # Update previous analysis result *after* performing current analysis
                previous_analysis_result = current_analysis_result

            # --- 5. Prepare and Update Dashboard State ---
            update_needed = (state_changed_flags['metrics'] or
                             state_changed_flags['analysis'] or
                             resolved_dtcs_info_this_cycle or # Covers manual/auto attempts and confirmations
                             state_changed_flags['dtcs']) # Covers DTCs appearing/disappearing

            if update_needed:
                # Debug: Print live metrics to terminal
                print("\n===== LIVE METRICS =====")
                if latest_metrics:
                    for name, metric_info in latest_metrics.items():
                        if isinstance(metric_info, dict) and 'value' in metric_info:
                            value = metric_info['value']
                            unit = metric_info.get('unit', '')
                            print(f"{name}: {value} {unit}")
                else:
                    print("No metrics available")
                print("=======================\n")
                
                # Prepare the state, ensuring active_dtcs and resolved_dtcs reflect current cycle updates
                active_dtc_list_for_frontend = []
                
                # Always send all active DTCs to the frontend, but force a refresh every time
                # This ensures healed DTCs are definitely removed
                logger.info(f"Current active_dtcs: {active_dtcs}")
                logger.info(f"Current pending_heal_confirmation: {pending_heal_confirmation}")
                logger.info(f"Current codes_confirmed_healed_session: {codes_confirmed_healed_session}")
                
                # Only include DTCs that aren't being healed or haven't been healed
                healed_dtcs = set()
                healed_dtcs.update(pending_heal_confirmation)
                healed_dtcs.update(codes_confirmed_healed_session)
                
                # First remove any healed DTCs from the active_dtcs set itself
                for dtc_code in list(healed_dtcs):
                    if dtc_code in active_dtcs:
                        active_dtcs.discard(dtc_code)
                        logger.info(f"Removed healed DTC {dtc_code} from active_dtcs.")
                        dtcs_changed_this_cycle = True
                        
                        # If this is the first resolved DTC, ensure we send it to the frontend
                        # to trigger removal of "None yet" placeholder
                        if not resolved_dtcs_info_this_cycle:
                            description = analyzer.DTC_DESCRIPTIONS.get(dtc_code, "Unknown Code")
                            resolved_dtcs_info_this_cycle.append({
                                "code": dtc_code,
                                "desc": description,
                                "resolution": "Self-Healed"
                            })
                
                active_dtc_list_for_frontend = []
                for dtc_code in sorted(list(active_dtcs)):
                    # Double check to never include DTCs that are in any of the healed sets
                    if dtc_code not in healed_dtcs:
                        description = analyzer.DTC_DESCRIPTIONS.get(dtc_code, "Unknown Code")
                        active_dtc_list_for_frontend.append({"code": dtc_code, "desc": description})
                
                # We're always forcing a refresh, no need to track what's been sent
                dtcs_sent_to_frontend = active_dtcs.copy()

                state_to_send = {
                    "metrics": deepcopy(latest_metrics),
                    "status_info": deepcopy(final_analysis_result_this_cycle) # Base analysis results
                }
                # Overwrite/add dtc info based on current state this cycle
                state_to_send['status_info']['resolved_dtcs_this_cycle'] = resolved_dtcs_info_this_cycle
                state_to_send['status_info']['active_dtcs'] = active_dtc_list_for_frontend

                # Only push if different from last pushed state to avoid redundant updates
                dashboard.update_shared_state(state_to_send)
                last_pushed_state = deepcopy(state_to_send)

            # Short sleep if nothing significant happened
            # Check the original flags *before* the update_needed logic
            if not state_changed_flags['metrics'] and not analysis_performed_this_cycle and not resolved_dtcs_info_this_cycle and not state_changed_flags['dtcs']:
                 time.sleep(0.05)

        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            traceback.print_exc()

def main():
    global db
    try:
        # Load DBC file
        db = cantools.database.load_file(DBC_FILE)
        logger.info(f"Loaded DBC file: {DBC_FILE}")

        # Initialize database reader
        db_reader = DatabaseReader(
            message_queue=queue.Queue(),
            db_config=DB_CONFIG
        )
        
        # Start the database reader
        db_reader.start()
        
        # Start the dashboard
        dashboard_thread = threading.Thread(target=dashboard.start, args=(db_reader,))
        dashboard_thread.daemon = True
        dashboard_thread.start()
        
        # Start the processing loop
        processing_loop(db_reader, db)
        
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        db_reader.stop()
        db_reader.db.close()
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        if 'db_reader' in locals():
            db_reader.stop()
            db_reader.db.close()

if __name__ == "__main__":
    main()