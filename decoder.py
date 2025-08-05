# canary_ai/decoder.py
import struct

# --- Mini DBC (CAN Database) ---
# Define how to interpret data for specific CAN IDs
# Format: { can_id: { 'name': 'SomeName', 'signals': { 'signal_name': (start_bit, length, scale, offset, unit), ... } } }
# Or a simpler format for direct values if bytes map directly
DBC = {
    0x1F4: { # ENGINE_PARAMS_1
        'name': 'Engine Parameters 1',
        'signals': {
            'EngineRPM':    (0, 16, 0.25, 0, 'RPM'),   # Bytes 0, 1 - Scale 0.25 RPM/bit
            'VehicleSpeed': (16, 8, 1.0, 0, 'km/h'), # Byte 2 - Scale 1 km/h/bit
        }
    },
    0x2A0: { # ENGINE_PARAMS_2
         'name': 'Engine Parameters 2',
         'signals': {
             'EngineTemp':  (0, 8, 1.0, -40, 'C'),      # Byte 0 - Offset -40 C
             'FuelLevel':   (8, 8, (100.0/255.0), 0, '%'), # Byte 1 - Scale to 0-100%
         }
    },
    0x7E8: { # DIAGNOSTIC_RESPONSE (Simplified DTC check)
         'name': 'Diagnostic Response',
         'signals': {
              # We'll parse DTCs more directly in the function for this example
         }
    }
    # Add more IDs and signals as needed based on your emulator/car
}

def decode_message(can_id, data_bytes):
    """
    Decodes a CAN message based on the hardcoded DBC.
    Returns a dictionary of decoded values or None if ID is unknown.
    Also checks for specific DTC patterns in diagnostic responses.
    """
    if can_id not in DBC:
        return None # Unknown ID

    decoded_info = {'id': can_id, 'name': DBC[can_id]['name'], 'metrics': {}, 'dtcs': []}
    message_def = DBC[can_id]

    # --- Specific DTC Parsing for OBD-II Response (Example) ---
    if can_id == 0x7E8 and len(data_bytes) >= 4:
        # Very basic check: Does it look like a positive DTC response?
        # Mode 03 (Show stored DTCs) response often starts with 0x43.
        # Or, based on our emulator: data[0]=0x02 indicates a DTC count
        if data_bytes[0] == 0x02 and data_bytes[1] == 0x01: # Matches our emulator's pattern
             # Reconstruct DTC from bytes 2 and 3
             byte2 = data_bytes[2]
             byte3 = data_bytes[3]
             # Determine first character (P, C, B, U) based on high bits of byte2
             prefix_map = {0: 'P', 1: 'C', 2: 'B', 3: 'U'}
             prefix = prefix_map.get((byte2 >> 6) & 0x03, '?')
             # Format the rest of the code (BCD or hex encoded)
             dtc_code = f"{prefix}{(byte2 & 0x3F):02X}{byte3:02X}"
             decoded_info['dtcs'].append(dtc_code)
             # For this simple example, we won't decode other signals if it's a DTC response
             return decoded_info


    # --- General Signal Decoding ---
    if 'signals' in message_def:
         # Convert data bytes to an integer for easier bit manipulation
         data_int = int.from_bytes(data_bytes, byteorder='little') # Assuming little-endian

         for sig_name, (start_bit, length, scale, offset, unit) in message_def['signals'].items():
             # Create a mask for the signal
             mask = (1 << length) - 1
             # Extract the signal's raw value
             raw_value = (data_int >> start_bit) & mask
             # Apply scaling and offset
             physical_value = raw_value * scale + offset
             decoded_info['metrics'][sig_name] = {'value': round(physical_value, 2), 'unit': unit}

    return decoded_info

# Example usage (if run directly)
if __name__ == "__main__":
    # Simulate Engine RPM=2000 (2000 / 0.25 = 8000 = 0x1F40), Speed=50km/h (0x32)
    test_data_1f4 = bytes([0x40, 0x1F, 0x32, 0x00, 0x00, 0x00, 0x00, 0x00])
    decoded_1f4 = decode_message(0x1F4, test_data_1f4)
    print(f"Decoded 0x1F4: {decoded_1f4}")

    # Simulate Temp=95C (95 + 40 = 135 = 0x87), Fuel=50% (50 * 2.55 = 127.5 -> 128 = 0x80)
    test_data_2a0 = bytes([0x87, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    decoded_2a0 = decode_message(0x2A0, test_data_2a0)
    print(f"Decoded 0x2A0: {decoded_2a0}")

    # Simulate DTC P0128 response
    test_data_dtc = bytes([0x02, 0x01, 0x01, 0x28, 0x00, 0x00, 0x00, 0x00])
    decoded_dtc = decode_message(0x7E8, test_data_dtc)
    print(f"Decoded 0x7E8 (DTC): {decoded_dtc}")

    # Simulate unknown ID
    decoded_unknown = decode_message(0x999, bytes([0x01, 0x02]))
    print(f"Decoded 0x999: {decoded_unknown}")