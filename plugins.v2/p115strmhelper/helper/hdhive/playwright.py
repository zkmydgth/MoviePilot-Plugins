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
from sys import platform
from time import sleep
from typing import Any, Dict, Iterator, Optional, Tuple
from urllib.parse import unquote, urlparse

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

    @staticmethod
    def _parse_checkin_result_text(text: str, label: str) -> Tuple[bool, str]:
        """
        根据签到结果弹窗文本判断签到是否成功，并返回干净的展示文案

        :param text: 签到结果弹窗文本
        :param label: 签到类型（赌狗签到或每日签到）

        :return: (是否成功, 展示用文案或错误信息)
        """
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        clean = " ".join(lines)

        already_keywords = ("已经签到", "签到过", "明天再来")
        fail_keywords = ("失败", "错误", "error", "failed")

        if any(k in clean for k in already_keywords):
            body_lines = [
                ln
                for ln in lines
                if not any(
                    ln == kw or ln.startswith("签到") and "失败" in ln
                    for kw in ("签到失败",)
                )
            ]
            display = " ".join(body_lines) if body_lines else clean
            return True, f"今日已签到：{display}"

        if any(k in clean.lower() for k in fail_keywords):
            return False, clean

        return True, clean

    def _checkin_via_browser(self, gamble: bool) -> Tuple[bool, str]:
        """
        模拟签到

        :param gamble: True 为赌狗签到，False 为每日签到

        :return: (是否成功, 展示用文案或错误信息)
        """
        if not self._cookie_str:
            return False, "请先 login 或传入 Cookie"

        root = self.DEFAULT_BASE_URL
        cookies = self._parse_cookie_str(self._cookie_str)
        domain = root.replace("https://", "").replace("http://", "")
        label = "赌狗签到" if gamble else "每日签到"

        def _do_checkin(page: Any) -> Tuple[bool, str]:
            page.goto(root, wait_until="domcontentloaded", timeout=30000)

            try:
                dismiss_loc = page.locator("button:has-text('我知道了')")
                dismiss_loc.first.wait_for(state="visible", timeout=8000)
                for _ in range(20):
                    try:
                        dismiss_loc.first.click()
                    except Exception:
                        pass
                    try:
                        dismiss_loc.first.wait_for(state="hidden", timeout=1500)
                        break
                    except PlaywrightTimeoutError:
                        sleep(1)
            except Exception:
                pass

            clicked_avatar = False
            for avatar_sel, force in (
                ("button:has(div.MuiAvatar-root)", False),
                ("div.MuiAvatar-root", True),
            ):
                try:
                    page.wait_for_selector(avatar_sel, timeout=15000)
                    page.click(avatar_sel, force=force)
                    clicked_avatar = True
                    break
                except PlaywrightTimeoutError:
                    continue
                except Exception:
                    continue
            if not clicked_avatar:
                return False, "等待头像按钮超时，可能未登录成功"

            btn_loc = page.locator(f"button:has-text('{label}')")
            try:
                btn_loc.first.wait_for(state="visible", timeout=15000)
            except PlaywrightTimeoutError:
                return False, f"等待{label}按钮超时，用户菜单未出现"

            _prev_box: dict = {}
            for _ in range(20):
                try:
                    _box = btn_loc.first.bounding_box() or {}
                except Exception:
                    _box = {}
                if _box and _box == _prev_box:
                    break
                _prev_box = _box
                sleep(0.1)

            page.evaluate("""
                () => {
                    window.__checkinResult = null;
                    const resultPhrases = [
                        '签到成功', '签到失败', '已经签到', '明天再来',
                        '获得积分', '签到奖励', '积分+', '赌狗签到成功', '赌狗签到失败'
                    ];
                    const seen = new WeakSet();
                    const obs = new MutationObserver((mutations) => {
                        if (window.__checkinResult) return;
                        for (const mut of mutations) {
                            for (const node of mut.addedNodes) {
                                if (node.nodeType !== 1 || seen.has(node)) continue;
                                seen.add(node);
                                const candidates = [node, ...node.querySelectorAll('*')];
                                for (const el of candidates) {
                                    const t = (el.innerText || '').trim();
                                    if (!t || t.length >= 300) continue;
                                    if (resultPhrases.some(p => t.includes(p))) {
                                        window.__checkinResult = t;
                                        obs.disconnect();
                                        return;
                                    }
                                }
                            }
                        }
                    });
                    obs.observe(document.body, { childList: true, subtree: true });
                }
            """)

            btn_loc.first.click()

            cf_iframe_sel = "iframe[src*='challenges.cloudflare.com']"
            try:
                page.wait_for_selector(cf_iframe_sel, timeout=8000)
                cf_frame = page.frame_locator(cf_iframe_sel)
                for cf_sel in (
                    "input[type='checkbox']",
                    "[class*='ctp-checkbox']",
                    ".mark",
                    "label",
                ):
                    try:
                        cf_frame.locator(cf_sel).click(timeout=3000)
                        break
                    except Exception:
                        continue
            except PlaywrightTimeoutError:
                pass
            _RESULT_PHRASES = [
                "签到成功",
                "签到失败",
                "已经签到",
                "明天再来",
                "获得积分",
                "签到奖励",
                "积分+",
            ]
            _SCAN_JS = (
                "() => {"
                "  const t = document.body.innerText || '';"
                "  const phrases = " + str(_RESULT_PHRASES) + ";"
                "  const hit = phrases.find(p => t.includes(p));"
                "  if (!hit) return null;"
                "  const idx = t.indexOf(hit);"
                "  return t.slice(Math.max(0, idx - 10), idx + 80).trim();"
                "}"
            )
            deadline_ms = 30000
            interval_ms = 500
            elapsed = 0
            result_text = None
            while elapsed < deadline_ms:
                captured = page.evaluate("() => window.__checkinResult")
                if captured:
                    result_text = str(captured)
                    break
                scanned = page.evaluate(_SCAN_JS)
                if scanned:
                    result_text = str(scanned)
                    break
                page.wait_for_timeout(interval_ms)
                elapsed += interval_ms

            if result_text:
                return HDHivePlaywrightClient._parse_checkin_result_text(
                    result_text, label
                )
            return False, f"{label}：等待结果超时"

        backend = self._check_backend()
        try:
            if backend == "cloakbrowser":
                context = self._make_cloak_context(self._headless)
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
                    return _do_checkin(page)
                finally:
                    context.close()
            else:
                with sync_playwright() as p:
                    with self._socks5_slippers_if_needed() as slip:
                        proxy = (
                            slip
                            if slip is not None
                            else self._playwright_proxy_settings()
                        )
                        browser, context = self._make_playwright_context(
                            p, self._headless, proxy
                        )
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
                            return _do_checkin(page)
                        finally:
                            browser.close()
        except PlaywrightTimeoutError as e:
            return False, f"{label}操作超时: {e}"
        except Exception as e:
            return False, f"{label}浏览器签到失败: {e}"

    def checkin(self, gamble: bool) -> Tuple[bool, str]:
        """
        签到

        :param gamble: True 为赌狗签到，False 为每日签到
        :return: (是否成功, 展示用文案或错误信息)
        """
        return self._checkin_via_browser(gamble)

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
