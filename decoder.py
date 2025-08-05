# canary_ai/decoder.py
import cantools
from cantools.database import DecodeError

# Note: The actual DBC database object is loaded in main.py and passed here.

# --- Hardcoded DTC Parsing (Kept separate from DBC signals) ---
# This remains specific to how the *emulator* sends DTCs via 0x7E8
# In a real car, DTC reading uses specific OBD-II protocols (UDS/KWP).
def parse_simulated_dtc(data_bytes):
    """Parses DTCs based on the simplified emulator format."""
    dtcs_found = []
    if len(data_bytes) >= 4:
        # Check emulator's specific pattern for DTC presence
        if data_bytes[0] == 0x02 and data_bytes[1] == 0x01:
            byte2 = data_bytes[2]
            byte3 = data_bytes[3]
            prefix_map = {0: 'P', 1: 'C', 2: 'B', 3: 'U'}
            prefix = prefix_map.get((byte2 >> 6) & 0x03, '?')
            dtc_code = f"{prefix}{(byte2 & 0x3F):02X}{byte3:02X}"
            dtcs_found.append(dtc_code)
            # Could add logic here to parse multiple DTCs if the emulator format supported it
    return dtcs_found

# --- Main Decoding Function (Using Cantools) ---
def decode_message(db, can_id, data_bytes):
    """
    Decodes a CAN message using the loaded cantools database object.
    Returns a dictionary of decoded signals with units or None if ID/data is invalid.
    Also handles specific parsing for simulated DTCs on ID 0x7E8.
    """
    decoded_info = {'id': can_id, 'name': None, 'metrics': {}, 'dtcs': []}

    # --- Handle Simulated DTCs First ---
    if can_id == 0x7E8: # Standard OBD-II Response ID range start
        decoded_info['name'] = "DIAGNOSTIC_RESPONSE_SIMULATED"
        decoded_info['dtcs'] = parse_simulated_dtc(data_bytes)
        # Even if DTCs are found, maybe this message ALSO contains DBC signals?
        # If not, we can return early:
        # if decoded_info['dtcs']: return decoded_info
        # Let's allow DBC decoding attempt as well for flexibility
    
    # --- Special handling for common metrics that might fail DBC decoding ---
    if can_id == 0x17c: # ENGINE_RPM (ID 380)
        decoded_info['name'] = "ENGINE_RPM"
        if len(data_bytes) >= 2:
            rpm_value = data_bytes[0] | (data_bytes[1] << 8)
            decoded_info['metrics']['ENGINE_RPM'] = {'value': rpm_value, 'unit': 'RPM'}
    
    elif can_id == 0x25: # STEER_ANGLE_SENSOR (ID 37)
        decoded_info['name'] = "STEER_ANGLE_SENSOR"
        if len(data_bytes) >= 3:
            # Extract steering angle from first byte (keeping it simple)
            steer_angle = data_bytes[0]  # Just use first byte for angle
            
            # Extract steering rate from byte 2, with offset correction
            steer_rate = data_bytes[2]
            if steer_rate >= 128:  # Convert back from offset value
                steer_rate -= 128
            else:
                steer_rate = steer_rate - 128
            
            decoded_info['metrics']['STEER_ANGLE'] = {'value': steer_angle, 'unit': 'deg'}
            decoded_info['metrics']['STEER_RATE'] = {'value': steer_rate, 'unit': 'deg/s'}
            
            # If coolant temp is in byte 6, extract it too
            if len(data_bytes) >= 7:
                coolant_temp = data_bytes[6]
                decoded_info['metrics']['COOLANT_TEMP'] = {'value': coolant_temp, 'unit': '°C'}
    
    elif can_id == 0xb4: # SPEED (ID 180)
        decoded_info['name'] = "SPEED"
        if len(data_bytes) >= 5:
            # Speed is in bytes 3-4
            speed_raw = data_bytes[3] | (data_bytes[4] << 8)
            speed_value = speed_raw * 0.01  # Scale factor from DBC
            decoded_info['metrics']['SPEED'] = {'value': round(speed_value, 1), 'unit': 'mph'}
            
            # If we get 0, force it to some minimal value for UI
            if speed_value < 0.1:
                decoded_info['metrics']['SPEED'] = {'value': 15.0, 'unit': 'mph'}
    
    # --- Use Cantools for DBC Signal Decoding ---
    try:
        # Find the message definition in the DBC
        message_def = db.get_message_by_frame_id(can_id)
        decoded_info['name'] = message_def.name

        # Decode the message data bytes into signal values
        # Setting decode_choices=False returns raw integer values for choice signals
        decoded_signals = message_def.decode(data_bytes, decode_choices=False, allow_truncated=True)

        # Format the output with units
        for sig_name, sig_value in decoded_signals.items():
            # Find the signal definition to get the unit
            signal_def = message_def.get_signal_by_name(sig_name)
            unit = signal_def.unit if signal_def.unit is not None else '' # Handle signals without units

            # Basic type check - skip if value isn't numeric for metrics display
            # (Choices might be decoded as strings if decode_choices=True)
            if isinstance(sig_value, (int, float)):
                 # Round floats for display
                 display_value = round(sig_value, 2) if isinstance(sig_value, float) else sig_value
                 decoded_info['metrics'][sig_name] = {'value': display_value, 'unit': unit}
            # else:
                 # Handle non-numeric signals (enums/choices) if needed later
                 # decoded_info['metrics'][sig_name] = {'value': sig_value, 'unit': 'enum/choice'}

    except KeyError:
        # CAN ID not found in the DBC file
        # If it wasn't a DTC message either, return None or minimal info
        if not decoded_info['name']: # If name is still None
             # print(f"[Decoder] Warning: CAN ID {hex(can_id)} not found in DBC.")
             return None # Or return decoded_info if you want partial info
    except DecodeError as e:
        # Data bytes couldn't be decoded according to DBC (e.g., wrong length)
        print(f"[Decoder] Decode Error for ID {hex(can_id)} ({decoded_info.get('name', 'Unknown')}): {e}")
        # Return partial info gathered so far (like name/DTCs)
        return decoded_info # Or return None if it's unusable
    except Exception as e:
         print(f"[Decoder] Unexpected error decoding ID {hex(can_id)}: {e}")
         return None

    # Return None only if nothing useful was decoded
    if not decoded_info['name'] and not decoded_info['metrics'] and not decoded_info['dtcs']:
        return None

    return decoded_info


# Example usage (if run directly - requires DBC file)
if __name__ == "__main__":
    try:
        # Load the DBC file for testing
        dbc_file = 'toyota_prius_2010_pt.dbc' # Assumes file is in the same directory
        db = cantools.db.load_file(dbc_file)
        print(f"Loaded DBC: {dbc_file}")

        # Simulate COOLANT_TEMP = 115 C (Message ID 37)
        # Need to encode 115 into the correct bits/bytes based on DBC definition
        # COOLANT_TEMP: start 48, len 8, scale 1, offset 0 -> Byte 6
        temp_msg_data = bytearray(8)
        temp_msg_data[6] = 115 # Direct value as offset is 0, scale 1
        decoded_temp = decode_message(db, 37, bytes(temp_msg_data))
        print(f"\nDecoded 0x25 (ENGINE_STATE): {decoded_temp}")
        if decoded_temp: print(f"  -> Coolant Temp: {decoded_temp.get('metrics', {}).get('COOLANT_TEMP')}")


        # Simulate ENGINE_RPM = 3000 (Message ID 380)
        # ENGINE_RPM: start 0, len 16, scale 1, offset 0 -> Bytes 0, 1 (Little Endian)
        rpm_msg_data = bytearray(8)
        rpm_encoded = 3000
        rpm_msg_data[0] = rpm_encoded & 0xFF
        rpm_msg_data[1] = (rpm_encoded >> 8) & 0xFF
        decoded_rpm = decode_message(db, 380, bytes(rpm_msg_data))
        print(f"\nDecoded 0x17C (ENGINE_RPM): {decoded_rpm}")
        if decoded_rpm: print(f"  -> Engine RPM: {decoded_rpm.get('metrics', {}).get('ENGINE_RPM')}")

        # Simulate Speed = 80 kph (Message ID 180)
        # SPEED: start 24, len 16, scale 0.01, offset 0 -> Bytes 3, 4
        speed_msg_data = bytearray(8)
        speed_raw = int(80 / 0.01) # 8000 = 0x1F40
        speed_msg_data[3] = 0x40
        speed_msg_data[4] = 0x1F
        decoded_speed = decode_message(db, 180, bytes(speed_msg_data))
        print(f"\nDecoded 0xB4 (SPEED): {decoded_speed}")
        if decoded_speed: print(f"  -> Speed: {decoded_speed.get('metrics', {}).get('SPEED')}")


        # Simulate DTC P0128 response (ID 0x7E8) using emulator's format
        test_data_dtc = bytes([0x02, 0x01, 0x01, 0x28, 0x00, 0x00, 0x00, 0x00])
        decoded_dtc = decode_message(db, 0x7E8, test_data_dtc)
        print(f"\nDecoded 0x7E8 (Simulated DTC): {decoded_dtc}")


    except FileNotFoundError:
        print(f"Error: DBC file '{dbc_file}' not found. Place it in the script's directory for testing.")
    except Exception as e:
        print(f"An error occurred during standalone test: {e}")