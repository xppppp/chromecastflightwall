import sys
from unittest.mock import MagicMock

# Mock out external libraries before importing the module under test
mock_gtts = MagicMock()
mock_gtts.gTTS = MagicMock()
sys.modules['gtts'] = mock_gtts

mock_pychromecast = MagicMock()
sys.modules['pychromecast'] = mock_pychromecast

mock_pil = MagicMock()
mock_pil.Image = MagicMock()
mock_pil.ImageDraw = MagicMock()
mock_pil.ImageFont = MagicMock()
sys.modules['PIL'] = mock_pil

mock_staticmap = MagicMock()
mock_staticmap.StaticMap = MagicMock()
mock_staticmap.Line = MagicMock()
mock_staticmap.CircleMarker = MagicMock()
sys.modules['staticmap'] = mock_staticmap

mock_fr24 = MagicMock()
mock_fr24.FlightRadar24API = MagicMock()
sys.modules['FlightRadar24'] = mock_fr24


import unittest
import threading
import socketserver
import http.server
import urllib.request
import urllib.error
import json
import time
from datetime import datetime

# Import the module under test
import myflightwall

class TestMyFlightWall(unittest.TestCase):

    def setUp(self):
        # Reset globals to clean values before each test
        self.original_home_lat = myflightwall.HOME_LAT
        self.original_home_lon = myflightwall.HOME_LON
        self.original_radar_radius = myflightwall.RADAR_RADIUS_METERS
        self.original_active_hours = myflightwall.ACTIVE_HOURS
        self.original_detected_flights_history = list(myflightwall.detected_flights_history)
        myflightwall.detected_flights_history.clear()

        # Start a local HTTP server on an ephemeral port (port 0) for testing
        class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
            allow_reuse_address = True

        self.server = ThreadedTCPServer(("127.0.0.1", 0), myflightwall.FlightWallRequestHandler)
        self.ip, self.port = self.server.server_address
        
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()

    def tearDown(self):
        # Shut down server and join thread
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join()

        # Restore original globals
        myflightwall.HOME_LAT = self.original_home_lat
        myflightwall.HOME_LON = self.original_home_lon
        myflightwall.RADAR_RADIUS_METERS = self.original_radar_radius
        myflightwall.ACTIVE_HOURS = self.original_active_hours
        myflightwall.detected_flights_history = self.original_detected_flights_history

    def test_parse_active_hours(self):
        # Valid format
        self.assertEqual(myflightwall.parse_active_hours("08:30,22:45"), ("08:30", "22:45"))
        self.assertEqual(myflightwall.parse_active_hours(" 09:00 , 21:00 "), ("09:00", "21:00"))
        
        # Invalid format (not enough or too many parts)
        with self.assertRaises(ValueError):
            myflightwall.parse_active_hours("09:00")
        with self.assertRaises(ValueError):
            myflightwall.parse_active_hours("09:00,21:00,23:00")
            
        # Invalid HH:MM structure
        with self.assertRaises(ValueError):
            myflightwall.parse_active_hours("25:00,12:00")
        with self.assertRaises(ValueError):
            myflightwall.parse_active_hours("09:60,12:00")

    def test_configure_valid_params(self):
        url = f"http://{self.ip}:{self.port}/configure?RADAR_RADIUS_METERS=6500&HOME_LAT=45.1234&ACTIVE_HOURS=11:00,23:00"
        
        req = urllib.request.urlopen(url)
        self.assertEqual(req.getcode(), 200)
        
        data = json.loads(req.read().decode('utf-8'))
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["updated_parameters"]["RADAR_RADIUS_METERS"], 6500)
        self.assertEqual(data["updated_parameters"]["HOME_LAT"], 45.1234)
        self.assertEqual(data["updated_parameters"]["ACTIVE_HOURS"], ["11:00", "23:00"])

        # Check that globals were actually updated in the module
        self.assertEqual(myflightwall.RADAR_RADIUS_METERS, 6500)
        self.assertEqual(myflightwall.HOME_LAT, 45.1234)
        self.assertEqual(myflightwall.ACTIVE_HOURS, ("11:00", "23:00"))

    def test_configure_invalid_params(self):
        # Test unknown configuration parameter
        url_unknown = f"http://{self.ip}:{self.port}/configure?NON_EXISTENT_PARAM=value"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url_unknown)
        self.assertEqual(ctx.exception.code, 400)
        data = json.loads(ctx.exception.read().decode('utf-8'))
        self.assertEqual(data["status"], "error")
        self.assertIn("Unknown configuration parameter", data["message"])

        # Test invalid float for HOME_LAT
        url_invalid_float = f"http://{self.ip}:{self.port}/configure?HOME_LAT=not_a_float"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url_invalid_float)
        self.assertEqual(ctx.exception.code, 400)
        data = json.loads(ctx.exception.read().decode('utf-8'))
        self.assertEqual(data["status"], "error")
        self.assertIn("Invalid value for HOME_LAT", data["message"])

        # Test invalid active hours format
        url_invalid_hours = f"http://{self.ip}:{self.port}/configure?ACTIVE_HOURS=12:00"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url_invalid_hours)
        self.assertEqual(ctx.exception.code, 400)
        data = json.loads(ctx.exception.read().decode('utf-8'))
        self.assertEqual(data["status"], "error")
        self.assertIn("ACTIVE_HOURS must be in the format", data["message"])

    def test_detected_endpoint(self):
        # Insert mock detected flights
        now = time.time()
        
        # Flight 1: 5 minutes ago
        flight_recent = {
            "id": "abc1",
            "callsign": "TEST1",
            "altitude": 10000,
            "ground_speed": 250,
            "vertical_speed": 0,
            "aircraft_type": "B738",
            "latitude": 38.0,
            "longitude": -77.0,
            "origin_iata": "IAD",
            "origin_name": "Dulles",
            "destination_iata": "JFK",
            "destination_name": "John F Kennedy",
            "timestamp": datetime.now().isoformat(),
            "timestamp_epoch": now - 5 * 60
        }
        # Flight 2: 2 hours (120 minutes) ago
        flight_older = {
            "id": "abc2",
            "callsign": "TEST2",
            "altitude": 15000,
            "ground_speed": 300,
            "vertical_speed": 0,
            "aircraft_type": "A320",
            "latitude": 39.0,
            "longitude": -76.0,
            "origin_iata": "LAX",
            "origin_name": "Los Angeles",
            "destination_iata": "SFO",
            "destination_name": "San Francisco",
            "timestamp": datetime.now().isoformat(),
            "timestamp_epoch": now - 120 * 60
        }
        # Flight 3: 10 days ago (should be pruned, as it is > 7 days)
        flight_pruned = {
            "id": "abc3",
            "callsign": "TEST3",
            "altitude": 20000,
            "ground_speed": 400,
            "vertical_speed": 0,
            "aircraft_type": "B772",
            "latitude": 40.0,
            "longitude": -75.0,
            "origin_iata": "LHR",
            "origin_name": "Heathrow",
            "destination_iata": "CDG",
            "destination_name": "Charles de Gaulle",
            "timestamp": datetime.now().isoformat(),
            "timestamp_epoch": now - 10 * 24 * 60 * 60
        }

        myflightwall.detected_flights_history.extend([flight_recent, flight_older, flight_pruned])

        # Request detected flights in the last 10 minutes
        url_10m = f"http://{self.ip}:{self.port}/detected?MINUTES=10"
        req = urllib.request.urlopen(url_10m)
        self.assertEqual(req.getcode(), 200)
        data = json.loads(req.read().decode('utf-8'))
        
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["minutes_requested"], 10)
        self.assertEqual(data["flights_count"], 1)
        self.assertEqual(data["flights"][0]["id"], "abc1")

        # Request detected flights in the last 150 minutes
        url_150m = f"http://{self.ip}:{self.port}/detected?MINUTES=150"
        req = urllib.request.urlopen(url_150m)
        self.assertEqual(req.getcode(), 200)
        data = json.loads(req.read().decode('utf-8'))
        
        self.assertEqual(data["flights_count"], 2)
        # Verify both flights abc1 and abc2 are returned
        flight_ids = {f["id"] for f in data["flights"]}
        self.assertEqual(flight_ids, {"abc1", "abc2"})

        # Verify that flight_pruned (10 days ago) was pruned completely from the history list
        self.assertNotIn("abc3", [f["id"] for f in myflightwall.detected_flights_history])

    def test_detected_invalid_params(self):
        # Missing MINUTES
        url_missing = f"http://{self.ip}:{self.port}/detected"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url_missing)
        self.assertEqual(ctx.exception.code, 400)
        data = json.loads(ctx.exception.read().decode('utf-8'))
        self.assertEqual(data["status"], "error")
        self.assertIn("Missing MINUTES parameter", data["message"])

        # Invalid MINUTES (non-integer)
        url_invalid = f"http://{self.ip}:{self.port}/detected?MINUTES=abc"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url_invalid)
        self.assertEqual(ctx.exception.code, 400)
        data = json.loads(ctx.exception.read().decode('utf-8'))
        self.assertEqual(data["status"], "error")
        self.assertIn("Invalid MINUTES parameter", data["message"])

        # Invalid MINUTES (negative)
        url_negative = f"http://{self.ip}:{self.port}/detected?MINUTES=-5"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url_negative)
        self.assertEqual(ctx.exception.code, 400)
        data = json.loads(ctx.exception.read().decode('utf-8'))
        self.assertEqual(data["status"], "error")
        self.assertIn("MINUTES must be a positive integer", data["message"])


if __name__ == "__main__":
    unittest.main()
