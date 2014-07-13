# HDHive Auth Broker

集中 OAuth 中转服务：持有 HDHive 应用 Secret，为 MoviePilot `p115strmhelper` 插件提供 OAuth 与 Open API 代理。

## HDHive 控制台配置

| 项 | 建议值 |
|----|--------|
| 授权结果投递 | 页面消息（`postmessage`） |
| Redirect URI 白名单 | 留空（使用环境变量 `HDHIVE_REDIRECT_URI`） |
| 预期服务端出口 IP | 在控制台填写 **本服务部署机的公网出口 IP**（部署后自行查询并登记，勿写入公开仓库） |
| Scope | 至少 `query`、`unlock` |

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `HDHIVE_CLIENT_ID` | 是 | OpenAPI 应用 ID |
| `HDHIVE_APP_SECRET` | 是 | 应用 Secret（仅本服务） |
| `HDHIVE_REDIRECT_URI` | 是 | authorize 与 token 交换共用的 redirect_uri |
| `LISTEN_ADDR` | 否 | 默认 `:8080` |
| `HDHIVE_BASE_URL` | 否 | 默认 `https://hdhive.com` |
| `OAUTH_STATE_TTL_MINUTES` | 否 | 默认 `10` |

## 路由

- `GET /oauth/hdhive/start?instance_key=&scope=`
- `POST /oauth/hdhive/exchange`
- `POST /oauth/hdhive/refresh`
- `POST /oauth/hdhive/revoke`
- `ANY /proxy/open/*path` — 转发至 HDHive Open API（附加 `X-API-Key`）
- `GET /health`

## 运行

```bash
cd services/hdhive-auth-broker
export HDHIVE_CLIENT_ID=app_xxx
export HDHIVE_APP_SECRET=your-secret
export HDHIVE_REDIRECT_URI=https://your-broker.example/oauth/hdhive/callback
go run ./cmd/server
```

## 测试

```bash
go test ./...
```

## Docker

```bash
docker build -t hdhive-auth-broker .
docker run -p 8080:8080 \
  -e HDHIVE_CLIENT_ID=app_xxx \
  -e HDHIVE_APP_SECRET=secret \
  -e HDHIVE_REDIRECT_URI=https://your-broker.example/oauth/hdhive/callback \
  hdhive-auth-broker
```

插件侧在 `helper/hdhive/open/constants.py` 配置 `HDHIVE_OAUTH_BROKER_BASE`（公开 URL）。
