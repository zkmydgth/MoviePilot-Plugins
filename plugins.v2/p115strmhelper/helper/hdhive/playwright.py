__all__ = ["HDHivePlaywrightClient", "HDHiveLoginError"]

from contextlib import contextmanager
from socket import (
    AF_INET,
    SO_REUSEADDR,
    SOCK_STREAM,
    SOL_SOCKET,
    socket,
)
from platform import machine as _machine
from re import match as re_match, search as re_search
from sys import platform
from time import sleep
from typing import Any, Dict, Iterator, Optional, Tuple
from urllib.parse import unquote, urlparse

from httpx import Client
from orjson import dumps as orjson_dumps, loads as orjson_loads

from app.core.config import settings

from ...utils.sentry import sentry_manager

_CLOAKBROWSER_AVAILABLE = False
_PLAYWRIGHT_AVAILABLE = False

try:
    from cloakbrowser import launch_context as _cloak_launch_context

    _CLOAKBROWSER_AVAILABLE = True
except ImportError:
    pass

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Playwright,
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    Browser = Any  # type: ignore[assignment,misc]
    BrowserContext = Any  # type: ignore[assignment,misc]
    Playwright = Any  # type: ignore[assignment,misc]

    class PlaywrightTimeoutError(Exception):  # type: ignore[misc]
        """
        Stub when playwright is not installed
        """

    sync_playwright = None  # type: ignore[assignment]

try:
    from slippers import Proxy as _SocksProxy

    _SLIPPERS_AVAILABLE = True
except ImportError:
    _SocksProxy = None  # type: ignore[assignment]
    _SLIPPERS_AVAILABLE = False


class HDHiveLoginError(Exception):
    """
    HDHive 网页登录失败或超时
    """


@sentry_manager.capture_all_class_exceptions
class HDHivePlaywrightClient:
    """
    HDHive 站点浏览器自动化客户端
    """

    DEFAULT_BASE_URL = "https://hdhive.com"
    LOGIN_PAGE = "/login"
    _CHROME_UA_SUFFIX = (
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    )

    def __init__(self, headless: bool = True) -> None:
        """
        :param headless: 浏览器是否无头模式
        """
        self._headless = headless
        self._cookie_str: Optional[str] = None

    @staticmethod
    def _check_backend() -> str:
        """
        检测可用的浏览器后端，优先返回 cloakbrowser

        :return: 'cloakbrowser' 或 'playwright'
        :raises RuntimeError: 两者均不可用时
        """
        if _CLOAKBROWSER_AVAILABLE:
            return "cloakbrowser"
        if _PLAYWRIGHT_AVAILABLE:
            return "playwright"
        raise RuntimeError(
            "浏览器登录需要 cloakbrowser 或 playwright，"
            "但当前环境中两者均未安装。"
            "新版 MoviePilot 请确认已安装 cloakbrowser；"
            "旧版 MoviePilot 请运行 playwright install 下载浏览器内核"
        )

    @staticmethod
    def _platform_product_and_hint() -> tuple[str, str]:
        """
        根据当前运行平台返回 UA product 字段和 Sec-Ch-Ua-Platform 值

        :return: (UA product 字符串, Sec-Ch-Ua-Platform 值)
        """
        m = _machine().lower()
        arm_like = "arm" in m or "aarch" in m
        if platform == "linux":
            arch = "aarch64" if arm_like else "x86_64"
            return f"X11; Linux {arch}", '"Linux"'
        elif platform == "win32":
            product = (
                "Windows NT 10.0; ARM64" if arm_like else "Windows NT 10.0; Win64; x64"
            )
            return product, '"Windows"'
        else:
            return "Macintosh; Intel Mac OS X 10_15_7", '"macOS"'

    @staticmethod
    def _build_ua() -> str:
        """
        构造与当前运行平台匹配的 Chrome User-Agent（用于 httpx 请求）

        :return: UA 字符串
        """
        product, _ = HDHivePlaywrightClient._platform_product_and_hint()
        return f"Mozilla/5.0 ({product}) {HDHivePlaywrightClient._CHROME_UA_SUFFIX}"

    @staticmethod
    def _build_browser_ua_and_hints(chrome_major: str) -> tuple[str, Dict[str, str]]:
        """
        根据实际 Chromium 版本构建与平台一致的 UA 和 Sec-Ch-Ua 系列请求头

        :param chrome_major: Chromium 主版本号字符串（如 "135"）
        :return: (UA 字符串, extra_http_headers 字典)
        """
        product, platform_hint = HDHivePlaywrightClient._platform_product_and_hint()
        ua = (
            f"Mozilla/5.0 ({product}) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_major}.0.0.0 Safari/537.36"
        )
        hints: Dict[str, str] = {
            "Sec-Ch-Ua": (
                f'"Chromium";v="{chrome_major}", '
                f'"Not.A/Brand";v="8", '
                f'"Google Chrome";v="{chrome_major}"'
            ),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": platform_hint,
        }
        return ua, hints

    @staticmethod
    def _stealth_init_script() -> str:
        """
        构造在每个页面启动前注入的反检测脚本（仅用于 playwright 后端）

        - 清除 navigator.webdriver
        - 伪造 plugins / languages
        - 注入 window.chrome
        - 从 navigator.userAgentData.brands 移除 HeadlessChrome
        - 同步 patch getHighEntropyValues 返回值

        :return: JS 字符串
        """
        return """
            try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); } catch(e) {}
            try { Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5].map(() => ({}))
            }); } catch(e) {}
            try { Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en-US', 'en']
            }); } catch(e) {}
            window.chrome = window.chrome || { runtime: {} };
            (function() {
                const origUAD = navigator.userAgentData;
                if (!origUAD) return;
                const isHeadless = b => /headless/i.test(b.brand);
                const cleanBrands = origUAD.brands.filter(b => !isHeadless(b));
                const fake = {
                    get brands() { return cleanBrands; },
                    get mobile() { return origUAD.mobile; },
                    get platform() { return origUAD.platform; },
                    getHighEntropyValues(hints) {
                        return origUAD.getHighEntropyValues(hints).then(v => {
                            if (v && v.brands) v.brands = v.brands.filter(b => !isHeadless(b));
                            if (v && v.fullVersionList) v.fullVersionList = v.fullVersionList.filter(b => !isHeadless(b));
                            return v;
                        });
                    },
                    toJSON() {
                        return { brands: cleanBrands, mobile: origUAD.mobile, platform: origUAD.platform };
                    }
                };
                try {
                    Object.defineProperty(Navigator.prototype, 'userAgentData', {
                        get: () => fake, configurable: true
                    });
                    return;
                } catch(e) {}
                try {
                    Object.defineProperty(navigator, 'userAgentData', {
                        get: () => fake, configurable: true
                    });
                    return;
                } catch(e) {}
                try {
                    Object.defineProperty(origUAD, 'brands', {
                        get: () => cleanBrands, configurable: true
                    });
                } catch(e) {}
            })();
            const origQuery = window.navigator.permissions && window.navigator.permissions.query;
            if (origQuery) {
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : origQuery.call(window.navigator.permissions, parameters)
                );
            }
        """

    @staticmethod
    def _install_request_header_sanitizer(
        context: BrowserContext, chrome_major: str
    ) -> None:
        """
        在 BrowserContext 上拦截所有出站请求，强制清理 sec-ch-ua 系列头（仅用于 playwright 后端）

        - sec-ch-ua / sec-ch-ua-full-version-list 中的 HeadlessChrome 项替换为 Google Chrome
        - 用作 extra_http_headers 的兜底（部分 Chromium 行为不受 extra_http_headers 覆盖）

        :param context: BrowserContext
        :param chrome_major: Chromium 主版本号
        """
        sec_ch_ua = (
            f'"Chromium";v="{chrome_major}", '
            f'"Not.A/Brand";v="8", '
            f'"Google Chrome";v="{chrome_major}"'
        )

        def _sanitize(route, request) -> None:
            try:
                headers = dict(request.headers)
                stripped = False
                for key in list(headers.keys()):
                    lower = key.lower()
                    if lower == "sec-ch-ua":
                        headers[key] = sec_ch_ua
                        stripped = True
                    elif lower == "sec-ch-ua-full-version-list":
                        if "headless" in headers[key].lower():
                            headers.pop(key)
                            stripped = True
                if stripped:
                    route.continue_(headers=headers)
                else:
                    route.continue_()
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass

        context.route("**/*", _sanitize)

    @staticmethod
    def _chromium_launch_args() -> list[str]:
        """
        返回 Chromium 进程启动参数（仅用于 playwright 后端）

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
        从 settings.PROXY 得到单一代理 URL 字符串

        :return: http(s)://... 或 socks5://... 字符串，未配置或无法解析时为 None
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
        将 MoviePilot settings.PROXY 转为 playwright chromium.launch 的 proxy 参数字典

        不含认证的 SOCKS5 可直接传给 playwright；含认证的 SOCKS5 须经由 slippers 转发

        :return: 含 server，可选 username / password 的字典；无代理时为 None
        """
        raw = HDHivePlaywrightClient._proxy_url_from_settings()
        if not raw:
            return None
        u = urlparse(raw)
        if not u.scheme or not u.hostname:
            return None
        if u.scheme in ("socks5", "socks") and (u.username or u.password):
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
    @contextmanager
    def _socks5_slippers_if_needed() -> Iterator[Optional[Dict[str, str]]]:
        """
        仅用于 playwright 后端：若全局代理为带认证的 SOCKS5，在本机启动 slippers 转发

        cloakbrowser 后端可直接传认证 SOCKS5 URL，无需此方法

        :yield: slippers 成功时为 {"server": "socks5://127.0.0.1:端口"}；否则为 None
        """
        raw = HDHivePlaywrightClient._proxy_url_from_settings()
        if not raw:
            yield None
            return
        u = urlparse(raw)
        if u.scheme not in ("socks5", "socks") or not (u.username or u.password):
            yield None
            return
        if not _SLIPPERS_AVAILABLE:
            yield None
            return
        sock = socket(AF_INET, SOCK_STREAM)
        try:
            sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            local_port = sock.getsockname()[1]
        finally:
            sock.close()
        sp = _SocksProxy(raw, host="127.0.0.1", port=local_port)
        with sp:
            local_url = sp.url()
            yield {"server": local_url}

    @staticmethod
    def _chromium_launch_kwargs(
        headless: bool, proxy: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        组装 chromium.launch 参数（仅用于 playwright 后端）

        - 用 channel="chromium" 强制使用完整 Chromium 二进制（新 headless 模式），
          避免 chromium-headless-shell 暴露 HeadlessChrome brand

        :param headless: 是否无头模式
        :param proxy: 已解析的 playwright proxy 字典；为 None 时不设置
        :return: 传给 launch 的关键字参数
        """
        kwargs: Dict[str, Any] = {
            "headless": headless,
            "channel": "chromium",
            "args": HDHivePlaywrightClient._chromium_launch_args(),
        }
        if proxy:
            kwargs["proxy"] = proxy
        return kwargs

    @staticmethod
    def _make_playwright_context(
        pw: Playwright,
        headless: bool,
        proxy: Optional[Dict[str, str]] = None,
    ) -> tuple[Browser, BrowserContext]:
        """
        playwright 后端：启动 Chromium 并创建登录页用上下文（语言、时区、视口）

        :param pw: sync_playwright() 返回的 Playwright 实例
        :param headless: 是否无头模式
        :param proxy: 已解析的 playwright proxy 字典
        :return: (browser, context)
        """
        browser = pw.chromium.launch(
            **HDHivePlaywrightClient._chromium_launch_kwargs(headless, proxy),
        )
        major = browser.version.split(".")[0]
        ua, hints = HDHivePlaywrightClient._build_browser_ua_and_hints(major)
        context = browser.new_context(
            user_agent=ua,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 720},
            extra_http_headers=hints,
        )
        context.add_init_script(HDHivePlaywrightClient._stealth_init_script())
        HDHivePlaywrightClient._install_request_header_sanitizer(context, major)
        return browser, context

    @staticmethod
    def _make_cloak_context(headless: bool) -> Any:
        """
        cloakbrowser 后端：创建浏览器上下文

        cloakbrowser 内置指纹伪装，无需手动注入 stealth 脚本或拦截请求头；
        认证 SOCKS5 代理也可直接传入 URL，无需 slippers 转发

        :param headless: 是否无头模式
        :return: playwright BrowserContext（由 cloakbrowser 内部创建）
        """
        proxy = HDHivePlaywrightClient._proxy_url_from_settings()
        humanize: bool = getattr(settings, "CLOAKBROWSER_HUMANIZE", True)
        human_preset: Optional[str] = getattr(
            settings, "CLOAKBROWSER_HUMAN_PRESET", None
        )
        kwargs: Dict[str, Any] = {
            "headless": headless,
            "humanize": humanize,
        }
        if proxy:
            kwargs["proxy"] = proxy
        if human_preset:
            kwargs["human_preset"] = human_preset
        return _cloak_launch_context(**kwargs)

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
        page: Any,
        username: str,
        password: str,
    ) -> bool:
        """
        打开登录页、填写账号密码并提交，等待离开 /login

        page API 与 playwright / cloakbrowser 均兼容

        :param page: 浏览器页面对象
        :param username: 登录用户名或邮箱
        :param password: 登录密码
        :return: 若 URL 在超时内离开登录页则为 True
        :raises HDHiveLoginError: 等待跳转超时
        """
        root = HDHivePlaywrightClient.DEFAULT_BASE_URL
        page.goto(
            f"{root}{HDHivePlaywrightClient.LOGIN_PAGE}",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        try:
            page.wait_for_selector(
                "input[name='username'], input[name='password']", timeout=15000
            )
        except PlaywrightTimeoutError:
            raise HDHiveLoginError(f"等待登录输入框超时，当前 URL: {page.url}")

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
                    page.fill(sel, username)
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
                    page.fill(sel, password)
                    break
            except Exception:
                continue

        sleep(0.5)
        submit_selectors = [
            "button[type='submit']",
            "button:has-text('登录')",
            "button:has-text('Login')",
        ]
        submitted = False
        for sel in submit_selectors:
            try:
                if page.query_selector(sel):
                    page.click(sel)
                    submitted = True
                    break
            except Exception:
                continue
        if not submitted:
            page.keyboard.press("Enter")

        try:
            page.wait_for_url(lambda url: "/login" not in url, timeout=30000)
            return True
        except PlaywrightTimeoutError:
            raise HDHiveLoginError(
                f"登录超时，当前 URL: {page.url}，页面标题: {page.title()}"
            )

    def _fetch_action_hash_via_playwright(self) -> Optional[str]:
        """
        打开首页，拦截 /_next/static/chunks/*.js 响应，解析 Server Action hash

        匹配形态: createServerReference)("<hash>", ..., "checkIn")

        同时兼容 cloakbrowser（新版）与 playwright（旧版）后端

        :return: 十六进制 hash，失败为 None
        """
        if not self._cookie_str:
            return None
        root = HDHivePlaywrightClient.DEFAULT_BASE_URL
        found_hash: list[str] = []

        def on_response(response: Any) -> None:
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
            backend = HDHivePlaywrightClient._check_backend()

            if backend == "cloakbrowser":
                context = HDHivePlaywrightClient._make_cloak_context(self._headless)
                try:
                    for name, value in cookies.items():
                        context.add_cookies(
                            [
                                {
                                    "name": name,
                                    "value": value,
                                    "domain": domain,
                                    "path": "/",
                                }
                            ]
                        )
                    page = context.new_page()
                    page.on("response", on_response)
                    page.goto(root, wait_until="networkidle", timeout=30000)
                finally:
                    context.close()
            else:
                with sync_playwright() as p:
                    with HDHivePlaywrightClient._socks5_slippers_if_needed() as slip:
                        proxy = (
                            slip
                            if slip is not None
                            else HDHivePlaywrightClient._playwright_proxy_settings()
                        )
                        kwargs = HDHivePlaywrightClient._chromium_launch_kwargs(
                            self._headless, proxy
                        )
                        browser = p.chromium.launch(**kwargs)
                        try:
                            major = browser.version.split(".")[0]
                            ua, hints = (
                                HDHivePlaywrightClient._build_browser_ua_and_hints(
                                    major
                                )
                            )
                            context = browser.new_context(
                                user_agent=ua,
                                extra_http_headers=hints,
                            )
                            context.add_init_script(
                                HDHivePlaywrightClient._stealth_init_script()
                            )
                            HDHivePlaywrightClient._install_request_header_sanitizer(
                                context, major
                            )
                            for name, value in cookies.items():
                                context.add_cookies(
                                    [
                                        {
                                            "name": name,
                                            "value": value,
                                            "domain": domain,
                                            "path": "/",
                                        }
                                    ]
                                )
                            page = context.new_page()
                            page.on("response", on_response)
                            page.goto(root, wait_until="networkidle", timeout=30000)
                        finally:
                            browser.close()
        except Exception:
            pass

        return found_hash[0] if found_hash else None

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

    def _login_via_cloakbrowser(
        self,
        username: str,
        password: str,
    ) -> Optional[Tuple[str, str]]:
        """
        cloakbrowser 后端登录（新版 MoviePilot）

        :param username: 登录用户名或邮箱
        :param password: 登录密码
        :return: (完整 Cookie 字符串, token)，登录失败为 None
        :raises HDHiveLoginError: 登录超时或表单交互失败
        """
        context = HDHivePlaywrightClient._make_cloak_context(self._headless)
        try:
            page = context.new_page()
            ok = self._fill_and_submit(page, username, password)
            raw_cookies = context.cookies()
        finally:
            context.close()

        if not ok:
            return None
        token = next((c["value"] for c in raw_cookies if c["name"] == "token"), None)
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
        return None

    def _login_via_playwright(
        self,
        username: str,
        password: str,
    ) -> Optional[Tuple[str, str]]:
        """
        playwright 后端登录（旧版 MoviePilot）

        :param username: 登录用户名或邮箱
        :param password: 登录密码
        :return: (完整 Cookie 字符串, token)，登录失败为 None
        :raises HDHiveLoginError: 登录超时或表单交互失败
        """
        with sync_playwright() as p:
            with HDHivePlaywrightClient._socks5_slippers_if_needed() as slip:
                proxy = (
                    slip
                    if slip is not None
                    else HDHivePlaywrightClient._playwright_proxy_settings()
                )
                browser, context = HDHivePlaywrightClient._make_playwright_context(
                    p, self._headless, proxy
                )
                try:
                    page = context.new_page()
                    ok = self._fill_and_submit(page, username, password)
                    raw_cookies = context.cookies()
                finally:
                    browser.close()

        if not ok:
            return None
        token = next((c["value"] for c in raw_cookies if c["name"] == "token"), None)
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
        return None

    def login(
        self,
        cookie_str: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> Optional[Tuple[str, str]]:
        """
        使用 Cookie 登录：传入 cookie_str 时写入实例并返回 (Cookie 字符串, token)

        浏览器登录：不传 cookie_str 时须传入 username 与 password，
        自动选择 cloakbrowser（新版 MoviePilot）或 playwright（旧版 MoviePilot）

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

        backend = HDHivePlaywrightClient._check_backend()
        try:
            if backend == "cloakbrowser":
                return self._login_via_cloakbrowser(username, password)
            else:
                return self._login_via_playwright(username, password)
        except HDHiveLoginError:
            raise
        except Exception as e:
            raise HDHiveLoginError(f"登录失败: {e}") from e
