from asyncio import gather
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from httpx import AsyncClient, Limits
from websockets import connect

from app.log import logger


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


def create_app(emby_host: str) -> FastAPI:
    """
    创建 Emby 反向代理 FastAPI 应用。

    :param emby_host: Emby 服务器根地址。
    :return: 配置好的 FastAPI 应用实例。
    """
    emby_host = emby_host.rstrip("/")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        limits = Limits(max_keepalive_connections=20, keepalive_expiry=30.0)
        app.state.http_client_follow = AsyncClient(follow_redirects=True, limits=limits)
        app.state.http_client_no_follow = AsyncClient(
            follow_redirects=False, limits=limits
        )
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
        path = request.scope["path"].lower()
        if path.startswith(("/emby/", "/items/", "/audio/", "/videos/", "/sync/")):
            request.scope["path"] = path
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

    def _is_browser(request: Request) -> bool:
        """
        判断请求是否来自浏览器（根据 User-Agent）。

        :param request: 当前请求。
        :return: 若 UA 包含常见浏览器标识则 True。
        """
        ua = (request.headers.get("user-agent") or "").lower()
        return any(k in ua for k in ("mozilla", "chrome", "safari"))

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

    async def _stream_remote(
        url: str, request: Request
    ) -> StreamingResponse | JSONResponse:
        """
        流式代理远程 URL 内容（用于浏览器客户端避免 CORS）。

        :param url: 远程媒体 URL。
        :param request: 当前请求。
        :return: 流式响应或 502 JSON 错误。
        """
        headers = _build_forward_headers(request)
        client = request.app.state.http_client_follow
        try:
            req = client.build_request(method=request.method, url=url, headers=headers)
            resp = await client.send(req, stream=True)
        except Exception:
            logger.warning("流式代理远程 URL 失败: %s", url, exc_info=True)
            return JSONResponse(
                status_code=502,
                content={"error": "Bad Gateway", "detail": f"无法连接: {url}"},
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
            stream(), status_code=resp.status_code, headers=resp_headers
        )

    async def _try_media_response(
        item_id: str, api_key: str, request: Request
    ) -> RedirectResponse | StreamingResponse | JSONResponse | None:
        """
        查询 PlaybackInfo 获取真实地址；浏览器走流式代理，原生客户端走 302 重定向。

        :param item_id: 媒体项 ID。
        :param api_key: Emby API Key。
        :param request: 当前请求。
        :return: 重定向/流式/错误响应，或 None 表示回退到反向代理。
        """
        url = f"{emby_host}/Items/{item_id}/PlaybackInfo?X-Emby-Token={api_key}"
        media_source_id = request.query_params.get("MediaSourceId")
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
            if path.startswith(("http://", "https://")):
                fwd_headers = _build_forward_headers(request)
                final_url = await _resolve_redirect(client_follow, path, fwd_headers)
                if _is_browser(request):
                    logger.info("流式代理: item_id=%s -> %s", item_id, final_url)
                    return await _stream_remote(final_url, request)
                else:
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
