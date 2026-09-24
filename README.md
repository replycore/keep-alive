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

以下变量名与代码 `os.getenv(...)` / 工作流 `secrets.*` 完全对应，照抄即可。

### 公共（通知）

| Secret | 代码读取位置 | 说明 |
|--------|--------------|------|
| `TG_BOT_TOKEN` | 两脚本 `os.getenv("TG_BOT_TOKEN")` | Telegram Bot Token，不填则跳过通知 |
| `TG_CHAT_ID` | 两脚本 `os.getenv("TG_CHAT_ID")`，工作流 `secrets.TG_CHAT_ID` | 接收通知的 Chat ID |

### Host-Ship（必填，仅 2 个）

| Secret | 代码读取位置 | 说明 |
|--------|--------------|------|
| `HOSTSHIP_LOGIN` | `os.getenv("HOSTSHIP_LOGIN")`，工作流 `secrets.HOSTSHIP_LOGIN` | 面板登录账号/邮箱 |
| `HOSTSHIP_PASSWORD` | `os.getenv("HOSTSHIP_PASSWORD")`，工作流 `secrets.HOSTSHIP_PASSWORD` | 面板登录密码 |

流程：登录面板首页（`PANEL_URL` 硬编码在代码里，无需配置）
→ 检查底部 MANAGE SERVER → 无按钮则判失败（账号下无服务）
→ 有则点击进入，服务器详情页地址自动获取（`/server/xxxxxxxx`，仅用于通知展示）。

### Host-Ship（可选）

| Secret | 代码读取位置 | 说明 |
|--------|--------------|------|
| `NODE_LINK` | 工作流 `secrets.NODE_LINK`（脚本不直接读） | 代理节点分享链接（vless / vmess / trojan / hysteria2 / tuic / anytls / socks），不填则直连；工作流连通后自动写 `IS_PROXY` / `PROXY_SERVER` |
| `SEND_SHOTS` | `os.getenv("SEND_SHOTS")`，工作流 `secrets.SEND_SHOTS` | 截图模式：默认 `auto`（成功只发文字，失败发 1 张截图）；`steps`（或 `true`）分步截图（调试用）；`false` / `off` 完全关闭 |
| `IS_PROXY` | `os.getenv("IS_PROXY")`，工作流 `env.IS_PROXY` 自动写入 | 是否走代理，由工作流根据 `NODE_LINK` 连通性自动设置，无需手动填 |
| `PROXY_SERVER` | `os.getenv("PROXY_SERVER")`，工作流 `env.PROXY_SERVER` 自动写入 | 代理地址，默认 `socks5://127.0.0.1:1080`；浏览器、TG 推送、IP 查询共用 |
| `MANUAL_RUN` | `os.getenv("MANUAL_RUN")`，工作流按 `workflow_dispatch` 自动传入 | 手动 Run 才发"未到续期时间"的检查消息，定时任务不打扰 |

### KataBump（必填）

| Secret | 代码读取位置 | 说明 |
|--------|--------------|------|
| `KATA_EMAIL` | `os.getenv("KATA_EMAIL")`（`katabump.yml` 传 `secrets.KATA_EMAIL`） | KataBump 面板登录邮箱 |
| `KATA_PASSWORD` | `os.getenv("KATA_PASSWORD")`（`katabump.yml` 传 `secrets.KATA_PASSWORD`） | KataBump 面板登录密码 |

### KataBump（可选）

| Secret | 代码读取位置 | 说明 |
|--------|--------------|------|
| `KATA_SERVER_ID` | `os.getenv("KATA_SERVER_ID")`（`katabump.yml` 传 `secrets.KATA_SERVER_ID`） | 服务器 ID，默认 `185829` |

工作流 `katabump.yml` 同样透传 `TG_BOT_TOKEN` / `TG_CHAT_ID`（与 Host-Ship 共用同一套）。

## 新增平台

1. 加脚本 `<name>_renew.py`（从环境变量读配置，从不写死账号密码）
2. 加工作流 `.github/workflows/<name>.yml`（参考 `hostship.yml` / `katabump.yml`）
3. 上表追加一行 + 对应变量小节
