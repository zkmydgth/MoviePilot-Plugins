# -*- coding: utf-8 -*-
"""
ConfigBackup 的 WebDAV 客户端：自研最小实现，**只用标准库**（v3.2.0 模块 B）。

为什么要自研
------------
- P115 的 ``helper/webdav`` 是 WebDAV **服务端**（对外提供网盘访问），方向相反，复用不了。
- 备份上传只需要 PUT / PROPFIND / GET / DELETE / MKCOL 五个动作，为它引入第三方依赖不划算
  （MP 环境装依赖有代价，本插件声明的依赖为空）。所以用 ``urllib`` + ``xml.etree``。

实现要点（踩坑判据都在注释里）
------------------------------
- **Basic Auth**；超时可配（``user.db`` 可能几百 MB，大包要给足读写时间）。
- **PROPFIND 解析用 local-name 通配** ``{*}displayname``：坚果云 / 群晖 / Nextcloud 的
  命名空间前缀各不相同，写死 ``D:`` 会解析不到任何字段。
- **错误一律翻译成可直接展示的中文原因**；重点区分「对方不是 WebDAV 端点」
  （PROPFIND 回了 2xx 但不是 207，常见于把 Web 页面地址填成了 WebDAV 地址）。
- **上传走流式**：显式带 ``Content-Length``，不把整个备份包读进内存。
"""

import base64
import errno
import posixpath
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

#: 默认单次请求超时（秒）——连接与每次读写都套用该值
DEFAULT_TIMEOUT = 120
#: 建立连接阶段允许的最短超时（秒）：太小会把慢网盘一律判失败
MIN_TIMEOUT = 5
#: 流式传输的块大小（字节）
CHUNK_SIZE = 256 * 1024

#: PROPFIND 请求体：只要这几项，避免部分服务端返回超大响应
PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:displayname/><d:getcontentlength/><d:getlastmodified/><d:resourcetype/>"
    "</d:prop></d:propfind>"
)
#: WebDAV 多状态响应码（PROPFIND 成功必须是它）
MULTI_STATUS = 207


class WebDAVError(Exception):
    """WebDAV 操作失败：``str(e)`` 就是可直接展示给用户的中文原因。"""


class WebDAVClient:
    """极简 WebDAV 客户端（Basic Auth + 备份闭环所需的五个动作）。"""

    def __init__(
        self, url: str, username: str = "", password: str = "",
        timeout: int = DEFAULT_TIMEOUT,
    ):
        """
        初始化客户端。

        :param url: WebDAV 根地址（如 ``https://dav.example.com/dav/MoviePilot``）
        :param username: 用户名（Basic Auth）
        :param password: 密码 / 应用专用密码
        :param timeout: 单次请求超时（秒）
        """
        url = (url or "").strip()
        if not url:
            raise WebDAVError("未配置 WebDAV 地址")
        if not url.lower().startswith(("http://", "https://")):
            raise WebDAVError("WebDAV 地址必须以 http:// 或 https:// 开头")
        self._base = url.rstrip("/")
        self._username = username or ""
        self._password = password or ""
        self._timeout = max(int(timeout or DEFAULT_TIMEOUT), MIN_TIMEOUT)

    # ------------------------------------------------------------------
    # 对外动作
    # ------------------------------------------------------------------
    def test(self) -> Tuple[bool, str]:
        """
        测试连接：对根目录做一次 ``Depth: 0`` 的 PROPFIND。

        不写文件、不建目录——只读探测，避免"测试连接"本身在网盘上留垃圾。

        :return: (是否成功, 结果信息)
        """
        try:
            self._request(
                "PROPFIND", "", body=PROPFIND_BODY.encode("utf-8"),
                headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
                action="测试连接", as_collection=True,
            )
            return True, "连接成功：服务端支持 WebDAV"
        except WebDAVError as e:
            return False, str(e)
        except Exception as e:  # pragma: no cover - 兜底，不把异常抛给 UI
            return False, f"测试连接失败：{e}"

    def mkdir_p(self, remote_dir: str) -> None:
        """
        逐级创建远端目录（已存在不算错）。

        为什么逐级：MKCOL 只能建"最后一级"，父目录不存在时服务端会回 409。

        :param remote_dir: 远端目录（相对根地址，如 ``backup/2026``）
        """
        remote_dir = (remote_dir or "").strip("/")
        if not remote_dir:
            return
        current = ""
        for segment in [s for s in remote_dir.split("/") if s]:
            current = f"{current}/{segment}" if current else segment
            status, _ = self._request(
                "MKCOL", current, action="创建远端目录",
                allow_status=(200, 201, 405, 301, 302),
            )
            # 405 = 已存在；301/302 = 服务端把无斜杠路径重定向到目录，都视为建好
            if status not in (201, 405, 301, 302):
                raise WebDAVError(f"创建远端目录失败：HTTP {status}")

    def upload(
        self, local_path: Path, remote_path: str, ensure_parent: bool = True,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """
        上传本地文件（流式，显式带 Content-Length，不整包读进内存）。

        :param local_path: 本地文件
        :param remote_path: 远端路径（相对根地址）
        :param ensure_parent: 是否先确保父目录存在
        :param on_progress: 可选进度回调 ``(已发字节, 总字节)``
        :return: 上传字节数

        ⚠️ 中途失败可能在远端留下**半个文件**（如超时），调用方按需清理。
        """
        local_path = Path(local_path)
        if not local_path.is_file():
            raise WebDAVError(f"上传失败：本地文件不存在（{local_path.name}）")
        if ensure_parent:
            parent = str(Path(remote_path).parent)
            if parent not in (".", "", "/"):
                self.mkdir_p(parent)

        size = local_path.stat().st_size
        sent = 0
        try:
            with local_path.open("rb") as handle:
                request = self._build_request(
                    "PUT", remote_path,
                    # Content-Length 必须显式给：否则 urllib 对文件对象改用 chunked，
                    # 部分 WebDAV 服务端（含群晖）拒不接受。
                    headers={"Content-Length": str(size)},
                    data=handle,
                    action="上传",
                )
                with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                    status = getattr(resp, "status", None) or resp.getcode()
                    if status not in (200, 201, 204):
                        raise WebDAVError(f"上传失败：HTTP {status}")
        except WebDAVError:
            raise
        except urllib.error.HTTPError as e:
            raise self._http_error(e, "上传") from e
        except urllib.error.URLError as e:
            raise self._url_error(e, "上传") from e
        except TimeoutError as e:
            raise self._timeout_error("上传") from e
        except OSError as e:
            raise self._url_error(urllib.error.URLError(e), "上传") from e
        sent = size
        if on_progress:
            on_progress(sent, size)
        return sent

    def download(self, remote_path: str, local_path: Path) -> int:
        """
        下载远端文件到本地（流式写入）。

        :param remote_path: 远端路径
        :param local_path: 本地目标文件
        :return: 下载字节数

        ⚠️ 中途失败会在本地留下**半个文件**，调用方按需删除。
        """
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            request = self._build_request("GET", remote_path, action="下载")
            written = 0
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                with local_path.open("wb") as handle:
                    while True:
                        chunk = resp.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
            return written
        except urllib.error.HTTPError as e:
            raise self._http_error(e, "下载") from e
        except urllib.error.URLError as e:
            raise self._url_error(e, "下载") from e
        except TimeoutError as e:
            raise self._timeout_error("下载") from e
        except OSError as e:
            raise self._url_error(urllib.error.URLError(e), "下载") from e

    def list_dir(self, remote_dir: str = "") -> List[Dict[str, Any]]:
        """
        列出目录内容（PROPFIND Depth: 1）。

        :param remote_dir: 远端目录（相对根地址；空串 = 根目录）
        :return: 条目列表，每项含 ``name`` / ``path`` / ``size`` / ``modified`` / ``is_dir``
        """
        _, body = self._request(
            "PROPFIND", remote_dir, body=PROPFIND_BODY.encode("utf-8"),
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            action="列出远端目录", as_collection=True,
        )
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise WebDAVError(f"列出远端目录失败：返回内容不是合法 XML（{e}）") from e

        self_path = self._abs_path(remote_dir, trailing_slash=True).rstrip("/")
        items: List[Dict[str, Any]] = []
        # ⚠️ 必须用 ElementPath 的 ``findall``：``Element.iter("{*}tag")`` **不支持**通配，
        # 会静默返回空列表（列表永远为空且不报错）—— 2026-10-04 实测踩到。
        for node in root.findall(".//{*}response"):
            href = self._node_text(node, "href")
            if not href:
                continue
            path = urllib.parse.unquote(urllib.parse.urlparse(href).path)
            is_dir = node.find(".//{*}resourcetype/{*}collection") is not None
            # 用 normpath 比较：各家服务端给的 href 花样很多（带不带尾斜杠、出现 ``./``、
            # 大小写不同的编码），逐字符比较会把目录自身当成一个条目列出来。
            if is_dir and posixpath.normpath(path) == posixpath.normpath(self_path):
                continue  # Depth:1 会带上目录自身，列表里要跳过
            items.append({
                "name": self._node_text(node, "displayname") or path.rstrip("/").rsplit("/", 1)[-1],
                "path": path,
                "size": self._node_int(node, "getcontentlength"),
                "modified": self._node_text(node, "getlastmodified"),
                "is_dir": is_dir,
            })
        return items

    def delete(self, remote_path: str) -> None:
        """
        删除远端文件（远端清理用）。

        :param remote_path: 远端路径
        """
        status, _ = self._request(
            "DELETE", remote_path, action="删除远端文件", allow_status=(200, 204, 404),
        )
        # 404 视为已删（远端清理要幂等）
        if status not in (200, 204, 404):
            raise WebDAVError(f"删除远端文件失败：HTTP {status}")

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _abs_path(self, remote_path: str, trailing_slash: bool = False) -> str:
        """
        把相对路径拼成「根地址 + 路径」的绝对 URL 路径。

        ``trailing_slash`` 只对**目录类请求**（PROPFIND / MKCOL）为真：
        给文件路径（PUT/GET/DELETE）加尾斜杠会被服务端当成集合，是踩过的坑。

        :param remote_path: 远端相对路径
        :param trailing_slash: 是否补尾斜杠（目录请求）
        :return: 绝对 URL 路径
        """
        base_path = urllib.parse.urlparse(self._base).path.rstrip("/")
        rel = str(remote_path or "").strip("/")
        path = f"{base_path}/{rel}" if rel else base_path
        return f"{path}/" if (trailing_slash or not rel) else path

    def _url(self, remote_path: str, as_collection: bool = False) -> str:
        """
        构造请求 URL（相对路径做最小转义，保留 ``/``）。

        :param remote_path: 远端相对路径
        :param as_collection: 是否为目录类请求
        :return: 完整 URL
        """
        path = urllib.parse.quote(
            self._abs_path(remote_path, trailing_slash=as_collection),
            safe="/:@!$&'()*+,;=~-._",
        )
        return f"{self._base}{path[len(urllib.parse.urlparse(self._base).path):]}"

    def _build_request(
        self, method: str, remote_path: str, headers: Optional[Dict[str, str]] = None,
        data: Any = None, action: str = "操作", as_collection: bool = False,
    ) -> urllib.request.Request:
        """
        构造带 Basic Auth 的请求对象。

        :param method: HTTP 方法
        :param remote_path: 远端相对路径
        :param headers: 附加请求头
        :param data: 请求体 / 文件对象
        :param action: 动作名（中文错误文案用）
        :param as_collection: 是否为目录类请求（决定尾斜杠）
        :return: 请求对象
        """
        request = urllib.request.Request(
            self._url(remote_path, as_collection=as_collection), data=data, method=method
        )
        request.add_header("Authorization", self._auth_header())
        request.add_header("User-Agent", "MoviePilot-ConfigBackup/3.2.0")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        return request

    def _auth_header(self) -> str:
        """生成 Basic Auth 头。"""
        raw = f"{self._username}:{self._password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _request(
        self, method: str, remote_path: str, body: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None, action: str = "操作",
        allow_status: Tuple[int, ...] = (), as_collection: bool = False,
    ) -> Tuple[int, bytes]:
        """
        发一个非流式请求，返回状态码（2xx 之外按错误分类抛出）。

        :param method: HTTP 方法
        :param remote_path: 远端路径
        :param body: 请求体
        :param headers: 附加请求头
        :param action: 动作名（用于中文错误文案）
        :param allow_status: 除 2xx 外额外允许的状态码（如 MKCOL 的 405）
        :param as_collection: 是否为目录类请求（PROPFIND 传 True）
        :return: (状态码, 响应体；非 PROPFIND 一律为空)
        """
        request = self._build_request(
            method, remote_path, headers=headers, data=body, action=action,
            as_collection=as_collection,
        )
        expect_multi = method == "PROPFIND"
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                if expect_multi and status != MULTI_STATUS:
                    # 2xx 但不是 207：典型是"填了个普通网页地址"，直接说清而不是让它超时
                    raise WebDAVError(
                        f"{action}失败：对方不是 WebDAV 端点"
                        f"（PROPFIND 返回 {status}，应为 {MULTI_STATUS}）"
                    )
                if not (200 <= status < 300 or status in allow_status):
                    raise WebDAVError(f"{action}失败：HTTP {status}")
                return status, resp.read() if expect_multi else b""
        except urllib.error.HTTPError as e:
            if e.code in allow_status:
                return e.code, b""
            raise self._http_error(e, action) from e
        except urllib.error.URLError as e:
            raise self._url_error(e, action) from e

        except TimeoutError as e:
            # ``h.getresponse()`` 阶段的超时**不会**被 urllib 包成 URLError（只有 ``h.request()`` 会），
            # 会原样抛 TimeoutError —— 不接住就把英文异常甩到用户脸上了。
            raise self._timeout_error(action) from e
        except OSError as e:
            raise self._url_error(urllib.error.URLError(e), action) from e

    def _http_error(self, err: urllib.error.HTTPError, action: str) -> WebDAVError:
        """把 HTTP 错误码翻译成中文原因。"""
        code = err.code
        if code == 401:
            return WebDAVError(f"{action}失败：认证失败（401），请检查用户名与密码/应用密码")
        if code == 403:
            return WebDAVError(f"{action}失败：无权限（403），请检查该账号对目标目录的权限")
        if code == 404:
            return WebDAVError(f"{action}失败：远端路径不存在（404）")
        if code == 405:
            return WebDAVError(f"{action}失败：服务端不接受该操作（405）")
        if code == 409:
            return WebDAVError(f"{action}失败：父目录不存在（409）")
        if code == 507:
            return WebDAVError(f"{action}失败：网盘空间不足（507）")
        if code >= 500:
            return WebDAVError(f"{action}失败：服务端错误（{code}）")
        return WebDAVError(f"{action}失败：HTTP {code}")

    def _timeout_error(self, action: str) -> WebDAVError:
        """
        统一的超时中文原因（带当前超时秒数，便于用户判断要不要调大）。

        :param action: 动作名
        :return: 超时错误
        """
        return WebDAVError(
            f"{action}失败：连接或读写超时（{self._timeout}s），可调大超时或检查网络"
        )

    def _url_error(self, err: urllib.error.URLError, action: str) -> WebDAVError:
        """把网络层错误翻译成中文原因。"""
        reason = getattr(err, "reason", err)
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
            return self._timeout_error(action)
        if isinstance(reason, socket.gaierror):
            return WebDAVError(f"{action}失败：域名解析失败（{reason}）")
        if isinstance(reason, OSError) and getattr(reason, "errno", None) in (
            errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ECONNRESET,
        ):
            return WebDAVError(f"{action}失败：无法连接远端服务（{reason}）")
        return WebDAVError(f"{action}失败：网络错误（{reason}）")

    @staticmethod
    def _node_text(node: ET.Element, tag: str) -> str:
        """按 local-name 取子元素文本（跨命名空间前缀都能命中）。"""
        child = node.find(f".//{{*}}{tag}")
        if child is None or child.text is None:
            return ""
        return child.text.strip()

    @staticmethod
    def _node_int(node: ET.Element, tag: str) -> int:
        """按 local-name 取子元素整数（解析失败给 0，不因一个坏字段整体失败）。"""
        raw = WebDAVClient._node_text(node, tag)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
