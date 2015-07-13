"""
「分享STRM清理 → 缺失媒体」行的键名兼容回归测试（2026-10-09）。

背景
----
V3 的媒体身份契约改为 ``media_source`` + ``media_id``，后端据此输出缺失媒体行；
但前端联邦产物 ``__federation_expose_AppPageStart`` 仍以 ``tmdbid`` / ``tvdbid`` /
``imdbid`` / ``doubanid`` 判断「该行是否有媒体 ID」并逐项渲染：

    xa = e => e.tmdbid || e.tvdbid || e.imdbid || e.doubanid

只给新键会让那一栏恒为空（判据恒假）。本文件把「按 ``media_source`` 反填旧四键」的
行为钉成断言，并含反向用例（未知来源、缺 media_id、入参不被修改、补键前落盘的历史
记录在读取时补全）。

本文件只测纯粹的行字段处理：与断言无关的插件重依赖（config / i18n / 消息 / sentry /
oof 等）都以桩模块替代，不拉起插件其余实现。
"""

import importlib
import sys
import types
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parents[1]

_PKG = "p115_missing_media_compat_under_test"


def _stub_module(name, **attrs):
    """
    注册一个最小桩模块并返回它

    :param name (str): 模块全名
    :return ModuleType: 已注册的桩模块
    """
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _synthetic_package(dotted, path):
    """
    注册「空壳包」：只给 ``__path__``，不执行插件真实的 ``__init__.py``

    :param dotted (str): 包全名
    :param path (Path): 该包对应的真实目录
    :return ModuleType: 已注册的包模块
    """
    module = sys.modules.get(dotted)
    if module is None:
        module = types.ModuleType(dotted)
        module.__path__ = [str(path)]
        sys.modules[dotted] = module
    return module


def _load_cleaner():
    """
    加载插件 cleaner 模块（含必要的桩），返回模块对象

    :return ModuleType: ``helper.strm.share.cleaner``
    """
    _synthetic_package(_PKG, plugin_root)
    for name in ("core", "helper", "utils"):
        _synthetic_package(f"{_PKG}.{name}", plugin_root / name)
    _synthetic_package(f"{_PKG}.helper.strm", plugin_root / "helper" / "strm")
    _synthetic_package(
        f"{_PKG}.helper.strm.share", plugin_root / "helper" / "strm" / "share"
    )
    _stub_module(
        f"{_PKG}.core.config",
        configer=types.SimpleNamespace(
            get_plugin_data=lambda _key: None,
            save_plugin_data=lambda _key, _value: None,
        ),
    )
    _stub_module(f"{_PKG}.core.i18n", i18n=lambda text, *_a, **_k: text)
    _stub_module(f"{_PKG}.core.message", post_message=lambda *_a, **_k: None)
    _stub_module(f"{_PKG}.helper.mediasyncdel", MediaSyncDelHelper=object)
    _stub_module(f"{_PKG}.utils.path", PathRemoveUtils=object)
    _stub_module(f"{_PKG}.utils.sentry", sentry_manager=types.SimpleNamespace())
    _stub_module(f"{_PKG}.helper.strm.share.oof", ShareOOPServerHelper=object)
    return importlib.import_module(f"{_PKG}.helper.strm.share.cleaner")


cleaner = _load_cleaner()
with_legacy_media_id_keys = cleaner.with_legacy_media_id_keys
ShareStrmMissingMediaStore = cleaner.ShareStrmMissingMediaStore

_LEGACY_KEYS = ("tmdbid", "tvdbid", "imdbid", "doubanid")


class _FakeHistory:
    """整理历史替身（只带缺失媒体行需要的字段）。"""

    def __init__(self, media_source=None, media_id=None):
        """
        :param media_source (Any): 媒体来源；真机传 ``MediaSource`` 枚举，这里传带 ``.value`` 的替身
        :param media_id (Optional[str]): 来源内的媒体 ID
        """
        self.id = 1
        self.type = "电视剧"
        self.title = "Demo"
        self.year = 2026
        self.media_source = media_source
        self.media_id = media_id
        self.seasons = "1"
        self.episodes = "1"
        self.image = None


def _source(value):
    """
    构造带 ``.value`` 的来源替身（与真机枚举同形）

    :param value (str): 规范来源值，如 ``"themoviedb"``
    :return SimpleNamespace: 带 ``value`` 属性的替身
    """
    return types.SimpleNamespace(value=value)


class _FakeShardStore:
    """分片存储替身：``page()`` 原样返回注入的行。"""

    def __init__(self, rows):
        self._rows = rows

    def page(self, _page, _limit):
        return list(self._rows), len(self._rows)


class TestLegacyMediaIdKeys(unittest.TestCase):
    """旧四键的反填规则。"""

    def test_themoviedb_fills_tmdbid_only(self):
        """TMDB 来源只填 tmdbid，其余旧键必须存在且为空。"""
        row = with_legacy_media_id_keys(
            {"media_source": "themoviedb", "media_id": "123"}
        )
        self.assertEqual(row["tmdbid"], "123")
        self.assertIsNone(row["tvdbid"])
        self.assertIsNone(row["imdbid"])
        self.assertIsNone(row["doubanid"])

    def test_each_source_maps_to_its_own_key(self):
        """tvdb / imdb / douban 各填各的键。"""
        pairs = (("tvdb", "tvdbid"), ("imdb", "imdbid"), ("douban", "doubanid"))
        for source, key in pairs:
            row = with_legacy_media_id_keys({"media_source": source, "media_id": "42"})
            self.assertEqual(row[key], "42", source)

    def test_new_keys_are_kept(self):
        """新契约键原样保留（两套键名共存）。"""
        row = with_legacy_media_id_keys(
            {"media_source": "themoviedb", "media_id": "123"}
        )
        self.assertEqual(row["media_source"], "themoviedb")
        self.assertEqual(row["media_id"], "123")

    def test_unknown_source_leaves_all_legacy_keys_empty(self):
        """bangumi 等在前契约里没有对应旧键：四个旧键存在但为空。"""
        row = with_legacy_media_id_keys({"media_source": "bangumi", "media_id": "9"})
        for key in _LEGACY_KEYS:
            self.assertIn(key, row)
            self.assertIsNone(row[key])

    def test_missing_media_id_keeps_legacy_keys_empty(self):
        """有来源但 media_id 为空（None / 空串）时不得凭空填值。"""
        for empty in (None, ""):
            with self.subTest(media_id=empty):
                row = with_legacy_media_id_keys(
                    {"media_source": "themoviedb", "media_id": empty}
                )
                # 空串必须覆盖：media_id 为 None 时「填 None」与「不填」结果相同，
                # 只有空串能把「漏判空值」这一变异体区分出来（变异测试实测逃逸后补）。
                self.assertIsNone(row["tmdbid"], repr(empty))
                self.assertEqual(row["media_id"], empty)

    def test_row_without_source_does_not_raise(self):
        """缺 media_source 的历史行不得抛异常。"""
        row = with_legacy_media_id_keys({"title": "Demo"})
        for key in _LEGACY_KEYS:
            self.assertIsNone(row[key])

    def test_existing_legacy_value_is_preserved(self):
        """已有旧键值（无 media_source）原样保留。"""
        row = with_legacy_media_id_keys({"tmdbid": "999"})
        self.assertEqual(row["tmdbid"], "999")

    def test_input_is_not_mutated(self):
        """补键只作用于副本，不修改入参。"""
        source = {"media_source": "themoviedb", "media_id": "123"}
        with_legacy_media_id_keys(source)
        self.assertEqual(source, {"media_source": "themoviedb", "media_id": "123"})


class TestRowFromTransferHistory(unittest.TestCase):
    """新写入的记录自包含旧四键。"""

    def test_row_outputs_legacy_keys(self):
        """行构造直接带 tmdbid，且保留原有字段。"""
        row = ShareStrmMissingMediaStore.row_from_transfer_history(
            _FakeHistory(_source("themoviedb"), "277910"), "/strm/x.strm", "code1", "pw1"
        )
        self.assertEqual(row["tmdbid"], "277910")
        self.assertEqual(row["media_id"], "277910")
        self.assertEqual(row["share_code"], "code1")
        self.assertEqual(row["receive_code"], "pw1")
        for key in _LEGACY_KEYS:
            self.assertIn(key, row)

    def test_row_without_identity_still_has_legacy_keys(self):
        """无媒体身份时四个旧键仍必须存在（前端判据读得到、值为空）。"""
        row = ShareStrmMissingMediaStore.row_from_transfer_history(
            _FakeHistory(None, None), "/strm/x.strm", "c", "p"
        )
        for key in _LEGACY_KEYS:
            self.assertIsNone(row[key])


class TestHostContractFidelity(unittest.TestCase):
    """桩宿主的常量取值必须与真机一致（不然测试会替真机"通过"）。"""

    def test_tmdb_value_is_canonical(self):
        """``MediaSource.TMDB`` 的规范值是 "themoviedb"（真机 app/schemas/types.py）。"""
        from app.schemas.types import MediaSource

        self.assertEqual(MediaSource.TMDB.value, "themoviedb")

    def test_mapping_values_exist_in_host_contract(self):
        """反填表用到的来源值必须都在宿主契约里（成员名拼写不参与断言）。"""
        from app.schemas.types import MediaSource

        values = {member.value for member in MediaSource}
        for value in ("themoviedb", "tvdb", "imdb", "douban"):
            self.assertIn(value, values)

    def test_mapping_table_is_subset_of_host_sources(self):
        """反填表的键必须都能在宿主 MediaSource 里找到（防表里写不存在的来源）。"""
        from app.schemas.types import MediaSource

        values = {member.value for member in MediaSource}
        self.assertTrue(
            set(cleaner._LEGACY_MEDIA_ID_KEYS) <= values,
            f"反填表含宿主没有的来源: {set(cleaner._LEGACY_MEDIA_ID_KEYS) - values}",
        )


class TestPageNormalizesPersistedRows(unittest.TestCase):
    """补键之前落盘的行，读取时补全（老数据同样能渲染）。"""

    @staticmethod
    def _store_with(rows):
        store = ShareStrmMissingMediaStore()
        store._store = _FakeShardStore(rows)
        return store

    def test_page_backfills_legacy_keys(self):
        """只有新键的已存行，page() 后带出 tmdbid。"""
        items, total = self._store_with(
            [{"uid": "u1", "media_source": "themoviedb", "media_id": "7"}]
        ).page(1, 20)
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["tmdbid"], "7")

    def test_page_keeps_existing_legacy_values(self):
        """已存行自带旧键时不被改写。"""
        items, _ = self._store_with([{"uid": "u1", "tmdbid": "8"}]).page(1, 20)
        self.assertEqual(items[0]["tmdbid"], "8")

    def test_page_on_empty_store(self):
        """空存储返回空页且总数为 0。"""
        items, total = self._store_with([]).page(1, 20)
        self.assertEqual((items, total), ([], 0))


if __name__ == "__main__":
    unittest.main()
