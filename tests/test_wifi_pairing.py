import builtins
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

import mooink_wifi_pairing as wifi


class FakeClient:
    def __init__(self):
        self.session = Mock()
        self.responses = []
    def register(self):
        print("private registration data")
        pin = input("PIN")
        if pin != "001234":
            return None
        return "cert", "test-key", "test-id"
    def authenticate(self, client_id, key):
        return SimpleNamespace(ok=True)
    def _get_endpoint(self, route):
        if route == "/ping":
            return SimpleNamespace(ok=True)
        if route == "/documents2?entry_type=all":
            return SimpleNamespace(json=lambda: {"count": 3, "entry_list": [
                {"entry_type":"folder", "entry_path":"Document"},
                {"entry_type":"folder", "entry_path":"sleep"},
                {"entry_type":"document", "entry_path":"Document/test.pdf"}]})
        raise AssertionError(route)


class PairingTests(unittest.TestCase):
    def test_gui_pin_callback_does_not_replace_builtin(self):
        original = builtins.input
        self.assertEqual(wifi.register_with_pin(FakeClient(), lambda: "001234"),
                         ("test-id", b"test-key"))
        self.assertIs(builtins.input, original)

    def test_cancel_and_invalid_pin(self):
        with self.assertRaises(wifi.PairingCancelled):
            wifi.register_with_pin(FakeClient(), lambda: None)
        with self.assertRaises(wifi.WifiPairingError):
            wifi.register_with_pin(FakeClient(), lambda: "secret text")

    def test_pair_saves_only_after_auth(self):
        client = FakeClient()
        with patch.object(wifi, "identity", return_value="device1"), \
             patch.object(wifi, "new_client", return_value=client), \
             patch.object(wifi, "save_credentials") as save:
            result = wifi.pair("192.168.1.140", lambda: "001234")
        self.assertEqual(result, "device1")
        save.assert_called_once_with("device1", ("test-id", b"test-key"))
        client.session.close.assert_called_once()

    def test_changed_reader_does_not_save(self):
        client = FakeClient()
        with patch.object(wifi, "identity", side_effect=["device1", "device2"]), \
             patch.object(wifi, "new_client", return_value=client), \
             patch.object(wifi, "save_credentials") as save:
            with self.assertRaises(wifi.WifiPairingError):
                wifi.pair("192.168.1.140", lambda: "001234")
        save.assert_not_called()

    def test_failed_auth_does_not_save(self):
        client = FakeClient()
        client.authenticate = Mock(side_effect=KeyError("nonce"))
        with patch.object(wifi, "identity", return_value="device1"), \
             patch.object(wifi, "new_client", return_value=client), \
             patch.object(wifi, "save_credentials") as save:
            with self.assertRaisesRegex(wifi.WifiPairingError, "認證失敗"):
                wifi.pair("192.168.1.140", lambda: "001234")
        save.assert_not_called()

    def test_separate_device_cache_lookup(self):
        client = FakeClient()
        with patch.object(wifi, "identity", return_value="device2"), \
             patch.object(wifi, "load_secret", return_value=None) as load, \
             patch.object(wifi, "new_client") as new:
            with self.assertRaises(wifi.WifiPairingError):
                wifi.connect_saved("192.168.1.140")
        load.assert_called_once_with("device2")
        new.assert_not_called()

    def test_readonly_folders(self):
        client = FakeClient()
        with patch.object(wifi, "connect_saved", return_value=(client,"device1")):
            result = wifi.inspect_folders("192.168.1.140")
        self.assertEqual(result["folders"],["Document","sleep"])
        self.assertEqual(result["sleep"],["sleep"])
        self.assertTrue(result["complete"])
        client.session.close.assert_called_once()

    def test_import_verifies_selected_credentials(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/"deviceid.dat").write_text("test-id",encoding="utf-8")
            (path/"privatekey.dat").write_bytes(b"test-key")
            with patch.object(wifi,"identity",return_value="device2"), \
                 patch.object(wifi,"new_client",return_value=client), \
                 patch.object(wifi,"save_credentials") as save:
                self.assertEqual(wifi.import_credentials("192.168.1.140",path),"device2")
                save.assert_called_once_with("device2",("test-id",b"test-key"))

    def test_incomplete_list_is_labeled(self):
        client = FakeClient()
        client._get_endpoint = Mock(return_value=SimpleNamespace(
            json=lambda: {"count":1301,"entry_list":[
                {"entry_type":"folder","entry_path":"Document"}]}))
        with patch.object(wifi,"connect_saved",return_value=(client,"device1")):
            result = wifi.inspect_folders("192.168.1.140")
        self.assertFalse(result["complete"])
        self.assertEqual(result["sleep"],[])
