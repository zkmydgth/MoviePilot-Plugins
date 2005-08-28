# -*- coding: utf-8 -*-
"""
WebDAV 客户端测试（v3.2.0 模块 B）：用标准库假服务端跑真实 HTTP 闭环。

为什么这么测
------------
WebDAV 的坑几乎全在**协议细节**上，mock 掉 HTTP 层等于什么都没测：

- PROPFIND 必须收 **207 Multi-Status**；收了 200（填成了普通网页地址）要能识别并说清；
- 各家网盘的**命名空间前缀不同**，解析写死 ``D:`` 就会「列表永远是空」；
- 上传若没显式带 ``Content-Length``，urllib 会改走 **chunked**，部分服务端直接拒收；
- MKCOL 只能建一级，父目录不存在要逐级建。

所以这里用 ``http.server`` 起一个**真 HTTP 假 WebDAV 服务端**（实现
PUT / PROPFIND / GET / DELETE / MKCOL），不引任何第三方 mock 框架。

覆盖清单
--------
1. 连接成功 / 认证失败（401）/ 非 WebDAV 端点 / 5xx / 超时 —— 都必须是中文原因
2. 上传：自动逐级建目录、Content-Length 而非 chunked、字节数一致
3. 下载：往返内容一致；缺失文件报 404
4. 列表：跨命名空间前缀解析、跳过目录自身、目录/文件标记、中文名（URL 编码）
5. 删除：幂等（缺失不报错）
6. 地址校验：空地址 / 非 http(s) 直接拒绝
"""

import base64
import http.server
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from tempfile import TemporaryDirectory

import tests  # noqa: F401  触发宿主桩路径注入

from configbackup.webdav_client import WebDAVClient, WebDAVError


class _FakeWebDAVHandler(http.server.BaseHTTPRequestHandler):
    """假 WebDAV 服务端：够跑通备份上传/下载/列表/删除的闭环。"""

    #: 由测试注入/读取
    root: Path = None                 # type: ignore[assignment]
    username = "user"
    password = "pass"
    dav_prefix = "/dav"
    ns = "D"                          # 命名空间前缀（可换成任何前缀验证 local-name 匹配）
    propfind_status = None            # 非 None 时 PROPFIND 直接回该状态码（模拟非 WebDAV / 5xx）
    propfind_delay = 0.0
    seen: list = []                   # [(method, path)]，用于断言客户端发了什么
    last_headers: dict = {}

    # ------------------------------------------------------------------
    def log_message(self, *args):      # pragma: no cover - 静音
        """静音访问日志，避免污染测试输出。"""

    # ------------------------------------------------------------------
    def _record(self):
        """记录本次请求（含请求头），供断言使用。"""
        type(self).seen.append((self.command, self.path))
        type(self).last_headers = {k.lower(): v for k, v in self.headers.items()}

    def _authorized(self) -> bool:
        """校验 Basic Auth。"""
        expected = "Basic " + base64.b64encode(
            f"{self.username}:{self.password}".encode("utf-8")
        ).decode("ascii")
        return self.headers.get("Authorization") == expected

    def _reject(self) -> bool:
        """未认证时回 401 并返回 True。"""
        if self._authorized():
            return False
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="dav"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _local(self) -> Path:
        """把请求 URL 映射到本地临时目录（并防越界）。"""
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        prefix = self.dav_prefix.strip("/")
        rel = path.strip("/")
        if prefix and (rel == prefix or rel.startswith(prefix + "/")):
            rel = rel[len(prefix):].strip("/")
        target = (self.root / rel).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise ValueError("路径越界")
        return target

    def _send(self, status: int, body: bytes = b"", ctype: str = "text/plain"):
        """回一个简单响应。"""
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    # ------------------------------------------------------------------
    def do_MKCOL(self):
        """建目录：已存在回 405。"""
        self._record()
        if self._reject():
            return
        target = self._local()
        if target.exists():
            self._send(405)
            return
        target.mkdir(parents=True, exist_ok=True)
        self._send(201)

    def do_PUT(self):
        """上传：按 Content-Length 读取；缺该头说明客户端用了 chunked。"""
        self._record()
        if self._reject():
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        target = self._local()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        self._send(201)

    def do_GET(self):
        """下载。"""
        self._record()
        if self._reject():
            return
        target = self._local()
        if not target.is_file():
            self._send(404)
            return
        self._send(200, target.read_bytes(), "application/octet-stream")

    def do_DELETE(self):
        """删除：缺失回 404。"""
        self._record()
        if self._reject():
            return
        target = self._local()
        if not target.exists():
            self._send(404)
            return
        target.unlink()
        self._send(204)

    def do_PROPFIND(self):
        """列目录：正常回 207 + XML；可按配置回其它状态码/延迟（模拟非 WebDAV、5xx、超时）。"""
        self._record()
        if self.propfind_status:
            self._send(int(self.propfind_status), b"<!DOCTYPE html><html>not dav</html>", "text/html")
            return
        if self.propfind_delay:
            time.sleep(self.propfind_delay)
        if self._reject():
            return
        target = self._local()
        if not target.exists():
            self._send(404)
            return
        depth = (self.headers.get("Depth") or "1").strip()
        entries = [target]
        if depth != "0" and target.is_dir():
            entries += sorted(target.iterdir())
        responses = []
        for item in entries:
            rel = item.relative_to(self.root).as_posix()
            if rel == ".":
                rel = ""            # 根目录自身：真实服务端给的是 "/dav/"，不能给 "/dav/./"
            href = f"{self.dav_prefix}/" + urllib.parse.quote(rel, safe="/")
            if item.is_dir():
                href += "/"
                resourcetype = f"<{self.ns}:resourcetype><{self.ns}:collection/></{self.ns}:resourcetype>"
                size = 0
            else:
                resourcetype = f"<{self.ns}:resourcetype/>"
                size = item.stat().st_size
            responses.append(
                f"<{self.ns}:response>"
                f"<{self.ns}:href>{href}</{self.ns}:href>"
                f"<{self.ns}:propstat><{self.ns}:prop>"
                f"<{self.ns}:displayname>{item.name}</{self.ns}:displayname>"
                f"<{self.ns}:getcontentlength>{size}</{self.ns}:getcontentlength>"
                f"<{self.ns}:getlastmodified>2026-10-04T00:00:00Z</{self.ns}:getlastmodified>"
                f"{resourcetype}"
                f"</{self.ns}:prop><{self.ns}:status>HTTP/1.1 200 OK</{self.ns}:status>"
                f"</{self.ns}:propstat></{self.ns}:response>"
            )
        body = (
            "<?xml version='1.0' encoding='utf-8'?>"
            f"<{self.ns}:multistatus xmlns:{self.ns}='DAV:'>{''.join(responses)}</{self.ns}:multistatus>"
        ).encode("utf-8")
        self._send(207, body, "application/xml; charset=utf-8")


class _FakeDAVServer:
    """把假服务端包成上下文管理器（每个用例一个独立实例，属性互不污染）。"""

    def __init__(self, root: Path, username: str = "user", password: str = "pass"):
        handler = type("_Handler", (_FakeWebDAVHandler,), {
            "root": root, "username": username, "password": password,
            "ns": "D", "propfind_status": None, "propfind_delay": 0.0,
            "seen": [], "last_headers": {},
        })
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.handler = handler
        port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}/dav"
        # poll_interval 默认 0.5s：每个用例 teardown 都要等半秒，30+ 个用例白等十几秒
        # （变异脚本要把整套测试重跑 20+ 遍，这个常数会被放大 20 倍）
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        """后台线程里跑 HTTP 循环（poll 间隔调小以缩短 shutdown 等待）。"""
        self.httpd.serve_forever(poll_interval=0.02)

    @property
    def requests(self):
        """本服务端收到的请求列表。"""
        return self.handler.seen

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class _WebDAVBase(unittest.TestCase):
    """夹具：临时根目录 + 假服务端 + 客户端。"""

    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="cb-webdav-")
        self.root = Path(self._tmp.name)
        self.server = _FakeDAVServer(self.root)
        self.server.__enter__()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = WebDAVClient(self.server.url, "user", "pass", timeout=20)


class TestConnection(_WebDAVBase):
    """连接探测与错误分类（错误文案必须是中文，用户看得懂）。"""

    def test_connection_ok(self):
        """正常服务端：测试连接应成功。"""
        ok, msg = self.client.test()

        self.assertTrue(ok, msg)
        self.assertIn("成功", msg)

    def test_wrong_password_is_reported(self):
        """密码错：必须报「认证失败（401）」而不是含糊的网络错误。"""
        client = WebDAVClient(self.server.url, "user", "wrong", timeout=20)

        ok, msg = client.test()

        self.assertFalse(ok)
        self.assertIn("401", msg)
        self.assertIn("认证失败", msg)

    def test_non_webdav_endpoint_detected(self):
        """填成了普通网页地址：PROPFIND 回 200，必须识别为「不是 WebDAV 端点」。"""
        self.server.handler.propfind_status = 200

        with self.assertRaises(WebDAVError) as ctx:
            self.client.list_dir("")

        self.assertIn("不是 WebDAV 端点", str(ctx.exception))
        self.assertIn("207", str(ctx.exception))

    def test_server_error_reported(self):
        """5xx：报服务端错误并带状态码。"""
        self.server.handler.propfind_status = 503

        with self.assertRaises(WebDAVError) as ctx:
            self.client.list_dir("")

        self.assertIn("服务端错误", str(ctx.exception))
        self.assertIn("503", str(ctx.exception))

    def test_timeout_reported_in_chinese(self):
        """服务端不响应：报「超时」而不是原样抛 socket 异常。"""
        self.server.handler.propfind_delay = 2.0
        self.client._timeout = 1        # 直接改私有属性：MIN_TIMEOUT 会抬到 5s，测不动

        with self.assertRaises(WebDAVError) as ctx:
            self.client.list_dir("")

        self.assertIn("超时", str(ctx.exception))

    def test_bad_url_rejected(self):
        """空地址 / 非 http(s) 地址在构造时就拒绝。"""
        with self.assertRaises(WebDAVError):
            WebDAVClient("")
        with self.assertRaises(WebDAVError):
            WebDAVClient("ftp://example.com/dav")


class TestUploadDownload(_WebDAVBase):
    """上传 / 下载：备份包的实际传输路径。"""

    def _local_file(self, name: str, size: int) -> Path:
        """造一个本地测试文件。"""
        path = Path(self._tmp.name) / name
        path.write_bytes(b"x" * size)
        return path

    def test_upload_creates_parent_dirs(self):
        """上传到多级子目录时应逐级 MKCOL，而不是直接 409。"""
        src = self._local_file("bk.zip", 16)

        sent = self.client.upload(src, "backup/2026/bk.zip")

        self.assertEqual(sent, 16)
        self.assertTrue((self.root / "backup" / "2026" / "bk.zip").exists())
        mkcol_paths = [p for m, p in self.server.requests if m == "MKCOL"]
        self.assertIn("/dav/backup", mkcol_paths)
        self.assertIn("/dav/backup/2026", mkcol_paths)

    def test_mkdir_p_is_idempotent(self):
        """重复建同一目录不得报错（405 已存在要吞掉）。"""
        self.client.mkdir_p("a/b")
        self.client.mkdir_p("a/b")

        self.assertTrue((self.root / "a" / "b").is_dir())

    def test_upload_uses_content_length_not_chunked(self):
        """上传必须显式带 Content-Length：改走 chunked 会被部分服务端拒收。"""
        src = self._local_file("big.zip", 512 * 1024)

        self.client.upload(src, "big.zip")

        headers = self.server.handler.last_headers
        self.assertEqual(headers.get("content-length"), str(512 * 1024))
        self.assertNotIn("transfer-encoding", headers)
        self.assertEqual((self.root / "big.zip").stat().st_size, 512 * 1024)

    def test_roundtrip_bytes_identical(self):
        """上传再下载，内容必须逐字节一致。"""
        src = Path(self._tmp.name) / "bk_1.zip"
        payload = bytes(range(256)) * 40
        src.write_bytes(payload)

        self.client.upload(src, "bk_1.zip")
        dst = Path(self._tmp.name) / "back" / "bk_1.zip"
        size = self.client.download("bk_1.zip", dst)

        self.assertEqual(size, len(payload))
        self.assertEqual(dst.read_bytes(), payload)

    def test_download_missing_file_reports_404(self):
        """下载不存在的文件：报 404。"""
        with self.assertRaises(WebDAVError) as ctx:
            self.client.download("nope.zip", Path(self._tmp.name) / "nope.zip")

        self.assertIn("404", str(ctx.exception))

    def test_upload_missing_local_file(self):
        """本地源文件不存在：直接给出中文原因，不发请求。"""
        with self.assertRaises(WebDAVError) as ctx:
            self.client.upload(Path(self._tmp.name) / "gone.zip", "gone.zip")

        self.assertIn("本地文件不存在", str(ctx.exception))


class TestListing(_WebDAVBase):
    """列目录：解析必须跨命名空间前缀，且跳过目录自身。"""

    def test_list_dir_parses_foreign_namespace(self):
        """服务端用非 D 前缀时也要能解析出条目（local-name 通配）。"""
        self.server.handler.ns = "x1"
        (self.root / "sub").mkdir()
        (self.root / "bk_a.zip").write_bytes(b"a" * 10)
        (self.root / "bk_b.zip").write_bytes(b"b" * 20)

        items = self.client.list_dir("")

        names = sorted(i["name"] for i in items)
        self.assertIn("sub", names)
        self.assertIn("bk_a.zip", names)
        self.assertIn("bk_b.zip", names)
        self.assertEqual(len(items), 3, f"Depth:1 的自身条目应被跳过，实际：{items}")
        by_name = {i["name"]: i for i in items}
        self.assertTrue(by_name["sub"]["is_dir"])
        self.assertFalse(by_name["bk_a.zip"]["is_dir"])
        self.assertEqual(by_name["bk_b.zip"]["size"], 20)

    def test_list_dir_handles_encoded_names(self):
        """中文/带空格的文件名（URL 编码）也要正确还原。"""
        name = "备份 2026.zip"
        (self.root / name).write_bytes(b"x")

        items = self.client.list_dir("")

        self.assertIn(name, [i["name"] for i in items])

    def test_list_dir_empty(self):
        """空目录：返回空列表而不是报错。"""
        self.assertEqual(self.client.list_dir(""), [])


class TestDelete(_WebDAVBase):
    """远端清理：删除要幂等。"""

    def test_delete_removes_file(self):
        """删掉存在的文件。"""
        (self.root / "old.zip").write_bytes(b"x")

        self.client.delete("old.zip")

        self.assertFalse((self.root / "old.zip").exists())

    def test_delete_missing_is_idempotent(self):
        """删不存在的文件（远端已被清掉）：不得报错。"""
        self.client.delete("never-existed.zip")


if __name__ == "__main__":
    unittest.main()
