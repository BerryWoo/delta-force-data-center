import json
import random
import sys
import time
from typing import Any, Callable

import httpx

from . import config
from . import vault_store
from .database import Database


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass


def _safe_print(message: str) -> None:
    text = str(message)
    try:
        print(text)
    except UnicodeEncodeError:
        stream = getattr(sys, "stdout", None)
        encoding = getattr(stream, "encoding", None) or "utf-8"
        try:
            fallback = text.encode(encoding, errors="backslashreplace").decode(
                encoding, errors="ignore"
            )
            print(fallback)
        except Exception:
            pass


_configure_stdio()


COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.wegame.com.cn/helper/df/score-detail/",
    "Origin": "https://www.wegame.com.cn",
    "Content-Type": "application/json",
}

WEGAME_API = "https://www.wegame.com.cn/api/v1/wegame.pallas.dfm.DfmBattle"


class WeGameClient:
    def __init__(
        self,
        cookies: dict[str, str],
        db: Database,
        logger: Callable[[str], None] | None = None,
    ):
        self.db = db
        self.cookies = cookies
        self.logger = logger
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        self.headers = {**COMMON_HEADERS, "Cookie": cookie_str}
        self.client = httpx.Client(
            headers=self.headers,
            timeout=config.API_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        self._openid: str | None = None
        self._area: int = 36
        self._account_type: int = self._load_account_type()

    def _log(self, message: str):
        _safe_print(message)
        if self.logger:
            self.logger(message)

    def _pace_request(self, step_index: int):
        delay = random.uniform(0.45, 0.95)
        if step_index > 0 and step_index % 6 == 0:
            delay += random.uniform(0.35, 0.85)
        time.sleep(delay)

    def _retry_backoff(self, attempt: int):
        delay = 1.1 + attempt * 0.9 + random.uniform(0.2, 0.8)
        time.sleep(delay)

    @staticmethod
    def _load_account_type() -> int:
        try:
            bodies = vault_store.load_request_bodies()
            role_body = bodies.get("GetRoleInfo")
            if role_body:
                data = json.loads(role_body)
                at = data.get("account_type")
                if isinstance(at, int):
                    _safe_print(f"[client] 从登录记录读取 account_type={at}")
                    return at
        except Exception:
            pass
        _safe_print("[client] 使用默认 account_type=2")
        return 2

    def _wegame_post(
        self, action: str, body: dict, extra_headers: dict[str, str] | None = None
    ) -> dict | None:
        url = f"{WEGAME_API}/{action}"
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.client.post(url, json=body, headers=extra_headers)
                if resp.status_code != 200:
                    self._log(f"[client] [HTTP {resp.status_code}] {action}")
                    return None
                data = resp.json()
                if isinstance(data, dict):
                    result = data.get("result")
                    if isinstance(result, dict):
                        err = result.get("error_code")
                        if err and err != 0:
                            msg = str(result.get("error_message", "") or "")
                            timeout_like = str(err) == "8000102" or "timeout" in msg.lower()
                            if timeout_like and attempt < max_attempts:
                                self._log(
                                    f"[client] 接口 {action} 超时，准备第 {attempt + 1} 次重试"
                                )
                                self._retry_backoff(attempt)
                                continue
                            self._log(
                                f"[client] 接口 {action} 返回错误: error_code={err} {msg}"
                            )
                            return data
                return data
            except Exception as e:
                if attempt < max_attempts:
                    self._log(
                        f"[client] 接口 {action} 请求失败，准备第 {attempt + 1} 次重试: {e}"
                    )
                    self._retry_backoff(attempt)
                    continue
                self._log(f"[client] 接口 {action} 请求失败: {e}")
                return None
        return None

    def _extract_nested(self, data: Any, *keys) -> Any:
        cur = data
        for k in keys:
            if isinstance(cur, dict):
                cur = cur.get(k)
            elif isinstance(cur, list) and isinstance(k, int):
                cur = cur[k] if k < len(cur) else None
            else:
                return None
        return cur

    def _deep_find_list(self, data: Any, depth: int = 0) -> list | None:
        if depth > 8:
            return None
        if isinstance(data, list):
            return data if len(data) > 0 else None
        if isinstance(data, dict):
            for k in ("data", "list", "items", "records"):
                if k in data:
                    r = self._deep_find_list(data[k], depth + 1)
                    if r:
                        return r
        return None

    # ---- role info & openid ----
    def fetch_role_info(self, force_refresh: bool = False) -> dict:
        if self._openid and not force_refresh:
            return {}
        body = {
            "from_src": "df_web",
            "account_type": self._account_type,
            "area": self._area,
        }
        data = self._wegame_post("GetRoleInfo", body)
        if data:
            vault_store.save_raw_json("role_info.json", data)

            top_keys = [k for k in data.keys()] if isinstance(data, dict) else []
            self._log(f"[client] GetRoleInfo 响应字段: {top_keys}")

            openid = (
                self._extract_nested(data, "role_info", "openid")
                or self._extract_nested(data, "data", "openid")
                or self._extract_nested(data, "data", "data", "openid")
                or self._extract_nested(data, "data", "OpenId")
            )
            if openid:
                self._openid = str(openid)
                self._log(f"[client] 获取到 openid: {self._openid}")

                role_info = self._extract_nested(data, "role_info") or {}
                self.db.upsert_role_info(
                    player_id=str(openid),
                    role_info=role_info,
                    account_type=self._account_type,
                )
                name = role_info.get("name", "")
                if name:
                    self._log(
                        f"[client] 当前账号: {name} (level {role_info.get('level', '?')})"
                    )
            else:
                self._log("[client] [!] 无法从响应中提取 openid")
        else:
            self._log("[client] [!] GetRoleInfo 返回为空")

        if not self._openid:
            self._try_load_openid_from_debug()

        return data if isinstance(data, dict) else {}

    def _get_openid(self) -> str | None:
        if self._openid:
            return self._openid
        self.fetch_role_info(force_refresh=False)
        return self._openid

    def _try_load_openid_from_debug(self):
        for path_name, content in [
            ("debug_requests.json", vault_store.load_debug_requests()),
            ("request_bodies.json", vault_store.load_request_bodies()),
        ]:
            if not content:
                continue
            try:
                if path_name == "debug_requests.json":
                    entries = content if isinstance(content, list) else []
                    for entry in entries:
                        if "GetBattleList" in entry.get("url", ""):
                            post_data = json.loads(entry.get("post_data", "{}"))
                            openid = post_data.get("openid")
                            if openid:
                                self._openid = str(openid)
                                self._log(
                                    f"[client] [!] 从调试记录恢复 openid: {self._openid}"
                                )
                                return
                elif path_name == "request_bodies.json":
                    battle_body = content.get("GetBattleList")
                    if battle_body:
                        post_data = json.loads(battle_body)
                        openid = post_data.get("openid")
                        if openid:
                            self._openid = str(openid)
                            self._log(
                                f"[client] [!] 从请求体记录恢复 openid: {self._openid}"
                            )
                            return
            except Exception:
                pass

    # ---- fetch maps ----
    def fetch_maps(self) -> list[dict]:
        self._log("[client] 正在加载地图数据...")
        self.db.load_mapid_from_json()
        count = self.db.conn.execute("SELECT COUNT(*) FROM MapID").fetchone()[0]
        self._log(f"[client] 已加载 {count} 个地图数据")
        return []

    # ---- fetch agents ----

    def fetch_agents(self) -> list[dict]:
        self._log("[client] 正在加载干员数据...")
        self.db.load_role_from_json()
        count = self.db.conn.execute("SELECT COUNT(*) FROM Role").fetchone()[0]
        self._log(f"[client] 已加载 {count} 个干员数据")
        return []

    def fetch_collectibles(self) -> dict:
        self._log("[client] 正在获取账号资产...")
        openid = self._get_openid()
        if not openid:
            raise RuntimeError("无法获取 openid，请先重新登录 WeGame")

        body = {
            "from_src": "df_web",
            "openid": openid,
            "area": self._area,
            "account_type": self._account_type,
        }
        data = self._wegame_post(
            "GetCollectibles",
            body,
            extra_headers={"trpc-caller": "wegame.pallas.web.DfmBattle"},
        )
        if data is None:
            raise RuntimeError("GetCollectibles 请求失败")
        vault_store.save_raw_json("collectibles.json", data)
        player_id = self.db.get_active_player_id() or openid
        summary = self.db.replace_collection_for_player(player_id, data)
        self._log(
            f"[client] 资产获取完成，录入 {summary.get('total_entries', 0)} 条资产明细"
        )
        return summary

    def ensure_account(self) -> str | None:
        self.fetch_role_info(force_refresh=True)
        openid = self._openid or self._get_openid()
        if openid:
            self._log("[client] 已刷新当前登录账号信息")
        return openid
    # ---- fetch battle list ----
    def fetch_battle_list(
        self,
        target_count: int = 0,
        queue: str = "sol",
        progress_callback: Callable[[int, int], None] | None = None,
        stop_on_duplicate: bool = False,
    ) -> list[dict]:
        if target_count <= 0 and not stop_on_duplicate:
            target_count = 100
        if stop_on_duplicate:
            target_count = (
                target_count
                if target_count > 0
                else config.BATTLE_LIST_PAGE_SIZE * config.MAX_BATTLE_PAGES
            )

        queue_name = "烽火地带" if queue == "sol" else "全面战场"
        if stop_on_duplicate:
            self._log(f"[client] 正在智能获取战绩列表 ({queue_name})")
        else:
            self._log(
                f"[client] 正在获取战绩列表 ({queue_name}，目标 {target_count} 条)"
            )
        all_battles: list[dict] = []

        openid = self._get_openid()
        if not openid:
            self._log("[client] 无法获取 openid，请先重新登录")
            return all_battles

        after = None
        page = 0
        if progress_callback:
            progress_callback(0, target_count if not stop_on_duplicate else config.BATTLE_LIST_PAGE_SIZE)
        while len(all_battles) < target_count and page < config.MAX_BATTLE_PAGES:
            page += 1
            remaining = target_count - len(all_battles)
            size = min(config.BATTLE_LIST_PAGE_SIZE, remaining)
            if page > 1:
                self._pace_request(page - 1)

            body = {
                "from_src": "df_web",
                "size": size,
                "openid": openid,
                "area": self._area,
                "queue": queue,
                "after": after,
                "account_type": self._account_type,
                "filters": [],
            }

            data = self._wegame_post("GetBattleList", body)
            if data is None:
                break

            vault_store.save_raw_json(f"battle_list_page{page}.json", data)

            battles = self._extract_battle_list(data, queue)

            if not battles:
                if page == 1:
                    self._log("[client] 第 1 页无数据，响应已保存到受保护数据仓")
                self._log("[client] 已无更多战绩数据")
                break

            all_battles.extend(battles)
            new_count = self.db.upsert_records(battles)
            skipped = len(battles) - new_count
            skip_msg = f"，跳过 {skipped} 条重复" if skipped > 0 else ""
            if stop_on_duplicate:
                self._log(
                    f"[client] 第 {page} 页：新增 {new_count} 条{skip_msg} (累计 {len(all_battles)} 条)"
                )
            else:
                self._log(
                    f"[client] 第 {page} 页：新增 {new_count} 条{skip_msg} (累计 {len(all_battles)}/{target_count})"
                )
            if progress_callback:
                total_hint = target_count if not stop_on_duplicate else max(len(all_battles) + size, config.BATTLE_LIST_PAGE_SIZE)
                progress_callback(min(len(all_battles), total_hint), total_hint)

            if stop_on_duplicate and skipped > 0:
                self._log("[client] 已命中数据库重复战绩，停止智能抓取")
                break

            last = battles[-1]
            after = last.get("dtEventTime") or last.get("startTime")
            if not after:
                self._log("[client] 无法获取翻页游标，停止抓取")
                break

        self._log(
            f"[client] 战绩列表抓取完成，共获取 {len(all_battles)} 条"
        )
        if progress_callback:
            final_total = target_count if not stop_on_duplicate else max(len(all_battles), config.BATTLE_LIST_PAGE_SIZE)
            progress_callback(min(len(all_battles), final_total), final_total)
        return all_battles

    def _extract_battle_list(self, data: dict, queue: str = "sol") -> list[dict]:
        key = "sols" if queue == "sol" else "tdms"
        if key in data and isinstance(data[key], list):
            return data[key]

        for path in [
            ("data", "data"),
            ("data", "data", "data"),
            ("data",),
        ]:
            val = self._extract_nested(data, *path)
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                return val

        found = self._deep_find_list(data)
        if found and isinstance(found[0], dict):
            return found
        return []
    def fetch_battle_details(
        self,
        room_ids: list[str] | None = None,
        player_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> int:
        if room_ids is None:
            missing = self.db.get_records_without_report(player_id=player_id)
            room_ids = [r["room_id"] for r in missing]

        if not room_ids:
            self._log("[client] 没有需要获取的对局详情")
            if progress_callback:
                progress_callback(0, 0)
            return 0

        self._log(f"[client] 开始获取对局详情，共 {len(room_ids)} 条")
        if progress_callback:
            progress_callback(0, len(room_ids))

        openid = self._get_openid()
        if not openid:
            self._log("[client] 无法获取 openid，无法抓取对局详情")
            return 0

        fetched = 0
        for i, room_id in enumerate(room_ids):
            try:
                if i > 0:
                    self._pace_request(i)
                body = {
                    "from_src": "df_web",
                    "openid": openid,
                    "area": self._area,
                    "account_type": self._account_type,
                    "battle_id": room_id,
                    "room_id": room_id,
                }
                data = self._wegame_post("GetBattleReport", body)
                if data is None:
                    continue

                has_error = False
                result = data.get("result", {})
                if isinstance(result, dict) and result.get("error_code"):
                    has_error = True

                if not has_error:
                    self.db.upsert_battle_detail(room_id, data)

                    if i == 0:
                        vault_store.save_raw_json(f"battle_detail_{room_id}.json", data)

                    players = self._extract_players(data)
                    if players:
                        self.db.upsert_battle_players(room_id, players)

                    fetched += 1

                if (i + 1) % 10 == 0 or i == len(room_ids) - 1:
                    self._log(
                        f"[client] 对局详情进度 [{i + 1}/{len(room_ids)}]，已获取 {fetched} 条"
                    )
                if progress_callback:
                    progress_callback(i + 1, len(room_ids))

            except Exception as e:
                self._log(
                    f"[client] 对局详情错误 [{i + 1}/{len(room_ids)}] {room_id}: {e}"
                )
                if progress_callback:
                    progress_callback(i + 1, len(room_ids))

        self._log(f"[client] 对局详情抓取完成，共获取 {fetched} 条")
        if progress_callback:
            progress_callback(len(room_ids), len(room_ids))
        return fetched

    def _extract_players(self, detail: dict) -> list[dict]:
        for path in [
            ("data", "data", "players"),
            ("data", "data", "playerList"),
            ("data", "data", "memberList"),
            ("data", "players"),
            ("data", "playerList"),
        ]:
            val = self._extract_nested(detail, *path)
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                return val

        inner = self._extract_nested(detail, "data", "data")
        if isinstance(inner, dict):
            for key, val in inner.items():
                if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                    if any(
                        k in val[0]
                        for k in (
                            "playerName",
                            "PlayerName",
                            "NickName",
                            "KillCount",
                            "killCount",
                        )
                    ):
                        return val
        return []
        return []

    def fetch_room_info(
        self,
        room_ids: list[str] | None = None,
        queue: str = "sol",
        player_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> int:
        if room_ids is None:
            missing = self.db.get_records_without_detail(player_id=player_id)
            room_ids = [r["room_id"] for r in missing]

        if not room_ids:
            self._log("[client] 没有需要获取的房间详情")
            if progress_callback:
                progress_callback(0, 0)
            return 0

        queue_name = "烽火地带" if queue == "sol" else "全面战场"
        self._log(f"[client] 开始获取房间详情（{queue_name}），共 {len(room_ids)} 条")
        if progress_callback:
            progress_callback(0, len(room_ids))

        openid = self._get_openid()
        if not openid:
            self._log("[client] 无法获取 openid，跳过房间详情抓取")
            return 0

        fetched = 0
        for i, room_id in enumerate(room_ids):
            try:
                if i > 0:
                    self._pace_request(i)
                rec = self.db.conn.execute(
                    "SELECT start_time FROM Record WHERE room_id = ?", (room_id,)
                ).fetchone()
                start_time = rec["start_time"] if rec else None

                body = {
                    "from_src": "df_web",
                    "roomId": room_id,
                    "openid": openid,
                    "area": self._area,
                    "queue": queue,
                    "account_type": self._account_type,
                    "start_time": start_time or "",
                }

                data = self._wegame_post("GetBattleDetail", body)
                if data is None:
                    continue

                result = data.get("result", {})
                if isinstance(result, dict) and result.get("error_code") not in (
                    0,
                    "0",
                    None,
                ):
                    if i == 0:
                        self._log(
                            f"[client] GetBattleDetail 返回错误: {result.get('error_message', '')}"
                        )
                    continue

                battle_detail = data.get("battle_detail", {})
                players_key = "sol_players" if queue == "sol" else "tdm_players"
                player_list = battle_detail.get(players_key, [])
                if not player_list:
                    alt_key = "tdm_players" if queue == "sol" else "sol_players"
                    player_list = battle_detail.get(alt_key, [])

                if not player_list:
                    if i == 0:
                        vault_store.save_raw_json(f"room_info_debug_{room_id}.json", data)
                        self._log("[client] 房间详情返回空数据，已保存到受保护数据仓")
                    continue

                self.db.upsert_battle_detail_v2(room_id, player_list, openid)
                fetched += 1

                if i == 0:
                    vault_store.save_raw_json(f"room_info_{room_id}.json", data)

                if (i + 1) % 10 == 0 or i == len(room_ids) - 1:
                    self._log(
                        f"[client] 房间详情进度 [{i + 1}/{len(room_ids)}]，已获取 {fetched} 条"
                    )
                if progress_callback:
                    progress_callback(i + 1, len(room_ids))

            except Exception as e:
                self._log(f"[client] 房间详情错误 {room_id}: {e}")
                if progress_callback:
                    progress_callback(i + 1, len(room_ids))

        self._log(f"[client] 房间详情抓取完成，共获取 {fetched} 条")
        if progress_callback:
            progress_callback(len(room_ids), len(room_ids))
        return fetched

    def fetch_missing_details(self, player_id: str | None = None) -> dict[str, int]:
        missing_report_rows = self.db.get_records_without_report(player_id=player_id)
        missing_room_rows = self.db.get_records_without_detail(player_id=player_id)
        report_room_ids = [str(r["room_id"]) for r in missing_report_rows]
        missing_room_ids = [str(r["room_id"]) for r in missing_room_rows]
        missing_any = len(set(report_room_ids) | set(missing_room_ids))
        if not missing_any:
            self._log("[client] 没有需要补全的对局")
            return {"missing": 0, "fetched_report": 0, "fetched_room": 0}

        self._log(f"[client] 需要补全的对局数: {missing_any}")

        fetched_report = 0
        if report_room_ids:
            self._log(f"[client] 待补 BattleReport 详情: {len(report_room_ids)} 条")
            fetched_report = self.fetch_battle_details(report_room_ids)
        else:
            self._log("[client] BattleReport 详情已完整")

        fetched_room = 0
        grouped: dict[str, list[str]] = {}
        for row in missing_room_rows:
            grouped.setdefault(str(row["queue"] or "sol"), []).append(str(row["room_id"]))
        if grouped:
            for queue, queue_room_ids in grouped.items():
                queue_name = "烽火地带" if queue == "sol" else "全面战场"
                self._log(f"[client] 开始补全房间详情（{queue_name}），共 {len(queue_room_ids)} 条")
                fetched_room += self.fetch_room_info(queue_room_ids, queue=queue)
        else:
            self._log("[client] 房间详情已完整")

        return {
            "missing": missing_any,
            "fetched_report": fetched_report,
            "fetched_room": fetched_room,
        }

    # ---- main entry ----

    def fetch_all(
        self, fetch_details: bool = True, queue: str = "sol", target_count: int = 100
    ):
        self._log("=" * 50)
        self._log("Delta Force Data Center 数据抓取")
        self._log("=" * 50)

        self.fetch_maps()
        self.fetch_agents()

        openid = self._get_openid()
        if openid:
            self._log("[client] 角色信息已就绪")

        battles = self.fetch_battle_list(queue=queue, target_count=target_count)

        if fetch_details and battles:
            fetched_report = self.fetch_battle_details()
            self._log(f"[client] 本轮补到 {fetched_report} 条对局详情")

        if fetch_details:
            fetched_room = self.fetch_room_info(queue=queue)
            self._log(f"[client] 本轮补到 {fetched_room} 条房间详情")

        self._save_raw_summary()

    def _save_raw_summary(self):
        stats = self.db.get_battle_stats()
        vault_store.save_raw_json("summary.json", stats)

    def close(self):
        self.client.close()
