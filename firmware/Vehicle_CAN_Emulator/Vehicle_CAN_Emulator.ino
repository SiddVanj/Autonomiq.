/*
 * @file     Vehicle_Emulator.ino
 * @brief    Vehicle CAN Emulator for Autonomiq Demo
 * @author   Om Anavekar
 * @date     April 2024
 */

#include <Ticker.h>
#include <avr/sleep.h>
#include <SPI.h>
#include <mcp2515.h>
#include <OneButton.h>

#define ENGINE_FAULT_PIN A1
#define CHECK_ENGINE_LED_PIN 4

// CAN pins
#define CAN_INT_PIN 2
#define CAN_STANDBY_PIN 8
#define CAN_RESET_PIN 9
#define CAN_nCS_PIN 10

// CAN messages
#define RPM_CAN_ID 0x10C
#define SPEED_CAN_ID 0x10D
#define TEMP_CAN_ID 0x105

#define DTC_CAN_ID 0x310
#define DTC_CLR_4 0x304
#define DTC_CLR_5 0x305
#define DTC_CLR_6 0x306
#define DTC_CLR_7 0x307

#define DEBUG_MODE 1
#define Serial if(DEBUG_MODE)Serial

// DTC flags
#define NUM_DTC_FLAGS 8
bool dtc_flags[NUM_DTC_FLAGS] = { false };

// Button instance
OneButton button(ENGINE_FAULT_PIN, true);

// ISR flags
volatile bool can_flag = false;

// CAN variables
struct can_frame frame;
MCP2515 mcp2515(CAN_nCS_PIN);
bool can_ok = true;

/**
 * @brief Interrupt Service Routine (ISR) for handling CAN interrupts.
 */
void can_isr() {
  can_flag = true;  // Set CAN flag to true, indicating a CAN message is received
}

/**
 * @brief Randomly select a DTC flag high.
 */
void set_dtc() {
  digitalWrite(CHECK_ENGINE_LED_PIN, HIGH);
  int index = random(NUM_DTC_FLAGS);
  dtc_flags[index] = true;
  Serial.print("DTC Set at index: ");
  Serial.println(index);
}

/**
 * @brief Sets a DTC flag low.
 */
void clear_dtc_flag(int index) {
  dtc_flags[index] = false;
  Serial.print("DTC Reset at index: ");
  Serial.println(index);

  bool all_off_flag = true;
  for (int i = 0; i < NUM_DTC_FLAGS; i++) {
    if (dtc_flags[i]) {
      all_off_flag = false;
    }
  }
  digitalWrite(CHECK_ENGINE_LED_PIN, !all_off_flag);
}

/**
 * @brief Set all DTC flags low.
 */
void reset_dtc_array() {
  digitalWrite(CHECK_ENGINE_LED_PIN, LOW);
  for (int i = 0; i < NUM_DTC_FLAGS; i++) {
    dtc_flags[i] = false;
  }
  Serial.println("DTC array reset");
}

/**
 * @brief Helper function to print the contents of a CAN message.
 *
 * @param msg The CAN frame to be printed.
 */
void printCANMessage(const struct can_frame &msg) {
    // Print the CAN ID in hexadecimal
    Serial.print("ID: 0x");
    Serial.print(msg.can_id, HEX);
    
    // Print the Data Length Code (DLC)
    Serial.print(" DLC: ");
    Serial.print(msg.can_dlc, HEX);
    
    // Print the data bytes in hexadecimal
    Serial.print(" Data: ");
    for (int i = 0; i < msg.can_dlc; i++) {
        Serial.print(msg.data[i], HEX);
        Serial.print(" ");
    }
    Serial.println();
}

/**
 * @brief Processes incoming CAN messages and performs actions based on message content.
 * Handles messages from both RXB0 and RXB1 buffers.
 */
void process_can_msgs() {
  uint8_t irq = mcp2515.getInterrupts();  // Retrieve CAN controller interrupt flags

  // Process messages in RXB0 buffer
  if (irq & MCP2515::CANINTF_RX0IF) {
    if (mcp2515.readMessage(MCP2515::RXB0, &frame) == MCP2515::ERROR_OK) {
      Serial.print("Match (RXB0) -> ");
      printCANMessage(frame);

      switch (frame.can_id) {
        case DTC_CAN_ID: { // DTC
          struct can_frame resp;
          resp.can_id  = 0x7E8;
          resp.can_dlc = 2;
          resp.data[0] = 0x01;  // Custom response type
          resp.data[1] = 0x00;

          // Pack 8 flags into 1 byte
          for (int i = 0; i < NUM_DTC_FLAGS; i++) {
            resp.data[1] |= (dtc_flags[i] ? (1 << i) : 0);
          }

          mcp2515.sendMessage(&resp);
          Serial.print("Replied with DTC flags: ");
          Serial.println(resp.data[1], BIN);
          break;
        }
        case DTC_CLR_4: { // DTC
          clear_dtc_flag(4);
          break;
        }
        case DTC_CLR_5: { // DTC
          clear_dtc_flag(5);
          break;
        }
        case DTC_CLR_6: { // DTC
          clear_dtc_flag(6);
          break;
        }
        case DTC_CLR_7: { // DTC
          clear_dtc_flag(7);
          break;
        }
                case RPM_CAN_ID: { // RPM

          uint16_t raw_rpm = (1000 + random(0, 200)) * 4;
          uint8_t A = (raw_rpm >> 8) & 0xFF;
          uint8_t B = raw_rpm & 0xFF;

          struct can_frame resp;
          resp.can_id  = 0x7E8;
          resp.can_dlc = 5;
          resp.data[0] = 0x04;
          resp.data[1] = 0x41;
          resp.data[2] = 0x0C;
          resp.data[3] = A;
          resp.data[4] = B;
          mcp2515.sendMessage(&resp);

          Serial.println("Replied with RPM");
          break;
        }
        case SPEED_CAN_ID: { // Speed

          struct can_frame resp;
          resp.can_id  = 0x7E8;
          resp.can_dlc = 4;
          resp.data[0] = 0x03;
          resp.data[1] = 0x41;
          resp.data[2] = 0x0D;
          resp.data[3] = 20 + random(0, 30);
          mcp2515.sendMessage(&resp);

          Serial.println("Replied with Speed");
          break;
        }
        case TEMP_CAN_ID: { // Coolant temp

          struct can_frame resp;
          resp.can_id  = 0x7E8;
          resp.can_dlc = 4;
          resp.data[0] = 0x03;
          resp.data[1] = 0x41;
          resp.data[2] = 0x05;
          resp.data[3] = 50 + random(0, 70);
          mcp2515.sendMessage(&resp);

          Serial.println("Replied with Coolant Temp");
          break;
        }
        default:
          Serial.print("Unknown PID");
          break;
      }
    }
  }

  // Clear the CAN controller interrupt flags
  mcp2515.clearInterrupts();
}

/**
 * @brief Initializes the CAN interface by configuring pins, MCP2515 settings, and performing a self-test.
 * 
 * @return bool True if the initialization and self-test were successful, false otherwise.
 */
bool can_init() {
  // Configure CAN control pins as outputs
  pinMode(CAN_RESET_PIN, OUTPUT);
  pinMode(CAN_STANDBY_PIN, OUTPUT);

  // Set initial states for CAN control pins
  digitalWrite(CAN_RESET_PIN, HIGH);
  digitalWrite(CAN_STANDBY_PIN, LOW);

  // Initialize MCP2515 CAN controller
  mcp2515.reset();  // Reset MCP2515 to default settings
  mcp2515.setBitrate(CAN_125KBPS, MCP_8MHZ);  // Set CAN bitrate to 125 kbps
  mcp2515.setConfigMode();  // Enter configuration mode

  // Perform self-test by sending a test message and checking for reception
  bool result = true;
  mcp2515.setLoopbackMode();  // Enter loopback mode to test transmission and reception

  struct can_frame test_msg;
  test_msg.can_id  = 0x311;  // Test CAN ID
  test_msg.can_dlc = 1;  // Test data length
  test_msg.data[0] = 0xAB;  // Test data byte

  mcp2515.sendMessage(&test_msg);  // Send test message
  delay(500);  // Wait for message to be processed
  uint8_t irq = mcp2515.getInterrupts();  // Retrieve CAN controller interrupts

  if (irq & MCP2515::CANINTF_RX1IF) {  // Check if message received
    if (mcp2515.readMessage(MCP2515::RXB1, &frame) != MCP2515::ERROR_OK) {
      result = true;  // Self-test failed
    }
  } else {
    result = false;  // Self-test failed
  }

  mcp2515.clearInterrupts();  // Clear interrupt flags
  mcp2515.setNormalMode();  // Return to normal operation mode
  return result;  // Return the result of the self-test
}

/**
 * @brief Initializes the system, configures pins, sets up peripherals, and starts initial tasks.
 */
void setup() {
  // Start serial communication for debugging
  Serial.begin(9600);
  Serial.println("----- Autonomiq Vehicle Emulator -----");

  // Initialize CAN interface
  if (!can_init()) {
    Serial.println("CAN initialization failed!");
    can_ok = false;  // Mark CAN initialization as failed
  }

  // Attach interrupts
  attachInterrupt(digitalPinToInterrupt(CAN_INT_PIN), can_isr, FALLING);

  pinMode(CHECK_ENGINE_LED_PIN, OUTPUT);
  for (int i = 0; i < 3; i++) {
    digitalWrite(CHECK_ENGINE_LED_PIN, HIGH);
    delay(100);
    digitalWrite(CHECK_ENGINE_LED_PIN, LOW);
    delay(100);
  }
  pinMode(ENGINE_FAULT_PIN, INPUT_PULLUP);
  button.attachClick(set_dtc);
  button.attachLongPressStop(reset_dtc_array);
}

/**
 * @brief Main loop that manages system tasks, including state machine execution, CAN message processing, and ticker updates.
 */
void loop() {
  // Process CAN messages if flag is set
  if (can_flag) {
    process_can_msgs();
    can_flag = false;  // Reset flag after processing
  }

  button.tick();  // Handle button events
}

