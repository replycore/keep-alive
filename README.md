# aclclouds 自动续期（GitHub Actions 版）

参考 [Host-Ship Auto Renew](https://github.com/qingxinguayu999-alt/Host-Ship-xuqi) 的工程骨架。
与其关键差异：aclclouds 面板有真实的 Pterodactyl Client API，直接用 `/upgrade/renew` 续期，不需要浏览器猜 DOM。

## 功能

- 每天北京时间 08:00 自动检查一次（GitHub Actions cron）
- 手动运行时也发 TG 简报
- 续期成功 / 失败 / 需要人机验证时发 Telegram
- 可选 `PROXY_SERVER`：续期请求经 Socks5/HTTP 出口发出（适用于面板对出口 IP 有限制的情形）
- TG 中显示：服务器编号、计划、到期时间、剩余天数、出口 IP、检查时间
- **不绕过验证码 / Cloudflare / 任何安全验证**：检测到人机验证就停下，并在 TG 里给出面板人工续期直链

## GitHub Secrets

`Settings -> Secrets and variables -> Actions`

### 必填

| Secret | 说明 |
|--------|------|
| `API_KEY` | aclclouds 面板 Client API Key（账号设置 -> API 凭据，`ptlc_` 开头） |

### 可选

| Secret | 说明 |
|--------|------|
| `API_BASE` | 面板 API 地址，默认 `https://aclclouds.com/api` |
| `SERVER_IDS` | 服务器白名单，逗号分隔；不填自动发现全部 |
| `PROXY_SERVER` | 出口代理，如 `socks5://user:pass@host:port`（续期走指定出口 IP） |
| `TG_BOT_TOKEN` | Telegram Bot Token |
| `TG_CHAT_ID` | 接收通知的 chat id |

> 不要把自己的 Key / Token / 代理凭据写进代码或 README，全部放 Secrets。

## 第一次测试

`Actions -> aclclouds Auto Renew -> Run workflow`

手动运行时，即使未到续期窗口，也会发送一条 Telegram 检查消息。

## 自动运行

每天北京时间 `08:00` 自动检查一次。未到续期窗口不点击续期；有效续期窗口自动续期；遇到人机验证停止并指引人工处理。

## 说明

- GitHub Actions 定时任务可能有几分钟延迟，属正常现象。
- aclclouds 面板若后续把续期改成纯网页按钮（无 Client API），本脚本需相应改动。
