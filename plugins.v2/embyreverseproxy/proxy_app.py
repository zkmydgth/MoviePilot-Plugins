from asyncio import Lock, gather
from contextlib import asynccontextmanager
from hashlib import sha256
from re import sub as re_sub
from time import monotonic
from typing import List, Tuple
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from httpx import AsyncClient, Limits
from websockets import connect

from app.log import logger

PLAYBACK_URL_CACHE_TTL_SECONDS = 90
PLAYBACK_URL_CACHE_MAX_SIZE = 500

CACHE_KEY_HEADERS = (
    "authorization",
    "cookie",
    "x-emby-token",
    "user-agent",
    "x-emby-device-id",
    "x-emby-device-name",
    "x-emby-client",
    "x-emby-client-version",
    "x-device-id",
    "x-device-name",
    "x-client",
)

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

MEDIA_ROUTES = [
    "/audio/{item_id}/{name}",
    "/emby/audio/{item_id}/{name}",
    "/videos/{item_id}/{name}",
    "/emby/videos/{item_id}/{name}",
    "/items/{item_id}/download",
    "/emby/items/{item_id}/download",
    "/items/{item_id}/file",
    "/emby/items/{item_id}/file",
    "/sync/jobitems/{item_id}/file",
    "/emby/sync/jobitems/{item_id}/file",
]

CROSS_ORIGIN_PATTERN = r"&&\s*\(elem\.crossOrigin\s*=\s*initialSubtitleStream\)"


def create_app(
    emby_host: str,
    pin_rules: List[Tuple[str, str]] | None = None,
) -> FastAPI:
    """
    创建 Emby 反向代理 FastAPI 应用。

    :param emby_host: Emby 服务器根地址。
    :param pin_rules: 顶置路径规则列表 (路径前缀, 目标URL)；命中时先替换再 302。
    :return: 配置好的 FastAPI 应用实例。
    """
    emby_host = emby_host.rstrip("/")
    pin_rules = pin_rules or []

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        limits = Limits(max_keepalive_connections=20, keepalive_expiry=30.0)
        app.state.http_client_follow = AsyncClient(follow_redirects=True, limits=limits)
        app.state.http_client_no_follow = AsyncClient(
            follow_redirects=False, limits=limits
        )
        app.state.playback_url_cache = {}
        app.state.playback_cache_order = []
        app.state.playback_cache_lock = Lock()
        yield
        await app.state.http_client_follow.aclose()
        await app.state.http_client_no_follow.aclose()

    app = FastAPI(lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def path_to_lower_middleware(request: Request, call_next):
        path = request.scope["path"]
        lower = path.lower()
        _api_prefixes = (
            "/emby/videos/",
            "/emby/audio/",
            "/emby/items/",
            "/emby/sync/",
            "/emby/system/",
            "/videos/",
            "/audio/",
            "/items/",
            "/sync/",
            "/system/",
        )
        if lower.startswith(_api_prefixes):
            request.scope["path"] = lower
        return await call_next(request)

    def _extract_api_key(request: Request) -> str | None:
        """
        从请求中提取 api_key（query 或 X-Emby-Token 头）。

        :param request: 当前请求。
        :return: API Key 或 None。
        """
        api_key = request.query_params.get("api_key") or request.query_params.get(
            "X-Emby-Token"
        )
        if not api_key:
            api_key = request.headers.get("X-Emby-Token")
        return api_key

    def _build_forward_headers(request: Request) -> dict[str, str]:
        """
        构建转发请求头，排除 host 和 hop-by-hop 头。

        :param request: 当前请求。
        :return: 用于转发的请求头字典。
        """
        return {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() != "host"
        }

    def _header_hash(request: Request) -> str:
        """
        对缓存 key 白名单内的请求头做稳定序列化并哈希，用于区分认证/设备。

        :param request: 当前请求。
        :return: 十六进制摘要字符串。
        """
        parts = []
        for name in sorted(CACHE_KEY_HEADERS):
            value = request.headers.get(name)
            if value is not None:
                parts.append(f"{name}:{value}")
        return sha256("\n".join(parts).encode("utf-8")).hexdigest()

    async def _handle_media(
        request: Request, item_id: str, name: str = ""
    ) -> RedirectResponse | StreamingResponse | JSONResponse:
        """
        处理媒体路由：有 api_key 时尝试 PlaybackInfo 后重定向/流式代理，否则走通用反向代理。

        :param request: 当前请求。
        :param item_id: 媒体项 ID。
        :param name: 路径中的名称（未使用，由路由匹配）。
        :return: 重定向、流式响应或错误 JSON。
        """
        api_key = _extract_api_key(request)
        if api_key:
            resp = await _try_media_response(item_id, api_key, request)
            if resp:
                return resp
        return await _reverse_proxy(request)

    async def _resolve_redirect(
        client: AsyncClient, url: str, headers: dict[str, str]
    ) -> str:
        """
        跟随重定向链，返回最终 URL。

        :param client: 共享的 httpx 客户端（follow_redirects=True）。
        :param url: 起始 URL。
        :param headers: 请求头。
        :return: 最终 URL；失败时返回原始 url。
        """
        try:
            resp = await client.head(url, headers=headers, timeout=10)
            return str(resp.url)
        except Exception:
            logger.warning("解析重定向失败，使用原始 URL: %s", url, exc_info=True)
            return url

    def _apply_pin_rules(url_or_path: str, rules: List[Tuple[str, str]]) -> str:
        """
        对 PlaybackInfo 返回的 Path（URL 或路径）应用顶置规则：匹配前缀则替换为目标 URL。

        :param url_or_path: 完整 URL 或路径字符串。
        :param rules: 顶置规则列表 (路径前缀, 目标URL)。
        :return: 替换后的 URL，未命中则返回原串。
        """
        if not url_or_path or not rules:
            return url_or_path
        path_component: str
        original_query: str = ""
        if url_or_path.startswith(("http://", "https://")):
            parsed = urlparse(url_or_path)
            path_component = parsed.path or "/"
            original_query = parsed.query or ""
        else:
            path_component = (
                url_or_path if url_or_path.startswith("/") else "/" + url_or_path
            )
        for path_prefix, target_url in rules:
            if path_component != path_prefix and not path_component.startswith(
                path_prefix + "/"
            ):
                continue
            suffix = path_component[len(path_prefix) :].lstrip("/")
            base = target_url.rstrip("/")
            new_url = base + ("/" + suffix if suffix else "")
            if original_query:
                new_url += "?" + original_query
            return new_url
        return url_or_path

    async def _try_media_response(
        item_id: str, api_key: str, request: Request
    ) -> RedirectResponse | StreamingResponse | JSONResponse | None:
        """
        查询 PlaybackInfo 获取真实地址；浏览器走流式代理，原生客户端走 302 重定向。
        对 (item_id, media_source_id, 必要 header 哈希) 做短期缓存，命中时跳过 PlaybackInfo 与 _resolve_redirect。

        :param item_id: 媒体项 ID。
        :param api_key: Emby API Key。
        :param request: 当前请求。
        :return: 重定向/流式/错误响应，或 None 表示回退到反向代理。
        """
        media_source_id = request.query_params.get("MediaSourceId") or ""
        cache_key = (item_id, media_source_id, _header_hash(request))
        cache = request.app.state.playback_url_cache
        order = request.app.state.playback_cache_order
        lock = request.app.state.playback_cache_lock

        cached_final_url = None
        async with lock:
            if cache_key in cache:
                final_url, expiry_ts = cache[cache_key]
                if monotonic() < expiry_ts:
                    cached_final_url = final_url
                else:
                    del cache[cache_key]
                    try:
                        order.remove(cache_key)
                    except ValueError:
                        pass
        if cached_final_url is not None:
            return RedirectResponse(url=cached_final_url, status_code=302)

        url = f"{emby_host}/Items/{item_id}/PlaybackInfo?X-Emby-Token={api_key}"
        client_follow = request.app.state.http_client_follow
        try:
            resp = await client_follow.post(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.warning("PlaybackInfo 请求失败: item_id=%s", item_id, exc_info=True)
            return None

        for source in data.get("MediaSources", []):
            sid = source.get("Id", "")
            if media_source_id and sid != media_source_id:
                continue
            path = source.get("Path", "")
            path = _apply_pin_rules(path, pin_rules)
            if path.startswith(("http://", "https://")):
                fwd_headers = _build_forward_headers(request)
                final_url = await _resolve_redirect(client_follow, path, fwd_headers)

                async with lock:
                    now = monotonic()
                    expiry = now + PLAYBACK_URL_CACHE_TTL_SECONDS
                    expired = [k for k in order if cache.get(k) and cache[k][1] < now]
                    for k in expired:
                        cache.pop(k, None)
                    order[:] = [k for k in order if k not in frozenset(expired)]
                    while len(cache) >= PLAYBACK_URL_CACHE_MAX_SIZE and order:
                        oldest = order.pop(0)
                        cache.pop(oldest, None)
                    cache[cache_key] = (final_url, expiry)
                    order.append(cache_key)

                logger.info("302 重定向: item_id=%s -> %s", item_id, final_url)
                return RedirectResponse(url=final_url, status_code=302)
        return None

    for route in MEDIA_ROUTES:
        app.api_route(route, methods=["GET", "HEAD"], response_model=None)(
            _handle_media
        )

    async def _ws_proxy(ws_client: WebSocket) -> None:
        """
        双向代理客户端 WebSocket 与 Emby 后端 WebSocket。

        :param ws_client: 客户端 WebSocket 连接。
        """
        await ws_client.accept()
        parsed = urlparse(emby_host)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        qs = str(ws_client.scope.get("query_string", b""), "utf-8")
        backend_url = f"{scheme}://{parsed.netloc}/embywebsocket"
        if qs:
            backend_url += f"?{qs}"

        try:
            async with connect(backend_url) as ws_backend:

                async def client_to_backend() -> None:
                    try:
                        while True:
                            data = await ws_client.receive_text()
                            await ws_backend.send(data)
                    except WebSocketDisconnect:
                        pass

                async def backend_to_client() -> None:
                    try:
                        async for msg in ws_backend:
                            if isinstance(msg, str):
                                await ws_client.send_text(msg)
                            else:
                                await ws_client.send_bytes(msg)
                    except Exception as e:
                        logger.debug("WebSocket 后端->客户端 结束: %s", e)

                await gather(client_to_backend(), backend_to_client())
        except Exception:
            logger.warning("WebSocket 代理异常", exc_info=True)
        finally:
            try:
                await ws_client.close()
            except Exception as e:
                logger.debug("关闭客户端 WebSocket 时异常: %s", e)

    app.websocket("/embywebsocket")(_ws_proxy)
    app.websocket("/emby/embywebsocket")(_ws_proxy)

    def _current_port(request: Request) -> int:
        """
        从请求中解析当前代理端口（供客户端连回），优先 X-Forwarded-Port。

        :param request: 当前请求。
        :return: 端口号。
        """
        forwarded = request.headers.get("x-forwarded-port")
        if forwarded:
            try:
                return int(forwarded.split(",")[0].strip())
            except (ValueError, AttributeError):
                pass
        server = request.scope.get("server")
        if server and len(server) >= 2:
            try:
                return int(server[1])
            except (ValueError, TypeError):
                pass
        return 80

    async def _system_info_handler(
        request: Request,
    ) -> JSONResponse | StreamingResponse:
        """
        代理 /emby/system/info 与 /system/info，并改写响应中的端口，使客户端连回代理而非后端。

        :param request: 当前请求。
        :return: 改写后的 JSON 或 502 错误。
        """
        path = request.scope.get("path", "/")
        qs = str(request.url.query)
        target_url = f"{emby_host}{path}"
        if qs:
            target_url += f"?{qs}"
        headers = _build_forward_headers(request)
        client = request.app.state.http_client_follow
        try:
            resp = await client.request(
                request.method,
                target_url,
                headers=headers,
                timeout=10,
            )
        except Exception as e:
            err_msg = str(e) or type(e).__name__
            logger.warning(
                "System/Info 请求失败: %s — %s", target_url, err_msg, exc_info=True
            )
            return JSONResponse(
                status_code=502,
                content={
                    "error": "Bad Gateway",
                    "detail": f"System/Info 请求失败: {err_msg}",
                },
            )
        if resp.status_code != 200:
            logger.warning(
                "System/Info 后端返回非 200: status=%s, url=%s",
                resp.status_code,
                target_url,
            )
            return JSONResponse(
                status_code=502,
                content={
                    "error": "Bad Gateway",
                    "detail": "System/Info 请求失败",
                },
            )
        try:
            body = resp.json()
        except Exception:
            logger.warning("System/Info 响应非 JSON: %s", target_url, exc_info=True)
            return JSONResponse(
                status_code=502,
                content={
                    "error": "Bad Gateway",
                    "detail": "System/Info 请求失败",
                },
            )
        origin_port = body.get("WebSocketPortNumber")
        if origin_port is not None:
            current_port = _current_port(request)
            try:
                origin_port = int(origin_port)
            except (TypeError, ValueError):
                origin_port = None
            if origin_port is not None:
                body["WebSocketPortNumber"] = current_port
                if body.get("HttpServerPortNumber") is not None:
                    body["HttpServerPortNumber"] = current_port
                for key in ("LocalAddresses", "RemoteAddresses"):
                    if isinstance(body.get(key), list):
                        body[key] = [
                            str(s).replace(str(origin_port), str(current_port))
                            for s in body[key]
                        ]
                for key in ("LocalAddress", "WanAddress"):
                    if body.get(key) is not None:
                        body[key] = str(body[key]).replace(
                            str(origin_port), str(current_port)
                        )
        excluded = HOP_BY_HOP_HEADERS | {"content-length", "content-encoding"}
        resp_headers = {
            k: v for k, v in resp.headers.multi_items() if k.lower() not in excluded
        }
        return JSONResponse(status_code=200, content=body, headers=resp_headers)

    for _path in ("/emby/system/info", "/system/info"):
        app.api_route(
            _path,
            methods=["GET", "HEAD"],
            response_model=None,
        )(_system_info_handler)

    async def _reverse_proxy(request: Request) -> StreamingResponse | JSONResponse:
        """
        将请求反向代理到 Emby 服务器并流式返回响应。

        :param request: 当前请求。
        :return: 流式响应或 502 JSON 错误。
        """
        path = request.scope.get("path", "/")
        qs = str(request.url.query)
        target_url = f"{emby_host}{path}"
        if qs:
            target_url += f"?{qs}"

        headers = _build_forward_headers(request)
        body = await request.body()

        client = request.app.state.http_client_no_follow
        try:
            req = client.build_request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body if body else None,
            )
            resp = await client.send(req, stream=True)
        except Exception:
            logger.warning("无法连接到 Emby: %s", target_url, exc_info=True)
            return JSONResponse(
                status_code=502,
                content={
                    "error": "Bad Gateway",
                    "detail": f"无法连接到 Emby 服务器: {emby_host}",
                },
            )

        excluded = HOP_BY_HOP_HEADERS | {"content-encoding", "content-length"}
        resp_headers = {
            k: v for k, v in resp.headers.multi_items() if k.lower() not in excluded
        }

        async def stream():
            try:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk
            finally:
                await resp.aclose()

        return StreamingResponse(
            stream(),
            status_code=resp.status_code,
            headers=resp_headers,
        )

    async def _patch_plugin_js(request: Request) -> JSONResponse | Response:
        """
        拦截 htmlvideoplayer/plugin.js，去除 crossOrigin 赋值，
        使浏览器播放器在 302 重定向时不触发 CORS 预检。

        :param request: 当前请求。
        :return: 修补后的 JS 响应或 502 错误 JSON。
        """
        path = request.scope.get("path", "/")
        qs = str(request.url.query)
        target_url = f"{emby_host}{path}"
        if qs:
            target_url += f"?{qs}"
        headers = _build_forward_headers(request)
        client = request.app.state.http_client_follow
        try:
            resp = await client.get(target_url, headers=headers, timeout=15)
        except Exception:
            logger.warning("获取 plugin.js 失败: %s", target_url, exc_info=True)
            return JSONResponse(status_code=502, content={"error": "Bad Gateway"})

        content = resp.text
        patched = re_sub(CROSS_ORIGIN_PATTERN, "", content)
        if patched != content:
            logger.info("已修补 plugin.js: 移除 crossOrigin 赋值")

        excluded = HOP_BY_HOP_HEADERS | {"content-encoding", "content-length"}
        resp_headers = {
            k: v for k, v in resp.headers.multi_items() if k.lower() not in excluded
        }
        resp_headers["content-type"] = "application/javascript; charset=utf-8"

        return Response(
            content=patched.encode("utf-8"),
            status_code=resp.status_code,
            headers=resp_headers,
        )

    for _js_path in (
        "/emby/web/modules/htmlvideoplayer/plugin.js",
        "/web/modules/htmlvideoplayer/plugin.js",
    ):
        app.api_route(_js_path, methods=["GET", "HEAD"], response_model=None)(
            _patch_plugin_js
        )

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        response_model=None,
    )
    async def catch_all(
        request: Request,
    ) -> StreamingResponse | JSONResponse:
        """
        兜底路由：将未匹配请求反向代理到 Emby。

        :param request: 当前请求。
        :return: 流式响应或 502 JSON 错误。
        """
        return await _reverse_proxy(request)

    return app
