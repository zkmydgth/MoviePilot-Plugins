# -*- coding: utf-8 -*-
"""
半残种子场景测试：文件已被部分删除的种子，种子级删除是否仍能正常运行。

背景：用户在真实环境试运行前提出的疑问——**对于现有的已被部分删除文件的
种子，后续在种子级删除时，也能正常运行吗**。

本测试针对三种「半残」形态，逐一验证清理链路不崩、不误删、且能自愈：

  A. **媒体库侧硬链接已丢**（此前跑过文件级模式删掉了媒体库侧）
     → 索引里该 inode 只剩下载侧路径；删种后下载侧也没了 → 无可删，
       但**不得报错、不得把别的文件当同 inode 删掉**。

  B. **下载侧部分文件已丢**（人工/其它工具删过几个）
     → 索引只覆盖仍存在的文件；已丢的 inode 查不到 → 跳过，不报错。

  C. **媒体库侧存在、但 inode 已变**（文件被重建为新文件，如重新入库）
     → `_delete_one` 的边界②必须拦住，**绝不能删掉重建后的新文件**。

  D. **全空壳**（两侧都没文件）→ 应走 `_reap_orphan_seeds`，且返回 True。

核心断言：清理过程**不抛异常**、**不误删无关文件**、统计数字**不为负**。
"""

import os
import shutil
import tempfile
import time
import unittest

import tests  # noqa: F401  触发宿主桩路径注入

from seedspaceguard import SeedSpaceGuard


class _SemiBase(unittest.TestCase):
    """构造「下载目录 + 媒体库目录」硬链接对的最小夹具。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ssg-semi-")
        self.dl = os.path.join(self.base, "download")
        self.lib = os.path.join(self.base, "library")
        os.makedirs(self.dl)
        os.makedirs(self.lib)
        self.plugin = SeedSpaceGuard()
        self.plugin._target_dirs = [self.dl, self.lib]
        self.plugin._active_dirs = [self.dl, self.lib]
        self.plugin._companion_cleanup = True
        self.plugin._ino_paths = {}
        self.plugin._downloadhis = None
        self.plugin._clean_stats = {
            "seeds": 0, "files": 0, "transfers": 0, "companions": 0, "stalled": 0,
        }

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # ------------------------------------------------------------------
    def make_seed_files(self, hash_str="h1", title="Show", count=5):
        """建 count 个文件，下载侧 + 媒体库侧互为硬链接。返回 (cand, pairs)。"""
        content = os.path.join(self.dl, hash_str)
        libc = os.path.join(self.lib, title)
        os.makedirs(content, exist_ok=True)
        os.makedirs(libc, exist_ok=True)
        pairs = []
        for i in range(count):
            f_dl = os.path.join(content, f"ep{i:02d}.mkv")
            with open(f_dl, "wb") as fh:
                fh.write(b"x" * 4096)
            f_lib = os.path.join(libc, f"ep{i:02d}.mkv")
            os.link(f_dl, f_lib)
            pairs.append((f_dl, f_lib))
        cand = {
            "hash": hash_str, "title": title, "added": 1000.0,
            "size_gb": 4.0, "path": content, "downloader": "qb",
            "module": None, "files": [],
        }
        return cand, pairs, content, libc

    def index_and_clean(self, cand):
        """跑 ①建索引 → ③清硬链接（②删种由用例自行模拟）。

        返回 (inode 数, 清理条数, 关联路径列表)。
        """
        related = self.plugin._seed_related_paths(cand)
        ino_index = self.plugin._build_inode_index(related)
        removed = self.plugin._clean_hardlinks_for(ino_index, cand["title"])
        return len(ino_index), removed, related


class TestSemiDeletedSeed(_SemiBase):

    # -------- A. 媒体库侧硬链接此前已被删（文件级模式跑过）--------
    def test_library_links_already_gone_no_crash(self):
        cand, pairs, content, libc = self.make_seed_files()
        # 模拟文件级模式此前已删掉媒体库侧
        for _, f_lib in pairs:
            os.unlink(f_lib)
        # 磁盘上只剩下载侧 5 个文件
        n_ino, removed, related = self.index_and_clean(cand)
        self.assertEqual(n_ino, 5, "应仅索引到下载侧的 5 个 inode")
        self.assertGreaterEqual(removed, 0, "清理条数不得为负")
        # 下载侧文件应被清理；目录可能已被 _prune_empty_dirs 一并删除
        if os.path.isdir(content):
            self.assertEqual(os.listdir(content), [], "下载侧文件应被清理干净")
        self.assertFalse(os.path.isdir(libc) and os.listdir(libc),
                        "媒体库侧不得凭空产生文件")

    # -------- B. 下载侧部分文件已丢 --------
    def test_download_side_partially_missing(self):
        cand, pairs, content, libc = self.make_seed_files()
        # 人工删掉下载侧前 2 个（其媒体库侧镜像仍在，变成孤儿硬链接）
        for f_dl, _ in pairs[:2]:
            os.unlink(f_dl)
        n_ino, removed, related = self.index_and_clean(cand)
        # 下载侧剩 3 个 + 媒体库侧 5 个（前 2 个 inode 只剩媒体库侧孤证）
        self.assertGreaterEqual(n_ino, 3, "至少索引到下载侧仍存的文件")
        self.assertGreaterEqual(removed, 0)
        # 关键：不抛异常，且两侧都清空（目录可能被 _prune_empty_dirs 删除）
        self.assertFalse(os.path.isdir(libc) and os.listdir(libc),
                        "媒体库侧应全部清理")
        self.assertFalse(os.path.isdir(content) and os.listdir(content),
                        "下载侧残留也应清理")

    # -------- C. 媒体库侧被重建为「新文件」（inode 已变）--------
    def test_rebuilt_library_file_not_deleted(self):
        cand, pairs, content, libc = self.make_seed_files(count=3)
        # 媒体库侧 ep00 被替换成一个全新文件（新 inode）
        f_lib_new = pairs[0][1]
        os.unlink(f_lib_new)
        with open(f_lib_new, "wb") as fh:
            fh.write(b"BRAND-NEW-CONTENT")
        new_inode = os.stat(f_lib_new).st_ino

        related = self.plugin._seed_related_paths(cand)
        ino_index = self.plugin._build_inode_index(related)
        # 建索引时新文件已存在，会被记入「新 inode」的 key；
        # 而我们要删的是「旧 inode」的 key —— 用旧 key 去删不得命中新文件
        old_key = None
        for key, plist in ino_index.items():
            if any(p.endswith("ep00.mkv") and p.startswith(self.lib) for p in plist):
                old_key = key
        self.assertIsNotNone(old_key, "索引里应能找到媒体库侧 ep00")
        # 构造「旧 key 去找新路径」的错配：直接把新文件路径塞进旧 key
        # 模拟「遍历之后文件被重建」的竞态
        other_key = (old_key[0], old_key[1] + 999999)
        self.plugin._ino_paths = {other_key: [f_lib_new]}
        removed = self.plugin._delete_one(f_lib_new, other_key)
        self.assertEqual(removed, 0, "inode 不匹配时必须拒绝删除")
        self.assertTrue(os.path.exists(f_lib_new), "重建后的新文件绝不能被删")
        self.assertEqual(os.stat(f_lib_new).st_ino, new_inode)

    # -------- D. 全空壳：应判定为「已删空」--------
    def test_fully_empty_dir_counts_as_removed(self):
        cand, pairs, content, libc = self.make_seed_files()
        # 两侧全清（模拟此前已删空）
        for f_dl, f_lib in pairs:
            os.unlink(f_dl)
            os.unlink(f_lib)
        # 目录仍在但为空
        self.assertTrue(os.path.isdir(content))
        self.assertFalse(self.plugin._dir_has_any_file(content),
                        "空目录应判定为无文件")

    # -------- E. 关联路径含不存在项时不崩 --------
    def test_related_paths_tolerate_missing(self):
        cand, pairs, content, libc = self.make_seed_files()
        # 两侧文件全部删掉，目录也删掉，模拟记录残留但路径已不存在
        for f_dl, f_lib in pairs:
            os.unlink(f_dl)
            os.unlink(f_lib)
        shutil.rmtree(content, ignore_errors=True)
        shutil.rmtree(libc, ignore_errors=True)
        related = self.plugin._seed_related_paths(cand)
        self.assertTrue(related, "仍应返回推断路径（不过滤存在性）")
        # 建索引不得抛异常，缺失路径直接跳过
        ino_index = self.plugin._build_inode_index(related)
        self.assertIsInstance(ino_index, dict)
        self.assertEqual(ino_index, {}, "路径全不存在时应得到空索引")
        removed = self.plugin._clean_hardlinks_for(ino_index, cand["title"])
        self.assertEqual(removed, 0, "无文件可删时应返回 0 而非报错")

    # -------- E2. 下载侧目录被删但媒体库侧镜像仍在（孤儿硬链接）--------
    def test_orphan_library_links_still_cleaned(self):
        """下载侧已消失、仅剩媒体库侧孤证时，仍应能凭 inode 清理干净。

        这正是「半残」里最需要自愈的一种：DownloadFiles 记录指向的下载侧
        路径已不存在，若只按记录走就会漏删媒体库侧 → 空间永不释放。
        """
        cand, pairs, content, libc = self.make_seed_files()
        # 只删下载侧目录（模拟下载器已清空内容）
        shutil.rmtree(content, ignore_errors=True)
        # 媒体库侧 5 个孤儿硬链接仍在
        self.assertEqual(len(os.listdir(libc)), 5)
        n_ino, removed, related = self.index_and_clean(cand)
        self.assertEqual(n_ino, 5, "应凭媒体库侧路径索引到这 5 个 inode")
        self.assertEqual(removed, 5, "5 条孤儿硬链接应全部清理")
        self.assertFalse(os.path.isdir(libc) and os.listdir(libc),
                        "媒体库侧应清空")

    # -------- F. 统计数字不为负 --------
    def test_stats_never_negative(self):
        cand, pairs, content, libc = self.make_seed_files(count=1)
        self.index_and_clean(cand)
        for key, val in self.plugin._clean_stats.items():
            self.assertGreaterEqual(val, 0, f"统计项 {key} 不得为负")

    # -------- G. 索引：单文件路径也必须被正确记录（非目录）--------
    def test_inode_index_accepts_plain_file_path(self):
        """关联路径里可能是**单个文件**（``cand["files"]`` 提供的下载侧清单）。

        必须走 ``_record`` 直接记录，而不能假定都是目录。
        变异「不判 isdir → 对文件也 walk」在此暴露：walk 对文件返回空，
        结果索引里少了这几条，媒体库侧硬链接就找不到同 inode 而漏删。
        """
        cand, pairs, content, libc = self.make_seed_files(count=3)
        f_dl = pairs[0][0]
        # 只传单文件路径（模拟 files 清单只有文件、没有目录）
        ino_index = self.plugin._build_inode_index([f_dl])
        self.assertEqual(len(ino_index), 1, "单文件路径应被记录为一个 inode")
        key = next(iter(ino_index))
        self.assertEqual(ino_index[key], [f_dl],
                        "该 inode 应精确指向传入的文件路径")

    # -------- H. 硬链接清理：已不存在的路径不得计入删除条数 --------
    def test_missing_paths_not_counted_as_removed(self):
        """索引里含「磁盘上已不存在」的路径时，计数必须精确为 0。

        变异「已不存在 → removed += 1」会把计数虚高，让摘要谎报「已清理
        N 条」，而实际一条都没删（空间没释放却被报告成已释放）。
        """
        cand, pairs, content, libc = self.make_seed_files(count=2)
        related = self.plugin._seed_related_paths(cand)
        ino_index = self.plugin._build_inode_index(related)
        # 建完索引后把磁盘上的文件全删掉（模拟期间被别的进程清走）
        for f_dl, f_lib in pairs:
            os.unlink(f_dl)
            os.unlink(f_lib)
        removed = self.plugin._clean_hardlinks_for(ino_index, cand["title"])
        self.assertEqual(removed, 0,
                        "文件已不存在时计数必须为 0，不得虚报已删")


if __name__ == "__main__":
    unittest.main()
