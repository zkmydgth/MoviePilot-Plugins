#!/usr/bin/env python3
"""
变异测试：往核心逻辑里注入缺陷，要求每一条都被现有测试捕获。

按本仓库约定：**跳过即失败** —— 变异锚点与源码失配时说明该变异体守护的
缺陷此刻已无人看守，脚本必须报红，不能静默放过。

用法（插件目录下）：:

    python tests/mutation_core.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent

# (文件, 原片段, 变异片段, 说明)
MUTANTS = [
    (
        "core/config.py",
        'return SIGN_TYPE_COMMAND if "命令" in value else SIGN_TYPE_BUTTON',
        "return SIGN_TYPE_BUTTON",
        "命令别名失效：所有方式都被当成按钮",
    ),
    (
        "core/config.py",
        '    "暂不可用",',
        '    "__never_matches__",',
        "内置失败词表丢失「暂不可用」（HDHaven 用例回归）",
    ),
    (
        "core/signin.py",
        "    failure_words = tuple(failure_keywords or DEFAULT_FAILURE_KEYWORDS)",
        "    failure_words = ()",
        "失败关键词失效：明确失败文案回到「未确认」且不进重试",
    ),
    (
        "core/signin.py",
        "    if status == STATUS_UNCONFIRMED and ai_judge is not None:",
        "    if False:  # 变异：AI 复核不再接线",
        "AI 复核不再接线（未确认永远保持未确认）",
    ),
    (
        "core/signin.py",
        "        if review.verdict in _AI_VERDICT_TO_STATUS:",
        "        if False:  # 变异：忽略复核结论",
        "AI 复核结论被忽略（判定不生效）",
    ),
    (
        "core/signin.py",
        "        # 失败不能计入成功：失败会进失败重试窗口\n        result[\"ok\"] = False",
        "        # 变异：失败不再回写 ok（不会进重试）",
        "失败不再回写 ok：失败目标进不了重试窗口",
    ),
    (
        "core/ai.py",
        '        if not self.enabled:\n            return AiReview(state=AI_STATE_NOT_CALLED, message="未开启 AI 复核/归纳")',
        '        if False:  # 变异：忽略启用开关\n            return AiReview(state=AI_STATE_NOT_CALLED, message="未开启 AI 复核/归纳")',
        "AI 启用开关失效：关闭时仍会调用模型",
    ),
    (
        "core/ai.py",
        '        if value in {"failure", "fail", "false", "失败"}:\n            return AI_VERDICT_FAILURE',
        '        if value in {"failure", "fail", "false", "失败"}:\n            return AI_VERDICT_SUCCESS',
        "复核结论翻转：失败被判成成功",
    ),
    (
        "core/config.py",
        "        wait_seconds = 15\n        if len(fields) > 4 and fields[4]:",
        "        wait_seconds = 5\n        if len(fields) > 4 and fields[4]:",
        "等待秒数默认值被改小",
    ),
    (
        "core/config.py",
        'if not bot_username.startswith("@"):',
        "if False:",
        "bot 用户名不再补 @",
    ),
    (
        "core/config.py",
        "        key = f\"acc{index}\"",
        "        key = \"\"",
        "槽位账号标识不再自动生成（账号列表变空）",
    ),
    (
        "core/config.py",
        "        if action not in (\n            LOGIN_ACTION_SEND,\n            LOGIN_ACTION_CONFIRM,\n            LOGIN_ACTION_LOGOUT,\n        ):",
        "        if False:  # 变异：不再筛选登录动作",
        "「不操作」也被当成待执行登录动作",
    ),
    (
        "core/config.py",
        "        if len(fields) < 4:\n            continue",
        "        if len(fields) < 3:\n            continue",
        "字段数下限放宽，残缺行被误收",
    ),
    (
        "core/signin.py",
        '            result["ok"] = bool(clicked and has_evidence)',
        '            result["ok"] = True',
        "按钮没点到/无返回也判成功",
    ),
    (
        "core/signin.py",
        '            has_evidence = bool(str(reply or "").strip() or str(alert or "").strip())',
        "            has_evidence = True",
        "无返回也算有证据（假成功回归）",
    ),
    (
        "core/signin.py",
        "            if sent_date >= sent_at:\n                fresh.append(message)",
        "            if sent_date <= sent_at:\n                fresh.append(message)",
        "时间归属反了：只认旧消息",
    ),
    (
        "core/signin.py",
        "        if already_signed_today:\n            return STATUS_REPEATED\n        return STATUS_FAILED",
        "        return STATUS_REPEATED",
        "只回菜单不再看今天是否成功过（假成功回归）",
    ),
    (
        "core/retry.py",
        '        record["ok_today"] = bool(record.get("ok_today")) or bool(item.get("ok"))',
        '        record["ok_today"] = bool(item.get("ok"))',
        "ok_today 不再粘住（当天成功记录丢失去）",
    ),
    (
        "core/signin.py",
        "            result[\"ok\"] = bool(reply)",
        '            result["ok"] = True',
        "命令式没回复也判成功",
    ),
    (
        "core/store.py",
        '    state["history"] = list(reversed(keep))',
        "    state[\"history\"] = list(keep)",
        "历史顺序反了（最近结果排序失效）",
    ),
    (
        "core/login.py",
        "    if not phone_code_hash:",
        "    if False:",
        "未发码也能进入确认登录",
    ),
    (
        "core/signin.py",
        "    if failed and mode == NOTIFY_MODE_SUCCESS:",
        "    if False:",
        "「仅成功时通知」在有失败项时也会发",
    ),
    (
        "core/config.py",
        "    if isinstance(value, dict):",
        "    if False:",
        "下拉选中的对象取值不再归一化（选了也按默认走）",
    ),
    (
        "core/config.py",
        '    "签到成功",\n    "签到完成",',
        '    "签到完成",',
        "「签到成功」不再被识别（状态分类失真）",
    ),
    (
        "core/signin.py",
        "    success_words = tuple(success_keywords or DEFAULT_SUCCESS_KEYWORDS)",
        "    success_words = tuple(success_keywords or ())",
        "自定义关键词为空时不再回落内置默认（成功档失真）",
    ),
    (
        "core/config.py",
        '        for item in re.split(r"[|,，、\\n]+", raw)',
        '        for item in re.split(r"[.]+", raw)',
        "关键词分隔符解析失效（配置里多写一个都不生效）",
    ),
    (
        "core/login.py",
        "    if not code:",
        "    if False:",
        "空验证码不再拦截",
    ),
    (
        "core/login.py",
        "    if time.time() - float(pending.get(\"ts\") or 0) > PENDING_TTL_SECONDS:",
        "    if False:",
        "过期验证码不再失效",
    ),
    (
        "core/signin.py",
        "        if max(1, int(concurrency)) <= 1:",
        "        if True:  # 变异：并发失效，永远串行",
        "并发失效：配置了并发仍逐条串行",
    ),
    (
        "core/signin.py",
        "    if wait_seconds is None:\n        return item",
        "    if True:  # 变异：FloodWait 不再重试\n        return item",
        "FloodWait 不再退避重试",
    ),
    (
        "core/signin.py",
        "    AI_VERDICT_REPEATED: STATUS_REPEATED,",
        "    AI_VERDICT_REPEATED: STATUS_SUCCESS,",
        "AI 判「已签到」被当成「签到成功」",
    ),
    (
        "core/autofill.py",
        "        if key in seen or key in blocked_keys:",
        "        if False:  # 变异：不再查重",
        "关键词查重失效：重复词被反复写入",
    ),
    (
        "core/autofill.py",
        "        if len(merged) >= max(1, int(limit)):",
        "        if False:  # 变异：上限失效",
        "词表上限失效：无限追加",
    ),
    (
        "core/ai.py",
        "            if not is_acceptable_keyword(word, source_text, blacklist):",
        "            if False:  # 变异：不再校验候选词",
        "候选词校验失效：黑名单/非原文词被采纳",
    ),
    (
        "core/store.py",
        "    state[\"ai_keyword_log\"] = log[-AI_KEYWORD_LOG_LIMIT:]",
        "    state[\"ai_keyword_log\"] = []",
        "AI 归纳审计日志丢失",
    ),
]


def run_suite(workdir: Path) -> int:
    """
    在变异后的副本里跑全部单测。

    :param workdir: 变异副本目录
    :return int: 子进程退出码（非 0 表示有测试失败 = 变异被捕获）
    """

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode


def main() -> int:
    """
    逐个注入变异并判定捕获情况。

    :return int: 0 = 全部捕获；1 = 存在逃逸或跳过
    """

    caught = 0
    escaped = 0
    skipped = 0
    for relative, old, new, desc in MUTANTS:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "tgsignin"
            shutil.copytree(
                PLUGIN_DIR,
                work,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
            target = work / relative
            text = target.read_text(encoding="utf-8")
            if old not in text:
                print(f"SKIP      {desc}（锚点失配：防护未生效！）")
                skipped += 1
                continue
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            code = run_suite(work)
            if code != 0:
                print(f"CAUGHT    {desc}")
                caught += 1
            else:
                print(f"ESCAPED   {desc}")
                escaped += 1

    total = len(MUTANTS)
    print("-" * 60)
    print(f"变异体 {total}：捕获 {caught} / 逃逸 {escaped} / 跳过 {skipped}")
    if escaped or skipped:
        print("有逃逸或有跳过（防护未生效！）")
        return 1
    print("全部捕获，0 逃逸 0 跳过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
