import os
import datetime
import random
from typing import List, Dict, Any, Optional

# LangChain imports
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain_core.runnables import RunnablePassthrough

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

class Recommendation(BaseModel):
    """Recommendation for a mechanic appointment."""
    appointment_id: int = Field(description="The ID of the recommended appointment")
    reasoning: str = Field(description="Brief reasoning for why this appointment is recommended")

class RecommendationResponse(BaseModel):
    """Response with recommended mechanic appointments."""
    recommendations: List[Recommendation] = Field(description="List of recommended appointments")

class SchedulingAgent:
    def __init__(self):
        self.llm_enabled = self._configure_langchain()
        self.available_appointments = []
        self.recommended_appointments = []
        self.booked_appointment = None
        
    def _configure_langchain(self):
        """Configure LangChain with Google Gemini API"""
        try:
            # api_key = os.environ.get("GOOGLE_API_KEY")
            api_key = 'AIzaSyByDknA9EI7ImXGhnkv55403p8mUREzCR0'
            self.llm = ChatGoogleGenerativeAI(
                model=GEMINI_MODEL_NAME,
                google_api_key=api_key,
                temperature=0.4,
                max_output_tokens=500,
            )
            print(f"[SchedulingAgent] LangChain configured with model: {GEMINI_MODEL_NAME}.")
            return True
        except Exception as e:
            print(f"[SchedulingAgent] WARNING: LangChain config failed: {e}")
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
        """Get recommended mechanics appointments using LangChain and Gemini"""
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
            appointments_text += f"- Appointment ID {apt['id']}: {shop['name']} (Rating: {shop['rating']}/5, {shop['distance']} away, Specialty: {shop['specialty']}): {date_str}, Est. cost: ${apt['price_estimate']}\n"
        
        dtc_text = ", ".join(dtcs) if dtcs else "None"
        
        # Define LangChain prompt template
        prompt_template = PromptTemplate(
            input_variables=["issue_key", "severity", "dtc_text", "metrics", "appointments_text"],
            template="""
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
            
            Return ONLY the appointment IDs of your top 3 recommendations with brief reasoning for each.
            For critical issues, prioritize earliest appointments.
            """
        )
        
        print(f"[SchedulingAgent] Querying LangChain for appointment recommendations")
        
        try:
            # Create the input for the LangChain prompt
            prompt_input = {
                "issue_key": issue_key,
                "severity": severity,
                "dtc_text": dtc_text,
                "metrics": metrics,
                "appointments_text": appointments_text
            }
            
            # Generate recommendations
            response = self.llm.invoke(prompt_template.format(**prompt_input))
            response_text = response.content
            
            # Process the response
            recommendation_ids = []
            lines = response_text.strip().split('\n')
            for line in lines:
                if 'Appointment ID' in line:
                    try:
                        # Extract appointment ID using string operations
                        start_idx = line.find('Appointment ID') + len('Appointment ID')
                        end_idx = line.find(':', start_idx) if ':' in line[start_idx:] else line.find(' ', start_idx)
                        apt_id = int(line[start_idx:end_idx].strip())
                        recommendation_ids.append(apt_id)
                    except (ValueError, IndexError):
                        continue
            
            # If we couldn't extract IDs, fall back to random recommendations
            if not recommendation_ids:
                print("[SchedulingAgent] Could not parse LLM recommendations, using random selection")
                self.recommended_appointments = random.sample(all_appointments, min(3, len(all_appointments)))
            else:
                # Get the recommended appointments by ID
                self.recommended_appointments = []
                for apt_id in recommendation_ids[:3]:  # Limit to top 3
                    for apt in all_appointments:
                        if apt["id"] == apt_id:
                            apt["ai_recommendation"] = response_text.strip()
                            self.recommended_appointments.append(apt)
                            break
                
                # If we couldn't find all recommended appointments, fill with random ones
                if len(self.recommended_appointments) < 3:
                    remaining = [apt for apt in all_appointments if apt not in self.recommended_appointments]
                    additional = random.sample(remaining, min(3 - len(self.recommended_appointments), len(remaining)))
                    for apt in additional:
                        apt["ai_recommendation"] = "Added as additional recommendation"
                        self.recommended_appointments.append(apt)
            
            return self.recommended_appointments
            
        except Exception as e:
            print(f"[SchedulingAgent] ERROR during LangChain/Gemini call: {e}")
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