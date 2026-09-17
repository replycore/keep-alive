# Host-Ship Auto Renew

## 使用方法

1. Fork 本仓库（或将 `host-ship` 分支文件复制到自己仓库）
2. `Settings -> Secrets and variables -> Actions` 按下表添加 Secrets
3. `Actions -> Host-Ship Auto Renew -> Run workflow` 手动跑一次验证
4. 之后每天北京时间 08:00 自动检查并续期

## 变量表

### 必填 Secrets

| Secret | 说明 |
|--------|------|
| `SERVER_URL` | 服务器详情页地址，如 `https://panel.host-ship.com/server/xxxxxxxx` |
| `HOSTSHIP_LOGIN` | 面板登录账号/邮箱 |
| `HOSTSHIP_PASSWORD` | 面板登录密码 |
| `TG_BOT_TOKEN` | Telegram Bot Token |
| `TG_CHAT_ID` | 接收通知的 Chat ID |

### 可选 Secrets

| Secret | 说明 |
|--------|------|
| `NODE_LINK` | 代理节点分享链接（vless / vmess / trojan / hysteria2 / tuic / anytls / socks），不填则直连 |
| `SEND_SHOTS` | 截图模式：默认 `auto`（成功只发文字，失败发 1 张截图）；`steps` 分步截图（调试用）；`false` 完全关闭 |
