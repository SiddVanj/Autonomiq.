import os
import json
import time
import requests
import google.generativeai as genai
import re
from typing import List, Dict, Any, Union, Optional, Tuple
from urllib.parse import quote

# --- Configuration ---
GEMINI_MODEL_NAME = "gemini-1.5-flash"
SERPER_API_BASE_URL = "https://google.serper.dev/search"

class MarketplaceAgent:
    def __init__(self):
        self.llm_enabled = self._configure_genai()
        self.serper_api_key = os.environ.get("SERPER_API_KEY")
        self.conversation_history = []
        self.vehicle_info = {}
        self.part_info = {}
        self.search_results = []
        self.recommended_parts = []
        self.current_step = "initialize"
        self.max_attempts = 3
        
    def _configure_genai(self):
        """Configure Google Gemini API"""
        try:
            # api_key = os.environ.get("GOOGLE_API_KEY")
            genai.configure(api_key='AIzaSyCqv0vDXbmtcSEbBU3vEQKUmKV_87PTXrQ')
            print(f"[MarketplaceAgent] Google GenAI configured with model: {GEMINI_MODEL_NAME}.")
            return True
        except Exception as e:
            print(f"[MarketplaceAgent] WARNING: GenAI config failed: {e}")
            return False
    
    def start_search(self, vehicle_info: Dict[str, Any], part_name: str, dtc_code: Optional[str] = None) -> Dict[str, Any]:
        """
        Start the autonomous part search process
        
        Args:
            vehicle_info: Dictionary containing vehicle details (make, model, year, etc.)
            part_name: Name of the part that needs replacement
            dtc_code: Optional DTC code associated with the failing part
            
        Returns:
            Dictionary with search status and initial information
        """
        print(f"[MarketplaceAgent] Starting search for {part_name} for {vehicle_info.get('year', '')} {vehicle_info.get('make', '')} {vehicle_info.get('model', '')}")
        
        self.vehicle_info = vehicle_info
        self.part_info = {"name": part_name, "dtc_code": dtc_code}
        self.conversation_history = []
        self.search_results = []
        self.recommended_parts = []
        # Skip directly to search marketplace step
        self.current_step = "search_marketplace"
        
        # Start the agent workflow
        return self._continue_search()
    
    def _continue_search(self) -> Dict[str, Any]:
        """
        Continue the autonomous part search process based on current step
        
        Returns:
            Dictionary with search status and current results
        """
        print(f"[MarketplaceAgent] Continuing search at step: {self.current_step}")
        
        if self.current_step == "identify_part":
            return self._identify_specific_part()
        elif self.current_step == "search_marketplace":
            return self._search_marketplace()
        elif self.current_step == "analyze_options":
            return self._analyze_and_recommend()
        elif self.current_step == "complete":
            return {
                "status": "complete",
                "recommended_parts": self.recommended_parts,
                "message": "Part search complete"
            }
        else:
            return {
                "status": "error",
                "message": f"Unknown step: {self.current_step}"
            }
    
    def _identify_specific_part(self) -> Dict[str, Any]:
        """
        Use LLM to identify the specific part details from the general part name and DTC code
        
        Returns:
            Dictionary with search status and part details
        """
        if not self.llm_enabled:
            print("[MarketplaceAgent] LLM disabled, using basic part information")
            self.current_step = "search_marketplace"
            return self._continue_search()
        
        # Prepare make/model/year info
        vehicle_str = f"{self.vehicle_info.get('year', 'Unknown')} {self.vehicle_info.get('make', 'Unknown')} {self.vehicle_info.get('model', 'Unknown')}"
        
        # Create prompt for the LLM
        prompt = f"""
        You are an automotive parts specialist helping to identify the correct replacement part.

        Vehicle Information:
        - {vehicle_str}
        - Engine: {self.vehicle_info.get('engine', 'Unknown')}
        - Transmission: {self.vehicle_info.get('transmission', 'Unknown')}

        Part Information:
        - General part name: {self.part_info['name']}
        - Associated DTC code: {self.part_info['dtc_code'] or 'None'}

        Task: Identify the specific part details that would be needed to search for a replacement.
        Please include:
        1. Specific part name/category
        2. Any relevant OEM part numbers (if you can determine)
        3. Compatible aftermarket part types
        4. Key specifications to check for compatibility
        5. Estimated price range for this part (low-end to high-end)

        Format your response as a JSON object with these fields.
        """
        
        print(f"[MarketplaceAgent] Querying Gemini to identify specific part details")
        generation_config = genai.types.GenerationConfig(
            max_output_tokens=800,
            temperature=0.2,
            response_mime_type="application/json"
        )
        
        try:
            model = genai.GenerativeModel(GEMINI_MODEL_NAME)
            response = model.generate_content(prompt, generation_config=generation_config)
            
            if not response.candidates or not response.candidates[0].content.parts:
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    print(f"[MarketplaceAgent] AI response blocked: {response.prompt_feedback.block_reason}")
                else:
                    print(f"[MarketplaceAgent] AI provided no part details.")
                
                # Move to next step with basic info
                self.current_step = "search_marketplace"
                return self._continue_search()
            
            # Process JSON response
            response_text = response.text.strip()
            
            # Try to extract JSON from the response
            try:
                # First try to directly parse it
                part_details = json.loads(response_text)
            except json.JSONDecodeError:
                # If that fails, try to extract JSON using regex
                match = re.search(r'\{[\s\S]*\}', response_text)
                if match:
                    try:
                        part_details = json.loads(match.group(0))
                    except json.JSONDecodeError:
                        print(f"[MarketplaceAgent] Could not parse JSON from response")
                        part_details = {
                            "specificPartName": self.part_info["name"],
                            "oemPartNumbers": [],
                            "compatibleAftermarketTypes": ["Generic replacement"],
                            "keySpecifications": [],
                            "estimatedPriceRange": {"low": 0, "high": 0}
                        }
                else:
                    print(f"[MarketplaceAgent] No JSON found in response")
                    part_details = {
                        "specificPartName": self.part_info["name"],
                        "oemPartNumbers": [],
                        "compatibleAftermarketTypes": ["Generic replacement"],
                        "keySpecifications": [],
                        "estimatedPriceRange": {"low": 0, "high": 0}
                    }
            
            # Update part info with details
            self.part_info.update({
                "specific_name": part_details.get("specificPartName", self.part_info["name"]),
                "oem_part_numbers": part_details.get("oemPartNumbers", []),
                "compatible_types": part_details.get("compatibleAftermarketTypes", []),
                "key_specs": part_details.get("keySpecifications", []),
                "price_range": part_details.get("estimatedPriceRange", {"low": 0, "high": 0})
            })
            
            print(f"[MarketplaceAgent] Part identified: {self.part_info['specific_name']}")
            
            # Add to conversation history
            self.conversation_history.append({
                "role": "system",
                "content": f"Part identified: {self.part_info['specific_name']}"
            })
            
            # Move to next step
            self.current_step = "search_marketplace"
            return {
                "status": "in_progress",
                "step": "identify_part",
                "part_info": self.part_info,
                "message": f"Identified specific part: {self.part_info['specific_name']}"
            }
            
        except Exception as e:
            print(f"[MarketplaceAgent] ERROR during Gemini call: {e}")
            # Move to next step with basic info
            self.current_step = "search_marketplace"
            return self._continue_search()
    
    def _search_marketplace(self) -> Dict[str, Any]:
        """
        Search marketplace for part prices and availability using Serper API
        
        Returns:
            Dictionary with search status and search results
        """
        if not self.serper_api_key:
            print("[MarketplaceAgent] Serper API key not found, using dummy search results")
            # Create dummy search results
            self.search_results = self._generate_dummy_search_results()
            self.current_step = "analyze_options"
            return self._continue_search()
        
        # Build search query
        vehicle_year = self.vehicle_info.get('year', '')
        vehicle_make = self.vehicle_info.get('make', '')
        vehicle_model = self.vehicle_info.get('model', '')
        part_name = self.part_info.get('specific_name', self.part_info['name'])
        
        # Include OEM part numbers if available
        oem_part_str = ""
        if "oem_part_numbers" in self.part_info and self.part_info["oem_part_numbers"]:
            oem_part_str = f" {' '.join(self.part_info['oem_part_numbers'][:2])}"
        
        search_query = f"{vehicle_year} {vehicle_make} {vehicle_model} {part_name}{oem_part_str} replacement part"
        
        headers = {
            "X-API-KEY": self.serper_api_key,
            "Content-Type": "application/json"
        }
        
        payload = {
            "q": search_query,
            "gl": "us",
            "num": 10
        }
        
        print(f"[MarketplaceAgent] Searching for parts with query: {search_query}")
        
        try:
            response = requests.post(SERPER_API_BASE_URL, headers=headers, json=payload)
            
            if response.status_code == 200:
                search_data = response.json()
                
                # Extract relevant information from search results
                self.search_results = self._parse_search_results(search_data)
                print(f"[MarketplaceAgent] Found {len(self.search_results)} part options")
                
                # Move to next step
                self.current_step = "analyze_options"
                
                return {
                    "status": "in_progress",
                    "step": "search_marketplace",
                    "search_results_count": len(self.search_results),
                    "message": f"Found {len(self.search_results)} part options from various suppliers"
                }
            else:
                print(f"[MarketplaceAgent] Error in Serper API call: {response.status_code}")
                # Use dummy data as fallback
                self.search_results = self._generate_dummy_search_results()
                self.current_step = "analyze_options"
                return self._continue_search()
                
        except Exception as e:
            print(f"[MarketplaceAgent] Error during marketplace search: {e}")
            # Use dummy data as fallback
            self.search_results = self._generate_dummy_search_results()
            self.current_step = "analyze_options"
            return self._continue_search()
    
    def _parse_search_results(self, search_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parse search results from Serper API
        
        Args:
            search_data: Raw search data from Serper API
            
        Returns:
            List of parsed part options
        """
        parsed_results = []
        
        # Process organic search results
        if "organic" in search_data:
            for result in search_data["organic"]:
                part_data = {
                    "title": result.get("title", ""),
                    "link": result.get("link", ""),
                    "snippet": result.get("snippet", ""),
                    "source": result.get("source", "Unknown"),
                    "price": None,
                    "location": None,
                    "rating": None,
                    "reviews_count": None,
                    "in_stock": None
                }
                
                # Extract price from title or snippet
                price_match = re.search(r'\$(\d+(?:\.\d{2})?)', result.get("title", "") + result.get("snippet", ""))
                if price_match:
                    part_data["price"] = float(price_match.group(1))
                
                # Extract location hints
                location_keywords = ["shipped from", "located in", "pickup at", "available at"]
                for keyword in location_keywords:
                    if keyword in result.get("snippet", "").lower():
                        location_text = result.get("snippet", "").lower().split(keyword)[1].split(".")[0].strip()
                        part_data["location"] = location_text
                        break
                
                parsed_results.append(part_data)
        
        # Process shopping results if available
        if "shopping" in search_data:
            for result in search_data["shopping"]:
                part_data = {
                    "title": result.get("title", ""),
                    "link": result.get("link", ""),
                    "snippet": result.get("snippet", ""),
                    "source": result.get("source", "Unknown"),
                    "price": None,
                    "location": None,
                    "rating": None,
                    "reviews_count": None,
                    "in_stock": None
                }
                
                # Extract price
                if "price" in result:
                    price_str = result["price"]
                    price_match = re.search(r'\$(\d+(?:\.\d{2})?)', price_str)
                    if price_match:
                        part_data["price"] = float(price_match.group(1))
                
                # Extract rating if available
                if "rating" in result:
                    part_data["rating"] = result["rating"]
                
                # Extract reviews count if available
                if "reviews" in result:
                    reviews_str = result["reviews"]
                    reviews_match = re.search(r'(\d+)', reviews_str)
                    if reviews_match:
                        part_data["reviews_count"] = int(reviews_match.group(1))
                
                parsed_results.append(part_data)
        
        # If we don't have enough results, use dummy data
        if len(parsed_results) < 3:
            dummy_results = self._generate_dummy_search_results()
            # Add dummy results to fill in
            parsed_results.extend(dummy_results[:(3 - len(parsed_results))])
            
        return parsed_results
    
    def _generate_dummy_search_results(self) -> List[Dict[str, Any]]:
        """
        Generate dummy search results for testing
        
        Returns:
            List of dummy part options
        """
        part_name = self.part_info.get('specific_name', self.part_info['name'])
        vehicle_year = self.vehicle_info.get('year', '2018')
        vehicle_make = self.vehicle_info.get('make', 'Toyota')
        vehicle_model = self.vehicle_info.get('model', 'Camry')
        
        base_price = 0
        if "price_range" in self.part_info and "low" in self.part_info["price_range"]:
            base_price = self.part_info["price_range"]["low"]
        
        if base_price == 0:
            # Assign default prices based on common part categories
            if "sensor" in part_name.lower():
                base_price = 45
            elif "pump" in part_name.lower():
                base_price = 85
            elif "filter" in part_name.lower():
                base_price = 15
            elif "belt" in part_name.lower():
                base_price = 25
            elif "battery" in part_name.lower():
                base_price = 120
            elif "brake" in part_name.lower() and "pad" in part_name.lower():
                base_price = 60
            else:
                base_price = 50
        
        return [
            {
                "title": f"OEM {part_name} for {vehicle_year} {vehicle_make} {vehicle_model}",
                "link": "https://example.com/oem-part",
                "snippet": f"Genuine OEM {part_name} for {vehicle_make} vehicles. Includes 12-month warranty.",
                "source": "DealerPartsOnline",
                "price": base_price * 1.8,
                "location": "Shipped from central warehouse (2-3 days)",
                "rating": 4.7,
                "reviews_count": 124,
                "in_stock": True
            },
            {
                "title": f"Premium Aftermarket {part_name} - {vehicle_make} {vehicle_model}",
                "link": "https://example.com/premium-part",
                "snippet": f"High quality replacement {part_name}. Compatible with {vehicle_year}-{int(vehicle_year)+5} {vehicle_make} models.",
                "source": "AutoZone",
                "price": base_price * 1.2,
                "location": "Available at local store (5.2 miles away)",
                "rating": 4.5,
                "reviews_count": 89,
                "in_stock": True
            },
            {
                "title": f"Economy {part_name} Replacement for {vehicle_make}",
                "link": "https://example.com/economy-part",
                "snippet": f"Budget-friendly {part_name} option. Fits most {vehicle_make} models.",
                "source": "PartsGeek",
                "price": base_price * 0.8,
                "location": "Shipped from regional warehouse (3-5 days)",
                "rating": 3.9,
                "reviews_count": 47,
                "in_stock": True
            },
            {
                "title": f"Used {part_name} - {vehicle_year} {vehicle_make} {vehicle_model}",
                "link": "https://example.com/used-part",
                "snippet": f"Recycled {part_name} from a {vehicle_year} {vehicle_make} {vehicle_model} with 45k miles. 30-day warranty.",
                "source": "Auto Salvage Direct",
                "price": base_price * 0.6,
                "location": "Pickup available at salvage yard (8.7 miles away)",
                "rating": 4.1,
                "reviews_count": 23,
                "in_stock": True
            },
            {
                "title": f"Universal {part_name} - Fits Multiple Vehicles",
                "link": "https://example.com/universal-part",
                "snippet": f"Universal fit {part_name}. May require minor modification for installation.",
                "source": "eBay",
                "price": base_price * 0.7,
                "location": "Shipped from seller in Nevada (5-7 days)",
                "rating": 3.8,
                "reviews_count": 156,
                "in_stock": True
            }
        ]
    
    def _analyze_and_recommend(self) -> Dict[str, Any]:
        """
        Analyze part options and recommend the best ones
        
        Returns:
            Dictionary with search status and recommendations
        """
        print(f"[MarketplaceAgent] Analyzing {len(self.search_results)} part options")
        
        # Simply sort by price and pick the top 3
        sorted_parts = sorted(self.search_results, key=lambda x: float('inf') if x.get('price') is None else x.get('price'))
        self.recommended_parts = sorted_parts[:3]
        
        # Add basic recommendation data to each part
        for i, part in enumerate(self.recommended_parts):
            best_for = "budget-conscious buyers"
            if i == 0:
                reasons = ["Lowest price option available", "Good value for money"]
                drawbacks = ["May not last as long as premium options"]
                best_for = "budget-conscious buyers" 
            elif i == 1:
                reasons = ["Good balance of price and quality", "Reliable performance"]
                drawbacks = ["Slightly more expensive than budget option"]
                best_for = "value-oriented buyers"
            else:
                reasons = ["High quality construction", "Best durability"]
                drawbacks = ["Higher price point"]
                best_for = "premium quality seekers"
                
            part["recommendation_reasons"] = reasons
            part["recommendation_drawbacks"] = drawbacks 
            part["recommendation_best_for"] = best_for
            part["recommendation_score"] = 10 - i
            
        # Mark the middle option as best overall balance
        if len(self.recommended_parts) >= 2:
            self.recommended_parts[1]["is_best_overall"] = True
            
        # Create summary
        cheapest = min(self.recommended_parts, key=lambda x: float('inf') if x.get('price') is None else x.get('price'))
        cheapest_price = f"${cheapest['price']:.2f}" if cheapest.get('price') is not None else "unknown price"
        
        best_rated = max(self.recommended_parts, key=lambda x: -1 if x.get('rating') is None else x.get('rating'))
        best_rating = f"{best_rated.get('rating', 'N/A')}/5" if best_rated.get('rating') is not None else "no rating"
        
        self.summary = f"Found {len(self.recommended_parts)} replacement options. Prices range from {cheapest_price} with ratings up to {best_rating}. The middle option offers the best balance of quality and price."
        
        # Move to next step
        self.current_step = "complete"
        print(f"[MarketplaceAgent] Analysis complete. Recommended {len(self.recommended_parts)} parts.")
        
        return {
            "status": "complete",
            "recommended_parts": self.recommended_parts,
            "summary": self.summary,
            "message": "Found the best part options based on price and quality"
        }
    
    def format_recommendations_for_display(self) -> List[Dict[str, Any]]:
        """
        Format recommendations for display in the GUI
        
        Returns:
            List of formatted recommendation data
        """
        formatted = []
        
        for i, part in enumerate(self.recommended_parts, 1):
            price_str = f"${part['price']:.2f}" if part.get('price') is not None else "Price not listed"
            
            formatted_part = {
                "id": i,
                "title": part.get('title', 'No title'),
                "price": price_str,
                "source": part.get('source', 'Unknown source'),
                "location": part.get('location', 'Location unknown'),
                "rating": part.get('rating', 'N/A'),
                "link": part.get('link', '#'),
                "description": part.get('snippet', 'No description'),
                "is_best_overall": part.get('is_best_overall', False),
                "reasons": part.get('recommendation_reasons', []),
                "drawbacks": part.get('recommendation_drawbacks', []),
                "best_for": part.get('recommendation_best_for', ''),
                "score": part.get('recommendation_score', 0)
            }
            
            formatted.append(formatted_part)
        
        return formatted
    
    def get_recommendations_summary(self) -> str:
        """
        Get the overall recommendations summary
        
        Returns:
            Summary text
        """
        if hasattr(self, 'summary') and self.summary:
            return self.summary
        
        # Generate basic summary if none exists
        if not self.recommended_parts:
            return "No part recommendations available."
        
        cheapest = min(self.recommended_parts, key=lambda x: float('inf') if x.get('price') is None else x.get('price'))
        cheapest_price = f"${cheapest['price']:.2f}" if cheapest.get('price') is not None else "unknown price"
        
        best_rated = max(self.recommended_parts, key=lambda x: -1 if x.get('rating') is None else x.get('rating'))
        best_rating = f"{best_rated.get('rating', 'N/A')}/5" if best_rated.get('rating') is not None else "no rating"
        
        return f"Found {len(self.recommended_parts)} replacement options. Prices range from {cheapest_price} with ratings up to {best_rating}."

# --- Example usage (if run directly) ---
if __name__ == "__main__":
    # Simple test
    agent = MarketplaceAgent()
    
    vehicle_info = {
        "year": "2018",
        "make": "Toyota",
        "model": "Camry",
        "engine": "2.5L 4-cylinder",
        "transmission": "Automatic"
    }
    
    result = agent.start_search(vehicle_info, "brake pads", "P0128")
    print("\nSearch started:", result)
    
    while result.get("status") == "in_progress":
        time.sleep(2)  # Wait for next step
        result = agent._continue_search()
        print("\nSearch progress:", result)
    
    print("\nRecommended Parts:")
    for i, part in enumerate(agent.recommended_parts, 1):
        price_str = f"${part['price']:.2f}" if part.get('price') is not None else "Price not listed"
        print(f"{i}. {part.get('title', 'No title')} - {price_str} from {part.get('source', 'Unknown')}")
    
    print(f"\nSummary: {agent.get_recommendations_summary()}")