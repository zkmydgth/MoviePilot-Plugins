# -*- coding: utf-8 -*-
"""
豆瓣开分通知的核心回归测试。

覆盖五类风险：
  A. 候选匹配：唯一命中才自动绑定，多候选必须交人工/AI
  B. AI 结果校验：候选外 ID、低置信度、越界季号一律丢弃
  C. 开分判定：人数不足不通知，达到门槛只通知一次
  D. 计划与窗口：随机时间落在配置窗口内，跨零点窗口判定正确
  E. 人工干预：手动绑定、忽略/恢复生效
"""

import unittest
from datetime import datetime

import tests  # noqa: F401  触发宿主桩路径注入

from doubanscorewatch import DoubanScoreWatch
from matcher import parse_ai_bindings, select_candidates


class FakeClient:
    """豆瓣客户端替身：按预设返回详情。"""

    def __init__(self, detail=None):
        """记录预设详情。"""
        self.detail_data = detail or {}
        self.calls = []

    def detail(self, douban_id, media_type):
        """返回预设详情。"""
        self.calls.append((douban_id, media_type))
        return self.detail_data


class DoubanScoreWatchTestCase(unittest.TestCase):
    """插件核心行为夹具。"""

    def setUp(self) -> None:
        """为每个用例准备全新插件实例。"""
        self.plugin = DoubanScoreWatch()
        self.plugin.init_plugin(
            {
                "enabled": True,
                "notify": True,
                "vote_threshold": 50,
                "window_start": "21:00",
                "window_end": "23:00",
                "rounds_per_day": 2,
                "keep_tracking": False,
                "change_delta": 0.5,
            }
        )

    def _entry(self, **overrides):
        """构造一条追踪记录。"""
        entry = {
            "key": "themoviedb:12345",
            "media_source": "themoviedb",
            "media_id": "12345",
            "type": "电视剧",
            "title": "凡人修仙传",
            "year": "2020",
            "seasons": [1],
            "status": "pending",
            "bindings": [],
            "candidates": [],
            "ai_suggestions": [],
        }
        entry.update(overrides)
        return entry

    def _binding(self, **overrides):
        """构造一条豆瓣绑定。"""
        binding = {
            "douban_id": "34925294",
            "title": "凡人修仙传：风起天南",
            "year": "2020",
            "seasons": [1],
            "match_source": "search",
            "rating": 0,
            "votes": 0,
            "first_seen": "",
            "notified": False,
            "history": [],
        }
        binding.update(overrides)
        return binding

    # A. 候选匹配
    def test_select_unique_candidate(self):
        """标题与年份唯一命中时自动绑定。"""
        result = select_candidates(
            [{"douban_id": "1", "title": "凡人修仙传", "year": "2020"}],
            "凡人修仙传",
            "2020",
        )
        self.assertIsNotNone(result["auto"])
        self.assertEqual(result["auto"]["douban_id"], "1")

    def test_select_ambiguous_candidates(self):
        """同一年份窗口内多个条目时不自动绑定。"""
        result = select_candidates(
            [
                {"douban_id": "1", "title": "凡人修仙传：风起天南", "year": "2020"},
                {"douban_id": "2", "title": "凡人修仙传：魔道争锋", "year": "2021"},
            ],
            "凡人修仙传",
            "2020",
        )
        self.assertIsNone(result["auto"])
        self.assertEqual(len(result["candidates"]), 2)

    def test_select_multi_season_title_keeps_year_window(self):
        """豆瓣按季拆分成多条时，候选应保留当年条目而不是只留同名条目。"""
        result = select_candidates(
            [
                {"douban_id": "35861087", "title": "凡人修仙传", "year": "2025"},
                {"douban_id": "34925294", "title": "凡人修仙传：风起天南", "year": "2020"},
                {"douban_id": "35640039", "title": "凡人修仙传：魔道争锋", "year": "2021"},
            ],
            "凡人修仙传",
            "2020",
        )
        self.assertIsNone(result["auto"])
        ids = [item["douban_id"] for item in result["candidates"]]
        self.assertIn("34925294", ids)
        self.assertNotIn("35861087", ids)

    def test_select_rejects_year_mismatch(self):
        """标题唯一但年份相差过大时不自动绑定。"""
        result = select_candidates(
            [{"douban_id": "1", "title": "凡人修仙传", "year": "1999"}],
            "凡人修仙传",
            "2020",
        )
        self.assertIsNone(result["auto"])

    # A3. 季对齐
    def test_season_marker_binds_second_season(self):
        """订阅 S2 时应命中豆瓣的「第二季」条目，而不是无季标记的第一季。"""
        result = select_candidates(
            [
                {"douban_id": "26310143", "title": "信号", "year": "2016"},
                {"douban_id": "34607563", "title": "信号 第二季", "year": "2026"},
                {"douban_id": "35460201", "title": "信号", "year": "2023"},
            ],
            "信号",
            "2016",
            2,
        )
        self.assertIsNotNone(result["auto"])
        self.assertEqual(result["auto"]["douban_id"], "34607563")

    def test_season_marker_matches_later_season_year(self):
        """订阅 S4 时不应被首播年窗口挡掉第四季条目。"""
        result = select_candidates(
            [
                {"douban_id": "36228081", "title": "现在就出发 第一季", "year": "2023"},
                {"douban_id": "36540851", "title": "现在就出发 第二季", "year": "2024"},
                {"douban_id": "37816990", "title": "现在就出发 第四季", "year": "2026"},
            ],
            "现在就出发",
            "2023",
            4,
        )
        self.assertIsNotNone(result["auto"])
        self.assertEqual(result["auto"]["douban_id"], "37816990")

    def test_missing_season_reports_reason(self):
        """豆瓣没有对应季时不乱绑，并说明原因。"""
        result = select_candidates(
            [{"douban_id": "1", "title": "某剧 第一季", "year": "2020"}],
            "某剧",
            "2020",
            2,
        )
        self.assertIsNone(result["auto"])
        self.assertEqual(result["candidates"], [])
        self.assertIn("没有第2季", result["reason"])

    def test_migration_drops_wrong_season_binding(self):
        """迁移会解绑单季订阅里绑错的其它季。"""
        entry = self._entry(
            seasons=[2],
            status="scored",
            bindings=[self._binding(douban_id="26310143", title="信号", year="2016")],
        )
        self.plugin._state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        self.plugin._migrate_season_bindings()
        self.assertEqual(entry["bindings"], [])
        self.assertEqual(entry["status"], "pending")

    # B. AI 结果校验
    def test_parse_ai_bindings_filters_invalid(self):
        """候选外 ID、低置信度与越界季号都被丢弃。"""
        candidates = [{"douban_id": "34925294", "title": "风起天南", "year": "2020"}]
        text = (
            '{"bindings":['
            '{"douban_id":"34925294","seasons":[1,9],"confidence":0.9,"reason":"标题一致"},'
            '{"douban_id":"999","seasons":[1],"confidence":0.99,"reason":"编造"},'
            '{"douban_id":"34925294","seasons":[1],"confidence":0.2}'
            "]}"
        )
        result = parse_ai_bindings(text, candidates, [1])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["douban_id"], "34925294")
        self.assertEqual(result[0]["seasons"], [1])

    def test_parse_ai_bindings_uncertain(self):
        """模型表示不确定时不产生绑定。"""
        result = parse_ai_bindings(
            '{"bindings":[],"uncertain":true}',
            [{"douban_id": "1", "title": "x", "year": "2020"}],
            [1],
        )
        self.assertEqual(result, [])

    # C. 开分判定
    def test_below_threshold_keeps_waiting(self):
        """人数不足门槛时记录分数但不通知。"""
        entry = self._entry(bindings=[self._binding(rating=7.9, votes=10)])
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        client = FakeClient({"title": "风起天南", "year": "2020", "rating": 7.9, "votes": 10})
        self.plugin._check_entry(entry, client, state)
        self.assertEqual(state["pending_notices"], [])
        self.assertEqual(entry["bindings"][0]["first_seen"], "")

    def test_open_score_queues_once(self):
        """达到门槛时入队一次，重复检查不重复入队。"""
        entry = self._entry(bindings=[self._binding()])
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        client = FakeClient({"title": "风起天南", "year": "2020", "rating": 7.9, "votes": 67042})
        self.plugin._check_entry(entry, client, state)
        self.plugin._check_entry(entry, client, state)
        self.assertEqual(len(state["pending_notices"]), 1)
        self.assertIn("7.9", state["pending_notices"][0]["text"])
        self.assertEqual(entry["bindings"][0]["first_seen"], state["pending_notices"][0]["time"][:10])

    def test_pre_scored_binding_skips_notice(self):
        """绑定时已有有效评分的条目不补发开分通知。"""
        entry = self._entry(
            bindings=[self._binding(initial_rating=7.9, initial_votes=67042)]
        )
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        client = FakeClient({"title": "风起天南", "year": "2020", "rating": 7.9, "votes": 67042})
        self.plugin._check_entry(entry, client, state)
        self.assertEqual(state["pending_notices"], [])
        self.assertTrue(entry["bindings"][0].get("pre_scored"))

    def test_flush_notices_sends_message(self):
        """到点后把队列里的通知交给宿主。"""
        state = {"entries": {}, "pending_notices": [{"title": "t", "text": "body"}]}
        self.plugin._flush_notices(state)
        self.assertEqual(len(self.plugin.sent_messages), 1)
        self.assertEqual(state["pending_notices"], [])

    # D. 计划与窗口
    def test_plan_times_inside_window(self):
        """生成的检查时间必须落在窗口内。"""
        times = self.plugin._build_plan(datetime.now().date())
        self.assertEqual(len(times), 2)
        for item in times:
            hour, minute = [int(part) for part in item.split(":", 1)]
            self.assertGreaterEqual(hour * 60 + minute, 21 * 60)
            self.assertLessEqual(hour * 60 + minute, 23 * 60)

    def test_clock_accepts_seconds(self):
        """时间选择器可能回传 HH:MM:SS，解析需兼容。"""
        self.assertEqual(self.plugin._to_clock("21:05:00", ""), "21:05")
        self.assertEqual(self.plugin._clock_minutes("21:05:30", 0), 21 * 60 + 5)

    def test_window_cross_midnight(self):
        """跨零点窗口判定正确。"""
        self.assertTrue(self.plugin._in_window(datetime(2026, 10, 23, 23, 30), "22:00", "02:00"))
        self.assertTrue(self.plugin._in_window(datetime(2026, 10, 24, 1, 30), "22:00", "02:00"))
        self.assertFalse(self.plugin._in_window(datetime(2026, 10, 23, 12, 0), "22:00", "02:00"))

    # E. 人工干预
    def test_manual_binding_applied(self):
        """配置中的手动绑定写入追踪记录。"""
        entry = self._entry()
        self.plugin._state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        self.plugin._manual_bindings = "themoviedb:12345=34925294"
        self.plugin._apply_manual_bindings()
        self.assertEqual(entry["bindings"][0]["douban_id"], "34925294")
        self.assertEqual(entry["bindings"][0]["match_source"], "manual")

    def test_ignore_and_restore(self):
        """忽略与恢复改变追踪状态。"""
        entry = self._entry()
        self.plugin._state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        self.plugin.api_ignore(entry["key"])
        self.assertEqual(entry["status"], "ignored")
        self.plugin.api_restore(entry["key"])
        self.assertEqual(entry["status"], "pending")

    def test_unknown_key_rejected(self):
        """未知媒体键的写操作被拒绝。"""
        self.plugin._state = {"entries": {}, "pending_notices": []}
        result = self.plugin.api_bind("themoviedb:404", "123")
        self.assertFalse(result["success"])

    # B2. AI 自动接受
    def test_ai_auto_accept_binds_candidate(self):
        """开启 AI 自动接受时，按候选映射绑定，不再因缺少 candidate 键报错。"""
        self.plugin._ai_enabled = True
        self.plugin._ai_auto_accept = True
        candidates = [
            {"douban_id": "7", "title": "凡人修仙传：风起天南", "year": "2020", "rating": 0, "votes": 0},
            {"douban_id": "8", "title": "凡人修仙传：魔道争锋", "year": "2021", "rating": 0, "votes": 0},
        ]

        class FakeSearchClient:
            """只提供搜索能力的客户端替身。"""

            def search(self, keyword, media_type):
                """返回两个候选以触发 AI 路径。"""
                del keyword, media_type
                return candidates

        self.plugin._invoke_llm = lambda messages: (
            '{"bindings":[{"douban_id":"7","seasons":[1],"confidence":0.9,"reason":"年份吻合"}]}'
        )
        entry = self._entry()
        matched = self.plugin._match_entry(entry, FakeSearchClient(), [5])
        self.assertTrue(matched)
        self.assertEqual(entry["bindings"][0]["douban_id"], "7")

    # A2. 空结果与手动轮次
    def test_empty_search_marks_unmatched(self):
        """豆瓣搜不到条目时标记未匹配，而不是一直显示待开分。"""

        class EmptyClient:
            """搜索恒为空的客户端替身。"""

            def search(self, keyword, media_type):
                """返回空列表。"""
                del keyword, media_type
                return []

        entry = self._entry()
        matched = self.plugin._match_entry(entry, EmptyClient(), [0])
        self.assertFalse(matched)
        self.assertEqual(entry["status"], "unmatched")
        self.assertEqual(entry["match_reason"], "豆瓣未搜到条目")

    def test_manual_round_does_not_consume_plan(self):
        """手动检查不占用当天计划轮次。"""
        import datetime as dt
        import doubanscorewatch as module

        class FakeFactory:
            """豆瓣客户端工厂替身。"""

            def __init__(self, cookie=""):
                """忽略 Cookie。"""
                del cookie

            def search(self, keyword, media_type):
                """返回空列表。"""
                del keyword, media_type
                return []

            def detail(self, douban_id, media_type):
                """返回空详情。"""
                del douban_id, media_type
                return None

        original = module.DoubanScoreClient
        module.DoubanScoreClient = FakeFactory
        self.plugin._sync_subscriptions = lambda entries: 0
        today = dt.date.today().isoformat()
        state = {
            "entries": {},
            "pending_notices": [],
            "plan": {"date": today, "times": ["21:10"], "done": 0},
        }
        try:
            self.plugin._run_round(state, scheduled=False)
        finally:
            module.DoubanScoreClient = original
        self.assertEqual(state["plan"]["done"], 0)

    # E2. 豆瓣客户端缓存绕行
    def test_client_bypasses_host_cache(self):
        """搜索与详情都要带不同的 _ts，避免读到宿主缓存里的空结果或旧分数。"""
        from doubanscorewatch.douban_api import DoubanScoreClient

        class FakeApi:
            """豆瓣接口替身，记录传入的 _ts。"""

            _urls = {"search": "/search/weixin", "tv_detail": "/tv/", "movie_detail": "/movie/"}

            def __init__(self):
                """初始化调用记录。"""
                self.calls = []

            def _DoubanApi__invoke_search(self, path, **kwargs):
                """模拟搜索与详情响应。"""
                self.calls.append((path, kwargs))
                if "q" in kwargs:
                    return {
                        "items": [
                            {
                                "type_name": "电视剧",
                                "target": {
                                    "id": 1,
                                    "title": "政盗鲁邦",
                                    "year": "2026",
                                    "rating": {"value": 8.0, "count": 120},
                                },
                            }
                        ]
                    }
                return {"title": "政盗鲁邦", "year": "2026", "rating": {"value": 8.0, "count": 120}}

        client = DoubanScoreClient()
        fake = FakeApi()
        client._api = lambda: fake
        candidates = client.search("政盗鲁邦", "电视剧")
        detail = client.detail("1", "电视剧")
        self.assertEqual(candidates[0]["douban_id"], "1")
        self.assertEqual(detail["rating"], 8.0)
        timestamps = [kwargs.get("_ts") for _, kwargs in fake.calls]
        self.assertTrue(all(timestamps))
        self.assertEqual(len(set(timestamps)), 2)

    # F. 页面渲染
    def test_page_lists_candidate_bind_buttons(self):
        """未匹配条目应在页面列出候选与绑定按钮。"""
        self.plugin._enabled = True
        self.plugin._state = {
            "entries": {
                "themoviedb:1": {
                    "key": "themoviedb:1",
                    "title": "政盗鲁邦",
                    "type": "电视剧",
                    "year": "2026",
                    "seasons": [1],
                    "status": "unmatched",
                    "bindings": [],
                    "ai_suggestions": [],
                    "candidates": [
                        {"douban_id": "9", "title": "政盗鲁邦", "year": "2026", "rating": 0, "votes": 0}
                    ],
                }
            },
            "pending_notices": [],
            "plan": {"date": "", "times": [], "done": 0},
        }
        page = self.plugin.get_page()
        rendered = str(page)
        self.assertIn("候选：政盗鲁邦", rendered)
        self.assertIn("/bind", rendered)

    # G. 整轮串联
    def test_round_end_to_end(self):
        """一轮检查应完成匹配、查分、入队通知三件事。"""
        import doubanscorewatch as module

        class FakeFactory:
            """豆瓣客户端工厂替身。"""

            def __init__(self, cookie=""):
                """忽略 Cookie。"""
                del cookie

            def search(self, keyword, media_type):
                """返回唯一候选。"""
                del keyword, media_type
                return [{"douban_id": "1", "title": "测试剧", "year": "2026", "rating": 0, "votes": 0}]

            def detail(self, douban_id, media_type):
                """返回已开分详情。"""
                del douban_id, media_type
                return {"title": "测试剧", "year": "2026", "rating": 8.1, "votes": 120}

        original = module.DoubanScoreClient
        module.DoubanScoreClient = FakeFactory
        self.plugin._sync_subscriptions = lambda entries: entries.setdefault(
            "themoviedb:1",
            {
                "key": "themoviedb:1",
                "media_source": "themoviedb",
                "media_id": "1",
                "type": "电视剧",
                "title": "测试剧",
                "year": "2026",
                "seasons": [1],
                "status": "pending",
                "bindings": [],
                "candidates": [],
                "ai_suggestions": [],
            },
        ) and 1
        state = {"entries": {}, "pending_notices": []}
        try:
            summary = self.plugin._run_round(state)
        finally:
            module.DoubanScoreClient = original
        self.assertIn("开分 1 条", summary)
        entry = state["entries"]["themoviedb:1"]
        self.assertEqual(entry["bindings"][0]["douban_id"], "1")
        self.assertEqual(entry["status"], "scored")
        self.assertEqual(len(state["pending_notices"]), 1)


if __name__ == "__main__":
    unittest.main()
