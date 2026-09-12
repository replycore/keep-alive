# Pterodactyl 面板服务器自动续期 Worker

基于 Pterodactyl 面板 Client API 的免费服务器自动续期 Cloudflare Worker。默认对接 aclclouds.com，可通过 `API_BASE` 切换到其它提供 `/upgrade/renew` 路由的面板。

## 功能

- 定时检查（默认每 3 天）：发现服务器 → `can_renew` 判断 → `POST /upgrade/renew` 续期
- Telegram 通知每次运行结果
- WebUI 控制台（密码登录）/ HTTP API 手动触发

## 变量

| 变量 | 位置 | 必填 | 说明 |
|------|------|------|------|
| `API_KEY` | 面板 Secret | 是 | 面板 Client API Key（`ptlc_` 开头）。在面板 账号设置 → API 凭据 创建。token 能访问哪些服务器就保活哪些。 |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | 面板 Secret | 否 | Telegram 通知（需同时配置） |
| `WEBUI_PASSWORD` | 面板 Secret | 否 | WebUI 登录密码（不配置则纯 API 模式） |
| `ADMIN_TOKEN` | 面板 Secret | 否 | 非 WebUI 模式下 HTTP 触发 `/` 的令牌 |
| `API_BASE` | 面板 Variable | 否 | 面板 API 基础地址，默认内置 `aclclouds.com/api` |
| `SERVER_IDS` | 面板 Variable | 否 | 服务器白名单，逗号分隔；不填自动发现全部 |

> 敏感值只放面板 **Secret**。`wrangler.toml` 内置 `keep_vars = true`，部署不会覆盖或删除面板变量。

## 部署

### Cloudflare Git 自动部署

1. `Workers & Pages → Create → Git`，选择本仓库
2. 分支 `aclclouds-renew`；安装命令 `npm install`；部署命令 `npx wrangler deploy`
3. 首次部署成功后，在 `Settings → Variables and Secrets` 添加真实值（Secret）
4. 之后每次 push 只更新代码与 Cron，面板变量保持不变

### 命令行部署

```bash
npm install
npx wrangler login
npx wrangler secret put API_KEY        # 必填
npx wrangler secret put TG_BOT_TOKEN   # 可选
npx wrangler secret put TG_CHAT_ID
npx wrangler secret put WEBUI_PASSWORD
npx wrangler secret put ADMIN_TOKEN
npm run deploy
```

## 定时

Cron：`0 0 */3 * *`（每 3 天，00:00 UTC）。

## HTTP 入口

| 路径 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/health` | GET | 公开 | 存活检查 |
| `/login` | GET/POST | - | WebUI 登录 |
| `/logout` | GET | WebUI session | 退出登录 |
| `/` | GET | WebUI session / `X-Admin-Token` | 有 WebUI 时返回控制台；无 WebUI 时触发一次续期（JSON） |
| `/run` | POST | WebUI session / `X-Admin-Token` | 手动触发续期 |

## 手动触发（非 WebUI 模式）

```bash
curl -H "X-Admin-Token: <ADMIN_TOKEN>" "https://<worker域名>/"
```

## 本地开发

```bash
cp .dev.vars.example .dev.vars   # 填 API_KEY 等
npm install
npm run dev
```