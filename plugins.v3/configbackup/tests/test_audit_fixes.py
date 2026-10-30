# -*- coding: utf-8 -*-
"""
2026-10-30 审计修复的回归用例。

覆盖审计确认的问题：备份包加密与认证、还原回滚与白名单、数据库导出一致性、
临时目录清理、安全备份解耦、清理保护、配置容错与远端下载防覆盖。

这些用例的共同点：**都验证「失败时不留坏状态」**，而旧实现的主要缺陷正是
失败路径无人看守（备份成功但还原必然失败、失败后残留临时目录等）。
"""

import stat
import zipfile
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.runtime.config import settings
from configbackup import ConfigBackup
from tests.test_boundary import _BoundaryBase

#: 测试用加密口令（不写入任何源码产物，仅测试内使用）
PASS = "audit-test-pass-2026"


class _AuditBase(_BoundaryBase):
    """审计用例夹具：假 /config、假临时目录、可指定勾选项的备份入口。"""

    def setUp(self):
        super().setUp()
        self.cfg = Path(self.base) / "cfg"
        (self.cfg / "cookies").mkdir(parents=True)
        (self.cfg / "plugins" / "demo").mkdir(parents=True)
        (self.cfg / "plugins" / "demo" / "conf.json").write_text("{\"a\": 1}")
        (self.cfg / "app.env").write_text("A=1\n")
        (self.cfg / "category.yaml").write_text("x: 1\n")
        self.tmp = Path(self.base) / "tmp"
        self.tmp.mkdir()

    def run_backup(self, parts=("system",)):
        """按指定勾选跑一次备份，返回 (是否成功, 消息)。"""
        self.plugin._backup_parts = tuple(parts)
        with mock.patch.object(settings, "CONFIG_PATH", str(self.cfg)), \
                mock.patch.object(settings, "TEMP_PATH", str(self.tmp)):
            return self.plugin._ConfigBackup__run_backup()

    def packages(self):
        """备份目录内的备份包（明文与加密）。"""
        return sorted(
            p for p in Path(self.backup_dir).glob(f"{ConfigBackup._prefix}*")
            if p.name.endswith((".zip", ".zip.enc"))
        )

    def make_zip(self, name, members, subdir=""):
        """在备份目录内造一个备份包。"""
        folder = Path(self.backup_dir) / subdir if subdir else Path(self.backup_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{ConfigBackup._prefix}{name}.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for member, content in members.items():
                zf.writestr(member, content)
        return path


class TestEncryption(_AuditBase):
    """备份包加密、认证与权限。"""

    def test_encrypted_backup_leaves_no_plaintext(self):
        """设置口令后：只留 .zip.enc，包头可读，明文包不得残留。"""
        self.plugin._encrypt_pass = PASS

        ok, msg = self.run_backup()

        self.assertTrue(ok, msg)
        names = [p.name for p in self.packages()]
        self.assertEqual(len(names), 1, f"备份目录内应只有一个包，实际：{names}")
        self.assertTrue(names[0].endswith(".zip.enc"), names)
        self.assertFalse(
            (Path(self.backup_dir) / names[0][:-4]).exists(), "明文 zip 必须删除"
        )
        self.assertEqual(names[0][:-4].split(".")[-1], "zip")
        header = ConfigBackup._ConfigBackup__read_enc_header(self.packages()[0])
        self.assertTrue(header and header.get("encrypted"), header)

    def test_encrypted_backup_file_mode_is_private(self):
        """加密包权限必须是 0600，避免同机其它账号读到凭据。"""
        self.plugin._encrypt_pass = PASS
        ok, msg = self.run_backup()
        self.assertTrue(ok, msg)

        mode = stat.S_IMODE(self.packages()[0].stat().st_mode)
        self.assertEqual(mode, 0o600, f"实际权限：{oct(mode)}")

    def test_verify_requires_correct_password(self):
        """口令错/包被改动时校验必须失败（GCM 认证）。"""
        self.plugin._encrypt_pass = PASS
        ok, msg = self.run_backup()
        self.assertTrue(ok, msg)
        package = self.packages()[0]

        self.assertTrue(self.plugin._ConfigBackup__verify_backup(package))

        self.plugin._encrypt_pass = "wrong-pass"
        self.assertFalse(self.plugin._ConfigBackup__verify_backup(package))

        self.plugin._encrypt_pass = PASS
        raw = bytearray(package.read_bytes())
        raw[-20] ^= 0x01          # 翻转认证标签前的一个密文字节
        package.write_bytes(bytes(raw))
        self.assertFalse(self.plugin._ConfigBackup__verify_backup(package), "篡改必须被发现")

    def test_encrypted_package_restores_config(self):
        """加密包能正常还原文件，且还原后不留临时目录。"""
        self.plugin._encrypt_pass = PASS
        ok, msg = self.run_backup(parts=("system",))
        self.assertTrue(ok, msg)
        package = self.packages()[0]

        (self.cfg / "app.env").write_text("A=2\n")
        with mock.patch.object(settings, "CONFIG_PATH", str(self.cfg)), \
                mock.patch.object(settings, "TEMP_PATH", str(self.tmp)), \
                mock.patch.object(settings, "DB_TYPE", "sqlite"):
            ok, msg = self.plugin._ConfigBackup__restore(package)

        self.assertTrue(ok, msg)
        self.assertEqual((self.cfg / "app.env").read_text(), "A=1\n")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), [],
                         "还原后临时目录必须清空")

    def test_upload_refuses_plaintext(self):
        """未设口令时拒绝上传：明文包含 Token 与 Cookie。"""
        self.plugin._encrypt_pass = ""
        self.plugin._backup_parts = tuple(ConfigBackup._ALL_PARTS)
        self.plugin._webdav_enabled = True
        with mock.patch.object(settings, "CONFIG_PATH", str(self.cfg)), \
                mock.patch.object(settings, "TEMP_PATH", str(self.tmp)):
            ok, msg = self.plugin._ConfigBackup__run_backup()

        self.assertFalse(ok, "未加密的包不得上传")
        self.assertIn("加密口令", msg)
        self.assertIn("本地备份已生成", msg)


class TestExtraPathSafety(_AuditBase):
    """附加路径：白名单校验与同名冲突。"""

    def test_manifest_path_outside_whitelist_is_skipped(self):
        """包内清单写到白名单之外时跳过，绝不落盘。"""
        allowed = Path(self.base) / "allowed"
        allowed.mkdir()
        outside = Path(self.base) / "outside-target"
        extra_dir = Path(self.base) / "extra"
        extra_dir.mkdir()
        payload = extra_dir / outside.name
        payload.write_text("evil")
        (extra_dir / "extra_paths.txt").write_text(f"{outside.name}\t{outside}\n")

        self.plugin._extra_paths = str(allowed)
        ok, msg = self.plugin._ConfigBackup__restore_extra_paths(
            extra_dir, extra_dir / "extra_paths.txt"
        )

        self.assertFalse(outside.exists(), "白名单之外的路径不得写入")
        self.assertIn("跳过", msg)

    def test_same_named_paths_do_not_collide(self):
        """两个同名目录各自还原到自己的位置，互不覆盖。"""
        first = Path(self.base) / "one" / "data"
        second = Path(self.base) / "two" / "data"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        (first / "mark.txt").write_text("first")
        (second / "mark.txt").write_text("second")

        self.plugin._extra_paths = f"{first}\n{second}"
        ok, msg = self.run_backup(parts=("extra",))
        self.assertTrue(ok, msg)
        package = self.packages()[0]

        (first / "mark.txt").write_text("changed")
        (second / "mark.txt").write_text("changed")
        with mock.patch.object(settings, "CONFIG_PATH", str(self.cfg)), \
                mock.patch.object(settings, "TEMP_PATH", str(self.tmp)):
            ok, msg = self.plugin._ConfigBackup__restore(package)

        self.assertTrue(ok, msg)
        self.assertEqual((first / "mark.txt").read_text(), "first")
        self.assertEqual((second / "mark.txt").read_text(), "second")


class TestRestoreRollback(_AuditBase):
    """文件阶段失败必须回滚，不允许半覆盖状态。"""

    def test_file_stage_failure_rolls_back(self):
        """附加路径缺失导致失败时，已写入的 app.env 必须恢复原样。"""
        missing = Path(self.base) / "missing-target"
        package = self.make_zip("20260918000000", {
            "app.env": "RESTORED=1\n",
            "extra/extra_paths.txt": f"{missing.name}\t{missing}\n",
        })

        (self.cfg / "app.env").write_text("ORIGINAL=1\n")
        self.plugin._extra_paths = str(missing)
        with mock.patch.object(settings, "CONFIG_PATH", str(self.cfg)), \
                mock.patch.object(settings, "TEMP_PATH", str(self.tmp)):
            ok, msg = self.plugin._ConfigBackup__restore(package)

        self.assertFalse(ok, msg)
        self.assertIn("回滚", msg)
        self.assertEqual(
            (self.cfg / "app.env").read_text(), "ORIGINAL=1\n",
            "失败后必须恢复原配置",
        )

    def test_restoring_blocks_concurrent_backup(self):
        """还原进行中拒绝备份，避免备份到半还原状态。"""
        self.plugin._restoring = True
        result = self.plugin.api_backup()
        self.assertFalse(result["success"])
        self.assertIn("还原正在进行", result["message"])

    def test_safety_backup_is_local_only_and_silent(self):
        """还原前安全备份只做本地、不发通知、不清理待还原包。"""
        pending_name = f"{ConfigBackup._prefix}20260918000000.zip"
        self.make_zip("20260918000000", {"category.yaml": "x: 1\n"})
        self.plugin._ConfigBackup__set_pending_restore({"filename": pending_name})
        self.plugin._notify = True
        self.plugin._webdav_enabled = True

        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__backup", return_value=(False, "磁盘已满")
        ) as safety, mock.patch.object(
            ConfigBackup, "_ConfigBackup__restore"
        ) as restore, mock.patch.object(
            ConfigBackup, "post_message"
        ) as notify:
            result = self.plugin.api_restore(filename="", confirm="1")

        self.assertFalse(result["success"])
        self.assertIn("中止", result["message"])
        self.assertFalse(restore.called)
        self.assertFalse(notify.called, "安全备份不得发通知")
        kwargs = safety.call_args.kwargs
        self.assertTrue(kwargs.get("local_only"), "安全备份不得上传远端")
        self.assertFalse(kwargs.get("notify", True))
        self.assertIn(pending_name, kwargs.get("protect") or ())


class TestDatabaseExportAndRestore(_AuditBase):
    """数据库导出/还原的契约：完整导出、单事务、结构校验。"""

    def test_dump_uses_pg_dump_with_full_structure(self):
        """PostgreSQL 导出必须走 pg_dump（自写 SQL 会丢约束）。"""
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        def fake_run(cmd, **kwargs):
            target = Path(cmd[cmd.index("--file") + 1])
            target.write_text("-- dump\n" + "x" * 4096)
            return completed

        with mock.patch.object(settings, "DB_TYPE", "postgresql"), \
                mock.patch("configbackup.shutil.which", return_value="/usr/bin/pg_dump"), \
                mock.patch("configbackup.subprocess.run", side_effect=fake_run) as run:
            ok, msg = self.plugin._ConfigBackup__dump_database(self.tmp)

        self.assertTrue(ok, msg)
        cmd = run.call_args.args[0]
        self.assertIn("pg_dump", cmd[0])
        for flag in ("--clean", "--if-exists", "--no-owner", "--no-privileges"):
            self.assertIn(flag, cmd, f"缺少 {flag} 会导出不完整结构")

    def test_dump_failure_reports_pg_dump_error(self):
        """pg_dump 失败要带上真实原因，且不留半成品。"""
        completed = mock.Mock(returncode=1, stdout="", stderr="FATAL: no connection")
        with mock.patch.object(settings, "DB_TYPE", "postgresql"), \
                mock.patch("configbackup.shutil.which", return_value="/usr/bin/pg_dump"), \
                mock.patch("configbackup.subprocess.run", return_value=completed):
            ok, msg = self.plugin._ConfigBackup__dump_database(self.tmp)

        self.assertFalse(ok)
        self.assertIn("FATAL", msg)
        self.assertFalse((self.tmp / "postgresql_backup.sql").exists())

    def test_restore_uses_single_transaction_psql(self):
        """还原必须单事务 + 遇错即停，失败自动回滚。"""
        sql_file = self.tmp / "postgresql_backup.sql"
        sql_file.write_text("CREATE TABLE t (id int);\n")
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["script"] = Path(cmd[cmd.index("--file") + 1]).read_text()
            return completed

        with mock.patch.object(settings, "DB_TYPE", "postgresql"), \
                mock.patch("configbackup.shutil.which", return_value="/usr/bin/psql"), \
                mock.patch("configbackup.subprocess.run", side_effect=fake_run):
            ok, msg = self.plugin._ConfigBackup__restore_database(
                sql_file, {"primary_keys": 3}
            )

        self.assertTrue(ok, msg)
        cmd = captured["cmd"]
        self.assertIn("--single-transaction", cmd)
        self.assertIn("ON_ERROR_STOP=1", cmd)
        self.assertIn("RAISE EXCEPTION", captured["script"], "还原后必须校验结构")

    def test_schema_check_covers_constraints(self):
        """结构校验覆盖表/主键/唯一/外键/索引/序列。"""
        sql = ConfigBackup._ConfigBackup__schema_check_sql({
            "tables": 36, "primary_keys": 16, "unique_constraints": 17,
            "foreign_keys": 4, "indexes": 120, "sequences": 10,
        })
        for key in ("tables", "primary_keys", "unique_constraints",
                    "foreign_keys", "indexes", "sequences"):
            self.assertIn(key, sql)

    def test_pg_env_keeps_password_out_of_argv(self):
        """密码走 PGPASSWORD 环境变量，并设置锁等待上限。"""
        with mock.patch.object(settings, "DB_POSTGRESQL_PASSWORD", "secret"):
            env = self.plugin._ConfigBackup__pg_env()

        self.assertEqual(env.get("PGPASSWORD"), "secret")
        self.assertIn("lock_timeout", env.get("PGOPTIONS", ""))


class TestBackupReliability(_AuditBase):
    """临时目录清理、清理保护、配置容错。"""

    def test_failed_backup_leaves_no_temp_dir(self):
        """中途失败也必须清掉临时目录，避免反复失败撑爆配置卷。"""
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__copy_plugins", return_value=(False, "boom")
        ):
            ok, msg = self.run_backup(parts=("plugins",))

        self.assertFalse(ok)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), [])

    def test_cleanup_protects_pending_package(self):
        """清理旧备份时必须保留正在待还原的那一份。"""
        for stamp in ("20260101000000", "20260102000000", "20260103000000"):
            self.make_zip(stamp, {"category.yaml": "x: 1\n"})
        oldest = f"{ConfigBackup._prefix}20260101000000.zip"
        self.plugin._keep_count = 1
        self.plugin._keep_days = 0

        deleted = self.plugin._ConfigBackup__clean_old_backups(
            Path(self.backup_dir), protect={oldest}
        )

        remaining = sorted(p.name for p in Path(self.backup_dir).glob("bk_*"))
        self.assertTrue((Path(self.backup_dir) / oldest).exists(), "待还原包被误删")
        self.assertEqual(len(remaining), 1, f"最终只应保留受保护的那份，实际：{remaining}")
        self.assertEqual(deleted, 2, "另外两份（超量且未受保护）应被删除")

    def test_invalid_config_falls_back_with_warning(self):
        """非法配置回退默认值并记录告警，不能让插件加载失败。"""
        self.plugin.init_plugin({
            "enabled": True,
            "cron": "not a cron",
            "backup_dir": "/",
            "keep_count": "abc",
            "keep_days": -5,
            "webdav_timeout": "oops",
            "extra_paths": "relative/path\n/etc/hosts",
        })

        self.assertEqual(self.plugin._cron, "")
        self.assertEqual(self.plugin._backup_dir, "/config/backup")
        self.assertEqual(self.plugin._keep_count, 10)
        self.assertEqual(self.plugin._keep_days, 0)
        self.assertEqual(self.plugin._webdav_timeout, 300)
        self.assertNotIn("relative/path", self.plugin._extra_paths)
        self.assertTrue(self.plugin._config_warnings)

    def test_invalid_cron_skips_schedule(self):
        """cron 非法时不注册定时服务，避免调度器启动失败。"""
        self.plugin.init_plugin({"enabled": True, "cron": "bad cron"})
        self.assertEqual(self.plugin.get_service(), [])
