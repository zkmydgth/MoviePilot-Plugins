"""
插件页保存配置的宿主存储回归测试（2026-10-10）。

背景
----
V3 宿主把 ``_PluginBase.get_config()`` 从 V2 的 ``systemconfig.get("plugin.<id>")``
（**插件自己写的那份**）改成读宿主实例存储 ``plugininstance.config_data``。插件页保存
（``POST plugin/<id>/save_config``）此前只写插件自有的 systemconfig，随后又执行
``init_plugin(config=self.get_config())`` —— 读到的是宿主里的**旧配置**，整份回灌，
本次修改被静默覆盖，而接口仍返回「保存成功」。

用户可见现象：在插件页「分享同步」里改任意字段（例如「最长审核等待时间」6→7、
「MP-媒体库 目录转换」填 1），点「保存配置」，重开面板值变回原样。

本文件只加载 ``__init__.py`` 里的 ``_save_config_api`` 一个函数（AST 切片，不执行
插件入口），用替身复刻「宿主存储 / 插件自有存储 / 内存配置」三者关系，把
「保存必须落到宿主存储，且不得被宿主旧值回灌覆盖」钉成断言。
"""

import ast
import copy
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "__init__.py"


def _load_save_handler(configer, i18n, sentry_manager):
    """
    从 ``__init__.py`` 中单独切出并编译 ``_save_config_api``

    :param configer (object): 插件配置管理器替身
    :param i18n (object): 语言包替身
    :param sentry_manager (object): sentry 替身
    :return function: 可调用的保存处理函数
    """
    module = ast.parse(SOURCE.read_text(encoding="utf-8"))
    target = None
    for node in ast.walk(module):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_save_config_api"
        ):
            target = node
            break
    if target is None:
        raise AssertionError("未找到 _save_config_api")
    namespace = {
        "Request": object,
        "Dict": dict,
        "Any": object,
        "configer": configer,
        "i18n": i18n,
        "sentry_manager": sentry_manager,
    }
    exec(
        compile(ast.Module(body=[target], type_ignores=[]), str(SOURCE), "exec"),
        namespace,
    )
    return namespace["_save_config_api"]


class _FakeRequest:
    """最小请求替身：只提供 ``.json()``"""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        """
        返回请求体副本

        :return dict: 请求体
        """
        return copy.deepcopy(self._payload)


class _FakeI18n:
    """i18n 替身：只记录调用"""

    def __init__(self):
        self.loaded = 0

    def load_translations(self):
        """记录一次语言包重载"""
        self.loaded += 1


class _FakeSentry:
    """sentry 替身：只记录调用"""

    def __init__(self):
        self.reloaded = 0

    def reload_config(self):
        """记录一次配置重载"""
        self.reloaded += 1


class _FakeConfiger:
    """
    复刻 ``configer`` 的对外语义：整份合并 + 可 dump

    真实实现是 pydantic 模型（``model_dump(mode="json")`` 与属性读写），这里只保留
    本用例用到的行为，避免拉起插件重依赖。
    """

    def __init__(self, initial, accept=True):
        self.data = copy.deepcopy(initial)
        self.accept = accept
        self.writes = 0
        self.plugin_store = None

    def update_plugin_config(self):
        """
        复刻「写插件自有存储」这一步（真机为 ``systemconfig`` 的 ``plugin.<id>`` 键）

        :return bool: 总是成功
        """
        self.plugin_store = copy.deepcopy(self.data)
        return True

    def update_config(self, updates):
        """
        合并一版配置

        :param updates (dict): 待合并的配置
        :return bool: 是否通过校验
        """
        if not self.accept:
            return False
        self.writes += 1
        merged = copy.deepcopy(self.data)
        merged.update(copy.deepcopy(updates or {}))
        self.data = merged
        return True

    def model_dump(self, mode="json"):
        """
        导出当前配置

        :param mode (str): 序列化模式
        :return dict: 配置副本
        """
        return copy.deepcopy(self.data)


class _FakePlugin:
    """
    复刻 V3 宿主契约下的插件对象

    * ``get_config()`` 读的是**宿主存储**（真机为 ``plugininstance.config_data``）；
    * ``update_config()`` 写宿主存储；
    * ``init_plugin(config)`` 与真机一样会把这版配置再喂给 ``configer``。
    """

    def __init__(self, configer, host_config):
        self.configer = configer
        self.host_store = copy.deepcopy(host_config)
        self.host_writes = 0
        self.init_configs = []

    def get_config(self, plugin_id=None):
        """
        读取宿主存储

        :param plugin_id (str|None): 实例 ID
        :return dict: 宿主存储副本
        """
        return copy.deepcopy(self.host_store)

    def update_config(self, config, plugin_id=None):
        """
        写入宿主存储

        :param config (dict): 配置
        :param plugin_id (str|None): 实例 ID
        :return bool: 总是成功
        """
        self.host_writes += 1
        self.host_store = copy.deepcopy(config)
        return True

    def init_plugin(self, config=None):
        """
        重新初始化插件

        :param config (dict|None): 传入的配置
        """
        self.init_configs.append(copy.deepcopy(config))
        if config:
            self.configer.update_config(config)


class TestSaveConfigHostStore(unittest.IsolatedAsyncioTestCase):
    """插件页保存必须落到宿主存储，且不被宿主旧值覆盖"""

    HOST_STORE = {
        "enabled": True,
        "share_audit_max_wait_seconds": 21600,
        "share_strm_mp_mediaserver_paths": None,
    }

    def _build(self, payload, accept=True):
        """
        组装一次保存调用所需的替身

        :param payload (dict): 前端提交的配置
        :param accept (bool): configer 是否接受这版配置
        :return tuple: (处理函数, configer, plugin, i18n, sentry, request)
        """
        configer = _FakeConfiger(self.HOST_STORE, accept=accept)
        plugin = _FakePlugin(configer, self.HOST_STORE)
        i18n = _FakeI18n()
        sentry = _FakeSentry()
        handler = _load_save_handler(configer, i18n, sentry)
        return handler, configer, plugin, i18n, sentry, _FakeRequest(payload)

    async def test_modified_value_reaches_host_store_without_rollback(self):
        """改动后的值必须同时留在内存与宿主存储，且重新初始化不得回灌旧值"""
        payload = dict(self.HOST_STORE)
        payload["share_audit_max_wait_seconds"] = 25200
        payload["share_strm_mp_mediaserver_paths"] = "1"
        handler, configer, plugin, i18n, sentry, request = self._build(payload)

        result = await handler(plugin, request)
        self.assertEqual(result.get("code"), 0, result)
        self.assertEqual(
            configer.data["share_audit_max_wait_seconds"],
            25200,
            "保存后内存配置被回灌覆盖",
        )
        self.assertEqual(
            configer.data["share_strm_mp_mediaserver_paths"],
            "1",
            "保存后内存配置被回灌覆盖",
        )
        self.assertEqual(
            plugin.get_config()["share_audit_max_wait_seconds"],
            25200,
            "宿主存储未同步本次保存（重启或重开面板会丢）",
        )
        self.assertEqual(
            plugin.get_config()["share_strm_mp_mediaserver_paths"],
            "1",
            "宿主存储未同步本次保存（重启或重开面板会丢）",
        )
        self.assertGreaterEqual(plugin.host_writes, 1, "保存未写入宿主存储")
        self.assertEqual(
            configer.plugin_store["share_audit_max_wait_seconds"],
            25200,
            "插件自有存储被宿主旧值覆盖",
        )
        self.assertEqual(
            configer.plugin_store["share_strm_mp_mediaserver_paths"],
            "1",
            "插件自有存储被宿主旧值覆盖",
        )
        if plugin.init_configs and plugin.init_configs[-1] is not None:
            self.assertEqual(
                plugin.init_configs[-1]["share_audit_max_wait_seconds"],
                25200,
                "重新初始化被喂了宿主旧配置",
            )
        self.assertEqual(i18n.loaded, 1)
        self.assertEqual(sentry.reloaded, 1)

    async def test_rejected_payload_keeps_stores_untouched(self):
        """校验不通过时返回失败，且不动任何存储"""
        handler, configer, plugin, i18n, _, request = self._build(
            {"share_audit_max_wait_seconds": "not-a-number"}, accept=False
        )

        result = await handler(plugin, request)
        self.assertEqual(result.get("code"), 1)
        self.assertEqual(configer.data, self.HOST_STORE)
        self.assertEqual(plugin.get_config(), self.HOST_STORE)
        self.assertEqual(plugin.host_writes, 0)
        self.assertEqual(plugin.init_configs, [])
        self.assertEqual(i18n.loaded, 0)


if __name__ == "__main__":
    unittest.main()
