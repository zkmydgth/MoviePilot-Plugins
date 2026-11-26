# -*- coding: utf-8 -*-
"""EffectSubtitleWatch 的纯逻辑与契约测试。"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# 必须在导入插件前放好宿主桩：pytest 会把插件目录放进 sys.path，
# 而插件目录里的 version.py 会遮蔽宿主同名模块。
_STUB_HOST = Path(__file__).parent / "_stub_host"
_PLUGIN_PARENT = Path(__file__).parent.parent.parent
if str(_STUB_HOST) not in sys.path:
    sys.path.insert(0, str(_STUB_HOST))
if str(_PLUGIN_PARENT) not in sys.path:
    sys.path.append(str(_PLUGIN_PARENT))

import effectsubtitlewatch as module  # noqa: E402
from effectsubtitlewatch import (  # noqa: E402
    DEFAULT_CONFIG,
    EffectSubtitleWatch,
    _build_recent,
    _classify,
    _compose_display,
    _fingerprint,
    _format_size,
    _group_candidates,
    _normalize_title,
    _parse_site_ids,
    _parse_title,
    _prune_seen,
    _season_episode_text,
    _short,
    _split_terms,
)


@pytest.fixture(autouse=True)
def _disable_host_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免测试环境加载宿主解析器，固定走正则兜底分支。"""
    monkeypatch.setattr(module, "MetaInfo", None)


def _item(site_id: int, name: str, title: str, size: int, pri: int = 1, **kwargs):
    """构造一条候选字典。"""
    payload = {
        "site_id": site_id,
        "site_name": name,
        "pri": pri,
        "title": title,
        "description": kwargs.get("description", ""),
        "size": size,
        "pubdate": kwargs.get("pubdate", "2026-10-08 21:55:37"),
        "page_url": kwargs.get("page_url", f"https://example.com/{site_id}"),
        "labels": kwargs.get("labels", ["特效"]),
        "category": kwargs.get("category", "电影"),
    }
    return payload


def test_split_terms_dedupes() -> None:
    """关键词按中英文逗号、分号、换行拆分并去重。"""
    assert _split_terms("特效，中字;特效\n4K") == ["特效", "中字", "4K"]
    assert _split_terms(None) == []


def test_fingerprint_requires_same_size() -> None:
    """严格指纹要求标题与字节大小都一致。"""
    first = _fingerprint("Zodiac 2007 2160p-CHD", 56221121905)
    same = _fingerprint("zodiac.2007.2160p-CHD", 56221121905)
    other_size = _fingerprint("Zodiac 2007 2160p-CHD", 56221121906)
    assert first == same
    assert first != other_size


def test_normalize_title_collapses_punctuation() -> None:
    """标题规范化去掉扩展名并把标点压成单空格。"""
    assert _normalize_title("Moana.2026.WEB-DL.mkv") == "moana 2026 web dl"
    assert _normalize_title("  ") == ""


def test_format_size_units() -> None:
    """大小按字节换算为 GB / TB。"""
    assert _format_size(1024 ** 3) == "1.0 GB"
    assert _format_size(0) == "未知"
    assert _format_size("bad") == "未知"


def test_parse_site_ids_ignores_invalid() -> None:
    """站点 ID 解析忽略非法项并去重。"""
    assert _parse_site_ids("14, 9,abc, 14") == [14, 9]
    assert _parse_site_ids("") == []


def test_parse_site_ids_accepts_multi_select_list() -> None:
    """多选框提交的列表同样能解析，0 与非法项被丢弃。"""
    assert _parse_site_ids([14, "9", 0, "abc", 14]) == [14, 9]
    assert _parse_site_ids([]) == []
    assert _parse_site_ids(None) == []


def test_compose_display_formats_year_and_season() -> None:
    """中文名后接年份与季集号，拿不到中文名时退回原标题。"""
    assert _compose_display("空中监狱", 1997, "", "Con Air 1997") == "空中监狱（1997）"
    assert _compose_display("某剧", 2026, "S01E03", "raw") == "某剧（2026） S01E03"
    assert _compose_display("", 1997, "", "Con Air") == "Con Air"
    assert _compose_display(None, None, "", "raw") == "raw"


def test_season_episode_text_formats_meta() -> None:
    """季集号格式化为 SxxExx，缺失时返回空串。"""

    class _WithSeason:
        """带季集的替身。"""

        begin_season = 1
        begin_episode = 3

    class _Empty:
        """无季集的替身。"""

        begin_season = None
        begin_episode = None

    assert _season_episode_text(_WithSeason()) == "S01E03"
    assert _season_episode_text(_Empty()) == ""


def test_parse_title_team_fallbacks() -> None:
    """制作组按尾部、@、字幕组括号依次兜底。"""
    assert _parse_title("Zodiac 2007 2160p x265 10bit-CHD")["team"] == "CHD"
    assert _parse_title("The Jungle Book 2016 7.1-greyfeeling@SGNB")["team"] == "greyfeeling@SGNB"
    assert _parse_title("The Hunting Wives H.265-DepWeb")["team"] == "DepWeb"
    assert _parse_title("【中文注释+特效 蒙太奇字幕组】Sentimental.Value")["team"] == "蒙太奇字幕组"
    assert _parse_title("危机13小时 4K UHD原盘 简英双语特效中文")["team"] == ""


def test_parse_title_quality_fallback() -> None:
    """解析器缺失时用正则兜底画质。"""
    assert _parse_title("Moana 2026 2160p TrueHD7.1")["quality"] == "2160p"


def test_group_candidates_merges_cross_site() -> None:
    """同标题同大小的跨站资源合并为一条，链接取优先级最高站。"""
    grouped = _group_candidates(
        [
            _item(14, "站点甲", "Moana 2026 2160p", 100, pri=1, page_url="https://a/1"),
            _item(9, "站点乙", "Moana 2026 2160p", 100, pri=2, page_url="https://b/1"),
        ]
    )
    assert len(grouped) == 1
    entry = next(iter(grouped.values()))
    assert [site["id"] for site in entry["sites"]] == [14, 9]
    assert entry["link"] == "https://a/1"


def test_group_candidates_keeps_different_size_apart() -> None:
    """大小不同视为不同资源。"""
    grouped = _group_candidates(
        [
            _item(14, "站点甲", "Moana 2026 2160p", 100),
            _item(9, "站点乙", "Moana 2026 2160p", 101),
        ]
    )
    assert len(grouped) == 2


def test_classify_new_item() -> None:
    """未记录过的指纹判定为新增。"""
    grouped = _group_candidates([_item(14, "站点甲", "Moana 2026 2160p", 100)])
    new_items, new_site_items = _classify(grouped, {"items": {}}, True)
    assert len(new_items) == 1
    assert new_site_items == []


def test_classify_new_site_for_known_item() -> None:
    """老资源出现在新站点时可单独提示。"""
    grouped = _group_candidates(
        [
            _item(14, "站点甲", "Moana 2026 2160p", 100),
            _item(9, "站点乙", "Moana 2026 2160p", 100),
        ]
    )
    fingerprint = next(iter(grouped))
    state = {"items": {fingerprint: {"sites": {"14": {"name": "站点甲"}}}}}
    new_items, new_site_items = _classify(grouped, state, True)
    assert new_items == []
    assert len(new_site_items) == 1
    assert [site["id"] for site in new_site_items[0]["new_sites"]] == [9]
    _, muted = _classify(grouped, state, False)
    assert muted == []


def test_prune_seen_removes_expired() -> None:
    """超过保留期的记录被清理。"""
    old = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    fresh = datetime.now().isoformat(timespec="seconds")
    kept = _prune_seen({"old": {"last": old}, "new": {"last": fresh}})
    assert list(kept) == ["new"]


def test_prune_seen_caps_limit() -> None:
    """超过上限时按最后时间保留最新记录。"""
    now = datetime.now()
    items = {
        f"k{index}": {"last": (now - timedelta(minutes=index)).isoformat(timespec="seconds")}
        for index in range(10)
    }
    kept = _prune_seen(items, keep_days=30, limit=3)
    assert len(kept) == 3
    assert "k0" in kept


def test_filter_candidates_rules() -> None:
    """过滤按分类、关键词、排除词、大小与站内重复生效。"""
    plugin = EffectSubtitleWatch()
    plugin._keywords = ["特效"]
    plugin._exclude_words = ["求片"]
    plugin._match_scope = "all"
    candidates = [
        _item(14, "站点甲", "Moana 2026 2160p 特效字幕", 100),
        _item(14, "站点甲", "Moana 2026 2160p 特效字幕", 100),
        _item(14, "站点甲", "求片：特效字幕", 100),
        _item(14, "站点甲", "特效字幕 音乐现场", 100, category="音乐"),
        _item(14, "站点甲", "特效字幕 零字节", 0),
        _item(14, "站点甲", "普通中字", 100, labels=["中字"]),
    ]
    filtered = plugin._filter_candidates(candidates)
    assert [item["title"] for item in filtered] == ["Moana 2026 2160p 特效字幕"]


def test_filter_scope_title_labels_ignores_description_only() -> None:
    """默认范围只看标题与标签，副标题里的“特效”不会命中。"""
    plugin = EffectSubtitleWatch()
    plugin._keywords = ["特效"]
    plugin._exclude_words = []
    plugin._match_scope = "title_labels"
    candidates = [
        _item(14, "站点甲", "Zodiac 2007 2160p BluRay", 100, description="国配简繁双语六特效字幕", labels=[]),
        _item(14, "站点甲", "Zodiac 2007 2160p BluRay", 101, description="", labels=["特效"]),
        _item(14, "站点甲", "Zodiac 2007 2160p 特效字幕", 102, description="", labels=[]),
    ]
    filtered = plugin._filter_candidates(candidates)
    assert [item["size"] for item in filtered] == [101, 102]


def test_filter_scope_all_includes_description() -> None:
    """范围选“全部字段”时，副标题命中同样算数。"""
    plugin = EffectSubtitleWatch()
    plugin._keywords = ["特效"]
    plugin._exclude_words = []
    plugin._match_scope = "all"
    candidates = [
        _item(14, "站点甲", "Zodiac 2007 2160p BluRay", 100, description="国配简繁双语六特效字幕", labels=[]),
    ]
    assert len(plugin._filter_candidates(candidates)) == 1


def test_filter_scope_title_only_skips_labels() -> None:
    """范围选“仅标题”时，只有标题里的关键词算命中。"""
    plugin = EffectSubtitleWatch()
    plugin._keywords = ["特效"]
    plugin._exclude_words = []
    plugin._match_scope = "title_only"
    candidates = [
        _item(14, "站点甲", "Zodiac 2007 2160p BluRay", 100, description="", labels=["特效"]),
        _item(14, "站点甲", "Zodiac 2007 2160p 特效字幕", 101, description="", labels=[]),
    ]
    filtered = plugin._filter_candidates(candidates)
    assert [item["size"] for item in filtered] == [101]


def test_apply_baseline_records_items() -> None:
    """首次运行把现有资源写入去重表并标记基线完成。"""
    plugin = EffectSubtitleWatch()
    grouped = _group_candidates([_item(14, "站点甲", "Moana 2026 2160p", 100)])
    state = {"items": {}}
    plugin._apply_baseline(state, grouped, datetime.now())
    assert state["baseline_done"] is True
    assert len(state["items"]) == 1


def test_form_matches_default_config() -> None:
    """表单默认值与配置键一一对应，且只使用基础组件。"""
    plugin = EffectSubtitleWatch()
    form, defaults = plugin.get_form()
    assert set(defaults) == set(DEFAULT_CONFIG)

    models: list[str] = []

    def walk(nodes) -> None:
        """递归收集表单里的 model 键。"""
        for node in nodes or []:
            props = node.get("props") or {}
            if props.get("model"):
                models.append(str(props["model"]))
            walk(node.get("content"))

    walk(form)
    assert set(models) <= set(DEFAULT_CONFIG)
    assert "run_once" in models
    assert "sites" in models


def test_get_page_handles_empty_state() -> None:
    """没有运行记录时详情页仍返回可渲染文本块。"""
    plugin = EffectSubtitleWatch()
    plugin._state = {"items": {}, "recent": [], "baseline_done": False}
    page = plugin.get_page()
    assert page and page[0]["component"] == "VAlert"


def test_notify_new_includes_team_and_sites() -> None:
    """新增通知里必须带制作组与来源站点。"""
    plugin = EffectSubtitleWatch()
    plugin._notify_enabled = True
    plugin._max_items = 10
    grouped = _group_candidates(
        [
            _item(14, "站点甲", "Moana 2026 2160p TrueHD7.1-DepWeb", 100, labels=[], page_url="https://a/1"),
            _item(9, "站点乙", "Moana 2026 2160p TrueHD7.1-DepWeb", 100, pri=2, labels=[], page_url="https://b/1"),
        ]
    )
    entry = next(iter(grouped.values()))
    plugin._notify_new([entry], [], 0)
    assert len(plugin.messages) == 1
    message = plugin.messages[0]
    assert "发现 1 条新资源" in message["title"]
    assert "制作组：DepWeb" in message["text"]
    assert "站点甲、站点乙" in message["text"]
    assert message["link"] == "https://a/1"


def test_notify_new_respects_max_items_and_tail_note() -> None:
    """超过单次上限时只列前 N 条并提示剩余数量。"""
    plugin = EffectSubtitleWatch()
    plugin._notify_enabled = True
    plugin._max_items = 2
    candidates = [
        _item(14, "站点甲", f"Moana 2026 2160p 第{index}版", 100 + index, labels=[])
        for index in range(4)
    ]
    entries = list(_group_candidates(candidates).values())
    plugin._notify_new(entries, [], 2)
    assert len(plugin.messages) == 1
    assert "还有 2 条未展示" in plugin.messages[0]["text"]


def test_notify_uses_chinese_display_name() -> None:
    """通知标题行用识别后的中文名，而不是英文原名。"""
    plugin = EffectSubtitleWatch()
    plugin._notify_enabled = True
    plugin._max_items = 10
    grouped = _group_candidates(
        [_item(14, "站点甲", "Con Air 1997 2160p TrueHD", 100, labels=[], page_url="https://a/1")]
    )
    entry = next(iter(grouped.values()))
    entry["display"] = "空中监狱（1997）"
    plugin._notify_new([entry], [], 0)
    text = plugin.messages[0]["text"]
    assert "空中监狱（1997）" in text
    assert "制作组" in text


def test_fill_names_caches_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """识别结果写进缓存，命中缓存后不再请求，且受单轮上限约束。"""
    plugin = EffectSubtitleWatch()
    calls: list[str] = []

    def fake(title: str) -> str:
        """记录调用并返回一个中文名。"""
        calls.append(title)
        return f"中文名{len(calls)}"

    monkeypatch.setattr(plugin, "_recognize_display", fake)
    grouped = _group_candidates(
        [_item(14, "站点甲", f"Movie {index} 2026 2160p", 100 + index, labels=[]) for index in range(4)]
    )
    state: dict = {"items": {}, "names": {}}
    assert plugin._fill_names(state, grouped, limit=2) == 2
    assert len(calls) == 2
    assert len(state["names"]) == 2
    assert plugin._fill_names(state, grouped, limit=5) == 2
    assert len(calls) == 4
    assert plugin._fill_names(state, grouped, limit=5) == 0


def test_fill_names_recognizes_priority_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """上限不足时优先识别即将推送的那条。"""
    plugin = EffectSubtitleWatch()
    seen: list[str] = []

    def fake(title: str) -> str:
        """记录识别顺序。"""
        seen.append(title)
        return "中文名"

    monkeypatch.setattr(plugin, "_recognize_display", fake)
    grouped = _group_candidates(
        [
            _item(14, "站点甲", "Old Movie 2020 2160p", 100, pubdate="2020-01-01", labels=[]),
            _item(14, "站点甲", "New Movie 2026 2160p", 101, pubdate="2026-01-01", labels=[]),
        ]
    )
    old_entry = next(entry for entry in grouped.values() if "Old" in str(entry["title"]))
    plugin._fill_names(
        {"items": {}, "names": {}}, grouped, priority_fps=[old_entry["fp"]], limit=1
    )
    assert seen == [old_entry["title"]]


def test_decorate_and_prune_names() -> None:
    """中文名写到条目上，去重表里没有的缓存会被清掉。"""
    plugin = EffectSubtitleWatch()
    grouped = _group_candidates([_item(14, "站点甲", "Con Air 1997 2160p", 100, labels=[])])
    entry = next(iter(grouped.values()))
    state = {"items": {entry["fp"]: {"last": "2026-11-26T00:00:00"}}, "names": {entry["fp"]: "空中监狱（1997）", "stale": "旧名"}}
    plugin._decorate_names(state, grouped)
    assert entry["display"] == "空中监狱（1997）"
    plugin._prune_names(state)
    assert list(state["names"]) == [entry["fp"]]


def test_build_recent_uses_chinese_name() -> None:
    """详情页最近列表显示中文名，并保留原名备查。"""
    plugin = EffectSubtitleWatch()
    grouped = _group_candidates([_item(14, "站点甲", "Con Air 1997 2160p", 100, labels=[])])
    fingerprint = next(iter(grouped))
    recent = _build_recent(grouped, {fingerprint: "空中监狱（1997）"})
    assert recent[0]["title"] == "空中监狱（1997）"
    assert recent[0]["raw"].startswith("Con Air")
    del plugin


def test_form_uses_multi_select_for_sites(monkeypatch: pytest.MonkeyPatch) -> None:
    """站点改成多选框，默认空列表表示全部站点。"""
    plugin = EffectSubtitleWatch()
    monkeypatch.setattr(
        plugin, "_site_options", lambda: [{"title": "站点甲", "value": 14}, {"title": "站点乙", "value": 9}]
    )
    form, defaults = plugin.get_form()
    assert defaults["sites"] == []
    assert DEFAULT_CONFIG["match_scope"] == "all"

    found: list[dict] = []

    def walk(nodes) -> None:
        """递归查找 sites 字段。"""
        for node in nodes or []:
            props = node.get("props") or {}
            if props.get("model") == "sites":
                found.append(props)
            walk(node.get("content"))

    walk(form)
    assert len(found) == 1
    assert found[0]["multiple"] is True
    assert [item["value"] for item in found[0]["items"]] == [14, 9]


def test_build_recent_limits_and_formats() -> None:
    """最近列表按发布时间倒序并限制条数。"""
    candidates = [
        _item(14, "站点甲", f"Moana 2026 2160p 第{index}版", 100 + index, pubdate=f"2026-10-{index + 1:02d}")
        for index in range(25)
    ]
    recent = _build_recent(_group_candidates(candidates))
    assert len(recent) == 20
    assert recent[0]["pubdate"] == "2026-10-25"


def test_short_truncates_long_text() -> None:
    """长文本按字符数截断并补省略号。"""
    assert _short("a" * 80, 10).endswith("…")
    assert len(_short("a" * 80, 10)) == 10
