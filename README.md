# FridayDev 免费服务器自动续期 Worker

基于 fridaydev 门户（fridaydev.fr）会话 API 的免费服务器自动续期 Cloudflare Worker。

免费服规则：5 天一轮，剩余 ≤2 天可续期，续一次回到 5 天。

## 功能

- 定时检查（默认每 4 天），免费服进入续期窗口自动续期 +5 天
- 门户会话缓存（KV），失效时自动用密码重登并重试
- Telegram 通知每次运行结果
- WebUI 控制台 / HTTP API 手动触发

## 变量

| 变量 | 位置 | 必填 | 说明 |
|------|------|------|------|
| `FD_EMAIL` | 面板 Secret | 是 | fridaydev 门户登录邮箱 |
| `FD_PASSWORD` | 面板 Secret | 是 | fridaydev 门户登录密码 |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | 面板 Secret | 否 | Telegram 通知（需同时配置） |
| `WEBUI_PASSWORD` | 面板 Secret | 否 | WebUI 登录密码 |
| `ADMIN_TOKEN` | 面板 Secret | 否 | HTTP API 手动触发令牌 |
| `FD_BASE` | `wrangler.toml` `[vars]` | 否 | 门户地址，默认 `https://fridaydev.fr` |
| `SERVICE_IDS` | 面板 Variable | 否 | 服务 uuid 白名单，逗号分隔；不填全部 |
| `AUTO_RENEW_PAID` | 面板 Variable | 否 | `"1"` 时对挂起/付费服也续期（扣余额，默认关） |
| `FD_KV` | 面板 KV 绑定 | 否 | 缓存门户会话 |

> 敏感值只放面板 **Secret**。`wrangler.toml` 内置 `keep_vars = true`，部署不会覆盖或删除面板变量。

## 部署

### Cloudflare Git 自动部署

1. `Workers & Pages → Create → Git`，选择本仓库
2. 分支 `fridaydev-renew`；安装命令 `npm install`；部署命令 `npx wrangler deploy`
3. 首次部署成功后，在 `Settings → Variables and Secrets` 添加真实值（Secret）
4. 之后每次 push 只更新代码与 Cron，面板变量保持不变

### 命令行部署

```bash
npm install
npx wrangler login
npx wrangler secret put FD_EMAIL
npx wrangler secret put FD_PASSWORD    # 以上两项必填
npx wrangler secret put TG_BOT_TOKEN   # 可选
npx wrangler secret put TG_CHAT_ID
npx wrangler secret put WEBUI_PASSWORD
npx wrangler secret put ADMIN_TOKEN
npm run deploy
```

## 定时

Cron：`0 0 */4 * *`（每 4 天，00:00 UTC）。

## HTTP 入口

| 路径 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/health` | GET | 公开 | 存活检查 |
| `/status` | GET | WebUI session 或 `X-Admin-Token` | 最近运行结果（JSON） |
| `/run` | POST | WebUI session 或 `X-Admin-Token` | 手动触发续期 |
| `/login` | GET/POST | - | WebUI 登录 |
| `/logout` | GET | WebUI session | 退出登录 |
| `/` | GET | WebUI session 或公开 | WebUI 控制台 / 状态 JSON |

## 手动触发

```bash
curl -X POST -H "X-Admin-Token: <ADMIN_TOKEN>" "https://<worker域名>/run"
```

WebUI 模式：登录后点击页面「续期」按钮。

## 本地开发

```bash
cp .dev.vars.example .dev.vars   # 填 FD_EMAIL / FD_PASSWORD 等
npm install
npm run dev
```