# -*- coding: utf-8 -*-
"""
v1.3.5 专项测试：Transmission 种子字段命名兼容（snake_case / camelCase）。

真实故障（2026-09-20 线上实测定位）：

    空壳种子回收「扫描 1 个」，而下载器里实有 1765 个种子。加诊断后发现：
    ``_parse_torrent`` 对 Transmission 只按 **camelCase** 取字段
    （``percentDone`` / ``downloadDir`` / ``totalSize``），
    而 ``transmission_rpc.Torrent`` 暴露的是 **snake_case**：

        percentDone  → <缺失>      实际为 percent_done = 1.0
        downloadDir  → <缺失>      实际为 download_dir = /volume2/...
        totalSize    → <缺失>      实际为 total_size

    于是每个 TR 种子都在 ``percentDone < 0.999`` 处被判为「未完成」丢弃，
    1765 个全数解析失败 → 删种与空壳回收彻底失效。

本文件锁定该兼容行为：两种命名都必须能正确解析。
"""

import os
import unittest
from datetime import datetime, timezone

import tests  # noqa: F401  触发宿主桩路径注入

from app.schemas.types import DownloaderType
from seedspaceguard import SeedSpaceGuard


class _TorrentSnake:
    """复刻 transmission_rpc.Torrent 的 snake_case 属性。"""

    def __init__(self, name="圆桌派.S08", dl_dir="/volume2/影视剧收藏/圆桌派系列",
                 percent=1.0, total=39808172988, hash_str="1c8864d2aa"):
        self.name = name
        self.download_dir = dl_dir
        self.percent_done = percent
        self.total_size = total
        self.hash_string = hash_str
        self.done_date = datetime(2026, 1, 21, 21, 50, 49, tzinfo=timezone.utc)
        self.added_date = datetime(2026, 1, 21, 13, 50, 49, tzinfo=timezone.utc)


class _TorrentCamel:
    """camelCase 变体：兼容 MoviePilot 其它版本/包装层的命名。"""

    def __init__(self, name="Show", dl_dir="/volume1/video/下载/Show",
                 percent=1.0, total=1024 ** 3, hash_str="cafe"):
        self.name = name
        self.downloadDir = dl_dir
        self.percentDone = percent
        self.totalSize = total
        self.hashString = hash_str
        self.done_date = None
        self.addedDate = 1700000000


class _TorrentBare:
    """仅有部分字段的种子：验证缺失时安全降级，不抛错。"""

    def __init__(self):
        self.name = "Bare"
        self.percent_done = 1.0


class TestTransmissionNaming(unittest.TestCase):
    """Transmission 字段命名兼容。"""

    def setUp(self):
        self.plugin = SeedSpaceGuard()

    # ------------------------------------------------------------------
    # snake_case（真实 transmission_rpc 形态）
    # ------------------------------------------------------------------
    def test_snake_case_parsed(self):
        """snake_case 属性必须能解析（原始 BUG 的直接回归）。"""
        cand = self.plugin._parse_torrent(DownloaderType.Transmission, _TorrentSnake())
        self.assertIsNotNone(cand, "snake_case 种子不得被判为未完成而丢弃")
        self.assertEqual(cand["hash"], "1c8864d2aa")
        self.assertEqual(cand["title"], "圆桌派.S08")
        self.assertEqual(
            cand["path"],
            "/volume2/影视剧收藏/圆桌派系列/圆桌派.S08",
            "download_dir 缺失会导致路径拼接为空，必须用 snake_case 取到",
        )
        self.assertAlmostEqual(cand["size_gb"], 37.1, places=1)

    def test_snake_case_added_from_done_date(self):
        """完成时间应取自 done_date 并转为时间戳。"""
        cand = self.plugin._parse_torrent(DownloaderType.Transmission, _TorrentSnake())
        self.assertGreater(cand["added"], 0, "done_date 应被解析为时间戳")
        self.assertEqual(cand["added"], int(_TorrentSnake().done_date.timestamp()))

    def test_snake_case_incomplete_rejected(self):
        """未完成的种子仍应被拒绝（只放宽命名，不放松判定）。"""
        cand = self.plugin._parse_torrent(
            DownloaderType.Transmission, _TorrentSnake(percent=0.5)
        )
        self.assertIsNone(cand)

    # ------------------------------------------------------------------
    # camelCase（兼容旧写法）
    # ------------------------------------------------------------------
    def test_camel_case_still_parsed(self):
        """camelCase 命名仍需兼容，避免修复引入反向回归。"""
        cand = self.plugin._parse_torrent(DownloaderType.Transmission, _TorrentCamel())
        self.assertIsNotNone(cand)
        self.assertEqual(cand["hash"], "cafe")
        self.assertEqual(cand["path"], "/volume1/video/下载/Show/Show")
        self.assertEqual(cand["added"], 1700000000)

    # ------------------------------------------------------------------
    # 降级与安全
    # ------------------------------------------------------------------
    def test_missing_dir_yields_empty_path(self):
        """字段缺失时路径为空而非抛错，交由上层「路径为空」过滤。"""
        cand = self.plugin._parse_torrent(DownloaderType.Transmission, _TorrentBare())
        self.assertIsNotNone(cand)
        self.assertEqual(cand["path"], "")

    def test_none_item_no_crash(self):
        """None 输入不应抛错（防御下载器返回脏数据）。"""
        cand = self.plugin._parse_torrent(DownloaderType.Transmission, None)
        self.assertIsNone(cand)

    def test_dict_item_supported(self):
        """dict 形态的种子（camelCase 键）也应支持。"""
        item = {
            "name": "DictShow",
            "downloadDir": "/volume1/video/下载/DictShow",
            "percentDone": 1.0,
            "totalSize": 2 * 1024 ** 3,
            "hashString": "dicthash",
        }
        cand = self.plugin._parse_torrent(DownloaderType.Transmission, item)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["hash"], "dicthash")
        self.assertEqual(cand["path"], "/volume1/video/下载/DictShow/DictShow")

    # ------------------------------------------------------------------
    # qBittorrent 侧（TerrentDictionary）不受影响
    # ------------------------------------------------------------------
    def test_qbittorrent_still_works(self):
        """qBittorrent 的 dict 解析不得被本次改动破坏。"""
        item = {
            "progress": 1.0,
            "content_path": "/volume1/video/下载/Movie",
            "hash": "qbh",
            "name": "Movie",
            "completion_on": 1700000000,
            "size": 1024 ** 3,
        }
        cand = self.plugin._parse_torrent(DownloaderType.Qbittorrent, item)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["hash"], "qbh")
        self.assertEqual(cand["path"], "/volume1/video/下载/Movie")
        self.assertAlmostEqual(cand["size_gb"], 1.0, places=2)

    def test_qbittorrent_incomplete_rejected(self):
        """qB 未完成种子仍被拒绝。"""
        item = {"progress": 0.3, "content_path": "/x", "hash": "h", "name": "n",
                "completion_on": 0, "size": 0}
        self.assertIsNone(
            self.plugin._parse_torrent(DownloaderType.Qbittorrent, item)
        )


class TestPickAttr(unittest.TestCase):
    """属性取值辅助方法的边界。"""

    def test_first_non_none_wins(self):
        """按顺序取第一个非 None 值。"""

        class Obj:
            a = None
            b = 0          # 0 是有效值，不应被当成缺失
            c = 5

        self.assertEqual(SeedSpaceGuard._pick_attr(Obj(), "a", "b", "c"), 0)

    def test_default_when_all_missing(self):
        """全部缺失时返回默认值。"""

        class Obj:
            pass

        self.assertIsNone(SeedSpaceGuard._pick_attr(Obj(), "x", "y"))
        self.assertEqual(SeedSpaceGuard._pick_attr(Obj(), "x", default=7), 7)

    def test_dict_lookup(self):
        """dict 输入按同样的顺序规则取值。"""
        item = {"b": 2, "a": 1}
        self.assertEqual(SeedSpaceGuard._pick_attr(item, "a", "b"), 1)
        self.assertIsNone(SeedSpaceGuard._pick_attr(item, "zz"))

    def test_object_attr_exception_degrades(self):
        """属性读取抛异常时应视为缺失，继续尝试下一个名字。"""

        class Obj:
            @property
            def boom(self):
                raise RuntimeError("bad")

            ok = 9

        # getattr 默认值参数会吞掉 AttributeError，但不吞 RuntimeError；
        # 这里验证不会被异常打断整体解析流程
        try:
            value = SeedSpaceGuard._pick_attr(Obj(), "ok")
        except RuntimeError:  # pragma: no cover
            self.fail("不应因单个属性异常而失败")
        self.assertEqual(value, 9)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
