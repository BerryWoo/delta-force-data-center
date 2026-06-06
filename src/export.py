import csv
import json
from pathlib import Path

from .database import Database
from . import config

GAME_RESULT_MAP = {0: "撤离成功", 1: "撤离失败", 2: "行动超时", 3: "中途退出"}


def _fmt_duration(seconds) -> str:
    if seconds is None:
        return ""
    m, s = divmod(int(seconds), 60)
    return f"{m}分{s}秒"


def export_records_csv(
    db: Database,
    output_path: Path | None = None,
    player_id: str | None = None,
) -> Path:
    output_path = output_path or config.DATA_DIR / "records.csv"
    records = db.get_all_records(player_id=player_id)

    if not records:
        print("[export] 没有战绩数据可导出")
        return output_path

    fieldnames = [
        "event_time",
        "map_name",
        "role_name",
        "evacuate_status",
        "duration_fmt",
        "kill_player",
        "kill_cnt",
        "assist_cnt",
        "rescue",
        "collection_price",
        "gained_price",
        "profit_loss",
        "original_equipment_price",
        "is_rank_match",
        "is_leave",
        "has_blue_box",
        "team_id",
        "queue",
        "area",
        "item_count",
        "has_detail",
        "start_time",
        "game_rule",
        "player_id",
        "openid",
        "player_name",
        "room_id",
    ]

    rows = []
    for r in records:
        gr = r.get("game_result")
        rows.append(
            {
                "event_time": r.get("event_time", ""),
                "map_name": r.get("map_name", ""),
                "role_name": r.get("role_name", ""),
                "evacuate_status": GAME_RESULT_MAP.get(gr, str(gr)),
                "duration_fmt": _fmt_duration(r.get("duration_s")),
                "kill_player": r.get("kill_player", 0),
                "kill_cnt": r.get("kill_cnt", 0),
                "assist_cnt": r.get("assist_cnt", 0),
                "rescue": r.get("rescue", 0),
                "collection_price": r.get("collection_price", 0),
                "gained_price": r.get("gained_price", 0),
                "profit_loss": r.get("profit_loss", 0),
                "original_equipment_price": r.get("original_equipment_price", 0),
                "is_rank_match": r.get("is_rank_match", 0),
                "is_leave": r.get("is_leave", 0),
                "has_blue_box": r.get("has_blue_box", 0),
                "team_id": r.get("team_id", ""),
                "queue": r.get("queue", ""),
                "area": r.get("area", ""),
                "item_count": r.get("item_count", 0),
                "has_detail": r.get("has_detail", 0),
                "start_time": r.get("start_time", ""),
                "game_rule": r.get("game_rule", ""),
                "player_id": f"ID:{r.get('player_id', '')}",
                "openid": f"ID:{r.get('openid', '')}",
                "player_name": r.get("player_name", ""),
                "room_id": f"ID:{r.get('room_id', '')}",
            }
        )

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"[export] 已导出 {len(rows)} 条战绩到 {output_path}")
    return output_path


def export_items_csv(
    db: Database,
    output_path: Path | None = None,
    player_id: str | None = None,
) -> Path:
    output_path = output_path or config.DATA_DIR / "battles_items.csv"
    items = db.get_items_for_export(player_id=player_id)

    if not items:
        print("[export] 没有带出物品数据可导出")
        return output_path

    fieldnames = [
        "event_time",
        "map_name",
        "role_name",
        "room_id",
        "item_id",
        "item_name",
        "grade",
        "num",
        "price",
        "pic",
    ]

    rows = []
    for item in items:
        rows.append(
            {
                "event_time": item.get("event_time", ""),
                "map_name": item.get("map_name", ""),
                "role_name": item.get("role_name", ""),
                "room_id": f"ID:{item.get('room_id', '')}",
                "item_id": item.get("item_id", ""),
                "item_name": item.get("item_name", ""),
                "grade": item.get("grade", ""),
                "num": item.get("num", 1),
                "price": item.get("price", 0),
                "pic": item.get("pic", ""),
            }
        )

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"[export] 已导出 {len(rows)} 条物品到 {output_path}")
    return output_path


def export_details_json(
    db: Database,
    output_path: Path | None = None,
) -> Path:
    output_path = output_path or config.DATA_DIR / "battle_details.json"
    try:
        rows = db.conn.execute(
            """SELECT bd.room_id, bd.raw_data, r.event_time, r.map_id
               FROM battle_details bd
               JOIN Record r ON bd.room_id = r.room_id
               ORDER BY r.event_time DESC"""
        ).fetchall()
    except Exception:
        rows = []

    details = []
    for row in rows:
        details.append(
            {
                "room_id": row["room_id"],
                "event_time": row["event_time"],
                "map_id": row["map_id"],
                "detail": json.loads(row["raw_data"]) if row["raw_data"] else {},
            }
        )

    output_path.write_text(
        json.dumps(details, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[export] 已导出 {len(details)} 条详情到 {output_path}")
    return output_path


def export_players_csv(
    db: Database,
    output_path: Path | None = None,
) -> Path:
    output_path = output_path or config.DATA_DIR / "players.csv"
    try:
        rows = db.conn.execute(
            """SELECT bp.*, r.event_time, r.map_id,
                      m.map_name, rl.role_name
               FROM battle_players bp
               JOIN Record r   ON bp.room_id = r.room_id
               LEFT JOIN MapID m  ON r.map_id  = m.map_id
               LEFT JOIN Role  rl ON bp.role_id = rl.role_id
               ORDER BY r.event_time DESC"""
        ).fetchall()
    except Exception:
        rows = []

    if not rows:
        print("[export] 没有玩家数据可导出")
        return output_path

    players = [dict(r) for r in rows]
    fieldnames = [
        "room_id",
        "event_time",
        "map_name",
        "role_name",
        "player_name",
        "player_id",
        "team_id",
        "kill_count",
        "death_count",
        "assist_count",
        "damage",
        "survive",
    ]

    for p in players:
        p["room_id"] = f"ID:{p.get('room_id', '')}"
        p["player_id"] = f"ID:{p.get('player_id', '')}"

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(players)

    print(f"[export] 已导出 {len(players)} 条玩家数据到 {output_path}")
    return output_path


def export_all(
    db: Database, output_dir: Path | None = None, player_id: str | None = None
):
    output_dir = output_dir or config.DATA_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[export] 正在导出所有数据...")
    export_records_csv(db, output_dir / "records.csv", player_id=player_id)
    export_items_csv(db, output_dir / "battles_items.csv", player_id=player_id)
    export_details_json(db, output_dir / "battle_details.json")
    export_players_csv(db, output_dir / "players.csv")

    stats = db.get_battle_stats(player_id=player_id)
    (output_dir / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[export] 统计数据已导出到 {output_dir / 'stats.json'}")
