# -*- coding: utf-8 -*-
"""
审计整改回归测试（v3.0.13）。

覆盖 2026-10-28 代码审计提出的四类问题，每个用例对应一条整改点：

A. 高风险
   A1 共用目录判断必须用「未过滤」的全量候选（保护期内的新种子要看得见）
   A2 删种必须带上刚定位到的下载器实例名
   A3 配置里的清理模式非法时拒绝启用（不能静默落到「仅文件」）
   A4 停用 / 重载后不再执行手动与命令入口；运行中的清理可被取消
   A5 「无候选」哨兵不得与无主清理数量相加

B. 中风险
   B1 等待秒数为 0 时不判「未释放」
   B2 内容路径与保存目录都不可见时不得判空壳
   B3 「有主」判定不再整体采信保存目录
   B4 联动删种按「本次实际模式」判定，而不是回读配置
   B5 读不到添加/完成时间的种子按保护处理
   B6 数字与定时配置非法时回退默认，不抛异常
   B7 空壳目录扫描忽略 DSM 索引目录；符号链接算内容
   B8 转移记录删除要回查，任务托管记录不删
   B9 监控目录拒绝根目录与卷根

C. 行为开关与展示
   C1 辅种默认受保护期约束，可用开关关闭
   D1 结果摘要标注「已提前停止 / 已取消」，并落盘保存
"""

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.core.module import ModuleManager
from app.db.oper.downloadhistory import DownloadHistoryOper
from app.db.oper.transferhistory import TransferHistoryOper
from app.schemas.types import DownloaderType
from seedspaceguard import SeedSpaceGuard


# ======================================================================
# 公共夹具
# ======================================================================

class _FakeServer:
    """伪下载器：记录删种调用，可选地真的删除文件。"""

    def __init__(self, torrents, files_map=None, delete_on_remove=False):
        self._torrents = list(torrents)
        self._files = dict(files_map or {})
        self._delete_on_remove = delete_on_remove
        self.removed = []

    def get_torrents(self, *args, **kwargs):
        return list(self._torrents)

    def get_files(self, tid=None, downloader=None, **kwargs):
        names = self._files.get(tid)
        return [{"name": name} for name in names] if names else None

    def remove_torrents(self, hashs, delete_file=False, downloader=None):
        normalized = [hashs] if isinstance(hashs, str) else list(hashs or [])
        self.removed.append((normalized, delete_file, downloader))
        if delete_file and self._delete_on_remove:
            for h in normalized:
                item = next(
                    (t for t in self._torrents if t.get("hash") == h), None
                )
                if not item:
                    continue
                # 只删「该种子自己的文件」：真实下载器删种时不会连带删掉
                # 同目录其它种子的文件，夹具必须保持同一语义，否则测不出
                # 插件自己的越界删除
                names = self._files.get(h)
                if names:
                    base = item.get("save_path") or os.path.dirname(
                        item.get("content_path") or ""
                    )
                    for name in names:
                        path = os.path.join(base, name)
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                    continue
                content = item.get("content_path") or ""
                if os.path.isfile(content):
                    os.remove(content)
        return True


def _qb(content_path, hash_str, name, added_days=30, size_gb=4,
        progress=1.0, save_path=None):
    """构造一个 qB 已完成种子条目。"""
    stamp = int(time.time()) - int(added_days * 86400)
    return {
        "progress": progress,
        "content_path": content_path,
        "save_path": save_path if save_path is not None else os.path.dirname(content_path),
        "hash": hash_str,
        "name": name,
        "completion_on": stamp,
        "added_on": stamp,
        "size": int(size_gb * 1024 ** 3),
    }


class _Base(unittest.TestCase):
    """真实临时目录 + 内存数据层 + 可注入下载器。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-audit-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        os.makedirs(self.dl)
        os.makedirs(self.lib)
        DownloadHistoryOper.reset()
        TransferHistoryOper.reset()
        ModuleManager.reset()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        SeedSpaceGuard._clean_stats = None
        ModuleManager.reset()

    # ------------------------------------------------------------------
    def make_file(self, path, size_kb=64, age_days=30):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size_kb)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def make_sparse(self, path, size_kb, age_days=30):
        """用稀疏文件制造 GB 级「名义大小」，不实际占用磁盘。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.truncate(size_kb * 1024)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def install(self, torrents, files_map=None, delete_on_remove=False,
                name="qb"):
        server = _FakeServer(
            torrents, files_map=files_map, delete_on_remove=delete_on_remove
        )
        ModuleManager.register_downloader(DownloaderType.Qbittorrent, name, server)
        return server

    def make_plugin(self, mode="seed", threshold=5, recent_days=1,
                    companion=True, companion_protect=True):
        plugin = SeedSpaceGuard()
        plugin._enabled = True
        plugin._target_dirs = [self.dl, self.lib]
        plugin._active_dirs = [self.dl, self.lib]
        plugin._volume_path = self.base
        plugin._protect_pattern = ""
        plugin._recent_skip_days = recent_days
        plugin._threshold_gb = threshold
        plugin._sync_wait_seconds = 0
        plugin._notify = False
        plugin._dry_run = False
        plugin._mode = mode
        plugin._active_mode = ""
        plugin._companion_cleanup = companion
        plugin._companion_protect = companion_protect
        plugin._orphan_cleanup = False
        plugin._orphan_seed_scope = False
        plugin._empty_dir_sweep = False
        plugin._delete_torrents = False
        plugin._delete_history = False
        plugin._downloadhis = DownloadHistoryOper()
        plugin._transferhis = TransferHistoryOper()
        plugin._ino_paths = {}
        plugin._clean_stats = {
            "files": 0, "transfers": 0, "seeds": 0, "companions": 0,
            "orphans": 0, "dirs": 0, "stalled": False, "cancelled": False,
            "dry_run": False,
        }
        return plugin


# ======================================================================
# A1 共用目录：保护期内的新种子必须被看见
# ======================================================================
class TestSharedDirWithProtectedSeed(_Base):
    """老种子与新种子共用一个内容目录时，绝不能删掉新种子的文件。"""

    def test_protected_sibling_files_survive(self):
        shared = os.path.join(self.dl, "Show")
        old_file = self.make_file(os.path.join(shared, "ep01.mkv"), 64)
        new_dir = os.path.join(shared, "NewSub")
        new_file = self.make_file(os.path.join(new_dir, "ep02.mkv"), 64)

        old_cand = _qb(shared, "h_old", "Show", added_days=30)
        new_cand = _qb(new_dir, "h_new", "Show.New", added_days=0)
        server = self.install(
            [old_cand, new_cand],
            files_map={"h_old": ["Show/ep01.mkv"],
                       "h_new": ["Show/NewSub/ep02.mkv"]},
            delete_on_remove=True,
        )
        plugin = self.make_plugin()
        plugin._disk_free_bytes = lambda: 1 * 1024 ** 3

        plugin._clean_by_seed(1 * 1024 ** 3, dry_run=False)

        self.assertEqual(server.removed[0][0], ["h_old"], "只应删除老种子")
        self.assertFalse(os.path.exists(old_file), "老种子的文件应被删除")
        self.assertTrue(
            os.path.exists(new_file),
            "与保护期内新种子共用的目录不得被整目录清理（会直接红种）",
        )


# ======================================================================
# A2 删种必须带上下载器实例名
# ======================================================================
class TestDeleteTorrentPassesDownloader(_Base):
    """多下载器环境下必须精确下发到定位到的实例。"""

    def test_downloader_name_is_forwarded(self):
        self.install([_qb(os.path.join(self.dl, "A"), "H", "A")], name="qb-main")
        plugin = self.make_plugin()
        plugin._downloaders = ["qb-main"]

        self.assertTrue(plugin._delete_torrent_by_hash("H", "A"))

        server = ModuleManager._downloaders[DownloaderType.Qbittorrent]["qb-main"]
        self.assertEqual(
            server.removed, [(["H"], False, "qb-main")],
            "删种必须带上下载器实例名，否则会落到同类默认实例",
        )


# ======================================================================
# A3 清理模式非法 → 拒绝启用
# ======================================================================
class TestInvalidModeRejected(_Base):

    def test_invalid_mode_disables_plugin(self):
        plugin = SeedSpaceGuard()
        plugin.init_plugin({
            "enabled": True,
            "mode": "files",          # 非法（只支持 seed / file）
            "target_dirs": self.dl,
            "volume_path": self.base,
        })
        self.assertFalse(plugin.get_state(), "非法模式必须拒绝启用")
        self.assertFalse(plugin._enabled)
        self.assertIn("清理模式", plugin._config_error)
        page = plugin.get_page()
        self.assertIsNotNone(page, "配置错误时详情页要能看到原因")
        self.assertIn("配置校验失败", str(page))


# ======================================================================
# A4 停用与取消
# ======================================================================
class TestDisableAndCancel(_Base):

    def test_manual_trigger_ignored_when_disabled(self):
        plugin = self.make_plugin()
        plugin._enabled = False
        self.assertEqual(
            plugin.check_and_clean(source="手动"), "插件未启用",
            "停用后手动入口也必须拒绝，不能绕过启用状态",
        )

    def test_disabled_init_does_not_start_manual_run(self):
        plugin = SeedSpaceGuard()
        with mock.patch("seedspaceguard.threading.Thread") as thread_cls:
            plugin.init_plugin({
                "enabled": False,
                "mode": "file",
                "target_dirs": self.dl,
                "volume_path": self.base,
                "manual_action": "clean",
            })
        self.assertFalse(
            thread_cls.called, "未启用时保存手动动作不得启动清理线程"
        )

    def test_stop_service_cancels_running_round(self):
        plugin = self.make_plugin(mode="file")
        plugin._sync_wait_seconds = 0
        self.make_file(os.path.join(self.dl, "a.mkv"), 2048)
        plugin._disk_free_bytes = lambda: 1 * 1024 ** 3
        plugin._run_cancel_event = mock.MagicMock()

        plugin.stop_service()

        plugin._run_cancel_event.set.assert_called_once()

    def test_cancelled_round_stops_deleting(self):
        plugin = self.make_plugin(mode="file", threshold=5000)
        target = self.make_file(os.path.join(self.dl, "a.mkv"), 2048)
        plugin._disk_free_bytes = lambda: 1 * 1024 ** 3
        event = mock.MagicMock()
        event.is_set.return_value = True
        plugin._run_cancel_event = event

        count, _gb, _lines = plugin._clean_by_file(1 * 1024 ** 3, dry_run=False)

        self.assertEqual(count, 0)
        self.assertTrue(os.path.exists(target), "已取消时不得继续删除")
        self.assertTrue(plugin._clean_stats["cancelled"])


# ======================================================================
# A5 无候选哨兵不得参与算术
# ======================================================================
class TestNoCandidateSentinel(_Base):

    def test_sentinel_not_added_to_orphan_count(self):
        plugin = self.make_plugin(threshold=10)
        plugin._empty_dir_sweep = False
        plugin._disk_free_bytes = lambda: 1 * 1024 ** 3

        def fake_orphan(_dry_run):
            """模拟无主清理真的删了 3 个文件（并同步计数）。"""
            plugin._clean_stats["orphans"] = 3
            return 3, 0.3, ["已删除无主文件：x"]

        with mock.patch.object(plugin, "_collect_seed_candidates", return_value=[]), \
                mock.patch.object(plugin, "_clean_orphan_files",
                                  side_effect=fake_orphan), \
                mock.patch.object(plugin, "_reap_orphan_seeds",
                                  return_value={"torrent": 0, "checked": 0,
                                                "alive": 0, "failed": 0,
                                                "_lines": []}), \
                mock.patch.object(plugin, "_sweep_empty_dirs",
                                  return_value={"dirs": 0, "_lines": []}):
            plugin._orphan_cleanup = True
            msg = plugin.check_and_clean(source="手动")

        self.assertIn(
            "共处理 3 个资源", msg,
            "哨兵 -1 不得参与算术（旧实现会把 3 算成 2）",
        )
        self.assertNotIn("未找到可清理的已完成种子", msg)
        self.assertEqual(plugin._clean_stats["seeds"], 0)
        self.assertEqual(plugin._clean_stats["orphans"], 3)


# ======================================================================
# B1 等待 0 秒不判「未释放」
# ======================================================================
class TestZeroWaitDoesNotStall(_Base):

    def test_no_wait_no_stall(self):
        plugin = self.make_plugin(mode="file", threshold=5000)
        plugin._sync_wait_seconds = 0
        # 名义 2GB：越过 1GB 硬判定下限，否则「未释放」分支根本不会触发，
        # 也就测不出「等待 0 秒要不要判失败」的差异
        self.make_sparse(os.path.join(self.dl, "a.mkv"), 2 * 1024 * 1024)
        plugin._disk_free_bytes = lambda: 1 * 1024 ** 3

        _count, _gb, lines = plugin._clean_by_file(1 * 1024 ** 3, dry_run=False)

        self.assertFalse(
            any("空间未按预期释放" in line for line in lines),
            "用户选择「不等待」时无法测量释放量，不得据此判失败",
        )
        self.assertFalse(plugin._clean_stats["stalled"])


# ======================================================================
# B2 不可见路径不得判空壳
# ======================================================================
class TestInvisiblePathNotReaped(_Base):

    def test_no_visible_evidence_kept(self):
        """候选本身没有任何可见证据（路径与保存目录都缺）→ 保守保留。

        这是 ``_seed_fully_removed`` 的最后一道兜底：既没有记录复核、也
        拿不到自身文件清单、连内容路径都没有时，唯一正确的答案是「不知道」，
        而「不知道」必须落到「保留种子」。若这里返回可删，任何路径映射错误
        或卷未挂载都会被当成「文件已删光」，批量误删仍在做种的种子。
        """
        plugin = self.make_plugin()
        cand = {
            "hash": "H_NO_EVIDENCE", "title": "None", "path": "",
            "dl_dir": "", "module": None,
        }

        self.assertFalse(
            plugin._seed_fully_removed("H_NO_EVIDENCE", cand),
            "无任何可见证据时不得判定已删空（会误删完好种子）",
        )

    def test_missing_content_and_save_dir_kept(self):
        missing_root = os.path.join(self.base, "unmounted")
        content = os.path.join(missing_root, "Gone")
        DownloadHistoryOper.add_seed(
            "H_INVISIBLE", [os.path.join(missing_root, "Gone", "a.mkv")]
        )
        server = self.install([_qb(content, "H_INVISIBLE", "Gone")])
        plugin = self.make_plugin()
        plugin._delete_torrents = True
        plugin._orphan_seed_scope = True

        stats = plugin._reap_orphan_seeds(dry_run=False)

        self.assertEqual(stats["torrent"], 0, "路径整体不可见时必须保守保留")
        self.assertEqual(server.removed, [])

    def test_missing_content_with_visible_save_dir_reaped(self):
        content = os.path.join(self.dl, "Gone")
        DownloadHistoryOper.add_seed(
            "H_GONE", [os.path.join(content, "a.mkv")]
        )
        server = self.install([_qb(content, "H_GONE", "Gone")])
        plugin = self.make_plugin()
        plugin._delete_torrents = True

        stats = plugin._reap_orphan_seeds(dry_run=False)

        self.assertEqual(stats["torrent"], 1, "保存目录可见时确证内容已消失")
        self.assertEqual(server.removed[0][0], ["H_GONE"])


# ======================================================================
# B3 「有主」判定不再整体采信保存目录
# ======================================================================
class TestOwnedDoesNotUseSavePath(_Base):

    def test_monitored_dir_as_save_path_does_not_own_everything(self):
        orphan = self.make_file(os.path.join(self.dl, "a.mkv"), 64)
        self.install([
            _qb(os.path.join(self.dl, "Other"), "h1", "Other", save_path=self.dl)
        ])
        plugin = self.make_plugin()

        deleted, _gb, lines = plugin._clean_orphan_files(dry_run=True)

        self.assertEqual(deleted, 1, "保存目录不能把整个监控目录都算成有主")
        self.assertTrue(any(orphan in line for line in lines))


# ======================================================================
# B4 联动按实际模式判定
# ======================================================================
class TestLinkageUsesActiveMode(_Base):

    def test_file_override_in_seed_config_still_deletes_torrent(self):
        plugin = self.make_plugin(mode="seed")
        plugin._delete_torrents = True
        plugin._active_mode = "file"

        with mock.patch.object(plugin, "_resolve_hash_by_path", return_value="H"), \
                mock.patch.object(plugin, "_seed_fully_removed", return_value=True), \
                mock.patch.object(plugin, "_delete_torrent_by_hash",
                                  return_value=True):
            stats = plugin._run_linkage_after_delete(
                [os.path.join(self.dl, "a.mkv")], [], dry_run=False
            )

        self.assertEqual(
            stats["torrent"], 1,
            "手动用「仅文件」模式清理时联动删种不能因配置是种子级而失效",
        )


# ======================================================================
# B5 时间未知的种子按保护处理
# ======================================================================
class TestUnknownTimeProtected(_Base):
    """读不到完成时间的种子不得被当成「最老」优先删除。"""

    def test_zero_added_not_selected(self):
        old = os.path.join(self.dl, "Old")
        unknown = os.path.join(self.dl, "Unknown")
        self.make_file(os.path.join(old, "a.mkv"), 64)
        self.make_file(os.path.join(unknown, "b.mkv"), 64)
        cands = [
            {"path": old, "hash": "h_old", "title": "Old", "added":
             int(time.time()) - 30 * 86400, "size_gb": 1.0, "dl_dir": self.dl,
             "module": None, "downloader": "qb"},
            {"path": unknown, "hash": "h_unknown", "title": "Unknown",
             "added": 0, "size_gb": 1.0, "dl_dir": self.dl,
             "module": None, "downloader": "qb"},
        ]
        plugin = self.make_plugin(mode="seed", threshold=100, recent_days=1)

        with mock.patch.object(plugin, "_collect_seed_candidates",
                               return_value=cands):
            count, _gb, lines = plugin._clean_by_seed(
                1 * 1024 ** 3, dry_run=True
            )

        self.assertEqual(count, 1)
        self.assertTrue(any("Old" in line for line in lines))
        self.assertFalse(
            any("Unknown" in line for line in lines),
            "读不到时间的种子必须按保护处理，不能当最老优先删",
        )


# ======================================================================
# B6 配置兜底
# ======================================================================
class TestConfigFallback(_Base):

    def test_bad_numbers_and_cron_fall_back(self):
        plugin = SeedSpaceGuard()
        plugin.init_plugin({
            "enabled": True,
            "mode": "file",
            "target_dirs": self.dl,
            "volume_path": self.base,
            "threshold_gb": "abc",
            "recent_skip_days": "x",
            "cron": "not a cron",
            "sync_wait_seconds": "oops",
        })

        self.assertTrue(plugin.get_state(), "数字非法不应导致启用失败")
        self.assertEqual(plugin._threshold_gb, 500)
        self.assertEqual(plugin._recent_skip_days, 0)
        self.assertEqual(plugin._cron, "0 */6 * * *")
        self.assertEqual(plugin._sync_wait_seconds, 90)
        self.assertTrue(plugin._config_error)
        services = plugin.get_service()
        self.assertEqual(len(services), 1, "定时服务仍必须能注册")

    def test_negative_threshold_falls_back(self):
        plugin = SeedSpaceGuard()
        plugin.init_plugin({
            "enabled": True, "mode": "file",
            "target_dirs": self.dl, "volume_path": self.base,
            "threshold_gb": -5,
        })
        self.assertEqual(plugin._threshold_gb, 500)


# ======================================================================
# B7 空壳判定的目录扫描口径
# ======================================================================
class TestDirScanSemantics(_Base):

    def test_syno_index_dir_not_counted_as_file(self):
        seed_dir = os.path.join(self.dl, "Seed")
        ea_dir = os.path.join(seed_dir, "@eaDir")
        os.makedirs(ea_dir)
        with open(os.path.join(ea_dir, "SYNOINDEX_MEDIA_INFO"), "wb") as handle:
            handle.write(b"x")

        self.assertFalse(
            SeedSpaceGuard._dir_has_any_file(seed_dir),
            "只剩 DSM 索引残片的目录必须判为「已无真实文件」",
        )

    def test_symlink_counts_as_content(self):
        seed_dir = os.path.join(self.dl, "SeedLink")
        os.makedirs(seed_dir)
        os.symlink(os.path.join(self.dl, "elsewhere"), os.path.join(seed_dir, "l"))

        self.assertTrue(
            SeedSpaceGuard._dir_has_any_file(seed_dir),
            "符号链接也是内容，不得判成空壳",
        )


# ======================================================================
# B8 转移记录删除的回查与托管记录
# ======================================================================
class TestTransferHistoryDelete(_Base):

    def test_task_managed_record_not_deleted(self):
        path = self.make_file(os.path.join(self.lib, "a.mkv"), 64)
        TransferHistoryOper.add_record(1, "/src/a.mkv", path)
        TransferHistoryOper.records[1].transfer_task_id = "task-1"
        plugin = self.make_plugin()
        plugin._delete_history = True

        self.assertFalse(plugin._delete_transfer_history(path))
        self.assertEqual(TransferHistoryOper.deleted_ids, [])

    def test_delete_is_verified(self):
        path = self.make_file(os.path.join(self.lib, "a.mkv"), 64)
        TransferHistoryOper.add_record(2, "/src/a.mkv", path)
        plugin = self.make_plugin()
        plugin._delete_history = True

        with mock.patch.object(
            TransferHistoryOper, "delete", return_value=None
        ) as delete_mock:
            # 删除接口“成功返回”但记录仍在 → 必须回报未删除
            self.assertFalse(plugin._delete_transfer_history(path))
        delete_mock.assert_called_once_with(2)

    def test_normal_record_deleted(self):
        path = self.make_file(os.path.join(self.lib, "a.mkv"), 64)
        TransferHistoryOper.add_record(3, "/src/a.mkv", path)
        plugin = self.make_plugin()
        plugin._delete_history = True

        self.assertTrue(plugin._delete_transfer_history(path))
        self.assertEqual(TransferHistoryOper.deleted_ids, [3])

    def test_multiple_records_all_deleted(self):
        """同一路径的多条记录必须逐条清掉，不能只删第一条。"""
        path = self.make_file(os.path.join(self.lib, "a.mkv"), 64)
        TransferHistoryOper.add_record(11, "/src/a.mkv", path)
        TransferHistoryOper.add_record(12, "/src/a.mkv", path)
        plugin = self.make_plugin()
        plugin._delete_history = True

        self.assertTrue(plugin._delete_transfer_history(path))
        self.assertEqual(sorted(TransferHistoryOper.deleted_ids), [11, 12])


class TestApiRunReportsFailure(_Base):
    """停用时接口必须回报失败，不能把「没跑」当成「跑成功」。"""

    def test_api_run_false_when_disabled(self):
        import asyncio

        plugin = self.make_plugin(mode="file")
        plugin._enabled = False

        class _Req:
            query_params = {}

            async def json(self):
                return {}

        result = asyncio.run(plugin.api_run(request=_Req()))

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "插件未启用")


# ======================================================================
# B9 监控目录护栏
# ======================================================================
class TestTargetDirGuard(_Base):

    def test_root_and_volume_root_rejected(self):
        plugin = SeedSpaceGuard()
        plugin.init_plugin({
            "enabled": True,
            "mode": "file",
            "target_dirs": "/\n" + self.base + "\nrelative/path",
            "volume_path": self.base,
        })
        self.assertEqual(
            plugin._target_dirs, [],
            "根目录、卷根与相对路径都必须被拒绝",
        )


class TestPathMapping(_Base):
    """下载器路径映射：跨容器/挂载时归属判断必须落在本地真实路径上。"""

    def test_returns_local_path(self):
        mapping = [("/volume1/dl", "/downloads")]
        self.assertEqual(
            SeedSpaceGuard._map_return_path("/downloads/Show/a.mkv", mapping),
            "/volume1/dl/Show/a.mkv",
        )
        self.assertEqual(
            SeedSpaceGuard._map_return_path("/volume1/dl/Show/a.mkv", mapping),
            "/volume1/dl/Show/a.mkv",
            "已是本地路径时不得再改",
        )
        self.assertEqual(
            SeedSpaceGuard._map_return_path("/other/a.mkv", mapping),
            "/other/a.mkv",
        )

    def test_longest_prefix_wins(self):
        mapping = [
            ("/local/dl", "/downloads"),
            ("/local/dl2", "/downloads/sub"),
        ]
        self.assertEqual(
            SeedSpaceGuard._map_return_path("/downloads/sub/x.mkv", mapping),
            "/local/dl2/x.mkv",
        )


# ======================================================================
# C1 辅种保护期开关
# ======================================================================
class TestCompanionProtect(_Base):

    def _run(self, protect):
        shared = os.path.join(self.dl, "Show")
        self.make_file(os.path.join(shared, "ep01.mkv"), 64)
        old = _qb(shared, "h_old", "Show", added_days=30)
        new = _qb(shared, "h_new", "Show", added_days=0)
        server = self.install([old, new], delete_on_remove=True)
        plugin = self.make_plugin(
            threshold=100, recent_days=1, companion=True,
            companion_protect=protect,
        )
        plugin._disk_free_bytes = lambda: 1 * 1024 ** 3
        plugin._clean_by_seed(1 * 1024 ** 3, dry_run=False)
        return [item[0][0] for item in server.removed]

    def test_protected_by_default(self):
        removed = self._run(protect=True)
        self.assertIn("h_old", removed)
        self.assertNotIn("h_new", removed, "保护期内的辅种默认不得连带删除")

    def test_switch_off_removes_all(self):
        removed = self._run(protect=False)
        self.assertIn("h_old", removed)
        self.assertIn("h_new", removed, "关闭保护后应连带删除同内容辅种")


# ======================================================================
# D1 结果展示与落盘
# ======================================================================
class TestResultPresentation(_Base):

    def test_stall_marks_summary_and_notification(self):
        plugin = self.make_plugin(mode="file", threshold=5000)
        plugin._sync_wait_seconds = 1
        # 名义 2GB 才越过「未释放即停止」的硬判定下限
        self.make_sparse(os.path.join(self.dl, "a.mkv"), 2 * 1024 * 1024)
        plugin._disk_free_bytes = lambda: 1 * 1024 ** 3

        msg = plugin.check_and_clean(source="手动")

        self.assertIn("已提前停止", msg, "停手必须写进摘要，不能仍说「处理完成」")
        self.assertIn("未如期释放", plugin._build_notify_text(msg))

    def test_alert_type_reflects_result(self):
        self.assertEqual(SeedSpaceGuard._result_alert_type(""), "info")
        self.assertEqual(
            SeedSpaceGuard._result_alert_type("空间充足，无需清理"), "success"
        )
        self.assertEqual(
            SeedSpaceGuard._result_alert_type("清理完成（已取消）"), "warning"
        )
        self.assertEqual(
            SeedSpaceGuard._result_alert_type("配置校验失败：模式无效"), "error"
        )

    def test_result_persisted(self):
        plugin = self.make_plugin()
        plugin._finish("测试结论", notify=False)
        self.assertEqual(plugin.get_data("last_result"), "测试结论")


if __name__ == "__main__":
    unittest.main()
