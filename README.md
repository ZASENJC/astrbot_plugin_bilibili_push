# B站解析推送助手

面向 AstrBot `aiocqhttp`（OneBot）的 Bilibili 视频解析与推送插件。

这个插件的核心目标只有一个：优先保证“被动识别指定群聊/用户发来的 B 站链接、卡片、BV 号并稳定解析发送”这条主链路正常运转。主动监控、扫码登录、视频发送策略都围绕这条主链路服务。

## 版本概览

- `v0.4.0`: QQ 表情改为随机默认表情，新增媒体缓存自动清理与手动清理命令，并明确当前仅支持 `aiocqhttp`（OneBot）。
- `v0.3.0`: 重构配置页分组，拆分“视频发送”和“图文发送”开关，新增视频时长上限，优化配置说明。
- `v0.2.0`: 默认优先发送视频文件、增加被动解析会话白名单/黑名单、增加 QQ `/zy` 表情提示、补齐 `CHANGELOG.md`。
- `v0.1.0`: 初始版本，完成 B 站被动解析、UP 主订阅推送、Cookie 持久化与扫码登录。

完整变更记录见 [CHANGELOG.md](./CHANGELOG.md)。

## 支持范围

当前版本仅支持 AstrBot 的 `aiocqhttp`（OneBot）适配器。

## 功能

- 被动解析：识别 `BV` 号、`av` 号、`bilibili.com/video/...`、`b23.tv` 短链、QQ JSON 分享卡片中的 B 站链接。
- 视频发送：默认优先发送视频；视频不可用时自动回退为图文。
- 图文发送：可单独发送图文，也可和视频一起发送。
- 会话控制：支持被动解析总开关、会话白名单 / 黑名单。
- QQ 表情提示：在 `aiocqhttp` 平台下，QQ 链接/卡片解析结果前可随机附带一个 QQ 默认表情。
- 登录持久化：支持填写 Cookie 或扫码登录，凭证持久化到 `data/plugin_data/astrbot_plugin_bilibili_push/`。
- 主动推送：支持按 UP 主 UID / 空间链接轮询新投稿，并向指定会话主动推送。
- 缓存维护：媒体缓存默认每天自动清理一次；缓存占用超过 `1 GiB` 时会强制清理，也可手动触发清理。

## 快速开始

1. 将插件目录放入 `AstrBot/data/plugins/astrbot_plugin_bilibili_push`。
2. 确认 AstrBot 使用的是 `aiocqhttp`（OneBot）适配器。
3. 安装依赖：

```bash
pip install -r requirements.txt
```

4. 建议在宿主环境安装 `ffmpeg`。
5. 在 AstrBot WebUI 中加载或重载插件。
6. 先配置 `被动解析设置`，确保主功能只在你需要的会话中触发。
7. 如需主动推送，再配置 `主动监控规则`。
8. 如需登录态，填写 Cookie 或执行 `/bili_login` 扫码登录。

说明：
- 当 B 站返回 DASH 分离音视频流时，插件需要 `ffmpeg` 合并后再发送完整视频。
- 如果没有 `ffmpeg`、视频超限或下载失败，插件会自动回退为图文，不会中断主功能。

## 被动解析主链路

这是插件的首要功能。

默认流程：
- 用户在允许的群聊 / 私聊会话中发送 B 站链接、分享卡片或 `BV` 号。
- 插件识别后，默认优先尝试发送视频。
- 若视频不可用，则自动回退为图文。
- 若命中 QQ 链接/卡片解析场景，可在消息前随机附带一个 QQ 默认表情。

`被动解析设置` 里的关键项：
- `auto_parse_enabled`: 被动自动解析总开关。
- `session_whitelist`: 留空表示不限制；填写后仅白名单会话触发。
- `session_blacklist`: 黑名单中的会话不会触发。
- `qq_link_emoji_enabled`: 是否在 QQ 链接/卡片解析结果前附带随机默认表情。

也可在当前会话使用：
- `/bili_parse_on`
- `/bili_parse_off`

## 发送策略

`推送与解析内容` 里的发送开关互相独立：

- `send_direct_video = true`，`send_rich_text = false`：默认模式，只发视频；若视频不可用，自动回退为图文。
- `send_direct_video = true`，`send_rich_text = true`：视频 + 图文一起发。
- `send_direct_video = false`，`send_rich_text = true`：只发图文。
- 若两个开关都关闭，插件仍会自动回退为图文，避免消息完全不发送。

视频会在以下情况下强制改为图文：
- 超过 `video_max_duration_minutes`
- 超过 `video_max_size_mb`
- 缺少 `ffmpeg`
- 下载或合并失败

图文相关项：
- `send_cover`: 图文消息是否附带封面图。
- `description_length`: 图文简介截断长度。
- `parse_message_format`: 图文回复模板。
- `push_message_format`: 主动推送图文模板。

视频相关项：
- `send_direct_video`: 是否优先发送视频。
- `video_max_duration_minutes`: 视频发送时长上限，填 `0` 表示不限制。
- `video_max_size_mb`: 视频发送大小上限。
- `video_quality`: 视频清晰度优先级。
- `video_codecs`: 视频编码优先级。

## 媒体缓存

媒体缓存目录：

- `data/plugin_data/astrbot_plugin_bilibili_push/media_cache`

清理策略：
- 默认每天自动清理一次。
- 当缓存占用超过 `1 GiB` 时，会执行强制清理。
- 可使用 `/bili_clear_cache` 手动清理。

## 登录与 Cookie

支持两种方式：

1. 在插件配置中填写 `auth_settings.bilibili_cookie`。
2. 发送 `/bili_login` 或 `登录B站` 扫码登录。

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

## 主动监控与推送

在插件配置的 `monitoring_settings.subscription_rules` 中添加规则：

- `source`: UP 主 UID 或空间链接，可多个，支持逗号或换行分隔。
- `allowed_targets`: 接收推送的会话 ID，可多个，支持逗号或换行分隔。

会话 ID 可通过 AstrBot 内置命令 `/sid` 获取。

监控逻辑说明：
- 首轮启动会为每个 UID 建立“最新视频基线”，不会补发历史内容。
- 后续轮询仅推送基线之后的新投稿。
- 手动执行 `/bili_check` 或 `/bili_check_all` 时，不会改写监控基线。

## 运行参数

`runtime_settings` 里主要控制轮询和请求节奏：

- `check_interval`: 主动监控检查间隔。
- `check_interval_jitter`: 检查间隔随机浮动。
- `request_interval`: 单次请求间隔。
- `request_interval_jitter`: 请求间隔随机浮动。
- `debounce_seconds`: 被动解析防抖时间。
- `latest_fetch_limit`: 每轮拉取的最新视频数量。
- `video_download_timeout`: 视频下载超时秒数。

## 指令

- `/bili_login`
- `/bili_verify`
- `/bili_logout`
- `/bili_parse_on`
- `/bili_parse_off`
- `/bili_check`
- `/bili_check <UID或空间链接>`
- `/bili_check_all`
- `/bili_clear_cache`

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

- 当前监控对象只覆盖 UP 主视频投稿，不包含动态、直播、专栏。
- 当前版本仅支持 AstrBot 的 `aiocqhttp`（OneBot）适配器。
- 若 B 站返回 DASH 分离音视频且宿主环境缺少 `ffmpeg`，插件会回退为图文。

## 官方参考

- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot 插件配置说明](https://docs.astrbot.app/dev/star/guides/plugin-config.html)
- [Zhalslar/astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)
- [ZASENJC/astrbot_plugin_weibo_push](https://github.com/ZASENJC/astrbot_plugin_weibo_push)
