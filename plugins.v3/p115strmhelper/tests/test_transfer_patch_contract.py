"""
整理接管补丁的宿主契约回归测试。

真机失效复盘（2026-10-09）：宿主 V3 把「刮削批次收尾」方法从 name-mangled 私有名
``_TransferChain__finish_scrape_batch_task`` 改为单下划线公开名
``_finish_scrape_batch_task``，而补丁仍按老名字做补丁前校验 → ``PatchTargetError``
→ ``enable()`` 放弃打补丁（且服务层照旧打印"已启用"），表现为「批量整理不能用」。
同一处校验还按**类**视角取 ``jobview``，而宿主把它挂在**实例**上。

本文件把这些宿主契约固化为回归测试：

* 候选名解析：优先 V3 公开名、兼容 V2 旧名、两个都没有时判失败；
* 目标校验：接受"实例持有 jobview"的真机形状，类视角要能回退到实例；
* enable/disable：真正替换与还原宿主方法，``is_enabled()`` 诚实反映结果（含失败态）；
* finally：打完补丁仍会调用单下划线的批次收尾方法，不因改名漏掉 pending 清理；
* durable 重试入口：可用时登记、不可用或抛错时降级返回 ``None`` 且不向外抛。

宿主对象优先复用已安装的 V3 桩（运行时 ``PYTHONPATH`` 指向 ``tests/_stub_host``）；
桩不可用时整体跳过，避免在无桩环境产生假失败。
"""

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

plugin_root = Path(__file__).resolve().parents[1]


def _detect_stub_host() -> bool:
    """
    判断当前 ``app`` 是否为本仓库的 V3 桩宿主。

    只为桩宿主跑目标形状相关的用例：真机宿主的私有名可能再次变化，拿它当断言
    基准会让测试随宿主漂移而假红。

    :return bool: 桩宿主可用时为 True
    """
    try:
        import app  # noqa: F401
    except Exception:  # noqa: BLE001 - 导入失败即视为无桩
        return False
    paths = getattr(sys.modules.get("app"), "__path__", None) or []
    return any("_stub_host" in Path(str(path)).as_posix() for path in paths)


_HAS_STUB = _detect_stub_host()


def _load_plugin_module(relative: str, package: str = "p115_contract_under_test"):
    """
    以合成包的形式加载插件源码模块。

    ``patch/transfer_chain.py`` 使用相对导入（``from ..utils...``），必须以包形式加载；
    直接把插件根目录塞进 ``sys.path`` 只能导入平级模块。这里不导入插件 ``__init__``
    （那会拉起第三方依赖），只给合成包挂上真实子目录的 ``__path__``。

    :param relative: 相对插件根目录的模块路径，如 ``"patch.transfer_chain"``
    :param package: 合成包名
    :return: 加载后的模块对象
    """
    if package not in sys.modules:
        root_pkg = types.ModuleType(package)
        root_pkg.__path__ = [str(plugin_root)]
        sys.modules[package] = root_pkg
    for name, sub in (("patch", plugin_root / "patch"), ("utils", plugin_root / "utils")):
        full = f"{package}.{name}"
        if full not in sys.modules:
            module = types.ModuleType(full)
            module.__path__ = [str(sub)]
            sys.modules[full] = module
    return importlib.import_module(f"{package}.{relative}")


transfer_chain_module = _load_plugin_module("patch.transfer_chain")
transfer_compat = _load_plugin_module("utils.transfer_compat")
patch_guard = _load_plugin_module("utils.patch_guard")

TransferChainPatcher = transfer_chain_module.TransferChainPatcher
resolve_scrape_batch_finish = transfer_compat.resolve_scrape_batch_finish
request_durable_transfer_retry = transfer_compat.request_durable_transfer_retry
PatchTargetError = patch_guard.PatchTargetError

if _HAS_STUB:
    importlib.import_module("app.schemas.types")
    from app.chain.transfer import TransferChain
else:  # pragma: no cover - 无桩环境只跑不依赖宿主的用例
    TransferChain = None

#: 导入期快照的桩宿主模块。``test_transfer_classify`` 会把 ``app.*`` 换成假模块，
#: 而插件源码在运行期按名字 import 宿主，被污染后 ``enable()`` 会打到假模块上，
#: 因此桩宿主相关用例执行前要把这份快照覆盖回 ``sys.modules``。
_PRISTINE_APP_MODULES = {
    name: module
    for name, module in sys.modules.items()
    if name == "app" or name.startswith("app.")
}


def _snapshot_app_modules() -> dict:
    """快照当前 ``sys.modules`` 里的 ``app*`` 切片。"""
    return {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }


def _apply_app_modules(target: dict) -> None:
    """把给定切片覆盖回 ``sys.modules``（只覆盖、不删除，避免影响其它测试的现场）。"""
    sys.modules.update(target)


class _StubHostIsolation:
    """隔离其它测试对 ``sys.modules['app.*']`` 的污染（顺序无关）。"""

    def setUp(self):  # noqa: D102 - 子类各自补充
        super().setUp()
        self._app_modules_before = _snapshot_app_modules()
        _apply_app_modules(_PRISTINE_APP_MODULES)
        self.chain_class = sys.modules["app.chain.transfer"].TransferChain
        self.assertIs(
            self.chain_class, TransferChain, "桩宿主类身份在被隔离的现场应保持不变"
        )

    def tearDown(self):
        _apply_app_modules(self._app_modules_before)
        super().tearDown()


class TestScrapeBatchFinishResolution(unittest.TestCase):
    """刮削批次收尾方法的候选名解析。"""

    def test_prefers_v3_public_name(self):
        """两个候选名都在时优先 V3 单下划线公开名。"""

        class _Both:
            def _finish_scrape_batch_task(self):
                return "v3"

            def _TransferChain__finish_scrape_batch_task(self):
                return "v2"

        resolved = resolve_scrape_batch_finish(_Both())
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved(), "v3")

    def test_accepts_v2_legacy_name_only(self):
        """只提供 V2 私有名时仍可解析（兼容旧宿主）。"""

        class _Legacy:
            def _TransferChain__finish_scrape_batch_task(self):
                return "v2"

        resolved = resolve_scrape_batch_finish(_Legacy())
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved(), "v2")

    def test_returns_none_when_missing(self):
        """两个候选名都不存在时返回 None。"""

        class _NoneAtAll:
            pass

        self.assertIsNone(resolve_scrape_batch_finish(_NoneAtAll()))

    def test_non_callable_candidate_is_ignored(self):
        """候选属性存在但不可调用时视为缺失（防止把数据成员当方法）。"""

        class _DataOnly:
            _finish_scrape_batch_task = None

        self.assertIsNone(resolve_scrape_batch_finish(_DataOnly()))


class TestDurableRetryEntry(unittest.TestCase):
    """V3 durable 重试入口的适配与降级。"""

    def test_registers_when_available(self):
        """宿主提供入口时按位置参数+关键字登记。"""
        chain = MagicMock()
        chain._request_durable_transfer_retry.return_value = (True, "ok")
        history = MagicMock()
        history.id = 7

        result = request_durable_transfer_retry(
            chain, history, requested_by="unit_test"
        )

        self.assertEqual(result, (True, "ok"))
        chain._request_durable_transfer_retry.assert_called_once_with(
            history, requested_by="unit_test"
        )

    def test_degrades_when_entry_missing(self):
        """宿主没有该入口时返回 None，不抛异常（V2 宿主或旧版本）。"""

        class _NoEntry:
            pass

        self.assertIsNone(
            request_durable_transfer_retry(_NoEntry(), MagicMock(), requested_by="t")
        )

    def test_degrades_when_entry_raises(self):
        """入口抛异常时吞掉并返回 None，不影响整理主流程。"""
        chain = MagicMock()
        chain._request_durable_transfer_retry.side_effect = RuntimeError("boom")

        self.assertIsNone(
            request_durable_transfer_retry(chain, MagicMock(), requested_by="t")
        )


class TestNoLegacyRetryEntryUsage(unittest.TestCase):
    """调用点不得再依赖 V2 专属的 ``retry_scheduler``（V3 宿主不提供该成员）。"""

    _FILES = (
        "patch/transfer_chain.py",
        "helper/transfer/handler.py",
        "helper/transfer/handler_linked_batch.py",
        "helper/transfer/linked_subtitle_audio.py",
    )

    def test_transfer_modules_do_not_use_retry_scheduler(self):
        for relative in self._FILES:
            text = (plugin_root / relative).read_text(encoding="utf-8")
            self.assertNotIn(
                "retry_scheduler",
                text,
                f"{relative} 仍在调用 V2 专属 retry_scheduler（V3 宿主无此成员）",
            )


class TestServiceEnableHonesty(unittest.TestCase):
    """服务层不得在补丁未生效时打印"已启用"（2026-10-09 假成功的另一半原因）。"""

    def test_service_reports_success_only_when_patch_is_enabled(self):
        """``_init_transfer_enhancement`` 必须用 ``is_enabled()`` 决定是否报"已启用"。"""
        text = (plugin_root / "service/__init__.py").read_text(encoding="utf-8")
        self.assertIn(
            "if TransferChainPatcher.is_enabled():",
            text,
            "服务层未按补丁真实状态分支，补丁启用失败时会打印假成功",
        )


@unittest.skipUnless(_HAS_STUB, "需要 V3 桩宿主（PYTHONPATH 指向 tests/_stub_host）")
class TestPatchTargetVerification(_StubHostIsolation, unittest.TestCase):
    """补丁前目标校验必须接受真机形状、拒绝真缺失。"""

    def test_accepts_class_view_when_jobview_is_instance_attribute(self):
        """真机形状：jobview 只在实例上，传类进去也要能校验通过。"""
        chain = self.chain_class()  # 宿主已创建实例
        self.assertFalse(hasattr(self.chain_class, "jobview"))
        self.assertIsNotNone(getattr(chain, "jobview", None))

        TransferChainPatcher._verify_targets(self.chain_class)

    def test_accepts_instance_view(self):
        """实例视角同样通过（补丁运行时拿到的就是实例）。"""
        TransferChainPatcher._verify_targets(self.chain_class())

    def test_skips_jobview_check_when_instance_not_created(self):
        """宿主链实例尚未创建时跳过 jobview 校验，不得把"探不到"判成失败。

        补丁不主动构造宿主单例：构造失败会被宿主 metaclass 缓存成半成品实例。
        """

        class _NeverBuilt(self.chain_class):
            """从未实例化过（等价于宿主启动早期）的形态。"""

        TransferChainPatcher._verify_targets(_NeverBuilt)

    def test_rejects_when_scrape_finish_missing(self):
        """两个候选名都缺失（宿主改名/移除）时必须报错，而不是静默降级。"""

        class _NoScrapeFinish(self.chain_class):
            _finish_scrape_batch_task = None
            _TransferChain__finish_scrape_batch_task = None

        with self.assertRaises(PatchTargetError) as ctx:
            TransferChainPatcher._verify_targets(_NoScrapeFinish)
        self.assertIn("刮削批次收尾", str(ctx.exception))

    def test_rejects_when_jobview_missing(self):
        """宿主实例存在但彻底没有 jobview 时必须报错。"""

        class _NoJobView(self.chain_class):
            def __init__(self, *args, **kwargs):  # noqa: D107 - 故意不设置 jobview
                pass

        _NoJobView()  # 让该实例存在于宿主单例缓存
        with self.assertRaises(PatchTargetError) as ctx:
            TransferChainPatcher._verify_targets(_NoJobView)
        self.assertIn("jobview", str(ctx.exception))


@unittest.skipUnless(_HAS_STUB, "需要 V3 桩宿主（PYTHONPATH 指向 tests/_stub_host）")
class TestRuntimeJobViewCheck(_StubHostIsolation, unittest.TestCase):
    """运行期首单复核：接口不完整只告警，不阻断整理主流程。"""

    def setUp(self):
        super().setUp()
        self._original_verified = TransferChainPatcher._jobview_verified

    def tearDown(self):
        TransferChainPatcher._jobview_verified = self._original_verified
        super().tearDown()

    def test_incomplete_jobview_only_warns(self):
        """缺方法的 jobview 不得抛异常（JobViewAdapter 逐方法降级）。"""
        TransferChainPatcher._jobview_verified = False
        chain_stub = types.SimpleNamespace(jobview=types.SimpleNamespace())

        TransferChainPatcher._verify_jobview_once(chain_stub)

        self.assertTrue(TransferChainPatcher._jobview_verified)

    def test_complete_jobview_passes(self):
        """完整 jobview 复核通过并置位，避免反复复核。"""
        TransferChainPatcher._jobview_verified = False
        chain_stub = types.SimpleNamespace(jobview=self.chain_class().jobview)

        TransferChainPatcher._verify_jobview_once(chain_stub)

        self.assertTrue(TransferChainPatcher._jobview_verified)

    def test_verify_runs_only_once(self):
        """已复核过则直接短路（第二个对象不再被检查）。"""
        TransferChainPatcher._jobview_verified = True
        chain_stub = types.SimpleNamespace(jobview=None)

        TransferChainPatcher._verify_jobview_once(chain_stub)

        self.assertTrue(TransferChainPatcher._jobview_verified)


@unittest.skipUnless(_HAS_STUB, "需要 V3 桩宿主（PYTHONPATH 指向 tests/_stub_host）")
class TestEnableDisableCycle(_StubHostIsolation, unittest.TestCase):
    """打补丁/还原的对称性与启用状态诚实性。"""

    def setUp(self):
        super().setUp()
        TransferChainPatcher.disable()
        self.original = self.chain_class.__dict__["_TransferChain__handle_transfer"]

    def tearDown(self):
        TransferChainPatcher.disable()
        super().tearDown()

    def test_enable_replaces_then_disable_restores(self):
        TransferChainPatcher.enable(
            task_manager=object(), handler=object(), storage_module="115网盘Plus"
        )
        self.assertTrue(TransferChainPatcher.is_enabled())
        self.assertIsNot(
            self.chain_class.__dict__["_TransferChain__handle_transfer"], self.original
        )

        TransferChainPatcher.disable()
        self.assertFalse(TransferChainPatcher.is_enabled())
        self.assertIs(
            self.chain_class.__dict__["_TransferChain__handle_transfer"], self.original
        )

    def test_enable_keeps_disabled_when_target_check_fails(self):
        """目标校验失败时必须保持未启用，且不动宿主方法（服务层据此不再打印假成功）。"""

        def _boom(cls, transfer_chain):
            raise PatchTargetError("unit-test: 目标缺失")

        original_verify = TransferChainPatcher._verify_targets
        TransferChainPatcher._verify_targets = classmethod(_boom)
        try:
            TransferChainPatcher.enable(
                task_manager=object(), handler=object(), storage_module="115网盘Plus"
            )
        finally:
            TransferChainPatcher._verify_targets = original_verify

        self.assertFalse(TransferChainPatcher.is_enabled())
        self.assertIs(
            self.chain_class.__dict__["_TransferChain__handle_transfer"], self.original
        )


class _FakeMeta:
    """最小 meta 替身。"""

    begin_season = 1
    season_seq = None
    year = None


class _FakeFileItem:
    """最小文件项替身。"""

    def __init__(self):
        self.name = "demo.mkv"
        self.storage = "local"
        self.path = "/downloads/demo.mkv"
        self.type = "file"
        self.extension = "mkv"


class _FakeMediaInfo:
    """最小媒体信息替身（电影，避免走集数据分支）。"""

    media_source = "tmdb"
    media_id = "1"
    tmdb_id = 1
    title = "Demo"
    season = None
    episode_group = None
    category = None


class _FakeTask:
    """够用即止的整理任务替身：非 115→115 场景，应回退到原生 transfer 部分。"""

    def __init__(self):
        from app.schemas.types import MediaType

        self.fileitem = _FakeFileItem()
        self.meta = _FakeMeta()
        self.mediainfo = _FakeMediaInfo()
        self.mediainfo.type = MediaType.MOVIE
        self.target_directory = object()
        self.target_storage = "local"
        self.episodes_info = []
        self.transfer_type = "move"
        self.scrape = False
        self.manual = False
        self.background = True
        self.username = None
        self.downloader = None
        self.download_hash = None
        self.preview = False
        self.target_path = None


@unittest.skipUnless(_HAS_STUB, "需要 V3 桩宿主（PYTHONPATH 指向 tests/_stub_host）")
class TestPatchedFlowFinally(_StubHostIsolation, unittest.TestCase):
    """补丁版的 finally 分支必须调用宿主真实的刮削批次收尾方法。"""

    def setUp(self):
        super().setUp()
        TransferChainPatcher.disable()
        self._original_part = TransferChainPatcher._call_original_transfer_part
        TransferChainPatcher._call_original_transfer_part = classmethod(
            lambda cls, chain_self, task, callback: (False, "unit-test: fallback")
        )
        self.chain_class.last_finished_scrape_task = None

    def tearDown(self):
        TransferChainPatcher._call_original_transfer_part = self._original_part
        TransferChainPatcher.disable()
        super().tearDown()

    def test_finally_calls_host_scrape_finish(self):
        TransferChainPatcher.enable(
            task_manager=object(), handler=object(), storage_module="115网盘Plus"
        )
        task = _FakeTask()

        result = self.chain_class()._TransferChain__handle_transfer(task, None)

        self.assertEqual(result, (False, "unit-test: fallback"))
        # 宿主签名是单下划线的 _finish_scrape_batch_task(task)
        self.assertIs(self.chain_class.last_finished_scrape_task, task)


if __name__ == "__main__":
    unittest.main()
