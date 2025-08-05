# canary_ai/analyzer.py

import os
import time
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
import json # Added for parsing if needed later

# --- Configuration ---
HIGH_TEMP_THRESHOLD = 110.0 # Celsius
LOW_FUEL_THRESHOLD = 10.0   # Percent
MISFIRE_RPM_DROP = 500      # Example threshold, maybe used in lambda
GEMINI_MODEL_NAME = "gemini-1.5-flash" # Or your preferred model

# --- Configure Google GenAI ---
LLM_ENABLED = False # Default to False
try:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable not set.")
    genai.configure(api_key=api_key)
    print(f"[Analyzer] Google GenAI configured with model: {GEMINI_MODEL_NAME}.")
    LLM_ENABLED = True
except (ValueError, google_exceptions.PermissionDenied, google_exceptions.GoogleAPIError) as e:
    print(f"[Analyzer] WARNING: Failed to configure Google GenAI: {e}")
    print("[Analyzer] LLM-based analysis/instructions will be limited.")
except Exception as e: # Catch other potential init errors
     print(f"[Analyzer] WARNING: An unexpected error occurred configuring Google GenAI: {e}")

# --- Known Issues & Actions ---
# Contains condition checks and flags for self-heal possibility.
# Does NOT contain logic to *perform* the heal anymore.
ISSUE_DEFINITIONS = {
    "HIGH_ENGINE_TEMP": {
        "severity": "CRITICAL",
        "conditions": lambda metrics, dtcs: metrics.get("EngineTemp", {}).get("value", 0) > HIGH_TEMP_THRESHOLD,
        "action_type": "DIY_DIAGNOSTIC", # Instruction focused
        "self_heal_possible": False,      # Cannot self-heal high temp usually
        "heal_command": None,             # No associated command
        "llm_context": "Engine temperature is critically high."
    },
    "LOW_FUEL": {
        "severity": "WARNING",
        "conditions": lambda metrics, dtcs: metrics.get("FuelLevel", {}).get("value", 100) < LOW_FUEL_THRESHOLD,
        "action_type": "INFO",            # Just inform the user
        "self_heal_possible": False,
        "heal_command": None,
        "llm_context": "Fuel level is low."
    },
    "DTC_P0128": {
        "severity": "WARNING",
        "conditions": lambda metrics, dtcs: "P0128" in dtcs,
        "action_type": "SELF_HEAL_ATTEMPT", # Analyzer identifies this type
        "self_heal_possible": True,       # Flags that a heal *can* be attempted
        "heal_command": "CLEAR_DTCS",     # Specifies *which* command the Healer should use
        "llm_context": "Diagnostic Trouble Code P0128 (Coolant Thermostat) is active. An automatic clear attempt may occur."
    },
    "DTC_P0301": {
        "severity": "ERROR",              # Misfires are generally more serious
        "conditions": lambda metrics, dtcs: "P0301" in dtcs,
        "action_type": "DIY_REPAIR",      # Suggest repair, but allow heal attempt? Or make separate?
        # Alternative: Could have a lower severity first that tries heal, then escalates
        "self_heal_possible": True,       # Maybe clear it once to see if transient?
        "heal_command": "CLEAR_DTCS",
        "llm_context": "DTC P0301 (Cylinder 1 Misfire) detected. Recommend inspection soon. An automatic clear attempt may occur once."
    },
    # Example for Relay Control Heal
    # "AUX_SYSTEM_OFFLINE": {
    #    "severity": "WARNING",
    #    "conditions": lambda metrics, dtcs: "U0200" in dtcs, # Example U-code
    #    "action_type": "SELF_HEAL_ATTEMPT",
    #    "self_heal_possible": True,
    #    "heal_command": "POWER_CYCLE_AUX", # Command for Healer to use relay
    #    "llm_context": "Lost communication with Auxiliary System Module. An automatic reset attempt may occur."
    #}
}

# --- LLM Instruction Generation (Using Gemini) ---
# Updated to handle the potential action types
def get_llm_instructions(issue_key, definition, metrics, dtcs):
    """Calls the Gemini API to get tailored instructions."""
    if not LLM_ENABLED:
        return f"LLM is disabled. Please address the issue: {issue_key}. Context: {definition.get('llm_context', 'N/A')}"

    action_type = definition.get("action_type", "INFO")
    prompt_context = definition.get('llm_context', f"Issue: {issue_key}")
    llm_needed = False

    # Generate detailed instructions for user actions
    if action_type in ["DIY_DIAGNOSTIC", "DIY_REPAIR", "MECHANIC"]:
        llm_needed = True
        task_description = "Provide clear, concise, step-by-step instructions for the car owner."
    # Generate simpler notification messages for other types
    elif action_type == "INFO":
        return prompt_context # Return basic context as the instruction
    elif action_type == "SELF_HEAL_ATTEMPT":
        # Check if a heal command exists - tailor message slightly
        if definition.get("heal_command"):
             return f"{prompt_context} An automated recovery attempt may be initiated by the system."
        else: # Heal possible but no command? Might be manual steps only.
             return f"{prompt_context} Please check system documentation for recovery steps."
    else: # Unknown action type
        return f"Acknowledged condition: {issue_key}. {prompt_context}"

    if not llm_needed: # Should have returned already, but safety check
        return f"Handled issue: {issue_key}"

    # --- Construct the Prompt for Detailed Instructions ---
    print(f"[Analyzer] Querying Gemini ({GEMINI_MODEL_NAME}) for instructions on: {issue_key}")
    active_dtc_list = ", ".join(sorted(list(dtcs))) if dtcs else "None"
    # Format key metrics nicely for the prompt
    metrics_summary = ", ".join([f"{k}={v.get('value','N/A')}{v.get('unit','')}"
                                for k, v in metrics.items()
                                if k in ['EngineTemp', 'EngineRPM', 'VehicleSpeed']]) # Example subset

    full_prompt = f"""
You are an AI Car Assistant advising a car owner via a dashboard display.
The owner is reasonably comfortable with basic DIY tasks but is not a professional mechanic.

Task: {task_description} Address safety first. If complex or requires tools, strongly recommend a mechanic visit. Keep the response under 150 words for dashboard display. Use plain text paragraphs, no markdown lists or headers.

Detected Issue Key: {issue_key}
Context: {prompt_context}
Active Diagnostic Trouble Codes: {active_dtc_list}
Current Key Metrics: {metrics_summary if metrics_summary else "N/A"}

Instructions for Owner:
"""
    # --- Configure and Call Generation API ---
    generation_config = genai.types.GenerationConfig( max_output_tokens=200, temperature=0.5 ) # Increased tokens slightly
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        response = model.generate_content( full_prompt, generation_config=generation_config )

        # Check for valid response
        if not response.candidates or not response.candidates[0].content.parts:
             block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else "Unknown"
             print(f"[Analyzer] Gemini blocked/empty response for {issue_key}. Reason: {block_reason}")
             return f"AI assistant could not provide instructions (Safety/Filter Block) for {issue_key}. Context: {prompt_context}"

        response_text = response.text.strip()
        print(f"[Analyzer] Gemini Response received for {issue_key}.")
        return response_text

    except (google_exceptions.GoogleAPIError, google_exceptions.RetryError) as e:
        print(f"[Analyzer] Google API Error during instruction generation for {issue_key}: {e}")
        return f"Error retrieving AI instructions for {issue_key}. Basic context: {prompt_context}"
    except Exception as e:
        print(f"[Analyzer] Unexpected error during Gemini call for {issue_key}: {e}")
        return f"Unexpected error retrieving AI instructions for {issue_key}. Basic context: {prompt_context}"


def analyze_vehicle_state(latest_metrics, active_dtcs):
    """
    Analyzes metrics and DTCs based on ISSUE_DEFINITIONS.
    Determines highest severity issue and associated actions/flags.
    Gets LLM instructions IF NEEDED based on action type.
    Returns a dictionary summarizing the state for further processing.
    """
    issues_found = [] # Collect all issues meeting conditions
    highest_severity_level = 0
    primary_issue_details = None # Holds details of the highest severity issue

    severity_map = {"INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4, "OK": 0}

    # Check defined issues
    for key, definition in ISSUE_DEFINITIONS.items():
        try:
            # Check the condition using the lambda function
            if definition.get("conditions") and definition["conditions"](latest_metrics, active_dtcs):
                severity_str = definition.get("severity", "INFO")
                level = severity_map.get(severity_str, 1)
                issue_data = {
                    "key": key,
                    "severity": severity_str,
                    "level": level, # Store numeric level
                    "action_type": definition.get("action_type", "INFO"),
                    "self_heal_possible": definition.get("self_heal_possible", False),
                    "definition": definition # Keep full definition for LLM context etc.
                }
                issues_found.append(issue_data) # Add to list of found issues

                # Determine if this is the new primary issue
                if level > highest_severity_level:
                    highest_severity_level = level
                    primary_issue_details = issue_data

        except Exception as e:
            print(f"[Analyzer] Error evaluating condition for {key}: {e}")

    # --- Prepare Action Details ---
    final_action_details = {"type": "NONE", "instructions": "All systems nominal.", "key": None, "self_heal_possible": False}
    overall_status = "OK"

    if primary_issue_details:
        overall_status = primary_issue_details["severity"]
        final_action_details["type"] = primary_issue_details["action_type"]
        final_action_details["key"] = primary_issue_details["key"]
        final_action_details["self_heal_possible"] = primary_issue_details["self_heal_possible"]

        # Get LLM instructions only for the primary issue and only if needed by action type
        instructions = get_llm_instructions(
            primary_issue_details["key"],
            primary_issue_details["definition"],
            latest_metrics,
            active_dtcs # Pass the full set of active DTCs for context
        )
        final_action_details["instructions"] = instructions
    else:
        # Handle the "OK" state if needed (no primary issue found)
        pass # Default 'All systems nominal' already set

    # --- Return the final structured result ---
    # Only include basic info about found issues for dashboard display
    simple_issues_list = [{"key": iss["key"], "severity": iss["severity"]} for iss in issues_found]

    return {
        "status": overall_status,       # Highest severity string ("OK", "WARNING", etc.)
        "issues": simple_issues_list,   # Basic list of currently detected issue keys/severities
        "action": final_action_details, # Action details for the PRIMARY issue (includes instructions)
        "active_dtcs": sorted(list(active_dtcs)) # Return current set of DTCs seen
    }

# Example usage (if run directly)
if __name__ == "__main__":
    # Ensure API key is set as environment variable before running this example
    if not os.getenv("GOOGLE_API_KEY"): print("WARNING: GOOGLE_API_KEY not set.")

    test_metrics_ok = {'EngineRPM': {'value': 800, 'unit': 'RPM'}, 'VehicleSpeed': {'value': 0, 'unit': 'km/h'}, 'EngineTemp': {'value': 85.0, 'unit': 'C'}, 'FuelLevel': {'value': 70.0, 'unit': '%'}}
    test_dtcs_ok = set()
    print("--- OK State ---")
    print(json.dumps(analyze_vehicle_state(test_metrics_ok, test_dtcs_ok), indent=2))

    test_metrics_p0128 = test_metrics_ok.copy()
    test_dtcs_p0128 = {"P0128"}
    print("\n--- P0128 DTC State (Self Heal Attempt Expected) ---")
    print(json.dumps(analyze_vehicle_state(test_metrics_p0128, test_dtcs_p0128), indent=2))

    test_metrics_misfire = test_metrics_ok.copy()
    test_dtcs_misfire = {"P0301"}
    print("\n--- P0301 DTC State (DIY Repair Action Type) ---")
    print(json.dumps(analyze_vehicle_state(test_metrics_misfire, test_dtcs_misfire), indent=2))
