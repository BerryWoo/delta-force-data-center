import json
import os
from pathlib import Path

_ENV_BASE_DIR = str(os.environ.get("DELTA_FORCE_DATA_CENTER_BASE_DIR", "") or "").strip()
if _ENV_BASE_DIR:
    BASE_DIR = Path(_ENV_BASE_DIR).resolve()
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
INFO_DIR = BASE_DIR / "info"
VAULT_DIR = DATA_DIR / "vault"
VAULT_DIR.mkdir(parents=True, exist_ok=True)
VAULT_STORE_DIR = VAULT_DIR / "store"
VAULT_STORE_DIR.mkdir(parents=True, exist_ok=True)
VAULT_RUNTIME_DIR = VAULT_DIR / "runtime"
VAULT_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

MAP_ID_JSON = INFO_DIR / "MapID.Json"
ROLE_JSON = INFO_DIR / "Role.Json"
COLLECTIBLE_OBJECT_JSON = INFO_DIR / "CollectibleObject.Json"


def _load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _normalize_role_info(raw: dict) -> tuple[dict[str, dict], dict[str, str]]:
    info_map: dict[str, dict] = {}
    name_map: dict[str, str] = {}
    for key, value in (raw or {}).items():
        role_id = str(key)
        if isinstance(value, dict):
            info = {
                "id": role_id,
                "name": str(value.get("name", "") or ""),
                "avatar": str(value.get("avatar", "") or ""),
                "avatar2": str(value.get("avatar2", "") or ""),
            }
        else:
            info = {
                "id": role_id,
                "name": str(value or ""),
                "avatar": "",
                "avatar2": "",
            }
        info_map[role_id] = info
        name_map[role_id] = info["name"]
    return info_map, name_map


MAP_ID_MAP: dict[str, str] = _load_json(MAP_ID_JSON)
ROLE_INFO_MAP, ROLE_MAP = _normalize_role_info(_load_json(ROLE_JSON))

WEGAME_HOME = "https://www.wegame.com.cn/helper/df/"
WEGAME_SCORE_DETAIL = "https://www.wegame.com.cn/helper/df/score-detail/"

WEGAME_API_BASE = "https://comm.ams.game.qq.com/ide"

LOGIN_WAIT_TIMEOUT = 120000
PAGE_LOAD_TIMEOUT = 30000
API_REQUEST_TIMEOUT = 30

BATTLE_LIST_PAGE_SIZE = 20
MAX_BATTLE_PAGES = 50

PLAINTEXT_DB_PATH = DATA_DIR / "delta_force_data_center.db"
LEGACY_DB_PATH = DATA_DIR / ("delta" + "_kpi.db")
DB_PATH = VAULT_RUNTIME_DIR / "delta_force_data_center.db"
COOKIE_PATH = DATA_DIR / "cookies.json"
RAW_DATA_DIR = DATA_DIR / "raw"
RAW_DATA_DIR.mkdir(exist_ok=True)
VAULT_DB_BLOB_PATH = VAULT_STORE_DIR / "db.bin"
VAULT_META_PATH = VAULT_STORE_DIR / "vault_meta.bin"
VAULT_COOKIE_PATH = VAULT_STORE_DIR / "cookies.bin"
VAULT_ENDPOINTS_PATH = VAULT_STORE_DIR / "endpoints.bin"
VAULT_REQUEST_BODIES_PATH = VAULT_STORE_DIR / "request_bodies.bin"
VAULT_DEBUG_REQUESTS_PATH = VAULT_STORE_DIR / "debug_requests.bin"
VAULT_TEAM_REPORT_INDEX_PATH = VAULT_STORE_DIR / "team_report_index.bin"
VAULT_RAW_DIR = VAULT_STORE_DIR / "raw"
VAULT_RAW_DIR.mkdir(parents=True, exist_ok=True)
COLLECTIBLE_OBJECT_OVERRIDE_JSON = DATA_DIR / "CollectibleObject.Json"
DEVICE_ID_PATH = DATA_DIR / "vault_device_id.txt"
