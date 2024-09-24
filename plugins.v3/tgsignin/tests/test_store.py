"""
状态持久化测试：运行记录、每个 bot 的历史上限、最近结果排序、登录信息。
"""

import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401  触发宿主桩与插件路径注入

from tgsignin.core.store import (
    MAX_HISTORY_PER_BOT,
    load_state,
    recent_results,
    record_login,
    record_run,
    save_state,
    state_path,
)


def _result(bot: str, ok: bool, time_text: str) -> dict:
    """
    构造一条签到结果。

    :param bot: bot 名
    :param ok: 是否成功
    :param time_text: 时间文本
    :return dict: 结果字典
    """
    return {
        "account": "acc1",
        "bot": bot,
        "method": "点按钮「签到」",
        "ok": ok,
        "reply": "🎉 签到成功" if ok else "",
        "error": "" if ok else "无回复",
        "time": time_text,
    }


class TestStore(unittest.TestCase):
    """状态文件读写与历史维护。"""

    def setUp(self) -> None:
        """准备临时数据目录。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_missing_state_returns_skeleton(self) -> None:
        """状态文件不存在时返回骨架而不是抛错。"""
        state = load_state(self.data_dir)
        self.assertEqual(state["last_summary"], "")
        self.assertEqual(state["history"], [])

    def test_corrupt_state_is_tolerated(self) -> None:
        """状态文件损坏时回落到骨架。"""
        state_path(self.data_dir).write_text("{ not json", encoding="utf-8")
        self.assertEqual(load_state(self.data_dir)["last_run_at"], "")

    def test_record_run_sets_summary_and_source(self) -> None:
        """运行记录写入来源、摘要与结果。"""
        results = [_result("@a", True, "2026-10-05 09:00:01")]
        state = record_run(self.data_dir, results, "定时", "1/1 成功")
        self.assertEqual(state["last_source"], "定时")
        self.assertEqual(state["last_summary"], "1/1 成功")
        self.assertEqual(state["last_run_at"], "2026-10-05 09:00:01")
        self.assertEqual(len(state["results"]), 1)

    def test_history_is_capped_per_bot(self) -> None:
        """同一个 bot 的历史条目被限制在 MAX_HISTORY_PER_BOT 条。"""
        for index in range(MAX_HISTORY_PER_BOT + 5):
            record_run(
                self.data_dir,
                [_result("@a", True, f"2026-10-05 09:00:{index:02d}")],
                "定时",
                "1/1 成功",
            )
        state = load_state(self.data_dir)
        same_bot = [item for item in state["history"] if item["bot"] == "@a"]
        self.assertEqual(len(same_bot), MAX_HISTORY_PER_BOT)

    def test_recent_results_newest_first(self) -> None:
        """最近结果按时间倒序返回。"""
        record_run(self.data_dir, [_result("@a", True, "t1")], "手动", "1/1 成功")
        record_run(self.data_dir, [_result("@b", False, "t2")], "手动", "0/1 成功")
        recent = recent_results(load_state(self.data_dir), 10)
        self.assertEqual([item["time"] for item in recent], ["t2", "t1"])

    def test_record_login_marks_logged_in(self) -> None:
        """登录信息写入后带 logged_in 标记。"""
        record_login(
            self.data_dir,
            "acc1",
            {"name": "测试用户", "username": "testuser", "user_id": "100000001"},
        )
        state = load_state(self.data_dir)
        self.assertTrue(state["accounts"]["acc1"]["logged_in"])
        self.assertEqual(state["accounts"]["acc1"]["username"], "testuser")

    def test_save_and_load_roundtrip(self) -> None:
        """写入的状态能被读回来。"""
        save_state(self.data_dir, {"last_run_at": "x", "history": [], "accounts": {}})
        self.assertEqual(load_state(self.data_dir)["last_run_at"], "x")


if __name__ == "__main__":
    unittest.main()
