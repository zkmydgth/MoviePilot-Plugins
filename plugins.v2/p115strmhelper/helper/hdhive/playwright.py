__all__ = ["HDHivePlaywrightClient", "HDHiveLoginError"]

from platform import machine as _machine
from re import match as re_match, search as re_search
from sys import platform
from time import sleep
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from httpx import Client
from orjson import dumps as orjson_dumps, loads as orjson_loads

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from app.core.config import settings


class HDHiveLoginError(Exception):
    """
    HDHive 网页登录失败或超时
    """


class HDHivePlaywrightClient:
    """
    HDHive 站点 Playwright 客户端
    """

    DEFAULT_BASE_URL = "https://hdhive.com"
    LOGIN_PAGE = "/login"
    _CHROME_UA_SUFFIX = (
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(self, headless: bool = True) -> None:
        """
        :param headless: Playwright 是否无头模式
        """
        self._headless = headless
        self._cookie_str: Optional[str] = None

    @staticmethod
    def _build_ua() -> str:
        """
        构造与当前运行平台匹配的 Chrome User-Agent

        :return: 用于 BrowserContext 的 UA 字符串
        """
        m = _machine().lower()
        arm_like = "arm" in m or "aarch" in m
        if platform == "linux":
            arch = "aarch64" if arm_like else "x86_64"
            product = f"X11; Linux {arch}"
        elif platform == "win32":
            product = (
                "Windows NT 10.0; ARM64" if arm_like else "Windows NT 10.0; Win64; x64"
            )
        else:
            product = "Macintosh; Intel Mac OS X 10_15_7"
        return f"Mozilla/5.0 ({product}) {HDHivePlaywrightClient._CHROME_UA_SUFFIX}"

    @staticmethod
    def _chromium_launch_args() -> list[str]:
        """
        返回 Chromium 进程启动参数

        :return: 传给 chromium.launch(args=...) 的参数列表
        """
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ]
        if platform == "linux":
            args.extend(
                [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                ]
            )
        return args

    @staticmethod
    def _proxy_url_from_settings() -> Optional[str]:
        """
        从 settings.PROXY 得到单一代理 URL

        :return: http(s)://... 字符串，未配置或无法解析时为 None
        """
        p = settings.PROXY
        if not p:
            return None
        if isinstance(p, str):
            return p
        if isinstance(p, dict):
            u = p.get("https") or p.get("http")
            return str(u) if u else None
        return None

    @staticmethod
    def _playwright_proxy_settings() -> Optional[Dict[str, str]]:
        """
        将 MoviePilot settings.PROXY 转为 Playwright chromium.launch 的 proxy 参数

        :return: 含 server，可选 username / password，无代理时为 None
        """
        raw = HDHivePlaywrightClient._proxy_url_from_settings()
        if not raw:
            return None
        u = urlparse(raw)
        if not u.scheme or not u.hostname:
            return None
        port = u.port
        if port is None:
            port = 443 if u.scheme == "https" else 80
        server = f"{u.scheme}://{u.hostname}:{port}"
        pw: Dict[str, str] = {"server": server}
        if u.username:
            pw["username"] = unquote(u.username)
        if u.password:
            pw["password"] = unquote(u.password)
        return pw

    @staticmethod
    def _chromium_launch_kwargs(headless: bool) -> Dict[str, Any]:
        """
        组装 chromium.launch 参数（含全局代理）

        :param headless: 是否无头模式
        :return: 传给 launch 的关键字参数
        """
        kwargs: Dict[str, Any] = {
            "headless": headless,
            "args": HDHivePlaywrightClient._chromium_launch_args(),
        }
        proxy = HDHivePlaywrightClient._playwright_proxy_settings()
        if proxy:
            kwargs["proxy"] = proxy
        return kwargs

    @staticmethod
    def _make_context(
        pw: Playwright,
        headless: bool = True,
    ) -> tuple[Browser, BrowserContext]:
        """
        启动浏览器并创建带语言、时区与视口的上下文

        :param pw: sync_playwright() 返回的 Playwright 实例
        :param headless: 是否无头模式
        :return: (browser, context)
        """
        browser = pw.chromium.launch(
            **HDHivePlaywrightClient._chromium_launch_kwargs(headless),
        )
        context = browser.new_context(
            user_agent=HDHivePlaywrightClient._build_ua(),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 720},
        )
        return browser, context

    @staticmethod
    def _parse_cookie_str(cookie_str: str) -> dict[str, str]:
        """
        解析 name=value; ... 格式的 Cookie 字符串

        :param cookie_str: Cookie 头字符串
        :return: 名称到值的映射
        """
        cookies: dict[str, str] = {}
        for item in cookie_str.split(";"):
            if "=" in item:
                name, value = item.strip().split("=", 1)
                cookies[name.strip()] = value.strip()
        return cookies

    def _fetch_action_hash_via_playwright(self) -> Optional[str]:
        """
        用 Playwright 打开首页，拦截 /_next/static/chunks/*.js 响应，
        在含 createServerReference 与 checkIn 的 chunk 中解析 Server Action hash

        匹配形态: createServerReference)("<hash>", ..., "checkIn")

        :return: 十六进制 hash，失败为 None
        """
        if not self._cookie_str:
            return None
        root = HDHivePlaywrightClient.DEFAULT_BASE_URL
        found_hash: list[str] = []

        def on_response(response: Response) -> None:
            if found_hash:
                return
            url = response.url
            if "_next/static/chunks" not in url or not url.endswith(".js"):
                return
            try:
                body = response.body().decode("utf-8", errors="ignore")
            except Exception:
                return
            m = re_search(
                r'createServerReference\)[(\s]*"([0-9a-f]{40,})"[^"]*"checkIn"',
                body,
            )
            if m:
                found_hash.append(m.group(1))

        try:
            cookies = HDHivePlaywrightClient._parse_cookie_str(self._cookie_str)
            domain = root.replace("https://", "").replace("http://", "")

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    **HDHivePlaywrightClient._chromium_launch_kwargs(
                        self._headless,
                    ),
                )
                context = browser.new_context(
                    user_agent=HDHivePlaywrightClient._build_ua(),
                )
                for name, value in cookies.items():
                    context.add_cookies(
                        [{"name": name, "value": value, "domain": domain, "path": "/"}]
                    )
                page = context.new_page()
                page.on("response", on_response)
                page.goto(root, wait_until="networkidle", timeout=30000)
                browser.close()
        except Exception:
            pass

        return found_hash[0] if found_hash else None

    @staticmethod
    def _checkin_parse_rsc_result(text: str) -> Optional[Dict[str, Any]]:
        """
        解析 Next.js RSC 流式响应（形如 <idx>:<json> 的逐行文本）

        跳过元数据帧；若存在 error 包裹则解包

        :param text: 响应体文本
        :return: 解析出的字典，无法解析则为 None
        """
        for line in text.splitlines():
            m = re_match(r"^\d+:(\{.*\})\s*$", line)
            if not m:
                continue
            try:
                obj = orjson_loads(m.group(1))
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            if set(obj.keys()) <= {"a", "f", "b", "q", "i"}:
                continue
            if "error" in obj and isinstance(obj["error"], dict):
                return obj["error"]
            return obj
        return None

    @staticmethod
    def _checkin_payload_dict(result: Dict[str, Any]) -> Dict[str, Any]:
        """
        将解析结果规范为含 success / message 的一层字典

        部分响应为 {"response": {"success": true, "message": "..."}}，需展开内层
        """
        inner = result.get("response")
        if isinstance(inner, dict):
            return inner
        return result

    def _fill_and_submit(
        self,
        page: Page,
        username: str,
        password: str,
    ) -> bool:
        """
        打开登录页、填写账号密码并提交，等待离开 /login

        :param page: 新开的页面
        :param username: 登录用户名或邮箱
        :param password: 登录密码
        :return: 若 URL 在超时内离开登录页则为 True
        :raises HDHiveLoginError: 等待跳转超时
        """
        root = HDHivePlaywrightClient.DEFAULT_BASE_URL
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page.goto(
            f"{root}{HDHivePlaywrightClient.LOGIN_PAGE}",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        page.wait_for_selector("input", timeout=15000)

        user_selectors = [
            "input[name='username']",
            "input[name='email']",
            "input[type='email']",
            "input[placeholder*='邮箱']",
            "input[placeholder*='email']",
            "input[placeholder*='用户名']",
        ]
        for sel in user_selectors:
            try:
                if page.query_selector(sel):
                    page.locator(sel).type(username, delay=60)
                    break
            except Exception:
                continue

        pwd_selectors = [
            "input[name='password']",
            "input[type='password']",
            "input[placeholder*='密码']",
        ]
        for sel in pwd_selectors:
            try:
                if page.query_selector(sel):
                    page.locator(sel).type(password, delay=60)
                    break
            except Exception:
                continue

        sleep(0.3)
        try:
            btn = (
                page.query_selector("button[type='submit']")
                or page.query_selector("button:has-text('登录')")
                or page.query_selector("button:has-text('Login')")
            )
            if btn:
                btn.click()
            else:
                page.keyboard.press("Enter")
        except Exception:
            page.keyboard.press("Enter")

        try:
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
            return True
        except PlaywrightTimeoutError:
            page.screenshot(path="/config/hdhive_login_debug.png")
            raise HDHiveLoginError(
                f"登录超时，当前 URL: {page.url}，页面标题: {page.title()}"
            )

    def checkin(
        self,
        gamble: bool,
        action_hash: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        签到请求

        :param gamble: 是否赌狗签到
        :param action_hash: 已知的 action hash，为空则尝试自动发现
        :return: (是否成功, 展示用文案或错误信息)
        """
        if not self._cookie_str:
            return False, "请先 login 或传入 Cookie"

        root = HDHivePlaywrightClient.DEFAULT_BASE_URL
        cookies = HDHivePlaywrightClient._parse_cookie_str(self._cookie_str)
        token = cookies.get("token")
        if not token:
            return False, "Cookie missing 'token'"

        resolved_hash = action_hash or self._fetch_action_hash_via_playwright()
        if not resolved_hash:
            return False, "无法获取 action hash，签到中止"

        ua = HDHivePlaywrightClient._build_ua()
        headers = {
            "User-Agent": ua,
            "Accept": "text/x-component",
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": root,
            "Referer": f"{root}/",
            "next-action": resolved_hash,
            "Authorization": f"Bearer {token}",
        }

        body = orjson_dumps([gamble])
        label = "赌狗签到" if gamble else "每日签到"

        proxy_h = HDHivePlaywrightClient._proxy_url_from_settings()
        try:
            with Client(verify=False, timeout=30.0, proxy=proxy_h) as client:
                resp = client.post(
                    root,
                    headers=headers,
                    cookies=cookies,
                    content=body,
                )
            text = resp.content.decode("utf-8", errors="replace")
            result = HDHivePlaywrightClient._checkin_parse_rsc_result(text)
            if result is None:
                if resp.status_code == 200:
                    return True, f"{label}请求成功（无详细响应）"
                return False, f"HTTP {resp.status_code}"

            payload = HDHivePlaywrightClient._checkin_payload_dict(result)
            message = str(payload.get("message") or "")
            description = str(payload.get("description") or "")
            display = description or message or str(payload)
            already_signed = any(
                k in part
                for k in ("已经签到", "签到过", "明天再来")
                for part in (message, description)
            )
            success = bool(payload.get("success")) or already_signed
            return success, display
        except Exception as e:
            return False, str(e)

    def login(
        self,
        cookie_str: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> Optional[Tuple[str, str]]:
        """
        使用 Cookie 登录：传入 cookie_str 时写入实例并返回 (Cookie 字符串, token)

        浏览器登录：不传 cookie_str 时须传入 username 与 password，由 Playwright 打开登录页

        :param cookie_str: 已持有的 token=...; csrf_access_token=... 等 Cookie 串
        :param username: 浏览器登录用用户名或邮箱
        :param password: 浏览器登录用密码
        :return: (完整 Cookie 字符串, token)，失败为 None
        :raises HDHiveLoginError: 浏览器登录失败或超时
        """
        if cookie_str is not None:
            s = cookie_str.strip()
            if not s:
                return None
            self._cookie_str = s
            cookies = HDHivePlaywrightClient._parse_cookie_str(s)
            token = cookies.get("token")
            if not token:
                return None
            return s, token

        if not username or not password:
            raise HDHiveLoginError("未提供 cookie_str 时须传入 username 与 password")

        try:
            with sync_playwright() as p:
                browser, context = HDHivePlaywrightClient._make_context(
                    p, self._headless
                )
                page = context.new_page()
                ok = self._fill_and_submit(page, username, password)
                raw_cookies = context.cookies()
                browser.close()

            if not ok:
                return None
            token = next(
                (c["value"] for c in raw_cookies if c["name"] == "token"), None
            )
            csrf = next(
                (c["value"] for c in raw_cookies if c["name"] == "csrf_access_token"),
                None,
            )
            if token:
                parts = [f"token={token}"]
                if csrf:
                    parts.append(f"csrf_access_token={csrf}")
                self._cookie_str = "; ".join(parts)
                return self._cookie_str, token
        except HDHiveLoginError:
            raise
        except Exception as e:
            raise HDHiveLoginError(f"登录失败: {e}") from e
        return None
