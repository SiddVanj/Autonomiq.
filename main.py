# canary_ai/main.py
import time
import queue
import threading
import sys
import cantools
import os
import traceback
import json
from copy import deepcopy # For ensuring true copies of state

# Import project modules
from can_emulator import CANEmulator
import decoder
import analyzer # Import the analyzer module to access DTC_DESCRIPTIONS
import dashboard

# --- Configuration ---
PROCESSING_INTERVAL = 1.0 # Analyze data every X seconds
MAX_QUEUE_SIZE = 200
DBC_FILE = 'toyota_prius_2010_pt.dbc'

# --- Global State ---
message_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
# The dashboard already has its own healing endpoint using trigger_heal
# manual_heal_requests_queue = queue.Queue() # Queue for heal requests from dashboard
latest_metrics = {} # Stores current metric values {sig_name: {value: V, unit: U}}
active_dtcs = set() # Stores current active DTC codes (strings) e.g. {"P0128"}
# ---> Track codes confirmed healed by valid action <---
codes_confirmed_healed_session = set()
# ---> Track codes pending confirmation from emulator <---
pending_heal_confirmation = set() # Store DTC codes {"P0128", ...}
stop_event = threading.Event()
db = None # Loaded DBC database object

# --- Main Processing Loop ---
def processing_loop(emulator_ref, cantools_db):
    """
    Refactored loop: Process messages -> Check heal confirmation -> Analyze -> Initiate Heal -> Push state.
    """
    global latest_metrics, active_dtcs, codes_confirmed_healed_session, pending_heal_confirmation
    if not cantools_db: print("[Processor] Error: Cantools DB not loaded."); return

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
        print("[Processor] Loaded initial state from dashboard.")
    except Exception as e:
        print(f"[Processor] Warning: Could not get dashboard state, using defaults: {e}")
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

            # NOTE: Dashboard directly calls analyzer.attempt_self_heal() via the /trigger_heal endpoint
            # We still need to update active_dtcs and track pending confirmation when they're triggered

            # --- 1. Process incoming messages & Update Globals ---
            messages_processed_this_cycle = 0
            max_msgs_per_cycle = 100
            newly_detected_dtcs_this_cycle = set()
            metrics_in_this_batch = {}

            while not message_queue.empty() and messages_processed_this_cycle < max_msgs_per_cycle:
                messages_processed_this_cycle += 1
                can_id, data_bytes = message_queue.get_nowait()
                print(f"[DEBUG] Processing CAN message: ID={hex(can_id)}, data={data_bytes.hex()}")
                decoded_data = decoder.decode_message(cantools_db, can_id, data_bytes)
                print(f"[DEBUG] Decoded result: {decoded_data}")

                if decoded_data:
                    new_metrics = decoded_data.get('metrics')
                    if new_metrics:
                        print(f"[DEBUG] New metrics: {new_metrics}")
                        for name, metric_info in new_metrics.items():
                            if latest_metrics.get(name) != metric_info:
                                latest_metrics[name] = metric_info
                                metrics_updated_this_cycle = True; state_changed_flags['metrics'] = True
                                print(f"[DEBUG] Updated metric: {name}={metric_info}")

                    new_dtc_codes = decoded_data.get('dtcs', [])
                    if new_dtc_codes:
                        added_dtcs = set(new_dtc_codes) - active_dtcs
                        if added_dtcs:
                            # print(f"[Processor] New DTCs added: {added_dtcs}") # Less verbose log
                            active_dtcs.update(added_dtcs)
                            dtcs_changed_this_cycle = True; state_changed_flags['dtcs'] = True
                            codes_confirmed_healed_session.difference_update(added_dtcs)
                            pending_heal_confirmation.difference_update(added_dtcs)
                            # Remove from resolved this cycle if it just reappeared
                            resolved_dtcs_info_this_cycle = [
                                item for item in resolved_dtcs_info_this_cycle
                                if item['code'] not in added_dtcs
                            ]

            # --- 2. Check for Heal Confirmation from Emulator ---
            if emulator_ref and emulator_ref.last_cleared_dtc_internal:
                code_cleared_by_emulator = emulator_ref.last_cleared_dtc_internal
                if code_cleared_by_emulator in pending_heal_confirmation:
                    print(f"[Processor] Confirmed heal via emulator: {code_cleared_by_emulator}.")
                    # Remove from pending, add to confirmed healed
                    pending_heal_confirmation.discard(code_cleared_by_emulator)
                    codes_confirmed_healed_session.add(code_cleared_by_emulator)
                    # Add to resolved list for UI display
                    resolved_dtcs_info_this_cycle.append({
                        "code": code_cleared_by_emulator,
                        "desc": analyzer.DTC_DESCRIPTIONS.get(code_cleared_by_emulator, "Unknown Code"),
                        "resolution": "Self-Healed (Confirmed)"
                    })
                    # Ensure code is removed from active DTCs
                    if code_cleared_by_emulator in active_dtcs:
                        active_dtcs.discard(code_cleared_by_emulator)
                        dtcs_changed_this_cycle = True
                    # Flag state change for dashboard update
                    state_changed_flags['dtcs'] = True
                elif code_cleared_by_emulator in active_dtcs:
                    # This might be an auto-heal or manual heal we didn't track properly
                    print(f"[Processor] Emulator cleared {code_cleared_by_emulator}, removing from active DTCs.")
                    active_dtcs.discard(code_cleared_by_emulator)
                    resolved_dtcs_info_this_cycle.append({
                        "code": code_cleared_by_emulator,
                        "desc": analyzer.DTC_DESCRIPTIONS.get(code_cleared_by_emulator, "Unknown Code"),
                        "resolution": "Self-Healed"
                    })
                    dtcs_changed_this_cycle = True
                    state_changed_flags['dtcs'] = True
                # Clear the flag in emulator after processing
                emulator_ref.last_cleared_dtc_internal = None

            # --- 3. Check if any manual healing happened via dashboard without confirmation ---
            # This extra tracking helps us handle pending heals even if we don't initiate them ourselves
            if emulator_ref and emulator_ref.active_dtc and emulator_ref.active_dtc not in active_dtcs:
                # The emulator has a DTC active that we're not tracking - something unexpected
                print(f"[Processor] Warning: Emulator has active_dtc={emulator_ref.active_dtc} not in our tracking. Adding.")
                active_dtcs.add(emulator_ref.active_dtc)
                dtcs_changed_this_cycle = True
                state_changed_flags['dtcs'] = True

            # When dashboard triggers a heal via analyzer.attempt_self_heal, 
            # add to pending_heal_confirmation if not already tracked
            if emulator_ref and emulator_ref.last_heal_request_dtc:
                heal_requested_code = emulator_ref.last_heal_request_dtc
                # Check if this is a new request we haven't seen
                if heal_requested_code and heal_requested_code in active_dtcs and \
                   heal_requested_code not in pending_heal_confirmation:
                    print(f"[Processor] Tracking heal request for {heal_requested_code} from dashboard.")
                    # Move from active to pending (this is the key fix for the manual heal request)
                    active_dtcs.discard(heal_requested_code)
                    pending_heal_confirmation.add(heal_requested_code)
                    # Add to resolved list with "Heal Attempted" status
                    resolved_dtcs_info_this_cycle.append({
                        "code": heal_requested_code,
                        "desc": analyzer.DTC_DESCRIPTIONS.get(heal_requested_code, "Unknown Code"),
                        "resolution": "Heal Attempted"
                    })
                    dtcs_changed_this_cycle = True
                    state_changed_flags['dtcs'] = True
                # Clear the request after processing
                emulator_ref.last_heal_request_dtc = None

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
                print(f"[Processor ANALYSIS] Metrics: {latest_metrics}, DTCs: {active_dtcs}") # DEBUG
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
                        heal_attempt_action_ok = analyzer.attempt_self_heal(issue_key_for_heal, emulator=emulator_ref)
                        if heal_attempt_action_ok:
                             print(f"[Processor] Auto-heal initiated for {dtc_code_to_heal}. Awaiting confirmation.")
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
                # Prepare the state, ensuring active_dtcs and resolved_dtcs reflect current cycle updates
                active_dtc_list_for_frontend = []
                # Use the LATEST active_dtcs set (potentially modified by manual heal)
                for dtc_code in sorted(list(active_dtcs)):
                    description = analyzer.DTC_DESCRIPTIONS.get(dtc_code, "Unknown Code")
                    active_dtc_list_for_frontend.append({"code": dtc_code, "desc": description})

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

        # ---> CORRECTED INDENTATION FOR EXCEPT BLOCKS <---
        except queue.Empty: time.sleep(0.05) # Queue empty, wait a bit longer before next check
        except KeyboardInterrupt: print("[Processor] KeyboardInterrupt."); stop_event.set(); break
        except Exception as e: print(f"[Processor] CRITICAL Error: {e}"); traceback.print_exc(); time.sleep(1.0)
        # ------------------------------------------------

    print("[Processor] Loop finished.")


# --- Main Execution ---
if __name__ == "__main__":
    print("[Main] Starting CANary AI Prototype...")
    # Load DBC
    try:
        base_dir=os.path.dirname(os.path.abspath(__file__));dbc_file_path=os.path.join(base_dir,DBC_FILE)
        if not os.path.exists(dbc_file_path): dbc_file_path=os.path.join(os.path.dirname(base_dir),DBC_FILE)
        if not os.path.exists(dbc_file_path): raise FileNotFoundError(f"DBC not found: {DBC_FILE}")
        print(f"[DEBUG] Loading DBC file from: {dbc_file_path}")
        db=cantools.db.load_file(dbc_file_path); print(f"[Main] Loaded DBC: {dbc_file_path}")
        print(f"[DEBUG] DBC database content: {db}")
    except Exception as e: print(f"[Main] CRITICAL ERROR loading DBC: {e}"); traceback.print_exc(); sys.exit(1)

    # Init components & threads
    emulator = CANEmulator(message_queue)
    
    # Share references with the dashboard
    dashboard.emulator_instance = emulator
    dashboard.analyzer_module = analyzer

    # Initialize shared state if not already done by dashboard module
    try:
        # Try to get the shared state - if it fails, initialize it with default values
        initial_state = dashboard.get_shared_state()
        print(f"[Main] Dashboard state already initialized.")
    except (AttributeError, TypeError, Exception) as e:
        print(f"[Main] Creating initial dashboard state: {e}")
        # Initialize with an empty structure that matches what processing_loop expects
        dashboard.update_shared_state({
            "metrics": {},
            "current_status": {
                "summary": "Initializing system...",
                "active_dtcs": [],
                "resolved_dtcs_this_cycle": []
            }
        })

    # Add a last_heal_request_dtc attribute to track direct dashboard requests
    emulator.last_heal_request_dtc = None
    
    # Define a monkeypatch for attempt_self_heal to track dashboard heal requests
    original_attempt_self_heal = analyzer.attempt_self_heal
    
    def attempt_self_heal_with_tracking(issue_key, emulator=None):
        # Call the original function first
        result = original_attempt_self_heal(issue_key, emulator)
        # If successful and we have an emulator, track the DTC code
        if result and emulator and issue_key.startswith("DTC_"):
            dtc_code = issue_key.split("_")[-1]
            emulator.last_heal_request_dtc = dtc_code
            print(f"[Main] Tracking heal request for {dtc_code}")
        return result
    
    # Replace the original function with our tracked version
    analyzer.attempt_self_heal = attempt_self_heal_with_tracking
    
    emulator.start()
    processing_thread=threading.Thread(target=processing_loop,args=(emulator,db),daemon=True); processing_thread.start()
    flask_thread=threading.Thread(target=dashboard.run_flask_app,daemon=True); flask_thread.start()

    # Keep alive loop
    try:
        while not stop_event.is_set():
            if not processing_thread.is_alive(): print("[Main] Error: Processing thread terminated."); stop_event.set(); break
            time.sleep(1)
    except KeyboardInterrupt: print("\n[Main] KbdInterrupt. Shutting down...")
    finally: # Graceful shutdown
        if not stop_event.is_set(): stop_event.set()
        print("[Main] Stopping Emulator..."); emulator.stop()
        print("[Main] Waiting for processor...");
        if processing_thread.is_alive(): processing_thread.join(timeout=2)
        print("[Main] Shutdown complete."); sys.exit(0)