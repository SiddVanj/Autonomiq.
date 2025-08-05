# canary_ai/analyzer.py
# No changes required from the previous version.
# Ensure the LLM prompt is the updated one requesting separate + combined analysis
# and mentioning metrics. Ensure self_heal_possible is correct.

import os
import time
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from cantools.database import DecodeError

# --- Configuration ---
HIGH_TEMP_THRESHOLD = 110.0
GEMINI_MODEL_NAME = "gemini-1.5-flash"

# Define DB_CONFIG here to avoid circular import
DB_CONFIG = {
    'user_id': 1,  # Default user ID
    'db_user': 'root',  # Update with your database username
    'db_password': '@ut0n0m1(',  # Update with your database password
    'db_name': 'vehicle_data',  # Update with your database name
    'db_host': '104.198.19.122',  # Update with your database host
    'gemini_api_key': 'AIzaSyDprfMUXwiuOh9hKc3qXbIeQYjJxcpgsLE'  # Add your Gemini API key here
}

# --- DTC Descriptions ---
DTC_DESCRIPTIONS = { "P0128": "Coolant Thermostat Malfunction", "P0301": "Cylinder 1 Misfire Detected", "U0100": "Lost Communication With ECM/PCM", "C0035": "Left Front Wheel Speed Sensor Circuit Malfunction" }

# --- Configure Google GenAI ---
try:
    api_key = DB_CONFIG.get('gemini_api_key')
    if not api_key:
        raise ValueError("Gemini API key not found in DB_CONFIG")
    genai.configure(api_key=api_key)
    print(f"[Analyzer] Google GenAI configured with model: {GEMINI_MODEL_NAME}.")
    LLM_ENABLED = True
except Exception as e: 
    print(f"[Analyzer] WARNING: GenAI config failed: {e}")
    LLM_ENABLED = False

# --- Known Issues & Actions ---
ISSUE_DEFINITIONS = {
    "HIGH_ENGINE_TEMP": { 
        "severity": "CRITICAL", 
        "conditions": lambda m, d: any(v > HIGH_TEMP_THRESHOLD for v in (m.get("COOLANT_TEMP", {}).get("value", []) if isinstance(m.get("COOLANT_TEMP", {}).get("value", []), list) else [m.get("COOLANT_TEMP", {}).get("value", 0)])), 
        "action_type": "DIY_DIAGNOSTIC", 
        "self_heal_possible": False, 
        "heal_command": None, 
        "llm_context": "Engine coolant temperature (COOLANT_TEMP) is critically high." 
    },
    "DTC_P0128": { "severity": "WARNING", "conditions": lambda m, d: "P0128" in d, "action_type": "SELF_HEAL_ATTEMPT", "self_heal_possible": True, "heal_command": "CLEAR_DTCS", "llm_context": "DTC P0128 (Coolant Thermostat Malfunction) is active." },
    "DTC_P0301": { "severity": "ERROR", "conditions": lambda m, d: "P0301" in d, "action_type": "DIY_REPAIR", "self_heal_possible": False, "heal_command": None, "llm_context": "DTC P0301 (Cylinder 1 Misfire Detected) is active." },
    "DTC_U0100": { "severity": "ERROR", "conditions": lambda m, d: "U0100" in d, "action_type": "MECHANIC", "self_heal_possible": False, "heal_command": None, "llm_context": "DTC U0100 (Lost Communication With ECM/PCM 'A') indicates a network issue." },
    "DTC_C0035": { "severity": "WARNING", "conditions": lambda m, d: "C0035" in d, "action_type": "DIY_DIAGNOSTIC", "self_heal_possible": False, "heal_command": None, "llm_context": "DTC C0035 (Left Front Wheel Speed Sensor Circuit Malfunction) affects ABS/Traction Control." }
}

# --- LLM Instruction Generation ---
def get_llm_instructions(issue_key, definition, metrics, dtcs):
    if not LLM_ENABLED: 
        print(f"[Analyzer] LLM is disabled. Returning fallback message for {issue_key}")
        return f"LLM is disabled. Please address: {issue_key}"
    
    dtc_codes_set = set(dtcs) if isinstance(dtcs, (set, list)) else set()
    print(f"[Analyzer] Querying Gemini ({GEMINI_MODEL_NAME}) for: {issue_key}")
    prompt_context = definition.get('llm_context', f"Issue {issue_key} detected.")
    active_dtc_details = [f"- {code} ({DTC_DESCRIPTIONS.get(code, 'Unknown Description')})" for code in sorted(list(dtc_codes_set))]
    active_dtc_list_str = "\n".join(active_dtc_details) if active_dtc_details else "None"
    rpm_val = metrics.get('ENGINE_RPM', {}).get('value', 'N/A')
    temp_val = metrics.get('COOLANT_TEMP', {}).get('value', 'N/A')
    speed_val = metrics.get('SPEED', {}).get('value', 'N/A')
    
    # Handle both single values and arrays
    def format_metric_value(val):
        if val == 'N/A':
            return 'N/A'
        if isinstance(val, list):
            return ', '.join(str(v) for v in val)
        return str(val)
    
    rpm_str = f"{format_metric_value(rpm_val)} RPM" if rpm_val != 'N/A' else "N/A"
    temp_str = f"{format_metric_value(temp_val)}°C" if temp_val != 'N/A' else "N/A"
    speed_str = f"{format_metric_value(speed_val)} mph" if speed_val != 'N/A' else "N/A"
    
    metrics_summary = f"Coolant Temp={temp_str}, Engine RPM={rpm_str}, Vehicle Speed={speed_str}"
    print(f"[Analyzer] Metrics sent to LLM: {metrics_summary}")
    
    # Create a fallback response based on the issue type
    fallback_response = f"""
**Individual DTC Advice:**
{active_dtc_list_str}

**Combined Analysis:**
Based on the current issue ({issue_key}), here are the recommended actions:
1. {prompt_context}
2. Monitor the vehicle's metrics: {metrics_summary}
3. If the issue persists, consider consulting a professional mechanic.
"""
    
    full_prompt = f"""
You are an AI Car Assistant advising a car owner via a dashboard.
The owner is reasonably comfortable with basic DIY tasks but not a professional mechanic.

**Current Vehicle Status:**
*   **Primary Issue Requiring Action:** {issue_key} ({prompt_context})
*   **All Active Diagnostic Trouble Codes:**
{active_dtc_list_str}
*   **Current Metrics:** {metrics_summary}

**Task:** Provide detailed, actionable advice based on the information above. Structure your response clearly:

**1. Individual DTC Analysis:**
For EACH active DTC listed, provide:
- A brief explanation of what the code means
- Common causes for this specific code
- Immediate actions the owner can take
- Safety implications (if any)
- Whether it's related to other active codes

**2. Combined Analysis & Priority Actions:**
- Explain how the active DTCs might be related
- Identify the most likely root cause based on the metrics
- Provide a step-by-step action plan, starting with the most critical
- Include specific metric thresholds to watch (e.g., "If coolant temp exceeds X°C...")
- Safety warnings and when to seek professional help

**3. Monitoring & Follow-up:**
- What metrics to monitor and their normal ranges
- Expected timeline for resolution
- Signs that indicate the issue is worsening
- When to schedule a mechanic visit

**Constraints:**
- **MUST use the provided 'Current Metrics'**. Refer to specific values (e.g., "High coolant temp of {temp_str}"). If a metric is "N/A", state that the value is unavailable.
- Prioritize safety warnings (e.g., "Stop driving immediately if...").
- If tasks are complex or need special tools, recommend a mechanic.
- Keep technical explanations clear and concise.
- Use plain text paragraphs with clear headings.

**Advice:**
"""
    generation_config = genai.types.GenerationConfig(
        max_output_tokens=300,  # Increased for more detailed responses
        temperature=0.6,        # Lower temperature for more focused responses
        # top_p=0.8,             # Added for better response quality
        # top_k=40               # Added for better response quality
    )
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        response = model.generate_content(full_prompt, generation_config=generation_config)
        
        if not response.candidates or not response.candidates[0].content.parts:
            print(f"[Analyzer] No valid response from Gemini for {issue_key}")
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                print(f"[Analyzer] Response blocked: {response.prompt_feedback.block_reason}")
            return fallback_response
            
        response_text = response.text.strip()
        if not response_text:
            print(f"[Analyzer] Empty response from Gemini for {issue_key}")
            return fallback_response
            
        if "don't have access" in response_text.lower() or "no metrics were provided" in response_text.lower():
            print(f"[Analyzer] WARNING: LLM response for {issue_key} may indicate it didn't use metrics. Metrics Sent: '{metrics_summary}'")
            
        print(f"[Analyzer] Gemini Response received for {issue_key}.")
        return response_text
        
    except google_exceptions.PermissionDenied as e:
        print(f"[Analyzer] Permission denied when calling Gemini API: {e}")
        return fallback_response
    except google_exceptions.InvalidArgument as e:
        print(f"[Analyzer] Invalid argument when calling Gemini API: {e}")
        return fallback_response
    except Exception as e:
        print(f"[Analyzer] ERROR during Gemini call for {issue_key}: {e}")
        return fallback_response

# --- Self-Healing Simulation ---
def attempt_self_heal(issue_key, database_reader=None):
    issue_def = ISSUE_DEFINITIONS.get(issue_key)
    if not issue_def or not issue_def.get("self_heal_possible", False): return False
    command = issue_def.get("heal_command")
    if command == "CLEAR_DTCS":
        print(f"[Analyzer] Attempting Self-Heal: Command={command} for {issue_key}...")
        if database_reader:
            dtc_code_to_clear = issue_key.split('_')[-1] if issue_key.startswith("DTC_") else None
            if dtc_code_to_clear:
                # Add to heal_requested_codes to track this as a user-initiated heal
                # This will allow the DTC to be moved from active to resolved list
                if hasattr(database_reader, 'heal_requested_codes'):
                    database_reader.heal_requested_codes.add(dtc_code_to_clear)
                    print(f"[Analyzer] Added {dtc_code_to_clear} to heal_requested_codes")
                
                # Attempt to clear the fault
                database_reader.clear_fault(dtc_code_to_clear)
                print(f"[Analyzer] Clear signal sent for {dtc_code_to_clear}.")
                return True
            else:
                print(f"[Analyzer] Could not extract DTC code from {issue_key}.")
                return True
        else:
            print("[Analyzer] Database reader not provided.")
            return True
    else:
        print(f"[Analyzer] No specific heal command for {issue_key}.")
        return False

# --- Main Analysis Function ---
def analyze_vehicle_state(latest_metrics, active_dtcs, previous_analysis):
    issues_found = []; primary_issue_details = None; highest_severity = "OK"
    current_action_details = { "type": "INFO", "instructions": "All systems nominal. Keep driving safely!", "key": None, "self_heal_possible": False }
    severity_map = {"INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}; current_max_severity_level = 0
    for key, definition in ISSUE_DEFINITIONS.items():
        try:
            if definition["conditions"](latest_metrics, active_dtcs):
                issue_data = { "key": key, "severity": definition["severity"], "action_type": definition["action_type"], "self_heal_possible": definition.get("self_heal_possible", False), "definition": definition }
                issues_found.append({ "key": key, "severity": definition["severity"], "action_type": definition["action_type"] })
                level = severity_map.get(issue_data["severity"], 0)
                if level > current_max_severity_level: current_max_severity_level = level; highest_severity = issue_data["severity"]; primary_issue_details = issue_data
        except Exception as e: print(f"[Analyzer] Error checking condition {key}: {e}")
    current_primary_issue_key = primary_issue_details["key"] if primary_issue_details else None
    previous_action = previous_analysis.get("action", {}) if isinstance(previous_analysis, dict) else {}
    previous_primary_issue_key = previous_action.get("key"); previous_instructions = previous_action.get("instructions", "N/A")
    previous_active_dtcs_list = previous_analysis.get("active_dtcs", []) if isinstance(previous_analysis, dict) else []
    previous_active_dtc_codes = set()
    if previous_active_dtcs_list:
        if previous_active_dtcs_list and isinstance(previous_active_dtcs_list[0], dict) and 'code' in previous_active_dtcs_list[0]: previous_active_dtc_codes = {item.get('code') for item in previous_active_dtcs_list if item.get('code')}
        elif previous_active_dtcs_list and isinstance(previous_active_dtcs_list[0], str): previous_active_dtc_codes = set(previous_active_dtcs_list)
    call_llm = False; needs_instructions = False
    if primary_issue_details:
        current_action_details["type"] = primary_issue_details["action_type"]; current_action_details["key"] = primary_issue_details["key"]; current_action_details["self_heal_possible"] = primary_issue_details.get("self_heal_possible", False)
        instruction_worthy_actions = ["DIY_DIAGNOSTIC", "DIY_REPAIR", "MECHANIC"]; needs_instructions = primary_issue_details["action_type"] in instruction_worthy_actions
        if needs_instructions:
            primary_key_changed = (current_primary_issue_key != previous_primary_issue_key); dtc_set_changed = (active_dtcs != previous_active_dtc_codes)
            if not primary_key_changed and not dtc_set_changed: current_action_details["instructions"] = previous_instructions
            else:
                call_llm = True; #if primary_key_changed: print(f"[Analyzer] Primary issue changed to: {current_primary_issue_key}"); #if dtc_set_changed: print(f"[Analyzer] Active DTC set changed: Current={active_dtcs}") # Less verbose
        else:
             if current_action_details["type"] == "INFO": current_action_details["instructions"] = primary_issue_details["definition"].get("llm_context", f"Handle issue: {current_primary_issue_key}")
             elif current_action_details["type"] == "SELF_HEAL_ATTEMPT": current_action_details["instructions"] = f"Attempting to clear code {current_primary_issue_key}..." if current_action_details["self_heal_possible"] else f"Issue {current_primary_issue_key} detected. Diagnosis needed."
             else: current_action_details["instructions"] = f"Acknowledged issue: {current_primary_issue_key}."
    if call_llm:
        print(f"[Analyzer] Fetching LLM instructions for: {current_primary_issue_key} (Context DTCs: {active_dtcs})")
        llm_instructions = get_llm_instructions( primary_issue_details["key"], primary_issue_details["definition"], latest_metrics, active_dtcs )
        current_action_details["instructions"] = llm_instructions
    active_dtc_list_for_frontend = []
    for dtc_code in sorted(list(active_dtcs)): description = DTC_DESCRIPTIONS.get(dtc_code, "Unknown Code"); active_dtc_list_for_frontend.append({"code": dtc_code, "desc": description})
    return { "status": highest_severity, "issues": issues_found, "action": current_action_details, "active_dtcs": active_dtc_list_for_frontend }

# --- Example usage (if run directly) ---
# ... (standalone test code remains the same) ...