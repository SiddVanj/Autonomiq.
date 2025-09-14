import os
import time
import json
import base64
import google.generativeai as genai
from typing import List, Dict, Any, Optional, Union

# --- Configuration ---
GEMINI_MODEL_NAME = "gemini-1.5-flash"
MAX_REPAIR_ATTEMPTS = 5

class DIYRepairAgent:
    def __init__(self):
        self.llm_enabled = self._configure_genai()
        self.conversation_history = []
        self.vehicle_info = {}
        self.issue_info = {}
        self.repair_attempts = 0
        self.is_resolved = False 
        
    def _configure_genai(self):
        """Configure Google Gemini API"""
        try:
            # api_key = os.environ.get("GOOGLE_API_KEY")
            genai.configure(api_key='******')
            print(f"[DIYRepairAgent] Google GenAI configured with model: {GEMINI_MODEL_NAME}.")
            return True
        except Exception as e:
            print(f"[DIYRepairAgent] WARNING: GenAI config failed: {e}")
            return False
    
    def start_session(self, vehicle_info: Dict[str, Any], issue_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Start a new DIY repair session
        
        Args:
            vehicle_info: Dictionary containing vehicle details (make, model, year, etc.)
            issue_info: Dictionary containing issue details (dtc_codes, symptoms, etc.)
            
        Returns:
            Dictionary with session status and initial instructions
        """
        print(f"[DIYRepairAgent] Starting DIY repair session for {vehicle_info.get('year', '')} {vehicle_info.get('make', '')} {vehicle_info.get('model', '')}")
        
        self.vehicle_info = vehicle_info
        self.issue_info = issue_info
        self.conversation_history = []
        self.repair_attempts = 0
        self.is_resolved = False
        
        # Generate initial instructions
        return self._generate_repair_instructions()
    
    def process_user_message(self, message: str, current_dtcs: List[str], image_data: Optional[str] = None) -> Dict[str, Any]:
        """
        Process a user message and generate next steps
        
        Args:
            message: The user's message about what they did
            current_dtcs: Current DTC codes from the vehicle
            image_data: Optional base64 encoded image data
            
        Returns:
            Dictionary with session status and next instructions
        """
        print(f"[DIYRepairAgent] Processing user message: {message}, image: {'yes' if image_data else 'no'}")
        
        # Add user message to conversation history
        user_content = {"text": message}
        if image_data:
            user_content["image"] = image_data
            
        self.conversation_history.append({
            "role": "user",
            "content": user_content
        })
        
        # Check if issue is resolved by comparing DTCs
        original_dtcs = self.issue_info.get("dtc_codes", [])
        self.is_resolved = all(dtc not in current_dtcs for dtc in original_dtcs)
        
        if self.is_resolved:
            return self._generate_success_message()
        
        # Increment attempt counter
        self.repair_attempts += 1
        
        # Check if max attempts reached
        if self.repair_attempts >= MAX_REPAIR_ATTEMPTS:
            return self._generate_mechanic_referral()
        
        # Generate next set of instructions
        return self._generate_repair_instructions(current_dtcs, message, image_data)
    
    def _generate_repair_instructions(self, current_dtcs: Optional[List[str]] = None, last_message: Optional[str] = None, image_data: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate repair instructions based on the current state
        
        Args:
            current_dtcs: Current DTC codes (if None, use original DTCs)
            last_message: The user's last message (if any)
            image_data: Optional base64 encoded image data
            
        Returns:
            Dictionary with instructions and chat message
        """
        if not self.llm_enabled:
            print("[DIYRepairAgent] LLM disabled, using default instructions")
            instructions = self._generate_default_instructions()
            self.conversation_history.append({
                "role": "agent",
                "content": {"text": instructions}
            })
            return {
                "status": "in_progress",
                "message": instructions,
                "attempt": self.repair_attempts + 1,
                "max_attempts": MAX_REPAIR_ATTEMPTS
            }
        
        # Prepare vehicle info
        vehicle_str = f"{self.vehicle_info.get('year', 'Unknown')} {self.vehicle_info.get('make', 'Unknown')} {self.vehicle_info.get('model', 'Unknown')}"
        
        # Use current DTCs if provided, otherwise use original DTCs
        dtcs = current_dtcs if current_dtcs is not None else self.issue_info.get("dtc_codes", [])
        dtc_str = ", ".join(dtcs) if dtcs else "None"
        
        # System prompt
        system_prompt = f"""You are an automotive DIY repair assistant helping a car owner fix their vehicle issue without going to a mechanic.

Vehicle Information:
- {vehicle_str}
- Engine: {self.vehicle_info.get('engine', 'Unknown')}
- Transmission: {self.vehicle_info.get('transmission', 'Unknown')}

Issue Information:
- Active DTC Codes: {dtc_str}
- Symptoms: {self.issue_info.get('symptoms', 'Unknown')}
- Current repair attempt: {self.repair_attempts + 1} of {MAX_REPAIR_ATTEMPTS}

Task: Provide BRIEF, CONCISE step-by-step instructions for the user to try fixing this issue themselves.
Keep your response short and direct. Focus on actionable steps rather than background information.
Focus on safe, simple fixes that don't require special tools or expertise.
Your instructions should be specific to the DTCs and symptoms mentioned.

If this is a follow-up instruction (attempt > 1), acknowledge what the user has done and suggest the next step.

If the user has shared an image, examine it closely for any visible issues that might relate to the DTC codes.
"""
        
        print(f"[DIYRepairAgent] Querying Gemini for repair instructions")
        
        try:
            model = genai.GenerativeModel(GEMINI_MODEL_NAME)
            
            # Create content parts for the model
            message_parts = []
            
            # Add system prompt
            message_parts.append({"role": "user", "parts": [{"text": system_prompt}]})
            message_parts.append({"role": "model", "parts": [{"text": "I'll help you diagnose and fix the issue with brief, clear instructions."}]})
            
            # Add relevant conversation history
            for i, entry in enumerate(self.conversation_history[-4:]):  # Get last 4 exchanges
                if entry["role"] == "user":
                    if isinstance(entry["content"], dict):
                        parts = []
                        if "text" in entry["content"] and entry["content"]["text"]:
                            parts.append({"text": entry["content"]["text"]})
                        
                        if "image" in entry["content"] and entry["content"]["image"]:
                            # Parse image data properly for Gemini
                            image_str = entry["content"]["image"]
                            # If it's a data URL, extract just the base64 data
                            if image_str.startswith('data:'):
                                # Extract mime type and base64 data
                                mime_type = "image/jpeg"  # Default
                                if ";" in image_str and ":" in image_str:
                                    mime_type = image_str.split(":")[1].split(";")[0]
                                
                                # Extract base64 data
                                base64_data = image_str.split(',')[1] if ',' in image_str else image_str
                                
                                parts.append({
                                    "inline_data": {
                                        "mime_type": mime_type,
                                        "data": base64_data
                                    }
                                })
                            else:
                                # If it's already just base64 data
                                parts.append({
                                    "inline_data": {
                                        "mime_type": "image/jpeg",
                                        "data": image_str
                                    }
                                })
                        
                        if parts:
                            message_parts.append({"role": "user", "parts": parts})
                    else:
                        # Simple text message
                        message_parts.append({"role": "user", "parts": [{"text": entry["content"]}]})
                elif entry["role"] == "agent":
                    # Agent message
                    content = entry["content"]["text"] if isinstance(entry["content"], dict) else entry["content"]
                    message_parts.append({"role": "model", "parts": [{"text": content}]})
            
            # Add the current message if there's an image or text
            current_parts = []
            if last_message:
                current_parts.append({"text": last_message})
            
            if image_data:
                # Parse image data for Gemini
                if image_data.startswith('data:'):
                    # Extract mime type and base64 data
                    mime_type = "image/jpeg"  # Default
                    if ";" in image_data and ":" in image_data:
                        mime_type = image_data.split(":")[1].split(";")[0]
                    
                    # Extract base64 data
                    base64_data = image_data.split(',')[1] if ',' in image_data else image_data
                    
                    current_parts.append({
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64_data
                        }
                    })
                else:
                    # If it's already just base64 data
                    current_parts.append({
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": image_data
                        }
                    })
            
            if current_parts:
                message_parts.append({"role": "user", "parts": current_parts})
            
            # Generate content with the conversation history
            generation_config = genai.types.GenerationConfig(
                max_output_tokens=800,
                temperature=0.2
            )
            
            response = model.generate_content(
                message_parts,
                generation_config=generation_config
            )
            
            if not response.candidates or not response.candidates[0].content.parts:
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    print(f"[DIYRepairAgent] AI response blocked: {response.prompt_feedback.block_reason}")
                else:
                    print(f"[DIYRepairAgent] AI provided no instructions.")
                
                # Fallback to default instructions
                instructions = self._generate_default_instructions()
            else:
                instructions = response.text.strip()
                print(f"[DIYRepairAgent] Got response: {instructions}")
            
            # Add to conversation history
            self.conversation_history.append({
                "role": "agent",
                "content": {"text": instructions}
            })
            
            return {
                "status": "in_progress",
                "message": instructions,
                "attempt": self.repair_attempts + 1,
                "max_attempts": MAX_REPAIR_ATTEMPTS
            }
            
        except Exception as e:
            print(f"[DIYRepairAgent] ERROR during Gemini call: {e}")
            instructions = self._generate_default_instructions()
            self.conversation_history.append({
                "role": "agent",
                "content": {"text": instructions}
            })
            return {
                "status": "in_progress",
                "message": instructions,
                "attempt": self.repair_attempts + 1,
                "max_attempts": MAX_REPAIR_ATTEMPTS
            }
    
    def _generate_default_instructions(self) -> str:
        """Generate default instructions based on DTC codes"""
        dtcs = self.issue_info.get("dtc_codes", [])
        
        if not dtcs:
            return "Check for any visible issues with your vehicle, such as loose connections, caps, or warning lights."
        
        default_instructions = {
            "P0455": "Check your gas cap. Remove, inspect for damage, and reinstall until you hear it click several times.",
            "P0440": "Inspect gas cap and tighten it. Look for visible damage to fuel lines.",
            "P0128": "Check coolant level and look for cooling system leaks.",
            "P0300": "Check ignition coil connections and that all spark plug wires are secure.",
            "P0420": "Look for exhaust leaks before or after the catalytic converter."
        }
        
        # Find matching DTC or use generic instruction
        for dtc in dtcs:
            if dtc in default_instructions:
                return default_instructions[dtc]
        
        return "Check for loose connections, caps, or visible damage related to your issue. Verify all fluid levels are correct."
    
    def _generate_success_message(self) -> Dict[str, Any]:
        """Generate success message when issue is resolved"""
        success_message = "Great news! The issue is resolved. The DTC codes are gone. Continue to monitor your vehicle and document what fixed the issue for future reference."
        
        self.conversation_history.append({
            "role": "agent",
            "content": {"text": success_message}
        })
        
        return {
            "status": "resolved",
            "message": success_message,
            "attempt": self.repair_attempts,
            "max_attempts": MAX_REPAIR_ATTEMPTS
        }
    
    def _generate_mechanic_referral(self) -> Dict[str, Any]:
        """Generate mechanic referral when max attempts reached"""
        referral_message = f"After {MAX_REPAIR_ATTEMPTS} attempts, the issue remains unresolved. Please consult a professional mechanic as this likely requires specialized tools or expertise."
        
        self.conversation_history.append({
            "role": "agent",
            "content": {"text": referral_message}
        })
        
        return {
            "status": "mechanic_referral",
            "message": referral_message,
            "attempt": self.repair_attempts,
            "max_attempts": MAX_REPAIR_ATTEMPTS
        }
    
    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """
        Get the conversation history for display
        
        Returns:
            List of conversation messages with role and content
        """
        return self.conversation_history
    
    def get_repair_status(self) -> Dict[str, Any]:
        """
        Get the current repair status
        
        Returns:
            Dictionary with repair status information
        """
        status = "resolved" if self.is_resolved else "in_progress"
        if not self.is_resolved and self.repair_attempts >= MAX_REPAIR_ATTEMPTS:
            status = "mechanic_referral"
        
        return {
            "status": status,
            "attempts": self.repair_attempts,
            "max_attempts": MAX_REPAIR_ATTEMPTS,
            "is_resolved": self.is_resolved
        }
    
    def format_chat_for_display(self) -> List[Dict[str, Any]]:
        """
        Format conversation history for display in GUI
        
        Returns:
            List of formatted chat messages
        """
        formatted = []
        
        for message in self.conversation_history:
            # Convert multimodal content to display format
            if isinstance(message["content"], dict):
                content_text = message["content"].get("text", "")
                image = message["content"].get("image", None)
                
                formatted.append({
                    "role": message["role"],
                    "content": content_text,
                    "image": image,
                    "timestamp": time.strftime("%H:%M:%S")
                })
            else:
                formatted.append({
                    "role": message["role"],
                    "content": message["content"],
                    "timestamp": time.strftime("%H:%M:%S")
                })
            
        return formatted

# --- Example usage (if run directly) ---
if __name__ == "__main__":
    # Simple test
    agent = DIYRepairAgent()
    
    vehicle_info = {
        "year": "2018",
        "make": "Toyota",
        "model": "Camry",
        "engine": "2.5L 4-cylinder",
        "transmission": "Automatic"
    }
    
    issue_info = {
        "dtc_codes": ["P0455"],
        "symptoms": "Check engine light is on"
    }
    
    # Start session
    result = agent.start_session(vehicle_info, issue_info)
    print("\nInitial instructions:", result["message"])
    
    # Simulate user response
    result = agent.process_user_message("I checked the gas cap and tightened it.", ["P0455"])
    print("\nResponse after first attempt:", result["message"])
    
    # Simulate successful fix
    result = agent.process_user_message("I removed and reseated the gas cap, it clicked three times.", [])
    print("\nFinal result:", result["message"])
    print("\nStatus:", agent.get_repair_status())
