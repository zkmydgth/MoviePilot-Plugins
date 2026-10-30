# -*- coding: utf-8 -*-
"""
插件目录备份的敏感文件排除（2026-10-30 审计加固）。

背景：``/config/plugins`` 会整目录进备份包，而 Telegram 类插件的 ``.session``
文件是账号授权密钥——拿到它等于拿到该账号的登录态。备份包落在共享目录并按保留
策略长期留存，因此 session 必须排除在外，而插件的其它状态文件仍要备份。
"""

from pathlib import Path
from typing import Tuple
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.runtime.config import settings
from configbackup import ConfigBackup
from tests.test_boundary import _BoundaryBase


class TestPluginSecretExclusion(_BoundaryBase):
    """插件配置备份必须排除 session 授权密钥。"""

    def _stage(self) -> Tuple[Path, Path]:
        """
        造一份含 session 与普通数据文件的假插件目录。

        :return Tuple[Path, Path]: ``(假 /config 路径, 备份临时目录)``
        """

        cfg = Path(self.base) / "cfg"
        plugin_dir = cfg / "plugins" / "TgSignin"
        (plugin_dir / "sessions").mkdir(parents=True)
        (plugin_dir / "sessions" / "acc_acc1.session").write_bytes(b"authorized-key")
        (plugin_dir / "sessions" / "acc_acc1.session-journal").write_bytes(b"journal")
        (plugin_dir / "state.json").write_text("{}", encoding="utf-8")
        (plugin_dir / "login_pending.json").write_text("{}", encoding="utf-8")
        stage = Path(self.base) / "stage"
        stage.mkdir()
        return cfg, stage

    def test_session_files_are_not_copied(self) -> None:
        """session 与其 journal 不进备份，普通状态文件照常备份。"""

        cfg, stage = self._stage()
        with mock.patch.object(settings, "CONFIG_PATH", str(cfg)):
            ok, message = self.plugin._ConfigBackup__copy_plugins(stage)
        self.assertTrue(ok, message)
        names = {path.name for path in stage.rglob("*") if path.is_file()}
        self.assertNotIn("acc_acc1.session", names)
        self.assertNotIn("acc_acc1.session-journal", names)
        self.assertIn("state.json", names)
        self.assertIn("login_pending.json", names)

    def test_other_plugin_files_are_kept(self) -> None:
        """排除规则只针对 session，不波及同目录下的其它文件。"""

        cfg, stage = self._stage()
        extra = cfg / "plugins" / "OtherPlugin" / "data.db"
        extra.parent.mkdir(parents=True)
        extra.write_bytes(b"db")
        with mock.patch.object(settings, "CONFIG_PATH", str(cfg)):
            ok, message = self.plugin._ConfigBackup__copy_plugins(stage)
        self.assertTrue(ok, message)
        names = {path.name for path in stage.rglob("*") if path.is_file()}
        self.assertIn("data.db", names)
