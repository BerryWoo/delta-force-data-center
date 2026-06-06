import json
import re
import sqlite3
import tempfile
import zipfile
from io import BytesIO
from datetime import datetime
from pathlib import Path

from . import config
from . import vault_store


BATTLE_TAG_RULES = [
    {
        "tag_name": "大杀四方",
        "rule_text": "本局玩家击杀大于3名（不含3名）并撤离成功",
        "dimension": "正面评价",
        "note": "",
        "scope": "both",
        "sort_order": 10,
    },
    {
        "tag_name": "百万撤离",
        "rule_text": "带出价值大于100万，并撤离成功",
        "dimension": "正面评价",
        "note": "",
        "scope": "both",
        "sort_order": 20,
    },
    {
        "tag_name": "盆满钵满",
        "rule_text": "带出价值大于300万，并撤离成功",
        "dimension": "正面评价",
        "note": "",
        "scope": "both",
        "sort_order": 30,
    },
    {
        "tag_name": "猛攻哥",
        "rule_text": "带入装备价值大于200万",
        "dimension": "正面评价",
        "note": "",
        "scope": "both",
        "sort_order": 40,
    },
    {
        "tag_name": "落地成盒",
        "rule_text": "对局时长小于2分钟且撤离失败",
        "dimension": "负面评价",
        "note": "",
        "scope": "both",
        "sort_order": 50,
    },
    {
        "tag_name": "砖厂老板",
        "rule_text": "对局详情中曼德尔砖状态为“有”",
        "dimension": "正面评价",
        "note": "",
        "scope": "both",
        "sort_order": 60,
    },
    {
        "tag_name": "以小博大",
        "rule_text": "地图非普通模式中，带入装备价值小于30万，带出价值大于100万且撤离成功",
        "dimension": "正面评价",
        "note": "",
        "scope": "both",
        "sort_order": 70,
    },
    {
        "tag_name": "再吃亿点",
        "rule_text": "当局带出的红色品质高价值物品大于1个（不含1个）",
        "dimension": "正面评价",
        "note": "",
        "scope": "both",
        "sort_order": 80,
    },
    {
        "tag_name": "打白工",
        "rule_text": "带入装备价值大于200万，撤离成功且盈亏数据小于50万",
        "dimension": "中性评价",
        "note": "",
        "scope": "both",
        "sort_order": 90,
    },
    {
        "tag_name": "连跪",
        "rule_text": "连续撤离失败大于等于5局",
        "dimension": "负面评价",
        "note": "只适用于战绩列表页面，不适用于对局详情",
        "scope": "record_list",
        "sort_order": 100,
    },
    {
        "tag_name": "手感火热",
        "rule_text": "连续撤离成功大于等于3局",
        "dimension": "正面评价",
        "note": "只适用于战绩列表页面，不适用于对局详情",
        "scope": "record_list",
        "sort_order": 110,
    },
]

COLLECTIBLE_GRADE_TEXT = {
    6: "红",
    5: "传说",
    4: "史诗",
    3: "稀有",
    2: "普通",
    1: "白",
    0: "未知",
}

COLLECTIBLE_CATEGORY_NAME = {
    "operator": "干员",
    "gun": "枪械",
    "dagger": "近战",
    "vehicle": "载具",
    "pendant": "挂饰",
}

COLLECTIBLE_RARITY_TEXT = {
    "0": "--",
    "1": "优品",
    "4": "极品",
}

PLAYER_NAME_ALLOWED_RE = re.compile(
    r"[^A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff\s"
    r"._\-·,，.。!！?？:：;；、/\\|+*=#@~&%￥$^"
    r"()（）\[\]【】{}<>《》'\"`]"
)


def sanitize_player_name(value: str | None) -> str:
    text = str(value or "")
    text = PLAYER_NAME_ALLOWED_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _collectible_finish_grade(wear_raw: str | int | float | None) -> str:
    try:
        wear_value = float(str(wear_raw or "0").strip()) / 1_000_000_000
    except Exception:
        return ""
    if wear_value <= 0:
        return ""
    if wear_value <= 0.4:
        return "S"
    if wear_value <= 1.25:
        return "A"
    if wear_value <= 2.5:
        return "B"
    return "C"


class Database:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or config.DB_PATH
        self.conn: sqlite3.Connection | None = None

    def connect(self):
        if self.db_path == config.DB_PATH:
            vault_store.ensure_runtime_database()
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        try:
            self._create_tables()
        except sqlite3.OperationalError as exc:
            if "readonly" not in str(exc).lower():
                raise

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
        if self.db_path == config.DB_PATH:
            vault_store.persist_runtime_database(remove_runtime=True)

    def _create_tables(self):
        for legacy in ["BattleDetail_Teammate"]:
            self.conn.execute(f"DROP TABLE IF EXISTS [{legacy}]")
        try:
            cols = [
                r[1]
                for r in self.conn.execute("PRAGMA table_info(BattleDetail)").fetchall()
            ]
            if cols and "player_id" not in cols:
                self.conn.execute("DROP TABLE IF EXISTS BattleDetail")
        except Exception:
            pass
        try:
            item_cols = [
                r[1]
                for r in self.conn.execute(
                    "PRAGMA table_info(battles_items)"
                ).fetchall()
            ]
            if item_cols and "player_id" not in item_cols:
                self.conn.execute("ALTER TABLE battles_items ADD COLUMN player_id TEXT")
        except Exception:
            pass
        self.conn.commit()

        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS Account (
                player_id   TEXT PRIMARY KEY,
                openid      TEXT,
                player_name TEXT,
                player_icon TEXT,
                level       INTEGER,
                area        INTEGER DEFAULT 36,
                account_type INTEGER DEFAULT 2,
                is_active   INTEGER DEFAULT 1,
                last_login  TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS MapID (
                map_id   INTEGER PRIMARY KEY,
                map_name TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS Role (
                role_id   INTEGER PRIMARY KEY,
                role_name TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS Record (
                room_id                          TEXT PRIMARY KEY,
                map_id                           INTEGER,
                event_time                       TEXT,
                start_time                       TEXT,
                duration_s                       INTEGER,
                game_result                      INTEGER,
                game_rule                        INTEGER,
                role_id                          INTEGER,
                kill_cnt                         INTEGER DEFAULT 0,
                kill_player                      INTEGER DEFAULT 0,
                assist_cnt                       INTEGER DEFAULT 0,
                rescue                           INTEGER DEFAULT 0,
                collection_price                 INTEGER DEFAULT 0,
                gained_price                     INTEGER DEFAULT 0,
                profit_loss                      INTEGER DEFAULT 0,
                original_equipment_price         INTEGER DEFAULT 0,
                is_rank_match                    INTEGER DEFAULT 0,
                is_leave                         INTEGER DEFAULT 0,
                has_blue_box                     INTEGER DEFAULT 0,
                team_id                          INTEGER,
                queue                            TEXT,
                area                             INTEGER,
                player_id                        TEXT,
                openid                           TEXT,
                player_name                      TEXT,
                player_icon                      TEXT,
                fetched_at                       TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (map_id)  REFERENCES MapID(map_id),
                FOREIGN KEY (role_id) REFERENCES Role(role_id)
            );

            CREATE TABLE IF NOT EXISTS battles_items (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id    TEXT    NOT NULL,
                item_id    TEXT,
                item_name  TEXT,
                pic        TEXT,
                grade      INTEGER,
                num        INTEGER DEFAULT 1,
                price      INTEGER DEFAULT 0,
                player_id  TEXT,
                FOREIGN KEY (room_id) REFERENCES Record(room_id)
            );

            CREATE TABLE IF NOT EXISTS battle_details (
                room_id    TEXT PRIMARY KEY,
                raw_data   TEXT,
                fetched_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (room_id) REFERENCES Record(room_id)
            );

            CREATE TABLE IF NOT EXISTS BattleDetail (
                id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id                    TEXT    NOT NULL,
                player_id                  TEXT,
                vopenid                    TEXT,
                player_name                TEXT,
                player_icon                TEXT,
                armed_force_id             INTEGER,
                map_id                     INTEGER,
                event_time                 TEXT,
                start_time                 TEXT,
                duration_s                 INTEGER,
                game_result                INTEGER,
                game_rule                  INTEGER,
                kill_cnt                   INTEGER DEFAULT 0,
                kill_player                INTEGER DEFAULT 0,
                assist_cnt                 INTEGER DEFAULT 0,
                rescue                     INTEGER DEFAULT 0,
                collection_price           INTEGER DEFAULT 0,
                gained_price               INTEGER DEFAULT 0,
                profit_loss                INTEGER DEFAULT 0,
                original_equipment_price   INTEGER DEFAULT 0,
                is_rank_match              INTEGER DEFAULT 0,
                is_leave                   INTEGER DEFAULT 0,
                has_blue_box               INTEGER DEFAULT 0,
                team_id                    INTEGER,
                area                       INTEGER,
                is_self                    INTEGER DEFAULT 0,
                fetched_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (room_id) REFERENCES Record(room_id),
                FOREIGN KEY (map_id)  REFERENCES MapID(map_id)
            );

            CREATE TABLE IF NOT EXISTS BattleTagRule (
                tag_name    TEXT PRIMARY KEY,
                rule_text   TEXT NOT NULL,
                dimension   TEXT NOT NULL,
                note        TEXT DEFAULT '',
                scope       TEXT DEFAULT 'both',
                sort_order  INTEGER DEFAULT 0
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_bdetail_room_player ON BattleDetail(room_id, player_id);

            CREATE TABLE IF NOT EXISTS battle_players (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id      TEXT    NOT NULL,
                player_name  TEXT,
                player_id    TEXT,
                team_id      INTEGER,
                kill_count   INTEGER DEFAULT 0,
                death_count  INTEGER DEFAULT 0,
                assist_count INTEGER DEFAULT 0,
                damage       INTEGER DEFAULT 0,
                survive      INTEGER DEFAULT 0,
                role_id      INTEGER,
                raw_data     TEXT,
                FOREIGN KEY (room_id) REFERENCES Record(room_id)
            );

            CREATE TABLE IF NOT EXISTS collection (
                inventory_key            TEXT PRIMARY KEY,
                player_id                TEXT NOT NULL,
                item_id                  TEXT NOT NULL,
                source_group             TEXT NOT NULL,
                item_name                TEXT,
                category_code            TEXT,
                category_name            TEXT,
                grade                    INTEGER DEFAULT 0,
                grade_text               TEXT,
                num                      INTEGER DEFAULT 1,
                quality_raw              INTEGER DEFAULT 0,
                is_collectible_gun       INTEGER DEFAULT 0,
                collectible_rarity_code  TEXT,
                collectible_rarity_text  TEXT,
                wear_raw                 TEXT,
                finish_grade             TEXT,
                unique_no                TEXT,
                pic                      TEXT,
                pre_pic                  TEXT,
                is_archive               INTEGER DEFAULT 0,
                tag_text                 TEXT,
                fetched_at               TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS RoleInfo (
                player_id         TEXT PRIMARY KEY,
                openid            TEXT,
                area              INTEGER DEFAULT 36,
                name              TEXT,
                level             INTEGER DEFAULT 0,
                icon              TEXT,
                tdm_level         INTEGER DEFAULT 0,
                tdm_exp           INTEGER DEFAULT 0,
                propcapital       INTEGER DEFAULT 0,
                hafcoinnum        INTEGER DEFAULT 0,
                total_price       INTEGER DEFAULT 0,
                noncurrent_asset  INTEGER DEFAULT 0,
                current_asset     INTEGER DEFAULT 0,
                fetched_at        TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_record_event_time ON Record(event_time);
            CREATE INDEX IF NOT EXISTS idx_record_map_id     ON Record(map_id);
            CREATE INDEX IF NOT EXISTS idx_record_role_id    ON Record(role_id);
            CREATE INDEX IF NOT EXISTS idx_items_room_id     ON battles_items(room_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_items_unique ON battles_items(room_id, item_id, player_id);
            CREATE INDEX IF NOT EXISTS idx_bdetail_room ON BattleDetail(room_id);
            CREATE INDEX IF NOT EXISTS idx_collection_player ON collection(player_id);
            CREATE INDEX IF NOT EXISTS idx_collection_category ON collection(category_code);
            CREATE INDEX IF NOT EXISTS idx_collection_grade ON collection(grade);
        """)
        self._seed_battle_tag_rules()

    def _seed_battle_tag_rules(self):
        self.conn.executemany(
            """INSERT OR REPLACE INTO BattleTagRule
               (tag_name, rule_text, dimension, note, scope, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    rule["tag_name"],
                    rule["rule_text"],
                    rule["dimension"],
                    rule["note"],
                    rule["scope"],
                    rule["sort_order"],
                )
                for rule in BATTLE_TAG_RULES
            ],
        )
        self.conn.commit()

    def get_battle_tag_rules(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT tag_name, rule_text, dimension, note, scope, sort_order
               FROM BattleTagRule
               ORDER BY sort_order ASC, tag_name ASC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Account ----

    def upsert_account(
        self,
        player_id: str,
        openid: str,
        player_name: str = "",
        player_icon: str = "",
        level: int = 0,
        area: int = 36,
        account_type: int = 2,
    ):
        player_name = sanitize_player_name(player_name)
        self.conn.execute(
            """INSERT INTO Account (player_id, openid, player_name, player_icon, level, area, account_type, is_active, last_login)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(player_id) DO UPDATE SET
                   openid=excluded.openid,
                   player_name=excluded.player_name,
                   player_icon=excluded.player_icon,
                   level=excluded.level,
                   area=excluded.area,
                   account_type=excluded.account_type,
                   is_active=1,
                   last_login=excluded.last_login""",
            (
                player_id,
                openid,
                player_name,
                player_icon,
                level,
                area,
                account_type,
                datetime.now().isoformat(),
            ),
        )
        self.conn.execute(
            "UPDATE Account SET is_active = 0 WHERE player_id != ?", (player_id,)
        )
        self.conn.commit()

    def get_active_account(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM Account WHERE is_active = 1").fetchone()
        return dict(row) if row else None

    def get_all_accounts(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM Account ORDER BY last_login DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_account(self, player_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM Account WHERE player_id = ?", (player_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_active_account(self, player_id: str):
        self.conn.execute("UPDATE Account SET is_active = 0")
        self.conn.execute(
            "UPDATE Account SET is_active = 1 WHERE player_id = ?", (player_id,)
        )
        self.conn.commit()

    def delete_account(self, player_id: str) -> dict | None:
        account = self.get_account(player_id)
        if not account:
            return None
        was_active = bool(account.get("is_active"))
        self.conn.execute("DELETE FROM Account WHERE player_id = ?", (player_id,))
        if was_active:
            next_row = self.conn.execute(
                "SELECT player_id FROM Account ORDER BY last_login DESC LIMIT 1"
            ).fetchone()
            self.conn.execute("UPDATE Account SET is_active = 0")
            if next_row and next_row["player_id"]:
                self.conn.execute(
                    "UPDATE Account SET is_active = 1 WHERE player_id = ?",
                    (next_row["player_id"],),
                )
        self.conn.commit()
        return account

    def get_active_player_id(self) -> str | None:
        row = self.conn.execute(
            "SELECT player_id FROM Account WHERE is_active = 1"
        ).fetchone()
        return row["player_id"] if row else None

    # ---- MapID ----

    def load_mapid_from_json(self):
        for k, v in config.MAP_ID_MAP.items():
            self.conn.execute(
                "INSERT OR REPLACE INTO MapID (map_id, map_name) VALUES (?, ?)",
                (int(k), v),
            )
        self.conn.commit()

    # ---- Role ----

    def load_role_from_json(self):
        for k, v in config.ROLE_MAP.items():
            role_name = str(v.get("name", "") if isinstance(v, dict) else v)
            self.conn.execute(
                "INSERT OR REPLACE INTO Role (role_id, role_name) VALUES (?, ?)",
                (int(k), role_name),
            )
        self.conn.commit()

    # ---- collection ----

    def _get_collectible_object_json_path(self) -> Path:
        override_path = getattr(config, "COLLECTIBLE_OBJECT_OVERRIDE_JSON", None)
        if isinstance(override_path, Path) and override_path.exists():
            return override_path
        return config.COLLECTIBLE_OBJECT_JSON

    def _load_collectible_catalog_lookup(self) -> dict[str, dict]:
        path = self._get_collectible_object_json_path()
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        data = raw
        for key in ("jData", "data", "data"):
            if isinstance(data, dict) and key in data:
                data = data[key]
        items = data.get("list", []) if isinstance(data, dict) else []

        lookup: dict[str, dict] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("objectID", "") or "").strip()
            if not item_id:
                continue
            details = item.get("assetsDetail") or {}
            if not isinstance(details, dict):
                details = {}
            lookup[item_id] = {
                "item_name": str(item.get("objectName", "") or "").strip(),
                "category_code": str(item.get("secondClass", "") or "").strip(),
                "category_name": str(item.get("secondClassCN", "") or "").strip(),
                "grade": _safe_int(item.get("grade"), 0),
                "pic": str(item.get("pic", "") or "").strip(),
                "pre_pic": str(item.get("prePic", "") or "").strip(),
                "is_archive": 1 if details.get("isArchive") else 0,
                "tag_text": str(details.get("tags", "") or "").strip(),
            }
        return lookup

    def replace_collection_for_player(self, player_id: str, payload: dict) -> dict:
        player_id = str(player_id or "").strip()
        if not player_id:
            raise ValueError("缺少 player_id")

        lookup = self._load_collectible_catalog_lookup()
        now = datetime.now().isoformat()
        self.conn.execute("DELETE FROM collection WHERE player_id = ?", (player_id,))

        def _insert_entries(entries: list[dict], source_group: str):
            for raw_item in entries or []:
                if not isinstance(raw_item, dict):
                    continue
                item_id = str(raw_item.get("id", "") or "").strip()
                if not item_id:
                    continue
                meta = lookup.get(item_id, {})
                unique_no = str(raw_item.get("UniqueNo", "") or "").strip()
                if unique_no in {"0", "None", "null"}:
                    unique_no = ""
                category_code = str(
                    meta.get("category_code")
                    or raw_item.get("secondClass")
                    or ("operator" if source_group == "operator" else "")
                ).strip()
                category_name = str(
                    meta.get("category_name")
                    or COLLECTIBLE_CATEGORY_NAME.get(category_code, "")
                ).strip()
                grade = _safe_int(
                    meta.get("grade"), _safe_int(raw_item.get("quality"), 0)
                )
                is_collectible_gun = (
                    1 if _safe_int(raw_item.get("IsCollectibles"), 0) == 1 else 0
                )
                rarity_code = str(raw_item.get("rarity", "") or "0").strip() or "0"
                wear_raw = str(raw_item.get("wear", "") or "").strip()
                if wear_raw in {"None", "null"}:
                    wear_raw = ""
                inventory_key = (
                    f"{player_id}:{source_group}:{item_id}:{unique_no or 'base'}"
                )
                self.conn.execute(
                    """INSERT OR REPLACE INTO collection
                       (inventory_key, player_id, item_id, source_group, item_name,
                        category_code, category_name, grade, grade_text, num,
                        quality_raw, is_collectible_gun, collectible_rarity_code,
                        collectible_rarity_text, wear_raw, finish_grade, unique_no,
                        pic, pre_pic, is_archive, tag_text, fetched_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        inventory_key,
                        player_id,
                        item_id,
                        source_group,
                        str(meta.get("item_name") or raw_item.get("name") or "").strip(),
                        category_code,
                        category_name,
                        grade,
                        COLLECTIBLE_GRADE_TEXT.get(grade, "未知"),
                        max(_safe_int(raw_item.get("num"), 1), 1),
                        _safe_int(raw_item.get("quality"), 0),
                        is_collectible_gun,
                        rarity_code,
                        COLLECTIBLE_RARITY_TEXT.get(rarity_code, "--"),
                        wear_raw,
                        _collectible_finish_grade(wear_raw),
                        unique_no,
                        str(meta.get("pic", "") or "").strip(),
                        str(meta.get("pre_pic", "") or "").strip(),
                        _safe_int(meta.get("is_archive"), 0),
                        str(meta.get("tag_text", "") or "").strip(),
                        now,
                    ),
                )

        _insert_entries(payload.get("collectibles", []), "collectible")
        _insert_entries(payload.get("opers", []), "operator")
        self.conn.commit()
        return self.get_collection_summary(player_id)

    def get_collection_summary(self, player_id: str | None = None) -> dict:
        where_parts = []
        args: list = []
        if player_id:
            where_parts.append("player_id = ?")
            args.append(player_id)
        where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        row = self.conn.execute(
            f"""SELECT COUNT(*) as total_entries,
                       COALESCE(SUM(num), 0) as total_units,
                       COALESCE(SUM(CASE WHEN is_collectible_gun = 1 AND grade = 5 THEN 1 ELSE 0 END), 0) as collectible_guns,
                       COALESCE(SUM(CASE WHEN category_code = 'operator' AND grade = 6 THEN 1 ELSE 0 END), 0) as operator_count,
                       COALESCE(SUM(CASE WHEN category_code = 'gun' THEN 1 ELSE 0 END), 0) as gun_count,
                       COALESCE(SUM(CASE WHEN category_code = 'dagger' AND grade = 5 THEN 1 ELSE 0 END), 0) as dagger_count,
                       COALESCE(SUM(CASE WHEN category_code = 'vehicle' THEN 1 ELSE 0 END), 0) as vehicle_count,
                       COALESCE(SUM(CASE WHEN category_code = 'pendant' THEN 1 ELSE 0 END), 0) as pendant_count,
                       MAX(fetched_at) as collection_fetched_at
                FROM collection{where}""",
            args,
        ).fetchone()
        if row:
            summary = dict(row)
        else:
            summary = {
                "total_entries": 0,
                "total_units": 0,
                "collectible_guns": 0,
                "operator_count": 0,
                "gun_count": 0,
                "dagger_count": 0,
                "vehicle_count": 0,
                "pendant_count": 0,
            }
        if player_id:
            role_row = self.conn.execute(
                """SELECT hafcoinnum, total_price, noncurrent_asset, current_asset, fetched_at
                   FROM RoleInfo
                   WHERE player_id = ?""",
                (player_id,),
            ).fetchone()
        else:
            role_row = None
        if role_row:
            summary.update(
                {
                    "hafcoinnum": int(role_row["hafcoinnum"] or 0),
                    "total_price": int(role_row["total_price"] or 0),
                    "noncurrent_asset": int(role_row["noncurrent_asset"] or 0),
                    "current_asset": int(role_row["current_asset"] or 0),
                    "last_fetched_at": str(
                        role_row["fetched_at"]
                        or summary.get("collection_fetched_at")
                        or ""
                    ).strip(),
                }
            )
        else:
            summary.update(
                {
                    "hafcoinnum": 0,
                    "total_price": 0,
                    "noncurrent_asset": 0,
                    "current_asset": 0,
                    "last_fetched_at": str(summary.get("collection_fetched_at") or "").strip(),
                }
            )
        return summary

    def upsert_role_info(self, player_id: str, role_info: dict, account_type: int = 2):
        player_id = str(player_id or "").strip()
        if not player_id or not isinstance(role_info, dict):
            return
        openid = str(role_info.get("openid") or player_id).strip()
        area = _safe_int(role_info.get("area"), 36)
        name = sanitize_player_name(str(role_info.get("name", "") or ""))
        icon = str(role_info.get("icon", "") or "").strip()
        level = _safe_int(role_info.get("level"), 0)
        self.upsert_account(
            player_id=player_id,
            openid=openid,
            player_name=name,
            player_icon=icon,
            level=level,
            area=area,
            account_type=account_type,
        )
        self.conn.execute(
            """INSERT INTO RoleInfo
               (player_id, openid, area, name, level, icon, tdm_level, tdm_exp,
                propcapital, hafcoinnum, total_price, noncurrent_asset, current_asset, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(player_id) DO UPDATE SET
                   openid=excluded.openid,
                   area=excluded.area,
                   name=excluded.name,
                   level=excluded.level,
                   icon=excluded.icon,
                   tdm_level=excluded.tdm_level,
                   tdm_exp=excluded.tdm_exp,
                   propcapital=excluded.propcapital,
                   hafcoinnum=excluded.hafcoinnum,
                   total_price=excluded.total_price,
                   noncurrent_asset=excluded.noncurrent_asset,
                   current_asset=excluded.current_asset,
                   fetched_at=excluded.fetched_at""",
            (
                player_id,
                openid,
                area,
                name,
                level,
                icon,
                _safe_int(role_info.get("tdmLevel"), 0),
                _safe_int(role_info.get("tdmExp"), 0),
                _safe_int(role_info.get("propcapital"), 0),
                _safe_int(role_info.get("hafcoinnum"), 0),
                _safe_int(role_info.get("totalPrice"), 0),
                _safe_int(role_info.get("noncurrentAsset"), 0),
                _safe_int(role_info.get("currentAsset"), 0),
                datetime.now().isoformat(),
            ),
        )
        self.conn.commit()

    def get_collection_category_counts(self, player_id: str | None = None) -> dict[str, int]:
        where = ""
        args: list = []
        if player_id:
            where = " WHERE player_id = ?"
            args.append(player_id)
        rows = self.conn.execute(
            f"""SELECT category_code, COUNT(*) as cnt
                FROM collection{where}
                GROUP BY category_code""",
            args,
        ).fetchall()
        counts = {str(r["category_code"] or "").strip(): int(r["cnt"] or 0) for r in rows}
        return {
            "operator": counts.get("operator", 0),
            "gun": counts.get("gun", 0),
            "dagger": counts.get("dagger", 0),
            "vehicle": counts.get("vehicle", 0),
            "pendant": counts.get("pendant", 0),
        }

    def get_collection_items(
        self,
        player_id: str | None = None,
        category_code: str = "",
        grade: str = "",
        keyword: str = "",
        collectible_only: bool = False,
    ) -> list[dict]:
        where_parts = []
        args: list = []
        if player_id:
            where_parts.append("player_id = ?")
            args.append(player_id)
        if category_code:
            where_parts.append("category_code = ?")
            args.append(category_code)
        if grade:
            where_parts.append("grade = ?")
            args.append(_safe_int(grade))
        if collectible_only:
            where_parts.append("is_collectible_gun = 1")
        if keyword:
            like = f"%{keyword.strip()}%"
            where_parts.append(
                "(item_name LIKE ? OR item_id LIKE ? OR unique_no LIKE ? OR tag_text LIKE ?)"
            )
            args.extend([like, like, like, like])
        where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        rows = self.conn.execute(
            f"""SELECT *
                FROM collection
                {where}
                ORDER BY grade DESC, category_name ASC, item_name ASC, unique_no ASC, item_id ASC
                LIMIT 2000""",
            args,
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Record ----

    def upsert_records(self, battles: list[dict]) -> int:
        inserted = 0
        for b in battles:
            room_id = str(b.get("roomId", ""))
            exists = self.conn.execute(
                "SELECT 1 FROM Record WHERE room_id = ?", (room_id,)
            ).fetchone()
            if exists:
                continue

            self.conn.execute(
                """INSERT INTO Record
                   (room_id, map_id, event_time, start_time, duration_s,
                    game_result, game_rule, role_id,
                    kill_cnt, kill_player, assist_cnt, rescue,
                    collection_price, gained_price, profit_loss,
                    original_equipment_price,
                    is_rank_match, is_leave, has_blue_box,
                    team_id, queue, area, player_id, openid,
                    player_name, player_icon, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(b.get("roomId", "")),
                    b.get("mapId"),
                    b.get("dtEventTime"),
                    b.get("startTime"),
                    b.get("gameTime"),
                    b.get("gameResult"),
                    b.get("gameRule"),
                    b.get("armedForceId"),
                    b.get("killCnt", 0),
                    b.get("killPlayer", 0),
                    b.get("assistCnt", 0),
                    b.get("rescue", 0),
                    int(b["collectionPrice"]) if b.get("collectionPrice") else 0,
                    int(b["gainedPrice"]) if b.get("gainedPrice") else 0,
                    b.get("ProfitLoss", 0),
                    b.get("originalEquipmentPriceWithoutKeyChain", 0),
                    b.get("isRankMatch", 0),
                    b.get("isLeave", 0),
                    b.get("hasBlueBox", 0),
                    b.get("teamId"),
                    b.get("queue"),
                    b.get("iZoneAreaId"),
                    b.get("playerId"),
                    b.get("vopenid"),
                    sanitize_player_name(b.get("name")),
                    b.get("icon"),
                    datetime.now().isoformat(),
                ),
            )

            for item in b.get("collections", []):
                self.conn.execute(
                    """INSERT OR IGNORE INTO battles_items (room_id, item_id, item_name, pic, grade, num, price, player_id)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(b.get("roomId", "")),
                        item.get("id"),
                        item.get("name"),
                        item.get("pic"),
                        item.get("grade"),
                        item.get("num", 1),
                        item.get("price", 0),
                        b.get("playerId"),
                    ),
                )
            inserted += 1
        self.conn.commit()
        return inserted

    # ---- battle_details (raw report) ----

    def upsert_battle_detail(self, room_id: str, raw_data: dict):
        self.conn.execute(
            """INSERT INTO battle_details (room_id, raw_data, fetched_at)
               VALUES (?, ?, ?)
               ON CONFLICT(room_id) DO UPDATE SET
                   raw_data=excluded.raw_data,
                   fetched_at=excluded.fetched_at""",
            (
                room_id,
                json.dumps(raw_data, ensure_ascii=False),
                datetime.now().isoformat(),
            ),
        )
        self.conn.commit()

    def upsert_battle_players(self, room_id: str, players: list[dict]):
        for p in players:
            self.conn.execute(
                """INSERT INTO battle_players
                   (room_id, player_name, player_id, team_id,
                    kill_count, death_count, assist_count, damage,
                    survive, role_id, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    room_id,
                    sanitize_player_name(
                        p.get("playerName", p.get("PlayerName", p.get("NickName", "")))
                    ),
                    str(p.get("playerId", p.get("PlayerId", p.get("OpenId", "")))),
                    p.get("teamId", p.get("TeamId", p.get("CampId", 0))),
                    p.get("killCount", p.get("KillCount", p.get("KillNum", 0))),
                    p.get("deathCount", p.get("DeathCount", p.get("DeadNum", 0))),
                    p.get("assistCount", p.get("AssistCount", p.get("AssistNum", 0))),
                    p.get("damage", p.get("Damage", 0)),
                    p.get("survive", p.get("Survive", p.get("IsEscaped", 0))),
                    p.get("armedForceId", p.get("ArmedForceId", 0)),
                    json.dumps(p, ensure_ascii=False),
                ),
            )
        self.conn.commit()

    def upsert_battle_detail_v2(
        self, room_id: str, players: list[dict], self_openid: str = ""
    ):
        for player in players:
            player_id = str(player.get("playerId", ""))
            vopenid = str(player.get("vopenid", ""))
            is_self = 1 if (self_openid and vopenid == self_openid) else 0

            self.conn.execute(
                """INSERT OR REPLACE INTO BattleDetail
                   (room_id, player_id, vopenid, player_name, player_icon,
                    armed_force_id, map_id, event_time, start_time, duration_s,
                    game_result, game_rule, kill_cnt, kill_player, assist_cnt,
                    rescue, collection_price, gained_price, profit_loss,
                    original_equipment_price, is_rank_match, is_leave, has_blue_box,
                    team_id, area, is_self, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    room_id,
                    player_id,
                    vopenid,
                    sanitize_player_name(player.get("name")),
                    player.get("icon"),
                    player.get("armedForceId"),
                    player.get("mapId"),
                    player.get("dtEventTime"),
                    player.get("startTime"),
                    player.get("gameTime"),
                    player.get("gameResult"),
                    player.get("gameRule"),
                    player.get("killCnt", 0),
                    player.get("killPlayer", 0),
                    player.get("assistCnt", 0),
                    player.get("rescue", 0),
                    int(player["collectionPrice"])
                    if player.get("collectionPrice")
                    else 0,
                    int(player["gainedPrice"]) if player.get("gainedPrice") else 0,
                    player.get("ProfitLoss", 0),
                    player.get("originalEquipmentPriceWithoutKeyChain", 0),
                    player.get("isRankMatch", 0),
                    player.get("isLeave", 0),
                    player.get("hasBlueBox", 0),
                    player.get("teamId"),
                    player.get("iZoneAreaId"),
                    is_self,
                    datetime.now().isoformat(),
                ),
            )

            for item in player.get("collections", []):
                self.conn.execute(
                    """INSERT OR IGNORE INTO battles_items (room_id, item_id, item_name, pic, grade, num, price, player_id)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        room_id,
                        item.get("id"),
                        item.get("name"),
                        item.get("pic"),
                        item.get("grade"),
                        item.get("num", 1),
                        item.get("price", 0),
                        player_id,
                    ),
                )

        self.conn.commit()

    def clear_records(self, player_id: str | None = None):
        if player_id:
            room_rows = self.conn.execute(
                "SELECT room_id FROM Record WHERE player_id = ?", (player_id,)
            ).fetchall()
            room_ids = [r["room_id"] for r in room_rows]
            if not room_ids:
                return
            placeholders = ",".join("?" for _ in room_ids)
            self.conn.execute(
                f"DELETE FROM BattleDetail WHERE room_id IN ({placeholders})", room_ids
            )
            self.conn.execute(
                f"DELETE FROM battle_players WHERE room_id IN ({placeholders})", room_ids
            )
            self.conn.execute(
                f"DELETE FROM battle_details WHERE room_id IN ({placeholders})", room_ids
            )
            self.conn.execute(
                f"DELETE FROM battles_items WHERE room_id IN ({placeholders})", room_ids
            )
            self.conn.execute("DELETE FROM Record WHERE player_id = ?", (player_id,))
            self.conn.execute("DELETE FROM collection WHERE player_id = ?", (player_id,))
        else:
            self.conn.execute("DELETE FROM BattleDetail")
            self.conn.execute("DELETE FROM battle_players")
            self.conn.execute("DELETE FROM battle_details")
            self.conn.execute("DELETE FROM battles_items")
            self.conn.execute("DELETE FROM Record")
            self.conn.execute("DELETE FROM collection")
        self.conn.commit()

    # ---- backup import / export ----

    def _report_index_map(self) -> dict[str, dict]:
        data = vault_store.load_team_report_index()
        reports = data.get("reports", data) if isinstance(data, dict) else data
        if not isinstance(reports, list):
            return {}
        out: dict[str, dict] = {}
        for item in reports:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = dict(item)
        return out

    def _write_report_index_map(self, index: dict[str, dict]) -> None:
        reports = sorted(
            index.values(),
            key=lambda item: float(item.get("created_at", 0) or 0),
        )
        vault_store.save_team_report_index({"reports": reports})

    def _backup_manifest_counts(self, conn: sqlite3.Connection) -> dict:
        def _count(table: str) -> int:
            try:
                return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
            except Exception:
                return 0

        return {
            "accounts": _count("Account"),
            "records": _count("Record"),
            "items": _count("battles_items"),
            "collections": _count("collection"),
            "raw_details": _count("battle_details"),
            "player_details": _count("BattleDetail"),
            "team_players": _count("battle_players"),
        }

    def export_backup_package(self) -> dict:
        reports_dir = config.DATA_DIR / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        self.conn.execute("PRAGMA wal_checkpoint(FULL)")
        report_index = self._report_index_map()
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"dfdc_backup_{stamp}.zip"
        manifest = {
            "backup_version": 1,
            "created_at": created_at,
            "app": "Delta Force Data Center",
            "database": {"filename": "database.sqlite3", "counts": self._backup_manifest_counts(self.conn)},
            "reports": {"count": 0},
        }
        report_entries: list[tuple[dict, Path]] = []
        for entry in report_index.values():
            report_name = str(entry.get("filename", "") or "").strip()
            if not report_name:
                continue
            path = reports_dir / report_name
            if not path.exists() or not path.is_file():
                continue
            report_entries.append((dict(entry), path))
        manifest["reports"]["count"] = len(report_entries)

        buffer = BytesIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_copy_path = Path(tmpdir) / "database.sqlite3"
            backup_conn = sqlite3.connect(str(db_copy_path))
            try:
                self.conn.backup(backup_conn)
            finally:
                backup_conn.close()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                zf.write(db_copy_path, "database.sqlite3")
                if report_entries:
                    zf.writestr(
                        "report_index.json",
                        json.dumps(
                            {"reports": [entry for entry, _ in report_entries]},
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                    for _, path in report_entries:
                        zf.write(path, f"reports/{path.name}")
        return {
            "filename": filename,
            "content": buffer.getvalue(),
            "manifest": manifest,
        }

    def _table_exists(self, schema: str, table_name: str) -> bool:
        row = self.conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return bool(row)

    def _merge_backup_database(self, db_path: Path) -> dict:
        before_records = self.get_record_count()
        attach_name = "backupdb"
        self.conn.execute("PRAGMA wal_checkpoint(FULL)")
        self.conn.execute(f"ATTACH DATABASE ? AS {attach_name}", (str(db_path),))
        try:
            if self._table_exists(attach_name, "MapID"):
                self.conn.execute(
                    f"""INSERT OR REPLACE INTO MapID (map_id, map_name)
                        SELECT map_id, map_name FROM {attach_name}.MapID"""
                )
            if self._table_exists(attach_name, "Role"):
                self.conn.execute(
                    f"""INSERT OR REPLACE INTO Role (role_id, role_name)
                        SELECT role_id, role_name FROM {attach_name}.Role"""
                )
            if self._table_exists(attach_name, "BattleTagRule"):
                self.conn.execute(
                    f"""INSERT OR REPLACE INTO BattleTagRule
                        (tag_name, rule_text, dimension, note, scope, sort_order)
                        SELECT tag_name, rule_text, dimension, note, scope, sort_order
                        FROM {attach_name}.BattleTagRule"""
                )
            if self._table_exists(attach_name, "Account"):
                self.conn.execute(
                    f"""INSERT OR IGNORE INTO Account
                        (player_id, openid, player_name, player_icon, level, area, account_type, is_active, last_login)
                        SELECT player_id, openid, player_name, player_icon, level, area, account_type, is_active, last_login
                        FROM {attach_name}.Account"""
                )
            if self._table_exists(attach_name, "Record"):
                self.conn.execute(
                    f"""INSERT OR IGNORE INTO Record
                        (room_id, map_id, event_time, start_time, duration_s,
                         game_result, game_rule, role_id, kill_cnt, kill_player,
                         assist_cnt, rescue, collection_price, gained_price, profit_loss,
                         original_equipment_price, is_rank_match, is_leave, has_blue_box,
                         team_id, queue, area, player_id, openid, player_name, player_icon, fetched_at)
                        SELECT room_id, map_id, event_time, start_time, duration_s,
                               game_result, game_rule, role_id, kill_cnt, kill_player,
                               assist_cnt, rescue, collection_price, gained_price, profit_loss,
                               original_equipment_price, is_rank_match, is_leave, has_blue_box,
                               team_id, queue, area, player_id, openid, player_name, player_icon, fetched_at
                        FROM {attach_name}.Record"""
                )
            if self._table_exists(attach_name, "battle_details"):
                self.conn.execute(
                    f"""INSERT OR IGNORE INTO battle_details (room_id, raw_data, fetched_at)
                        SELECT room_id, raw_data, fetched_at FROM {attach_name}.battle_details"""
                )
            if self._table_exists(attach_name, "BattleDetail"):
                self.conn.execute(
                    f"""INSERT OR IGNORE INTO BattleDetail
                        (room_id, player_id, vopenid, player_name, player_icon,
                         armed_force_id, map_id, event_time, start_time, duration_s,
                         game_result, game_rule, kill_cnt, kill_player, assist_cnt,
                         rescue, collection_price, gained_price, profit_loss,
                         original_equipment_price, is_rank_match, is_leave, has_blue_box,
                         team_id, area, is_self, fetched_at)
                        SELECT room_id, player_id, vopenid, player_name, player_icon,
                               armed_force_id, map_id, event_time, start_time, duration_s,
                               game_result, game_rule, kill_cnt, kill_player, assist_cnt,
                               rescue, collection_price, gained_price, profit_loss,
                               original_equipment_price, is_rank_match, is_leave, has_blue_box,
                               team_id, area, is_self, fetched_at
                        FROM {attach_name}.BattleDetail"""
                )
            if self._table_exists(attach_name, "battles_items"):
                self.conn.execute(
                    f"""INSERT OR IGNORE INTO battles_items
                        (room_id, item_id, item_name, pic, grade, num, price, player_id)
                        SELECT room_id, item_id, item_name, pic, grade, num, price, player_id
                        FROM {attach_name}.battles_items"""
                )
            if self._table_exists(attach_name, "battle_players"):
                self.conn.execute(
                    f"""INSERT INTO battle_players
                        (room_id, player_name, player_id, team_id, kill_count, death_count,
                         assist_count, damage, survive, role_id, raw_data)
                        SELECT s.room_id, s.player_name, s.player_id, s.team_id, s.kill_count, s.death_count,
                               s.assist_count, s.damage, s.survive, s.role_id, s.raw_data
                        FROM {attach_name}.battle_players s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM battle_players t
                            WHERE t.room_id = s.room_id
                              AND COALESCE(t.player_id, '') = COALESCE(s.player_id, '')
                              AND COALESCE(t.player_name, '') = COALESCE(s.player_name, '')
                              AND COALESCE(t.team_id, -1) = COALESCE(s.team_id, -1)
                        )"""
                )
            if self._table_exists(attach_name, "collection"):
                self.conn.execute(
                    f"""INSERT OR REPLACE INTO collection
                        (inventory_key, player_id, item_id, source_group, item_name,
                         category_code, category_name, grade, grade_text, num,
                         quality_raw, is_collectible_gun, collectible_rarity_code,
                         collectible_rarity_text, wear_raw, finish_grade, unique_no,
                         pic, pre_pic, is_archive, tag_text, fetched_at)
                        SELECT inventory_key, player_id, item_id, source_group, item_name,
                               category_code, category_name, grade, grade_text, num,
                               quality_raw, is_collectible_gun, collectible_rarity_code,
                               collectible_rarity_text, wear_raw, finish_grade, unique_no,
                               pic, pre_pic, is_archive, tag_text, fetched_at
                        FROM {attach_name}.collection"""
                )
            self.conn.commit()
        finally:
            self.conn.execute(f"DETACH DATABASE {attach_name}")

        if not self.get_active_account():
            next_row = self.conn.execute(
                "SELECT player_id FROM Account ORDER BY is_active DESC, last_login DESC LIMIT 1"
            ).fetchone()
            if next_row and next_row["player_id"]:
                self.set_active_account(next_row["player_id"])

        after_records = self.get_record_count()
        return {
            "records_added": max(after_records - before_records, 0),
        }

    def import_backup_package(self, raw_bytes: bytes) -> dict:
        if not raw_bytes:
            raise ValueError("备份文件内容为空")
        reports_dir = config.DATA_DIR / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        existing_index = self._report_index_map()
        added_reports = 0
        skipped_reports = 0

        with zipfile.ZipFile(BytesIO(raw_bytes), "r") as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names or "database.sqlite3" not in names:
                raise ValueError("备份文件格式不正确，缺少 manifest.json 或 database.sqlite3")
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except Exception as exc:
                raise ValueError(f"备份清单读取失败: {exc}") from exc
            version = int(manifest.get("backup_version", 0) or 0)
            if version != 1:
                raise ValueError(f"不支持的备份版本: {version}")

            with tempfile.TemporaryDirectory() as tmpdir:
                import_db_path = Path(tmpdir) / "import.sqlite3"
                import_db_path.write_bytes(zf.read("database.sqlite3"))
                merge_summary = self._merge_backup_database(import_db_path)

            if "report_index.json" in names:
                try:
                    imported_index_raw = json.loads(zf.read("report_index.json").decode("utf-8"))
                except Exception:
                    imported_index_raw = {}
                imported_reports = imported_index_raw.get("reports", imported_index_raw) if isinstance(imported_index_raw, dict) else imported_index_raw
                if isinstance(imported_reports, list):
                    merged_index = dict(existing_index)
                    for entry in imported_reports:
                        if not isinstance(entry, dict) or not entry.get("id"):
                            continue
                        report_id = str(entry["id"])
                        zip_name = f"reports/{str(entry.get('filename', '') or '').strip()}"
                        if zip_name not in names:
                            continue
                        if report_id in merged_index:
                            skipped_reports += 1
                            continue
                        raw_report = zf.read(zip_name)
                        target_name = Path(zip_name).name
                        target_path = reports_dir / target_name
                        if target_path.exists():
                            target_name = f"{report_id}_{target_name}"
                            target_path = reports_dir / target_name
                        target_path.write_bytes(raw_report)
                        new_entry = dict(entry)
                        new_entry["filename"] = target_name
                        merged_index[report_id] = new_entry
                        added_reports += 1
                    self._write_report_index_map(merged_index)

        self.conn.execute("PRAGMA wal_checkpoint(FULL)")
        if self.db_path == config.DB_PATH:
            vault_store.persist_runtime_database(remove_runtime=False)
        declared_records = int(((manifest.get("database") or {}).get("counts") or {}).get("records", 0) or 0)
        return {
            "manifest": manifest,
            "records_added": int(merge_summary.get("records_added", 0) or 0),
            "records_skipped": max(declared_records - int(merge_summary.get("records_added", 0) or 0), 0),
            "reports_added": added_reports,
            "reports_skipped": skipped_reports,
        }

    # ---- queries ----

    def get_record_count(self, player_id: str | None = None) -> int:
        if player_id:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM Record WHERE player_id = ?", (player_id,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM Record").fetchone()
        return row[0]

    def get_detail_count(self, player_id: str | None = None) -> int:
        try:
            if player_id:
                row = self.conn.execute(
                    """SELECT COUNT(*)
                       FROM battle_details bd
                       JOIN Record r ON r.room_id = bd.room_id
                       WHERE r.player_id = ?""",
                    (player_id,),
                ).fetchone()
            else:
                row = self.conn.execute("SELECT COUNT(*) FROM battle_details").fetchone()
            return row[0]
        except Exception:
            return 0

    def get_records_without_report(self, player_id: str | None = None) -> list[dict]:
        try:
            where = " AND r.player_id = ?" if player_id else ""
            args = (player_id,) if player_id else ()
            rows = self.conn.execute(
                f"""SELECT r.room_id, COALESCE(NULLIF(r.queue, ''), 'sol') AS queue
                    FROM Record r
                    WHERE NOT EXISTS (SELECT 1 FROM battle_details bd WHERE bd.room_id = r.room_id)
                    {where}
                    ORDER BY r.event_time DESC""",
                args,
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_records_without_detail(self, player_id: str | None = None) -> list[dict]:
        try:
            where = " AND r.player_id = ?" if player_id else ""
            args = (player_id,) if player_id else ()
            rows = self.conn.execute(
                f"""SELECT r.room_id, COALESCE(NULLIF(r.queue, ''), 'sol') AS queue
                    FROM Record r
                    WHERE NOT EXISTS (SELECT 1 FROM BattleDetail bd WHERE bd.room_id = r.room_id)
                    {where}
                    ORDER BY r.event_time DESC""",
                args,
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_battle_detail_count(self) -> int:
        try:
            row = self.conn.execute("SELECT COUNT(*) FROM BattleDetail").fetchone()
            return row[0]
        except Exception:
            return 0

    def get_battle_detail_count_distinct(self, player_id: str | None = None) -> int:
        try:
            if player_id:
                row = self.conn.execute(
                    """SELECT COUNT(DISTINCT bd.room_id)
                       FROM BattleDetail bd
                       JOIN Record r ON r.room_id = bd.room_id
                       WHERE r.player_id = ?""",
                    (player_id,),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT COUNT(DISTINCT room_id) FROM BattleDetail"
                ).fetchone()
            return row[0]
        except Exception:
            return 0

    def get_all_records(self, player_id: str | None = None) -> list[dict]:
        where = " WHERE r.player_id = ?" if player_id else ""
        args: list = [player_id] if player_id else []
        rows = self.conn.execute(
            f"""SELECT r.*,
                      m.map_name,
                      rl.role_name,
                      CASE WHEN bd.room_id IS NOT NULL THEN 1 ELSE 0 END as has_detail,
                      (SELECT COUNT(*) FROM battles_items bi WHERE bi.room_id = r.room_id) as item_count
               FROM Record r
               LEFT JOIN MapID m  ON r.map_id  = m.map_id
               LEFT JOIN Role  rl ON r.role_id = rl.role_id
               LEFT JOIN battle_details bd ON r.room_id = bd.room_id
               {where}
               ORDER BY r.event_time DESC""",
            args,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_items_for_export(self, player_id: str | None = None) -> list[dict]:
        where = " WHERE r.player_id = ?" if player_id else ""
        args: list = [player_id] if player_id else []
        rows = self.conn.execute(
            f"""SELECT bi.*, r.event_time, r.room_id as battle_room_id,
                      m.map_name, rl.role_name
               FROM battles_items bi
               JOIN Record r   ON bi.room_id = r.room_id
               LEFT JOIN MapID m  ON r.map_id  = m.map_id
               LEFT JOIN Role  rl ON r.role_id = rl.role_id
               {where}
               ORDER BY r.event_time DESC, bi.id""",
            args,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_battle_stats(
        self, player_id: str | None = None, start_dt: str = "", end_dt: str = ""
    ) -> dict:
        where_parts = []
        args: list = []
        if player_id:
            where_parts.append("r.player_id = ?")
            args.append(player_id)
        if start_dt:
            where_parts.append("r.event_time >= ?")
            args.append(start_dt)
        if end_dt:
            where_parts.append("r.event_time <= ?")
            args.append(end_dt)
        where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        total_row = self.conn.execute(
            f"SELECT COUNT(*) FROM Record r{where}", args
        ).fetchone()
        total = total_row[0]
        if total == 0:
            return {"total": 0}
        row = self.conn.execute(
            f"""SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN game_result = 0 THEN 1 ELSE 0 END) as escaped,
                 SUM(kill_cnt) as total_kills,
                 SUM(kill_player) as total_player_kills,
                 AVG(duration_s) as avg_duration,
                 AVG(CAST(profit_loss AS REAL)) as avg_profit,
                 SUM(CAST(profit_loss AS REAL)) as total_profit
               FROM Record r{where}""",
            args,
        ).fetchone()
        return dict(row)
