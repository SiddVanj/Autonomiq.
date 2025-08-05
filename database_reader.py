import time
import queue
import threading
from utils.DBops.gcloud import VehicleHealthDB
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DatabaseReader:
    def __init__(self, message_queue, db_config):
        self.message_queue = message_queue
        self._running = False
        self._thread = None
        self._lock = threading.Lock()  # For thread safety
        self.db = VehicleHealthDB(
            user_id=db_config['user_id'],
            db_user=db_config['db_user'],
            db_password=db_config['db_password'],
            db_name=db_config['db_name'],
            db_host=db_config['db_host']
        )
        self.current_metrics = {
            'SPEED': {'value': [], 'unit': 'mph'},
            'ENGINE_RPM': {'value': [], 'unit': 'RPM'},
            'COOLANT_TEMP': {'value': [], 'unit': '°C'}
        }
        self.active_dtcs = set()  # Changed from single DTC to set of DTCs
        self.last_cleared_dtcs = set()  # Changed from single DTC to set of DTCs
        self.heal_requested_codes = set()  # Track DTCs that have been requested to heal by user
        self.last_update_time = 0
        self.update_interval = 5.0  # Increased from 1.0 to 5.0 seconds
        self.max_retries = 3  # Maximum number of retries for database operations
        self.retry_delay = 1.0  # Delay between retries in seconds
        self._last_health_data = None  # Cache last health data
        self._last_health_data_time = 0  # Timestamp of last health data
        self._health_data_cache_duration = 30.0  # Cache health data for 30 seconds

    def _validate_metric_value(self, value, metric_name):
        """Validate metric values are within reasonable ranges"""
        validation_ranges = {
            'SPEED': (0, 200),  # mph
            'ENGINE_RPM': (0, 8000),  # RPM
            'COOLANT_TEMP': (-40, 150)  # °C
        }
        
        if metric_name in validation_ranges:
            min_val, max_val = validation_ranges[metric_name]
            return max(min_val, min(value, max_val))
        return value

    def _update_from_database(self):
        """Update metrics and DTCs from the database with retry logic"""
        current_time = time.time()
        
        # Check if we can use cached data
        if (self._last_health_data is not None and 
            current_time - self._last_health_data_time < self._health_data_cache_duration):
            health_data = self._last_health_data
        else:
            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    # Get the latest vehicle health data
                    health_data_list = self.db.get_vehicle_health()
                    if not health_data_list or len(health_data_list) == 0:
                        logger.warning("No health data returned from database")
                        return
                    
                    # Get the first (latest) record
                    health_data = health_data_list[0]
                    
                    # Cache the health data
                    self._last_health_data = health_data
                    self._last_health_data_time = current_time
                    break
                except Exception as e:
                    retry_count += 1
                    logger.error(f"Database query failed (attempt {retry_count}/{self.max_retries}): {e}")
                    if retry_count < self.max_retries:
                        time.sleep(self.retry_delay)
                    else:
                        logger.error("Max retries reached, using cached data if available")
                        if self._last_health_data is None:
                            return
                        health_data = self._last_health_data

        # Update metrics with the latest values from the arrays
        with self._lock:  # Thread-safe metric updates
            metrics_changed = False
            
            if 'speed' in health_data and health_data['speed']:
                speed_values = [self._validate_metric_value(v, 'SPEED') for v in health_data['speed']]
                if speed_values != self.current_metrics['SPEED']['value']:
                    self.current_metrics['SPEED']['value'] = speed_values
                    metrics_changed = True
                    
            if 'rpm' in health_data and health_data['rpm']:
                rpm_values = [self._validate_metric_value(v, 'ENGINE_RPM') for v in health_data['rpm']]
                if rpm_values != self.current_metrics['ENGINE_RPM']['value']:
                    self.current_metrics['ENGINE_RPM']['value'] = rpm_values
                    metrics_changed = True
                    
            if 'temp' in health_data and health_data['temp']:
                temp_values = [self._validate_metric_value(v, 'COOLANT_TEMP') for v in health_data['temp']]
                if temp_values != self.current_metrics['COOLANT_TEMP']['value']:
                    self.current_metrics['COOLANT_TEMP']['value'] = temp_values
                    metrics_changed = True

            # Update active DTCs - always keep these in sync with the database
            # But don't add back DTCs that have been cleared
            if 'dtcs' in health_data and health_data['dtcs']:
                new_dtcs = set(health_data['dtcs'])
                # Filter out any DTCs that have been cleared recently
                filtered_dtcs = new_dtcs - self.last_cleared_dtcs
                if filtered_dtcs != self.active_dtcs:
                    self.active_dtcs = filtered_dtcs
                    metrics_changed = True
            else:
                if self.active_dtcs:
                    self.active_dtcs = set()
                    metrics_changed = True

            # Only update last_update_time if metrics actually changed
            if metrics_changed:
                self.last_update_time = current_time
                logger.info("Metrics updated from database")
            else:
                logger.debug("No metric changes detected")

    def _encode_message_data(self, can_id):
        data = bytearray(8)
        try:
            with self._lock:  # Thread-safe metric access
                if can_id == 37:  # STEER_ANGLE_SENSOR message
                    temp_val = int(max(0, min(255, self.current_metrics['COOLANT_TEMP']['value'][-1])))
                    data[6] = temp_val
                    
                    # Use fixed values for steering angle for now
                    steer_angle = 90  # Center position
                    steer_rate = 0    # No movement
                    
                    data[0] = steer_angle & 0xFF
                    data[1] = 0
                    data[2] = (steer_rate + 128) & 0xFF
                    
                elif can_id == 180:  # SPEED message
                    speed_value = max(0, self.current_metrics['SPEED']['value'][-1])
                    speed_raw = int(speed_value * 100)
                    data[3] = speed_raw & 0xFF
                    data[4] = (speed_raw >> 8) & 0xFF
                    
                elif can_id == 380:  # ENGINE_RPM message
                    rpm_val = int(max(0, self.current_metrics['ENGINE_RPM']['value'][-1]))
                    data[0] = rpm_val & 0xFF
                    data[1] = (rpm_val >> 8) & 0xFF
                    
                elif can_id == 0x7E8 and self.active_dtcs:  # DIAGNOSTIC_RESPONSE_ID
                    # Get the first DTC from the set for this message
                    code = next(iter(self.active_dtcs)) if self.active_dtcs else None
                    if code:
                        prefix_map = {'P': 0x00, 'C': 0x40, 'B': 0x80, 'U': 0xC0}
                        prefix_val = 0x00
                        num_part_str = ""
                        
                        if code and len(code) > 1:
                            prefix = code[0].upper()
                            prefix_val = prefix_map.get(prefix, 0x00)
                            num_part_str = code[1:]
                        
                        byte2 = prefix_val
                        byte3 = 0x00
                        
                        if len(num_part_str) == 4:
                            try:
                                byte2 = prefix_val | int(num_part_str[0:2], 16)
                                byte3 = int(num_part_str[2:4], 16)
                            except ValueError:
                                pass
                        
                        data[0] = 0x02
                        data[1] = 0x01
                        data[2] = byte2
                        data[3] = byte3
                    
        except Exception as e:
            logger.error(f"Error encoding CAN message {hex(can_id)}: {e}")
            return bytearray(8)
        return data

    def _generate_message(self):
        possible_ids = [37, 180, 380]  # STEER_ANGLE_SENSOR, SPEED, ENGINE_RPM
        if self.active_dtcs and time.time() % 2 < 0.1:  # Send DTC every 2 seconds
            can_id = 0x7E8  # DIAGNOSTIC_RESPONSE_ID
        else:
            can_id = possible_ids[int(time.time() * 10) % len(possible_ids)]  # Rotate through IDs
            
        data_bytes = self._encode_message_data(can_id)
        return (can_id, bytes(data_bytes))

    def _run(self):
        while self._running:
            self._update_from_database()
            message = self._generate_message()
            try:
                self.message_queue.put(message, timeout=0.1)
            except queue.Full:
                pass
            time.sleep(0.01)  # 10ms delay between messages

    def start(self):
        if not self._running:
            logger.info("Starting database reader...")
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            logger.info("Database reader started")

    def stop(self):
        if self._running:
            logger.info("Stopping database reader...")
            self._running = False
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            logger.info("Database reader stopped")

    def clear_fault(self, dtc_code=None):
        """Clear a specific DTC or all DTCs if none specified"""
        with self._lock:  # Thread-safe DTC update
            if dtc_code:
                if dtc_code in self.active_dtcs:
                    self.last_cleared_dtcs.add(dtc_code)
                    self.heal_requested_codes.add(dtc_code)  # Add to heal requested codes
                    self.active_dtcs.remove(dtc_code)
                    logger.info(f"Cleared fault: {dtc_code}")
            else:
                # Clear all DTCs
                self.last_cleared_dtcs.update(self.active_dtcs)
                self.heal_requested_codes.update(self.active_dtcs)  # Add all active DTCs to heal requested codes
                self.active_dtcs.clear()
                logger.info("Cleared all faults") 