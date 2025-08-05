# canary_ai/self_healer.py
import time
import logging

logger = logging.getLogger(__name__)

# Status Constants
HEAL_STATUS_NOT_ATTEMPTED = 0
HEAL_STATUS_ATTEMPTED_LOGGED = 1
HEAL_STATUS_SEND_FAILED = 2
HEAL_STATUS_ON_COOLDOWN = 3
HEAL_STATUS_NOT_POSSIBLE = 4
VERIFY_STATUS_SUCCESS = 2
VERIFY_STATUS_FAILED = 3

class SelfHealingAgent:
    """ Manages state/cooldown for self-healing attempts (logging only). """
    def __init__(self, dummy_command_func, issue_definitions):
        self.dummy_command_func = dummy_command_func
        self.issue_definitions = issue_definitions
        self.heal_last_initiated = {} # Tracks { 'issue_key': timestamp }
        self.heal_cooldown_seconds = 60
        self.pending_verification = {} # Tracks { 'issue_key': timestamp }
        self.verification_timeout_seconds = 30
        logger.info("SelfHealer Agent initialized (Commands Disabled).")

    def _can_initiate_heal(self, issue_key):
        if issue_key not in self.heal_last_initiated: return True
        if time.time() - self.heal_last_initiated[issue_key] > self.heal_cooldown_seconds: return True
        logger.debug(f"Heal intent for '{issue_key}' on cooldown.")
        return False

    def initiate_heal(self, analysis_result):
        action_info = analysis_result.get("action", {})
        issue_key = action_info.get("key")
        if not (action_info.get("action_type") == "SELF_HEAL_ATTEMPT" and \
                action_info.get("self_heal_possible") and issue_key):
            return HEAL_STATUS_NOT_POSSIBLE
        if not self._can_initiate_heal(issue_key): return HEAL_STATUS_ON_COOLDOWN

        definition = self.issue_definitions.get(issue_key)
        if not definition: return HEAL_STATUS_NOT_POSSIBLE
        heal_cmd = definition.get("heal_command")
        if not heal_cmd: return HEAL_STATUS_NOT_POSSIBLE

        logger.info(f"Logging heal intent for '{issue_key}' (Command Type: {heal_cmd})")
        self.heal_last_initiated[issue_key] = time.time()
        self.dummy_command_func({"action": heal_cmd, "issue": issue_key})
        self.pending_verification[issue_key] = time.time() # Track verification anyway
        return HEAL_STATUS_ATTEMPTED_LOGGED

    def check_verification(self, current_metrics, current_dtcs):
        """ Checks if heal condition cleared passively. """
        verification_results = {}
        keys_to_remove = []
        for issue_key, init_time in list(self.pending_verification.items()):
            definition = self.issue_definitions.get(issue_key)
            if not definition: keys_to_remove.append(issue_key); continue

            condition_still_met = True
            try:
                cond_func = definition.get("conditions")
                if cond_func: condition_still_met = cond_func(current_metrics, current_dtcs)
                else: logger.warning(f"No condition func for {issue_key} in verification"); continue # Cannot verify
            except Exception as e: logger.warning(f"Verify condition error {issue_key}: {e}")

            if not condition_still_met:
                 logger.info(f"Verification SUCCESS for '{issue_key}' (condition false).")
                 verification_results[issue_key] = VERIFY_STATUS_SUCCESS
                 keys_to_remove.append(issue_key)
                 if issue_key in self.heal_last_initiated: del self.heal_last_initiated[issue_key] # Reset cooldown maybe
            elif time.time() - init_time > self.verification_timeout_seconds:
                 logger.warning(f"Verification FAILED (Timeout) for '{issue_key}' (condition still true).")
                 verification_results[issue_key] = VERIFY_STATUS_FAILED
                 keys_to_remove.append(issue_key)

        for key in keys_to_remove:
            if key in self.pending_verification: del self.pending_verification[key]
        return verification_results
