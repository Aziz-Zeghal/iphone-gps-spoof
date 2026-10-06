import pytest

from device import DeveloperModeRequired, DeviceError, DeviceManager, explain

IPHONE = {
    "Identifier": "00008120-TEST",
    "DeviceName": "iPhone 15",
    "DeviceClass": "iPhone",
    "ProductVersion": "26.2",
    "ConnectionType": "USB",
}


def named_error(name, message=""):
    """An exception whose class name matches one of pymobiledevice3's."""
    return type(name, (Exception,), {})(message)


class FakeLocation:
    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with

    async def set(self, latitude, longitude):
        if self.fail_with:
            raise self.fail_with
        self.calls.append(("set", latitude, longitude))

    async def clear(self):
        self.calls.append(("clear",))


class FakeBackend:
    def __init__(self, developer_mode=True, mount_error=None, location=None):
        self.developer_mode = developer_mode
        self.mount_error = mount_error
        self.location = location or FakeLocation()
        self.opened_lockdowns = 0
        self.closed_lockdowns = 0
        self.location_closed = False
        self.mounts = 0
        self.locations_opened = 0
        self.developer_mode_enabled = 0
        self.enable_error = None

    async def list_devices(self):
        return [dict(IPHONE)]

    async def open_lockdown(self, udid):
        self.opened_lockdowns += 1
        return {"udid": udid, "ProductVersion": IPHONE["ProductVersion"], "DeviceName": IPHONE["DeviceName"]}

    async def close_lockdown(self, lockdown):
        self.closed_lockdowns += 1

    def describe(self, lockdown):
        return {"name": lockdown["DeviceName"], "ios_version": lockdown["ProductVersion"]}

    async def developer_mode_status(self, lockdown):
        return self.developer_mode

    async def enable_developer_mode(self, lockdown):
        if self.enable_error:
            raise self.enable_error
        self.developer_mode_enabled += 1

    async def mount_developer_image(self, lockdown):
        self.mounts += 1
        if self.mount_error:
            raise self.mount_error

    async def open_location(self, lockdown, udid, stack):
        self.locations_opened += 1

        async def mark_closed():
            self.location_closed = True

        stack.push_async_callback(mark_closed)
        return self.location


@pytest.fixture
def make_manager():
    managers = []

    def make(backend):
        manager = DeviceManager(backend=backend)
        managers.append(manager)
        return manager

    yield make
    for manager in managers:
        manager.close()


def test_list_devices_returns_backend_devices(make_manager):
    assert make_manager(FakeBackend()).list_devices() == [IPHONE]


def test_connect_mounts_and_opens_location_once(make_manager):
    backend = FakeBackend()
    manager = make_manager(backend)

    assert manager.connect("00008120-TEST") == {"name": "iPhone 15", "ios_version": "26.2"}
    manager.connect("00008120-TEST")  # already connected: no second session

    assert (backend.mounts, backend.locations_opened) == (1, 1)


def test_connect_requires_developer_mode(make_manager):
    backend = FakeBackend(developer_mode=False)
    manager = make_manager(backend)

    with pytest.raises(DeveloperModeRequired):
        manager.connect("00008120-TEST")
    assert backend.closed_lockdowns == backend.opened_lockdowns == 1
    assert backend.mounts == 0


def test_locked_phone_during_mount_explains_and_cleans_up(make_manager):
    locked = named_error("PyMobileDevice3Exception", "command ReceiveBytes failed with: {'Error': 'DeviceLocked'}")
    backend = FakeBackend(mount_error=locked)
    manager = make_manager(backend)

    with pytest.raises(DeviceError, match="Unlock the iPhone"):
        manager.connect("00008120-TEST")
    assert backend.closed_lockdowns == 1
    with pytest.raises(DeviceError, match="Connect the iPhone first"):
        manager.set_location(1, 2)


def test_set_location_needs_a_connection(make_manager):
    with pytest.raises(DeviceError, match="Connect the iPhone first"):
        make_manager(FakeBackend()).set_location(48.85, 2.35)


def test_set_and_clear_location_use_the_open_session(make_manager):
    backend = FakeBackend()
    manager = make_manager(backend)
    manager.connect("00008120-TEST")

    manager.set_location("48.8566", "2.3522")
    manager.clear_location()

    assert backend.location.calls == [("set", 48.8566, 2.3522), ("clear",)]


def test_lost_connection_drops_the_session(make_manager):
    backend = FakeBackend(location=FakeLocation(fail_with=ConnectionResetError("reset")))
    manager = make_manager(backend)
    manager.connect("00008120-TEST")

    with pytest.raises(DeviceError, match="Lost the connection"):
        manager.set_location(48.85, 2.35)
    assert backend.location_closed and backend.closed_lockdowns == 1
    with pytest.raises(DeviceError, match="Connect the iPhone first"):
        manager.set_location(48.85, 2.35)


def test_clear_location_without_a_session_is_a_no_op(make_manager):
    make_manager(FakeBackend()).clear_location()


def test_disconnect_clears_and_closes_everything(make_manager):
    backend = FakeBackend()
    manager = make_manager(backend)
    manager.connect("00008120-TEST")

    manager.disconnect()

    assert backend.location.calls == [("clear",)]
    assert backend.location_closed and backend.closed_lockdowns == 1


def test_enable_developer_mode_explains_passcode_error(make_manager):
    backend = FakeBackend()
    backend.enable_error = named_error("DeviceHasPasscodeSetError")
    manager = make_manager(backend)

    with pytest.raises(DeviceError, match="passcode"):
        manager.enable_developer_mode("00008120-TEST")
    assert backend.closed_lockdowns == 1


def test_a_call_that_times_out_does_not_block_later_calls(make_manager, monkeypatch):
    import asyncio

    import device

    class SlowBackend(FakeBackend):
        async def list_devices(self):
            await asyncio.sleep(60)

    manager = make_manager(SlowBackend())
    monkeypatch.setattr(device, "CALL_TIMEOUT", 0.2)

    with pytest.raises(DeviceError, match="took too long"):
        manager.list_devices()
    manager.clear_location()  # would hang if the timed-out call still held the lock


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (named_error("PairingDialogResponsePendingError"), "Tap Trust"),
        (named_error("PasswordRequiredError"), "Unlock the iPhone"),
        (named_error("ConnectionFailedToUsbmuxdError"), "Apple Devices"),
        (named_error("DeviceNotFoundError"), "Plug the iPhone in"),
        (named_error("SomethingNew", "boom"), "SomethingNew: boom"),
    ],
)
def test_explain(error, expected):
    assert expected in explain(error)
