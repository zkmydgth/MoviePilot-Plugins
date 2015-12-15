"""
两阶段登录：① 发送验证码 ② 确认登录（含两步验证密码）。

MoviePilot 插件跑在 Web 环境，无法 ``input()`` 交互，所以登录拆成两次
HTTP 调用：页面按钮「发送验证码」与「确认登录」，两次调用都读写同一份
``login_pending.json``（保存 ``phone_code_hash``），并由同一个 session 文件
承载 Telegram 的授权密钥——因此两步之间**不要删除 session 文件**。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import AccountConfig
from .session import (
    build_client,
    import_telethon,
    mask_phone,
    secure_session_files,
    session_base,
)
from .store import read_json, write_json_atomic

__all__ = [
    "pending_path",
    "load_pending",
    "save_pending",
    "clear_pending",
    "describe_me",
    "send_code",
    "confirm_login",
]

# 待登录状态的有效期（秒）：超过则要求重新发码
PENDING_TTL_SECONDS = 15 * 60


def pending_path(data_dir: Path) -> Path:
    """
    返回待登录状态文件路径。

    :param data_dir: 插件数据目录
    :return Path: ``login_pending.json`` 路径
    """

    return Path(data_dir) / "login_pending.json"


def load_pending(data_dir: Path) -> Dict[str, Any]:
    """
    读取待登录状态。

    :param data_dir: 插件数据目录
    :return Dict[str, Any]: ``账号标识 -> {phone, phone_code_hash, ts}``
    """

    data = read_json(pending_path(data_dir), {})
    return data if isinstance(data, dict) else {}


def save_pending(data_dir: Path, data: Dict[str, Any]) -> None:
    """
    写入待登录状态。

    :param data_dir: 插件数据目录
    :param data: 待登录状态字典
    :return None
    """

    write_json_atomic(pending_path(data_dir), data)


def clear_pending(data_dir: Path, account_key: str) -> None:
    """
    清除某个账号的待登录状态。

    :param data_dir: 插件数据目录
    :param account_key: 账号标识
    :return None
    """

    data = load_pending(data_dir)
    if account_key in data:
        data.pop(account_key, None)
        save_pending(data_dir, data)


def describe_me(me: Any) -> Dict[str, Any]:
    """
    把 telethon 的 me 对象整理成可序列化信息。

    :param me: ``client.get_me()`` 的返回值
    :return Dict[str, Any]: ``{name, username, user_id, phone}``
    """

    if not me:
        return {"name": "", "username": "", "user_id": "", "phone": ""}
    name = " ".join(
        part for part in (getattr(me, "first_name", ""), getattr(me, "last_name", "")) if part
    )
    return {
        "name": name or getattr(me, "username", "") or "",
        "username": getattr(me, "username", "") or "",
        "user_id": str(getattr(me, "id", "") or ""),
        # 手机号只留打码形式，避免明文进 state.json 与日志（2026-10-11 加固）
        "phone": mask_phone(getattr(me, "phone", "") or ""),
    }


async def send_code(
    data_dir: Path,
    account: AccountConfig,
    proxy: Optional[Tuple[str, str, int]],
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    阶段一：向账号手机号发送 Telegram 登录验证码。

    :param data_dir: 插件数据目录
    :param account: 账号配置（需含手机号）
    :param proxy: 代理元组，None 表示直连
    :param timeout: 整体超时秒数
    :return Dict[str, Any]: ``{ok, message, phone_code_hash?}``
    """

    if not account.phone:
        return {"ok": False, "message": f"账号 {account.key} 没填手机号"}
    try:
        client = build_client(data_dir, account, proxy)
    except RuntimeError as error:
        return {"ok": False, "message": str(error)}

    async def _run() -> Dict[str, Any]:
        """执行发码流程。"""

        await client.connect()
        secure_session_files(data_dir, account.key)
        if await client.is_user_authorized():
            me = describe_me(await client.get_me())
            return {
                "ok": True,
                "already": True,
                "message": f"该账号已登录：{me['name']} @{me['username']}",
            }
        sent = await client.send_code_request(account.phone)
        pending = load_pending(data_dir)
        pending[account.key] = {
            "phone": account.phone,
            "phone_code_hash": sent.phone_code_hash,
            "ts": time.time(),
        }
        save_pending(data_dir, pending)
        return {
            "ok": True,
            "message": (
                f"验证码已发往 {mask_phone(account.phone)}，"
                "请查看 Telegram 后执行「确认登录」"
            ),
        }

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "message": f"发送验证码超时（>{timeout}s），请检查代理"}
    except Exception as error:  # pylint: disable=broad-except
        return {"ok": False, "message": f"发送验证码失败：{type(error).__name__}: {error}"}
    finally:
        try:
            await client.disconnect()
        except Exception:  # pylint: disable=broad-except
            pass


async def confirm_login(
    data_dir: Path,
    account: AccountConfig,
    code: str,
    password: str,
    proxy: Optional[Tuple[str, str, int]],
    timeout: int = 90,
) -> Dict[str, Any]:
    """
    阶段二：用验证码（必要时加两步验证密码）完成登录。

    :param data_dir: 插件数据目录
    :param account: 账号配置
    :param code: Telegram 收到的登录验证码
    :param password: 两步验证密码（未启用两步验证时留空）
    :param proxy: 代理元组，None 表示直连
    :param timeout: 整体超时秒数
    :return Dict[str, Any]: ``{ok, message, me?}``
    """

    if not code:
        return {"ok": False, "message": "验证码为空"}
    pending = load_pending(data_dir).get(account.key) or {}
    phone_code_hash = pending.get("phone_code_hash")
    phone = pending.get("phone") or account.phone
    if not phone_code_hash:
        return {"ok": False, "message": "没有待确认的登录（请先点「发送验证码」）"}
    if time.time() - float(pending.get("ts") or 0) > PENDING_TTL_SECONDS:
        clear_pending(data_dir, account.key)
        return {"ok": False, "message": "验证码已过期（超过 15 分钟），请重新发送"}

    telethon = import_telethon()
    needs_password_error = telethon.errors.SessionPasswordNeededError
    try:
        client = build_client(data_dir, account, proxy)
    except RuntimeError as error:
        return {"ok": False, "message": str(error)}

    async def _run() -> Dict[str, Any]:
        """执行确认登录流程。"""

        await client.connect()
        secure_session_files(data_dir, account.key)
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except needs_password_error:
            if not password:
                return {
                    "ok": False,
                    "need_password": True,
                    "message": "该账号启用了两步验证，请在配置里填写「两步验证密码」后重试",
                }
            await client.sign_in(password=password)
        me = describe_me(await client.get_me())
        clear_pending(data_dir, account.key)
        return {
            "ok": True,
            "me": me,
            "message": f"登录成功：{me['name']} @{me['username']}（id {me['user_id']}）",
        }

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "message": f"确认登录超时（>{timeout}s）"}
    except Exception as error:  # pylint: disable=broad-except
        return {"ok": False, "message": f"确认登录失败：{type(error).__name__}: {error}"}
    finally:
        try:
            await client.disconnect()
        except Exception:  # pylint: disable=broad-except
            pass
