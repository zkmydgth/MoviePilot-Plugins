# -*- coding: utf-8 -*-
"""
WebDAV 备份闭环集成测试（v3.2.0 模块 B）：上传 → 远端列表 → 下载/下载并还原 → 远端清理。

为什么单独做
------------
模块 B 的价值全在"串起来之后"，单测客户端本体（见 ``test_webdav_client.py``）覆盖不到：

- 上传时机必须是**包自检通过之后**（传了半包上去比不传更糟）；
- 上传失败要**整次标红**，但文案必须写明「本地备份已生成、可用」——否则用户以为白备份了；
- 远端清理必须与本地**同一套规则**，且绝不能碰用户放在同目录的其它文件；
- 下载回来的包必须重新校验：损坏包留在本地会占保留名额、还会被当成可用备份。

覆盖清单
--------
1. 备份成功后自动上传（远端出现同名同内容的包）
2. 上传失败：整次备份判失败，文案含「本地备份已生成…（可用）」+「上传失败」
3. 未启用 WebDAV 时不上传、不碰远端
4. 远端清理：超出份数删最旧；保留天数内不删；非 ``bk_`` 前缀文件不碰
5. 远端保留份数留空 → 跟随本地 ``keep_count``
6. 下载：落到本地备份目录并校验；远端损坏包下载后删除并报错
7. 下载并还原：下载后进入两阶段确认（未确认不还原），可取消
8. 远端列表：按时间倒序、过滤非法文件名
9. 测试连接接口：未配置地址 / 正常连通 / 认证失败
"""

import shutil
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.runtime.config import settings
from configbackup import ConfigBackup
from tests.test_boundary import _BoundaryBase
from tests.test_webdav_client import _FakeDAVServer


class _RemoteBackupBase(_BoundaryBase):
    """夹具：假 /config + 假 WebDAV 服务端 + 已配置好 WebDAV 的插件实例。"""

    def setUp(self):
        super().setUp()
        # 假 /config
        self.cfg = Path(self.base) / "cfg"
        self.cfg.mkdir()
        (self.cfg / "cookies").mkdir()
        (self.cfg / "cookies" / "site.json").write_text("{}")
        (self.cfg / "plugins" / "a").mkdir(parents=True)
        (self.cfg / "plugins" / "a" / "d.json").write_text("{}")
        (self.cfg / "app.env").write_text("A=1")
        (self.cfg / "category.yaml").write_text("x: 1")
        self.tmp = Path(self.base) / "tmp"
        self.tmp.mkdir()

        # 假 WebDAV 服务端（复用客户端测试里的实现）
        self.dav_root = Path(self.base) / "davroot"
        self.dav_root.mkdir()
        self.dav = _FakeDAVServer(self.dav_root)
        self.dav.__enter__()
        self.addCleanup(self.dav.__exit__, None, None, None)

        # 插件侧的 WebDAV 配置
        self.plugin._webdav_enabled = True
        self.plugin._webdav_url = self.dav.url
        self.plugin._webdav_user = "user"
        self.plugin._webdav_pass = "pass"
        self.plugin._webdav_dir = "/MoviePilot"
        self.plugin._webdav_keep = ""
        self.plugin._webdav_timeout = 20
        self.plugin._keep_days = 0          # 默认只看份数，按需在用例里改

    # ------------------------------------------------------------------
    @property
    def remote_dir(self) -> Path:
        """远端目录在假服务端上的本地落点。"""
        return self.dav_root / "MoviePilot"

    def run_backup(self):
        """按当前配置跑一次完整备份，返回 (是否成功, 消息)。"""
        self.plugin._backup_parts = tuple(ConfigBackup._ALL_PARTS)
        with mock.patch.object(settings, "CONFIG_PATH", str(self.cfg)), \
                mock.patch.object(settings, "TEMP_PATH", str(self.tmp)):
            return self.plugin._ConfigBackup__run_backup()

    def local_packages(self):
        """本地备份包列表。"""
        return sorted(Path(self.backup_dir).glob(f"{ConfigBackup._prefix}*.zip"))

    def make_remote_package(self, stamp: str, payload: bytes = b"remote") -> Path:
        """在远端造一个合法备份包（bk_<时间戳>.zip）。"""
        self.remote_dir.mkdir(parents=True, exist_ok=True)
        path = self.remote_dir / f"{ConfigBackup._prefix}{stamp}.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("backup_manifest.json", '{"parts": ["system"]}')
            zf.writestr("app.env", payload.decode("utf-8", "ignore"))
        return path


class TestUploadOnBackup(_RemoteBackupBase):
    """备份成功后的上传与失败语义。"""

    def test_backup_uploads_package(self):
        """备份成功后，远端必须出现同名同内容的包，且消息写明已上传。"""
        ok, msg = self.run_backup()

        self.assertTrue(ok, msg)
        self.assertIn("已上传到 WebDAV", msg)
        remote = sorted(self.remote_dir.glob(f"{ConfigBackup._prefix}*.zip"))
        self.assertEqual(len(remote), 1, f"远端应有 1 个包，实际：{remote}")
        self.assertEqual(
            remote[0].read_bytes(), self.local_packages()[0].read_bytes(),
            "上传内容必须与本地包逐字节一致",
        )

    def test_upload_failure_fails_backup_but_keeps_local_package(self):
        """上传失败：整次备份判失败，但必须写清本地包已生成且可用。"""
        self.plugin._webdav_pass = "wrong"

        ok, msg = self.run_backup()

        self.assertFalse(ok, "上传失败必须让整次备份标红")
        self.assertIn("本地备份已生成", msg)
        self.assertIn("可用", msg)
        self.assertIn("上传失败", msg)
        self.assertIn("401", msg)
        self.assertEqual(len(self.local_packages()), 1, "本地包必须仍然存在")

    def test_disabled_webdav_does_not_upload(self):
        """未启用 WebDAV：不连接远端、远端目录不该被建出来。"""
        self.plugin._webdav_enabled = False

        ok, msg = self.run_backup()

        self.assertTrue(ok, msg)
        self.assertNotIn("已上传", msg)
        self.assertFalse(self.remote_dir.exists(), "未启用时不应在远端建目录")


class TestRemoteCleanup(_RemoteBackupBase):
    """远端清理：与本地同一套规则，且只动自己前缀的文件。"""

    def test_excess_oldest_removed(self):
        """超出保留份数时删最旧的（本次上传的新包要被算进去）。"""
        self.make_remote_package("20260101000000")
        self.make_remote_package("20260102000000")
        self.plugin._webdav_keep = "2"
        self.plugin._keep_days = 0

        ok, msg = self.run_backup()

        self.assertTrue(ok, msg)
        self.assertIn("远端清理旧备份 1 份", msg)
        names = sorted(p.name for p in self.remote_dir.glob("bk_*.zip"))
        self.assertNotIn(f"{ConfigBackup._prefix}20260101000000.zip", names, "最旧的应被删")
        self.assertEqual(len(names), 2)

    def test_keep_days_protects_recent(self):
        """保留天数内的远端包一律不删（即使超出份数）。"""
        self.make_remote_package("20260101000000")
        self.make_remote_package("20260102000000")
        self.plugin._keep_count = 1
        self.plugin._keep_days = 36500        # 极大：所有历史包都在保护期内

        ok, msg = self.run_backup()

        self.assertTrue(ok, msg)
        self.assertNotIn("远端清理", msg)
        self.assertEqual(len(list(self.remote_dir.glob("bk_*.zip"))), 3)

    def test_unrelated_remote_files_untouched(self):
        """远端同目录里用户的其它文件必须原样保留（不能被当备份删掉）。"""
        self.remote_dir.mkdir(parents=True, exist_ok=True)
        (self.remote_dir / "important.zip").write_bytes(b"user file")
        (self.remote_dir / "note.txt").write_text("hello")
        self.plugin._webdav_keep = "1"
        self.plugin._keep_days = 0

        ok, msg = self.run_backup()

        self.assertTrue(ok, msg)
        self.assertTrue((self.remote_dir / "important.zip").exists(), "非 bk_ 前缀文件不得被清理")
        self.assertTrue((self.remote_dir / "note.txt").exists())

    def test_remote_keep_follows_local_when_blank(self):
        """远端保留份数留空 → 跟随本地 keep_count；填了则以远端为准。"""
        self.plugin._webdav_keep = ""
        self.plugin._keep_count = 4
        self.assertEqual(self.plugin._ConfigBackup__remote_keep(), 4)

        self.plugin._webdav_keep = "7"
        self.assertEqual(self.plugin._ConfigBackup__remote_keep(), 7)

        self.plugin._webdav_keep = "abc"        # 非法值回落跟随
        self.assertEqual(self.plugin._ConfigBackup__remote_keep(), 4)


class TestDownload(_RemoteBackupBase):
    """下载：落地到本地备份目录，并重新校验完整性。"""

    def test_download_puts_package_locally(self):
        """下载远端包到本地备份目录，内容一致。"""
        remote = self.make_remote_package("20260101000000")

        result = self.plugin.api_webdav_download(filename=remote.name)

        self.assertTrue(result["success"], result["message"])
        local = Path(self.backup_dir) / remote.name
        self.assertTrue(local.exists())
        self.assertEqual(local.read_bytes(), remote.read_bytes())

    def test_corrupt_remote_package_is_deleted(self):
        """远端损坏包：下载后校验失败 → 本地删除并报错（不许留成"可用备份"）。"""
        self.remote_dir.mkdir(parents=True, exist_ok=True)
        broken = self.remote_dir / f"{ConfigBackup._prefix}20260101000000.zip"
        broken.write_bytes(b"not a zip at all")

        result = self.plugin.api_webdav_download(filename=broken.name)

        self.assertFalse(result["success"])
        self.assertIn("校验失败", result["message"])
        self.assertFalse((Path(self.backup_dir) / broken.name).exists(), "损坏包必须删掉")

    def test_download_rejects_illegal_name(self):
        """非法文件名（路径穿越 / 非备份名）必须拒绝。"""
        for bad in ("../bk_20260101000000.zip", "evil.zip", "/tmp/bk_20260101000000.zip"):
            with self.subTest(name=bad):
                result = self.plugin.api_webdav_download(filename=bad)
                self.assertFalse(result["success"], f"{bad} 应被拒绝")

    def test_missing_remote_package_reports_error(self):
        """远端没有这个包：报 404，不产生本地残留。"""
        result = self.plugin.api_webdav_download(filename=f"{ConfigBackup._prefix}20260101000000.zip")

        self.assertFalse(result["success"])
        self.assertIn("404", result["message"])


class TestDownloadAndRestore(_RemoteBackupBase):
    """下载并还原：复用本地还原的两阶段确认。"""

    def test_download_then_pending_then_cancel(self):
        """下载并还原 = 下载 + 选中为待还原；取消后待还原状态清空。"""
        remote = self.make_remote_package("20260101000000")

        result = self.plugin.api_webdav_restore(filename=remote.name)

        self.assertTrue(result["success"], result["message"])
        pending = self.get_pending()
        self.assertIsNotNone(pending, "应进入待还原状态（两阶段确认的第一步）")
        self.assertEqual(pending["filename"], remote.name)

        cancel = self.plugin.api_webdav_restore(confirm="cancel")
        self.assertTrue(cancel["success"])
        self.assertIsNone(self.get_pending(), "取消后不应再有待还原状态")

    def test_download_failure_does_not_enter_pending(self):
        """下载失败：不得进入待还原状态（否则一点确认就还原了个不存在的包）。"""
        result = self.plugin.api_webdav_restore(filename=f"{ConfigBackup._prefix}20260101000000.zip")

        self.assertFalse(result["success"])
        self.assertIsNone(self.get_pending())


class TestRemoteListing(_RemoteBackupBase):
    """远端列表：排序、过滤、以及测试连接接口。"""

    def test_list_sorted_and_filtered(self):
        """只列备份包，且按时间倒序。"""
        self.make_remote_package("20260101000000")
        self.make_remote_package("20260201000000")
        self.remote_dir.mkdir(parents=True, exist_ok=True)
        (self.remote_dir / "other.zip").write_bytes(b"x")

        result = self.plugin.api_webdav_list()

        self.assertTrue(result["success"], result["message"])
        names = [item["name"] for item in result["data"]]
        self.assertEqual(names, [
            f"{ConfigBackup._prefix}20260201000000.zip",
            f"{ConfigBackup._prefix}20260101000000.zip",
        ])

    def test_test_endpoint_without_url(self):
        """未配置地址：直接给出中文原因，不发请求。"""
        self.plugin._webdav_url = ""

        result = self.plugin.api_webdav_test()

        self.assertFalse(result["success"])
        self.assertIn("未配置 WebDAV 地址", result["message"])

    def test_test_endpoint_ok_and_bad_password(self):
        """测试连接：正常连通为成功；密码错要报 401。"""
        ok = self.plugin.api_webdav_test()
        self.assertTrue(ok["success"], ok["message"])

        self.plugin._webdav_pass = "wrong"
        bad = self.plugin.api_webdav_test()
        self.assertFalse(bad["success"])
        self.assertIn("401", bad["message"])


if __name__ == "__main__":
    unittest.main()
