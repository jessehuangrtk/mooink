"""Wi-Fi PDF cover delivery for original mooInk Pro.

Uses only the authenticated, documented-by-client Document API. Never writes
the MTP sleep directory and never deletes documents. The two fixed file names
are reserved for this watcher; a user enabling delivery accepts replacement
of those exact names. A verified upload and HTTP open are not physical screen
or sleep-cover verification.
"""
from pathlib import Path
import hashlib
import re
from urllib.parse import quote_plus

from mooink_wifi_pairing import WifiPairingError, WifiHttpError, identity
from mooink_wifi_pairing import connect_saved

NAMES = frozenset(("mooink_display_10.pdf", "mooink_display_13.pdf"))


def _resolve(client, path, missing=False):
    try:
        response = client._get_endpoint("/resolve/entry/path/" + quote_plus(path))
    except WifiHttpError as exc:
        if missing and exc.status_code == 404:
            return None
        raise
    obj = response.json()
    if not isinstance(obj, dict) or obj.get("entry_path") != path:
        raise WifiPairingError("API 回傳路徑不符，停止寫入或顯示：" + path)
    _entry_id(obj.get("entry_id"))
    return obj


def _entry_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise WifiPairingError("裝置回傳無效的文件識別碼。")
    return value


def check_identity(ip, device):
    if identity(ip) != device:
        raise WifiPairingError("IP 對應的 mooInk 已改變，停止本次更新。")


def upload_cover(client, pdf, log=lambda msg: None):
    """Create/replace one reserved PDF and verify the exact downloaded bytes."""
    pdf = Path(pdf)
    if pdf.name not in NAMES:
        raise ValueError("僅允許 Watch Tool 專用 PDF 檔名")
    data = pdf.read_bytes()
    if not data.startswith(b"%PDF-"):
        raise ValueError("本機檔案不是 PDF")
    remote = "Document/" + pdf.name
    doc = _resolve(client, remote, missing=True)
    if doc is None:
        folder = _resolve(client, "Document")
        if folder.get("entry_type") != "folder":
            raise WifiPairingError("Document 不是資料夾，停止上傳")
        response = client._post_endpoint("/documents2", data={
            "file_name": pdf.name, "parent_folder_id": folder["entry_id"],
            "document_source": "",
        })
        doc_id = _entry_id(response.json().get("document_id"))
        log("已建立專用文件：" + remote)
    else:
        if doc.get("entry_type") != "document":
            raise WifiPairingError("專用檔名已被非文件物件占用，停止上傳")
        doc_id = _entry_id(doc["entry_id"])
        log("使用既有專用文件：" + remote)

    endpoint = "/documents/" + doc_id + "/file"
    # A prior partial run may have uploaded successfully. Avoid unnecessary
    # writes by comparing the actual bytes first if this document existed.
    already_same = False
    if doc is not None:
        try:
            already_same = hashlib.sha256(client._get_endpoint(endpoint).content).digest() == hashlib.sha256(data).digest()
        except WifiHttpError as exc:
            if exc.status_code != 404:
                raise
    if not already_same:
        with pdf.open("rb") as stream:
            response = client._put_endpoint(endpoint, files={
                "file": (quote_plus(pdf.name), stream, "application/pdf")
            })
        log("PDF 上傳 HTTP %s" % response.status_code)
    actual = client._get_endpoint(endpoint).content
    if hashlib.sha256(actual).digest() != hashlib.sha256(data).digest():
        raise WifiPairingError("PDF 回讀 SHA-256 不一致，不開啟、不喚醒。")
    confirmed = _resolve(client, remote)
    if confirmed.get("entry_type") != "document" or confirmed["entry_id"] != doc_id:
        raise WifiPairingError("PDF 上傳後的路徑或文件識別碼不符。")
    log("PDF 回讀 SHA-256 驗證成功：" + remote)
    return doc_id


def open_cover(client, doc_id):
    doc_id = _entry_id(doc_id)
    response = client._put_endpoint("/viewer/controls/open2",
                                   data={"document_id": doc_id, "page": 1})
    return response.status_code
