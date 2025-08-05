import os
import datetime
import random
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

# --- Configuration ---
GEMINI_MODEL_NAME = "gemini-1.5-flash"

# --- Dummy Mechanic Data ---
MECHANIC_SHOPS = [
    {"id": 1, "name": "AutoCare Plus", "rating": 4.8, "distance": "2.5 miles", "specialty": "General Repairs"},
    {"id": 2, "name": "ElectroFix Motors", "rating": 4.6, "distance": "3.8 miles", "specialty": "Electrical Systems"},
    {"id": 3, "name": "TransmissionPro", "rating": 4.9, "distance": "5.2 miles", "specialty": "Transmission Repair"},
    {"id": 4, "name": "EcoMechanic", "rating": 4.4, "distance": "1.7 miles", "specialty": "Hybrid/Electric Vehicles"},
    {"id": 5, "name": "QuickFix Auto", "rating": 4.3, "distance": "2.1 miles", "specialty": "Fast Diagnostics"}
]

class SchedulingAgent:
    def __init__(self):
        self.llm_enabled = self._configure_genai()
        self.available_appointments = []
        self.recommended_appointments = []
        self.booked_appointment = None
        
    def _configure_genai(self):
        """Configure Google Gemini API"""
        try:
            # api_key = os.environ.get("GOOGLE_API_KEY")
            genai.configure(api_key='AIzaSyByDknA9EI7ImXGhnkv55403p8mUREzCR0')
            print(f"[SchedulingAgent] Google GenAI configured with model: {GEMINI_MODEL_NAME}.")
            return True
        except Exception as e:
            print(f"[SchedulingAgent] WARNING: GenAI config failed: {e}")
            return False
    
    def _generate_dummy_appointments(self, issue_key, severity):
        """Generate dummy appointment data for testing"""
        self.available_appointments = []
        
        # Get current date and time
        now = datetime.datetime.now()
        
        # Generate appointments for the next 7 days
        for day in range(1, 8):
            appointment_date = now + datetime.timedelta(days=day)
            
            # Generate 1-3 appointments per day
            for _ in range(random.randint(1, 3)):
                # Random hour between 8 AM and 5 PM
                hour = random.randint(8, 17)
                appointment_time = appointment_date.replace(hour=hour, minute=0, second=0)
                
                # Random shop
                shop = random.choice(MECHANIC_SHOPS)
                
                # Price based on severity
                if severity == "CRITICAL":
                    price = random.randint(300, 500)
                elif severity == "ERROR":
                    price = random.randint(200, 350)
                elif severity == "WARNING":
                    price = random.randint(100, 250)
                else:
                    price = random.randint(50, 150)
                
                self.available_appointments.append({
                    "id": len(self.available_appointments) + 1,
                    "shop": shop,
                    "datetime": appointment_time,
                    "price_estimate": price,
                    "availability": "Available",
                    "issue_key": issue_key
                })
        
        return self.available_appointments
    
    def get_recommendations(self, issue_key, dtcs, metrics, severity="WARNING"):
        """Get recommended mechanics appointments using Gemini"""
        if not self.llm_enabled:
            print("[SchedulingAgent] LLM disabled, using random recommendations")
            self._generate_dummy_appointments(issue_key, severity)
            self.recommended_appointments = random.sample(self.available_appointments, min(3, len(self.available_appointments)))
            return self.recommended_appointments
        
        # Get all possible appointments
        all_appointments = self._generate_dummy_appointments(issue_key, severity)
        
        # Format data for prompt
        appointments_text = ""
        for apt in all_appointments:
            shop = apt["shop"]
            date_str = apt["datetime"].strftime("%a, %b %d at %I:%M %p")
            appointments_text += f"- {shop['name']} (Rating: {shop['rating']}/5, {shop['distance']} away, Specialty: {shop['specialty']}): {date_str}, Est. cost: ${apt['price_estimate']}\n"
        
        dtc_text = ", ".join(dtcs) if dtcs else "None"
        
        # Create the prompt for Gemini
        prompt = f"""
        You are an AI Car Assistant helping a car owner schedule a mechanic appointment.
        
        **Vehicle Issue:**
        - Issue Key: {issue_key}
        - Severity: {severity}
        - Active DTCs: {dtc_text}
        - Metrics: {metrics}
        
        **Available Mechanic Appointments:**
        {appointments_text}
        
        **Task:** Recommend the top 3 most appropriate mechanic appointments based on:
        1. Mechanic specialty matching the vehicle issue
        2. Appointment availability (sooner is better for critical issues)
        3. Distance and convenience
        4. Cost vs. mechanic rating
        
        Provide your recommendations as a numbered list with brief reasoning (1-2 sentences) for each.
        For critical issues, prioritize earliest appointments.
        """
        
        print(f"[SchedulingAgent] Querying Gemini for appointment recommendations")
        generation_config = genai.types.GenerationConfig(
            max_output_tokens=500,
            temperature=0.4
        )
        
        try:
            model = genai.GenerativeModel(GEMINI_MODEL_NAME)
            response = model.generate_content(prompt, generation_config=generation_config)
            
            if not response.candidates or not response.candidates[0].content.parts:
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    print(f"[SchedulingAgent] AI response blocked: {response.prompt_feedback.block_reason}")
                else:
                    print(f"[SchedulingAgent] AI provided no recommendations.")
                
                # Fallback to random recommendations
                self.recommended_appointments = random.sample(all_appointments, min(3, len(all_appointments)))
            else:
                # Process AI recommendations - for now just randomly select 3 appointments
                # In a production environment, you would parse the text response to extract the specific recommendations
                print(f"[SchedulingAgent] Received Gemini recommendations")
                self.recommended_appointments = random.sample(all_appointments, min(3, len(all_appointments)))
                
                # Store the LLM response for display
                for apt in self.recommended_appointments:
                    apt["ai_recommendation"] = response.text.strip()
            
            return self.recommended_appointments
            
        except Exception as e:
            print(f"[SchedulingAgent] ERROR during Gemini call: {e}")
            # Fallback to random recommendations
            self.recommended_appointments = random.sample(all_appointments, min(3, len(all_appointments)))
            return self.recommended_appointments
    
    def book_appointment(self, appointment_id):
        """Book a specific appointment by ID"""
        for apt in self.recommended_appointments:
            if apt["id"] == appointment_id:
                self.booked_appointment = apt
                apt["status"] = "Booked"
                print(f"[SchedulingAgent] Appointment booked with {apt['shop']['name']} on {apt['datetime'].strftime('%a, %b %d at %I:%M %p')}")
                return apt
        
        print(f"[SchedulingAgent] No appointment found with ID {appointment_id}")
        return None
    
    def get_booking_status(self):
        """Get the current booking status"""
        if not self.booked_appointment:
            return "No appointment booked"
            
        apt = self.booked_appointment
        return f"Appointment booked successfully with {apt['shop']['name']} on {apt['datetime'].strftime('%a, %b %d at %I:%M %p')}"
    
    def format_appointments_for_gui(self):
        """Format appointments for display in GUI"""
        formatted = []
        
        for apt in self.recommended_appointments:
            shop = apt["shop"]
            formatted.append({
                "id": apt["id"],
                "shop_name": shop["name"],
                "shop_rating": shop["rating"],
                "shop_distance": shop["distance"],
                "shop_specialty": shop["specialty"],
                "datetime": apt["datetime"].strftime("%a, %b %d at %I:%M %p"),
                "price_estimate": f"${apt['price_estimate']}",
                "availability": apt["availability"],
                "issue_key": apt["issue_key"]
            })
            
        return formatted

# --- Example usage (if run directly) ---
if __name__ == "__main__":
    # Simple test
    scheduler = SchedulingAgent()
    recommendations = scheduler.get_recommendations("HIGH_ENGINE_TEMP", ["P0128"], {"COOLANT_TEMP": {"value": 115}}, "CRITICAL")
    
    print("\nRecommended Appointments:")
    for apt in recommendations:
        shop = apt["shop"]
        print(f"{shop['name']} ({shop['specialty']}): {apt['datetime'].strftime('%a, %b %d at %I:%M %p')}, ${apt['price_estimate']}")
    
    # Book the first appointment
    if recommendations:
        booked = scheduler.book_appointment(recommendations[0]["id"])
        print(f"\nBooking Status: {scheduler.get_booking_status()}")