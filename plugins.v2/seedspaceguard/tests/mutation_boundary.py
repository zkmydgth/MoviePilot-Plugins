#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变异测试（边界/安全）：攻击三层安全边界与保守返回，验证边界测试能否捕获。

与 ``mutation_linkage.py`` 的分工
---------------------------------
- ``mutation_linkage.py``：攻击**联动清理的业务判定**（删种放宽、文案回退）
- 本文件：攻击**不可逆操作的防护边界**（路径越界、inode 竞态、链接逃逸、
  保守返回被改成乐观默认）

为什么单独做
------------
边界测试最容易变成"永远绿的装饰"——断言写松了，把防护代码全删掉也照样
通过。本脚本把防护代码逐个拆除，若某个变异体全绿，说明对应边界测试是
无效的，必须收紧断言。

变异体设计原则：**每个变异体都对应一处真实可能写错的防护**，
不是随机改字符。
"""

import os
import re
import shutil
import subprocess
import sys

PLUGIN_DIR = "/root/.codebuddy/artifact/user-repo/plugins.v2/seedspaceguard"
PLUGINS_V2 = "/root/.codebuddy/artifact/user-repo/plugins.v2"
TARGET = os.path.join(PLUGIN_DIR, "__init__.py")
BACKUP = "/tmp/ssg_boundary_backup.py"


# (名称, 原文, 变异后, 期望被捕获的说明)
MUTANTS = [
    # ---------------- 边界①：词法校验 ----------------
    (
        "边界①③组合穿透：词法校验 + realpath 校验同时拆除（任意路径都可删）",
        [
            ("""            if not self._path_under_any(path):
                logger.warning("【保种空间守护】跳过配置目录外的路径：%s", path)
                continue""", """            pass"""),
            ("""            if not self._path_under_any(os.path.realpath(path)):
                logger.warning("【保种空间守护】realpath 越界，跳过：%s", path)
                continue""", """            pass"""),
        ],
        # 只拆①会「假绿」：③ realpath 校验同样能拦下外部路径（防御纵深）。
        # 实测拆①③后 removed=1，外部文件真被删 → 边界测试必须报错。
        "应导致 outside_path_refused / traversal_path_refused 失败（越界删除）",
    ),
    (
        "边界①失效：词法校验反转",
        """            if not self._path_under_any(path):
                logger.warning("【保种空间守护】跳过配置目录外的路径：%s", path)
                continue""",
        """            if self._path_under_any(path):
                logger.warning("【保种空间守护】跳过配置目录内的路径：%s", path)
                continue""",
        "应导致 inside_path_accepted / both_sides_deleted 失败",
    ),
    (
        "前缀陷阱：_path_under 改回朴素 startswith（兄弟目录被误认）",
        """        return norm == target or norm.startswith(target + "/")""",
        """        return norm.startswith(target)""",
        "应导致 sibling_prefix_not_recognized / same_name_prefix 失败",
    ),
    (
        "反斜杠未归一：路径分隔符混用导致前缀比较失效",
        """        norm = os.path.normpath(path).replace("\\\\", "/")
        target = os.path.normpath(target_dir).replace("\\\\", "/")""",
        """        norm = os.path.normpath(path)
        target = os.path.normpath(target_dir)""",
        "应导致 backslash_normalized 失败",
    ),

    # ---------------- 边界②：inode 复核 ----------------
    (
        "边界②失效：删除 inode 复核（竞态下删掉重建的新文件）",
        """            if (st.st_dev, st.st_ino) != key:
                logger.warning("【保种空间守护】inode 已变化，跳过（文件可能被重建）：%s", path)
                continue""",
        """            pass""",
        "应导致 recreated_same_name_not_deleted 失败",
    ),
    (
        "边界②组合穿透：S_ISREG + inode 复核同时旁路（目录被当文件删）",
        [
            ("""            if not stat.S_ISREG(st.st_mode):
                logger.warning("【保种空间守护】跳过非普通文件：%s", path)
                continue""", """            pass"""),
            ("""            if (st.st_dev, st.st_ino) != key:
                logger.warning("【保种空间守护】inode 已变化，跳过（文件可能被重建）：%s", path)
                continue""", """            pass"""),
        ],
        # 只拆 S_ISREG 会「假绿」：inode 复核以"inode 已变化"拦下目录
        # （目录 inode ≠ 传入的文件 inode）。两道一起拆才测得出 S_ISREG 的作用。
        "应导致 non_regular_file_not_deleted 失败（目录被删）",
    ),
    (
        "边界②放宽：文件不存在时不计为已处理（语义回退）",
        """            except FileNotFoundError:
                # 已不存在（可能被其它进程删除），视为已处理
                removed += 1
                continue""",
        """            except FileNotFoundError:
                continue""",
        "应导致 missing_file_counted_as_handled / one_side_missing 失败",
    ),

    # ---------------- 边界③：realpath 逃逸 ----------------
    (
        "边界③失效：删除 realpath 校验（符号链接可逃逸）",
        """            if not self._path_under_any(os.path.realpath(path)):
                logger.warning("【保种空间守护】realpath 越界，跳过：%s", path)
                continue""",
        """            pass""",
        "应导致 symlink_escape 相关测试失败",
    ),
    (
        "索引改用 stat 跟随链接（symlink 被误认为普通文件）",
        """                        st = os.lstat(fpath)""",
        """                        st = os.stat(fpath)""",
        "应导致 index_skips_symlink_entries / symlink_to_dir 失败",
    ),

    # ---------------- 索引与统计 ----------------
    (
        "系统目录排除失效（@eaDir / #recycle 被遍历）",
        """                dirs[:] = [d for d in dirs if not d.startswith("@") and d != "#recycle"]""",
        """                pass""",
        "应导致 syno_dirs_skipped / recycle_skipped 失败",
    ),
    (
        "inode 去重失效：同一文件两侧各计一次（预计释放量虚高）",
        """                    key = (st.st_dev, st.st_ino)""",
        """                    key = (st.st_dev, st.st_ino, len(files))""",
        "应导致 size_counted_once_per_inode / both_sides_deleted 失败",
    ),
    (
        "路径映射未登记双侧（只登记代表路径）",
        """                    paths = ino_paths.setdefault(key, [])
                    if fpath not in paths:
                        paths.append(fpath)""",
        """                    ino_paths.setdefault(key, [fpath])""",
        "应导致 index_reports_all_paths_for_key / both_sides_deleted 失败",
    ),

    # ---------------- 保守返回：改成乐观默认 ----------------
    (
        "保守返回失效：无任何依据时乐观认为可删种",
        """        if records_checked == 0 and not (cand and cand.get("path")):
            # 完全没有任何可复核的依据：无从判定，保守保留种子
            return False""",
        """        if records_checked == 0 and not (cand and cand.get("path")):
            return True""",
        "应导致 test_blank_record_path_is_inconclusive_and_kept / "
        "test_no_record_at_all_with_real_files_kept 失败",
    ),
    (
        "保守返回失效：查询异常时乐观认为可删种",
        """            except Exception as err:
                logger.error("【保种空间守护】查询种子文件记录失败（%s）：%s", hash_str, err)
                return False""",
        """            except Exception as err:
                logger.error("【保种空间守护】查询种子文件记录失败（%s）：%s", hash_str, err)
                pass""",
        "应导致 test_record_query_error_keeps_seed 失败",
    ),
    (
        "物理复核失效：候选路径不被查验（退回只信记录，会误删完好种子）",
        """        if cand is not None:
            content_path = str(cand.get("path") or "").strip()
            if content_path and os.path.exists(content_path):""",
        """        if False:
            content_path = str(cand.get("path") or "").strip()
            if content_path and os.path.exists(content_path):""",
        "应导致 test_real_files_alive_with_stale_record_kept / "
        "test_blank_record_with_real_files_kept / test_nested_subdir_with_files_kept 失败",
    ),
    (
        "下载器异常未被吞掉（联动异常冒泡到主流程）",
        """        except Exception as err:
            logger.error("【保种空间守护】联动删除种子失败（%s）：%s", hash_str, err)
            return False""",
        """        except Exception as err:
            logger.error("【保种空间守护】联动删除种子失败（%s）：%s", hash_str, err)
            raise""",
        "应导致 delete_torrent_exception_contained 失败",
    ),
    (
        "删种时连带删文件（delete_file=True，双重删除）",
        """            ok = module.remove_torrents(
                hashs=[hash_str] if isinstance(hash_str, str) else hash_str,
                delete_file=False,
            )""",
        """            ok = module.remove_torrents(
                hashs=[hash_str] if isinstance(hash_str, str) else hash_str,
                delete_file=True,
            )""",
        "应导致 delete_torrent_passes_delete_file_false 失败",
    ),
    (
        "联动开关失效：dry_run 仍执行联动",
        """        if dry_run or not deleted_paths or not self._linkage_enabled():
            return stats""",
        """        if not deleted_paths or not self._linkage_enabled():
            return stats""",
        "应导致 linkage_noop_on_dry_run 失败",
    ),

    # ---------------- 目录配置解析 ----------------
    (
        "嵌套剔除失效（子目录重复遍历 → 重复计数与归属歧义）",
        """        return [
            d for d in dirs
            if not any(d != other and d.startswith(other + os.sep) for other in dirs)
        ]""",
        """        return dirs""",
        "应导致 nested_removed / nested_reverse_order 失败",
    ),
    (
        "去重失效（同一目录重复清理）",
        """            if path not in seen:
                seen.add(path)
                dirs.append(path)""",
        """            dirs.append(path)""",
        "应导致 duplicates_removed 失败",
    ),
    (
        "注释行未跳过（# 开头的目录会被当成真实路径）",
        """            if not line or line.startswith("#"):
                continue""",
        """            if not line:
                continue""",
        "应导致 blank_and_comment_skipped 失败",
    ),

    # ---------------- DSM 索引残片清理 ----------------
    (
        "残片清理放宽：不校验真实文件是否存在（误删在用索引）",
        """            if os.path.exists(os.path.join(dir_path, entry)):
                continue""",
        """            pass""",
        "应导致 index_for_existing_file_kept 失败",
    ),
    (
        "残片清理放宽：不校验白名单（误删用户数据）",
        """            if not self._is_syno_meta_tree(entry_path):
                logger.debug("【保种空间守护】跳过非 DSM 索引残片：%s", entry_path)
                continue""",
        """            pass""",
        "应导致 user_data_in_index_kept / unknown_entry_skipped 失败",
    ),
    (
        "空 @eaDir 未回收（父目录永远无法 rmdir）",
        """        try:
            if os.path.isdir(ea_dir) and not os.listdir(ea_dir):
                os.rmdir(ea_dir)
        except OSError:
            pass""",
        """        pass""",
        "应导致 empty_ea_dir_removed 失败",
    ),

    # ---------------- 空目录清理边界 ----------------
    #
    # 注意：``current != owner`` 这一条是**冗余防御**——`owner` 自身不满足
    # ``startswith(owner + os.sep)``，循环本来就停得住。移除它属于**等价变异**，
    # 不能作为有效变异体（测不出任何东西）。这里改为攻击真正决定范围的
    # ``_owner_dir_of`` 判定，以及真正的越界风险点。
    (
        "空目录清理越界：owner 一律取父目录（配置外目录也会被清理）",
        """        owner: Optional[str] = None
        for target in (self._active_dirs or self._target_dirs):
            normalized = os.path.normpath(target)
            if path == normalized or path.startswith(normalized + os.sep):
                if owner is None or len(normalized) > len(owner):
                    owner = normalized
        return owner""",
        """        # 变异：不管配不配置，一律把父目录当作 owner
        return os.path.dirname(os.path.normpath(path)) or None""",
        "应导致 outside_path_untouched / prunes_up_to_but_not_config_dir 失败",
    ),
    (
        "空目录清理越界：不校验归属（owner 为 None 也继续清理）",
        """        owner = self._owner_dir_of(start)
        if not owner:
            # 不属于任何配置目录，不动它
            return
        current = start
        while current and current != owner and current.startswith(owner + os.sep):""",
        """        owner = self._owner_dir_of(start) or start
        current = start
        while current:""",
        "应导致 outside_path_untouched 失败（配置外目录被清理）",
    ),
    (
        "空目录清理越界：允许删除配置目录本身",
        """        owner = self._owner_dir_of(start)
        if not owner:
            # 不属于任何配置目录，不动它
            return
        current = start
        while current and current != owner and current.startswith(owner + os.sep):""",
        """        owner = os.path.dirname(self._owner_dir_of(start) or start)
        current = start
        while current and current != owner and current.startswith(owner + os.sep):""",
        "应导致 prunes_up_to_but_not_config_dir 失败（配置目录被删）",
    ),
]


def run_tests():
    """跑全部测试，返回 (通过, 摘要)。

    必须从 ``plugins.v2``（插件目录的**父目录**）发起，并把「桩宿主」与
    「plugins.v2」同时放进 PYTHONPATH：插件在本地源仓库中是包
    （目录名 `seedspaceguard` + `__init__.py`），pytest 需以
    ``seedspaceguard/tests`` 的相对形态收集，包名才解析正确；桩宿主则
    必须优先于真实 site-packages，否则会 ModuleNotFoundError。
    """
    env = dict(os.environ)
    stub = os.path.join(PLUGIN_DIR, "tests", "_stub_host")
    env["PYTHONPATH"] = os.pathsep.join(
        [stub, PLUGINS_V2] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "seedspaceguard/tests/", "-q",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=PLUGINS_V2, env=env, capture_output=True, text=True,
    )
    output = proc.stdout + proc.stderr
    tail = "\n".join(output.strip().splitlines()[-3:])
    return proc.returncode == 0, tail


def main():
    # 原文读入内存，全程以它为基准，结束再写回（不依赖 /tmp 残留文件）。
    # 旧版用固定路径的 /tmp 备份：若该文件恰是历史遗留的旧版本，
    # 会把源码静默打回旧版；且残留文件会跨脚本互相干扰。
    if not os.path.exists(TARGET):
        print(f"❌ 找不到目标源码：{TARGET}")
        return 1
    source = open(TARGET, encoding="utf-8").read()
    try:
        return _run(source)
    finally:
        # 关键：必须无条件还原。
        # 本脚本会直接覆写插件源码，一旦中途抛异常（如解包错误、语法错误）
        # 而没走到还原那一步，源码就会**永久停留在变异状态**——后续所有测试
        # 都在被污染的源码上跑，表现为"基线即失败"，极易误判为测试坏了。
        # （本次开发中真实踩过一次：框架解包错误导致 7 个测试失败。）
        with open(TARGET, "w", encoding="utf-8") as handle:
            handle.write(source)


def _run(source):
    base_ok, base_tail = run_tests()
    print(f"基线：{'✅ 全部通过' if base_ok else '❌ 基线即失败'}")
    if not base_ok:
        print(base_tail)
        return 1

    caught = escaped = skipped = 0
    escaped_names = []

    print(f"\n{'='*70}\n变异测试（边界/安全，共 {len(MUTANTS)} 个变异体）\n{'='*70}")
    for idx, entry in enumerate(MUTANTS, 1):
        # 兼容两种条目写法：
        #   (name, old, new, expect)                  —— 单段替换（沿用旧格式）
        #   (name, [(old, new), (old2, new2)], expect) —— 多段替换（"组合穿透"）
        # "组合穿透"是必须的：本插件有防御纵深，只拆一道防线往往会被另一道
        # 兜住，测出的"全绿"是假象（实测：只拆词法校验，realpath 仍拦得住；
        # 拆①③后才真正发生越界删除）。表达不了组合穿透的框架会漏掉真盲区。
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
