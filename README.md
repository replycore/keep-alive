# Host-Ship Auto Renew

Host-Ship 免费服务器自动续期（GitHub Actions + Playwright 真浏览器点击）。

## 功能

- 每天北京时间 08:00 自动检查一次（cron `0 0 * * *`，UTC 00:00）
- 登录面板读取续期状态，未到窗口（`Renew Limit Reached`）不点
- 到窗口时走人工同款两步：点 `Renew` → 等确认弹窗 `Confirm server renewal` → 点 `Renew now`
- 点 `Renew now` 时监听后端请求确认已发出，然后短轮询确认天数变化
- 文字结果发 Telegram；可选把每步截图也发 TG（`SEND_SHOTS`，默认开）
- 失败截图上传为 Actions Artifact（`hostship-debug-screenshots`）
- 不绕过验证码 / Cloudflare / 任何安全验证：遇到直接报错等人处理

## 续期流程（与人工一致）

1. 打开 `SERVER_URL`，账号密码登录
2. 读取状态（如 `RENEWAL IN 7 Days`）
3. 点 `Renew` → 等确认对话框（最多 10 秒，认 `role=dialog` 或标题 `Confirm server renewal`）
4. 点 `Renew now`（常规点击失败自动换 JS 直点；用 `expect_response` 确认后端请求已发出）
5. 短轮询最多 15 秒：成功词 / 天数变化 / `Renew Limit Reached` 即判成功

整次续期操作最多等待约 35 秒（10 + 10 + 15），日常未到窗口秒级退出。

## GitHub Secrets

`Settings -> Secrets and variables -> Actions`

### 必填

| Secret | 说明 |
|--------|------|
| `SERVER_URL` | 服务器详情页完整地址，如 `https://panel.host-ship.com/server/xxxxxxxx` |
| `HOSTSHIP_LOGIN` | 面板登录账号/邮箱 |
| `HOSTSHIP_PASSWORD` | 面板登录密码 |
| `TG_BOT_TOKEN` | Telegram Bot Token |
| `TG_CHAT_ID` | 接收通知的 Chat ID |

### 可选

| Secret | 说明 |
|--------|------|
| `NODE_LINK` | 代理节点分享链接（vless / vmess / trojan / hysteria2 / tuic / anytls / socks），不填则直连 |
| `SEND_SHOTS` | `false` 关闭 TG 步骤截图（只收文字通知）；默认开启 |

> 所有敏感信息只放 Secrets，不要写进代码或 README。

## 代理说明

`NODE_LINK` 由第三方脚本 `https://main.ssss.nyc.mn/setup_proxy.sh` 解析并启动本地 sing-box（socks5 `127.0.0.1:1080`）。workflow 在其后打了两个本地补丁（`renew.yml` 内）：

1. **裸 TCP transport**：无 `type=` 参数的链接会被上游生成 `"transport":{"type":"tcp"}`，sing-box 无此类型直接启动失败。补丁删掉 `tcp` / `raw` 的 transport 字段（`ws/http/grpc/quic/httpupgrade` 保留）。
2. **旧版 trojan `peer=` 参数**：上游只认 `sni=`，`peer=` 会被丢弃导致 `tls.server_name` 回退成 IP，自签证书校验失败。补丁：server_name 为 IP 且链接含 `peer=` 时，用 peer 值覆盖并置 `insecure=true`（自签证书必须跳过系统 CA 校验，流量仍是 TLS 加密）。
3. 上游脚本自带 `set -e`，启动失败会直接 `exit 1` 中断 step，因此调用后加了 `|| ...` 保证补丁一定执行。

Trojan 自签节点（`peer=` + `hpkp` 形）已本地实测拨号验证通过。

## Telegram 通知

- 文字结果每次都发（成功 / 未到窗口（仅手动） / 失败）
- 步骤截图（`SEND_SHOTS` 未关闭时）：
  1. 登录后-服务器页
  2. 点击 Renew 前-状态页
  3. 确认对话框
  4. 点击 Renew now 后
  5. 最终结果页

## 第一次测试

`Actions -> Host-Ship Auto Renew -> Run workflow`，手动运行即使未到窗口也会发 TG 检查消息（含截图，方便核对流程）。

稳定后在 Secrets 加 `SEND_SHOTS=false` 关闭截图，只收文字。

## 说明

- GitHub Actions 定时任务可能有几分钟延迟，属正常现象
- 面板页面结构变化可能需要更新选择器（确认弹窗标题 `Confirm server renewal`、按钮 `Renew now`）
- 频繁手动 Run 可能触发面板限流/风控，调试通过后尽量只靠 cron
