# API STRM 生成功能文档

## 概述

API STRM 生成功能允许第三方开发者通过 HTTP API 调用，批量生成 STRM 文件。该功能提供了两种方式：

1. **通过文件信息生成**: 指定具体的文件信息（pick_code、id 或文件路径）生成 STRM
2. **通过文件夹路径批量生成**: 指定文件夹路径，系统自动遍历文件夹下的所有文件并生成 STRM

两种方式都支持通过配置路径映射自动确定本地生成路径，也支持在请求中直接指定路径。

## 前置条件

1. **插件已启用**：确保 p115strmhelper 插件已启用
2. **Cookie 已配置**：需要配置有效的 115 Cookie
3. **路径映射配置**（可选）：在插件配置中设置 `api_strm_config`，用于自动匹配本地路径

## API 端点

插件提供了两个 API 端点用于生成 STRM 文件：

1. **`/api_strm_sync_creata`**: 通过指定文件信息生成 STRM（支持单个文件或批量文件）
2. **`/api_strm_sync_create_by_path`**: 通过指定文件夹路径批量生成 STRM（自动遍历文件夹下的所有文件）

---

## 端点 1: 通过文件信息生成 STRM

### 基本信息

- **路径**: `/api/v1/plugin/P115StrmHelper/api_strm_sync_creata`
- **方法**: `POST`
- **认证**: Bearer Token（需要 MoviePilot API Key）
- **Content-Type**: `application/json`

### 请求 URL 格式

```
POST {server_url}/api/v1/plugin/P115StrmHelper/api_strm_sync_creata?apikey={APIKEY}
```

## 请求参数

### 请求体结构

```json
{
  "data": [
    {
      "id": 1234567890,
      "pick_code": "abc123def456ghi78",
      "name": "电影名称.mkv",
      "pan_path": "/我的资源/电影/电影名称.mkv",
      "sha1": "abc123def456...",
      "size": 2147483648,
      "local_path": "/media/movies/电影名称.mkv",
      "pan_media_path": "/我的资源/电影",
      "scrape_metadata": true,
      "media_server_refresh": true
    },
    {
      "pan_path": "/我的资源/电影/另一个电影.mkv"
    }
  ]
}
```

### 字段说明

#### 必需字段（至少提供以下之一）

- `id` (integer, 可选): 115 网盘文件 ID
- `pick_code` (string, 可选): 115 网盘文件 pickcode（17位字符串）
- `pan_path` (string, 可选): 文件在 115 网盘中的完整路径

> **注意**: `id`、`pick_code` 和 `pan_path` 至少需要提供一个。如果提供了 `id` 或 `pick_code`，系统会自动转换；如果只提供了 `pan_path`，系统会通过路径查询文件信息。

#### 可选字段

- `name` (string, 可选): 文件名称。如果不提供，将从 `pan_path` 中提取文件名
- `sha1` (string, 可选): 文件的 SHA1 值。如果不提供，系统会通过 API 查询
- `size` (integer, 可选): 文件大小（字节）。如果不提供，系统会通过 API 查询
- `local_path` (string, 可选): 本地生成 STRM 文件的目录路径。如果不提供，将根据 `api_strm_config` 配置自动匹配
- `pan_media_path` (string, 可选): 网盘媒体库根路径。如果不提供，将根据 `api_strm_config` 配置自动匹配
- `scrape_metadata` (boolean, 可选): 是否刮削元数据。如果不提供，使用插件配置中的 `api_strm_scrape_metadata_enabled` 默认值
- `media_server_refresh` (boolean, 可选): 是否刷新媒体服务器。如果不提供，使用插件配置中的 `api_strm_media_server_refresh_enabled` 默认值

### 路径映射机制

如果请求中未提供 `local_path` 或 `pan_media_path`，系统会根据插件配置中的 `api_strm_config` 进行自动匹配：

1. 系统会遍历配置的路径映射列表
2. 检查文件的 `pan_path` 是否以配置的 `pan_path` 开头
3. 如果匹配，使用对应的 `local_path` 和 `pan_path` 作为媒体库路径

**配置示例**:
```json
{
  "api_strm_config": [
    {
      "pan_path": "/我的资源/电影",
      "local_path": "/media/movies"
    },
    {
      "pan_path": "/我的资源/剧集",
      "local_path": "/media/tvshows"
    }
  ]
}
```

## 响应格式

### 成功响应

```json
{
  "code": 10200,
  "msg": "生成完成",
  "data": {
    "success": [
      {
        "id": 1234567890,
        "pick_code": "abc123def456ghi78",
        "name": "电影名称.mkv",
        "pan_path": "/我的资源/电影/电影名称.mkv",
        "sha1": "abc123def456...",
        "size": 2147483648,
        "local_path": "/media/movies",
        "pan_media_path": "/我的资源/电影"
      }
    ],
    "fail": [],
    "success_count": 1,
    "fail_count": 0
  }
}
```

### 错误响应

```json
{
  "code": 10400,
  "msg": "未传有效参数",
  "data": {
    "success": [],
    "fail": [],
    "success_count": 0,
    "fail_count": 0
  }
}
```

### 部分成功响应

```json
{
  "code": 10200,
  "msg": "生成完成",
  "data": {
    "success": [
      {
        "id": 1234567890,
        "pick_code": "abc123def456ghi78",
        "name": "电影名称.mkv",
        "pan_path": "/我的资源/电影/电影名称.mkv",
        "sha1": "abc123def456...",
        "size": 2147483648,
        "local_path": "/media/movies",
        "pan_media_path": "/我的资源/电影"
      }
    ],
    "fail": [
      {
        "id": 9876543210,
        "pick_code": "xyz789abc123def45",
        "name": "无效文件.txt",
        "pan_path": "/我的资源/无效文件.txt",
        "code": 10600,
        "reason": "文件扩展名不属于可整理媒体文件扩展名"
      }
    ],
    "success_count": 1,
    "fail_count": 1
  }
}
```

## 状态码说明

| 状态码 | 说明 |
|--------|------|
| 10200 | 成功 |
| 10400 | 未传有效参数（请求体为空或 data 为空） |
| 10422 | 缺失必要参数（pick_code、id 或 pan_path 参数） |
| 10600 | 文件扩展名不属于可整理媒体文件扩展名 |
| 10601 | 无法获取本地生成 STRM 路径 |
| 10602 | 无法获取网盘媒体库路径或文件信息 |
| 10911 | STRM 文件生成失败 |

---

## 端点 2: 通过文件夹路径批量生成 STRM

### 基本信息

- **路径**: `/api/v1/plugin/P115StrmHelper/api_strm_sync_create_by_path`
- **方法**: `POST`
- **认证**: Bearer Token（需要 MoviePilot API Key）
- **Content-Type**: `application/json`

### 请求 URL 格式

```
POST {server_url}/api/v1/plugin/P115StrmHelper/api_strm_sync_create_by_path?apikey={APIKEY}
```

### 功能说明

此端点允许您通过提供文件夹路径列表，自动遍历这些文件夹下的所有文件并生成 STRM。系统会自动：
1. 获取指定文件夹的目录 ID
2. 递归遍历文件夹下的所有文件
3. 为每个符合条件的媒体文件生成 STRM

### 请求参数

#### 请求体结构

```json
{
  "data": [
    "/我的资源/电影",
    "/我的资源/剧集/2024"
  ],
  "scrape_metadata": true,
  "media_server_refresh": true
}
```

#### 字段说明

- `data` (array, 必需): 需要生成 STRM 的文件夹路径列表。系统会递归遍历这些文件夹下的所有文件
- `scrape_metadata` (boolean, 可选): 是否刮削元数据。如果不提供，使用插件配置中的 `api_strm_scrape_metadata_enabled` 默认值
- `media_server_refresh` (boolean, 可选): 是否刷新媒体服务器。如果不提供，使用插件配置中的 `api_strm_media_server_refresh_enabled` 默认值

### 响应格式

响应格式与端点 1 相同，返回成功和失败的文件列表。

### 使用示例

#### Python 示例

```python
import requests
import json

# API 配置
server_url = "http://your-moviepilot-server:3001"
apikey = "your-api-key"
api_endpoint = f"{server_url}/api/v1/plugin/P115StrmHelper/api_strm_sync_create_by_path"

# 请求数据
payload = {
    "data": [
        "/我的资源/电影",
        "/我的资源/剧集/2024"
    ],
    "scrape_metadata": True,
    "media_server_refresh": True
}

# 发送请求
headers = {
    "Content-Type": "application/json"
}
params = {
    "apikey": apikey
}

response = requests.post(
    api_endpoint,
    json=payload,
    headers=headers,
    params=params
)

result = response.json()
print(json.dumps(result, indent=2, ensure_ascii=False))

# 处理结果
if result.get("code") == 10200:
    data = result.get("data", {})
    print(f"成功生成: {data.get('success_count', 0)} 个文件")
    print(f"失败: {data.get('fail_count', 0)} 个文件")
```

#### cURL 示例

```bash
curl -X POST \
  "http://your-moviepilot-server:3001/api/v1/plugin/P115StrmHelper/api_strm_sync_create_by_path?apikey=your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "data": [
      "/我的资源/电影",
      "/我的资源/剧集/2024"
    ],
    "scrape_metadata": true,
    "media_server_refresh": true
  }'
```

### 注意事项

1. **路径格式**: 文件夹路径必须是 115 网盘中的完整路径，以 `/` 开头
2. **递归遍历**: 系统会递归遍历指定文件夹下的所有子文件夹和文件
3. **文件过滤**: 只有符合媒体文件扩展名的文件才会生成 STRM（根据 `user_rmt_mediaext` 配置）
4. **路径映射**: 系统会根据 `api_strm_config` 配置自动匹配本地路径
5. **性能考虑**: 如果文件夹包含大量文件，处理时间可能较长，建议分批处理

---

## 使用示例（端点 1）

### Python 示例

```python
import requests
import json

# API 配置
server_url = "http://your-moviepilot-server:3001"
apikey = "your-api-key"
api_endpoint = f"{server_url}/api/v1/plugin/P115StrmHelper/api_strm_sync_creata"

# 请求数据
payload = {
    "data": [
        {
            "pick_code": "abc123def456ghi78",
            "name": "电影名称.mkv",
            "pan_path": "/我的资源/电影/电影名称.mkv",
            "scrape_metadata": True,
            "media_server_refresh": True
        },
        {
            "id": 9876543210,
            "name": "剧集名称 S01E01.mkv",
            "pan_path": "/我的资源/剧集/剧集名称/剧集名称 S01E01.mkv"
        },
        {
            "pan_path": "/我的资源/电影/另一个电影.mkv",
            "name": "另一个电影.mkv"
        }
    ]
}

# 发送请求
headers = {
    "Content-Type": "application/json"
}
params = {
    "apikey": apikey
}

response = requests.post(
    api_endpoint,
    json=payload,
    headers=headers,
    params=params
)

result = response.json()
print(json.dumps(result, indent=2, ensure_ascii=False))

# 处理结果
if result.get("code") == 10200:
    data = result.get("data", {})
    print(f"成功生成: {data.get('success_count', 0)} 个文件")
    print(f"失败: {data.get('fail_count', 0)} 个文件")
    
    # 处理失败的文件
    for fail_item in data.get("fail", []):
        print(f"失败: {fail_item.get('name')} - {fail_item.get('reason')}")
```

### cURL 示例

```bash
curl -X POST \
  "http://your-moviepilot-server:3001/api/v1/plugin/P115StrmHelper/api_strm_sync_creata?apikey=your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "data": [
      {
        "pick_code": "abc123def456ghi78",
        "name": "电影名称.mkv",
        "pan_path": "/我的资源/电影/电影名称.mkv",
        "scrape_metadata": true,
        "media_server_refresh": true
      },
      {
        "pan_path": "/我的资源/电影/另一个电影.mkv"
      }
    ]
  }'
```

### JavaScript/Node.js 示例

```javascript
const axios = require('axios');

const serverUrl = 'http://your-moviepilot-server:3001';
const apikey = 'your-api-key';
const apiEndpoint = `${serverUrl}/api/v1/plugin/P115StrmHelper/api_strm_sync_creata`;

const payload = {
  data: [
    {
      pick_code: 'abc123def456ghi78',
      name: '电影名称.mkv',
      pan_path: '/我的资源/电影/电影名称.mkv',
      scrape_metadata: true,
      media_server_refresh: true
    }
  ]
};

axios.post(apiEndpoint, payload, {
  params: { apikey },
  headers: { 'Content-Type': 'application/json' }
})
  .then(response => {
    const result = response.data;
    if (result.code === 10200) {
      console.log(`成功生成: ${result.data.success_count} 个文件`);
      console.log(`失败: ${result.data.fail_count} 个文件`);
    } else {
      console.error('请求失败:', result.msg);
    }
  })
  .catch(error => {
    console.error('请求错误:', error.message);
  });
```

## 配置说明

### 插件配置项

在插件配置界面中，可以配置以下选项：

1. **路径映射配置** (`api_strm_config`): 配置网盘路径到本地路径的映射关系
   - `pan_path`: 网盘媒体库根路径
   - `local_path`: 本地生成 STRM 文件的目录路径

2. **媒体服务器刷新** (`api_strm_mediaservers`): 指定需要刷新的媒体服务器列表

3. **MP-媒体库路径转换** (`api_strm_mp_mediaserver_paths`): MoviePilot 媒体库路径转换配置

4. **自动刮削元数据** (`api_strm_scrape_metadata_enabled`): 是否自动刮削生成的 STRM 文件元数据（默认值）

5. **自动刷新媒体服务器** (`api_strm_media_server_refresh_enabled`): 是否自动刷新媒体服务器（默认值）

### 配置示例

```json
{
  "api_strm_config": [
    {
      "pan_path": "/我的资源/电影",
      "local_path": "/media/movies"
    },
    {
      "pan_path": "/我的资源/剧集",
      "local_path": "/media/tvshows"
    }
  ],
  "api_strm_mediaservers": ["Emby", "Jellyfin"],
  "api_strm_mp_mediaserver_paths": "/我的资源/电影#/media/movies\n/我的资源/剧集#/media/tvshows",
  "api_strm_scrape_metadata_enabled": true,
  "api_strm_media_server_refresh_enabled": true
}
```

## 注意事项

1. **文件扩展名限制**: 只有配置中 `user_rmt_mediaext` 指定的媒体文件扩展名才会生成 STRM 文件。默认支持的扩展名包括：`mp4, mkv, ts, iso, rmvb, avi, mov, mpeg, mpg, wmv, 3gp, asf, m4v, flv, m2ts, tp, f4v`

2. **路径匹配**: 如果使用路径映射，确保 `pan_path` 能够匹配到配置的路径前缀

3. **批量处理**: API 支持批量处理多个文件，但每个文件处理之间有 1 秒的冷却时间

4. **错误处理**: 即使部分文件处理失败，API 仍会返回成功状态码（10200），需要在响应中检查 `fail` 数组

5. **权限要求**: 确保 MoviePilot 有权限在指定的 `local_path` 目录中创建文件

6. **STRM 文件位置**: STRM 文件会生成在 `local_path` 目录下，保持与网盘路径相同的相对目录结构

7. **自动创建目录**: 系统会自动创建所需的目录结构，无需手动创建目标目录

## 常见问题

### Q: 如何获取文件的 pick_code、id 或 pan_path？

A: 可以通过 115 网盘的 API 或其他工具获取文件的 pick_code、id 或 pan_path。pick_code 是 17 位的字符串，id 是数字，pan_path 是文件在网盘中的完整路径。至少需要提供其中一个参数即可。

### Q: 如果文件路径不在配置的映射中怎么办？

A: 可以在请求中直接指定 `local_path` 和 `pan_media_path`，这样就不需要依赖路径映射配置。

### Q: 生成的 STRM 文件在哪里？

A: STRM 文件会生成在 `local_path` 目录下，保持与网盘路径相同的相对目录结构。例如：
- 网盘路径: `/我的资源/电影/电影名称.mkv`
- 媒体库路径: `/我的资源/电影`
- 本地路径: `/media/movies`
- 生成的 STRM: `/media/movies/电影名称.strm`

### Q: 如何知道哪些文件处理失败了？

A: 检查响应中的 `fail` 数组，每个失败项都包含 `code` 和 `reason` 字段，说明失败原因。

### Q: 可以同时处理多少个文件？

A: API 支持批量处理，理论上没有数量限制，但建议单次请求不要超过 100 个文件，以避免请求超时。

### Q: 什么时候使用 `/api_strm_sync_creata`，什么时候使用 `/api_strm_sync_create_by_path`？

A: 
- **使用 `/api_strm_sync_creata`**: 当您已经知道具体的文件信息（如 pick_code、id 或文件路径），需要为特定文件生成 STRM 时使用
- **使用 `/api_strm_sync_create_by_path`**: 当您想要为整个文件夹下的所有文件批量生成 STRM 时使用，系统会自动遍历文件夹下的所有文件

### Q: `/api_strm_sync_create_by_path` 会处理子文件夹吗？

A: 是的，`/api_strm_sync_create_by_path` 会递归遍历指定文件夹下的所有子文件夹和文件，为所有符合条件的媒体文件生成 STRM。

### Q: 如果目标目录不存在会怎样？

A: 系统会自动创建所需的目录结构。在生成 STRM 文件前，系统会自动创建所有必要的父目录，确保文件能够成功写入。
