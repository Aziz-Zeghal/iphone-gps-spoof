"""The iPhone session: pairing, Developer Mode, the developer disk image and location simulation.

pymobiledevice3 is async while Flask is not, so every device call runs on one private event loop
in a background thread, and the session (tunnel + location service) stays open between requests.
"""

import asyncio
import contextlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("GeoPort")

CONNECT_TIMEOUT = 300  # first connect downloads and mounts the developer disk image
CALL_TIMEOUT = 30


class DeviceError(Exception):
    """A device problem, with a message meant for the person using the app."""


class DeveloperModeRequired(DeviceError):
    def __init__(self) -> None:
        super().__init__("Developer Mode is off on the iPhone.")


def explain(error: BaseException) -> str:
    """Turn a pymobiledevice3 error into what the person should do about it."""
    name = type(error).__name__
    if "DeviceLocked" in str(error) or name == "PasswordRequiredError":
        return "Unlock the iPhone and keep it unlocked, then connect again."
    if name == "PairingDialogResponsePendingError":
        return "Tap Trust on the iPhone, then connect again."
    if name == "UserDeniedPairingError":
        return "The iPhone refused to trust this computer. Unplug it, plug it back in and tap Trust."
    if name == "ConnectionFailedToUsbmuxdError":
        return "Apple's USB service isn't running. Install the Apple Devices app or iTunes, then try again."
    if name in ("DeviceNotFoundError", "NoDeviceConnectedError"):
        return "Plug the iPhone in by USB, then refresh the list."
    if name == "DeviceHasPasscodeSetError":
        return ("Developer Mode can only be turned on from here when the iPhone has no passcode. "
                "Turn it on in Settings > Privacy & Security > Developer Mode instead.")
    return f"{name}: {error}"


class Pmd3Backend:
    """The pymobiledevice3 calls DeviceManager needs (imported lazily so tests can run without it)."""

    async def list_devices(self) -> list[dict[str, Any]]:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.usbmux import list_devices

        devices = []
        for mux_device in await list_devices():
            if mux_device.connection_type != "USB":
                continue
            try:
                async with await create_using_usbmux(serial=mux_device.serial, autopair=True) as lockdown:
                    devices.append(dict(lockdown.short_info))
            except Exception as e:
                # Still list a phone that is locked or not trusted yet, with what to do in its name
                logger.warning(f"Could not read {mux_device.serial}: {explain(e)}")
                devices.append({"Identifier": mux_device.serial, "DeviceName": f"iPhone ({explain(e)})",
                                "DeviceClass": "iPhone", "ProductVersion": "?", "ConnectionType": "USB"})
        return devices

    async def open_lockdown(self, udid: str) -> Any:
        from pymobiledevice3.lockdown import create_using_usbmux

        return await create_using_usbmux(serial=udid, autopair=True)

    async def close_lockdown(self, lockdown: Any) -> None:
        await lockdown.close()

    def describe(self, lockdown: Any) -> dict[str, str]:
        info = lockdown.short_info
        return {"name": info.get("DeviceName", "iPhone"), "ios_version": info.get("ProductVersion", "")}

    async def developer_mode_status(self, lockdown: Any) -> bool:
        return await lockdown.get_developer_mode_status()

    async def enable_developer_mode(self, lockdown: Any) -> None:
        from pymobiledevice3.services.amfi import AmfiService

        await AmfiService(lockdown).enable_developer_mode()

    async def mount_developer_image(self, lockdown: Any) -> None:
        from pymobiledevice3.services.mobile_image_mounter import auto_mount

        await auto_mount(lockdown)

    async def open_location(self, lockdown: Any, udid: str, stack: contextlib.AsyncExitStack) -> Any:
        """Open the location service; everything opened is closed by `stack`."""
        major = int(lockdown.short_info.get("ProductVersion", "0").split(".")[0])
        if major < 17:
            from pymobiledevice3.services.simulate_location import DtSimulateLocation

            return DtSimulateLocation(lockdown)

        from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

        # No-admin tunnel to the iOS 17+ developer services
        rsd = await stack.enter_async_context(PreferredRsdTunnel(serial=udid))
        dvt = await stack.enter_async_context(DvtProvider(rsd))
        return await stack.enter_async_context(LocationSimulation(dvt))


@dataclass
class _Session:
    udid: str
    lockdown: Any
    location: Any
    info: dict[str, str]
    stack: contextlib.AsyncExitStack = field(default_factory=contextlib.AsyncExitStack)


class DeviceManager:
    def __init__(self, backend: Any = None) -> None:
        self._backend = backend or Pmd3Backend()
        self._session: _Session | None = None
        self._loop = asyncio.new_event_loop()
        self._lock = asyncio.Lock()
        self._thread = threading.Thread(target=self._loop.run_forever, name="device-loop", daemon=True)
        self._thread.start()

    # ---- called from Flask (any thread) ----

    def list_devices(self) -> list[dict[str, Any]]:
        return self._run(self._backend.list_devices(), CALL_TIMEOUT)

    def connect(self, udid: str) -> dict[str, str]:
        return self._run(self._connect(udid), CONNECT_TIMEOUT)

    def set_location(self, latitude: float, longitude: float) -> None:
        self._run(self._set_location(float(latitude), float(longitude)), CALL_TIMEOUT)

    def clear_location(self) -> None:
        self._run(self._clear_location(), CALL_TIMEOUT)

    def disconnect(self) -> None:
        self._run(self._disconnect(), CALL_TIMEOUT)

    def enable_developer_mode(self, udid: str) -> None:
        self._run(self._enable_developer_mode(udid), CALL_TIMEOUT)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.disconnect()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    # ---- on the device loop ----

    def _run(self, coroutine: Any, timeout: float) -> Any:
        future = asyncio.run_coroutine_threadsafe(self._locked(coroutine), self._loop)
        try:
            return future.result(timeout)
        except DeviceError:
            raise
        except TimeoutError as e:
            future.cancel()  # otherwise it keeps the lock and blocks every later call
            raise DeviceError("The iPhone took too long to answer. Unplug it, plug it back in and try again.") from e
        except Exception as e:
            logger.exception("Device call failed")
            raise DeviceError(explain(e)) from e

    async def _locked(self, coroutine: Any) -> Any:
        async with self._lock:
            return await coroutine

    async def _connect(self, udid: str) -> dict[str, str]:
        if self._session is not None:
            if self._session.udid == udid:
                return self._session.info
            await self._disconnect()

        lockdown = await self._backend.open_lockdown(udid)
        stack = contextlib.AsyncExitStack()
        try:
            if not await self._backend.developer_mode_status(lockdown):
                raise DeveloperModeRequired()
            logger.info("Mounting the developer disk image")
            await self._backend.mount_developer_image(lockdown)
            logger.info("Opening the location service")
            location = await self._backend.open_location(lockdown, udid, stack)
        except BaseException:
            await stack.aclose()
            await self._backend.close_lockdown(lockdown)
            raise

        info = self._backend.describe(lockdown)
        self._session = _Session(udid=udid, lockdown=lockdown, location=location, info=info, stack=stack)
        logger.info(f"Connected to {info['name']} (iOS {info['ios_version']})")
        return info

    async def _set_location(self, latitude: float, longitude: float) -> None:
        session = self._require_session()
        try:
            await session.location.set(latitude, longitude)
        except Exception as e:
            logger.exception("Setting the location failed")
            await self._drop_session()
            raise DeviceError(f"Lost the connection to the iPhone. Connect again. ({explain(e)})") from e
        logger.info(f"Location set to {latitude}, {longitude}")

    async def _clear_location(self) -> None:
        if self._session is None:
            return
        try:
            await self._session.location.clear()
        except Exception as e:
            logger.exception("Clearing the location failed")
            await self._drop_session()
            raise DeviceError(f"Lost the connection to the iPhone. Connect again. ({explain(e)})") from e
        logger.info("Location cleared")

    async def _disconnect(self) -> None:
        if self._session is None:
            return
        with contextlib.suppress(Exception):
            await self._session.location.clear()
        await self._drop_session()

    async def _enable_developer_mode(self, udid: str) -> None:
        lockdown = await self._backend.open_lockdown(udid)
        try:
            await self._backend.enable_developer_mode(lockdown)
        finally:
            await self._backend.close_lockdown(lockdown)

    def _require_session(self) -> _Session:
        if self._session is None:
            raise DeviceError("Connect the iPhone first.")
        return self._session

    async def _drop_session(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        with contextlib.suppress(Exception):
            await session.stack.aclose()
        with contextlib.suppress(Exception):
            await self._backend.close_lockdown(session.lockdown)
