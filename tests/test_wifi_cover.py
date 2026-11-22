import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from mooink_wifi_cover import upload_cover, open_cover
from mooink_wifi_pairing import WifiHttpError, WifiPairingError


class FakeAPI:
    def __init__(self, existing=False, corrupt=False):
        self.exists=existing
        self.data=b"%PDF-old"
        self.corrupt=corrupt
        self.calls=[]
    def _get_endpoint(self, endpoint):
        self.calls.append(("GET",endpoint))
        if endpoint=="/resolve/entry/path/Document":
            return SimpleNamespace(json=lambda:{"entry_path":"Document","entry_type":"folder","entry_id":"root"})
        if endpoint=="/resolve/entry/path/Document%2Fmooink_display_10.pdf":
            if not self.exists:
                raise WifiHttpError(404)
            return SimpleNamespace(json=lambda:{"entry_path":"Document/mooink_display_10.pdf",
                                               "entry_type":"document","entry_id":"doc-1"})
        if endpoint=="/documents/doc-1/file":
            return SimpleNamespace(content=self.data)
        raise AssertionError(endpoint)
    def _post_endpoint(self, endpoint, data):
        self.calls.append(("POST",endpoint,data))
        self.exists=True
        return SimpleNamespace(json=lambda:{"document_id":"doc-1"})
    def _put_endpoint(self, endpoint, data=None, files=None):
        self.calls.append(("PUT",endpoint,data))
        if endpoint=="/documents/doc-1/file":
            self.data=files["file"][1].read() if not self.corrupt else b"bad"
        return SimpleNamespace(status_code=204)


class CoverTests(unittest.TestCase):
    def test_create_readback_and_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"mooink_display_10.pdf"
            path.write_bytes(b"%PDF-test")
            client=FakeAPI()
            self.assertEqual(upload_cover(client,path),"doc-1")
            self.assertEqual(client.data,path.read_bytes())
            self.assertEqual(open_cover(client,"doc-1"),204)
            self.assertEqual(client.calls[-1],("PUT","/viewer/controls/open2",{"document_id":"doc-1","page":1}))
            self.assertFalse(any(c[0]=="DELETE" for c in client.calls))

    def test_existing_identical_pdf_not_uploaded_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"mooink_display_10.pdf"
            path.write_bytes(b"%PDF-old")
            client=FakeAPI(existing=True)
            upload_cover(client,path)
            self.assertTrue(all(c[0]=="GET" for c in client.calls))

    def test_bad_readback_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"mooink_display_10.pdf"
            path.write_bytes(b"%PDF-test")
            with self.assertRaisesRegex(WifiPairingError,"SHA-256"):
                upload_cover(FakeAPI(corrupt=True),path)

    def test_other_filename_never_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"other.pdf"
            path.write_bytes(b"%PDF-test")
            client=FakeAPI()
            with self.assertRaises(ValueError):
                upload_cover(client,path)
            self.assertEqual(client.calls,[])
