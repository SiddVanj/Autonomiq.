# canary_ai/self_healer.py
import time
import json

# Define constants for heal states/outcomes (optional but clearer)
HEAL_STATUS_NOT_ATTEMPTED = 0
HEAL_STATUS_ATTEMPTED = 1
HEAL_STATUS_SEND_FAILED = 2
HEAL_STATUS_ON_COOLDOWN = 3
HEAL_STATUS_NOT_POSSIBLE = 4
# Verification states
VERIFY_STATUS_NONE = 0
VERIFY_STATUS_PENDING = 1
VERIFY_STATUS_SUCCESS = 2
VERIFY_STATUS_FAILED = 3

class SelfHealingAgent:
    """
    Manages the decision process and initiation of self-healing actions.
    """
    def __init__(self, publish_command_func, issue_definitions):
        """
        Initializes the agent.
        Args:
            publish_command_func: A function(payload_dict) that publishes a command to the ESP via MQTT.
            issue_definitions: The dictionary mapping issue keys to their definitions (from analyzer).
        """
        self.publish_command = publish_command_func
        self.issue_definitions = issue_definitions
        self.heal_attempts = {} # Tracks recent attempts: { 'issue_key': timestamp }
        self.heal_cooldown_seconds = 60 # Don't retry healing the same issue for 60 seconds

        # Optional state for tracking verification after an attempt
        self.pending_verification = {} # { 'issue_key': timestamp_of_attempt }
        self.verification_timeout_seconds = 30 # Wait up to 30s for confirmation

        print("[SelfHealer] Agent initialized.")

    def _can_attempt_heal(self, issue_key):
        """Checks if enough time has passed since the last attempt."""
        if issue_key not in self.heal_attempts:
            return True # Never attempted before
        last_attempt_time = self.heal_attempts[issue_key]
        if time.time() - last_attempt_time > self.heal_cooldown_seconds:
            return True # Cooldown period has passed
        else:
            print(f"[SelfHealer] Heal for '{issue_key}' is on cooldown.")
            return False # Still on cooldown

    def attempt_heal(self, analysis_result):
        """
        Evaluates the analysis result and attempts a heal if appropriate and feasible.
        Args:
            analysis_result: The dictionary returned by analyzer.analyze_vehicle_state.
        Returns:
            An integer status code (e.g., HEAL_STATUS_ATTEMPTED).
        """
        action_info = analysis_result.get("action", {})
        issue_key = action_info.get("key")
        action_type = action_info.get("action_type")
        is_possible = action_info.get("self_heal_possible", False)

        if not (action_type == "SELF_HEAL_ATTEMPT" and is_possible and issue_key):
            return HEAL_STATUS_NOT_POSSIBLE # Conditions not met

        if not self._can_attempt_heal(issue_key):
            return HEAL_STATUS_ON_COOLDOWN # Don't attempt yet

        # Look up the specific command from definitions
        definition = self.issue_definitions.get(issue_key)
        if not definition:
            print(f"[SelfHealer] Error: No definition found for issue '{issue_key}'")
            return HEAL_STATUS_NOT_POSSIBLE

        heal_command_name = definition.get("heal_command") # e.g., "CLEAR_DTCS", "POWER_CYCLE_AUX"
        if not heal_command_name:
            print(f"[SelfHealer] No 'heal_command' defined for issue '{issue_key}'")
            return HEAL_STATUS_NOT_POSSIBLE

        print(f"[SelfHealer] Attempting heal for '{issue_key}' using command '{heal_command_name}'...")
        self.heal_attempts[issue_key] = time.time() # Record attempt time

        command_sent = False
        # --- Construct and Send MQTT Command ---
        if heal_command_name == "CLEAR_DTCS":
            command_payload = {"action": "clear_dtcs_hw"} # Match ESP command
            command_sent = self.publish_command(command_payload)
        # Add elif blocks for other heal commands here
        # elif heal_command_name == "POWER_CYCLE_AUX":
        #     # Example sequence for relay
        #     if self.publish_command({"action": "set_relay", "relay_num": 1, "state": "off"}):
        #         time.sleep(1) # Short delay - consider if this blocks processing too much
        #         command_sent = self.publish_command({"action": "set_relay", "relay_num": 1, "state": "on"})
        #     else:
        #          command_sent = False # Initial off command failed
        else:
            print(f"[SelfHealer] Unknown heal command name: '{heal_command_name}'")
            # Remove from attempts since we didn't really try
            del self.heal_attempts[issue_key]
            return HEAL_STATUS_NOT_POSSIBLE

        # --- Update State Based on Send Success ---
        if command_sent:
            print(f"[SelfHealer] Heal command for '{issue_key}' published successfully.")
            self.pending_verification[issue_key] = time.time() # Mark for verification
            return HEAL_STATUS_ATTEMPTED
        else:
            print(f"[SelfHealer] Failed to publish heal command for '{issue_key}'.")
            # Remove from attempts since send failed, allowing immediate retry maybe? Or keep cooldown?
            # For now, let's keep the cooldown to prevent hammering failed sends.
            return HEAL_STATUS_SEND_FAILED

    def check_verification(self, current_metrics, current_dtcs):
        """
        Checks if previously attempted heals have succeeded based on new state.
        Call this periodically *before* attempting new heals in the main loop.

        Args:
            current_metrics: The latest decoded metrics dictionary.
            current_dtcs: The set of currently active DTCs.

        Returns:
            A dictionary: { 'issue_key': verification_status_code }
        """
        verification_results = {}
        keys_to_remove = [] # Issues no longer pending verification

        for issue_key, attempt_time in list(self.pending_verification.items()): # Iterate over copy
            definition = self.issue_definitions.get(issue_key)
            if not definition:
                keys_to_remove.append(issue_key)
                continue

            time_since_attempt = time.time() - attempt_time

            # Re-evaluate the original trigger condition for the issue
            # Using the *current* metrics and DTCs
            is_condition_still_met = False
            try:
                if definition["conditions"](current_metrics, current_dtcs):
                     is_condition_still_met = True
            except Exception as e:
                 print(f"[SelfHealerVerify] Error checking condition for {issue_key}: {e}")
                 # Assume condition still met if check fails? Or clear verification? Risky.
                 is_condition_still_met = True # Safer to assume failure for now


            if not is_condition_still_met:
                # Success! The condition that triggered the heal is no longer true.
                print(f"[SelfHealerVerify] SUCCESS: Heal for '{issue_key}' verified (condition no longer met).")
                verification_results[issue_key] = VERIFY_STATUS_SUCCESS
                keys_to_remove.append(issue_key)
                # Reset cooldown for this issue - it might reappear legitimately later
                if issue_key in self.heal_attempts: del self.heal_attempts[issue_key]

            elif time_since_attempt > self.verification_timeout_seconds:
                # Failure! Timeout reached and the condition is still met.
                print(f"[SelfHealerVerify] FAILED: Heal for '{issue_key}' timed out ({self.verification_timeout_seconds}s), condition still met.")
                verification_results[issue_key] = VERIFY_STATUS_FAILED
                keys_to_remove.append(issue_key)
                # Optionally trigger escalation/handoff logic here or return status to main loop

            else:
                # Still waiting for verification
                print(f"[SelfHealerVerify] PENDING: Verification for '{issue_key}' ongoing...")
                verification_results[issue_key] = VERIFY_STATUS_PENDING

        # Clean up issues that are no longer pending verification
        for key in keys_to_remove:
            if key in self.pending_verification:
                del self.pending_verification[key]

        return verification_results
