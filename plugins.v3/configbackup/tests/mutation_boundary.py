#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（边界/异常）：攻击 ConfigBackup 的防护与降级逻辑。

与 ``mutation_restore_ui.py`` 的分工
------------------------------------
- ``mutation_restore_ui.py``：攻击**两阶段交互 UI**（按钮显示、引导文案）
- 本文件：攻击**安全防护与异常降级**（路径穿越、误删防护、损坏输入）

变异体设计原则
--------------
每个变异体都对应一处真实可能写错的防护，不是随机改字符。特别是
「把防护删掉后，异常输入会怎样」——若测试仍全绿，说明该防护无测试覆盖。

框架支持两种条目写法
--------------------
- ``(name, old, new, expect)``                    —— 单段替换
- ``(name, [(old, new), (old2, new2)], expect)``  —— 多段替换（组合穿透）
"""

import os
import re
import shutil
import subprocess
import sys

# 由脚本自身位置推导，避免换克隆/换机器后硬编码路径失配（此前写死沙箱绝对路径）
_HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(_HERE)
PLUGINS_V2 = os.path.dirname(PLUGIN_DIR)
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")
BACKUP = "/tmp/cb_boundary_backup.py"


MUTANTS = [
    # ---------------- 路径穿越防护 ----------------
    (
        "路径穿越防护移除：__resolve_backup_path 不校验文件名",
        """        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.startswith(self._prefix) \\
                or not safe_name.endswith(".zip"):
            return None
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        return bk_path / safe_name""",
        """        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        return bk_path / filename""",
        "应导致 resolve 拒删测试失败（可解析出备份目录外的路径）",
    ),
    (
        "路径穿越防护放宽：只取 basename 不比对原值（../ 被静默吞噬）",
        """        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.startswith(self._prefix) \\
                or not safe_name.endswith(".zip"):
            return None""",
        """        safe_name = Path(filename).name
        if not safe_name.startswith(self._prefix):
            return None""",
        "应导致穿越/绝对路径测试失败",
    ),
    (
        "路径穿越防护放宽：不再要求 .zip 后缀",
        """        if safe_name != filename or not safe_name.startswith(self._prefix) \\
                or not safe_name.endswith(".zip"):
            return None""",
        """        if safe_name != filename or not safe_name.startswith(self._prefix):
            return None""",
        "应导致非 zip 后缀测试失败",
    ),
    (
        "api_delete 防护移除：不校验文件名直接删除",
        """        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.startswith(self._prefix) \\
                or not safe_name.endswith(".zip"):
            return {"success": False, "message": "非法文件名", "data": None}
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        target = bk_path / safe_name""",
        """        safe_name = Path(filename).name
        bk_path = Path(self._backup_dir) if self._backup_dir else self.get_data_path()
        target = bk_path / safe_name""",
        "应导致 delete_rejects_traversal 失败（越界删除）",
    ),
    (
        "api_restore 选择阶段防护移除：不校验文件名",
        """            safe_name = Path(filename).name
            if safe_name != filename or not safe_name.startswith(self._prefix) \\
                    or not safe_name.endswith(".zip"):
                return {"success": False, "message": "非法文件名", "data": None}
            zip_path = self.__resolve_backup_path(safe_name)""",
        """            safe_name = Path(filename).name
            zip_path = self.__resolve_backup_path(safe_name)""",
        # 注意：__resolve_backup_path 仍有校验，需一并拆除才测得出
        "应导致 restore_rejects_traversal 失败",
    ),

    # ---------------- keep_count 误删防护 ----------------
    (
        "保留份数防护移除：keep_count<=0 时清空全部备份（严重）",
        """        if not self._keep_count or self._keep_count <= 0:
            return 0""",
        """        pass""",
        "应导致 zero_keeps_all / negative_keeps_all 失败（全部备份被删）",
    ),
    (
        "保留份数判断反转：keep_count<=0 时反而全删",
        """        if not self._keep_count or self._keep_count <= 0:
            return 0""",
        """        if not self._keep_count or self._keep_count <= 0:
            return len(glob.glob(f"{bk_path}/{self._prefix}*.zip")) and -1""",
        "应导致 zero/negative 保护测试失败",
    ),
    (
        "清理范围放宽：把非备份文件也纳入删除候选",
        """        files = sorted(glob.glob(f"{bk_path}/{self._prefix}*.zip"), key=self.__backup_time)""",
        """        files = sorted(glob.glob(f"{bk_path}/*"), key=self.__backup_time)""",
        "应导致 does_not_touch_non_backup_files 失败",
    ),

    # ---------------- 损坏输入降级 ----------------
    (
        "损坏状态文件不再降级：异常直接抛出（详情页崩溃）",
        """        except Exception as e:
            logger.debug(f"读取待还原状态失败: {e}")
        return None""",
        """        except Exception as e:
            logger.debug(f"读取待还原状态失败: {e}")
            raise""",
        "应导致 corrupt 状态相关测试报错",
    ),
    (
        "缺字段状态不再降级：只判存在不判 filename（删除 / 还原两侧同时变异）",
        [
            (
                """                data = json.loads(f.read_text(encoding="utf-8"))
                if data and data.get("filename"):
                    if self.__is_expired(data):
                        logger.info("待确认删除状态已过期，自动清除")
                        self.__set_pending_delete(None)
                        return None
                    return data""",
                """                data = json.loads(f.read_text(encoding="utf-8"))
                if data:
                    if self.__is_expired(data):
                        logger.info("待确认删除状态已过期，自动清除")
                        self.__set_pending_delete(None)
                        return None
                    return data""",
            ),
            (
                """                data = json.loads(f.read_text(encoding="utf-8"))
                if data and data.get("filename"):
                    if self.__is_expired(data):
                        logger.info("待确认还原状态已过期，自动清除")
                        self.__set_pending_restore(None)
                        return None
                    return data""",
                """                data = json.loads(f.read_text(encoding="utf-8"))
                if data:
                    if self.__is_expired(data):
                        logger.info("待确认还原状态已过期，自动清除")
                        self.__set_pending_restore(None)
                        return None
                    return data""",
            ),
        ],
        "应导致 删除侧 / 还原侧「缺 filename 降级为 None」用例失败",
    ),
    (
        "zip 完整性校验移除：损坏包也能进入待还原状态",
        """            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    bad = zf.testzip()
                if bad:
                    return {"success": False, "message": f"备份文件已损坏（{bad}）", "data": None}
            except Exception as e:
                return {"success": False, "message": f"无法读取备份文件: {e}", "data": None}""",
        """            pass""",
        "应导致 损坏包/截断包/空文件 拒绝测试失败",
    ),
    (
        "悬空状态未清除：文件不存在也不清 pending（残留脏状态）",
        """                    if not zip_path or not zip_path.exists():
                        self.__set_pending_restore(None)
                        return {"success": False, "message": f"待还原的备份文件不存在：{pending['filename']}", "data": None}""",
        """                    if not zip_path or not zip_path.exists():
                        return {"success": False, "message": f"待还原的备份文件不存在：{pending['filename']}", "data": None}""",
        "应导致 pending_pointing_to_deleted_file_is_cleared 失败",
    ),
    (
        "确认还原不校验 pending：无待还原时静默继续",
        """                    pending = self.__get_pending_restore()
                    if not pending or not pending.get("filename"):
                        return {"success": False, "message": "没有待还原的备份，请先在列表中选择备份文件", "data": None}""",
        """                    pending = self.__get_pending_restore() or {}""",
        "应导致 confirm_without_pending / corrupt_state_confirm 失败",
    ),
    (
        "取消非幂等：无 pending 时取消报错",
        """            if confirm == "cancel":
                self.__set_pending_restore(None)
                return {"success": True, "message": "已取消还原操作", "data": None}""",
        """            if confirm == "cancel":
                if not self.__get_pending_restore():
                    return {"success": False, "message": "没有待还原的备份", "data": None}
                self.__set_pending_restore(None)
                return {"success": True, "message": "已取消还原操作", "data": None}""",
        "应导致 cancel_is_idempotent_when_no_pending 失败",
    ),
    (
        "备份目录不存在时列表报错（详情页打不开）",
        """        result = []
        if not bk_path.exists():
            return result""",
        """        result = []
        if not bk_path.exists():
            raise FileNotFoundError(f"备份目录不存在：{bk_path}")""",
        "应导致 missing_dir_lists_empty / page_still_renders 报错",
    ),
    (
        "删除不校验存在性：不存在也报成功（误导用户）",
        """            if not target or not target.exists():
                self.__set_pending_delete(None)
                return {"success": False, "message": f"待删除的备份文件不存在：{safe_name}", "data": None}
            try:""",
        """            try:""",
        "应导致 delete_twice_is_idempotent / delete_on_missing_dir 失败",
    ),
    (
        "空文件名不校验（删除接口）",
        """        if not filename:
            return {"success": False, "message": "缺少文件名参数", "data": None}
        # 防止路径穿越""",
        """        # 防止路径穿越""",
        "应导致 empty_filename_rejected 失败",
    ),

    # ---------------- 还原并发锁 ----------------
    (
        "还原并发锁移除（可重入导致重复还原）",
        """                if not self._restore_lock.acquire(blocking=False):
                    return {"success": False, "message": "已有还原操作正在进行，请稍后再试", "data": None}""",
        """                pass""",
        "应导致 restore_lock_prevents_concurrent 失败",
    ),

    # ---------------- v3.1.0 新增防护 ----------------
    (
        "覆盖式还原退化为合并：不删目标端直接 copytree",
        """            if target.is_symlink():
                target.unlink()
            elif target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink()""",
        """            pass""",
        "应导致 test_directory_is_replaced_not_merged 失败（残留文件清不掉）",
    ),
    (
        "安全网失败仍继续还原（先删后写没了兜底）",
        """                    if not bk_ok:
                        # 完全还原会先删后写，安全网没兜住就不许动——
                        # 否则一次失败的安全网 + 一次失败的还原 = 什么都没了。
                        self.__set_pending_restore(None)
                        return {
                            "success": False,
                            "message": f"还原前安全备份失败，已中止还原（未改动任何配置）：{bk_msg}",
                            "data": None,
                        }""",
        """                    pass""",
        "应导致 test_restore_aborted_when_safety_backup_fails 失败",
    ),
    (
        "保留天数保护移除：高频定时会把最近几小时的备份也删光",
        """        keep_days = int(self._keep_days or 0)
        if keep_days > 0:
            cutoff = time.time() - keep_days * 86400
            old_enough = 0
            for f in files:
                if self.__backup_time(f) >= cutoff:
                    break
                old_enough += 1
            del_cnt = min(del_cnt, old_enough)""",
        """        keep_days = int(self._keep_days or 0)""",
        "应导致 test_recent_backups_survive_count_pressure 失败",
    ),

    # ---------------- v3.2.0 模块 A：备份内容勾选 ----------------
    (
        "空勾选不再拒绝：一项都不选也照打空包",
        """        # 一项都没勾选：直接拒绝，不打空包——空包会让「什么都没有」被当成一次有效备份
        parts = tuple(p for p in self._ALL_PARTS if p in (self._backup_parts or ()))
        if not parts:""",
        """        parts = tuple(p for p in self._ALL_PARTS if p in (self._backup_parts or ()))
        if False:""",
        "应导致 test_empty_parts_refused_without_package 失败（空包被当成有效备份）",
    ),
    (
        "勾选被忽略：没勾「系统配置」也照样打包",
        """            # 2. 备份系统配置（app.env / category.yaml）
            cfg_success = True
            if self._PART_SYSTEM in parts:""",
        """            # 2. 备份系统配置（app.env / category.yaml）
            cfg_success = True
            if True:""",
        "应导致 test_database_only_keeps_user_db 失败（勾了 A 却带走了 B）",
    ),
    (
        "user.db 混回「系统配置」：勾系统配置把数据库一起带走",
        """            for name in ("app.env", "category.yaml"):
                src = config_path / name
                if src.exists() and src.is_file():
                    shutil.copy(src, temp_dir)
                    copied.append(name)
            return True, f"系统配置备份成功（{'、'.join(copied) if copied else '无'}）\"""",
        """            for name in ("app.env", "category.yaml", "user.db"):
                src = config_path / name
                if src.exists() and src.is_file():
                    shutil.copy(src, temp_dir)
                    copied.append(name)
            return True, f"系统配置备份成功（{'、'.join(copied) if copied else '无'}）\"""",
        "应导致 test_system_only_excludes_database 失败（user.db 归属又被混掉）",
    ),
    (
        "清单不写 parts：摘要退回旧逻辑，勾选信息丢失",
        """            #: 本次勾选了哪些部分（v3.2.0 起写入；老读取方忽略即可）
            "parts": parts,""",
        """            #: 本次勾选了哪些部分（v3.2.0 起写入；老读取方忽略即可）
            "parts": None,""",
        "应导致 test_manifest_parts_follow_canonical_order 失败",
    ),
]


def run_tests():
    """跑 ConfigBackup 全部测试，返回 (通过, 摘要)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover",
         "-s", "configbackup/tests", "-t", "configbackup"],
        cwd=PLUGINS_V2, capture_output=True, text=True,
    )
    output = proc.stdout + proc.stderr
    tail = "\n".join([ln for ln in output.strip().splitlines()[-4:]])
    return proc.returncode == 0, tail


def main():
    shutil.copy(TARGET, BACKUP)
    try:
        return _run()
    finally:
        # 无条件还原：本脚本直接覆写插件源码，中途异常若不还原，源码会
        # 永久停留在变异状态，后续测试全部假失败（SeedSpaceGuard 侧真实
        # 踩过一次，此处同步加固）。
        shutil.copy(BACKUP, TARGET)


def _run():
    base_ok, base_tail = run_tests()
    print(f"基线：{'✅ 全部通过' if base_ok else '❌ 基线即失败'}")
    if not base_ok:
        print(base_tail)
        return 1

    source = open(BACKUP, encoding="utf-8").read()
    caught = escaped = skipped = 0
    escaped_names = []

    print(f"\n{'='*70}\n变异测试（边界/异常，共 {len(MUTANTS)} 个变异体）\n{'='*70}")
    for idx, entry in enumerate(MUTANTS, 1):
        if len(entry) == 4:
            name, old, new, expect = entry
            pairs = [(old, new)]
        else:
            name, pairs, expect = entry
        missing = [o for o, _ in pairs if o not in source]
        if missing:
            skipped += 1
            print(f"[{idx:2d}] ⚠️  跳过（定位失败，{len(missing)} 段未匹配）：{name}")
            continue
        mutated = source
        for old, new in pairs:
            mutated = mutated.replace(old, new, 1)
        with open(TARGET, "w", encoding="utf-8") as handle:
            handle.write(mutated)
        ok, tail = run_tests()
        if ok:
            escaped += 1
            escaped_names.append(name)
            print(f"[{idx:2d}] ❌ 逃逸：{name}\n       （{expect}）")
        else:
            caught += 1
            fails = re.search(r"FAILED \((.*?)\)", tail)
            detail = fails.group(0) if fails else "有失败"
            print(f"[{idx:2d}] ✅ 捕获：{name} → {detail}")

    print(f"\n{'='*70}")
    print(f"结果：捕获 {caught} / 逃逸 {escaped} / 跳过 {skipped} / 合计 {caught + escaped + skipped}")
    if escaped_names:
        print("逃逸清单（对应边界测试存在盲区，必须补强）：")
        for name in escaped_names:
            print(f"  - {name}")
    if skipped:
        print(f"⚠️ {skipped} 个变异体定位失败（防护未生效！）——按规约等同于失败，必须同步锚点")
    print("已还原原始源码。")
    # 跳过必须等同于失败（2026-10-02 定案）：否则源码重构后变异体悄悄失配，
    # 脚本却退出码 0、报表全绿，等于留一条永不生效的假防护。
    if escaped_names or skipped:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
