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

PLUGIN_DIR = "/root/.codebuddy/artifact/user-repo/plugins.v2/configbackup"
PLUGINS_V2 = "/root/.codebuddy/artifact/user-repo/plugins.v2"
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
        if safe_name != filename or not safe_name.startswith(self._prefix):
            return {"success": False, "message": "非法文件名"}
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
            if safe_name != filename or not safe_name.startswith(self._prefix):
                return {"success": False, "message": "非法文件名"}
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
        """        files = sorted(glob.glob(f"{bk_path}/{self._prefix}*.zip"), key=os.path.getctime)""",
        """        files = sorted(glob.glob(f"{bk_path}/*"), key=os.path.getctime)""",
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
        "缺字段状态不再降级：只判存在不判 filename",
        """                data = json.loads(f.read_text(encoding="utf-8"))
                if data and data.get("filename"):
                    return data""",
        """                data = json.loads(f.read_text(encoding="utf-8"))
                if data:
                    return data""",
        "应导致 empty_object_degrades_to_none 失败",
    ),
    (
        "zip 完整性校验移除：损坏包也能进入待还原状态",
        """            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    bad = zf.testzip()
                if bad:
                    return {"success": False, "message": f"备份文件已损坏（{bad}）"}
            except Exception as e:
                return {"success": False, "message": f"无法读取备份文件: {e}"}""",
        """            pass""",
        "应导致 损坏包/截断包/空文件 拒绝测试失败",
    ),
    (
        "悬空状态未清除：文件不存在也不清 pending（残留脏状态）",
        """                    if not zip_path or not zip_path.exists():
                        self.__set_pending_restore(None)
                        return {"success": False, "message": f"待还原的备份文件不存在：{pending['filename']}"}""",
        """                    if not zip_path or not zip_path.exists():
                        return {"success": False, "message": f"待还原的备份文件不存在：{pending['filename']}"}""",
        "应导致 pending_pointing_to_deleted_file_is_cleared 失败",
    ),
    (
        "确认还原不校验 pending：无待还原时静默继续",
        """                    pending = self.__get_pending_restore()
                    if not pending or not pending.get("filename"):
                        return {"success": False, "message": "没有待还原的备份，请先在列表中选择备份文件"}""",
        """                    pending = self.__get_pending_restore() or {}""",
        "应导致 confirm_without_pending / corrupt_state_confirm 失败",
    ),
    (
        "取消非幂等：无 pending 时取消报错",
        """            if confirm == "cancel":
                self.__set_pending_restore(None)
                return {"success": True, "message": "已取消还原操作"}""",
        """            if confirm == "cancel":
                if not self.__get_pending_restore():
                    return {"success": False, "message": "没有待还原的备份"}
                self.__set_pending_restore(None)
                return {"success": True, "message": "已取消还原操作"}""",
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
        """        if not target.exists():
            return {"success": False, "message": "备份文件不存在"}
        try:""",
        """        try:""",
        "应导致 delete_twice_is_idempotent / delete_on_missing_dir 失败",
    ),
    (
        "空文件名不校验（删除接口）",
        """        if not filename:
            return {"success": False, "message": "缺少文件名参数"}
        # 防止路径穿越""",
        """        # 防止路径穿越""",
        "应导致 empty_filename_rejected 失败",
    ),

    # ---------------- 还原并发锁 ----------------
    (
        "还原并发锁移除（可重入导致重复还原）",
        """                if not self._restore_lock.acquire(blocking=False):
                    return {"success": False, "message": "已有还原操作正在进行，请稍后再试"}""",
        """                pass""",
        "应导致 restore_lock_prevents_concurrent 失败",
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
    print("已还原原始源码。")
    return 0 if not escaped_names else 2


if __name__ == "__main__":
    sys.exit(main())
