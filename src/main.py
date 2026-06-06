import asyncio
import json
import sys
from pathlib import Path

from . import config
from . import vault_store
from .auth import login_and_capture_cookies, load_cookies
from .client import WeGameClient
from .database import Database
from .export import (
    export_all,
    export_records_csv,
    export_items_csv,
    export_details_json,
    export_players_csv,
)
from .web import start_server


def _check_login() -> bool:
    return vault_store.has_cookies()


def _check_data() -> bool:
    return vault_store.database_exists()


def _print_header():
    print()
    print("=" * 52)
    print("        三角洲行动 · 战绩自动导出工具")
    print("=" * 52)


def _print_status():
    logged_in = _check_login()
    has_data = _check_data()
    print()
    print(f"  登录状态: {'[OK] 已登录' if logged_in else '[X]  未登录'}")

    active_name = ""
    try:
        db = Database()
        db.connect()
        acc = db.get_active_account()
        if acc:
            active_name = acc.get("player_name", "") or acc.get("player_id", "")
        if has_data:
            count = db.get_record_count()
            detail = db.get_detail_count()
            items = db.conn.execute("SELECT COUNT(*) FROM battles_items").fetchone()[0]
            print(f"  数据状态: {count} 条战绩, {detail} 条详情, {items} 件物品")
        else:
            print(f"  数据状态: 无数据")
        db.close()
    except Exception:
        if has_data:
            print(f"  数据状态: (读取中)")
        else:
            print(f"  数据状态: 无数据")

    if active_name:
        print(f"  当前账号: {active_name}")
    print()


def _print_menu():
    print("-" * 52)
    print("  [1] 登录 WeGame         (QQ扫码)")
    print("  [2] 获取战绩 - 烽火地带  (sol)")
    print("  [3] 获取战绩 - 全面战场  (mp)")
    print("  [4] 补全对局详情         (已有列表但缺详情)")
    print("  [5] 导出数据             (CSV + JSON)")
    print("  [6] 查看统计")
    print("  [7] 拦截模式             (浏览器浏览时自动捕获)")
    print("  [8] 启动 Web 面板        (浏览器查看数据)")
    print("  [9] 清空数据             (清空战绩和物品)")
    print("  [a] 切换账号             (查看/切换已登录账号)")
    print("  [d] 退出登录             (清除凭证，切换账号)")
    print("  [0] 退出")
    print("-" * 52)


def _prompt_choice(prompt: str, choices: list[str]) -> str:
    while True:
        raw = input(prompt).strip()
        if raw in choices:
            return raw
        print(f"  无效输入，请选择 {choices}")


def _prompt_confirm(prompt: str) -> bool:
    raw = input(prompt).strip().lower()
    return raw in ("y", "yes", "是")


def _cleanup_credentials():
    vault_store.clear_sensitive_runtime_files()


def _do_login():
    print()
    print("[登录] 将打开浏览器，请使用 QQ 扫码登录")
    print("[登录] 登录完成后会自动保存凭证")
    _cleanup_credentials()
    try:
        asyncio.run(login_and_capture_cookies())
        print("[登录] 完成!")
    except Exception as e:
        print(f"[登录] 失败: {e}")


def _do_switch_account():
    if not _check_data():
        print("\n  [X] 数据库中没有数据，请先获取战绩")
        return

    db = Database()
    db.connect()
    try:
        accounts = db.get_all_accounts()
        if not accounts:
            print("\n  没有已记录的账号，请先登录并获取战绩")
            return

        print("\n  已记录的账号:")
        print(f"  {'序号':<4} {'昵称':<20} {'PlayerID':<24} {'状态'}")
        print(f"  {'-' * 60}")
        for i, acc in enumerate(accounts):
            name = acc.get("player_name", "") or "(未知)"
            pid = acc.get("player_id", "")
            active = "★ 当前" if acc.get("is_active") else ""
            print(f"  {i + 1:<4} {name:<20} {pid:<24} {active}")

        active = db.get_active_account()
        if active:
            active_name = active.get("player_name", "") or active.get("player_id", "")
            print(f"\n  当前活跃账号: {active_name}")

        raw = input("\n  输入序号切换账号 (直接回车取消): ").strip()
        if not raw:
            print("  已取消")
            return
        try:
            idx = int(raw) - 1
        except ValueError:
            print("  无效输入")
            return
        if idx < 0 or idx >= len(accounts):
            print("  序号超出范围")
            return

        target = accounts[idx]
        if target.get("is_active"):
            print(
                f"  已经是当前账号: {target.get('player_name', '') or target.get('player_id', '')}"
            )
            return

        db.set_active_account(target["player_id"])
        target_name = target.get("player_name", "") or target.get("player_id", "")
        print(f"  [OK] 已切换到: {target_name}")
    finally:
        db.close()


def _do_logout():
    print()
    _cleanup_credentials()
    print("[退出登录] 已清除登录凭证")
    if _prompt_confirm("  是否立即登录新账号? [y/N] "):
        _do_login()


def _prompt_int(prompt: str, default: int) -> int:
    raw = input(prompt).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _do_fetch(queue: str):
    if not _check_login():
        print("\n  [X] 请先登录 (选项 1)")
        return

    queue_label = "烽火地带" if queue == "sol" else "全面战场"
    print(f"\n[获取] 获取 {queue_label} 战绩")

    count = _prompt_int("  输入获取数量 (直接回车默认100): ", 100)
    if count <= 0:
        count = 100

    try:
        cookies = load_cookies()
    except FileNotFoundError:
        print("  [X] Cookie 文件丢失，请重新登录")
        return

    db = Database()
    db.connect()
    try:
        client = WeGameClient(cookies, db)
        client.fetch_all(fetch_details=True, queue=queue, target_count=count)
        client.close()

        _print_stats(db)

        if _prompt_confirm("\n是否立即导出数据? [y/N] "):
            pid = db.get_active_player_id()
            export_all(db, player_id=pid)
    finally:
        db.close()


def _do_fetch_details():
    if not _check_data():
        print("\n  [X] 数据库中没有数据，请先获取战绩")
        return

    if not _check_login():
        print("\n  [X] 请先登录 (选项 1)")
        return

    db = Database()
    db.connect()
    try:
        missing_report = db.get_records_without_report()
        missing_room = db.get_records_without_detail()
        missing_count = len(
            {r["room_id"] for r in missing_report} | {r["room_id"] for r in missing_room}
        )
        if not missing_count:
            print("\n  所有对局详情已完整，无需补全")
            return
        print(f"\n[补全] 有 {missing_count} 条对局缺少详情，开始获取...")

        cookies = load_cookies()
        client = WeGameClient(cookies, db)
        result = client.fetch_missing_details()
        client.close()
        print(
            f"[补全] 完成，获取 {result['fetched_report']} 条详情, "
            f"{result['fetched_room']} 条房间详情"
        )
    finally:
        db.close()


def _do_export():
    if not _check_data():
        print("\n  [X] 数据库中没有数据，请先获取战绩")
        return

    db = Database()
    db.connect()
    try:
        pid = db.get_active_player_id()
        export_all(db, player_id=pid)
    finally:
        db.close()


def _do_clear():
    if not _check_data():
        print("\n  数据库中没有数据")
        return

    db = Database()
    db.connect()
    try:
        record_count = db.get_record_count()
        item_count = db.conn.execute("SELECT COUNT(*) FROM battles_items").fetchone()[0]
        print(f"\n  当前数据: {record_count} 条战绩, {item_count} 件物品")
        print("  [!] 此操作将清空 Record 和 battles_items 表，不可恢复!")
        if not _prompt_confirm("  确认清空? 输入 y 确认: "):
            print("  已取消")
            return
        if not _prompt_confirm("  再次确认? 输入 y 确认: "):
            print("  已取消")
            return
        db.clear_records()
        print("  [OK] 数据已清空")
    finally:
        db.close()


def _do_stats():
    if not _check_data():
        print("\n  [X] 数据库中没有数据，请先获取战绩")
        return

    db = Database()
    db.connect()
    try:
        _print_stats(db)
    finally:
        db.close()


def _do_web(port: int = 8080, open_browser: bool = True):
    db = Database()
    db.connect()
    try:
        start_server(port=port, db=db, open_browser=open_browser)
    finally:
        db.close()


def _do_intercept():
    print("\n[拦截] 启动浏览器拦截模式...")
    print("[拦截] 请在浏览器中浏览战绩页面，数据会被自动捕获")
    print("[拦截] 按 Ctrl+C 停止\n")
    try:
        asyncio.run(_run_intercept_mode())
    except KeyboardInterrupt:
        print("\n[拦截] 已停止")


def _print_stats(db: Database):
    pid = db.get_active_player_id()
    stats = db.get_battle_stats(player_id=pid)
    total = stats.get("total", 0)
    if total == 0:
        print("\n  数据库中没有战绩数据")
        return

    try:
        item_count = db.conn.execute(
            "SELECT COUNT(*) FROM battles_items bi JOIN Record r ON bi.room_id = r.room_id"
            + (" WHERE r.player_id = ?" if pid else ""),
            ([pid] if pid else []),
        ).fetchone()[0]
    except Exception:
        item_count = 0

    escaped = stats.get("escaped", 0) or 0
    avg_dur = stats.get("avg_duration", 0) or 0
    m, s = divmod(int(avg_dur), 60)

    detail_where = " WHERE r.player_id = ?" if pid else ""
    detail_args = [pid] if pid else []
    try:
        detail_count = db.conn.execute(
            f"SELECT COUNT(*) FROM battle_details bd JOIN Record r ON bd.room_id = r.room_id{detail_where}",
            detail_args,
        ).fetchone()[0]
    except Exception:
        detail_count = 0
    try:
        missing = db.conn.execute(
            f"SELECT COUNT(*) FROM Record r WHERE NOT EXISTS (SELECT 1 FROM BattleDetail bd WHERE bd.room_id = r.room_id){' AND r.player_id = ?' if pid else ''}",
            detail_args,
        ).fetchone()[0]
    except Exception:
        missing = 0
    try:
        bd_count = db.conn.execute(
            f"SELECT COUNT(DISTINCT bd.room_id) FROM BattleDetail bd JOIN Record r ON bd.room_id = r.room_id{detail_where}",
            detail_args,
        ).fetchone()[0]
    except Exception:
        bd_count = 0

    print()
    print(f"  战绩统计:")
    print(f"  {'-' * 40}")
    print(f"  总对局数:   {total}")
    print(f"  成功撤离:   {escaped} ({escaped / total * 100:.1f}%)")
    print(f"  总击杀:     {stats.get('total_kills', 0) or 0}")
    print(f"  玩家击杀:   {stats.get('total_player_kills', 0) or 0}")
    print(f"  平均时长:   {m}分{s}秒")
    print(f"  平均盈亏:   {(stats.get('avg_profit', 0) or 0):,.0f}")
    print(f"  总盈亏:     {(stats.get('total_profit', 0) or 0):,.0f}")
    print(f"  详情已获取: {detail_count}/{total} (缺失 {missing})")
    print(f"  房间详情:   {bd_count}/{total}")
    print(f"  带出物品:   {item_count} 件")
    print()


async def _run_intercept_mode():
    from playwright.async_api import async_playwright

    db = Database()
    db.connect()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            captured_data: dict[str, list] = {
                "battles": [],
                "battle_details": [],
                "maps": [],
                "agents": [],
            }

            async def on_response(response):
                url = response.url
                try:
                    if not ("comm.ams.game.qq.com" in url or "wegame.com.cn" in url):
                        return
                    if response.request.method not in ("GET", "POST"):
                        return

                    lower_url = url.lower()
                    if "map" in lower_url and "battle" not in lower_url:
                        data = await response.json()
                        maps = _extract_list_from_response(data)
                        if maps:
                            captured_data["maps"].extend(maps)
                            print(f"  [捕获] 地图: {len(maps)} 条")

                    elif "agent" in lower_url:
                        data = await response.json()
                        agents = _extract_list_from_response(data)
                        if agents:
                            captured_data["agents"].extend(agents)
                            print(f"  [捕获] 干员: {len(agents)} 条")

                    elif "battlelist" in lower_url or "battle_list" in lower_url:
                        data = await response.json()
                        battles = _extract_battles_from_response(data)
                        if battles:
                            captured_data["battles"].extend(battles)
                            db.upsert_records(battles)
                            print(f"  [捕获] 战绩: {len(battles)} 条")

                    elif "battledetail" in lower_url or "battle_detail" in lower_url:
                        data = await response.json()
                        captured_data["battle_details"].append(data)
                        print(f"  [捕获] 详情: 1 条")

                except Exception:
                    pass

            page.on("response", on_response)

            await page.goto(config.WEGAME_HOME, wait_until="networkidle")

            try:
                while True:
                    await page.wait_for_timeout(1000)
            except KeyboardInterrupt:
                print("\n[拦截] 正在停止...")

            cookies = await context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies}
            vault_store.save_cookies(cookie_dict)

            for detail in captured_data["battle_details"]:
                raw = detail
                jdata = raw.get("jData", raw)
                room_id = ""
                if isinstance(jdata, dict):
                    data = jdata.get("data", jdata)
                    if isinstance(data, dict):
                        data2 = data.get("data", data)
                        if isinstance(data2, dict):
                            room_id = str(
                                data2.get("RoomId", data2.get("BattleId", ""))
                            )
                if room_id:
                    db.upsert_battle_detail(room_id, raw)

            await browser.close()

        print(f"\n[拦截] 捕获统计:")
        print(f"  地图: {len(captured_data['maps'])} 条")
        print(f"  干员: {len(captured_data['agents'])} 条")
        print(f"  战绩: {len(captured_data['battles'])} 条")
        print(f"  详情: {len(captured_data['battle_details'])} 条")

    finally:
        db.close()


def _extract_list_from_response(data: dict) -> list:
    if isinstance(data, list):
        return data
    jdata = data.get("jData", data)
    if isinstance(jdata, dict):
        for key in ["data"]:
            if key in jdata:
                val = jdata[key]
                if isinstance(val, dict):
                    for inner_key in ["data", "list", "maps", "agents"]:
                        if inner_key in val and isinstance(val[inner_key], list):
                            return val[inner_key]
                if isinstance(val, list):
                    return val
    return []


def _extract_battles_from_response(data: dict) -> list:
    jdata = data.get("jData", data)
    if isinstance(jdata, dict):
        inner = jdata.get("data", jdata)
        if isinstance(inner, dict):
            inner2 = inner.get("data", inner)
            if isinstance(inner2, list):
                return inner2
            if isinstance(inner2, dict):
                return inner2.get("data", inner2.get("list", []))
        if isinstance(inner, list):
            return inner
    return []


def main():
    if len(sys.argv) == 1:
        _do_web()
        return
    _run_cli()


def _run_cli():
    import argparse

    parser = argparse.ArgumentParser(
        prog="delta-force-data-center",
        description="三角洲行动战绩自动导出工具",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("login")
    fp = sub.add_parser("fetch")
    fp.add_argument("--no-details", action="store_true")
    fp.add_argument("--export", action="store_true")
    fp.add_argument("-q", "--queue", choices=["sol", "mp"], default="sol")
    fp.add_argument("-n", "--count", type=int, default=100, help="获取数量 (默认100)")

    ep = sub.add_parser("export")
    ep.add_argument(
        "-f",
        "--format",
        choices=["all", "csv", "json", "players", "items"],
        default="all",
    )
    ep.add_argument("-o", "--output")

    sub.add_parser("stats")
    sub.add_parser("intercept")
    sub.add_parser("logout")
    wp = sub.add_parser("web")
    wp.add_argument("-p", "--port", type=int, default=8080, help="端口号 (默认8080)")
    wp.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    args = parser.parse_args()

    if args.command == "login":
        _do_login()
    elif args.command == "fetch":
        try:
            cookies = load_cookies()
        except FileNotFoundError:
            print("请先运行 delta-force-data-center login 登录")
            sys.exit(1)
        db = Database()
        db.connect()
        try:
            client = WeGameClient(cookies, db)
            client.fetch_all(
                fetch_details=not args.no_details,
                queue=args.queue,
                target_count=args.count,
            )
            client.close()
            _print_stats(db)
            if args.export:
                export_all(db)
        finally:
            db.close()
    elif args.command == "export":
        db = Database()
        db.connect()
        try:
            output_dir = Path(args.output) if args.output else config.DATA_DIR
            if args.format == "all":
                export_all(db, output_dir)
            elif args.format == "csv":
                export_records_csv(db, output_dir / "records.csv")
            elif args.format == "json":
                export_details_json(db, output_dir / "battle_details.json")
            elif args.format == "players":
                export_players_csv(db, output_dir / "players.csv")
            elif args.format == "items":
                export_items_csv(db, output_dir / "battles_items.csv")
        finally:
            db.close()
    elif args.command == "stats":
        _do_stats()
    elif args.command == "intercept":
        _do_intercept()
    elif args.command == "web":
        _do_web(port=args.port, open_browser=not args.no_browser)
    elif args.command == "logout":
        _do_logout()
    else:
        parser.print_help()


def _run_interactive():
    _print_header()
    _print_status()

    dispatch = {
        "1": _do_login,
        "2": lambda: _do_fetch("sol"),
        "3": lambda: _do_fetch("mp"),
        "4": _do_fetch_details,
        "5": _do_export,
        "6": _do_stats,
        "7": _do_intercept,
        "8": _do_web,
        "9": _do_clear,
        "a": _do_switch_account,
        "d": _do_logout,
    }

    while True:
        _print_menu()
        choice = _prompt_choice(
            "  请选择 [0-9,a,d]: ",
            ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "d"],
        )

        if choice == "0":
            print("\n  再见!")
            break

        dispatch[choice]()

        print()
        input("  按 Enter 继续...")
        _print_header()
        _print_status()


app = main

if __name__ == "__main__":
    main()
