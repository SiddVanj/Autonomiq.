import mysql.connector
import json
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

class VehicleHealthDB:
    def __init__(self, user_id, db_user, db_password, db_name, db_host):
        self.user_id = user_id
        try:
            logging.info("Connecting to the database...")
            self.conn = mysql.connector.connect(
                user=db_user,
                password=db_password,
                host=db_host,
                database=db_name
            )
            logging.info("Connected to the database.")
            self.cursor = self.conn.cursor(dictionary=True)
        except mysql.connector.Error as err:
            logging.error(f"Database connection failed: {err}")
            raise Exception(f"Database connection failed: {err}")

    def get_vehicle_health(self):
        try:
            logging.info(f"Calling stored procedure with user_id={self.user_id}...")
            self.cursor.callproc('get_vehicle_health_by_user_id', [self.user_id])

            # Collect result from the stored procedure
            records = []
            for result in self.cursor.stored_results():
                fetched = result.fetchall()
                logging.info(f"Retrieved {len(fetched)} rows.")
                records.extend(fetched)

            if not records:
                logging.warning("No vehicle health data found.")
                return None

            # Decode JSON fields
            for record in records:
                for field in ['speed', 'rpm', 'temp', 'dtcs']:
                    if record[field] and isinstance(record[field], str):
                        try:
                            record[field] = json.loads(record[field])
                        except json.JSONDecodeError as e:
                            logging.warning(f"Failed to decode JSON in field '{field}': {e}")

            return records

        except mysql.connector.Error as err:
            logging.error(f"Failed to call stored procedure: {err}")
            return None
        finally:
            self.cursor.close()
            self.conn.close()
            logging.info("Database connection closed.")


if __name__ == "__main__":
        # Replace with your Cloud SQL credentials
        db = VehicleHealthDB(
            user_id=1,
            db_user='root',
            db_password='@ut0n0m1(',
            db_name='vehicle_data',
            db_host='104.198.19.122' #'128.237.82.142'
        )

        result = db.get_vehicle_health()
        if result:
            logging.info("Vehicle health data retrieved successfully")
            # Return the result directly as a Python dict instead of printing as JSON
            vehicle_data = result

            print(f"First record: {vehicle_data[0]}")
        else:
            logging.info("No data returned.")
            vehicle_data = None