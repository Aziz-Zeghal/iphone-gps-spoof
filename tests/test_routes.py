"""The replies the page's JavaScript depends on, with a fake phone behind device.py."""

import importlib
import sys

import pytest
from test_device import IPHONE, FakeBackend, named_error

import device


@pytest.fixture
def make_client(monkeypatch):
    clients = []

    def make(backend=None):
        backend = backend or FakeBackend()
        monkeypatch.setattr(sys, "argv", ["main.py", "--no-browser"])
        monkeypatch.setattr(device, "Pmd3Backend", lambda: backend)
        sys.modules.pop("main", None)
        main = importlib.import_module("main")
        clients.append(main)
        return main.app.test_client(), backend

    yield make
    for main in clients:
        main.device.close()


def test_list_devices_groups_by_udid_and_connection(make_client):
    client, _ = make_client()
    assert client.get("/list_devices").get_json() == {IPHONE["Identifier"]: {"USB": [IPHONE]}}


def test_connect_reports_the_device(make_client):
    client, _ = make_client()
    reply = client.post("/connect_device", json={"udid": IPHONE["Identifier"]}).get_json()
    assert reply == {"rsd_data": "iPhone 15 (iOS 26.2)"}


def test_connect_asks_for_developer_mode(make_client):
    client, _ = make_client(FakeBackend(developer_mode=False))
    reply = client.post("/connect_device", json={"udid": IPHONE["Identifier"]}).get_json()
    assert reply == {"developer_mode_required": "True"}


def test_connect_error_is_a_readable_message(make_client):
    locked = named_error("PyMobileDevice3Exception", "{'Error': 'DeviceLocked'}")
    client, _ = make_client(FakeBackend(mount_error=locked))
    reply = client.post("/connect_device", json={"udid": IPHONE["Identifier"]}).get_json()
    assert "Unlock the iPhone" in reply["error"]


def test_set_and_stop_location_reply_with_the_texts_the_page_checks(make_client):
    client, backend = make_client()
    client.post("/connect_device", json={"udid": IPHONE["Identifier"]})
    client.post("/update_location", json={"lat": 48.8566, "lng": 2.3522})

    assert client.post("/set_location", json={}).get_data(as_text=True) == "Location set successfully"
    assert client.post("/stop_location", json={}).get_data(as_text=True) == "Location cleared successfully"
    assert backend.location.calls == [("set", 48.8566, 2.3522), ("clear",)]


def test_set_location_before_connecting_explains(make_client):
    client, _ = make_client()
    client.post("/update_location", json={"lat": 1, "lng": 2})
    reply = client.post("/set_location", json={})
    assert reply.status_code == 500 and "Connect the iPhone first" in reply.get_data(as_text=True)


def test_enable_developer_mode(make_client):
    client, backend = make_client()
    reply = client.post("/enable_developer_mode", json={"udid": IPHONE["Identifier"]}).get_json()
    assert reply == {"success": True, "udid": IPHONE["Identifier"]}
    assert backend.developer_mode_enabled == 1
