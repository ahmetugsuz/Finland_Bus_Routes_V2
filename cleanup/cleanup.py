import psycopg2
import sys
import logging
from psycopg2 import OperationalError
import time
import datetime

cleanup_in_progress = False

# Create a logger instance
logger = logging.getLogger('cleanup_logger')

# Configure the logging settings
logger.setLevel(logging.INFO)  # Set the log level (INFO, WARNING, ERROR, etc.)

# Create a file handler to write logs to a file
file_handler = logging.FileHandler('cleanup.log')
file_handler.setLevel(logging.INFO)

# Create a console handler to print logs to the terminal
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)

# Define the log message format
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Add the handlers to the logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Configure the logging settings
logging.basicConfig(
    filename='cleanup.log',  # Specify the log file name
    level=logging.INFO,     # Set the log level (INFO, WARNING, ERROR, etc.)
    format='%(asctime)s - %(levelname)s - %(message)s'  # Define the log message format
)

timestamp = datetime.datetime.now().strftime("%H:%M:%S")
minutes = timestamp.split(":")[1]

# Function to establish a database connection with retries
def connect_to_database():
    retries = 5
    delay = 5  # Adjust this delay as needed
    while retries > 0:
        try:
            connection = psycopg2.connect(
                host="db",
                port=5432,
                database="bus_data",
                user="ahmettugsuz",
                password="bus_finland",
            )
            return connection
        except OperationalError as e:
            print(f"Connection to the database failed: {e}")
            print(f"Retrying in {delay} seconds...")
            time.sleep(delay)
            retries -= 1

    raise Exception("Failed to connect to the database after retries")

def cleanup_database(max_retries = 3):
    connection = connect_to_database()
    cursor = connection.cursor()
    try:
        # Set the flag to True when cleanup starts
        global cleanup_in_progress
        cleanup_in_progress = True
        connection.autocommit = False

        # Delete records from bus_status referencing stop by stop_id
        cursor.execute("DELETE FROM bus_status WHERE stop_id IN (SELECT id FROM stop)")

        # Delete records from bus_status referencing bus by vehicle_number
        cursor.execute("DELETE FROM bus_status WHERE vehicle_number IN (SELECT vehicle_number FROM bus)")

        # Delete records from stop referencing stop_event by stop_event
        cursor.execute("DELETE FROM stop WHERE stop_event IN (SELECT id FROM stop_event)")

        # Delete records from bus_status
        cursor.execute("DELETE FROM bus_status")

        # Delete records from bus
        cursor.execute("DELETE FROM bus")

        # Delete records from stop
        cursor.execute("DELETE FROM stop")

        # Delete records from stop_event
        cursor.execute("DELETE FROM stop_event")

        # Log a message indicating successful cleanup
        logger.info('Cleanup completed successfully.')
        print(' ')
        connection.commit()
    except Exception as e:
        connection.rollback()
        if max_retries > 0:
            logger.info("Error during cleanup")
            logger.info("Retrying cleanup prosess..")
            cleanup_database(max_retries - 1)
        else:
            # Handle exceptions and log after the error
            logger.error(f"Error during cleanup: {str(e)}")
            time.sleep(2)
    finally:
        # Close the db connection
        cleanup_in_progress = False
        cursor.close()
        connection.autocommit = True
        connection.close()



if __name__ == "__main__":
    cleanup_in_progress = False  # Initialize the variable
    timesheduler = 360
    time.sleep(4)
    logger.info("Configuring database cleanup schedule...")
    time.sleep(1)
    logger.warning("Database memory cleanup scheduled every 6 minutes: [{}]".format(timestamp))
    #logger.info("Deleting data from db to free memory")
    logger.info("Timeschedule for cleanup can be configured in the code")
    
    while True:
        time.sleep(timesheduler)
        print(' ')
        logger.warning('Starting the prosess of cleaning up the database ...')
        cleanup_database()

