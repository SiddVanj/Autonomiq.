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
        self.db_user = db_user
        self.db_password = db_password
        self.db_name = db_name
        self.db_host = db_host
        self.connection = None
        self.cursor = None
        self.connect()

    def connect(self):
        """Establish database connection"""
        try:
            if self.connection is None or not self.connection.is_connected():
                self.connection = mysql.connector.connect(
                    host=self.db_host,
                    user=self.db_user,
                    password=self.db_password,
                    database=self.db_name
                )
                self.cursor = self.connection.cursor(dictionary=True)
                logging.info("Database connection established")
        except Exception as e:
            logging.error(f"Failed to connect to database: {str(e)}")
            raise

    def get_vehicle_health(self):
        """Get vehicle health data for a specific user"""
        try:
            if not self.connection or not self.connection.is_connected():
                self.connect()

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

        except Exception as e:
            logging.error(f"Failed to call stored procedure: {str(e)}")
            # Don't close the connection here, let the caller handle reconnection
            return None

    def close(self):
        """Close database connection"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.connection and self.connection.is_connected():
                self.connection.close()
            logging.info("Database connection closed.")
        except Exception as e:
            logging.error(f"Error closing database connection: {str(e)}")

    def __del__(self):
        """Destructor to ensure connection is closed"""
        self.close()


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