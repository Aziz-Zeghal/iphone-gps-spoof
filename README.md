# iphone-gps-spoof

This project is a fork of [GeoPort](https://github.com/davesc63/GeoPort) by [davesc63](https://github.com/davesc63), a tool to simulate the GPS location of an iOS device.

## Setup

```bash
conda create -n gps_iphone python=3.12
conda activate gps_iphone
uv pip install -r requirements.txt
```

## Usage

```bash
conda activate gps_iphone
python src/main.py --no-browser --port 54321
```

On Windows, download the latest build from the [Build Windows exe](../../actions/workflows/build-windows.yml) workflow (or run it there), extract it and start `iphone-gps-spoof.exe`. Windows also needs Apple's USB driver, from the Apple Devices app or iTunes.

Then open `http://localhost:54321`, or `http://<tailscale-ip>:54321` from another device on your tailnet.

- No admin or root rights are needed: the tunnel to iOS 17+ devices runs in-process.
- Plug the iPhone in by USB and accept "Trust This Computer". Developer Mode must be on (Settings > Privacy & Security > Developer Mode).
- Keep the iPhone unlocked while connecting: the first connection after each restart of the phone mounts Apple's developer disk image.
- If the port is taken, the app silently picks a random one. Check the `Serving: http://localhost:<port>` log line.
- There is no login and Flask runs in debug mode. Keep it on your LAN or Tailscale; never expose it to the internet.

## Development

- Backend: `src/main.py` (Flask routes) and `src/device.py` (the iPhone session, on [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) 11.23).
- Frontend: `src/templates/map.html`, plain HTML/CSS/JS with libraries loaded from CDNs. No build step: edit, restart the server, refresh. `src/templates/map2.html` is not used.
- Based on GeoPort v2.3.3, the latest source upstream published, with the device layer rewritten for current pymobiledevice3 (iOS 26 support).
- Dependencies: add the package to `requirements.in`, then regenerate `requirements.txt` with the command at its top.
- Tests: `uvx --with-requirements requirements.txt --with pytest pytest` (device.py runs against a fake phone).
- Lint with `uvx ruff check src` (config in `pyproject.toml`).

## License

Licensed under the GNU General Public License v3.0, like the original project. See [LICENSE](LICENSE).
