# Keep-Alive（多平台自动续期）

各平台独立脚本 + 独立工作流，互不影响。新增平台时仿照现有条目追加即可。

| 平台 | 脚本 | 工作流 | 定时（北京时间） |
|------|------|--------|------------------|
| Host-Ship | `hostship_renew.py` | `Host-Ship Auto Renew` | 每 5 天 08:00 |
| KataBump | `katabump_renew.py` | `KataBump Auto Renew` | 每 5 天 09:00 |

## 使用方法

1. Fork 本仓库
2. `Settings -> Secrets and variables -> Actions` 按下表添加 Secrets
3. `Actions -> <对应工作流> -> Run workflow` 手动跑一次验证
4. 之后按上表定时自动运行

公共通知变量（两个平台共用同一套）：`TG_BOT_TOKEN`、`TG_CHAT_ID`，只需配一次。

## 变量表

### 公共（通知）

| Secret | 说明 |
|--------|------|
| `TG_BOT_TOKEN` | Telegram Bot Token |
| `TG_CHAT_ID` | 接收通知的 Chat ID |

### Host-Ship（必填）

| Secret | 说明 |
|--------|------|
| `SERVER_URL` | 服务器详情页地址，如 `https://panel.host-ship.com/server/xxxxxxxx` |
| `HOSTSHIP_LOGIN` | 面板登录账号/邮箱 |
| `HOSTSHIP_PASSWORD` | 面板登录密码 |

### Host-Ship（可选）

| Secret | 说明 |
|--------|------|
| `NODE_LINK` | 代理节点分享链接（vless / vmess / trojan / hysteria2 / tuic / anytls / socks），不填则直连 |
| `SEND_SHOTS` | 截图模式：默认 `auto`（成功只发文字，失败发 1 张截图）；`steps` 分步截图（调试用）；`false` 完全关闭 |

### KataBump（必填）

| Secret | 说明 |
|--------|------|
| `KATA_EMAIL` | KataBump 面板登录邮箱 |
| `KATA_PASSWORD` | KataBump 面板登录密码 |

### KataBump（可选）

| Secret | 说明 |
|--------|------|
| `KATA_SERVER_ID` | 服务器 ID，默认 `185829` |

## 新增平台

1. 加脚本 `<name>_renew.py`（从环境变量读配置，从不写死账号密码）
2. 加工作流 `.github/workflows/<name>.yml`（参考 `hostship.yml` / `katabump.yml`）
3. 上表追加一行 + 对应变量小节
