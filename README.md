# B站解析推送助手

面向 AstrBot 的 Bilibili 视频自动解析与订阅推送插件。

当前版本实现了两条主线：

- 自动识别用户发送的 B 站链接、分享卡片、BV 号并解析回复。
- 轮询监控指定 UP 主的新投稿，并向配置好的会话主动推送。

## 功能

- 支持识别 `BV` 号、`av` 号、`bilibili.com/video/...`、`b23.tv` 短链、QQ JSON 分享卡片中的 B 站链接。
- 支持通过群聊/私聊指令扫码登录 B 站账号，并将登录态持久化到 `data/plugin_data/astrbot_plugin_bilibili_push/`。
- 支持直接在插件配置中填写 Cookie，插件会自动校验并持久化。
- 支持按 UP 主 UID / 空间链接配置订阅规则，将新投稿主动推送到指定会话。
- 支持封面图推送，支持可选的“尝试发送直链视频”模式。

## 安装

1. 将插件目录放入 `AstrBot/data/plugins/astrbot_plugin_bilibili_push`。
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 在 AstrBot WebUI 中重载插件。

## 登录与 Cookie

有两种方式：

1. 在插件配置中填写 `auth_settings.bilibili_cookie`。
2. 发送指令 `/bili_login` 或 `登录B站` 扫码登录。

扫码登录成功后，凭证会保存到：

- `data/plugin_data/astrbot_plugin_bilibili_push/bilibili_credential.json`

相关指令：

- `/bili_login`
- `/bili_verify`
- `/bili_logout`

说明：

- 影响全局登录态的指令仅允许机器人主人执行。
- 若配置中的 Cookie 有效，插件会优先使用并持久化该 Cookie。
- 若配置中的 Cookie 无效，插件会回退到本地持久化凭证。

## 自动解析

默认开启自动解析。用户发送以下内容时，插件会自动回复：

- `BVxxxxxxxxxx`
- `av12345678`
- `https://www.bilibili.com/video/BV...`
- `https://b23.tv/...`
- QQ / OneBot 的 B 站分享卡片

可在配置中调整：

- 是否自动解析
- 是否附带封面图
- 自动解析回复模板
- 防抖时间

## 订阅推送

在插件配置的 `monitoring_settings.subscription_rules` 中添加规则：

- `source`: UP 主 UID 或空间链接，可多个，支持逗号或换行分隔。
- `allowed_targets`: 允许接收推送的会话 ID，可多个，支持逗号或换行分隔。

会话 ID 可通过 AstrBot 内置命令 `/sid` 获取。

监控逻辑说明：

- 首轮启动会为每个 UID 建立“最新视频基线”，不会补发历史内容。
- 后续轮询仅推送基线之后的新投稿。
- 手动执行 `/bili_check` 或 `/bili_check_all` 时，不会改写监控基线。

相关指令：

- `/bili_check`：立即检查第一条配置规则并推送。
- `/bili_check <UID或空间链接>`：检查指定 UP 主，并把最新视频发送到当前会话。
- `/bili_check_all`：立即检查全部配置规则。

## 模板变量

`parse_message_format` 和 `push_message_format` 支持以下变量：

- `{title}`
- `{up_name}`
- `{duration}`
- `{pub_time}`
- `{view}`
- `{like}`
- `{danmaku}`
- `{reply}`
- `{favorite}`
- `{coin}`
- `{share}`
- `{desc}`
- `{link}`
- `{bvid}`
- `{aid}`
- `{tname}`
- `{part_title}`

## 已知限制

- `send_direct_video` 仅在 B 站返回单文件视频流时可用。若视频为 DASH 分离音视频，插件会自动回退为图文卡片，不会在运行时调用 `ffmpeg` 合并。
- 当前监控对象只覆盖 UP 主视频投稿，不包含动态、直播、专栏。
