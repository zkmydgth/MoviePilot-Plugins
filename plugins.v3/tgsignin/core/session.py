"""
Telegram 客户端构建、会话文件管理与连通性自检。

会话文件放在插件数据目录（``/config/plugins/<插件ID>/sessions/``）下，
**不写进容器内的 /app**，否则容器重建即丢；文件名用账号标识派生，
避免中文显示名进入路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import DEFAULT_API_HASH, DEFAULT_API_ID, AccountConfig

__all__ = [
    "build_proxy",
    "parse_proxy_url",
    "resolve_proxy",
    "proxy_desc",
    "session_base",
    "session_exists",
    "session_files",
    "delete_session",
    "import_telethon",
    "build_client",
    "connection_selftest",
]


def build_proxy(
    proxy_type: str,
    proxy_host: str,
    proxy_port: int,
) -> Optional[Tuple[str, str, int]]:
    """
    组装 telethon 需要的代理元组。

    :param proxy_type: 代理类型（``socks5`` / ``http``）
    :param proxy_host: 代理主机（留空表示直连）
    :param proxy_port: 代理端口
    :return Optional[Tuple[str, str, int]]: 代理元组；未配置主机时返回 None
    """

    host = (proxy_host or "").strip()
    if not host:
        return None
    kind = (proxy_type or "socks5").strip().lower() or "socks5"
    return (kind, host, int(proxy_port or 7893))


def proxy_desc(proxy: Optional[Tuple[str, str, int]]) -> str:
    """
    返回代理的人类可读描述（用于日志与页面）。

    :param proxy: build_proxy 的返回值
    :return str: 如 ``socks5://192.0.2.94:7893`` 或 ``直连``
    """

    if not proxy:
        return "直连"
    return f"{proxy[0]}://{proxy[1]}:{proxy[2]}"


def parse_proxy_url(raw: str) -> Optional[Tuple[str, str, int]]:
    """
    解析 MoviePilot ``PROXY_HOST`` 这类代理地址。

    兼容 ``http://192.0.2.94:7893``、``socks5://host:port``、``host:port``
    三种写法；解析不出主机时返回 None。

    :param raw: 代理地址字符串
    :return Optional[Tuple[str, str, int]]: ``(类型, 主机, 端口)``
    """

    text = (raw or "").strip()
    if not text:
        return None
    kind = "http"
    if "://" in text:
        scheme, text = text.split("://", 1)
        kind = (scheme or "http").strip().lower()
    if kind.startswith("socks5"):
        kind = "socks5"
    elif kind.startswith("socks4"):
        kind = "socks4"
    else:
        kind = "http"
    text = text.split("/", 1)[0].strip()
    if not text:
        return None
    host, _, port_text = text.rpartition(":")
    if not host:
        # 没有端口号：整体当主机，端口用默认值
        host = text
        port = 7893
    else:
        try:
            port = int(port_text)
        except ValueError:
            return None
    if not host:
        return None
    return (kind, host, port)


def resolve_proxy(
    mode: str,
    proxy_type: str,
    proxy_host: str,
    proxy_port: int,
    mp_proxy_host: str,
) -> Optional[Tuple[str, str, int]]:
    """
    按代理模式解析出 telethon 需要的代理元组。

    :param mode: 代理模式（``mp`` 跟随 MoviePilot / ``custom`` 自定义 / ``direct`` 直连）
    :param proxy_type: 自定义模式的代理类型
    :param proxy_host: 自定义模式的代理主机
    :param proxy_port: 自定义模式的代理端口
    :param mp_proxy_host: MoviePilot 的 ``PROXY_HOST`` 设置值
    :return Optional[Tuple[str, str, int]]: 代理元组；None 表示直连
    """

    wanted = (mode or "mp").strip().lower()
    if wanted == "direct":
        return None
    if wanted == "custom":
        return build_proxy(proxy_type, proxy_host, proxy_port)
    # 默认：与 MoviePilot 保持一致；MP 没配代理则直连
    return parse_proxy_url(mp_proxy_host)


def session_base(data_dir: Path, account_key: str) -> Path:
    """
    返回某个账号的 session 文件基路径（不含 ``.session`` 后缀）。

    :param data_dir: 插件数据目录
    :param account_key: 账号标识
    :return Path: session 基路径
    """

    return Path(data_dir) / "sessions" / f"acc_{account_key}"


def session_exists(data_dir: Path, account_key: str) -> bool:
    """
    判断账号是否已有 session 文件。

    :param data_dir: 插件数据目录
    :param account_key: 账号标识
    :return bool: 存在返回 True
    """

    return session_base(data_dir, account_key).with_suffix(".session").exists()


def session_files(data_dir: Path) -> Dict[str, str]:
    """
    列出数据目录下所有 session 文件。

    :param data_dir: 插件数据目录
    :return Dict[str, str]: ``账号标识 -> 文件路径``
    """

    result: Dict[str, str] = {}
    folder = Path(data_dir) / "sessions"
    if not folder.exists():
        return result
    for path in sorted(folder.glob("acc_*.session")):
        result[path.stem.removeprefix("acc_")] = str(path)
    return result


def delete_session(data_dir: Path, account_key: str) -> bool:
    """
    删除某个账号的 session 文件（含 journal 临时文件）。

    :param data_dir: 插件数据目录
    :param account_key: 账号标识
    :return bool: 是否至少删除了一个文件
    """

    base = session_base(data_dir, account_key)
    removed = False
    for path in (
        base.with_suffix(".session"),
        base.with_suffix(".session-journal"),
    ):
        if path.exists():
            path.unlink()
            removed = True
    return removed


def import_telethon() -> Any:
    """
    延迟导入 telethon，缺失依赖时给出可读错误。

    :return Any: telethon 模块对象
    :raises RuntimeError: 未安装 telethon 时抛出
    """

    try:
        import telethon  # pylint: disable=import-outside-toplevel

        return telethon
    except ImportError as error:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(
            "缺少依赖 telethon / python-socks，请重新安装插件以安装依赖"
        ) from error


def build_client(
    data_dir: Path,
    account: AccountConfig,
    proxy: Optional[Tuple[str, str, int]],
) -> Any:
    """
    构造某个账号的 TelegramClient（不连接）。

    :param data_dir: 插件数据目录
    :param account: 账号配置
    :param proxy: 代理元组，None 表示直连
    :return Any: ``telethon.TelegramClient`` 实例
    """

    telethon = import_telethon()
    base = session_base(data_dir, account.key)
    base.parent.mkdir(parents=True, exist_ok=True)
    return telethon.TelegramClient(
        str(base),
        int(account.api_id or DEFAULT_API_ID),
        (account.api_hash or DEFAULT_API_HASH),
        proxy=proxy,
    )


async def connection_selftest(
    data_dir: Path,
    proxy: Optional[Tuple[str, str, int]],
    timeout: int = 25,
) -> Dict[str, Any]:
    """
    用公开应用凭据做一次 Telegram 连通性自检（不登录）。

    :param data_dir: 插件数据目录（自检 session 放这里，跑完即删）
    :param proxy: 代理元组，None 表示直连
    :param timeout: 连接超时秒数
    :return Dict[str, Any]: ``{"ok": bool, "message": str, "dc": str}``
    """

    import asyncio  # pylint: disable=import-outside-toplevel

    try:
        client = build_client(
            data_dir,
            AccountConfig(key="_selftest", label="自检"),
            proxy,
        )
    except RuntimeError as error:
        return {"ok": False, "message": str(error), "dc": ""}

    try:
        await asyncio.wait_for(client.connect(), timeout=timeout)
        dc_id = getattr(client.session, "dc_id", "") or ""
        return {
            "ok": True,
            "message": f"已连上 Telegram（DC {dc_id}）",
            "dc": str(dc_id),
        }
    except Exception as error:  # pylint: disable=broad-except
        return {
            "ok": False,
            "message": f"连接失败：{type(error).__name__}: {error}",
            "dc": "",
        }
    finally:
        try:
            await client.disconnect()
        except Exception:  # pylint: disable=broad-except
            pass
        delete_session(data_dir, "_selftest")
