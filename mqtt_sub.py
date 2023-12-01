# This class do subscribe to consume telemetry from MQTT Server
import paho.mqtt.client as paho
import psycopg2
import time
import sys
import json
import datetime
import json
from geopy.geocoders import Nominatim # libary to get the location with lat and long 
from geopy.exc import GeocoderUnavailable
from graphqlclient import GraphQLClient # libary to seek the stop ID of a position
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from gql.transport.requests import RequestsHTTPTransport
from requests_toolbelt.multipart.encoder import MultipartEncoder
from gql import gql
from gql import Client as TransportClient
from graphql import parse
from requests import Session
from cleanup.cleanup import cleanup_in_progress
import time


class MQTTSubscriber:    
    def __init__(self, broker_address, topic, conn_pool, conn_key):
        self.broker_address = broker_address
        self.topic = topic  
        self.client = paho.Client()
        self.client.on_connect = self.on_connect # A callback function
        self.client.on_message = self.on_message # A callback function
        self.client.connect(broker_address, 1883, 60) # making a connection every 60 second (to keep it alive)
        self.last_data_gps = []
        self.last_data_odo = []
        self.teller = 0

        # Dict of valuable status, avoiding status like when door opens or closing etc. We dont need that additional information or data for our case. 
        # The "vp" is set to be "Driving", as it's not like the other event types who gives us more additional information about the vehicle status, it's more likely that when "vp" message comes in, the data is representing a driving vehicle  
        self.vp_status = {"vp": "Driving", "due": "Arriving to a stop", "arr": "Arrives inside of a stop radius",  "dep": "Departing from stop", "ars": "Arrived to a stop",
        "pde": "Ready to depart from a stop", "wait": "Waiting at a stop", "dl": "Time offset from schedule", "start": "Start time"}

        # Dict to get the operator name based on their ID  
        self.oper_dict = {6: "Oy Pohjolan Liikenne Ab", 12: "Helsingin Bussiliikenne Oy", 17: "Tammelundin Liikenne Oy", 18: "Oy Pohjolan Liikenne Ab", 20: "Bus Travel Åbergin Linja Oy",
        21: "Bus Travel Oy Reissu Ruoti", 22: "Nobina Finland Oy", 30: "Savonlinja Oy", 36: "Nurmijärven Linja Oy", 40: "HKL-Raitioliikenne", 47: "Taksikuljetus Oy", 50: "HKL-Metroliikenne",
        51: "Korsisaari Oy", 54: "V-S Bussipalvelut Oy", 58: "Koillisen Liikennepalvelut Oy", 60: "Suomenlinnan Liikenne Oy", 59: "Tilausliikenne Nikkanen Oy", 89: "Metropolia", 90: "VR Oy",
        130: "Matkahuolto", 195: "Siuntio"}

        self.geolocator = Nominatim(user_agent="my_app") # creating a geolocater variable on my app

        requests.packages.urllib3.disable_warnings(InsecureRequestWarning) # Disabling the ssl verification, DON'T Recommend this on dev, but to not use time on this in first place as it's not the main case, i'm avoiding it.

        # Initialize the GraphQL client and set the endpoint URL
        subscription_key = 'baf79f82592c44aabce4feff0ef46c25'
        url = 'https://dev-api.digitransit.fi/routing/v1/routers/hsl/index/graphql'
        #self.graph_client = TransportClient(transport=transport, fetch_schema_from_transport=True)
        # Set up headers with the subscription key
        transport = RequestsHTTPTransport(
            url=url,
            verify=False,  # Disabling SSL verification for dev (not recommended for production)
            headers={
                'digitransit-subscription-key': subscription_key,
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache'
            }
        )
        self.graph_client = TransportClient(transport=transport, fetch_schema_from_transport=True)

        # Connecting to db
        self.conn_pool = conn_pool
        self.conn = conn_pool.getconn(key=conn_key) # getting the connection from the connection pool
        self.cur = self.conn.cursor() # setting up the cursor
        self.conn_key = conn_key # Connection key

        self.conn.commit()
        self.cur.execute("CREATE EXTENSION IF NOT EXISTS cube")
        self.cur.execute("CREATE EXTENSION IF NOT EXISTS earthdistance")
        # commit the changes to the database
        self.conn.commit()
        self.cur.execute("CREATE TABLE IF NOT EXISTS stop_event (id SERIAL PRIMARY KEY, status TEXT, arrival_time_to_the_stop TEXT)")
        self.cur.execute("CREATE TABLE IF NOT EXISTS stop (id SERIAL PRIMARY KEY, tsi INTEGER, stop_event INTEGER REFERENCES stop_event(id) ON DELETE CASCADE, stop_name TEXT, stop_adress TEXT, latitude FLOAT, longitude FLOAT)")
        self.cur.execute("CREATE TABLE IF NOT EXISTS bus (vehicle_number INTEGER PRIMARY KEY, operator TEXT)")  # vehicle number is the unique key
        self.cur.execute("CREATE TABLE IF NOT EXISTS bus_status (id SERIAL PRIMARY KEY, vehicle_number INTEGER NOT NULL REFERENCES bus(vehicle_number) ON DELETE CASCADE, tsi INTEGER NOT NULL, utc_timestamp TIME, route_number TEXT, current_location TEXT," +
                        "latitude FLOAT, longitude FLOAT, stop_id INTEGER REFERENCES stop(id) ON DELETE CASCADE, destination TEXT)")

        self.conn.commit()

    def get_next_stop_data(self, next_stop_name):
        # Query to get the stop location from GraphQL
        graph_query_stop = """
                query GetStop($id: String!) {
                stop(id: $id) {
                    name
                    lat
                    lon
                }
            }
        """

        # Parse the query and check for any errors
        try:
            document = gql(graph_query_stop)
            #print("Query is valid.")
        except Exception as e: # If the query is invalid 
            print(f"Query is invalid: {e}")

        # Define the variables for the GraphQL query
        variables = {
            "id": "HSL:"+next_stop_name
        }


        # Execute the GraphQL query with the document and variables
        result_next_stop = self.graph_client.execute(document, variables)

        if result_next_stop is not None and 'stop' in result_next_stop:
            stop_data = result_next_stop['stop']
            return stop_data  # Return the stop data
        else:
            print("Failed to retrieve stop data.")
            return None
    
    def get_next_stop_adress(self, stop_name: str, next_stop_adress: list):
        # stop name could be in next_stop_adress, so if u want to do something with it, do it here!
        return f"{next_stop_adress[0]}, {next_stop_adress[1]}, {next_stop_adress[2]}"
        
    
    def reverse_geocode_with_retry(self, lat, long, max_retries=3, retry_delay=5):
        retries = 0
        while retries < max_retries:
            try:
                time.sleep(0.5)
                location = self.geolocator.reverse(f"{lat}, {long}")
                retries += 1
                return location
            except GeocoderUnavailable as e:
                print(f"Geocoding request failed. Retrying in {retry_delay} seconds.")
                time.sleep(retry_delay)
                retries += 1
        return None  # If all retries fail, return None or handle the error accordingly
    
    def is_duplicate_location(self, location, liste):
        if liste and len(liste) > 0:
            last_location = liste.pop()
            # Compare the locations by address, or use a more flexible comparison method
            return location.address == last_location.address
        return False

    

    def on_connect(self, client, userdata, flags, rc):
        #print("LOG: Connected with result code: ", str(rc))
        client.subscribe(self.topic) # connecting to a specific topic

    def on_message(self, client, userdata, msg):
        """
        Parameters: 
        - client: Instance of the MQTT client that received the message.
        - userdata: any user-defined data that was passed to the 'client' instance when it was created.  
        - msg: telemetry data sends us the message that is divided to two parts: 
            1. msg.topic -> in the format of: "/hfp/v2/journey/ongoing/vp/bus/#" (just an example, can be modified)
            2. msg.payload -> in the format of: "{"VP": {"desi": str, "dir": int,...}}"
        This method:
        Is called by MQTT client libary when a new message arrives on subscribed topic, 
        server: mqtt.hsl.fi in our case,
        topic subscribed on: "/hfp/v2/journey/ongoing/vp/bus/#"

        From message: msg
        - We can extract 'msg.topic' in format of: "/hfp/v2/journey/ongoing/vp/bus/...", it can be splitted to parts by "/". -> we do put this on a dict, to make it more readable
        - We can also extract the 'msg.payload' as in format of:  "{"VP": {"desi": str, "dir": int,...}} -> dict"

        """

        #print(msg.topic+" "+str(msg.payload))

        msg_topic = str(msg.topic)
        topic_parts = msg_topic[1:].split("/")

        status = self.vp_status.get(topic_parts[4], "") # getting the status from the global status dict created for some status cases, to make it more clear. If dict does not contain element it is set to empty value as default.
        start_time = topic_parts[11] # not neccassarry, but cool to have, maybe useful when making an app or website. 
        topic_dict = {"status": status, "route_id": topic_parts[6], "vehicle_number": topic_parts[7], "destination": topic_parts[10], "next_stop": topic_parts[12]}
        vehicle_number = topic_dict["vehicle_number"]
        next_stop = topic_dict["next_stop"]
        
        # If trip has end, and consider our case, it's not neccassary hold up old bus values/data/information on the bus_status table
        if next_stop == "EOL" or next_stop == "": 
            try:
                self.cur.execute("DELETE FROM bus_status WHERE bus_status.vehicle_number IN (SELECT b.vehicle_number FROM bus AS b WHERE b.vehicle_number=%s)", (vehicle_number,))
                self.conn.commit() # commiting the transaction
                return # return, because we dont want to do more operations than deleting it 
            except:
                self.conn.rollback() # If any query fails Undo changes that have been made
            finally:
                self.conn_pool.putconn(conn=self.conn, key=self.conn_key)

        if topic_parts[4] in self.vp_status: # we dont need additional information about the door closing or other activities like bus on server etc. So we dont add them to our database  
            temp_payload_dict = json.loads(msg.payload) # since it is a json format, we turn it into a python dictionary
            payload_dict = temp_payload_dict[topic_parts[4].upper()] # we can get rid of first dict, with just passing status as now, we only need to consider the one dictionary who gives us information needed: {"VP": {this one}}. 

            # Initializing variable to be the utc timestamp
            utc_timestamp = payload_dict["tst"] 
            utc_datetime_obj = datetime.datetime.strptime(utc_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").time().strftime("%H:%M:%S") # removing the millieseconds

            time_string_next_stop = None
            if topic_parts[4] in self.vp_status and topic_parts[4] != "vp":
                ttarr = datetime.datetime.strptime(payload_dict["ttarr"], '%Y-%m-%dT%H:%M:%S.%fZ')
                time_to_next_stop = ttarr.time()

                # Convert the time to a string in the format HH:MM:SS
                time_string_next_stop = time_to_next_stop.strftime("%H:%M:%S")
            
            # Getting the location of the bus
            # defining a empty value address. 
            current_address = "Undefined" # Undefined till we can access some values that gives us the location, oterwhise it will stay as 'Undifened'
            lat = payload_dict["lat"]
            long = payload_dict["long"]

            # Getting the 'next stop' data with executing the document based on  query and the ID for the stop name we want to get
            #result_next_stop = self.graph_client.execute(document, {'id': "HSL:"+next_stop })

            result_next_stop = self.get_next_stop_data(next_stop_name=next_stop)
            
            stop_name = ""
            stop_lat = 0.0  # Initialize with default latitude
            stop_long = 0.0  # Initialize with default longitude
            # Extracting the values from the dictionary returned by execute() -> result_next_stop
            if result_next_stop is not None:
                stop_name = result_next_stop['name']  # result_next_stop['name'] 
                stop_lat = result_next_stop['lat'] 
                stop_long = result_next_stop['lon'] 
                # print(f"Stop Name: {stop_name}, Latitude: {stop_lat}, Longitude: {stop_long}")
            else:
                print("Stop data is not available.")
            
            # Getting the exact location/adress for the next stop  
            next_stop_adress = self.reverse_geocode_with_retry(stop_lat, stop_long) 
            final_next_stop = None
            if next_stop_adress:
                next_stop_adress = next_stop_adress.address
                next_stop_adress = next_stop_adress.split(", ")
                final_next_stop = self.get_next_stop_adress(result_next_stop['name'], next_stop_adress)
            if not next_stop_adress: # if it returned none value or not the adress, set it to be empty
                next_stop_adress = ""

            if final_next_stop == None:
                print("WARNING: Couldnt find next stop")
                final_next_stop = ""

            if (payload_dict["loc"] == "ODO"): 
                if lat is not None and long is not None:
                    location = self.reverse_geocode_with_retry(lat, long)
                    if location:
                        if self.is_duplicate_location(location, self.last_data_odo): # if same data appears two times in a row, we select to not write the duplicate one, as it can be multiple times same signal is received, or similiar with signal with un-important information for our case
                            #print("Duplicate!")
                            return
                        self.last_data_odo.append(location)

                        address = location.address
                        address_parts = address.split(", ")
                        if len(address_parts) > 1:
                            street = f"{address_parts[0]}, {address_parts[1]}, {address_parts[2]}"
                            city = ""
                        if len(address_parts) > 5:
                            city = address_parts[-5]
                        current_address = f"{street}, {city}" # concatenating the street and city name into one variable, as current address
                else:
                    # Finding the last registered location since we didnt have any lat or long values.
                    self.cur.execute("SELECT status.current_location FROM bus_status AS status INNER JOIN bus AS b ON b.vehicle_number = status.vehicle_number WHERE b.vehicle_number = %s AND status.current_location != 'Undefined' ORDER BY status.tsi DESC LIMIT 1", (payload_dict["veh"],))
                    self.conn.commit()
                    
                    result = self.cur.fetchone() # fetching the result from the query

                    if result is None: # we didn't find any last stop location
                        #print("No results found")
                        current_address = stop_name # we are still setting the location to be the stop location were at
                        print("result is none")
                    else:
                        current_location = result[0].split(" ") # splitting the last current location 
                        if len(current_location) >= 3:
                            current_address = f"{stop_name}, {current_location[-1]}" # concatenating the address, the last value should be the city name from last known location
                        else:
                            current_address = stop_name
                    
            elif (payload_dict["loc"] == "GPS") or (payload_dict["loc"] == "MAN"):
                """
                GPS values are given (manually or automatically)
                Location can be calculated by:
                    - lat
                    - long
                If the location is given through GPS or manually, location can be calculated using lat and long values.
                Uses python library geolocator to retrieve the address.
                Returns the current address if street and city name are found, otherwise returns Undefined.
                """

                self.teller += 1
                #print("Teller: ", self.teller)
                location = self.reverse_geocode_with_retry(lat, long)
                if location:
                    if self.is_duplicate_location(location, self.last_data_gps): # if same data appears two times in a row, we select to not write the duplicate one, as it can be multiple times same signal is received, or similiar with signal with un-important information for our case
                        return
                    if len(self.last_data_gps) > 0:
                        self.last_data_gps.pop()
                    if self.last_data_gps: # if it is still not empty
                        raise Exception("The list is not empty as expected")
                    self.last_data_gps.append(location)
                    address = location.address
                    if (payload_dict["loc"] == "GPS"):
                        #print("kommer fra GPS")
                        ...
                    elif (payload_dict["loc"] == "MAN"):
                        #print("Kommer fra MAN")
                        ...
                    address_parts = address.split(", ") # splitting the address into parts to get the most valuable information
                    if len(address_parts) > 1:
                        street = f"{address_parts[0]}, {address_parts[1]}, {address_parts[2]}"
                        city = ""
                        if len(address_parts) > 5:
                            city = address_parts[-5]
                        current_address = f"{street}, {city}" # concatenating the street and city name into one variable, as current address
                    else:
                        current_address = "Undefined"
                else: #Geocoding request failed after multiple retries.
                    current_address = "Undefined"

            elif lat == None or long == None or lat == "null" or long == "null" or payload_dict["loc"] == "N/A": # this could be just else, but to make it more readable i'm not removing it
                current_address = "Undefined" # handle None values / unidentified location values, 
            else: 
                current_address = "Undefined"

            if topic_parts[4] == "dep": # we don't need to show what next stop here is, as it already departing from the stop, and showing current location, while next stop is not defined yet
                stop_name = "" # can be confusing to show next stop, so we set it as empty, while status of it telling what the situation is, it gives enough information to the user


            self.cur.execute("INSERT INTO bus (vehicle_number, operator) VALUES (%s, %s) ON CONFLICT (vehicle_number) DO UPDATE SET operator = EXCLUDED.operator WHERE bus.vehicle_number = EXCLUDED.vehicle_number", (vehicle_number, self.oper_dict[payload_dict["oper"]]))
            self.conn.commit() # committing it, after inserts, while there are relation betweens the tables, it needs to be updated 

            if payload_dict["stop"] is not None:
                self.cur.execute("INSERT INTO stop_event (id, status, arrival_time_to_the_stop) VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, arrival_time_to_the_stop = EXCLUDED.arrival_time_to_the_stop", (payload_dict["stop"], status, time_string_next_stop))
                self.conn.commit()

            self.cur.execute("INSERT INTO stop (tsi, stop_event, stop_name, stop_adress, latitude, longitude) VALUES (%s, %s, %s, %s, %s, %s)", (payload_dict["tsi"], payload_dict["stop"], stop_name, final_next_stop, stop_lat, stop_long))
            self.conn.commit()


            # Check if the bus record exists in the bus table
            self.cur.execute("SELECT 1 FROM bus WHERE vehicle_number = %s", (vehicle_number,))
            bus_exists = self.cur.fetchone()

            self.cur.execute("SELECT 1 FROM stop WHERE id = (SELECT stop.id FROM stop ORDER BY stop.id DESC LIMIT 1)")
            stop_id_exists = self.cur.fetchone()
            if bus_exists and stop_id_exists:
                # Both the bus record and stop.id exist, so you can proceed with the insertion
                self.cur.execute("INSERT INTO bus_status (vehicle_number, tsi, utc_timestamp, route_number, current_location, latitude, longitude, stop_id, destination) SELECT %s, %s, %s, %s, %s, %s, %s, (SELECT stop.id FROM stop ORDER BY stop.id DESC LIMIT 1), %s",
                                (vehicle_number, payload_dict["tsi"], utc_datetime_obj, payload_dict["desi"], current_address, lat, long, topic_dict["destination"]))
                self.conn.commit()
            else:
                # Either the bus record or stop.id (or both) do not exist, handle the error
                if not bus_exists:
                    print(f"Bus record with vehicle_number {vehicle_number} does not exist in the bus table. Skipping insertion into bus_status.")
                if not stop_id_exists:
                    print("stop.id does not exist in the stop table. Skipping insertion into bus_status.")

            self.conn.commit()




    def start(self):
        if self.client.connect(self.broker_address) != 0:
            print("Could not connect to MTTQ broker")
            sys.exit(-1)

        print("Press CTRL+C to exit...")
        self.client.loop_start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt: # User is stopping the program by: "ctrl+c" on keyboard
            self.client.loop_stop() 
            self.cur.close() # closing the cursor to prevent error 
            self.conn.close() # Closing the connection
            self.conn_pool.putconn(self.conn, self.conn_key) # Returning connection to the pool, so it can be re-used by other parts of the program 
            print("Program stopped by user")
            sys.exit(0)



