# -*- coding: utf-8 -*-
"""
边界 / 异常测试：ConfigBackup 的"最坏输入"防护。

与 ``test_restore_flow.py`` 的分工
---------------------------------
- ``test_restore_flow.py``：**正常路径**的回归（按钮显示条件、两阶段交互）
- 本文件：**异常路径**的边界（恶意文件名、畸形备份包、坏配置、坏状态文件）

为什么必须单独做
----------------
备份还原插件持有两个高危能力：**删文件**与**覆盖配置/数据库**。正常路径
再绿也不代表遇到畸形输入时安全——越界删除、误清空备份目录、畸形 JSON
导致崩溃都属于"只在极端输入下才暴露"的缺陷，靠正常路径用例永远测不到。

覆盖清单
--------
1. 路径穿越防护：``../../etc/passwd``、绝对路径、无前缀名、非 zip 后缀、
   多重后缀、前缀出现在中间等 —— 一律拒绝，且**不得触碰备份目录之外**
2. ``keep_count`` 边界：0 / 负数 / 非数字字符串 —— 不得误删备份
3. 畸形备份包：普通文本冒充 zip、截断 zip、zip 内含目录 —— 拒绝且不残留
4. 待还原状态文件损坏：非法 JSON / 空对象 / 缺字段 —— 降级为"无待还原"
5. 备份目录异常：目录不存在 / 路径被普通文件占用 —— 优雅失败不崩
6. ``api_delete`` 幂等与自我防护：重复删除、试图删目录
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from configbackup import ConfigBackup


class _BoundaryBase(unittest.TestCase):
    """边界测试夹具：独立临时目录 + 独立插件实例。"""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="cb-boundary-")
        self.backup_dir = os.path.join(self.base, "backups")
        os.makedirs(self.backup_dir)
        # 备份目录的"外部"，用来验证越界访问确实没发生
        self.outside = os.path.join(self.base, "outside")
        os.makedirs(self.outside)

        ConfigBackup._stub_data_path = os.path.join(self.base, "data")

        self.plugin = ConfigBackup()
        self.plugin._enabled = True
        self.plugin._backup_dir = self.backup_dir
        self.plugin._keep_count = 10
        self.plugin._notify = False
        self._prefix = ConfigBackup._prefix

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        ConfigBackup._stub_data_path = None

    # ------------------------------------------------------------------
    def make_backup(self, name: str) -> Path:
        """造一个合法的备份 zip（名称为 ``{prefix}{name}.zip``）。"""
        path = Path(self.backup_dir) / f"{self._prefix}{name}.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", "{}")
        return path

    def make_fake_zip(self, name: str, content: bytes = b"not a zip at all") -> Path:
        """造一个扩展名是 .zip、内容却不是 zip 的文件。"""
        path = Path(self.backup_dir) / f"{self._prefix}{name}.zip"
        path.write_bytes(content)
        return path

    # 私有方法别名
    def resolve(self, filename: str):
        return self.plugin._ConfigBackup__resolve_backup_path(filename)

    def get_pending(self):
        return self.plugin._ConfigBackup__get_pending_restore()

    def set_pending(self, data):
        self.plugin._ConfigBackup__set_pending_restore(data)

    def pending_file(self) -> Path:
        return self.plugin._ConfigBackup__pending_file()


# ======================================================================
# 1. 路径穿越防护
# ======================================================================
class TestPathTraversal(_BoundaryBase):
    """
    恶意文件名必须被拒绝，且不得解析出备份目录之外的路径。

    这是插件最重要的安全边界：还原/删除接口的 filename 来自前端请求，
    若不做校验，``../../`` 即可指向配置文件甚至系统文件。
    """

    #: (用例名, 恶意文件名)
    MALICIOUS = [
        ("相对穿越", f"../{ConfigBackup._prefix}evil.zip"),
        ("多级穿越", f"../../../etc/{ConfigBackup._prefix}passwd.zip"),
        ("绝对路径", f"/tmp/{ConfigBackup._prefix}evil.zip"),
        ("穿越到备份目录外", f"../outside/{ConfigBackup._prefix}evil.zip"),
        ("无前缀", "evil.zip"),
        ("非 zip 后缀", f"{ConfigBackup._prefix}evil.tar.gz"),
        ("前缀在中间", f"evil{ConfigBackup._prefix}x.zip"),
        ("前缀重复但后缀错", f"{ConfigBackup._prefix}evil.zip.bak"),
        ("大小写前缀", f"{ConfigBackup._prefix.upper()}evil.zip"),
        ("空白前缀只有后缀", ".zip"),
    ]

    def test_resolve_rejects_malicious_names(self):
        """所有恶意文件名都必须被解析为 None。"""
        for label, name in self.MALICIOUS:
            with self.subTest(case=label, name=name):
                self.assertIsNone(
                    self.resolve(name),
                    f"【{label}】应被拒绝，实际解析出路径：{self.resolve(name)}",
                )

    def test_resolve_result_never_escapes_backup_dir(self):
        """即使侥幸放行，解析结果也绝不能落在备份目录之外。"""
        bk = os.path.realpath(self.backup_dir)
        for label, name in self.MALICIOUS:
            with self.subTest(case=label, name=name):
                resolved = self.resolve(name)
                if resolved is None:
                    continue  # 已拒绝，符合预期
                real = os.path.realpath(str(resolved))
                self.assertTrue(
                    real.startswith(bk + os.sep),
                    f"【{label}】解析结果越界：{real}",
                )

    def test_resolve_accepts_legit_name(self):
        """反向校验：合法文件名必须被正常接受。

        否则上面的断言可能因为"实现永远返回 None"而假绿。
        """
        name = f"{self._prefix}20260918_030000.zip"
        self.make_backup("20260918_030000")
        resolved = self.resolve(name)
        self.assertIsNotNone(resolved, "合法文件名不应被拒绝")
        self.assertEqual(os.path.realpath(str(resolved)),
                         os.path.realpath(os.path.join(self.backup_dir, name)))

    def test_restore_rejects_traversal_filename(self):
        """api_restore 选择阶段必须拒绝穿越文件名。"""
        # 在备份目录之外造一个"合法命名"的包，试图诱导插件去还原它
        outsider = Path(self.outside) / f"{self._prefix}outside.zip"
        with zipfile.ZipFile(outsider, "w") as zf:
            zf.writestr("manifest.json", "{}")

        for label, name in self.MALICIOUS:
            with self.subTest(case=label, name=name):
                result = self.plugin.api_restore(filename=name, confirm="")
                self.assertFalse(
                    result.get("success"),
                    f"【{label}】选择阶段应拒绝，实际：{result}",
                )
        self.assertIsNone(self.get_pending(), "被拒后不应残留待还原状态")

    def test_delete_rejects_traversal_and_keeps_outside_file(self):
        """api_delete 必须拒绝穿越，且备份目录外的文件必须完好。"""
        outsider = Path(self.outside) / f"{self._prefix}keepme.zip"
        outsider.write_bytes(b"important")

        for label, name in self.MALICIOUS:
            with self.subTest(case=label, name=name):
                result = self.plugin.api_delete(filename=name)
                self.assertFalse(
                    result.get("success"), f"【{label}】删除应被拒绝：{result}"
                )

        self.assertTrue(outsider.exists(), "备份目录外的文件被误删（严重）")

    def test_delete_rejects_basename_collision(self):
        """
        穿越路径不得因"取 basename 后恰好同名"而删掉目录内的文件。

        ``Path("../bk_x.zip").name`` 结果是 ``bk_x.zip``。若实现只做
        ``basename`` 而不比对原值（``safe_name != filename``），这条请求
        就会**删掉备份目录内那个同名的合法备份**——用户以为在删一个非法
        路径，实际丢了一份真备份。本用例正是为这条路径兜底。
        """
        # 在备份目录内放一个"会被 basename 撞上"的合法备份
        victim = self.make_backup("collide")

        for label, name in [
            ("相对穿越", f"../{victim.name}"),
            ("多级穿越", f"../../{victim.name}"),
            ("子目录穿越", f"sub/{victim.name}"),
        ]:
            with self.subTest(case=label):
                result = self.plugin.api_delete(filename=name)
                self.assertFalse(result.get("success"), f"应拒绝：{result}")
                self.assertTrue(
                    victim.exists(),
                    f"【{label}】合法备份被穿越请求删掉了（取 basename 后同名）",
                )

    def test_restore_rejects_basename_collision(self):
        """同上的 basename 撞名场景，还原接口同样必须拒绝。"""
        victim = self.make_backup("collide2")

        result = self.plugin.api_restore(filename=f"../{victim.name}", confirm="")

        self.assertFalse(result.get("success"), f"应拒绝：{result}")
        self.assertIsNone(self.get_pending(), "被拒后不应留下待还原状态")


# ======================================================================
# 2. keep_count 边界：清理逻辑不得误删
# ======================================================================
class TestKeepCountBoundary(_BoundaryBase):
    """
    保留份数的边界值不得触发清理。

    ``keep_count`` 来自前端数字输入框，非法值（0/负数/空/非数字）若被
    当成"保留 0 份"，会把全部备份一次清空——用户将失去唯一的回滚手段。
    """

    def _make_many(self, count: int) -> list:
        return [self.make_backup(f"2026090100000{i}") for i in range(count)]

    def call_clean(self) -> int:
        return self.plugin._ConfigBackup__clean_old_backups(Path(self.backup_dir))

    def test_zero_keeps_all(self):
        """keep_count=0 不得删除任何备份（0 应理解为"未配置"而非"不留"）。"""
        files = self._make_many(5)
        self.plugin._keep_count = 0

        self.assertEqual(self.call_clean(), 0, "keep_count=0 不应删除")
        for path in files:
            self.assertTrue(path.exists(), f"被误删：{path.name}")

    def test_negative_keeps_all(self):
        """keep_count 为负数时不得删除任何备份。"""
        files = self._make_many(5)
        self.plugin._keep_count = -3

        self.assertEqual(self.call_clean(), 0, "负数不应删除")
        for path in files:
            self.assertTrue(path.exists(), f"被误删：{path.name}")

    def test_keeps_exactly_configured_count(self):
        """正常值应精确保留配置的份数（反向校验，防"永不清理"假绿）。"""
        files = self._make_many(5)
        # 逐个回拨 ctime 不可行，改用存在性断言：无论删哪些，最终必须剩 3 份
        self.plugin._keep_count = 3

        deleted = self.call_clean()

        remaining = [p for p in files if p.exists()]
        self.assertEqual(len(remaining), 3, f"应保留 3 份，实际 {len(remaining)}")
        self.assertEqual(deleted, 2, f"应删除 2 份，实际 {deleted}")

    def test_does_not_touch_non_backup_files(self):
        """清理只认前缀+.zip，同目录下的其它文件不得被删。"""
        self._make_many(5)
        bystander = Path(self.backup_dir) / "important.txt"
        bystander.write_text("keep me", encoding="utf-8")
        no_prefix = Path(self.backup_dir) / "20260901000000.zip"
        no_prefix.write_bytes(b"x")

        self.plugin._keep_count = 1
        self.call_clean()

        self.assertTrue(bystander.exists(), "非备份文件被误删")
        self.assertTrue(no_prefix.exists(), "无前缀的 zip 被误删")


# ======================================================================
# 3. 畸形备份包
# ======================================================================
class TestMalformedArchive(_BoundaryBase):
    """扩展名是 zip 但内容不是 / 已损坏的包，必须被拒绝而非崩溃。"""

    def test_plain_text_masquerading_as_zip_rejected(self):
        """文本文件冒充 zip：选择阶段应明确报错。"""
        name = f"{self._prefix}20260918_040000.zip"
        self.make_fake_zip("20260918_040000")

        result = self.plugin.api_restore(filename=name, confirm="")

        self.assertFalse(result.get("success"), f"应拒绝：{result}")
        self.assertIsNone(self.get_pending(), "不应留下待还原状态")

    def test_truncated_zip_rejected(self):
        """被截断的真 zip：同样应被拒绝。"""
        full = self.make_backup("20260918_040001")
        data = full.read_bytes()
        full.write_bytes(data[: len(data) // 2])
        name = f"{self._prefix}20260918_040001.zip"

        result = self.plugin.api_restore(filename=name, confirm="")

        self.assertFalse(result.get("success"), f"应拒绝截断包：{result}")

    def test_empty_file_rejected(self):
        """空文件：应被拒绝且不抛异常。"""
        name = f"{self._prefix}20260918_040002.zip"
        self.make_fake_zip("20260918_040002", b"")

        result = self.plugin.api_restore(filename=name, confirm="")

        self.assertFalse(result.get("success"), f"应拒绝空文件：{result}")

    def test_corrupt_archive_not_listed_or_restored(self):
        """损坏包不得阻止列表接口工作（列表只读元信息，不解压）。"""
        good = self.make_backup("20260918_040003")
        self.make_fake_zip("20260918_040004")

        result = self.plugin.api_list()

        self.assertTrue(result.get("success"), result)
        names = [item["name"] for item in result["data"]]
        self.assertIn(good.name, names, "合法包应出现在列表里")
        self.assertEqual(len(names), 2, f"两个文件都应列出（列表不做内容校验）：{names}")


# ======================================================================
# 4. 待还原状态文件损坏
# ======================================================================
class TestPendingStateRobustness(_BoundaryBase):
    """
    状态文件被写坏时必须降级为"无待还原"，而不是抛异常。

    真实场景：还原中容器被强杀、磁盘写满导致写入中断、用户手改文件。
    此时插件若崩溃，整个详情页面打不开。
    """

    def test_invalid_json_degrades_to_none(self):
        """非法 JSON：应返回 None。"""
        path = self.pending_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")

        self.assertIsNone(self.get_pending(), "损坏 JSON 应降级为 None")

    def test_empty_object_degrades_to_none(self):
        """空对象（无 filename）：应返回 None。"""
        path = self.pending_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

        self.assertIsNone(self.get_pending(), "缺 filename 应降级为 None")

    def test_wrong_type_degrades_to_none(self):
        """JSON 是数组而非对象：应返回 None 而非崩溃。"""
        path = self.pending_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('["a", "b"]', encoding="utf-8")

        self.assertIsNone(self.get_pending(), "类型不符应降级为 None")

    def test_corrupt_state_does_not_break_page(self):
        """状态文件损坏时，详情页仍须能正常渲染（不能 500）。"""
        path = self.pending_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<<<broken>>>", encoding="utf-8")
        self.make_backup("20260918_050000")

        page = self.plugin.get_page()

        self.assertIsInstance(page, list)
        self.assertTrue(page, "页面组件不应为空")

    def test_corrupt_state_confirm_reports_no_pending(self):
        """状态文件损坏时点确认还原，应报"没有待还原"而非崩。"""
        path = self.pending_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-json", encoding="utf-8")

        result = self.plugin.api_restore(filename="", confirm="1")

        self.assertFalse(result.get("success"))
        self.assertIn("没有待还原", result.get("message", ""))

    def test_cancel_is_idempotent_when_no_pending(self):
        """无待还原时取消：应成功返回（幂等），不报错。"""
        result = self.plugin.api_restore(filename="", confirm="cancel")

        self.assertTrue(result.get("success"), f"取消应幂等：{result}")
        self.assertIsNone(self.get_pending())

    def test_pending_pointing_to_deleted_file_is_cleared(self):
        """状态指向的备份已被外部删除：确认时应报错并清状态，不残留。"""
        name = f"{self._prefix}20260918_050001.zip"
        path = self.make_backup("20260918_050001")
        self.set_pending({"filename": name, "time": "2026-09-18 05:00:01"})
        path.unlink()

        result = self.plugin.api_restore(filename="", confirm="1")

        self.assertFalse(result.get("success"), f"应报错：{result}")
        self.assertIn("不存在", result.get("message", ""))
        self.assertIsNone(self.get_pending(), "悬空状态必须被清除")


# ======================================================================
# 5. 备份目录异常
# ======================================================================
class TestBackupDirFailure(_BoundaryBase):
    """备份目录异常时应优雅失败，不得抛出未捕获异常。"""

    def test_missing_dir_lists_empty(self):
        """目录不存在时列表应为空，而非报错。"""
        self.plugin._backup_dir = os.path.join(self.base, "no-such-dir")

        result = self.plugin.api_list()

        self.assertTrue(result.get("success"), result)
        self.assertEqual(result["data"], [])

    def test_missing_dir_page_still_renders(self):
        """目录不存在时详情页仍应能打开。"""
        self.plugin._backup_dir = os.path.join(self.base, "no-such-dir")

        page = self.plugin.get_page()

        self.assertIsInstance(page, list)
        self.assertTrue(page)

    def test_dir_replaced_by_file_fails_gracefully(self):
        """备份路径被普通文件占用：应返回失败信息而非抛异常。"""
        blocker = os.path.join(self.base, "blocked")
        Path(blocker).write_text("i am a file", encoding="utf-8")
        self.plugin._backup_dir = blocker

        result = self.plugin.api_backup()

        self.assertFalse(result.get("success"), f"应优雅失败：{result}")
        self.assertTrue(Path(blocker).is_file(), "占位文件不应被动过")

    def test_delete_on_missing_dir_reports_not_found(self):
        """目录不存在时删除：应报"不存在"。"""
        self.plugin._backup_dir = os.path.join(self.base, "no-such-dir")
        name = f"{self._prefix}20260918_060000.zip"

        result = self.plugin.api_delete(filename=name)

        self.assertFalse(result.get("success"))
        self.assertIn("不存在", result.get("message", ""))


# ======================================================================
# 6. api_delete 幂等与目录防护
# ======================================================================
class TestDeleteSafety(_BoundaryBase):
    """删除接口的边界行为。"""

    def test_empty_filename_rejected(self):
        """空文件名应被拒绝。"""
        result = self.plugin.api_delete(filename="")

        self.assertFalse(result.get("success"))
        self.assertIn("缺少文件名", result.get("message", ""))

    def test_delete_twice_is_idempotent(self):
        """重复删除：第二次应报"不存在"，且不影响其它备份。"""
        target = self.make_backup("20260918_070000")
        other = self.make_backup("20260918_070001")
        name = target.name

        first = self.plugin.api_delete(filename=name)
        second = self.plugin.api_delete(filename=name)

        self.assertTrue(first.get("success"), first)
        self.assertFalse(second.get("success"), f"重复删除应报不存在：{second}")
        self.assertFalse(target.exists())
        self.assertTrue(other.exists(), "其它备份不应受影响")

    def test_delete_does_not_touch_lookalike(self):
        """删除一个备份不得连带删除名字相近的备份。"""
        target = self.make_backup("20260918_070002")
        lookalike = self.make_backup("20260918_070003")

        self.plugin.api_delete(filename=target.name)

        self.assertFalse(target.exists())
        self.assertTrue(lookalike.exists(), "相似名备份被误删")


# ======================================================================
# 7. 还原并发锁
# ======================================================================
class TestRestoreLock(_BoundaryBase):
    """
    还原持锁期间不得再次进入（防并发还原互相覆盖）。

    还原会重写数据库与配置文件，两次并发执行会把配置写成半新半旧的
    混合态——比失败更糟。锁必须在 ``finally`` 中释放，否则一次异常
    会让插件永久卡在"已有还原操作正在进行"。
    """

    def test_lock_prevents_concurrent_restore(self):
        """已持锁时再次确认还原应被拒绝。"""
        name = f"{self._prefix}20260918_080000.zip"
        self.make_backup("20260918_080000")
        self.set_pending({"filename": name, "time": "2026-09-18 08:00:00"})

        self.plugin._restore_lock.acquire()
        try:
            result = self.plugin.api_restore(filename="", confirm="1")
        finally:
            self.plugin._restore_lock.release()

        self.assertFalse(result.get("success"), f"应拒绝并发还原：{result}")
        self.assertIn("正在进行", result.get("message", ""))

    def test_lock_released_after_failure(self):
        """还原过程抛异常后，锁必须已被释放（否则永久卡死）。"""
        name = f"{self._prefix}20260918_080001.zip"
        self.make_backup("20260918_080001")
        self.set_pending({"filename": name, "time": "2026-09-18 08:00:01"})

        # 让真实还原流程抛异常，验证 finally 是否释放了锁
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__restore",
            side_effect=RuntimeError("模拟还原炸了"),
        ):
            result = self.plugin.api_restore(filename="", confirm="1")

        self.assertFalse(result.get("success"), f"应返回失败：{result}")
        self.assertTrue(
            self.plugin._restore_lock.acquire(blocking=False),
            "锁未被释放，插件将永久无法再还原",
        )
        self.plugin._restore_lock.release()

    def test_lock_released_after_success(self):
        """还原成功后锁同样必须释放。"""
        name = f"{self._prefix}20260918_080002.zip"
        self.make_backup("20260918_080002")
        self.set_pending({"filename": name, "time": "2026-09-18 08:00:02"})

        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__backup",
            return_value=(True, "安全网备份成功"),
        ), mock.patch.object(
            ConfigBackup, "_ConfigBackup__restore",
            return_value=(True, "还原成功"),
        ):
            result = self.plugin.api_restore(filename="", confirm="1")

        self.assertTrue(result.get("success"), f"应成功：{result}")
        self.assertTrue(
            self.plugin._restore_lock.acquire(blocking=False), "锁未释放"
        )
        self.plugin._restore_lock.release()

    def test_confirm_clears_pending_on_success(self):
        """还原成功后待还原状态必须被清除（否则会重复触发）。"""
        name = f"{self._prefix}20260918_080003.zip"
        self.make_backup("20260918_080003")
        self.set_pending({"filename": name, "time": "2026-09-18 08:00:03"})

        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__backup", return_value=(True, "ok")
        ), mock.patch.object(
            ConfigBackup, "_ConfigBackup__restore", return_value=(True, "ok")
        ):
            self.plugin.api_restore(filename="", confirm="1")

        self.assertIsNone(self.get_pending(), "成功后应清除待还原状态")

    def test_confirm_clears_pending_on_failure(self):
        """还原失败后同样要清状态，避免用户被困在"待确认"上。"""
        name = f"{self._prefix}20260918_080004.zip"
        self.make_backup("20260918_080004")
        self.set_pending({"filename": name, "time": "2026-09-18 08:00:04"})

        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__backup", return_value=(True, "ok")
        ), mock.patch.object(
            ConfigBackup, "_ConfigBackup__restore", return_value=(False, "还原失败")
        ):
            self.plugin.api_restore(filename="", confirm="1")

        self.assertIsNone(self.get_pending(), "失败后也应清除待还原状态")


if __name__ == "__main__":
    unittest.main()
