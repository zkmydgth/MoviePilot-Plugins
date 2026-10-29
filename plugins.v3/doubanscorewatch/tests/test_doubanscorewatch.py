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
from types import SimpleNamespace

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

    # A4. 集数区间落位（长篇动画）
    def test_episode_slot_picks_year_arc(self):
        """按累计集数把第 163 集落到年番3。"""
        from matcher import episode_slot

        hit = episode_slot(
            [
                {"douban_id": "1", "title": "仙逆 第一季", "year": "2023", "episodes": 24},
                {"douban_id": "2", "title": "仙逆 年番1", "year": "2024", "episodes": 52},
                {"douban_id": "3", "title": "仙逆 年番2", "year": "2025", "episodes": 52},
                {"douban_id": "4", "title": "仙逆 年番3", "year": "2026", "episodes": 52},
            ],
            163,
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["douban_id"], "4")
        self.assertEqual((hit["episode_start"], hit["episode_end"]), (129, 180))

    def test_resolve_candidates_uses_episode_range(self):
        """季标记条目的集数覆盖不了当前进度时，改按集数区间落位。"""
        entry = self._entry(
            title="仙逆",
            year="2023",
            seasons=[1],
            total_episode=200,
            lack_episode=37,
        )
        candidates = [
            {"douban_id": "1", "title": "仙逆 第一季", "year": "2023"},
            {"douban_id": "2", "title": "仙逆 年番1", "year": "2024"},
            {"douban_id": "3", "title": "仙逆 年番2", "year": "2025"},
            {"douban_id": "4", "title": "仙逆 年番3", "year": "2026"},
        ]
        episodes = {"1": 24, "2": 52, "3": 52, "4": 52}

        class FakeClient:
            """按 ID 返回集数的客户端替身。"""

            def detail(self, douban_id, media_type):
                """返回集数。"""
                del media_type
                return {"episodes": episodes[douban_id]}

        auto, _pool, note = self.plugin._resolve_candidates(entry, candidates, FakeClient(), 1)
        self.assertIsNotNone(auto)
        self.assertEqual(auto["douban_id"], "4")
        self.assertIn("按集数区间匹配", note)

    def test_migration_unbinds_marked_binding_for_single_season(self):
        """单季订阅绑到带季标记的条目时，迁移解绑等待按集数重匹配。"""
        entry = self._entry(
            title="仙逆",
            seasons=[1],
            status="scored",
            bindings=[self._binding(douban_id="1", title="仙逆 第一季", year="2023")],
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
        self.plugin._sync_subscriptions = lambda entries: (0, 0)
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
    def test_note_once_deduplicates_per_day(self):
        """同一原因同一天只记一次日志，避免每十分钟刷屏。"""
        from app.runtime.log import logger as stub_logger

        self.plugin._state = {"entries": {}, "pending_notices": [], "logged_reasons": {}}
        before = len(stub_logger.records)
        self.plugin._note_once(self.plugin._state, "done", "今日定时检查已完成 1/1 轮，跳过")
        self.plugin._note_once(self.plugin._state, "done", "今日定时检查已完成 1/1 轮，跳过")
        hits = [m for m in stub_logger.records[before:] if "已完成" in m]
        self.assertEqual(len(hits), 1)

    def test_tick_logs_completed_reason(self):
        """当天轮次已用完时，定时入口写一条跳过原因。"""
        import datetime as dt

        from app.runtime.log import logger as stub_logger

        self.plugin._enabled = True
        self.plugin._rounds_per_day = 1
        today = dt.date.today().isoformat()
        self.plugin._state = {
            "entries": {},
            "pending_notices": [],
            "plan": {"date": today, "times": ["21:10"], "done": 1},
        }
        before = len(stub_logger.records)
        self.plugin._tick()
        hits = [m for m in stub_logger.records[before:] if "今日定时检查已完成" in m]
        self.assertEqual(len(hits), 1)

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
        ) and (1, 0)
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

    # H. 订阅同步：退订清理与完结保留
    def test_deleted_subscription_entry_is_removed(self):
        """订阅被删除后，追踪条目在下一轮同步时移除。"""
        entry = self._sync_entry(sub_seen_at="2026-10-28 22:20:00")
        entries = {entry["key"]: entry}
        self.plugin._load_subscriptions = lambda: []
        self.plugin._completion_finished_at = lambda item: None
        synced, removed = self.plugin._sync_subscriptions(entries)
        self.assertEqual((synced, removed), (0, 1))
        self.assertEqual(entries, {})

    def test_completed_subscription_entry_is_kept(self):
        """订阅完结（宿主写完历史后删订阅）不算删除，条目保留并标记完结。"""
        entry = self._sync_entry(sub_seen_at="2026-10-27 21:30:00")
        entries = {entry["key"]: entry}
        self.plugin._load_subscriptions = lambda: []
        calls = []
        finished = datetime(2026, 10, 28, 9, 0, 0)
        self.plugin._completion_finished_at = lambda item: calls.append(item) or finished
        synced, removed = self.plugin._sync_subscriptions(entries)
        self.assertEqual((synced, removed), (0, 0))
        self.assertTrue(entries[entry["key"]]["sub_completed"])
        self.assertEqual(calls, [entry])

    def test_stale_completion_history_does_not_keep_entry(self):
        """完结历史早于最后一次看到订阅：属于完结后重新订阅再删除，条目要清理。"""
        entry = self._sync_entry(sub_seen_at="2026-10-28 22:20:00")
        entries = {entry["key"]: entry}
        self.plugin._load_subscriptions = lambda: []
        self.plugin._completion_finished_at = lambda item: datetime(2026, 10, 1, 20, 0, 0)
        synced, removed = self.plugin._sync_subscriptions(entries)
        self.assertEqual((synced, removed), (0, 1))
        self.assertEqual(entries, {})

    def test_missing_seen_timestamp_keeps_entry(self):
        """旧状态没有订阅时间戳时按完结保留，避免升级后误删条目。"""
        entry = self._sync_entry(sub_seen_at="")
        entries = {entry["key"]: entry}
        self.plugin._load_subscriptions = lambda: []
        self.plugin._completion_finished_at = lambda item: datetime(2026, 10, 1, 20, 0, 0)
        synced, removed = self.plugin._sync_subscriptions(entries)
        self.assertEqual((synced, removed), (0, 0))
        self.assertIn(entry["key"], entries)

    def test_completed_entry_does_not_query_history_again(self):
        """已标记完结的条目不再逐轮查历史，避免额外数据库查询。"""
        entry = self._sync_entry(sub_completed=True, sub_seen_at="2026-10-27 21:30:00")
        entries = {entry["key"]: entry}
        self.plugin._load_subscriptions = lambda: []
        calls = []
        self.plugin._completion_finished_at = lambda item: calls.append(item) or None
        synced, removed = self.plugin._sync_subscriptions(entries)
        self.assertEqual((synced, removed), (0, 0))
        self.assertIn(entry["key"], entries)
        self.assertEqual(calls, [])

    def test_history_failure_keeps_entry(self):
        """历史表不可读时保守保留条目，不误删正在追踪的媒体。"""
        entry = self._sync_entry(sub_seen_at="2026-10-28 22:20:00")
        entries = {entry["key"]: entry}
        self.plugin._load_subscriptions = lambda: []

        def broken(item):
            """模拟数据库不可用。"""
            del item
            raise RuntimeError("db down")

        self.plugin._completion_finished_at = broken
        synced, removed = self.plugin._sync_subscriptions(entries)
        self.assertEqual((synced, removed), (0, 0))
        self.assertIn(entry["key"], entries)

    def test_resubscribe_clears_completed_flag(self):
        """重新订阅同一媒体要恢复为活动订阅，并记录剧集组。"""
        entry = self._sync_entry(sub_completed=True, sub_seen_at="2026-10-20 21:30:00")
        entries = {entry["key"]: entry}
        self.plugin._load_subscriptions = lambda: [self._fake_sub(episode_group="grp")]

        def unexpected(item):
            """重新订阅的条目不应查历史。"""
            del item
            raise AssertionError("重新订阅后不应查历史")

        self.plugin._completion_finished_at = unexpected
        synced, removed = self.plugin._sync_subscriptions(entries)
        self.assertEqual((synced, removed), (1, 0))
        self.assertFalse(entries[entry["key"]]["sub_completed"])
        self.assertEqual(entries[entry["key"]]["episode_group"], "grp")
        self.assertIsNotNone(self.plugin._parse_timestamp(entries[entry["key"]]["sub_seen_at"]))

    def test_completion_finished_at_queries_each_season(self):
        """完结时间按条目覆盖的每个季号查历史，带上剧集组并取最新一条。"""
        import sys
        import types
        from unittest.mock import patch

        calls = []
        dates = {1: "2026-10-01 08:00:00", 2: "2026-10-28 09:00:00"}

        class FakeOper:
            """记录查询条件的订阅历史 Oper 替身。"""

            def query(self, filters, page):
                """按季号返回历史记录，并记录分页参数。"""
                calls.append((filters, page))
                date = dates.get(filters.season)
                records = [SimpleNamespace(date=date)] if date else []
                return records, len(records)

        module = types.ModuleType("app.db.oper.subscribehistory")
        module.SubscribeHistoryOper = FakeOper
        entry = self._sync_entry(seasons=[1, 2], episode_group="grp")
        with patch.dict(sys.modules, {"app.db.oper.subscribehistory": module}):
            finished = self.plugin._completion_finished_at(entry)
        self.assertEqual(finished, datetime(2026, 10, 28, 9, 0, 0))
        self.assertEqual([item[0].season for item in calls], [1, 2])
        self.assertTrue(all(item[0].episode_group == "grp" for item in calls))
        self.assertEqual([item[0].media_id for item in calls], ["290684", "290684"])
        self.assertTrue(all((item[1].page, item[1].count) == (1, 1) for item in calls))

    def test_parse_timestamp_accepts_host_formats(self):
        """状态与历史里的多种时间文本都要能解析，垃圾值返回 None。"""
        self.assertEqual(self.plugin._parse_timestamp("2026-10-28 22:20:00"), datetime(2026, 10, 28, 22, 20))
        self.assertEqual(self.plugin._parse_timestamp("2026-10-28 22:20"), datetime(2026, 10, 28, 22, 20))
        self.assertEqual(self.plugin._parse_timestamp("2026-10-28"), datetime(2026, 10, 28))
        self.assertIsNone(self.plugin._parse_timestamp(""))
        self.assertIsNone(self.plugin._parse_timestamp("两天前"))

    def test_page_draws_divider_between_entries(self):
        """条目分组之间画分隔线，完结保留的条目在状态里标注。"""
        first = self._sync_entry(key="themoviedb:1", media_id="1", title="剑来", sub_completed=True)
        second = self._sync_entry(key="themoviedb:2", media_id="2", title="凡人修仙传")
        self.plugin._enabled = True
        self.plugin._state = {
            "entries": {first["key"]: first, second["key"]: second},
            "pending_notices": [],
            "plan": {"date": "", "times": [], "done": 0},
        }
        page = self.plugin.get_page()
        rows = []

        def walk(node):
            """递归收集表格行组件。"""
            if isinstance(node, dict):
                if node.get("component") == "tr":
                    rows.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(page)
        divider_rows = [
            row
            for row in rows
            if any(
                "border-bottom" in str((cell.get("props") or {}).get("style") or "")
                for cell in row.get("content") or []
            )
        ]
        self.assertEqual(len(divider_rows), 2)
        self.assertIn("订阅已完结", str(page))

    def test_deleted_entry_not_checked_in_round(self):
        """退订条目在本轮同步后直接移除，不再请求豆瓣。"""
        import doubanscorewatch as module

        calls = []

        class FakeFactory:
            """记录详情请求的豆瓣客户端替身。"""

            def __init__(self, cookie=""):
                """忽略 Cookie。"""
                del cookie

            def detail(self, douban_id, media_type):
                """记录调用并返回空详情。"""
                calls.append((douban_id, media_type))
                return None

        entry = self._sync_entry(
            status="scored",
            bindings=[{"douban_id": "1", "title": "剑来", "rating": 7.0, "votes": 100}],
        )
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        self.plugin._load_subscriptions = lambda: []
        self.plugin._completion_finished_at = lambda item: None
        original = module.DoubanScoreClient
        module.DoubanScoreClient = FakeFactory
        try:
            summary = self.plugin._run_round(state, scheduled=False)
        finally:
            module.DoubanScoreClient = original
        self.assertIn("删除 1 条已退订", summary)
        self.assertEqual(state["entries"], {})
        self.assertEqual(calls, [])

    def test_sync_stamps_seen_timestamp(self):
        """每轮同步都把订阅存在的时间写进条目，供完结判定比对。"""
        entry = self._sync_entry(sub_seen_at="")
        entries = {entry["key"]: entry}
        self.plugin._load_subscriptions = lambda: [self._fake_sub()]
        synced, removed = self.plugin._sync_subscriptions(entries)
        self.assertEqual((synced, removed), (1, 0))
        self.assertIsNotNone(self.plugin._parse_timestamp(entries[entry["key"]]["sub_seen_at"]))

    # I. 全量修复回归
    def test_same_year_unrelated_title_not_bound(self):
        """同年唯一但标题完全不同的候选不自动绑定，只列候选。"""
        result = select_candidates(
            [{"douban_id": "9", "title": "完全无关的纪录片", "year": "2026"}],
            "剑来",
            "2026",
        )
        self.assertIsNone(result["auto"])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertIn("不一致", result["reason"])

    def test_exact_title_allows_two_year_gap(self):
        """同名条目年份差两年以内仍可自动绑定。"""
        result = select_candidates(
            [{"douban_id": "3", "title": "剑来", "year": "2024"}],
            "剑来",
            "2026",
        )
        self.assertIsNotNone(result["auto"])
        self.assertEqual(result["auto"]["douban_id"], "3")

    def test_match_backoff_skips_next_round(self):
        """匹配失败的条目在退避期内不再搜索豆瓣。"""
        entry = self._entry(status="unmatched", next_try="2999-01-01 00:00:00", tries=2)
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        self.plugin._load_subscriptions = lambda: [self._fake_sub(media_id="12345")]
        calls = []

        class SlowClient:
            """记录搜索调用的客户端替身。"""

            consecutive_failures = 0

            def __init__(self, cookie=""):
                """忽略 Cookie。"""
                del cookie

            def search(self, keyword, media_type):
                """记录调用。"""
                calls.append((keyword, media_type))
                return []

            def detail(self, douban_id, media_type):
                """不应被调用。"""
                calls.append((douban_id, media_type))
                return None

        import doubanscorewatch as module

        original = (module.DoubanScoreClient, module.time.sleep)
        module.DoubanScoreClient = SlowClient
        module.time.sleep = lambda *_: None
        try:
            summary = self.plugin._run_round(state, scheduled=False)
        finally:
            module.DoubanScoreClient, module.time.sleep = original
        self.assertIn("匹配 0 条", summary)
        self.assertEqual(calls, [])

    def test_match_failure_defers_next_try(self):
        """匹配失败后按次数写入下次重试时间。"""
        entry = self._entry()

        class EmptyClient:
            """搜索恒为空的客户端替身。"""

            def search(self, keyword, media_type):
                """返回空列表。"""
                del keyword, media_type
                return []

        self.plugin._match_entry(entry, EmptyClient(), [0])
        self.assertEqual(entry["tries"], 1)
        self.assertIsNotNone(self.plugin._parse_timestamp(entry.get("next_try")))
        self.assertFalse(self.plugin._match_due(entry))

    def test_seasons_rebuilt_from_subscriptions(self):
        """季号按当前订阅重建，已退订的季不再残留。"""
        entry = self._sync_entry(seasons=[1, 2])
        entries = {entry["key"]: entry}
        self.plugin._load_subscriptions = lambda: [self._fake_sub(season=2)]
        self.plugin._sync_subscriptions(entries)
        self.assertEqual(entries[entry["key"]]["seasons"], [2])

    def test_closed_binding_not_checked(self):
        """关闭追变化后，已开分绑定不再请求豆瓣。"""
        entry = self._entry(
            status="scored",
            bindings=[self._binding(closed=True, rating=7.9, votes=100)],
        )
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        client = FakeClient({"title": "风起天南", "rating": 7.9, "votes": 100})
        self.plugin._check_entry(entry, client, state)
        self.assertEqual(client.calls, [])
        self.assertEqual(state["pending_notices"], [])
        self.assertEqual(entry["status"], "scored")

    def test_expired_binding_not_checked(self):
        """超过最长追踪天数的绑定不再请求豆瓣。"""
        self.plugin._keep_tracking = True
        self.plugin._track_days = 30
        entry = self._entry(
            bindings=[self._binding(first_seen="2020-01-01", rating=7.9, votes=100, notified=True)],
        )
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        client = FakeClient({"title": "风起天南", "rating": 9.0, "votes": 100})
        self.plugin._check_entry(entry, client, state)
        self.assertEqual(client.calls, [])

    def test_baseline_pending_confirms_open_score(self):
        """手动绑定时没读到分数：确认无分后，后续开分正常通知。"""
        binding = self._binding(baseline_pending=True, initial_rating=0, initial_votes=0)
        entry = self._entry(bindings=[binding])
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}

        class MutableClient:
            """按阶段返回不同详情的客户端替身。"""

            def __init__(self):
                """初始为无评分。"""
                self.detail_data = {"title": "风起天南", "rating": 0, "votes": 0}

            def detail(self, douban_id, media_type):
                """返回当前详情。"""
                del douban_id, media_type
                return self.detail_data

        client = MutableClient()
        self.plugin._check_entry(entry, client, state)
        self.assertNotIn("baseline_pending", binding)
        self.assertEqual(state["pending_notices"], [])
        client.detail_data = {"title": "风起天南", "rating": 7.9, "votes": 67042}
        self.plugin._check_entry(entry, client, state)
        self.assertEqual(len(state["pending_notices"]), 1)

    def test_baseline_pending_suppresses_notice_when_scored(self):
        """绑定时没读到分数但实际已有分：按早已开分处理，不补发。"""
        binding = self._binding(baseline_pending=True, initial_rating=0, initial_votes=0)
        entry = self._entry(bindings=[binding])
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        client = FakeClient({"title": "风起天南", "rating": 7.9, "votes": 67042})
        self.plugin._check_entry(entry, client, state)
        self.assertEqual(state["pending_notices"], [])
        self.assertTrue(binding.get("pre_scored"))
        self.assertNotIn("baseline_pending", binding)

    def test_notice_retry_backoff_and_limit(self):
        """通知失败按次数退避，达到上限后丢弃。"""
        state = {"entries": {}, "pending_notices": [{"title": "t", "text": "b"}]}

        def failing(**kwargs):
            """模拟发送失败。"""
            del kwargs
            raise RuntimeError("channel down")

        self.plugin.post_message = failing
        self.plugin._flush_notices(state)
        queued = state["pending_notices"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["attempts"], 1)
        self.assertIsNotNone(self.plugin._parse_timestamp(queued[0]["next_try"]))
        for _ in range(4):
            state["pending_notices"][0]["next_try"] = "2000-01-01 00:00:00"
            self.plugin._flush_notices(state)
        self.assertEqual(state["pending_notices"], [])

    def test_notice_enqueued_during_send_is_kept(self):
        """发送过程中新入队的通知不会被冲刷覆盖。"""
        state = {"entries": {}, "pending_notices": [{"title": "t1", "text": "b1"}]}

        def sender(**kwargs):
            """发送第一条时再入队一条。"""
            del kwargs
            self.plugin._enqueue(state, "t2", "b2")

        self.plugin.post_message = sender
        self.plugin._flush_notices(state)
        titles = [item["title"] for item in state["pending_notices"]]
        self.assertEqual(titles, ["t2"])

    def test_quiet_single_side_uses_defaults(self):
        """静默只填一侧时，另一侧按 00:00 / 23:59 处理。"""
        self.plugin._quiet_start, self.plugin._quiet_end = "22:00", ""
        self.assertTrue(self.plugin._in_quiet(datetime(2026, 10, 29, 23, 0)))
        self.assertFalse(self.plugin._in_quiet(datetime(2026, 10, 29, 12, 0)))
        self.plugin._quiet_start, self.plugin._quiet_end = "", "08:00"
        self.assertTrue(self.plugin._in_quiet(datetime(2026, 10, 29, 7, 0)))
        self.assertFalse(self.plugin._in_quiet(datetime(2026, 10, 29, 12, 0)))
        self.plugin._quiet_start, self.plugin._quiet_end = "", ""
        self.assertFalse(self.plugin._in_quiet(datetime(2026, 10, 29, 3, 0)))

    def test_manual_binding_removed_from_config_unbinds(self):
        """配置里删掉手动绑定行后自动解绑，页面手点的绑定不受影响。"""
        entry = self._entry()
        self.plugin._state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        self.plugin._manual_bindings = "themoviedb:12345=34925294"
        self.plugin._apply_manual_bindings()
        self.assertEqual(entry["bindings"][0]["config_key"], "themoviedb:12345")
        self.plugin._bind(entry, {"douban_id": "888", "title": "", "year": ""}, "manual")
        self.plugin._manual_bindings = ""
        self.plugin._apply_manual_bindings()
        self.assertEqual([item["douban_id"] for item in entry["bindings"]], ["888"])

    def test_sync_error_recorded(self):
        """订阅同步失败时写进状态并在页面摘要里显示。"""
        self.plugin._state = {"entries": {}, "pending_notices": []}

        def boom():
            """模拟订阅读取失败。"""
            raise RuntimeError("db down")

        self.plugin._load_subscriptions = boom
        synced, removed = self.plugin._sync_subscriptions(self.plugin._state["entries"])
        self.assertEqual((synced, removed), (0, 0))
        self.assertIn("db down", self.plugin._state["last_sync_error"])
        self.plugin._enabled = True
        self.assertIn("订阅同步异常", self.plugin._summary_text())

    def test_zero_match_rounds_counted(self):
        """连续多轮没有任何匹配时在页面摘要里提示。"""
        import doubanscorewatch as module

        entry = self._entry(status="unmatched")
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        self.plugin._load_subscriptions = lambda: [self._fake_sub(media_id="12345")]

        class EmptyClient:
            """搜索与详情都为空的客户端替身。"""

            consecutive_failures = 0

            def __init__(self, cookie=""):
                """忽略 Cookie。"""
                del cookie

            def search(self, keyword, media_type):
                """返回空结果。"""
                del keyword, media_type
                return []

            def detail(self, douban_id, media_type):
                """返回空详情。"""
                del douban_id, media_type
                return None

        original = (module.DoubanScoreClient, module.time.sleep)
        module.DoubanScoreClient = EmptyClient
        module.time.sleep = lambda *_: None
        try:
            self.plugin._run_round(state, scheduled=False)
            entry["next_try"] = ""
            self.plugin._run_round(state, scheduled=False)
        finally:
            module.DoubanScoreClient, module.time.sleep = original
        self.assertEqual(state["zero_match_rounds"], 2)
        self.plugin._enabled = True
        self.assertIn("没有任何新匹配", self.plugin._summary_text())

    def test_failure_breaker_stops_round(self):
        """豆瓣连续失败达到阈值时提前结束本轮。"""
        import doubanscorewatch as module

        entry = self._entry(bindings=[self._binding()])
        state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        self.plugin._load_subscriptions = lambda: [self._fake_sub()]

        class FailingClient:
            """详情恒失败、并累计连续失败次数的客户端替身。"""

            def __init__(self, cookie=""):
                """初始化失败计数。"""
                del cookie
                self.consecutive_failures = 0

            def detail(self, douban_id, media_type):
                """返回空并累加失败。"""
                del douban_id, media_type
                self.consecutive_failures += 5
                return None

        original = (module.DoubanScoreClient, module.time.sleep)
        module.DoubanScoreClient = FailingClient
        module.time.sleep = lambda *_: None
        try:
            summary = self.plugin._run_round(state, scheduled=False)
        finally:
            module.DoubanScoreClient, module.time.sleep = original
        self.assertIn("豆瓣连续失败", summary)

    def test_legacy_removed_entries_dropped(self):
        """旧状态里的「订阅已删除」条目在迁移时清掉。"""
        entry = self._sync_entry(status="removed")
        self.plugin._state = {"entries": {entry["key"]: entry}, "pending_notices": []}
        self.plugin._migrate_season_bindings()
        self.assertEqual(self.plugin._state["entries"], {})

    def test_api_bind_does_not_call_douban(self):
        """页面绑定不再同步访问豆瓣，只写绑定。"""
        import doubanscorewatch as module

        entry = self._entry()
        self.plugin._state = {"entries": {entry["key"]: entry}, "pending_notices": []}

        class RefusingClient:
            """任何实例化都视为失败。"""

            def __init__(self, *args, **kwargs):
                """直接报错。"""
                del args, kwargs
                raise AssertionError("绑定不应访问豆瓣")

        original = module.DoubanScoreClient
        module.DoubanScoreClient = RefusingClient
        try:
            result = self.plugin.api_bind(entry["key"], "34925294")
        finally:
            module.DoubanScoreClient = original
        self.assertTrue(result["success"])
        self.assertTrue(entry["bindings"][0]["baseline_pending"])

    def _sync_entry(self, **overrides):
        """构造同步用例的追踪条目。"""
        entry = {
            "key": "themoviedb:290684",
            "media_source": "themoviedb",
            "media_id": "290684",
            "type": "电视剧",
            "title": "剑来",
            "year": "2026",
            "seasons": [1],
            "episode_group": None,
            "sub_seen_at": "2026-10-28 22:20:00",
            "status": "pending",
            "bindings": [],
            "candidates": [],
            "ai_suggestions": [],
        }
        entry.update(overrides)
        return entry

    @staticmethod
    def _fake_sub(**overrides):
        """构造宿主订阅记录替身。"""
        values = {
            "type": "电视剧",
            "media_source": "themoviedb",
            "media_id": "290684",
            "name": "剑来",
            "year": "2026",
            "season": 1,
            "episode_group": None,
            "state": "R",
            "total_episode": 26,
            "lack_episode": 3,
        }
        values.update(overrides)
        return SimpleNamespace(**values)


if __name__ == "__main__":
    unittest.main()
