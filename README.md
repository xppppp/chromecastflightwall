# ✈️ Virtual FlightWall Radar

This is a lightweight, standalone Python script that turns your Google Nest Hub (or any Chromecast-enabled display) into a localized flight tracking dashboard. 

Inspired by the physical "FlightWall" device, this script monitors the airspace above your home using the Flightradar24 API. When a plane enters your custom radius, the script generates a sleek radar map with the flight's historical trail and overlays human-readable flight data (aircraft type, origin, destination, speed, and altitude). It then casts this image directly to your smart display for a configured duration.

---

### ✨ Features
* **Real-Time Overhead Tracking:** Uses Flightradar24 to detect aircraft within a precise, custom radius around your GPS coordinates.
* **Rich Flight Data:** Automatically translates raw IATA/ICAO codes into human-readable aircraft types and airport names.
* **Dynamic Radar Map:** Generates a live OpenStreetMap image showing your home, the plane's current location, and the blue flight trail.
* **Frosted Glass UI:** Uses alpha compositing to draw a semi-transparent, rounded text box over the map for a modern dashboard look.
* **Quiet Hours:** Configurable active hours ensure your display doesn't wake you up with alerts in the middle of the night.
* **Built-in Local Server:** Automatically spins up a background web server to serve the generated assets to your local network display.

---

### 🌐 Remote Control API
The built-in local server supports API endpoints to control the radar and query history in real-time without restarting the script.

#### ⚙️ Configuration (`/configure`)
Dynamically update script settings via GET requests.
**Example:** `http://<LOCAL_IP>:65530/configure?RADAR_RADIUS_METERS=7000&HOME_LAT=38.89`

**Available Parameters:**
* `HOME_LAT` / `HOME_LON`: Your home GPS coordinates.
* `RADAR_RADIUS_METERS`: Detection radius in meters.
* `ACTIVE_HOURS`: Active window in `HH:MM,HH:MM` format (e.g., `09:00,21:00`).
* `CAST_DURATION_SECONDS`: How long the image stays on screen.
* `PUSHOVER_USER_KEY`: Your Pushover user key.
* `LOCAL_IP`: The IP address of the host machine.
* `PORT`: The local server port.
* `HUB_NAME`: The friendly name of your Chromecast.

#### 📜 Detection History (`/detected`)
Retrieve a JSON list of all flights detected in the past X minutes.
**Example:** `http://<LOCAL_IP>:65530/detected?MINUTES=60`

**Notes:**
* The `MINUTES` parameter is required and must be a positive integer.
* Maximum history retrieval is limited to one week (10,080 minutes).

---

### 🛠️ Prerequisites

1. **Python 3.x** installed on an always-on machine (like a Raspberry Pi, Mac mini, or home server).
2. **Static Local IP:** The machine running this script needs a fixed local IP address (e.g., `192.168.1.50`) so the Chromecast can find the generated images.
3. A **Google Nest Hub** or Chromecast device on the same Wi-Fi network.

#### Required Python Libraries
Open your terminal and install the required dependencies:
```bash
pip install requests gTTS pychromecast Pillow staticmap FlightRadarAPI
