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
        self.tunnel_closed = False
        self.mounted_on = None
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

    async def open_service_provider(self, lockdown, udid, stack):
        async def close_tunnel():
            self.tunnel_closed = True

        stack.push_async_callback(close_tunnel)
        return "tunnel"

    async def mount_developer_image(self, provider):
        self.mounts += 1
        self.mounted_on = provider
        if self.mount_error:
            raise self.mount_error

    async def open_location(self, provider, stack):
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
    assert backend.mounted_on == "tunnel"  # like `mounter auto-mount --userspace`


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
    assert backend.tunnel_closed  # also closes anything the failed mount left open on it
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


class FakeMounter:
    IMAGE_TYPE = "Personalized"
    mounted = False
    opened = closed = 0

    def __init__(self, lockdown):
        self.lockdown = lockdown

    async def __aenter__(self):
        FakeMounter.opened += 1
        return self

    async def __aexit__(self, *_):
        FakeMounter.closed += 1

    async def is_image_mounted(self, image_type):
        return FakeMounter.mounted


@pytest.fixture
def fake_mounter(monkeypatch):
    image_mounter = pytest.importorskip("pymobiledevice3.services.mobile_image_mounter")
    calls = []

    async def fake_auto_mount(provider):
        calls.append(provider)
        if fake_auto_mount.error:
            raise fake_auto_mount.error

    fake_auto_mount.error = None
    FakeMounter.mounted, FakeMounter.opened, FakeMounter.closed = False, 0, 0
    monkeypatch.setattr(image_mounter, "PersonalizedImageMounter", FakeMounter)
    monkeypatch.setattr(image_mounter, "DeveloperDiskImageMounter", FakeMounter)
    monkeypatch.setattr(image_mounter, "uses_personalized_image", lambda provider: True)
    monkeypatch.setattr(image_mounter, "auto_mount", fake_auto_mount)
    return fake_auto_mount, calls


def run(coroutine):
    import asyncio

    return asyncio.run(coroutine)


def test_mount_skips_an_image_that_is_already_mounted(fake_mounter):
    from device import Pmd3Backend

    _, calls = fake_mounter
    FakeMounter.mounted = True
    run(Pmd3Backend().mount_developer_image("tunnel"))

    assert calls == []
    assert FakeMounter.opened == FakeMounter.closed == 1  # the check's connection is always closed


def test_mount_mounts_when_needed(fake_mounter):
    from device import Pmd3Backend

    _, calls = fake_mounter
    run(Pmd3Backend().mount_developer_image("tunnel"))

    assert calls == ["tunnel"]


def test_mount_treats_already_mounted_as_success(fake_mounter):
    from pymobiledevice3.exceptions import AlreadyMountedError

    from device import Pmd3Backend

    auto_mount, calls = fake_mounter
    auto_mount.error = AlreadyMountedError()
    run(Pmd3Backend().mount_developer_image("tunnel"))  # no exception

    assert calls == ["tunnel"]
