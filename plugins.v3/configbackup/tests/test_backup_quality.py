# -*- coding: utf-8 -*-
"""
备份质量测试（v3.1.0）：通知覆盖、包完整性自检、排序依据、待确认状态有效期。

为什么单独做
------------
这些都是「成功之后才暴露」的缺陷，正常路径用例永远看不到：

- 备份**失败**不通知 —— 定时任务静默失败，直到真要恢复才发现一个包都没有
- 打包中断留下的**半成品 zip** 照样占保留名额、照样被当成可还原备份
- 按 **ctime** 排序 —— 文件经 rsync / 拷贝到本机后 ctime 变成拷贝时间，
  会把最新备份当成最旧的删掉
- 待确认状态**永不失效** —— 隔天回页面还挂着「确认还原」按钮

覆盖清单
--------
1. 通知：成功与失败都要发；场景取配置；未知场景回落「插件」
2. 自检：校验失败的包隔离为 ``.broken``，不留可用 ``.zip``，且不计入清理
3. 排序：以文件名时间戳为准，mtime 被颠倒也不得误删最新备份
4. 待确认状态：超 TTL 自动作废；老格式（无 ts）不判过期
5. SQLite：备份前 checkpoint；还原前清残留 ``-wal`` / ``-shm``
6. 并发：已有备份在跑时拒绝第二次
"""

import json
import os
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.runtime.config import settings
from configbackup import ConfigBackup
from tests.test_boundary import _BoundaryBase


class TestNotifyCoverage(_BoundaryBase):
    """通知覆盖：失败也是需要被感知的事件。"""

    def test_failure_also_notifies(self):
        """备份失败也必须发通知——定时备份的失败不该只躺在日志里。"""
        self.plugin._notify = True
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__do_backup", return_value=(False, "数据库备份失败")
        ):
            ok, _ = self.plugin._ConfigBackup__backup()

        self.assertFalse(ok)
        calls = getattr(self.plugin, "_stub_messages", [])
        self.assertEqual(len(calls), 1, "失败也应发出一次通知")
        self.assertIn("失败", calls[0][1].get("title", ""))

    def test_success_notifies_with_complete_title(self):
        """成功通知的标题应标明"完成"，与失败可区分。"""
        self.plugin._notify = True
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__do_backup", return_value=(True, "备份完成")
        ):
            self.plugin._ConfigBackup__backup()

        calls = getattr(self.plugin, "_stub_messages", [])
        self.assertEqual(len(calls), 1)
        self.assertIn("完成", calls[0][1].get("title", ""))

    def test_notify_uses_configured_scene(self):
        """通知场景取配置项，不再写死「站点」。"""
        self.plugin._notify = True
        self.plugin._notify_type = "手动处理"
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__do_backup", return_value=(True, "ok")
        ):
            self.plugin._ConfigBackup__backup()

        kwargs = getattr(self.plugin, "_stub_messages", [])[0][1]
        self.assertEqual(kwargs["mtype"].value, "手动处理")

    def test_unknown_scene_falls_back_to_plugin(self):
        """未知场景值回落「插件」，不能让通知整个崩掉。"""
        self.plugin._notify_type = "不存在的场景"
        self.assertEqual(self.plugin._ConfigBackup__notify_mtype().value, "插件")

    def test_silent_when_notify_disabled(self):
        """未开启通知时不发任何消息。"""
        self.plugin._notify = False
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__do_backup", return_value=(True, "ok")
        ):
            self.plugin._ConfigBackup__backup()

        self.assertEqual(getattr(self.plugin, "_stub_messages", []), [])


class TestPackageSelfCheck(_BoundaryBase):
    """打包后的完整性自检。"""

    def test_broken_package_is_quarantined(self):
        """校验失败的包应隔离为 .broken，不留一个看起来可用的 .zip。"""
        with mock.patch.object(ConfigBackup, "_ConfigBackup__verify_zip", return_value=False):
            ok, msg = self.plugin._ConfigBackup__run_backup()

        self.assertFalse(ok)
        self.assertIn("校验失败", msg)
        left = sorted(p.name for p in Path(self.backup_dir).iterdir())
        self.assertTrue(
            any(n.endswith(".broken") for n in left), f"应留下 .broken，实际：{left}"
        )
        self.assertFalse(
            any(n.endswith(".zip") for n in left), f"不应留下可用 .zip，实际：{left}"
        )

    def test_broken_not_counted_in_cleanup(self):
        """被隔离的 .broken 不占保留名额。"""
        for i in range(3):
            self.make_backup(f"20260918_0{i}0000")
        (Path(self.backup_dir) / "bk_20260918_099999.zip.broken").write_bytes(b"junk")

        self.plugin._keep_count = 2
        self.plugin._keep_days = 0    # 本用例只验证个数维度，关掉天数保护
        deleted = self.plugin._ConfigBackup__clean_old_backups(Path(self.backup_dir))

        self.assertEqual(deleted, 1, "只应删超出的 1 份，.broken 不参与计数")
        self.assertTrue((Path(self.backup_dir) / "bk_20260918_020000.zip").exists())
        self.assertTrue((Path(self.backup_dir) / "bk_20260918_099999.zip.broken").exists())


class TestSortByFilename(_BoundaryBase):
    """排序依据：文件名时间戳优先于 mtime/ctime。"""

    def test_mtime_confusion_does_not_delete_newest(self):
        """mtime 被颠倒（拷贝/同步常见）时，仍按文件名保留真正最新的那份。"""
        old = self.make_backup("20260101000000")
        new = self.make_backup("20260918000000")
        os.utime(new, (1, 1))    # 新包的 mtime 反而更旧
        os.utime(old, (9, 9))

        self.plugin._keep_count = 1
        self.plugin._ConfigBackup__clean_old_backups(Path(self.backup_dir))

        self.assertTrue(new.exists(), "文件名更新的备份必须保留")
        self.assertFalse(old.exists(), "文件名更旧的备份应被清理")

    def test_listing_uses_filename_time(self):
        """列表展示的时间取文件名时间戳，而非拷贝时间。"""
        self.make_backup("20260101000000")
        os.utime(Path(self.backup_dir) / "bk_20260101000000.zip", (1, 1))

        items = self.plugin._ConfigBackup__list_backups(Path(self.backup_dir))

        self.assertEqual(items[0]["time"], "2026-01-01 00:00:00")


class TestKeepDays(_BoundaryBase):
    """保留天数：与保留个数「满足其一即保留」。"""

    def test_recent_backups_survive_count_pressure(self):
        """天数内的备份不该因为超出保留个数被删。"""
        for i in range(3):
            self.make_backup(f"2026100{i}000000")
        self.plugin._keep_count = 1
        self.plugin._keep_days = 30

        deleted = self.plugin._ConfigBackup__clean_old_backups(Path(self.backup_dir))

        self.assertEqual(deleted, 0, "30 天内的备份一律不删")

    def test_zero_days_means_count_only(self):
        """keep_days=0 表示只看个数。"""
        for i in range(3):
            self.make_backup(f"2026100{i}000000")
        self.plugin._keep_count = 1
        self.plugin._keep_days = 0

        self.assertEqual(self.plugin._ConfigBackup__clean_old_backups(Path(self.backup_dir)), 2)

    def test_only_old_and_excess_are_deleted(self):
        """既超个数、又早于截止时间的才删。"""
        old = self.make_backup("20260101000000")
        new = self.make_backup("20261001000000")
        self.plugin._keep_count = 1
        self.plugin._keep_days = 30

        self.assertEqual(self.plugin._ConfigBackup__clean_old_backups(Path(self.backup_dir)), 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())


class TestManifest(_BoundaryBase):
    """备份包清单：还原前能看清这个包里到底有什么。"""

    def test_manifest_written_into_package(self):
        """备份包内应写入清单，并记录数据库类型。"""
        cfg = Path(self.base) / "cfg"
        cfg.mkdir()
        (cfg / "category.yaml").write_text("x: 1")

        with mock.patch.object(settings, "CONFIG_PATH", str(cfg)):
            ok, msg = self.plugin._ConfigBackup__run_backup()

        self.assertTrue(ok, msg)
        zips = list(Path(self.backup_dir).glob("bk_*.zip"))
        self.assertEqual(len(zips), 1, f"应产出 1 个备份包，实际：{zips}")
        data = ConfigBackup._ConfigBackup__read_manifest(zips[0])
        self.assertIsNotNone(data, "备份包内应有清单")
        self.assertEqual(data["db_type"], "sqlite")
        self.assertIn("created", data)

    def test_legacy_package_degrades(self):
        """老包没有清单：摘要降级，不得因此拒绝还原。"""
        path = self.make_backup("20260918000000")
        self.assertEqual(
            ConfigBackup._ConfigBackup__summarize(str(path)), "老备份包（无清单）"
        )

    def test_summary_lists_contents(self):
        """摘要应体现包含的内容。"""
        path = self.make_backup("20260918000000")
        with zipfile.ZipFile(path, "a") as zf:
            zf.writestr("backup_manifest.json", json.dumps({
                "database": True, "db_type": "postgresql",
                "plugins": True, "extra_count": 2,
            }))

        summary = ConfigBackup._ConfigBackup__summarize(str(path))
        self.assertIn("数据库", summary)
        self.assertIn("插件配置", summary)
        self.assertIn("附加×2", summary)


class TestOverlayRestore(_BoundaryBase):
    """完全还原：备份包里出现过的项先删后写。"""

    def test_overwrites_same_name(self):
        """同名项应被备份内容整体替换，而不是合并。"""
        src = Path(self.base) / "src"
        src.mkdir()
        (src / "a.json").write_text("new")
        dst = Path(self.base) / "dst"
        dst.mkdir()
        (dst / "a.json").write_text("old")

        n = ConfigBackup._ConfigBackup__overlay(src, dst)

        self.assertEqual(n, 1)
        self.assertEqual((dst / "a.json").read_text(), "new")

    def test_unrelated_items_are_kept(self):
        """备份包里没有的目标端内容不动——老备份包不能把现在的清光。"""
        src = Path(self.base) / "src"
        src.mkdir()
        (src / "a.json").write_text("new")
        dst = Path(self.base) / "dst"
        dst.mkdir()
        (dst / "unrelated.json").write_text("keep")

        ConfigBackup._ConfigBackup__overlay(src, dst)

        self.assertTrue((dst / "unrelated.json").exists(), "包里没有的不应被清掉")

    def test_directory_is_replaced_not_merged(self):
        """目录整体替换：备份里没有的文件不得残留（否则还原名不副实）。"""
        src = Path(self.base) / "src"
        (src / "cfg").mkdir(parents=True)
        (src / "cfg" / "x.json").write_text("new")
        dst = Path(self.base) / "dst"
        (dst / "cfg").mkdir(parents=True)
        (dst / "cfg" / "stale.json").write_text("stale")

        ConfigBackup._ConfigBackup__overlay(src, dst)

        self.assertTrue((dst / "cfg" / "x.json").exists())
        self.assertFalse((dst / "cfg" / "stale.json").exists(), "同目录下备份里没有的文件应清除")


class TestRestoreSafetyNet(_BoundaryBase):
    """完全还原会先删后写，安全网没兜住就不许动手。"""

    def test_restore_aborted_when_safety_backup_fails(self):
        """安全备份失败时必须中止还原，且不得触碰任何配置。"""
        name = f"{self._prefix}20260918_100000.zip"
        self.make_backup("20260918_100000")
        self.set_pending({"filename": name})

        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__backup", return_value=(False, "磁盘已满")
        ), mock.patch.object(ConfigBackup, "_ConfigBackup__restore") as restore:
            result = self.plugin.api_restore(filename="", confirm="1")

        self.assertFalse(result.get("success"))
        self.assertIn("中止", result.get("message", ""))
        self.assertFalse(restore.called, "安全网失败时不得执行还原")


class TestExtraPathExcludes(_BoundaryBase):
    """附加路径排除规则。"""

    def test_exclude_patterns_respected(self):
        """排除模式生效：附加路径里的日志/缓存不再被塞进备份包。"""
        src = Path(self.base) / "src"
        src.mkdir()
        (src / "keep.txt").write_text("k")
        (src / "skip.log").write_text("s")
        (src / "cache").mkdir()
        (src / "cache" / "x.bin").write_bytes(b"x")

        self.plugin._extra_paths = f"{src}|*.log,cache"
        temp = Path(self.base) / "t"
        temp.mkdir()
        ok, msg = self.plugin._ConfigBackup__copy_extra_paths(temp)

        self.assertTrue(ok, msg)
        copied = temp / "extra" / src.name
        self.assertTrue((copied / "keep.txt").exists())
        self.assertFalse((copied / "skip.log").exists(), "排除模式应生效")
        self.assertFalse((copied / "cache").exists(), "排除目录应生效")


class TestTempDirLocation(_BoundaryBase):
    """打包临时目录的位置。"""

    def test_no_intermediate_files_left_in_backup_dir(self):
        """中间产物不落在备份目录（备份目录常挂在网盘上）。"""
        cfg = Path(self.base) / "cfg"
        cfg.mkdir()
        tmp = Path(self.base) / "tmp"
        tmp.mkdir()

        with mock.patch.object(settings, "CONFIG_PATH", str(cfg)), \
                mock.patch.object(settings, "TEMP_PATH", str(tmp)):
            ok, msg = self.plugin._ConfigBackup__run_backup()

        self.assertTrue(ok, msg)
        left = [p.name for p in Path(self.backup_dir).iterdir() if not p.name.endswith(".zip")]
        self.assertEqual(left, [], f"备份目录不应残留中间产物：{left}")


class TestPendingExpiry(_BoundaryBase):
    """待确认状态的有效期。"""

    def test_expired_pending_is_dropped(self):
        """超过 TTL 的待确认状态自动作废并清除文件。"""
        with mock.patch.object(ConfigBackup, "_pending_ttl", 0):
            self.set_pending({"filename": f"{self._prefix}20260918_000000.zip"})
            self.assertIsNone(self.get_pending(), "过期后应视为无待确认")
            self.assertFalse(self.pending_file().exists(), "过期后应清掉状态文件")

    def test_missing_filename_delete_degrades_to_none(self):
        """待删除状态有内容却缺 filename：应降级为 None。"""
        path = self.plugin._ConfigBackup__pending_delete_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"foo": "bar"}', encoding="utf-8")

        self.assertIsNone(self.get_pending_delete(), "缺 filename 应降级为 None")

    def test_legacy_pending_without_ts_survives(self):
        """老格式无 ts 字段：不判过期，避免升级后无声清掉用户正在进行的确认。"""
        path = self.pending_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"filename": "bk_20260918_000000.zip"}', encoding="utf-8")

        with mock.patch.object(ConfigBackup, "_pending_ttl", 0):
            self.assertIsNotNone(self.get_pending())

    def test_fresh_pending_survives(self):
        """刚写入的待确认状态在 TTL 内有效。"""
        self.set_pending({"filename": f"{self._prefix}20260918_000000.zip"})
        self.assertIsNotNone(self.get_pending())


class TestSqliteWal(_BoundaryBase):
    """SQLite 的 WAL 一致性。"""

    def test_checkpoint_called_before_copy(self):
        """SQLite 场景复制数据库文件前应先 checkpoint。"""
        target = Path(self.base) / "t"
        target.mkdir()
        with mock.patch.object(ConfigBackup, "_ConfigBackup__checkpoint_sqlite") as ck:
            self.plugin._ConfigBackup__copy_config_files(target)

        self.assertTrue(ck.called, "未 checkpoint 就复制 user.db 会拿到不完整快照")

    def test_restore_clears_stale_wal(self):
        """还原前应清掉目标端残留的 -wal/-shm，否则旧 WAL 会与新主库冲突。"""
        cfg = Path(self.base) / "cfg"
        cfg.mkdir()
        (cfg / "user.db-wal").write_bytes(b"stale")
        (cfg / "user.db-shm").write_bytes(b"stale")

        zip_path = Path(self.backup_dir) / "bk_20260918000000.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("category.yaml", "x: 1")

        with mock.patch.object(settings, "CONFIG_PATH", str(cfg)):
            ok, msg = self.plugin._ConfigBackup__restore(zip_path)

        self.assertTrue(ok, msg)
        self.assertFalse((cfg / "user.db-wal").exists(), "残留 WAL 必须清除")
        self.assertFalse((cfg / "user.db-shm").exists(), "残留 SHM 必须清除")


class TestBackupConcurrency(_BoundaryBase):
    """「立即运行一次」改为后台线程后，必须防止与定时任务撞车。"""

    def test_second_backup_rejected_while_running(self):
        """已有备份在跑时，第二次应被拒绝而不是并发写同一个临时目录。"""
        self.plugin._backup_lock.acquire()
        try:
            ok, msg = self.plugin._ConfigBackup__run_backup()
        finally:
            self.plugin._backup_lock.release()

        self.assertFalse(ok)
        self.assertIn("正在执行", msg)

    def test_lock_released_after_success(self):
        """备份结束后锁必须释放，否则后续所有备份都会被永久拒绝。"""
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__do_backup", return_value=(True, "ok")
        ):
            self.plugin._ConfigBackup__run_backup()

        # 注意：_backup_lock 是类属性（跨实例共享），拿到后必须释放，
        # 否则后续用例会永久阻塞在 acquire 上。
        acquired = self.plugin._backup_lock.acquire(blocking=False)
        if acquired:
            self.plugin._backup_lock.release()
        self.assertTrue(acquired, "备份结束后锁应已释放")


if __name__ == "__main__":
    unittest.main()
