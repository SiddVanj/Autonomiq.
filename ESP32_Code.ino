// ESP32 REAL CAN -> HTTP POST Bridge v8.2 (MCP2515 - Heal Action Focus - ALL FIXES)
// - Reads CAN bus via MCP2515. Focuses on DTC Status Byte (Req ID 0x310).
// - If Healable Bit (4-7) active, sends target JSON via HTTP POST per active bit.
// - Sends other metric requests periodically to stimulate bus.
// - Does NOT POST general metrics (rpm, speed, temp).
// - Includes API Key Header. Connects to hotspot.
// - Requires MCP2515 Library (e.g., AUTOWP fork) and Hardware.

#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClient.h>
#include <ArduinoJson.h>
#include <SPI.h>
#include <mcp2515.h> // Assumes AUTOWP or similar ESP32-compatible mcp2515 library

// --- ----------------------------------- ---
// ---           USER CONFIGURATION          ---
// --- ----------------------------------- ---

// WiFi Network Credentials (Hotspot)
const char* ssid = "Lumio-Dev";
const char* password = "omsohamom";

// Backend Server Details (FRIEND'S DEVICE)
const char* server_ip = "172.26.108.184";         // Friend's IP
const int server_port = 8080;                     // Friend's Port
const char* server_endpoint = "/update_health";   // Friend's Endpoint

// Authentication
const char* api_key = "sflNQfPaiL11iSEtzVFcmndhmj4GH11M"; // API Key for friend's server

// MCP2515 Configuration
const int SPI_CS_PIN = 5;                         // <<< VERIFY Your CS Pin Wiring (e.g., GPIO5 for VSPI SS)
#define CAN_INT_PIN 2                             // <<< VERIFY INT Pin GPIO connection
// Use constant defines from mcp2515 library
#define CAN_SPEED CAN_125KBPS                     // <<< VERIFY CAN speed (CAN_125KBPS, CAN_500KBPS etc)
#define CAN_CLOCK MCP_8MHZ                        // <<< VERIFY MCP crystal freq (MCP_8MHZ or MCP_16MHZ)

// Timing Configuration
const unsigned long CYCLE_INTERVAL_MS = 2000;      // Time between requesting OTHER metrics
const unsigned long DTC_REQ_INTERVAL_MS = 1500;    // How often to request the DTC Status Byte (every 1.5s)
const unsigned long RESPONSE_TIMEOUT_MS = 350;     // Max time waiting (no longer used directly by state machine logic)

// Request CAN IDs (Based on Whiteboard)
const uint32_t REQ_ID_RPM = 0x10C;
const uint32_t REQ_ID_SPEED = 0x10D;
const uint32_t REQ_ID_TEMP = 0x105;
const uint32_t REQ_ID_DTC_STATUS = 0x310;

// JSON Buffer Size (For small heal action JSON)
const size_t JSON_BUFFER_SIZE = 256;
// Vehicle/Session IDs for the target JSON
const char* VEHICLE_ID = "ESP32_MCP_HealTrigger_1";
const char* SESSION_ID = "default_session_1";
// --- End Configuration ---

// Global Objects
WiFiClient clientWifi;
HTTPClient http;
MCP2515 mcp2515(SPI_CS_PIN); // Initialize with CS pin
unsigned long lastDtcRequestTime = 0;
unsigned long lastOtherRequestTime = 0; // Timer for less frequent requests
volatile bool can_flag = false; // Set by ISR

// Variables for latest values from CAN bus
byte latest_dtc_status_byte = 0x00;
bool dtc_status_received_flag = false; // Flag to trigger processing in loop

// JSON buffer for heal action payload
StaticJsonDocument<JSON_BUFFER_SIZE> healActionJsonBuffer;

// --- Function Declarations ---
void setup_wifi();
bool postData(String jsonData);
void sendCanRequest(uint32_t reqId);
void checkAndProcessCanMessages();
String mapBitToDtc(byte bitNumber);
void sendHealActionToServer(const char* dtcCode);
void setup_can();
void can_isr();


// ======================= ISR =======================
// Interrupt Service Routine - sets flag when MCP2515 signals message receipt
void IRAM_ATTR can_isr() {
  can_flag = true;
}

// ======================= SETUP =======================
void setup() {
  Serial.begin(115200); while (!Serial);
  Serial.println("\n\nESP32 CAN Heal Trigger -> HTTP Bridge v8.2 (MCP2515)");

  // Initialize Built-in LED if defined by board package (safe if not defined)
  #ifdef LED_BUILTIN
  pinMode(LED_BUILTIN, OUTPUT); digitalWrite(LED_BUILTIN, LOW); // LOW usually OFF for ESP32 devkit blue LED
  #endif

  setup_wifi(); // Connect to WiFi
  setup_can();  // Initialize MCP2515 CAN controller and SPI

  // Configure and attach interrupt AFTER setting up MCP2515
  pinMode(CAN_INT_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(CAN_INT_PIN), can_isr, FALLING);

  Serial.println("Setup Complete. Periodically querying DTC Status...");
  // Make initial request immediately after setup
  sendCanRequest(REQ_ID_DTC_STATUS);
  lastDtcRequestTime = millis();
  lastOtherRequestTime = millis(); // Start timers
  #ifdef LED_BUILTIN
    digitalWrite(LED_BUILTIN, HIGH); // Turn ON (HIGH for common ESP32 LED polarity)
  #endif
}

// ======================= Setup MCP2515 CAN =======================
void setup_can() {
  Serial.println("Init MCP2515...");
  SPI.begin(); // Initialize SPI interface on default ESP32 pins (VSPI: CLK=18, MISO=19, MOSI=23)
  mcp2515.reset(); // Reset MCP2515 device

  // Set Bitrate and Clock Speed
  if (mcp2515.setBitrate(CAN_SPEED, CAN_CLOCK) == MCP2515::ERROR_OK) {
     Serial.println(" MCP2515 Bitrate Set OK.");
  } else {
     Serial.println(" !!! ERROR Setting MCP2515 Bitrate! HALTING."); while(1);
  }

  // Set Mask/Filter 0 for RXB0 to accept ALL standard IDs (0x000 - 0x7FF)
  if (mcp2515.setFilterMask(MCP2515::MASK0, false, 0x000) == MCP2515::ERROR_OK) {
     Serial.println(" MCP2515 Mask 0 Set OK.");
  } else { Serial.println(" !!! ERROR Setting MCP2515 Mask 0!"); }
  if (mcp2515.setFilter(MCP2515::RXF0, false, 0x000) == MCP2515::ERROR_OK) {
     Serial.println(" MCP2515 Filter 0 Set OK.");
  } else { Serial.println(" !!! ERROR Setting MCP2515 Filter 0!"); }
   // NOTE: Default links RXF0/1 to RXB0. RXF2-5 link to RXB1.

  mcp2515.clearInterrupts(); // Clear any pending hardware interrupts

  // --- Enable RX Interrupts (RX0IE and RX1IE flags in CANINTE register) ---
  // Try the direct register modify again, guarded by #ifdef
  #ifdef MCP_CANINTE // Check if this common define exists from library includes
    mcp2515.modifyRegister(MCP_CANINTE, MCP2515::RX0IF | MCP2515::RX1IF, MCP2515::RX0IF | MCP2515::RX1IF);
    Serial.println(" Enabled RX0/RX1 Interrupts via modifyRegister (using defines).");
  #else
    Serial.println(" WARN: MCP_CANINTE not defined, cannot explicitly enable RX interrupts via modifyRegister. Relies on filter/mode settings.");
    // Some library forks might require different methods or do it automatically
  #endif

  // Set Normal Operation Mode
  if (mcp2515.setNormalMode() != MCP2515::ERROR_OK) {
     Serial.println(" !!! ERROR Setting Normal Mode! CAN might not work! HALT."); while(1);
  } else { Serial.println(" MCP2515 Set to Normal Mode OK."); }

  Serial.println(" MCP2515 CAN Initialized.");
}

// ======================= LOOP =======================
void loop() {
  // 0. Maintain WiFi Connection
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost! Reconnecting...");
    #ifdef LED_BUILTIN
       digitalWrite(LED_BUILTIN, LOW); // LED OFF
    #endif
    setup_wifi(); // Attempt reconnect
    delay(1000);
    return; // Don't proceed without WiFi
  } else {
    #ifdef LED_BUILTIN
       digitalWrite(LED_BUILTIN, HIGH); // LED ON
    #endif
  }

  // --- 1. Check if CAN interrupt flag was set ---
  if (can_flag) {
    can_flag = false;             // Reset the software flag immediately
    checkAndProcessCanMessages(); // Read hardware buffer and update globals if DTC response received
    // Interrupt flags in the MCP2515 register are typically cleared by the readMessage function
    // Or explicitly: mcp2515.clearInterrupts(); // Ensure hardware flags are cleared
  }

  unsigned long currentMillis = millis();

  // --- 2. Process DTC Status Byte if Received ---
  if (dtc_status_received_flag) { // Check software flag set by CAN reader
    dtc_status_received_flag = false; // Consume the flag - process this byte only once
    byte statusByte = latest_dtc_status_byte;

    Serial.printf("[PROC] Checking Received DTC Status Byte: 0x%02X\n", statusByte);
    bool healSentThisCheck = false;
    // Loop through healable bits (4, 5, 6, 7)
    for (int bitIndex = 4; bitIndex < 8; bitIndex++) {
      if ((statusByte >> bitIndex) & 0x01) { // Check if bit is 1
        String dtcCode = mapBitToDtc(bitIndex);
        if (dtcCode.length() > 0) {
          Serial.printf(">> Healable Bit %d (%s) ACTIVE -> Sending POST...\n", bitIndex, dtcCode.c_str());
          sendHealActionToServer(dtcCode.c_str()); // Send JSON via HTTP
          healSentThisCheck = true;
        }
      }
    }
    // Only log if heal sent and critical bits are also set
    if (healSentThisCheck && (statusByte & 0x0F)) {
        Serial.println("[PROC] NOTE: Heal Action Sent, but Critical DTC flag bits (0-3) are also active!");
    }
  }

  // --- 3. Periodically send CAN Requests ---
  if (currentMillis - lastDtcRequestTime >= DTC_REQ_INTERVAL_MS) {
    lastDtcRequestTime = currentMillis;
    sendCanRequest(REQ_ID_DTC_STATUS); // Prioritize getting the status byte
  }
  // Optionally request other data less frequently
  if (currentMillis - lastOtherRequestTime >= DTC_REQ_INTERVAL_MS) {
    lastOtherRequestTime = currentMillis;
    sendCanRequest(REQ_ID_RPM); delay(5); // Small delays between requests
    sendCanRequest(REQ_ID_SPEED); delay(5);
    sendCanRequest(REQ_ID_TEMP);
  }

  vTaskDelay(pdMS_TO_TICKS(10)); // Yield for ESP32 tasks
}


// ======================= Send Request via CAN =======================
void sendCanRequest(uint32_t reqId) {
  //Serial.printf("[CAN TX] Requesting ID: 0x%lX\n", reqId); // Verbose log optional
  struct can_frame txFrame;
  txFrame.can_id = reqId;
  txFrame.can_dlc = 0; // Whiteboard seems to use 0-length requests
  if (mcp2515.sendMessage(&txFrame) != MCP2515::ERROR_OK) {
      Serial.printf("!!! CAN TX Failed: 0x%lX\n", reqId);
  }
}

// ======================= Check/Process CAN Msgs =======================
// Reads MCP buffer, specifically looks for DTC Status response, updates global state.
void checkAndProcessCanMessages() {
    struct can_frame frame;
    //Serial.println("  ISR triggered -> Checking MCP buffer..."); // Debug
    // Read all messages to clear buffer and interrupt flag properly
    while (mcp2515.readMessage(&frame) == MCP2515::ERROR_OK) {
        // Process only standard data frames
        if (!(frame.can_id & CAN_EFF_FLAG) && !(frame.can_id & CAN_RTR_FLAG)) {
            uint32_t rxId = frame.can_id;
            uint8_t len = frame.can_dlc;
            uint8_t* rxBuf = frame.data;

            // Check specifically for the DTC Status Response pattern
            // Response ID might be ECU specific (0x7E8-0x7EF) or maybe matches 0x310 request? Assuming matches data for now.
            if (len >= 2 && rxBuf[0] == 0x01) {
                 latest_dtc_status_byte = rxBuf[1];  // Store byte globally
                 dtc_status_received_flag = true;    // Set flag for main loop processing
                 Serial.printf("   [CAN RX Match] Received DTC Status Byte = 0x%02X\n", latest_dtc_status_byte);
            }
            // We *ignore* RPM, Speed, Temp responses here, as this sketch only *acts* on the DTC status byte
        }
    } // end while messages available
}

// ======================= Map Healable Bit to DTC Code =======================
String mapBitToDtc(byte bitNumber) { // Unchanged
  switch(bitNumber) { case 4: return "P0128"; case 5: return "C0073"; case 6: return "U0100"; case 7: return "P03D0"; default: return ""; }
}

// ======================= Send Specific Heal Action JSON =======================
void sendHealActionToServer(const char* dtcCode) { // Unchanged
    healActionJsonBuffer.clear();
    healActionJsonBuffer["dtc_code"] = dtcCode;
    healActionJsonBuffer["action"] = "heal";
    healActionJsonBuffer["vehicle_id"] = VEHICLE_ID;
    healActionJsonBuffer["session_id"] = SESSION_ID;
    String outputPayload = ""; serializeJson(healActionJsonBuffer, outputPayload);
    Serial.print("[POST Action] JSON: "); Serial.println(outputPayload);
    if (!postData(outputPayload)) { Serial.println(" >> Heal Action POST Failed <<"); }
}

// ======================= WiFi Setup =======================
void setup_wifi() { // Unchanged
  delay(10); Serial.println(); Serial.print("WiFi: "); Serial.print(ssid);
  WiFi.mode(WIFI_STA); WiFi.begin(ssid, password); Serial.print("...");
  int a=0; while(WiFi.status()!=WL_CONNECTED&&a++<30){delay(500);Serial.print(".");}
  if(WiFi.status()==WL_CONNECTED){ Serial.print(" OK! IP:"); Serial.println(WiFi.localIP());} else{Serial.println(" FAIL!");}
}

// ======================= HTTP POST =======================
bool postData(String jsonData) { // Unchanged
  bool success=false; if(WiFi.status()!=WL_CONNECTED){Serial.println("[HTTP] No WiFi");return false;}
  String path="http://"+String(server_ip)+":"+String(server_port)+String(server_endpoint);
  http.end(); if(http.begin(clientWifi, path)){ // Use global clientWifi
    http.addHeader("Content-Type", "application/json"); http.addHeader("X-DEVICE-API-KEY", api_key);
    int code = http.POST(jsonData);
    if (code>=200 && code<300) success=true; else {String p=http.getString(); Serial.printf("[HTTP] Err %d: %s\n",code,p.c_str());} http.end();
  } else { Serial.printf("[HTTP] Conn Err: %s\n", path.c_str()); }
  return success;
}

