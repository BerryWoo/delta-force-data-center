import base64
import ctypes
import hashlib
import hmac
import json
import os
import shutil
from pathlib import Path

from . import config


_DPAPI_AVAILABLE = os.name == "nt"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(raw: str) -> bytes:
    return base64.b64decode(raw.encode("ascii"))


def _device_fallback_key() -> bytes:
    device_id = get_or_create_device_id()
    return hashlib.sha256(
        f"delta-force-local-vault|{device_id}".encode("utf-8")
    ).digest()


def get_or_create_device_id() -> str:
    path = config.DEVICE_ID_PATH
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    device_id = hashlib.sha256(os.urandom(32)).hexdigest()
    path.write_text(device_id, encoding="utf-8")
    return device_id


def _dpapi_protect(raw: bytes) -> bytes:
    if not _DPAPI_AVAILABLE:
        return raw
    buffer = ctypes.create_string_buffer(raw)
    in_blob = _DATA_BLOB(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "DFDC",
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(raw: bytes) -> bytes:
    if not _DPAPI_AVAILABLE:
        return raw
    buffer = ctypes.create_string_buffer(raw)
    in_blob = _DATA_BLOB(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _stream_xor(raw: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < len(raw):
        block = hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(a ^ b for a, b in zip(raw, out[: len(raw)]))


def _seal_with_key(raw: bytes, key: bytes) -> bytes:
    nonce = os.urandom(16)
    body = _stream_xor(raw, key, nonce)
    mac = hmac.new(key, nonce + body, hashlib.sha256).digest()
    payload = {"v": 1, "nonce": _b64e(nonce), "body": _b64e(body), "mac": _b64e(mac)}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _open_with_key(raw: bytes, key: bytes) -> bytes:
    payload = json.loads(raw.decode("utf-8"))
    nonce = _b64d(str(payload.get("nonce", "")))
    body = _b64d(str(payload.get("body", "")))
    mac = _b64d(str(payload.get("mac", "")))
    expected = hmac.new(key, nonce + body, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("vault mac mismatch")
    return _stream_xor(body, key, nonce)


def _write_dpapi_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(_dpapi_protect(raw))


def _read_dpapi_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = _dpapi_unprotect(path.read_bytes())
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _load_vault_meta() -> dict:
    return _read_dpapi_json(config.VAULT_META_PATH)


def _save_vault_meta(data: dict) -> None:
    _write_dpapi_json(config.VAULT_META_PATH, data)


def _current_vault_fingerprint() -> str:
    if config.VAULT_DB_BLOB_PATH.exists():
        try:
            return hashlib.sha256(config.VAULT_DB_BLOB_PATH.read_bytes()).hexdigest()[:32]
        except Exception:
            return ""
    return ""


def _looks_like_sqlite(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size < 16:
            return False
        with path.open("rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except Exception:
        return False


def _app_key() -> bytes:
    meta = _load_vault_meta()
    material = str(meta.get("material", "") or "").strip()
    if not material:
        return _device_fallback_key()
    seed = "|".join(
        [
            str(meta.get("user_id", "") or ""),
            str(meta.get("username", "") or ""),
            str(meta.get("device_id", "") or ""),
            str(meta.get("material_id", "") or ""),
            material,
        ]
    ).encode("utf-8")
    return hashlib.sha256(seed).digest()


def _candidate_keys() -> list[bytes]:
    keys: list[bytes] = []
    current = _app_key()
    fallback = _device_fallback_key()
    for key in [current, fallback]:
        if key not in keys:
            keys.append(key)
    return keys


def _open_with_available_key(raw: bytes) -> bytes:
    last_error: Exception | None = None
    for key in _candidate_keys():
        try:
            return _open_with_key(raw, key)
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise ValueError("vault key unavailable")


def _rewrite_protected_file_with_current_key(path: Path) -> None:
    if not path.exists():
        return
    raw = _open_with_available_key(path.read_bytes())
    path.write_bytes(_seal_with_key(raw, _app_key()))


def clear_local_vault_meta() -> None:
    if config.VAULT_META_PATH.exists():
        config.VAULT_META_PATH.unlink()


def _path_for_name(name: str) -> Path:
    safe = name.replace("\\", "/").strip("/").replace("/", "__")
    return config.VAULT_STORE_DIR / f"{safe}.bin"


def write_protected_json(name: str, data: dict | list) -> None:
    path = _path_for_name(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(_seal_with_key(raw, _app_key()))


def read_protected_json(name: str, default):
    path = _path_for_name(name)
    if not path.exists():
        return default
    try:
        raw = _open_with_available_key(path.read_bytes())
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return default


def protected_exists(name: str) -> bool:
    return _path_for_name(name).exists()


def delete_protected(name: str) -> None:
    path = _path_for_name(name)
    if path.exists():
        path.unlink()


def save_cookies(data: dict[str, str]) -> None:
    write_protected_json("cookies", data)
    if config.COOKIE_PATH.exists():
        config.COOKIE_PATH.unlink()


def load_cookies() -> dict[str, str]:
    if config.COOKIE_PATH.exists() and not protected_exists("cookies"):
        try:
            save_cookies(json.loads(config.COOKIE_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return read_protected_json("cookies", {})


def has_cookies() -> bool:
    return protected_exists("cookies") or config.COOKIE_PATH.exists()


def clear_cookies() -> None:
    delete_protected("cookies")
    if config.COOKIE_PATH.exists():
        config.COOKIE_PATH.unlink()


def save_endpoints(data: dict) -> None:
    write_protected_json("endpoints", data)
    legacy = config.DATA_DIR / "endpoints.json"
    if legacy.exists():
        legacy.unlink()


def save_request_bodies(data: dict) -> None:
    write_protected_json("request_bodies", data)
    for legacy in [config.DATA_DIR / "request_bodies.json", config.DATA_DIR / "debug_requests.json"]:
        if legacy.exists():
            legacy.unlink()


def load_request_bodies() -> dict:
    legacy = config.DATA_DIR / "request_bodies.json"
    if legacy.exists() and not protected_exists("request_bodies"):
        try:
            save_request_bodies(json.loads(legacy.read_text(encoding="utf-8")))
        except Exception:
            pass
    return read_protected_json("request_bodies", {})


def save_debug_requests(data: dict | list) -> None:
    write_protected_json("debug_requests", data)
    legacy = config.DATA_DIR / "debug_requests.json"
    if legacy.exists():
        legacy.unlink()


def load_debug_requests():
    legacy = config.DATA_DIR / "debug_requests.json"
    if legacy.exists() and not protected_exists("debug_requests"):
        try:
            save_debug_requests(json.loads(legacy.read_text(encoding="utf-8")))
        except Exception:
            pass
    return read_protected_json("debug_requests", [])


def save_raw_json(name: str, data) -> None:
    write_protected_json(f"raw/{name}", data)
    legacy = config.RAW_DATA_DIR / name
    if legacy.exists():
        legacy.unlink()


def load_raw_json(name: str, default=None):
    legacy = config.RAW_DATA_DIR / name
    if legacy.exists() and not protected_exists(f"raw/{name}"):
        try:
            save_raw_json(name, json.loads(legacy.read_text(encoding="utf-8")))
        except Exception:
            pass
    if default is None:
        default = {}
    return read_protected_json(f"raw/{name}", default)


def save_team_report_index(data: dict) -> None:
    write_protected_json("team_report_index", data)


def load_team_report_index() -> dict:
    legacy = config.DATA_DIR / "reports" / "index.json"
    if legacy.exists() and not protected_exists("team_report_index"):
        try:
            save_team_report_index(json.loads(legacy.read_text(encoding="utf-8-sig")))
            legacy.unlink()
        except Exception:
            pass
    return read_protected_json("team_report_index", {})


def clear_sensitive_runtime_files() -> None:
    clear_cookies()
    for name in ["endpoints", "request_bodies", "debug_requests"]:
        delete_protected(name)
    if config.RAW_DATA_DIR.exists():
        shutil.rmtree(config.RAW_DATA_DIR, ignore_errors=True)
    config.RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for legacy in [
        config.DATA_DIR / "endpoints.json",
        config.DATA_DIR / "request_bodies.json",
        config.DATA_DIR / "debug_requests.json",
        config.DATA_DIR / "role_info.json",
    ]:
        if legacy.exists():
            legacy.unlink()


def database_exists() -> bool:
    return (
        config.VAULT_DB_BLOB_PATH.exists()
        or config.DB_PATH.exists()
        or config.PLAINTEXT_DB_PATH.exists()
        or config.LEGACY_DB_PATH.exists()
    )


def ensure_runtime_database() -> Path:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _looks_like_sqlite(config.DB_PATH):
        return config.DB_PATH
    if config.DB_PATH.exists():
        try:
            config.DB_PATH.unlink()
        except Exception:
            pass
    if config.VAULT_DB_BLOB_PATH.exists():
        try:
            raw = _open_with_available_key(config.VAULT_DB_BLOB_PATH.read_bytes())
            config.DB_PATH.write_bytes(raw)
            if _looks_like_sqlite(config.DB_PATH):
                return config.DB_PATH
        except Exception:
            pass
        try:
            config.DB_PATH.unlink()
        except Exception:
            pass
    source = None
    if _looks_like_sqlite(config.PLAINTEXT_DB_PATH):
        source = config.PLAINTEXT_DB_PATH
    elif _looks_like_sqlite(config.LEGACY_DB_PATH):
        source = config.LEGACY_DB_PATH
    if source is not None:
        shutil.copy2(source, config.DB_PATH)
        persist_runtime_database(remove_runtime=False)
        try:
            source.unlink()
        except Exception:
            pass
    return config.DB_PATH


def persist_runtime_database(remove_runtime: bool = False) -> None:
    if not _looks_like_sqlite(config.DB_PATH):
        return
    config.VAULT_DB_BLOB_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.VAULT_DB_BLOB_PATH.write_bytes(
        _seal_with_key(config.DB_PATH.read_bytes(), _app_key())
    )
    for legacy in [config.PLAINTEXT_DB_PATH, config.LEGACY_DB_PATH]:
        if legacy.exists():
            try:
                legacy.unlink()
            except Exception:
                pass
    if remove_runtime:
        try:
            config.DB_PATH.unlink()
        except Exception:
            pass
