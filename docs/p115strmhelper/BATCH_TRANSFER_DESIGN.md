# 115网盘批量整理模块设计文档

## 目录

- [概述](#概述)
- [设计思路](#设计思路)
- [架构设计](#架构设计)
- [与 MoviePilot 的关系](#与-moviepilot-的关系)
- [核心组件](#核心组件)
- [处理流程](#处理流程)
- [关键实现细节](#关键实现细节)
- [数据流](#数据流)
- [与 MoviePilot 的差异](#与-moviepilot-的差异)
- [注意事项](#注意事项)

---

## 概述

本模块实现了 115网盘到115网盘的**批量整理功能**，通过 Monkey Patching 拦截 MoviePilot 的整理流程，将原本的单文件逐个处理改为批量处理，大幅提升整理效率。

### 核心目标

1. **性能优化**：将多个文件的移动/复制操作合并为批量 API 调用
2. **功能对等**：保持与 MoviePilot 原整理逻辑的 100% 功能对等
3. **无缝集成**：通过补丁机制，对 MoviePilot 透明，不影响其他存储模块

---

## 设计思路

### 为什么需要批量处理？

MoviePilot 的原始整理逻辑是**单文件逐个处理**：
- 每个文件独立调用 API
- 每个文件独立记录历史
- 每个文件独立发送通知

对于 115网盘这种支持批量操作的存储，这种方式效率低下：
- 100 个文件需要 100 次 API 调用
- 批量操作可以合并为 1-2 次 API 调用

### 设计原则

1. **最小侵入**：通过 Monkey Patching 拦截，不修改 MoviePilot 源码
2. **功能完整**：实现 MoviePilot 原逻辑的所有功能点
3. **状态同步**：确保 MoviePilot 的 `jobview` 状态与插件处理结果一致
4. **错误处理**：任何步骤失败都能正确记录并阻止后续处理

---

## 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                    MoviePilot TransferChain                  │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  __handle_transfer(task)                            │   │
│  │  ├─ 识别媒体信息                                     │   │
│  │  ├─ 获取目标目录                                     │   │
│  │  └─ [被拦截] → TransferChainPatcher                 │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              TransferChainPatcher (补丁层)                    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  _patched_handle_transfer()                          │   │
│  │  ├─ 执行 MoviePilot 的识别/目录逻辑                   │   │
│  │  ├─ 判断：115 → 115？                                │   │
│  │  └─ 是 → 创建 PluginTransferTask                     │   │
│  │      └─ 加入 TransferTaskManager 队列                │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│            TransferTaskManager (任务队列管理)                 │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  add_task(task)                                      │   │
│  │  ├─ 加入 _pending_tasks 队列                          │   │
│  │  ├─ 重置延迟定时器（10秒）                            │   │
│  │  └─ 达到 batch_max_size → 立即触发                    │   │
│  │                                                       │   │
│  │  _trigger_batch_process()                             │   │
│  │  ├─ 取出所有待处理任务                                │   │
│  │  ├─ 标记 _processing = True                           │   │
│  │  └─ 调用 TransferHandler.process_batch()              │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              TransferHandler (批量处理执行器)                  │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  process_batch(tasks)                                │   │
│  │  ├─ _discover_related_files()      # 发现字幕/音轨    │   │
│  │  ├─ _batch_create_directories()     # 批量创建目录     │   │
│  │  ├─ _batch_move_or_copy()          # 批量移动/复制    │   │
│  │  ├─ _batch_rename_files()          # 批量重命名       │   │
│  │  ├─ _record_history()               # 记录历史         │   │
│  │  └─ _batch_delete_empty_dirs()      # 删除空目录       │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              MoviePilot JobManager (状态同步)                │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  finish_task() / fail_task()                        │   │
│  │  is_finished() / is_done()                          │   │
│  │  remove_job()                                        │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 与 MoviePilot 的关系

### 拦截点

**位置**：`TransferChain.__handle_transfer`

**时机**：在 MoviePilot 完成以下步骤后拦截：
1. ✅ 媒体信息识别（`mediainfo`）
2. ✅ 目标目录计算（`target_directory`）
3. ✅ 任务状态标记（`running_task`）

**拦截条件**：
- 源存储 = 目标存储 = `115网盘Plus`
- 文件类型 = `file`（目录/蓝光原盘回退到原方法）
- **字幕/音频文件**：直接忽略（标记为完成但不加入队列），因为会跟随主文件一起处理

### 职责划分

| 组件 | 职责 | MoviePilot 原逻辑 |
|------|------|------------------|
| **TransferChainPatcher** | 拦截判断、任务转换 | 不涉及 |
| **TransferTaskManager** | 任务队列、批量触发 | `TransferChain._queue` |
| **TransferHandler** | 批量文件操作 | `TransHandler.transfer_media` |
| **JobManager** | 任务状态管理 | MoviePilot 原生 |
| **TransferHistoryOper** | 历史记录 | MoviePilot 原生 |

### 关键同步点

1. **任务状态同步**：
   - 拦截时：调用 `chain.jobview.running_task(task)`
   - 成功时：调用 `chain.jobview.finish_task(mp_task)`
   - 失败时：调用 `chain.jobview.fail_task(mp_task)`

2. **队列清理**：
   - 按媒体组检查 `is_finished()` / `is_done()`
   - 调用 `chain.jobview.remove_job(mp_task)` 移除已完成组

3. **历史记录**：
   - 使用 MoviePilot 的 `TransferHistoryOper.add_success/add_fail`
   - 保持与 MoviePilot 历史记录格式一致

---

## 核心组件

### 1. TransferChainPatcher

**文件**：`plugins.v2/p115strmhelper/patch/transfer_chain.py`

**职责**：
- Monkey Patching `TransferChain.__handle_transfer`
- 判断是否应该拦截（115 → 115）
- 转换 MoviePilot `TransferTask` → 插件 `TransferTask`
- 加入批量队列

**关键方法**：
- `enable()`: 启用补丁
- `_patched_handle_transfer()`: 拦截后的处理逻辑
- `_should_intercept()`: 判断是否拦截
- `_compute_target_path()`: 使用 MoviePilot 逻辑计算目标路径

### 2. TransferTaskManager

**文件**：`plugins.v2/p115strmhelper/helper/transfer/task.py`

**职责**：
- 管理待处理任务队列
- 延迟批量触发（默认 10 秒）
- 批量大小控制（默认 100）

**关键特性**：
- **延迟触发**：新任务到达时重置定时器，10秒后触发
- **批量上限**：达到 `batch_max_size` 立即触发
- **连续处理**：批量处理完成后，自动检查队列并触发下一批

**关键方法**：
- `add_task()`: 添加任务到队列
- `_trigger_batch_process()`: 触发批量处理
- `_reset_timer()`: 重置延迟定时器

### 3. TransferHandler

**文件**：`plugins.v2/p115strmhelper/helper/transfer/handler.py`

**职责**：
- 执行批量文件操作
- 记录历史
- 同步 MoviePilot 状态

**关键方法**：
- `process_batch()`: 批量处理主流程
- `_discover_related_files()`: 发现字幕/音轨
- `_batch_create_directories()`: 批量创建目录
- `_batch_move_or_copy()`: 批量移动/复制
- `_batch_rename_files()`: 批量重命名
- `_record_history()`: 记录历史
- `_batch_delete_empty_dirs()`: 删除空目录

**辅助方法**：
- `_create_mp_task()`: 创建 MoviePilot TransferTask
- `_group_tasks_by_media()`: 按媒体分组
- `_remove_completed_jobs()`: 移除已完成任务组

### 4. TransferTask (插件)

**文件**：`plugins.v2/p115strmhelper/schemas/transfer.py`

**数据结构**：
```python
@dataclass
class TransferTask:
    fileitem: FileItem              # 源文件
    target_path: Path              # 目标路径
    mediainfo: MediaInfo           # 媒体信息
    meta: MetaBase                 # 元数据
    transfer_type: str             # 整理类型 (move/copy)
    overwrite_mode: Optional[str]  # 覆盖模式
    related_files: List[RelatedFile]  # 关联文件（字幕/音轨）
    # ... 其他字段
```

**与 MoviePilot TransferTask 的区别**：
- 插件版本：包含 `target_path`（已计算好的完整路径）
- MoviePilot 版本：包含 `target_directory`（目录配置，需要计算路径）

---

## 处理流程

### 完整流程

```
1. MoviePilot 调用 TransferChain.__handle_transfer(task)
   │
   ├─ [被拦截] TransferChainPatcher._patched_handle_transfer()
   │   ├─ 执行 MoviePilot 的识别/目录逻辑（复用）
   │   ├─ 判断：115 → 115？
   │   └─ 是 → 创建 PluginTransferTask
   │       └─ TransferTaskManager.add_task(plugin_task)
   │
   └─ [未拦截] 继续 MoviePilot 原逻辑

2. TransferTaskManager 队列管理
   ├─ add_task() → 加入队列
   ├─ 延迟 10 秒或达到上限 → _trigger_batch_process()
   └─ 调用 TransferHandler.process_batch(tasks)

3. TransferHandler 批量处理
   ├─ _discover_related_files()      # 发现字幕/音轨
   │   └─ 失败 → 所有任务标记失败，停止
   │
   ├─ _batch_create_directories()     # 批量创建目录
   │   └─ 失败 → 受影响任务标记失败，停止
   │
   ├─ _batch_move_or_copy()          # 批量移动/复制
   │   ├─ 按目标目录分组
   │   ├─ 批量检查文件存在性
   │   ├─ 处理覆盖模式（always/size/latest/never）
   │   ├─ 批量删除已存在文件（如需要）
   │   ├─ 批量删除版本文件（latest 模式，目标文件不存在时，循环外批量处理）
   │   ├─ 批量移动/复制
   │   └─ 失败 → 受影响任务标记失败，继续
   │
   ├─ _batch_rename_files()          # 批量重命名
   │   └─ 失败 → 受影响任务标记失败，继续
   │
   ├─ _record_history()              # 记录历史
   │   ├─ 成功任务：add_success()
   │   │   ├─ finish_task()
   │   │   ├─ 发送 TransferComplete 事件（包含 transfer_history_id）
   │   │   ├─ 关联文件发送 SubtitleTransferComplete/AudioTransferComplete 事件（包含 transfer_history_id）
   │   │   ├─ 登记到 _success_target_files
   │   │   ├─ 关联文件独立写历史（每个文件一条，包含 transfer_history_id）
   │   │   └─ is_finished() → 发送通知/刮削
   │   │
   │   └─ 失败任务：add_fail()
   │       ├─ fail_task()
   │       ├─ 发送 TransferFailed 事件（包含 transfer_history_id）
   │       ├─ 关联文件发送 SubtitleTransferFailed/AudioTransferFailed 事件（包含 transfer_history_id）
   │       ├─ 关联文件独立写失败历史（每个文件一条，包含 transfer_history_id）
   │       └─ 发送失败通知
   │
   ├─ _batch_delete_empty_dirs()     # 删除空目录（仅 move 模式）
   │   ├─ 检查 is_success()
   │   ├─ 收集空目录（递归检查）
   │   └─ 批量删除
   │
   └─ _remove_completed_jobs()        # 移除已完成任务组
       ├─ 成功任务：is_finished() → remove_job()
       └─ 失败任务：is_done() → remove_job()

4. 批量处理完成后
   └─ TransferTaskManager 检查队列
       └─ 仍有待处理任务 → 立即触发下一批
```

### 错误处理流程

```
任何步骤失败：
├─ 记录失败原因
├─ 标记任务失败（fail_task）
├─ 记录失败历史（add_fail）
├─ 发送失败通知
└─ 阻止后续步骤（remaining_tasks 清空）

批量操作失败：
├─ 不再回退到逐个处理（避免 API 风控）
└─ 批量失败的任务统一标记为失败
```

---

## 关键实现细节

### 1. 任务状态同步

**问题**：确保 MoviePilot 的 `jobview` 状态与插件处理结果一致

**实现**：
- 拦截时：`chain.jobview.running_task(task)` - 标记为运行中
- 成功时：`chain.jobview.finish_task(mp_task)` - 标记为完成
- 失败时：`chain.jobview.fail_task(mp_task)` - 标记为失败
- 移除时：按媒体组检查 `is_finished()` / `is_done()`，然后 `remove_job()`

**关键点**：
- 使用 `MPTransferTask`（MoviePilot 的 TransferTask）与 JobManager 交互
- 按媒体组统一移除，避免重复调用

### 2. 批量操作优化

**目录创建**：
- 收集所有目标目录（包括关联文件的目录）
- 识别叶子目录（非其他目录的父目录）
- 批量创建（115 API 自动递归）

**文件移动/复制**：
- 按目标目录分组
- 批量检查文件存在性（每个目录只列出一次）
- 批量删除已存在文件（如需要）
- 批量删除版本文件（latest 模式，循环外批量处理，按目录分组）
- 批量移动/复制
- 失败时不回退到逐个处理（避免 API 风控）

**文件重命名**：
- 收集所有需要重命名的文件
- 批量重命名（使用 `p115client.tool.edit.update_name`）
- 失败时回退到逐个重命名

### 3. 覆盖模式处理

**支持的模式**：
- `always`: 总是覆盖
- `size`: 大覆盖小
- `latest`: 仅保留最新版本（删除其他版本）
- `never`: 不覆盖（跳过）

**实现**：
- 批量检查目标文件是否存在
- 根据 `overwrite_mode` 决定是否覆盖
- 附加文件（字幕/音轨）强制覆盖
- `latest` 模式：
  - 目标文件已存在：收集到批量删除列表，循环外统一删除
  - 目标文件不存在：收集到 `version_delete_tasks`，循环外调用 `_batch_delete_version_files()` 批量处理
  - 按目录分组，每个目录只执行一次 `list_files`，收集所有目标文件的季集信息后批量删除

### 4. 关联文件处理

**发现逻辑**：
- 按源目录分组任务
- 列出目录文件
- 匹配字幕文件（基于文件名、季集信息）
- 匹配音轨文件（基于文件名）

**命名规则**：
- 字幕：根据语言标识添加后缀（`.chi.zh-cn`, `.zh-tw`, `.eng`）
- 音轨：保持原扩展名
- 默认字幕：添加 `.default` 前缀

**历史记录**：
- 主视频：正常记录历史（包含 `transfer_history_id`）
- 关联文件：**独立写入历史记录**（每个文件一条，包含 `transfer_history_id`）
- 关联文件历史：`need_notify=False`, `need_scrape=False`（不触发通知/刮削）
- **失败时**：主文件和关联文件都会独立记录失败历史（与 MoviePilot 逻辑一致）

### 5. 空目录删除

**触发条件**：
- `transfer_type == "move"`
- `is_success() == True`（媒体组所有任务都成功）

**删除逻辑**：
- 递归检查父目录
- 检查目录是否为空（无文件）
- 检查目录是否包含媒体文件
- 不删除资源目录/媒体库目录结构内的目录
- 批量删除（按深度排序，深度大的先删除）

**注意**：
- 115→115 场景下，**不删除种子**（无下载器种子）

### 6. 任务移除逻辑

**成功任务**：
- 使用 `is_finished()` 检查（需要至少一个成功）
- 按媒体组统一移除

**失败任务**：
- 使用 `is_done()` 检查（不管成功还是失败）
- 按媒体组统一移除

**关键点**：
- 确保所有任务都调用 `finish_task()` / `fail_task()`
- 使用 `_remove_completed_jobs()` 统一处理

---

## 数据流

### 任务流转

```
MoviePilot TransferTask
    │
    ├─ [识别/目录计算] (MoviePilot 原逻辑)
    │
    └─ [拦截] TransferChainPatcher
        │
        └─ Plugin TransferTask
            │
            └─ TransferTaskManager._pending_tasks
                │
                └─ [批量触发] TransferHandler.process_batch()
                    │
                    ├─ 成功 → finish_task() → add_success()
                    └─ 失败 → fail_task() → add_fail()
```

### 状态同步

```
插件处理状态          MoviePilot JobManager
─────────────────────────────────────────────
拦截任务        →     running_task()
处理成功        →     finish_task()
处理失败        →     fail_task()
媒体组完成      →     is_finished() / is_done()
                    → remove_job()
```

---

## 与 MoviePilot 的差异

### 设计原则：保持代码简洁

**核心原则**：115→115 网盘整理场景下，某些 MoviePilot 功能**不需要**，因此**故意不实现**，以保持代码简洁。

### 已实现的差异

| 功能点 | MoviePilot | 插件实现 | 说明 |
|--------|-----------|---------|------|
| **文件操作** | 逐个处理 | 批量处理 | 性能优化 |
| **关联文件历史** | 独立记录 | ✅ 独立记录 | 已对齐 |
| **事件触发** | 仅主媒体文件 | 仅主媒体文件 | 已对齐 |
| **空目录删除** | 支持 | ✅ 支持 | 已实现 |

### 故意不实现的功能（115→115 场景不需要）

#### 1. 种子删除功能 ❌

**MoviePilot 实现**：
- 在 `is_success()` 时检查并删除下载器种子
- 使用 `_can_delete_torrent()` 判断是否可删除
- 调用 `remove_torrents()` 删除种子

**插件未实现原因**：
- **115→115 整理场景**：文件在 115 网盘内移动，**不涉及下载器**
- **无种子文件**：115 网盘不是下载器，不存在做种文件
- **代码已移除**：已删除所有种子删除相关代码（`_can_delete_torrent`, `remove_torrents`, `transfer_exclude_words`）

**影响**：无影响，115→115 场景下确实不需要

---

#### 2. transfer_completed 调用 ❌

**MoviePilot 实现**：
```python
if self.jobview.is_done(task):
    tasks = self.jobview.all_tasks()
    for t in tasks:
        if t.download_hash:
            self.transfer_completed(hashs=t.download_hash, downloader=t.downloader)
```

**插件未实现原因**：
- **115→115 整理场景**：文件在 115 网盘内移动，**不涉及下载器**
- **transfer_completed 作用**：标记下载器中的任务为"已整理"状态
- **115 网盘无此概念**：115 网盘不是下载器，不存在"已整理"状态标记

**影响**：无影响，115→115 场景下确实不需要

---

#### 3. StorageOperSelection 事件 ❌

**MoviePilot 实现**：
- 广播 `ChainEventType.StorageOperSelection` 事件
- 获取额外的源/目标存储操作对象（`source_oper`, `target_oper`）
- 支持存储插件扩展

**插件未实现原因**：
- **115→115 整理场景**：源和目标都是 115 网盘，**使用固定的 115 API**
- **不需要扩展**：直接使用 `P115Client` 进行文件操作，不需要存储插件扩展
- **代码更简洁**：避免事件广播和额外的抽象层

**影响**：无影响，115→115 场景下直接使用 115 API 更高效

---

### 已对齐的功能

#### 1. 事件触发条件 ✅

**MoviePilot 实现**：
- `TransferComplete` 事件：仅主媒体文件触发
- `MetadataScrape` 事件：仅主媒体文件触发
- `TransferFailed` 事件：仅主媒体文件触发

**插件实现**：
- 主视频文件：发送 `TransferComplete` / `TransferFailed` 事件（包含 `transfer_history_id`）
- 关联文件（字幕/音轨）：
  - 成功时：发送 `SubtitleTransferComplete` / `AudioTransferComplete` 事件（包含 `transfer_history_id`）
  - 失败时：发送 `SubtitleTransferFailed` / `AudioTransferFailed` 事件（包含 `transfer_history_id`）
  - 关联文件事件：`need_notify=False`, `need_scrape=False`（不触发通知/刮削）
- 刮削事件：仅主媒体文件触发 `MetadataScrape` 事件

**状态**：已对齐（与 MoviePilot 最新实现一致，每个文件类型都发送对应事件）

---

#### 2. 关联文件历史记录 ✅

**MoviePilot 实现**：
- 字幕/音轨文件作为独立任务处理
- 每个文件独立写入历史记录

**插件实现**：
- 成功时：使用 `_record_related_files_success_history()` 方法，为每个关联文件独立写入历史记录（包含 `transfer_history_id`）
- 失败时：在 `_record_fail()` 中为每个关联文件独立调用 `add_fail()` 记录失败历史（包含 `transfer_history_id`）
- `need_notify=False`, `need_scrape=False`（不触发通知/刮削）

**状态**：已对齐（与 MoviePilot 最新实现一致，成功和失败都独立记录）

---

#### 3. 任务状态管理 ✅

**MoviePilot 实现**：
- `running_task()`: 标记运行中
- `finish_task()`: 标记完成
- `fail_task()`: 标记失败
- `is_finished()`: 检查是否完成且有成功
- `is_done()`: 检查是否完成（不管成功失败）
- `remove_job()`: 移除任务组

**插件实现**：
- 完全对齐 MoviePilot 的状态管理逻辑
- 使用 `_remove_completed_jobs()` 统一处理移除

**状态**：已对齐

---

### 代码简洁性考虑

**设计原则**：
1. **只实现需要的功能**：115→115 场景不需要的功能不实现
2. **避免过度抽象**：直接使用 115 API，不需要存储操作抽象层
3. **减少依赖**：移除不必要的导入（如 `SystemConfigOper`, `SystemConfigKey`）

**已移除的代码**：
- ❌ 种子删除相关（`_can_delete_torrent`, `remove_torrents`, `transfer_exclude_words`）
- ❌ `SystemConfigOper` 和 `SystemConfigKey` 导入（仅用于种子删除判断）
- ❌ `transfer_completed()` 调用（115→115 无下载器）

**保留的代码**：
- ✅ `downloader` 和 `download_hash` 字段（历史记录需要，用于关联下载记录）
- ✅ 空目录删除（move 模式需要）

---

## 注意事项

### 1. 任务移除时机

**关键点**：
- 必须在所有任务都标记为 `completed` / `failed` 后才能移除
- 使用 `is_finished()`（成功）或 `is_done()`（完成）检查
- 按媒体组统一移除，避免重复调用

### 2. 批量处理中的新任务

**场景**：批量处理进行中，新任务不断加入队列

**处理**：
- 新任务加入时重置定时器
- 定时器触发时，如果正在处理则跳过
- **批量处理完成后，自动检查队列并触发下一批**

### 3. 失败任务处理

**原则**：
- 任何步骤失败，立即标记任务失败
- 阻止后续步骤（`remaining_tasks` 清空）
- 批量记录失败历史
- 按媒体组统一移除失败任务组

### 4. 关联文件历史

**实现**：
- 主视频：正常记录（触发通知/刮削，包含 `transfer_history_id`）
- 关联文件成功：独立记录（不触发通知/刮削，包含 `transfer_history_id`）
- 关联文件失败：独立记录失败历史（与 MoviePilot 逻辑一致，包含 `transfer_history_id`）
- 使用 `_record_related_files_success_history()` 方法（成功时）
- 失败时在 `_record_fail()` 中为每个关联文件独立调用 `add_fail()`

### 5. 115→115 特殊处理

#### 不涉及的功能（故意不实现，保持代码简洁）

**种子删除** ❌：
- **原因**：115→115 整理场景下，文件在 115 网盘内移动，不涉及下载器，无种子文件
- **代码状态**：已完全移除相关代码（`_can_delete_torrent`, `remove_torrents`, `transfer_exclude_words`）

**transfer_completed 调用** ❌：
- **原因**：115→115 整理场景下，无下载器，不需要标记下载器任务为"已整理"
- **代码状态**：未实现，保持代码简洁

**StorageOperSelection 事件** ❌：
- **原因**：115→115 整理场景下，直接使用 115 API，不需要存储插件扩展机制
- **代码状态**：未实现，避免不必要的抽象层

#### 保留的功能

**空目录删除** ✅：
- **原因**：move 模式下需要清理源目录
- **实现**：`_batch_delete_empty_dirs()` 方法

**历史记录字段** ✅：
- **原因**：`downloader` 和 `download_hash` 用于关联下载记录，即使 115→115 场景下也可能有下载历史
- **实现**：保留在 `TransferTask` 和 `add_success/add_fail` 调用中

---

## 文件结构

```
plugins.v2/p115strmhelper/
├── patch/
│   └── transfer_chain.py          # 补丁层：拦截 MoviePilot
├── helper/transfer/
│   ├── __init__.py
│   ├── task.py                    # 任务队列管理
│   ├── handler.py                 # 批量处理执行器
│   └── cache_updater.py           # 缓存管理
└── schemas/
    └── transfer.py                # 数据结构定义
```

---

## 关键配置

### TransferTaskManager 参数

- `batch_delay`: 批量等待时间（默认 10.0 秒）
- `batch_max_size`: 单批次最大任务数（默认 100）

### 触发条件

1. **延迟触发**：新任务到达后 10 秒触发
2. **立即触发**：队列达到 `batch_max_size` 立即触发
3. **连续触发**：批量处理完成后，队列仍有任务则立即触发下一批

---

## 调试建议

### 关键日志

- `【整理接管】检测到 115 → 115 整理任务`: 拦截成功
- `【整理接管】开始批量处理 N 个任务`: 批量处理开始
- `【整理接管】批量处理完成，成功: X 个，失败: Y 个`: 批量处理完成
- `【整理接管】已移除 N 个已完成的任务组`: 任务组移除成功

### 常见问题

1. **任务未移除**：
   - 检查是否所有任务都调用了 `finish_task()` / `fail_task()`
   - 检查 `is_finished()` / `is_done()` 的返回值
   - 检查是否按媒体组统一移除

2. **批量处理不触发**：
   - 检查 `TransferTaskManager._processing` 状态
   - 检查队列中是否有待处理任务
   - 检查定时器是否正常触发

3. **关联文件未写入历史**：
   - 检查 `task.related_files` 是否为空
   - 检查 `_record_related_files_success_history()` 是否被调用

---

## 版本兼容性

### MoviePilot 版本要求

- 支持 v2.8.0+ 的整理逻辑
- 关键依赖：
  - `TransferChain.__handle_transfer`
  - `JobManager` (finish_task, fail_task, is_finished, is_done, remove_job)
  - `TransferHistoryOper` (add_success, add_fail)
  - `TransHandler` (用于计算目标路径)

### 已知差异

- v2.9.5+ 引入了多线程整理，但插件通过批量处理已实现类似效果
- v2.9.5+ 的 `transfer_completed()` 调用在 115→115 场景下不需要

---

## 未来改进方向

### 性能优化

1. **文件存在性检查优化**：
   - 当前：每个目标目录批量列出文件
   - 优化：可以缓存结果，减少重复 API 调用

2. **批量操作失败处理**：
   - 当前：批量失败时不再回退到逐个处理（避免 API 风控）
   - 已优化：所有批量操作失败时统一标记为失败，不进行逐个重试

### 功能完善

1. **错误处理细化**：
   - 部分失败场景的处理可以更细化
   - 批量操作的原子性可以进一步优化

### 代码维护

1. **保持简洁**：
   - **不实现 115→115 场景不需要的功能**（种子删除、transfer_completed 等）
   - **避免过度抽象**（直接使用 115 API，不需要存储操作抽象层）
   - **减少不必要的依赖**（已移除 SystemConfigOper 等）

2. **与 MoviePilot 对齐**：
   - 关注 MoviePilot 新版本的功能变更
   - 只对齐 115→115 场景需要的功能
   - 不需要的功能保持不实现，保持代码简洁

---

## 相关文件

- **补丁层**：`plugins.v2/p115strmhelper/patch/transfer_chain.py`
- **任务管理**：`plugins.v2/p115strmhelper/helper/transfer/task.py`
- **处理执行**：`plugins.v2/p115strmhelper/helper/transfer/handler.py`
- **数据结构**：`plugins.v2/p115strmhelper/schemas/transfer.py`
- **MoviePilot 源码**：`/Users/rem/Documents/Git/MoviePilot/app/chain/transfer.py`

---

*最后更新：2026-01-27*
