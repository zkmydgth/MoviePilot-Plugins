# -*- coding: utf-8 -*-
"""
备份内容勾选测试（v3.2.0 模块 A）：5 项复选框、user.db 归位、manifest.parts 与老配置迁移。

为什么单独做
------------
备份内容从「一个开关（备份插件配置）」变成「5 项可勾选」后，最容易出错的是
**勾了 A 却把 B 也带走了** —— 尤其 SQLite 的 ``user.db`` 原先混在「系统配置」段里，
不拆开的话取消勾选「数据库」也照样把库文件打进包（反之勾「系统配置」会连库一起带走）。
这类偏差在默认全选（= 全量）路径下永远看不出来，正是「正常路径用例全绿但功能已坏」的典型。

覆盖清单
--------
1. 只勾「系统配置」→ 包内无 ``user.db*``、无 ``plugins/``、无 ``cookies/``
2. 只勾「数据库」（SQLite）→ 包内有 ``user.db``，且复制前先 checkpoint
3. 只勾「站点 Cookie」/「插件配置」→ 包内只有对应内容
4. 全不勾 → 拒绝备份、不打空包、不产生 zip
5. ``manifest.parts`` 与实际勾选一致（含固定顺序）
6. 老配置迁移：无 ``backup_parts`` 时按 ``backup_plugins`` 推导；两者都无 → 全选
7. 摘要按 parts 展示；无 parts 的老包仍按旧逻辑降级
8. 勾「附加路径」但未配置路径 → 跳过且不算失败；未勾则忽略已配置路径
9. PostgreSQL 场景只在勾了「数据库」时才走 ``__dump_database``
"""

import json
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  触发宿主桩路径注入

from app.runtime.config import settings
from configbackup import ConfigBackup
from tests.test_boundary import _BoundaryBase


class _PartsBase(_BoundaryBase):
    """备份内容用例夹具：造一份"什么都有"的假 /config。"""

    def setUp(self):
        super().setUp()
        self.cfg = Path(self.base) / "cfg"
        (self.cfg / "plugins" / "plugina").mkdir(parents=True)
        (self.cfg / "plugins" / "plugina" / "data.json").write_text("{}")
        (self.cfg / "cookies").mkdir()
        (self.cfg / "cookies" / "site.json").write_text("{}")
        (self.cfg / "app.env").write_text("A=1")
        (self.cfg / "category.yaml").write_text("x: 1")
        (self.cfg / "user.db").write_bytes(b"SQLite format 3\x00fake")
        (self.cfg / "user.db-wal").write_bytes(b"wal")

        self.tmp = Path(self.base) / "tmp"
        self.tmp.mkdir()

    # ------------------------------------------------------------------
    def run_backup(self, parts):
        """按指定勾选项跑一次备份，返回 (是否成功, 信息, 包内文件清单)。"""
        self.plugin._backup_parts = tuple(parts)
        with mock.patch.object(settings, "CONFIG_PATH", str(self.cfg)), \
                mock.patch.object(settings, "TEMP_PATH", str(self.tmp)):
            ok, msg = self.plugin._ConfigBackup__run_backup()

        zips = sorted(Path(self.backup_dir).glob("bk_*.zip"))
        names = []
        if zips:
            with zipfile.ZipFile(zips[-1]) as zf:
                names = zf.namelist()
        return ok, msg, names

    def manifest(self):
        """读取唯一那个备份包内的清单。"""
        zips = sorted(Path(self.backup_dir).glob("bk_*.zip"))
        self.assertEqual(len(zips), 1, f"应恰好产出 1 个备份包，实际：{zips}")
        return ConfigBackup._ConfigBackup__read_manifest(zips[0])


class TestPartSelection(_PartsBase):
    """勾选什么就只备份什么。"""

    def test_system_only_excludes_database(self):
        """只勾「系统配置」时，SQLite 的 user.db 绝不能被打进包（核心回归点）。"""
        ok, msg, names = self.run_backup(["system"])

        self.assertTrue(ok, msg)
        self.assertIn("app.env", names)
        self.assertIn("category.yaml", names)
        self.assertFalse(
            [n for n in names if n.startswith("user.db")],
            f"user.db 属「数据库」项，不该被「系统配置」带走：{names}",
        )
        self.assertFalse([n for n in names if n.startswith("plugins/")], names)
        self.assertFalse([n for n in names if n.startswith("cookies/")], names)
        self.assertEqual(self.manifest()["parts"], ["system"])

    def test_database_only_keeps_user_db(self):
        """只勾「数据库」时，SQLite 的 user.db 必须进包，且不带系统配置。"""
        ok, msg, names = self.run_backup(["database"])

        self.assertTrue(ok, msg)
        self.assertIn("user.db", names)
        self.assertNotIn("app.env", names)
        self.assertEqual(self.manifest()["parts"], ["database"])
        self.assertTrue(self.manifest()["database"], "清单里的 database 应表示包里确实有库")

    def test_checkpoint_before_copying_database(self):
        """复制数据库文件前必须先 checkpoint，否则拿到的是不完整快照。"""
        with mock.patch.object(ConfigBackup, "_ConfigBackup__checkpoint_sqlite") as ck:
            ok, msg, names = self.run_backup(["database"])

        self.assertTrue(ok, msg)
        self.assertTrue(ck.called, "未 checkpoint 就复制 user.db 会拿到不完整快照")
        self.assertIn("user.db", names)

    def test_checkpoint_not_called_when_database_unchecked(self):
        """没勾「数据库」时不该碰数据库文件（连 checkpoint 都不该做）。"""
        with mock.patch.object(ConfigBackup, "_ConfigBackup__checkpoint_sqlite") as ck:
            ok, msg, _ = self.run_backup(["system"])

        self.assertTrue(ok, msg)
        self.assertFalse(ck.called, "未勾选数据库却动了 user.db")

    def test_cookies_only(self):
        """只勾「站点 Cookie」时只带 cookies。"""
        ok, msg, names = self.run_backup(["cookies"])

        self.assertTrue(ok, msg)
        self.assertIn("cookies/site.json", names)
        self.assertNotIn("app.env", names)
        self.assertNotIn("user.db", names)

    def test_plugins_only(self):
        """只勾「插件配置」时只带 plugins。"""
        ok, msg, names = self.run_backup(["plugins"])

        self.assertTrue(ok, msg)
        self.assertIn("plugins/plugina/data.json", names)
        self.assertNotIn("app.env", names)

    def test_postgres_only_dumped_when_database_selected(self):
        """PostgreSQL 场景：只有勾了「数据库」才走 SQL 导出。"""
        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__dump_database", return_value=(True, "pg ok")
        ) as dump:
            with mock.patch.object(settings, "DB_TYPE", "postgresql"):
                self.run_backup(["system"])
            self.assertFalse(dump.called, "未勾「数据库」却导出了 SQL")

        with mock.patch.object(
            ConfigBackup, "_ConfigBackup__dump_database", return_value=(True, "pg ok")
        ) as dump:
            with mock.patch.object(settings, "DB_TYPE", "postgresql"):
                ok, msg, _ = self.run_backup(["database"])
            self.assertTrue(dump.called)
            self.assertTrue(ok, msg)

    def test_manifest_parts_follow_canonical_order(self):
        """清单里的 parts 按固定顺序落盘，不受勾选顺序影响。"""
        ok, msg, _ = self.run_backup(["plugins", "system"])

        self.assertTrue(ok, msg)
        self.assertEqual(self.manifest()["parts"], ["system", "plugins"])


class TestEmptySelection(_PartsBase):
    """一项都不勾：拒绝备份，不打空包。"""

    def test_empty_parts_refused_without_package(self):
        """全不勾时必须拒绝，并说清原因，备份目录不得留下任何包。"""
        ok, msg, names = self.run_backup([])

        self.assertFalse(ok)
        self.assertIn("未勾选任何备份内容", msg)
        self.assertEqual(names, [])
        self.assertEqual(
            sorted(p.name for p in Path(self.backup_dir).iterdir()), [],
            "拒绝备份时不应留下任何产物（含临时文件）",
        )

    def test_empty_parts_leaves_temp_dir_clean(self):
        """拒绝发生在解压/建临时目录之前，不该产生中间目录。"""
        self.run_backup([])

        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), [])


class TestPartsMigration(_PartsBase):
    """老配置迁移：升级后行为必须与升级前一致。"""

    def test_missing_parts_migrates_from_legacy_switch(self):
        """只有老开关 backup_plugins=False → 迁移后不含「插件配置」。"""
        self.plugin.init_plugin({"enabled": True, "backup_plugins": False})

        self.assertNotIn("plugins", self.plugin._backup_parts)
        self.assertIn("database", self.plugin._backup_parts)
        self.assertIn("system", self.plugin._backup_parts)

    def test_legacy_switch_true_keeps_plugins(self):
        """老开关为 True（或缺失）→ 迁移后全选，与升级前行为一致。"""
        self.plugin.init_plugin({"enabled": True, "backup_plugins": True})
        self.assertEqual(tuple(self.plugin._backup_parts), ConfigBackup._ALL_PARTS)

        self.plugin.init_plugin({"enabled": True})
        self.assertEqual(tuple(self.plugin._backup_parts), ConfigBackup._ALL_PARTS)

    def test_explicit_empty_parts_stays_empty(self):
        """显式存了空列表 = 用户主动全取消，不得被迁移逻辑塞回默认值。"""
        self.plugin.init_plugin({"enabled": True, "backup_parts": []})

        self.assertEqual(tuple(self.plugin._backup_parts), ())

    def test_new_config_wins_over_legacy_switch(self):
        """两个键都在时以 backup_parts 为准（老键只用于迁移）。"""
        self.plugin.init_plugin({
            "enabled": True, "backup_parts": ["system"], "backup_plugins": True,
        })

        self.assertEqual(tuple(self.plugin._backup_parts), ("system",))

    def test_unknown_values_dropped_and_order_normalized(self):
        """未知取值丢弃，并按固定顺序归位（前端可能传来任意顺序）。"""
        self.plugin.init_plugin({
            "enabled": True, "backup_parts": ["extra", "不存在的项", "system"],
        })

        self.assertEqual(tuple(self.plugin._backup_parts), ("system", "extra"))

    def test_string_parts_parsed(self):
        """宿主把多选存成逗号串时也要能解析（列表/逗号串双兼容）。"""
        self.plugin.init_plugin({"enabled": True, "backup_parts": "system, cookies"})

        self.assertEqual(tuple(self.plugin._backup_parts), ("system", "cookies"))


class TestPartsSummary(_PartsBase):
    """摘要：新包按 parts 展示，老包按旧逻辑降级。"""

    def test_summary_lists_selected_parts(self):
        """有 parts 的包：只列出实际勾选的部分。"""
        path = self.make_backup("20260918000000")
        with zipfile.ZipFile(path, "a") as zf:
            zf.writestr("backup_manifest.json", json.dumps({
                "parts": ["database", "cookies"], "db_type": "sqlite",
            }))

        summary = ConfigBackup._ConfigBackup__summarize(str(path))

        self.assertIn("数据库", summary)
        self.assertIn("站点 Cookie", summary)
        self.assertNotIn("插件配置", summary)

    def test_summary_shows_extra_count(self):
        """附加路径有计数时展示 ×N。"""
        path = self.make_backup("20260918000000")
        with zipfile.ZipFile(path, "a") as zf:
            zf.writestr("backup_manifest.json", json.dumps({
                "parts": ["extra"], "extra_count": 3,
            }))

        self.assertIn("附加×3", ConfigBackup._ConfigBackup__summarize(str(path)))

    def test_legacy_package_without_parts_still_summarized(self):
        """无 parts 的老包不得因为新字段而变空——仍按包里实际内容展示。"""
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


class TestExtraPart(_PartsBase):
    """附加路径：勾了才做，没配路径不算失败。"""

    def test_extra_selected_without_paths_is_not_failure(self):
        """勾了「附加路径」但没配置任何路径：跳过，不算失败。"""
        self.plugin._extra_paths = ""
        ok, msg, _ = self.run_backup(["system", "extra"])

        self.assertTrue(ok, f"未配置附加路径应跳过而非失败：{msg}")

    def test_extra_not_selected_ignores_paths(self):
        """没勾「附加路径」时，即使配了路径也不该复制。"""
        src = Path(self.base) / "extra-src"
        src.mkdir()
        (src / "keep.txt").write_text("k")
        self.plugin._extra_paths = str(src)

        ok, msg, names = self.run_backup(["system"])

        self.assertTrue(ok, msg)
        self.assertFalse([n for n in names if n.startswith("extra/")], names)

    def test_extra_selected_copies_paths(self):
        """勾了「附加路径」且配了路径：内容进包，清单计数正确。"""
        src = Path(self.base) / "extra-src"
        src.mkdir()
        (src / "keep.txt").write_text("k")
        self.plugin._extra_paths = str(src)

        ok, msg, names = self.run_backup(["system", "extra"])

        self.assertTrue(ok, msg)
        self.assertTrue([n for n in names if n.startswith("extra/")], names)
        self.assertEqual(self.manifest()["extra_count"], 1)


class TestPartsForm(_PartsBase):
    """配置表单：备份内容必须是多项勾选，且默认全选。"""

    def _props(self, model):
        """按 model 名在表单树里找出所有控件 props。"""
        form, _ = self.plugin.get_form()
        found = []

        def walk(nodes):
            for node in nodes or []:
                props = node.get("props") or {}
                if props.get("model") == model:
                    found.append(props)
                walk(node.get("content"))

        walk(form)
        return found

    def test_backup_parts_is_multi_select(self):
        """「备份内容」必须是一个多选控件，且选项恰好是 5 个部分。"""
        props = self._props("backup_parts")

        self.assertEqual(len(props), 1, "应有且仅有一个 backup_parts 控件")
        self.assertIs(props[0].get("multiple"), True, "多选必须显式声明 multiple")
        self.assertEqual(
            [item["value"] for item in props[0]["items"]],
            list(ConfigBackup._ALL_PARTS),
        )

    def test_legacy_switch_removed_from_form(self):
        """老的「备份插件配置」开关必须从表单移除，避免与多选语义打架。"""
        self.assertEqual(self._props("backup_plugins"), [])

    def test_default_config_selects_all_parts(self):
        """表单默认值必须是 5 项全选（= 全量，老用户升级后行为不变）。"""
        _, default = self.plugin.get_form()

        self.assertEqual(list(default["backup_parts"]), list(ConfigBackup._ALL_PARTS))
        self.assertNotIn("backup_plugins", default)

    def test_hint_is_persistent(self):
        """说明文字必须常驻显示（否则桌面端要点开控件才看得到）。"""
        props = self._props("backup_parts")[0]

        self.assertIs(props.get("persistent-hint"), True)


if __name__ == "__main__":
    unittest.main()
