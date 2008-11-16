# MoviePilot-Plugins

MoviePilot 自用插件市场，可直接作为插件市场源添加：

```
https://github.com/zkmydgth/MoviePilot-Plugins
```

---

## 插件列表

| 插件 | 用途 | 适用版本 |
| --- | --- | --- |
| [**115网盘STRM助手**](#115网盘strm助手p115strmhelper) | 115 网盘 STRM 生成、302 跳转、转移整理一条龙 | MoviePilot >= 3.0.0 |
| [**配置备份**](#配置备份configbackup) | 定时备份系统配置、数据库与插件数据，支持一键还原 | MoviePilot >= 3.0.0 |
| [**保种空间守护**](#保种空间守护seedspaceguard) | 空间不足时自动清理保种最久的资源，避免 H&R | MoviePilot >= 3.0.0 |

> 以上为 **V3 插件线**（源码在 `plugins.v3/`），仅支持 MoviePilot >= 3.0.0。
> V2 插件线已停止更新，相关说明见文末[附录](#附录v2-插件线已停更)，仅供存档查阅。

---

## 115网盘STRM助手（P115StrmHelper）

115 网盘 STRM 生成一条龙服务，覆盖 STRM 生成、302 跳转、转移整理、媒体库同步删除等完整链路。

| 项目 | 说明 |
| --- | --- |
| 标签 | 云盘 |
| 适用版本 | MoviePilot >= 3.0.0 |
| 源码目录 | `plugins.v3/p115strmhelper` |

> ⚠️ **请勿同时订阅上游仓库**：上游 [DDSRem-Dev/MoviePilot-Plugins](https://github.com/DDSRem-Dev/MoviePilot-Plugins) 的 V2 版插件 ID 同为 `P115StrmHelper`，同时订阅会导致插件被反复覆盖，且数据库结构不兼容。

### 功能特性

- **STRM 生成**：全量 / 增量生成，支持多账号、多目录、媒体库分类
- **302 跳转服务**：Open / Cookie 两种模式，供 Emby / Jellyfin 直连播放；同键并发请求做**单飞合并**，仅向 115 请求一次，避免缓存雪崩
- **多端播放**（账号副本）：同一文件多端同时播放时自动创建副本，规避 115 单文件并发限制
- **转移整理**：接管 MoviePilot 整理流程，支持 115 内部移动/复制、命名识别、失败重试
- **媒体库同步删除**：媒体服务器删除媒体时联动清理网盘文件与 STRM，含目录安全防护
- **FUSE 挂载**：把 115 网盘挂载为本地目录，供扫描与播放
- **分享 STRM 清理**：扫描分享链接中的冗余文件，支持批量清理与缺失媒体检测
- **本地 STRM 备份**：本地生成 STRM，不依赖网盘
- **HDHive 资源获取**：从 HDHive 站点获取资源并入库
- **侧栏仪表盘**：STRM 同步记录、运行状态、同步删除历史、网盘整理、全量同步五个面板

### 使用说明

1. 安装并启用插件后，在**配置页**填写 115 账号（扫码授权）与 STRM 生成目录
2. 按需开启 **302 跳转服务**，把地址填入 Emby / Jellyfin 的代理设置
3. 需要历史全量补档时点**全量同步**，日常新增走**增量同步**（自动触发）
4. 主界面左侧导航可进入**115助手仪表盘**查看运行状态

### 注意事项

- 本插件为主版本 3.x，与上游 V2 版本（2.x）**数据不互通**，不建议在 V2 环境安装
- 在 **MoviePilot V2** 上因 `system_version` 要求 `>=3.0.0`，该插件不会出现在插件市场列表中——这是版本约束的正常表现，**不是发布故障**
- 插件数据库迁移锚点、事件队列名沿用上游命名，便于从上游 V2 迁移时保留既有数据

---

## 配置备份（ConfigBackup）

定时备份 MoviePilot 系统配置、数据库及插件配置到指定目录，支持保留数量自动清理、手动触发和一键还原。

| 项目 | 说明 |
| --- | --- |
| 标签 | 系统工具, 备份 |
| 适用版本 | MoviePilot >= 3.0.0 |
| 源码目录 | `plugins.v3/configbackup` |
| 数据库要求 | PostgreSQL（10+，含 18.x） |

### 功能特性

- **定时自动备份**：按 cron 规则定期备份
- **备份内容**：
  - PostgreSQL 数据库（完整逻辑导出，含表结构、数据、索引与序列）
  - 系统配置文件（app.env、category.yaml、user.db、cookies 等）
  - 插件配置与数据
  - 自定义附加路径（按清单回原路径还原）
- **保留清理**：仅保留最近 N 份备份，旧备份自动清理，避免磁盘膨胀
- **两阶段确认还原**：选中备份 → 页面确认后执行还原，还原前自动先备份当前状态作为安全网，支持中途取消
- **备份目录灵活配置**：支持下拉候选 + 自由输入
- **完成通知**：备份/还原结果站内消息推送

### 使用说明

1. 在配置页设置**备份目录**与**保留份数**，配置 cron 定时规则
2. 需要立即备份时，点页面上的**手动备份**按钮
3. 还原时在列表选中目标备份 → 点【还原】→ 页面确认后执行

> ⚠️ **数据库备份/还原依赖 MoviePilot 以 PostgreSQL 运行**（`DB_TYPE=postgresql`），插件直连主库导出，无需外部 `pg_dump` 工具。
> 若 MoviePilot 使用非 PostgreSQL（如 SQLite/MySQL），将自动跳过数据库备份与还原，仅备份配置与插件数据。

---

## 保种空间守护（SeedSpaceGuard）

存储空间不足时，自动清理保种目录中「保种最久」的资源（种子 + 文件），避免触发 H&R。

| 项目 | 说明 |
| --- | --- |
| 标签 | 存储管理 |
| 适用版本 | MoviePilot >= 3.0.0 |
| 源码目录 | `plugins.v3/seedspaceguard` |

### 功能特性

- **空间阈值触发**：定期检查卷剩余空间，低于设定阈值时自动开始清理
- **多目录清理**：「保种/清理目录」为多行文本框，**每行填写一个绝对路径**，支持 `#` 注释行，自动去重并剔除互为嵌套的目录
- **两种清理模式**：
  - 种子级（seed）：直接删除下载器中最老的已完成种子（连带文件），一步到位不留红种
  - 仅文件（file）：仅删除文件，**由本插件自行处理硬链接**，无需依赖外部插件
- **硬链接自动双侧删除（仅文件模式）**：按 `(st_dev, st_ino)` 自动识别文件身份，同一 inode 在多目录下的所有副本一次性删净，避免「删一侧留一侧、空间一点没释放」的假删除
- **目标下载器可限定（种子级）**：下拉多选，自动读取 MoviePilot 已启用下载器，不选 = 全部
- **空壳种子自动回收**：每轮清理前先扫一遍「文件已全部删除、种子却仍留在下载器里做种」的空壳并回收，**不删除任何文件**
- **删到达标为止（按缺口预选）**：按保种时间从早到晚清理，直到剩余空间恢复至阈值以上；删除后轮询等待空间实际释放再复核
- **空间未释放即停（宁可少删）**：删除后按字节精度核对实际释放量，若空间确实没有释放（可能存在快照引用或目录外硬链接），**立即停止本轮清理并告警**
- **保护机制**：
  - 最近 N 天添加的种子/文件不清理（保护期）
  - 支持按后缀保护指定文件（如 `*.part`、`*.!qb` 等未完成文件）
  - 自动跳过 DSM 系统目录（`@eaDir` 等）与回收站
- **DSM 索引残片自动清理**：清除 `@eaDir` 中对应真实文件已不存在的索引残片（双重白名单校验，不会误删用户数据）
- **联动清理（可选，默认全关）**：
  - **联动删除种子**：删除文件后顺带删除对应下载器种子。**仅文件模式下有严格前置条件——必须该种子下的所有文件都已删除，才会删除该种子**
  - **删除转移记录**：先按目标路径匹配，未命中再按源路径匹配
- **通知**：清理结果站内消息推送；定时触发沿用「无动作即静默」，**手动与命令触发一律推送结果**

### 使用说明

1. 在配置页填写**保种/清理目录**（每行一个绝对路径）、**空间阈值**与**保护期天数**
2. 选择**清理模式**（种子级 / 仅文件）
3. 先点**试运行**预览「将删哪些、预计释放多少空间」，确认无误后再正式清理
4. 配置 cron 定时规则，或使用命令 `/seedguard` 手动触发

> 💡 **清理范围是「除保护后缀外全部删除」**：配置目录下所有文件均纳入清理候选，不区分文件类型。「保护文件后缀」是**唯一**的保留手段，需要保留的文件务必填入该项（例如正在下载的 `*.part`）。

---

## 使用方式

在 MoviePilot「设置 → 插件市场」中添加本仓库地址作为市场源，即可搜索安装上述插件：

```
https://github.com/zkmydgth/MoviePilot-Plugins
```

如果本文档信息与插件实际表现不符，以插件仓库代码为准。

---

## 附录：V2 插件线（已停更）

> ⚠️ **V2 插件线已停止更新**，以下内容仅作存档查阅。
> 新功能与修复只进入 V3 线（`plugins.v3/`），V2 源码保留在 `plugins.v2/` 供既有用户安装，但不再接收更新。

### V2 / V3 双线说明

本仓库同时维护 **MoviePilot V2** 与 **MoviePilot V3** 两条**互相独立**的插件线：

| 维护线 | 源码目录 | 清单文件 | 宿主要求 | 状态 |
| --- | --- | --- | --- | --- |
| V2 | `plugins.v2/<插件>` | `package.v2.json` | MoviePilot 2.x | ⛔ 已停更 |
| V3 | `plugins.v3/<插件>` | `package.v3.json` | MoviePilot >= 3.0.0 | ✅ 维护中 |

两条线是**两个各自演进的版本**，各有独立的更新记录（详见仓库 [Releases](https://github.com/zkmydgth/MoviePilot-Plugins/releases) 页），不能互相覆盖：

- 同一插件 ID 在 V2 与 V3 上的版本号互不相关。
- V3 线要求 `>=3.0.0`，因此在 MoviePilot V2 上不会出现在插件市场列表中——**这是正常的版本约束表现，不是发布故障。**
- 发布由 `.github/workflows/plugins-release.yml` 驱动，触发路径包括：`package*.json`、`plugins/**`、`plugins.v2/**`、`plugins.v3/**`、`.github/scripts/verify_plugin_versions.py` 以及该 workflow 自身。
- 工作流第一步是 **版本一致性校验**（`.github/scripts/verify_plugin_versions.py`），要求：
  1. `version.py` 中定义了 `VERSION`；
  2. 该 `VERSION` 与 `package.v2.json` / `package.v3.json` 里的 `version` **完全一致**；
  3. `__init__.py` 中必须写作 `plugin_version = VERSION`，**禁止硬编码版本号字符串**。
- 以上任一条件不满足，工作流会在校验阶段直接失败，不会产出 Release —— 这是为了避免「清单版本已升、插件类属性仍是旧值」导致用户端**一直提示有更新**但装不上新版本。
- 改完代码后必须同步更新对应清单里的 `version`，否则发布工作流不会触发。

<details>
<summary>📜 保种空间守护（SeedSpaceGuard）V2 版</summary>

| 项目 | 说明 |
| --- | --- |
| 适用版本 | MoviePilot >= 2.12.0 |
| 源码目录 | `plugins.v2/seedspaceguard` |
| 发布状态 | 已停更 |

V2 版功能与 V3 版一致，区别仅在宿主 API 写法（`ModuleManager` / `ServiceConfigHelper` 等 V3 已移除的接口）。

#### V3 适配说明

V3 版功能与 V2 版完全一致，改动全部集中在宿主机 API 适配上：

| 原 V2 依赖 | V3 替代 |
| --- | --- |
| `app.core.event` | `app.sdk.events` |
| `app.core.module.ModuleManager` | `app.application.downloader.DownloaderHelper` |
| `app.helper.service.ServiceConfigHelper` | 同上 |
| `app.db.downloadhistory_oper` | `app.db.oper.downloadhistory` |
| `app.db.transferhistory_oper` | `app.db.oper.transferhistory` |
| `app.log` | `app.runtime.log` |
| `NotificationType` | `MessageType` |

> 其中下载器访问层的 `DownloaderType` 是纯 Enum（值是 `"Qbittorrent"` 首字母大写），而 `DownloaderConf.type` 是普通字符串（全小写），二者用 `==` 比较恒为 False。V3 版已加入归一化处理，否则所有依赖「下载器类型判定」的分支会静默全部失效。

</details>

<details>
<summary>📜 配置备份（ConfigBackup）V2 版</summary>

| 项目 | 说明 |
| --- | --- |
| 适用版本 | MoviePilot >= 2.0.0 |
| 源码目录 | `plugins.v2/configbackup` |
| 发布状态 | 已停更 |

V2 版功能与 V3 版一致，区别仅在宿主 API 写法。

#### V3 适配说明

| 原 V2 依赖 | V3 替代 |
| --- | --- |
| `app.core.config` | `app.runtime.config` |
| `app.helper.directory` | `app.application.directory` |
| `app.log` | `app.runtime.log` |
| `app.utils.string` | `app.sdk.string` |
| `NotificationType` | `MessageType` |

</details>
