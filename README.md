# iphone-gps-spoof

This project is a fork of [GeoPort](https://github.com/davesc63/GeoPort) by [davesc63](https://github.com/davesc63), a tool to simulate the GPS location of an iOS device.

## Setup

```bash
conda create -n gps_iphone python=3.12
conda activate gps_iphone
uv pip install -r requirements.txt
```

## Usage

iOS 17+ needs root (the app creates a network tunnel to the phone). `sudo` drops the conda env, so call the env's Python by its full path:

```bash
conda activate gps_iphone
sudo "$CONDA_PREFIX/bin/python" src/main.py --no-browser --port 54321
```

Then open `http://localhost:54321`, or `http://<tailscale-ip>:54321` from another device on your tailnet.

- The first connection must be over USB: plug in the iPhone and accept "Trust this computer". Wi-Fi works afterwards, on the same local network only.
- If the port is taken, the app silently picks a random one. Check the `Serving: http://localhost:<port>` log line.
- Other options: `--udid <id>` to target one device, `--wifihost <ip>` to connect by IP (iOS 16 and older).
- There is no login and Flask runs in debug mode. Keep it on your LAN or Tailscale; never expose it to the internet.

## Development

- Backend: `src/main.py` (Flask + [pymobiledevice3](https://github.com/doronz88/pymobiledevice3)).
- Frontend: `src/templates/map.html`, plain HTML/CSS/JS with libraries loaded from CDNs. No build step: edit, restart the server, refresh. `src/templates/map2.html` is not used.
- The code is GeoPort v2.3.3, the latest source upstream published. `pymobiledevice3` is pinned to 4.4.1, the newest release that matches it. Newer releases changed the API to async, so upgrading needs code changes.
- To add a package: `uv pip install <package>`, then add its pinned line to `requirements.txt`.

## License

Licensed under the GNU General Public License v3.0, like the original project. See [LICENSE](LICENSE).
