import locale
import os
import re
import sys
import time
import socket
import random
import asyncio
import argparse
import requests
import webbrowser
import pycountry

from flask import Flask, jsonify, render_template, request
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
from contextlib import asynccontextmanager

from device import DeveloperModeRequired, DeviceError, DeviceManager

#========= Arg Parser ========
# Parse command-line arguments
parser = argparse.ArgumentParser()
parser.add_argument('--no-browser', action='store_true', help='Skip auto opening the browser')
parser.add_argument('--port', type=int, help='Specify port number to listen on for web browser requests')
args = parser.parse_args()
#========= Arg Parser ========

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


import logging


# Get or create a logger instance named "GeoPort"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)

# Create a logger named "GeoPort"
logger = logging.getLogger("GeoPort")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("pymobiledevice3").setLevel(logging.INFO)

logging.getLogger('werkzeug').disabled = True
#log.disabled = True

app = Flask(__name__)

# Define constants
# Get the home directory of the current user
home_dir = os.path.expanduser("~")
is_windows = sys.platform == 'win32'
base_directory = getattr(sys, '_MEIPASS', os.path.abspath(os.path.dirname(sys.argv[0])))
flask_port = 54321
user_locale = None
location = None
error_message = None
sudo_message = ""
APP_VERSION_NUMBER = "2.3.3"
APP_VERSION_TYPE = "fuel"

# Get the current platform using sys.platform
current_platform = sys.platform

# Map the platform names to standard values
platform = {
    'win32': 'Windows',
    'linux': 'Linux',
    'darwin': 'MacOS',
}.get(current_platform, 'Unknown')

# The iPhone session (see device.py)
device = DeviceManager()


def create_geoport_folder():
    # Define the path to the GeoPort folder
    geoport_folder = os.path.join(home_dir, 'GeoPort')

    # Check if the GeoPort folder exists, create it if not
    if not os.path.exists(geoport_folder):
        os.makedirs(geoport_folder)
        logger.info(f"GeoPort Home: {geoport_folder}")
        logger.info("GeoPort folder created successfully")

    # Set permissions for the GeoPort folder
    if current_platform == 'win32':
        # Windows permissions (read/write for everyone)
        os.system(f"icacls {geoport_folder} /grant Everyone:(OI)(CI)F")
        logger.info("Permissions set for GeoPort folder on Windows")
    else:  # Linux and MacOS
        # POSIX permissions (read/write for everyone)
        os.chmod(geoport_folder, 0o777)
        logger.info("Permissions set for GeoPort folder on MacOS")


def get_user_country():
    global user_locale
    try:
        # Attempt to get the user's country using locale and pycountry
        user_locale, _ = locale.getlocale()

        if user_locale is None:
            logger.warning("User locale is None. Defaulting to IP geolocation service.")
            return get_country_from_ip()

        country_code = user_locale.split('_')[-1]
        country = pycountry.countries.get(alpha_2=country_code)
        country_name = country.name if country else None

        # If country_name is None, try IP geolocation service as a fallback
        if country_name is None:
            logger.warning("Failed to retrieve country name using locale. Using IP geolocation service.")
            return get_country_from_ip()
        else:
            return country_name

    except Exception as e:
        logger.error(f"Error getting user country: {e}")
        return None


def get_country_from_ip():
    try:
        response = requests.get("http://ip-api.com/json/", timeout=5)
        if response.status_code == 200:
            data = response.json()
            country_name = data.get("country")
            if country_name:
                return country_name
            else:
                logger.warning("Failed to retrieve country name from IP geolocation service.")
        else:
            logger.error(f"Error: Unable to retrieve data. Status code: {response.status_code}")
            logger.warning("Setting to default country")
            country_name = "Spain"
        return country_name
    except Exception as e:
        logger.error(f"Error getting country from IP geolocation service: {e}")
        country_name = "Spain"
        return country_name


def remove_ansi_escape_codes(text):
    ansi_escape = re.compile(r'\x1b[^m]*m')
    return ansi_escape.sub('', text)


@app.route('/update_location', methods=['POST'])
def update_location():
    # Use 'request' to get the JSON data from the client
    data = request.get_json()

    # Convert latitude and longitude to float values
    lat = float(data['lat'])
    lng = float(data['lng'])

    global location
    location = f"{lat} {lng}"
    return 'Location updated successfully'


@app.route('/list_devices')
def py_list_devices():
    try:
        devices = device.list_devices()
    except DeviceError as e:
        logger.error(f"Listing devices failed: {e}")
        return jsonify({})

    # The page expects {udid: {connection type: [device info]}}
    connected_devices = {}
    for info in devices:
        connected_devices.setdefault(info["Identifier"], {}).setdefault(info.get("ConnectionType", "USB"), []).append(info)
    logger.info(f"Connected devices: {connected_devices}")
    return jsonify(connected_devices)


@app.route('/connect_device', methods=['POST'])
def connect_device():
    udid = request.get_json().get('udid')
    logger.info(f"Connecting to {udid}")
    try:
        info = device.connect(udid)
    except DeveloperModeRequired:
        return jsonify({'developer_mode_required': 'True'})
    except DeviceError as e:
        logger.error(f"Connecting failed: {e}")
        return jsonify({'error': str(e)})
    return jsonify({'rsd_data': f"{info['name']} (iOS {info['ios_version']})"})


@app.route('/enable_developer_mode', methods=['POST'])
def enable_developer_mode_route():
    udid = request.get_json().get('udid')
    try:
        device.enable_developer_mode(udid)
    except DeviceError as e:
        logger.error(f"Enabling Developer Mode failed: {e}")
        return jsonify({'error': str(e)})
    return jsonify({'success': True, 'udid': udid})


@app.route('/set_location', methods=['POST'])
def set_location():
    if location is None:
        return 'Pick a location on the map first.', 400
    latitude, longitude = location.split()
    try:
        device.set_location(latitude, longitude)
    except DeviceError as e:
        logger.error(f"Setting the location failed: {e}")
        return str(e), 500
    return 'Location set successfully'


@app.route('/stop_location', methods=['POST'])
def stop_location():
    try:
        device.clear_location()
    except DeviceError as e:
        logger.error(f"Clearing the location failed: {e}")
        return str(e), 500
    return 'Location cleared successfully'


@app.route('/exit', methods=['POST'])
def exit_app():
    logger.warning("Exit GeoPort")
    device.close()
    os._exit(0)


@app.route('/')
def index():
    user_locale = get_user_country()
    logger.info(f"Country: {user_locale}")
    logger.info(f"Current platform: {platform}")
    logger.info(f"App Version = {APP_VERSION_NUMBER}")
    logger.info(f"base dir =  {base_directory}")

    return render_template('map.html', user_locale=user_locale, app_version_num=APP_VERSION_NUMBER,
                           app_version_type=APP_VERSION_TYPE, error_message=error_message, current_platform=platform,
                           sudo_message=sudo_message)


def open_browser():
    time.sleep(2)  # Wait for the Flask app to start
    #webbrowser.open(f'http://localhost:{chosen_port}')
    browser = webbrowser.get()
    browser.open(f'http://localhost:{chosen_port}')


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0
    # with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    #     try:
    #         s.bind((' ', port))
    #         return False  # Port is available
    #     except OSError:
    #         return True  # Port is already in use


# Define try_bind_listener_on_free_port function
def try_bind_listener_on_free_port():
    global chosen_port
    min_port = 49215
    max_port = 65535

    # Check if --port argument is provided
    if args.port:
        chosen_port = args.port
    else:
        chosen_port = flask_port

    if is_port_in_use(chosen_port):
        chosen_port = random.randint(min_port, max_port)
    logger.info(f'Serving: http://localhost:{chosen_port}')
    return chosen_port


if __name__ == '__main__':
    #create_geoport_folder()
    if is_windows:
        try:
            import pyi_splash

            pyi_splash.update_text('UI Loaded ...')
            logger.info("clear splash")
            pyi_splash.close()
        except:
            pass
    #else:




    chosen_port = try_bind_listener_on_free_port()

    # Check if --no-browser flag is provided
    if not args.no_browser:
        open_browser()
    else:
        logger.info("--no-browser flag passed")
        logger.info("Running without auto-browser popup")



    #threading.Thread(target=open_browser).start()

    app.run(debug=True, use_reloader=False, port=chosen_port, host='0.0.0.0')




