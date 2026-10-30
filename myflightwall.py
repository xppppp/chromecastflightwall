import os
import time
import requests
from gtts import gTTS
import pychromecast
from PIL import Image, ImageDraw, ImageFont 
from staticmap import StaticMap, Line, CircleMarker
import threading
import http.server
import socketserver
try:
    from FlightRadarAPI import FlightRadar24API
except ImportError:
    from FlightRadar24 import FlightRadar24API
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import json

# --- CONFIGURATION ---
#
# Your home latitude/longitude.
#
HOME_LAT = 38.8977
HOME_LON = -77.0365

# The detection radius around your home coordinates (in meters)
RADAR_RADIUS_METERS = 5000 

# Time window (24-hour format) during which alerts should be sent to the display
ACTIVE_HOURS = ("09:00", "21:00") 

# How long (in seconds) the flight radar image stays on your Google Nest Hub screen
CAST_DURATION_SECONDS = 30

# If you want to send Android push notifications
PUSHOVER_USER_KEY = "your_user_key"

# How the chromecast will contact this script
LOCAL_IP = "192.168.1.43" # Change to this machine's IP
PORT = 65530

# The name of the Chromecast
HUB_NAME = "Chromecast"
# --- END CONFIGURATION ---

known_flights = set()
fr_api = FlightRadar24API()

# --- CONFIGURATION PARSERS, LOCKS, & HISTORY ---
def parse_active_hours(val):
    """Parses a string formatted as 'HH:MM,HH:MM' or a 2-element list/tuple into a tuple of two strings."""
    if isinstance(val, (list, tuple)) and len(val) == 2:
        datetime.strptime(str(val[0]).strip(), "%H:%M")
        datetime.strptime(str(val[1]).strip(), "%H:%M")
        return (str(val[0]).strip(), str(val[1]).strip())
    if isinstance(val, str):
        parts = val.split(',')
        if len(parts) == 2:
            # validate HH:MM formats
            datetime.strptime(parts[0].strip(), "%H:%M")
            datetime.strptime(parts[1].strip(), "%H:%M")
            return (parts[0].strip(), parts[1].strip())
    raise ValueError("ACTIVE_HOURS must be in the format 'HH:MM,HH:MM'")

CONFIG_PARSERS = {
    "HOME_LAT": float,
    "HOME_LON": float,
    "RADAR_RADIUS_METERS": int,
    "ACTIVE_HOURS": parse_active_hours,
    "CAST_DURATION_SECONDS": int,
    "PUSHOVER_USER_KEY": str,
    "LOCAL_IP": str,
    "PORT": int,
    "HUB_NAME": str,
}

data_lock = threading.Lock()
detected_flights_history = []

CONFIG_ENV_VARS = ["FLIGHTWALL_CONFIG", "FLIGHTWALL_CONFIG_FILE", "CONFIG_FILE"]

def get_config_file_path():
    """Returns the config file path specified by environment variables, or None if not set."""
    for var in CONFIG_ENV_VARS:
        path = os.environ.get(var)
        if path and path.strip():
            return os.path.expanduser(os.path.expandvars(path.strip()))
    return None

def load_config_file(config_path=None):
    """Loads configuration from a JSON or key-value file specified by config_path or environment variables."""
    if config_path is None:
        config_path = get_config_file_path()
    if not config_path:
        return {}

    config_path = os.path.expanduser(os.path.expandvars(config_path))
    if not os.path.isfile(config_path):
        print(f"Config file not found at: {config_path}", flush=True)
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = {}
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith(";"):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        data[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as e:
        print(f"Error reading config file {config_path}: {e}", flush=True)
        return {}

    applied = {}
    with data_lock:
        for key, val in data.items():
            if key in CONFIG_PARSERS:
                try:
                    parsed_val = CONFIG_PARSERS[key](val)
                    globals()[key] = parsed_val
                    applied[key] = parsed_val
                except Exception as e:
                    print(f"Error parsing {key} from config file: {e}", flush=True)
    if applied:
        print(f"Loaded configuration from {config_path}: {list(applied.keys())}", flush=True)
    return applied

def save_config_file(config_path=None):
    """Writes the current configuration to the config file specified by config_path or environment variables."""
    if config_path is None:
        config_path = get_config_file_path()
    if not config_path:
        return False

    config_path = os.path.expanduser(os.path.expandvars(config_path))

    current_config = {}
    with data_lock:
        for key in CONFIG_PARSERS:
            val = globals().get(key)
            if isinstance(val, tuple):
                val = list(val)
            current_config[key] = val

    parent_dir = os.path.dirname(config_path)
    if parent_dir and not os.path.exists(parent_dir):
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except OSError as e:
            print(f"Error creating directory {parent_dir}: {e}", flush=True)
            return False

    is_ini = False
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content and not content.startswith("{"):
                    is_ini = True
        except Exception:
            pass
    elif config_path.endswith((".ini", ".conf", ".env")):
        is_ini = True

    tmp_path = f"{config_path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            if is_ini:
                for k, v in current_config.items():
                    if isinstance(v, list):
                        v_str = ",".join(str(item) for item in v)
                    else:
                        v_str = str(v)
                    f.write(f"{k}={v_str}\n")
            else:
                json.dump(current_config, f, indent=4)
                f.write("\n")
        os.replace(tmp_path, config_path)
        return True
    except Exception as e:
        print(f"Error writing config file {config_path}: {e}", flush=True)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False

# Initial configuration load from file if specified by environment variable
load_config_file()


class FlightWallRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP request handler serving local assets and endpoints /configure and /detected."""
    
    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)
        
        if path == '/':
            self.handle_root()
        elif path == '/configure':
            self.handle_configure(query_params)
        elif path == '/detected':
            self.handle_detected(query_params)
        else:
            super().do_GET()

    def handle_root(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        response = {
            "endpoints": {
                "/configure": "Update or retrieve system configuration. Accepts parameters like HOME_LAT, HOME_LON, etc.",
                "/detected": "Retrieve recently detected flights. Accepts 'MINUTES' parameter (defaults to 60)."
            }
        }
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def handle_configure(self, query_params):
        if not query_params:
            # Return current configuration
            current_config = {}
            for key in CONFIG_PARSERS:
                current_config[key] = globals().get(key)
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {
                "status": "success",
                "message": "Current configuration retrieved.",
                "current_configuration": current_config
            }
            self.wfile.write(json.dumps(response).encode('utf-8'))
            return

        updated_params = {}
        errors = []
        
        with data_lock:
            for key, vals in query_params.items():
                if key in CONFIG_PARSERS:
                    val = vals[-1]
                    try:
                        parsed_val = CONFIG_PARSERS[key](val)
                        globals()[key] = parsed_val
                        updated_params[key] = parsed_val
                    except Exception as e:
                        errors.append(f"Invalid value for {key}: {str(e)}")
                else:
                    errors.append(f"Unknown configuration parameter: {key}")
            
            if errors:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                response = {
                    "status": "error",
                    "message": "; ".join(errors)
                }
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return

            # Prepare current config for response
            current_config = {}
            for key in CONFIG_PARSERS:
                current_config[key] = globals().get(key)

        if updated_params:
            save_config_file()

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        response = {
            "status": "success",
            "updated_parameters": updated_params,
            "current_configuration": current_config
        }
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def handle_detected(self, query_params):
        minutes_list = query_params.get("MINUTES") or query_params.get("minutes")
        if not minutes_list:
            minutes = 60
        else:
            minutes_str = minutes_list[-1]
            try:
                minutes = int(minutes_str)
                if minutes <= 0:
                    raise ValueError("MINUTES must be a positive integer.")
            except ValueError as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                response = {
                    "status": "error",
                    "message": f"Invalid MINUTES parameter: {str(e)}"
                }
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return

        # limit to 1 week (7 * 24 * 60 = 10080 minutes)

        # limit to 1 week (7 * 24 * 60 = 10080 minutes)
        if minutes > 10080:
            minutes = 10080
            
        now_epoch = time.time()
        cutoff = now_epoch - minutes * 60
        
        with data_lock:
            # First, clean up history older than 7 days
            global detected_flights_history
            seven_days_cutoff = now_epoch - 7 * 24 * 60 * 60
            detected_flights_history = [f for f in detected_flights_history if f["timestamp_epoch"] >= seven_days_cutoff]
            
            # Filter for requested minutes
            filtered_flights = [f for f in detected_flights_history if f["timestamp_epoch"] >= cutoff]
            
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        response = {
            "status": "success",
            "minutes_requested": minutes,
            "flights_count": len(filtered_flights),
            "flights": filtered_flights
        }
        self.wfile.write(json.dumps(response).encode('utf-8'))


# --- HELPER FUNCTIONS ---

def is_within_active_hours():
    """Checks if the current local machine time falls within the defined window."""
    now = datetime.now().time()
    start_time = datetime.strptime(ACTIVE_HOURS[0], "%H:%M").time()
    end_time = datetime.strptime(ACTIVE_HOURS[1], "%H:%M").time()
    
    if start_time <= end_time:
        return start_time <= now <= end_time
    else: # Handles overnight windows (e.g., 21:00 to 09:00)
        return now >= start_time or now <= end_time

def start_local_server():
    """Runs a background web server to serve the image/audio."""
    Handler = FlightWallRequestHandler
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("", PORT), Handler)
    httpd.serve_forever()

def send_android_push(callsign, altitude, origin, dest, ac_type):
    """Sends a push notification to your phone via Pushover."""
    if not PUSHOVER_USER_KEY or PUSHOVER_USER_KEY == "your_user_key":
        return
    try:
        requests.post("https://api.pushover.net/1/messages.json", data={
            "token": "your_app_token",
            "user": PUSHOVER_USER_KEY,
            "title": "✈️ Plane Overhead!",
            "message": f"{ac_type} ({callsign}) from {origin} to {dest} is flying overhead at {altitude} ft."
        }, timeout=5)
    except Exception as e:
        print(f"Warning: Failed to send Pushover push notification: {e}", flush=True)

def get_large_font():
    """Attempts to load a standard system font to double the text size."""
    font_paths = [
        "arial.ttf",                                        # Windows
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux / Raspberry Pi
        "/Library/Fonts/Arial.ttf"                          # macOS
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, 32)
        except IOError:
            continue
            
    print("Warning: Could not find system font for larger text. Using default.", flush=True)
    return ImageFont.load_default()

def create_alert_media(callsign, plane_lat, plane_lon, altitude, origin_iata, origin_name, dest_iata, dest_name, trail, ac_type, gspeed, vspeed):
    """Generates the TTS audio and the Map image with the flight track."""
    
    # 1. Render the Base Map
    m = StaticMap(1024, 600, url_template='https://a.tile.openstreetmap.org/{z}/{x}/{y}.png')
    m.add_marker(CircleMarker((HOME_LON, HOME_LAT), 'red', 8))
    
    if trail and len(trail) > 1:
        track_coords = [(pt['lng'], pt['lat']) for pt in trail if isinstance(pt, dict) and 'lng' in pt and 'lat' in pt]
        if len(track_coords) > 1:
            m.add_line(Line(track_coords, 'blue', 4))
        
    m.add_marker(CircleMarker((plane_lon, plane_lat), 'black', 12))
    img = m.render(zoom=10, center=[HOME_LON, HOME_LAT])
    
    # 2. Advanced Overlay Text Data (True Transparency)
    # Convert base image to RGBA (allows alpha channel)
    img = img.convert("RGBA")
    
    # Create a blank, fully transparent image the exact same size as the map
    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    d = ImageDraw.Draw(overlay)
    font = get_large_font()
    
    # Draw Light Grey Rounded Rectangle with 50% Opacity (128 out of 255)
    # The 'radius' parameter controls how curved the corners are
    d.rounded_rectangle([(10, 10), (850, 230)], radius=15, fill=(200, 200, 200, 128))
    
    # Format Vertical Speed
    vspeed = vspeed or 0
    vspeed_str = f"+{vspeed}" if vspeed > 0 else str(vspeed)
    
    # Write the expanded data in Solid Black (0, 0, 0, 255)
    text_color = (0, 0, 0, 255)
    d.text((20, 20), f"FLIGHT: {callsign}  |  {ac_type}", font=font, fill=text_color)
    d.text((20, 60), f"FROM: {origin_iata} - {origin_name}", font=font, fill=text_color)
    d.text((20, 100), f"TO: {dest_iata} - {dest_name}", font=font, fill=text_color)
    d.text((20, 140), f"ALT: {altitude} ft  |  V/S: {vspeed_str} fpm", font=font, fill=text_color)
    d.text((20, 180), f"SPD: {gspeed} kts", font=font, fill=text_color)
    
    # Merge the transparent overlay box onto the base map
    img = Image.alpha_composite(img, overlay)
    
    # Convert back to standard RGB to save as a standard JPEG file
    img = img.convert("RGB")
    img.save("alert.jpg")

def cast_to_hub():
    """Finds the Nest Hub and casts the generated media with strict timeouts."""
    cast = None
    browser = None
    try:
        chromecasts, browser = pychromecast.get_listed_chromecasts(
            friendly_names=[HUB_NAME],
            tries=1,
            timeout=5,
            retry_wait=1.0,
            discovery_timeout=5
        )
        if not chromecasts:
            print(f"Could not find Chromecast: {HUB_NAME}", flush=True)
            return
        
        cast = chromecasts[0]
        cast.wait(timeout=5)
        mc = cast.media_controller
        
        img_url = f"http://{LOCAL_IP}:{PORT}/alert.jpg"
        mc.play_media(img_url, 'image/jpeg')
        mc.block_until_active(timeout=5)
        
        time.sleep(2) # Buffer time for the image to load
        time.sleep(CAST_DURATION_SECONDS) # Keep on screen for user-defined duration
        cast.quit_app()
    except Exception as e:
        print(f"Error casting to {HUB_NAME}: {e}", flush=True)
    finally:
        if cast:
            try:
                cast.disconnect(timeout=2)
            except Exception:
                pass
        if browser:
            try:
                pychromecast.discovery.stop_discovery(browser)
            except Exception:
                pass

cast_trigger_event = threading.Event()

def cast_worker_loop():
    """Background worker that processes cast requests sequentially without blocking the radar loop."""
    while True:
        cast_trigger_event.wait()
        cast_trigger_event.clear()
        try:
            cast_to_hub()
        except Exception as e:
            print(f"Unexpected error in cast worker: {e}", flush=True)

def trigger_cast():
    """Signals the background cast worker to cast the latest alert."""
    cast_trigger_event.set()

# --- MAIN LOOP ---

if __name__ == "__main__":
    load_config_file()
    threading.Thread(target=start_local_server, daemon=True).start()
    threading.Thread(target=cast_worker_loop, daemon=True).start()
    print("FlightWall radar is active (Powered by FR24)...", flush=True)
    
    while True:
        try:
            bounds = fr_api.get_bounds_by_point(HOME_LAT, HOME_LON, RADAR_RADIUS_METERS)
            flights = fr_api.get_flights(bounds=bounds)

            for flight in flights:
                if flight.id not in known_flights:
                    known_flights.add(flight.id)
                    callsign = (flight.callsign or "").strip() or "UNKNOWN"
                    altitude = flight.altitude 
                    
                    gspeed = flight.ground_speed or 0
                    vspeed = flight.vertical_speed or 0
                    ac_type = flight.aircraft_code or "Unknown Aircraft"
                    
                    print(f"New flight detected: {callsign}", flush=True)

                    details = None
                    try:
                        details = fr_api.get_flight_details(flight)
                    except Exception as err:
                        print(f"Warning: Could not fetch flight details for {callsign} ({flight.id}): {err}", flush=True)
                    
                    origin_iata = "UNKNOWN"
                    dest_iata = "UNKNOWN"
                    origin_name = "Unknown Location"
                    dest_name = "Unknown Location"
                    
                    if details and isinstance(details, dict):
                        aircraft_info = details.get('aircraft')
                        if aircraft_info and isinstance(aircraft_info, dict):
                            model_info = aircraft_info.get('model')
                            if model_info and isinstance(model_info, dict):
                                ac_type = model_info.get('text') or ac_type

                        airport_info = details.get('airport')
                        if airport_info and isinstance(airport_info, dict):
                            origin_info = airport_info.get('origin')
                            if origin_info and isinstance(origin_info, dict):
                                code_info = origin_info.get('code')
                                if code_info and isinstance(code_info, dict):
                                    origin_iata = code_info.get('iata') or 'UNKNOWN'
                                raw_origin = origin_info.get('name') or 'Unknown Location'
                                origin_name = raw_origin.replace(" Airport", "").replace(" International", "")
                                
                            dest_info = airport_info.get('destination')
                            if dest_info and isinstance(dest_info, dict):
                                code_info = dest_info.get('code')
                                if code_info and isinstance(code_info, dict):
                                    dest_iata = code_info.get('iata') or 'UNKNOWN'
                                raw_dest = dest_info.get('name') or 'Unknown Location'
                                dest_name = raw_dest.replace(" Airport", "").replace(" International", "")
                            
                    trail = details.get('trail', []) if details and isinstance(details, dict) else []
                    
                    # Log the detected flight in the thread-safe history
                    detected_flight_info = {
                        "id": flight.id,
                        "callsign": callsign,
                        "altitude": altitude,
                        "ground_speed": gspeed,
                        "vertical_speed": vspeed,
                        "aircraft_type": ac_type,
                        "latitude": flight.latitude,
                        "longitude": flight.longitude,
                        "origin_iata": origin_iata,
                        "origin_name": origin_name,
                        "destination_iata": dest_iata,
                        "destination_name": dest_name,
                        "timestamp": datetime.now().isoformat(),
                        "timestamp_epoch": time.time()
                    }
                    with data_lock:
                        now_epoch = time.time()
                        # prune anything older than 7 days
                        cutoff = now_epoch - 7 * 24 * 60 * 60
                        detected_flights_history = [f for f in detected_flights_history if f["timestamp_epoch"] >= cutoff]
                        detected_flights_history.append(detected_flight_info)

                    if not is_within_active_hours():
                        print(f"Alert suppressed for {callsign}: current time is outside active hours ({ACTIVE_HOURS[0]} - {ACTIVE_HOURS[1]}).", flush=True)
                        continue

                    send_android_push(callsign, altitude, origin_iata, dest_iata, ac_type)
                    create_alert_media(callsign, flight.latitude, flight.longitude, altitude, origin_iata, origin_name, dest_iata, dest_name, trail, ac_type, gspeed, vspeed)
                    trigger_cast()
            
        except Exception as e:
            print(f"Error checking flights: {e}", flush=True)
            
        if len(known_flights) > 1000:
            known_flights.clear()
            
        time.sleep(30)
