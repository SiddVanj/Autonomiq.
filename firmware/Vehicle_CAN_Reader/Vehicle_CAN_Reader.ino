// ESP8266 FAKE Vehicle Data -> HTTP POST Bridge V4.2
// - Sends structured vehicle data JSON {vin, speed[], rpm[], temp[], dtcs[]}
// - Includes X-DEVICE-API-KEY header
// - Generates FAKE data internally (No CAN hardware needed)
// - Connects to your specified WiFi hotspot
// - Sends data to EXT_DEV'S IP ADDRESS AND ENDPOINT

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h> // Library for making HTTP requests
#include <WiFiClient.h>       // Required by HTTPClient
#include <ArduinoJson.h> // For formatting outgoing data

// --- ----------------------------------- ---
// ---           USER CONFIGURATION          ---
// --- ----------------------------------- ---

// WiFi Network Credentials (FOR YOUR HOTSPOT)
const char* ssid = "Lumio-Dev";
const char* password = "omsohamom";

// --- <<< Backend Server Details (EXT_DEV'S DEVICE) >>> ---
const char* server_ip = "172.26.108.184"; // <<< EXT_DEV'S IP ADDRESS
const int server_port = 8080;             // <<< EXT_DEV'S PORT
const char* server_endpoint = "/update_health"; // <<< EXT_DEV'S ENDPOINT
// --- <<< --------------------------------------- >>> ---

// Authentication - API Key needed by EXT_DEV's endpoint
const char* api_key = "sflNQfPaiL11iSEtzVFcmndhmj4GH11M"; // Your API Key

// Data Simulation Configuration
const unsigned long POST_INTERVAL_MS = 10000; // Send simulated data every 5 seconds
const unsigned long SAMPLE_INTERVAL_MS = 500; // Generate a sample reading every 0.5 seconds
const int SAMPLES_PER_POST = (POST_INTERVAL_MS / SAMPLE_INTERVAL_MS); // Samples per POST = 10
const size_t JSON_BUFFER_SIZE = 1024; // 1KB Buffer Size
const unsigned long DTC_INTERVAL_MIN_MS = 45000; // Min time between simulated DTCs
const unsigned long DTC_INTERVAL_MAX_MS = 90000; // Max time between simulated DTCs
const unsigned long DTC_DURATION_MS = 15000;     // How long a simulated DTC stays active

// --- ----------------------------------- ---
// ---        END USER CONFIGURATION         ---
// --- ----------------------------------- ---

// Global Objects
WiFiClient clientWifi;
HTTPClient http;
unsigned long lastPostTime = 0;
unsigned long lastSampleTime = 0;

// JSON Buffer for data to be posted
StaticJsonDocument<JSON_BUFFER_SIZE> postJsonBuffer;
// Temporary storage arrays for samples
float speed_samples[SAMPLES_PER_POST];
float rpm_samples[SAMPLES_PER_POST];
float temp_samples[SAMPLES_PER_POST];
int sample_count = 0;

// Simulated Vehicle State Variables
const char* sim_vin = "1G1YR26R395800228";
float sim_rpm_current = 750.0;
float sim_speed_current = 0.0;
float sim_temp_current = 85.0;
String sim_active_dtcs[3];
int sim_active_dtc_count = 0;
unsigned long dtc_start_time = 0;
unsigned long next_dtc_possible_time = 0;

// --- Function Declarations ---
void setup_wifi();
bool postData(String jsonData);
void generateFakeVehicleReading();

// =======================
//     SETUP
// =======================
void setup() {
  Serial.begin(115200);
  while (!Serial);
  Serial.println("\n\nESP8266 FAKE Vehicle Data -> HTTP Bridge v4.2 (Sending)");
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH); // LED Off

  randomSeed(analogRead(A0) + ESP.getChipId());
  setup_wifi(); // Connect to WiFi

  Serial.println("Setup Complete.");
  Serial.print("Collecting "); Serial.print(SAMPLES_PER_POST); Serial.print(" samples, POSTing every ");
  Serial.print(POST_INTERVAL_MS / 1000.0); Serial.print("s ");
  Serial.print("to http://"); Serial.print(server_ip); Serial.print(":"); Serial.print(server_port);
  Serial.println(server_endpoint);
  Serial.println("X-DEVICE-API-KEY header will be included.");

  next_dtc_possible_time = millis() + random(15000, 30000); // Schedule first potential DTC
  digitalWrite(LED_BUILTIN, LOW); // LED On indicates running
}

// =======================
//     LOOP
// =======================
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost! Reconnecting...");
    digitalWrite(LED_BUILTIN, HIGH); setup_wifi();
    lastPostTime = millis(); lastSampleTime = millis(); sample_count = 0;
    delay(1000); return;
  } else { digitalWrite(LED_BUILTIN, LOW); }

  unsigned long currentMillis = millis();

  // --- 1. Periodically generate a FAKE sensor reading & update DTC state ---
  if (currentMillis - lastSampleTime >= SAMPLE_INTERVAL_MS) {
    lastSampleTime = currentMillis;
    generateFakeVehicleReading(); // Update sim variables AND DTC state
    // Store the sample if buffer has space
    if (sample_count < SAMPLES_PER_POST) {
        speed_samples[sample_count] = sim_speed_current;
        rpm_samples[sample_count] = sim_rpm_current;
        temp_samples[sample_count] = sim_temp_current;
        sample_count++;
    }
  }

  // --- 2. Periodically POST the collected samples ---
  if (currentMillis - lastPostTime >= POST_INTERVAL_MS) {
    lastPostTime = currentMillis;
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN)); // Toggle LED

    if (sample_count > 0) {
        postJsonBuffer.clear(); // Clear previous data
        postJsonBuffer["vin"] = sim_vin;
        JsonArray speedArray = postJsonBuffer.createNestedArray("speed");
        JsonArray rpmArray = postJsonBuffer.createNestedArray("rpm");
        JsonArray tempArray = postJsonBuffer.createNestedArray("temp");
        JsonArray dtcsArray = postJsonBuffer.createNestedArray("dtcs");

        for (int i = 0; i < sample_count; i++) {
            speedArray.add(round(speed_samples[i]*10)/10.0);
            rpmArray.add(round(rpm_samples[i]));
            tempArray.add(round(temp_samples[i]*10)/10.0);
        }
        for(int i=0; i < sim_active_dtc_count; i++){ dtcsArray.add(sim_active_dtcs[i]); }

        String outputJsonString;
        serializeJson(postJsonBuffer, outputJsonString);

        Serial.printf("Collected %d samples. Sending POST to %s...\n", sample_count, server_ip);
        if (postData(outputJsonString)) {
            Serial.println("POST successful.");
            sample_count = 0; // Reset count on success
        } else {
            Serial.println("POST Failed.");
            sample_count = 0; // Reset count on failure (simple mode, data lost)
        }
    } else { Serial.println("No samples collected to POST."); }

    digitalWrite(LED_BUILTIN, LOW); // LED back on solid
  }
  yield();
}


// =======================
// Generate Fake Vehicle Reading & Update DTC State
// =======================
void generateFakeVehicleReading() { // (Implementation unchanged from V4.1)
    unsigned long currentMillis = millis();
    // Update metrics
    if (sim_speed_current < 2) { sim_rpm_current += random(-20, 25); sim_rpm_current = max(700.0f, min(850.0f, sim_rpm_current)); }
    else { sim_rpm_current += random(-60, 65); sim_rpm_current = max(900.0f, min(3000.0f, sim_rpm_current)); }
    sim_speed_current = max(0.0f, (sim_rpm_current - 700) / 30.0f + random(-2, 2));
    sim_temp_current += random(-35, 40) / 100.0; sim_temp_current = max(85.0f, min(105.0f, sim_temp_current));

    // Update DTC state
    if (sim_active_dtc_count == 0 && currentMillis >= next_dtc_possible_time) {
        int chance = random(100);
        if (chance < 15) {
             sim_active_dtcs[0] = "P0128"; sim_active_dtc_count = 1; dtc_start_time = currentMillis;
             Serial.printf("\n--- Simulating DTC %s Start ---\n", sim_active_dtcs[0].c_str());
        } else if (chance < 25) {
             sim_active_dtcs[0] = "P0301"; sim_active_dtc_count = 1; dtc_start_time = currentMillis;
             Serial.printf("\n--- Simulating DTC %s Start ---\n", sim_active_dtcs[0].c_str());
        } // Add more conditions for other DTCs if needed
         // If no DTC triggered, schedule next check relatively soon
        next_dtc_possible_time = currentMillis + random(5000, 10000);

    } else if (sim_active_dtc_count > 0) {
        if (currentMillis - dtc_start_time > DTC_DURATION_MS) {
             Serial.printf("--- Simulating DTCs End (Count: %d) ---\n", sim_active_dtc_count);
             sim_active_dtc_count = 0; // Clear active count
             next_dtc_possible_time = currentMillis + random(DTC_INTERVAL_MIN_MS, DTC_INTERVAL_MAX_MS);
        }
    }
}

// =======================
// WiFi Setup Implementation
// =======================
void setup_wifi() { // (Implementation unchanged from V4.1)
  delay(10); Serial.println();
  Serial.print("Connecting to WiFi: "); Serial.println(ssid);
  WiFi.mode(WIFI_STA); WiFi.begin(ssid, password);
  Serial.print("Waiting for connection");
  int attempt = 0;
  while (WiFi.status() != WL_CONNECTED && attempt++ < 30) { delay(500); Serial.print("."); }
  if (WiFi.status() == WL_CONNECTED) { Serial.println("\nWiFi connected!"); Serial.print("IP Address: "); Serial.println(WiFi.localIP()); }
  else { Serial.println("\nWiFi FAILED!"); }
}

// =======================
// HTTP POST Implementation (With API Key Header)
// =======================
bool postData(String jsonData) { // (Implementation unchanged from V4.1 - API key included)
  bool success = false;
  if (WiFi.status() == WL_CONNECTED) {
    String serverPath = "http://" + String(server_ip) + ":" + String(server_port) + String(server_endpoint);
    // Close previous connection just in case (helps stability on ESP8266 sometimes)
    http.end();
    bool beginOk = http.begin(clientWifi, serverPath);
    if (beginOk) {
      http.addHeader("Content-Type", "application/json");
      http.addHeader("X-DEVICE-API-KEY", api_key); // Send API Key
      int httpResponseCode = http.POST(jsonData);
      if (httpResponseCode > 0) {
        if (httpResponseCode >= 200 && httpResponseCode < 300) { success = true; }
        else { String payload = http.getString(); Serial.printf("[HTTP] POST Failed, Status %d, Response: %s\n", httpResponseCode, payload.c_str()); }
      } else { Serial.printf("[HTTP] POST Error: %s\n", http.errorToString(httpResponseCode).c_str()); }
      http.end();
    } else { Serial.printf("[HTTP] Conn Error: Cannot begin HTTP for %s\n", serverPath.c_str()); }
  } else { Serial.println("[HTTP] Cannot POST: WiFi disconnected."); }
  return success;
}