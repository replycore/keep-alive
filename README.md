# Host-Ship Auto Renew

一个可直接 Fork / 上传到 GitHub 使用的 Host-Ship 自动续期模板。

## 功能

- 每天北京时间 08:00 自动检查一次
- 仅在 Host-Ship 页面出现可用的 `Renew` 按钮时尝试续期
- 未到续期窗口时不会点击续期
- 手动运行时会发送 Telegram 检查结果
- 真正续期成功、失败或异常时发送 Telegram
- Telegram 中显示：
  - 服务器编号
  - 节点状态
  - 当前出口 IP
  - 检查时间
  - 距离续期天数
  - 预计可续期日期
  - 自动检查时间
- 支持可选 `NODE_LINK`
- 已测试可用于 VMess / VLESS 类分享链接
- 不尝试绕过验证码、Cloudflare 或其他安全验证

## GitHub Secrets

进入：

`Settings -> Secrets and variables -> Actions`

添加以下 Repository secrets：

### 必填

`SERVER_URL`

Host-Ship 服务器详情页完整地址，例如：

`https://panel.host-ship.com/server/xxxxxxxx`

`HOSTSHIP_LOGIN`

Host-Ship 登录账号/邮箱。

`HOSTSHIP_PASSWORD`

Host-Ship 登录密码。

`TG_BOT_TOKEN`

Telegram Bot Token。

`TG_CHAT_ID`

接收通知的 Telegram Chat ID。

### 可选

`NODE_LINK`

代理节点完整分享链接，例如：

`vmess://...`

或：

`vless://...`

> 不要把节点链接、密码、Telegram Token 写进代码或 README。

## 第一次测试

进入：

`Actions -> Host-Ship Auto Renew -> Run workflow`

手动运行时，即使当前未到续期窗口，也会发送一条 Telegram 检查消息。

## 自动运行

默认每天北京时间：

`08:00`

自动检查一次。

如果当前页面仍显示类似：

`Renew Limit Reached`

脚本不会点击续期。

如果出现可用的 `Renew` 按钮，脚本会尝试续期。

## 隐私说明

这个模板本身不包含任何账号、密码、节点、TG Token、服务器真实地址或真实 IP。

所有敏感信息都应该只存放在 GitHub Repository secrets 中。

如果你公开分享这个模板：

- 不要把自己的 Secrets 写进代码
- 不要公开失败截图或 Actions artifacts 中可能包含的敏感页面
- 不要把真实 `NODE_LINK` 放进 README
- 不要把真实服务器 URL 写入仓库

## 节点兼容说明

当前代理初始化使用第三方脚本：

`https://main.ssss.nyc.mn/setup_proxy.sh`

VMess / VLESS 已验证可用。

部分 Trojan 分享链接如果包含某些 `type=tcp` 参数，可能会被该第三方转换脚本生成成 sing-box 不兼容配置；这种情况建议换用 VMess/VLESS，或自行修改代理初始化方式。

## 说明

GitHub Actions 定时任务可能会有几分钟延迟，这是正常现象。

Host-Ship 页面结构如果后续发生变化，脚本可能需要更新选择器。
