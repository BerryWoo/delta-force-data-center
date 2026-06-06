import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright, Page, BrowserContext

from . import config
from . import vault_store


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


def _resolve_packaged_chromium_executable() -> Path | None:
    browser_root = config.BASE_DIR / "ms-playwright"
    if not browser_root.exists():
        return None

    browser_meta = (
        config.BASE_DIR
        / "_internal"
        / "playwright"
        / "driver"
        / "package"
        / "browsers.json"
    )
    revision = ""
    try:
        if browser_meta.exists():
            data = json.loads(browser_meta.read_text(encoding="utf-8"))
            for item in data.get("browsers", []):
                if str(item.get("name") or "") == "chromium":
                    revision = str(item.get("revision") or "").strip()
                    break
    except Exception:
        revision = ""

    candidates: list[Path] = []
    if revision:
        chromium_dir = browser_root / f"chromium-{revision}"
        candidates.extend(
            [
                chromium_dir / "chrome-win" / "chrome.exe",
                chromium_dir / "chrome-win64" / "chrome.exe",
            ]
        )

    for chrom_dir in sorted(browser_root.glob("chromium-*")):
        candidates.extend(
            [
                chrom_dir / "chrome-win" / "chrome.exe",
                chrom_dir / "chrome-win64" / "chrome.exe",
            ]
        )

    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            return path
    return None


async def login_and_capture_cookies(cookie_path: Path | None = None) -> dict[str, str]:
    cookie_path = cookie_path or config.COOKIE_PATH

    async with async_playwright() as p:
        launch_kwargs = {
            "headless": False,
            "args": ["--start-maximized"],
        }
        executable_path = _resolve_packaged_chromium_executable()
        if executable_path is not None:
            launch_kwargs["executable_path"] = str(executable_path)
        browser = await p.chromium.launch(
            **launch_kwargs,
        )
        context = await browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        captured_endpoints: dict[str, str] = {}
        captured_bodies: dict[str, str] = {}
        api_status: dict[str, dict] = {}
        _install_context_page_hooks(
            context, captured_endpoints, captured_bodies, api_status
        )
        page = await context.new_page()
        _setup_interception(page, captured_endpoints, captured_bodies, api_status)

        _safe_print("[auth] 正在打开 WeGame 三角洲行动助手...")
        _safe_print("[auth] 请在浏览器中扫码登录 QQ 账号")
        _safe_print("[auth] 程序会持续等待，直到检测到有效登录")

        await _goto_wegame_entry(page)
        await _bring_login_page_to_front(page)
        await page.wait_for_timeout(300)

        login_ok = await _wait_for_login(page)

        if not login_ok:
            await browser.close()
            raise RuntimeError("登录窗口已关闭，请重新发起登录")

        _safe_print("[auth] 已检测到登录动作，正在跳转到战绩页面校验登录状态...")

        vault_store.clear_sensitive_runtime_files()

        score_page = await _open_score_detail_page(
            context, captured_endpoints, captured_bodies, api_status
        )
        await _wait_for_valid_session(score_page, api_status)
        _safe_print("[auth] 已确认登录有效，开始保存凭证...")

        cookies = await context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}

        vault_store.save_cookies(cookie_dict)
        vault_store.save_endpoints(captured_endpoints)

        if captured_bodies:
            vault_store.save_request_bodies(captured_bodies)
            _safe_print(f"[auth] 捕获 {len(captured_bodies)} 个请求体示例")

        _safe_print("[auth] Cookies 已保存到受保护本地数据仓")
        _safe_print(f"[auth] 发现 {len(captured_endpoints)} 个 API 端点")

        await browser.close()

    return cookie_dict


def _setup_interception(
    page: Page,
    captured_endpoints: dict[str, str],
    captured_bodies: dict[str, str] | None = None,
    api_status: dict[str, dict] | None = None,
):
    async def on_response(response):
        url = response.url
        try:
            if "comm.ams.game.qq.com" in url or (
                "wegame.com.cn" in url and "/api/" in url
            ):
                method = response.request.method
                path = url.split("?")[0]
                key = f"{method}:{path}"
                if key not in captured_endpoints:
                    captured_endpoints[key] = url
                    _safe_print(f"  [intercept] {method} {path}")

                if captured_bodies is not None and method == "POST":
                    try:
                        body = response.request.post_data
                        if body:
                            action = path.split("/")[-1]
                            captured_bodies[action] = body
                    except Exception:
                        pass

                if api_status is not None and "/api/" in path:
                    action = path.split("/")[-1]
                    if action in {"GetRoleInfo", "GetBattleList", "GetBattleReport", "GetBattleDetail"}:
                        try:
                            data = await response.json()
                            result = data.get("result", {}) if isinstance(data, dict) else {}
                            err = result.get("error_code") if isinstance(result, dict) else None
                            api_status[action] = {
                                "ok": err in (0, "0", None),
                                "error_code": err,
                                "error_message": result.get("error_message", "") if isinstance(result, dict) else "",
                            }
                        except Exception:
                            pass
        except Exception:
            pass

    page.on("response", on_response)


def _install_context_page_hooks(
    context: BrowserContext,
    captured_endpoints: dict[str, str],
    captured_bodies: dict[str, str] | None = None,
    api_status: dict[str, dict] | None = None,
) -> None:
    def focus_later(new_page: Page):
        _setup_interception(
            new_page, captured_endpoints, captured_bodies, api_status
        )

        async def run():
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            await _bring_login_page_to_front(new_page)

        try:
            asyncio.create_task(run())
        except RuntimeError:
            pass

    context.on("page", focus_later)


async def _open_score_detail_page(
    context: BrowserContext,
    captured_endpoints: dict[str, str],
    captured_bodies: dict[str, str] | None = None,
    api_status: dict[str, dict] | None = None,
) -> Page:
    score_detail = config.WEGAME_SCORE_DETAIL.rstrip("/")
    for candidate in reversed(context.pages):
        if candidate.is_closed():
            continue
        try:
            if candidate.url.rstrip("/").startswith(score_detail):
                await _bring_login_page_to_front(candidate)
                await candidate.wait_for_timeout(1500)
                return candidate
        except Exception:
            continue

    _safe_print("[auth] 为避免广告页干扰，正在新开战绩页校验登录状态...")
    score_page = await context.new_page()
    _setup_interception(score_page, captured_endpoints, captured_bodies, api_status)
    try:
        await score_page.goto(config.WEGAME_SCORE_DETAIL, wait_until="domcontentloaded")
    except Exception:
        await score_page.goto(config.WEGAME_SCORE_DETAIL)
    await _bring_login_page_to_front(score_page)
    await score_page.wait_for_timeout(1500)
    return score_page


async def _goto_wegame_entry(page: Page) -> None:
    targets = [
        ("助手首页", config.WEGAME_HOME),
        ("战绩页", config.WEGAME_SCORE_DETAIL),
    ]
    last_error: Exception | None = None
    for label, url in targets:
        try:
            _safe_print(f"[auth] 正在打开 WeGame {label}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return
        except Exception as exc:
            last_error = exc
            _safe_print(f"[auth] 打开 WeGame {label}失败，尝试下一个入口: {exc}")
    if last_error is not None:
        raise last_error


async def _bring_context_pages_to_front(context: BrowserContext) -> None:
    for candidate in reversed(context.pages):
        if candidate.is_closed():
            continue
        await _bring_login_page_to_front(candidate)
        return


async def _bring_login_page_to_front(page: Page) -> None:
    try:
        await page.bring_to_front()
    except Exception:
        pass
    if sys.platform != "win32":
        return
    try:
        title = await page.title()
    except Exception:
        title = ""
    _force_windows_browser_to_front(title)


def _force_windows_browser_to_front(page_title: str = "") -> None:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.EnumWindows.argtypes = [
        ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM),
        wintypes.LPARAM,
    ]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetActiveWindow.argtypes = [wintypes.HWND]
    enum_windows_proc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
    )
    visible_windows: list[tuple[int, str]] = []

    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        visible_windows.append((int(hwnd), buffer.value))
        return True

    try:
        user32.EnumWindows(enum_windows_proc(enum_proc), 0)
    except Exception:
        return

    title = (page_title or "").strip()
    keywords = [k for k in [title, "WeGame", "三角洲", "QQ登录", "扫码登录"] if k]
    hwnd = None
    for candidate, window_title in visible_windows:
        if any(keyword in window_title for keyword in keywords):
            hwnd = candidate
            break
    if not hwnd:
        for candidate, window_title in reversed(visible_windows):
            if "Chromium" in window_title or "Chrome" in window_title:
                hwnd = candidate
                break
    if not hwnd:
        return

    SW_RESTORE = 9
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002
    FLASHW_ALL = 0x00000003
    FLASHW_TIMERNOFG = 0x0000000C

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]

    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.ShowWindowAsync(hwnd, SW_RESTORE)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        user32.SetWindowPos(
            hwnd,
            HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )

        current_thread = kernel32.GetCurrentThreadId()
        foreground = user32.GetForegroundWindow()
        foreground_thread = (
            user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        )
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        if foreground_thread and foreground_thread != current_thread:
            user32.AttachThreadInput(current_thread, foreground_thread, True)
        if target_thread and target_thread != current_thread:
            user32.AttachThreadInput(current_thread, target_thread, True)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
        user32.SwitchToThisWindow(hwnd, True)
        user32.BringWindowToTop(hwnd)
        user32.SetActiveWindow(hwnd)
        flash = FLASHWINFO(
            ctypes.sizeof(FLASHWINFO),
            hwnd,
            FLASHW_ALL | FLASHW_TIMERNOFG,
            3,
            0,
        )
        user32.FlashWindowEx(ctypes.byref(flash))
    except Exception:
        pass
    finally:
        try:
            if "target_thread" in locals() and target_thread and target_thread != current_thread:
                user32.AttachThreadInput(current_thread, target_thread, False)
            if (
                "foreground_thread" in locals()
                and foreground_thread
                and foreground_thread != current_thread
            ):
                user32.AttachThreadInput(current_thread, foreground_thread, False)
        except Exception:
            pass


async def _wait_for_login(page: Page) -> bool:
    async def looks_logged_in() -> bool:
        if page.is_closed():
            return False
        try:
            login_count = await page.locator(".button-login-text").count()
            if login_count == 0:
                content = await page.content()
                if "请登录" not in content or "已登录" in content:
                    return True
        except Exception:
            pass
        return False

    async def try_click_login() -> bool:
        selectors = [
            ".button-login-text",
            ".button-login",
            "button:has-text('登录')",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                await locator.click(timeout=3000)
                await page.wait_for_timeout(500)
                await _bring_context_pages_to_front(page.context)
                return True
            except Exception:
                continue
        return False

    idle_round = 0
    click_cooldown = 0
    while not page.is_closed():
        if await looks_logged_in():
            return True
        idle_round += 1
        click_cooldown += 1
        if idle_round in (1, 3, 8) or idle_round % 20 == 0:
            await _bring_context_pages_to_front(page.context)
        if click_cooldown >= 5 and await try_click_login():
            _safe_print("[auth] 检测到未登录，尝试点击登录按钮...")
            click_cooldown = 0
        if idle_round % 20 == 0:
            _safe_print("[auth] 尚未完成登录，继续等待用户操作...")
        await page.wait_for_timeout(1000)
    return False


async def _wait_for_valid_session(page: Page, api_status: dict[str, dict]) -> bool:
    wait_round = 0
    while not page.is_closed():
        role_state = api_status.get("GetRoleInfo")
        if role_state and role_state.get("ok"):
            return True

        err_code = str(role_state.get("error_code")) if role_state else ""
        err_msg = str(role_state.get("error_message", "")) if role_state else ""
        if err_code == "8000120" or "登录信息过期" in err_msg:
            if wait_round % 10 == 0:
                _safe_print("[auth] 当前登录尚未生效，检测到登录信息过期，继续等待用户完成登录...")

        wait_round += 1
        if wait_round % 15 == 0 and not role_state:
            _safe_print("[auth] 正在等待战绩页返回有效账号信息...")
        await page.wait_for_timeout(1000)

    raise RuntimeError("登录窗口已关闭，请重新发起登录")


def load_cookies(cookie_path: Path | None = None) -> dict[str, str]:
    if cookie_path is not None and cookie_path.exists():
        return json.loads(cookie_path.read_text(encoding="utf-8"))
    cookies = vault_store.load_cookies()
    if not cookies:
        raise FileNotFoundError(
            "Cookie 文件不存在\n请先运行 delta-force-data-center login 登录"
        )
    return cookies
