# B站解析推送助手

面向 AstrBot 的 Bilibili 视频解析与推送插件。

当前版本的设计重点只有一个：优先保证“被动识别指定群聊/用户发来的 B 站链接、卡片、BV 号并稳定解析发送”这条主链路正常运转；订阅推送、扫码登录、默认视频发送都围绕这条主链路服务。

## 版本概览

- `v0.2.0`: 默认优先发送视频文件、增加被动解析会话白名单/黑名单、增加 QQ `/zy` 表情提示、补齐 `CHANGELOG.md`。
- `v0.1.0`: 初始版本，完成 B 站被动解析、UP 主订阅推送、Cookie 持久化与扫码登录。

完整变更记录见 [CHANGELOG.md](./CHANGELOG.md)。

## 功能

- 被动解析：识别 `BV` 号、`av` 号、`bilibili.com/video/...`、`b23.tv` 短链、QQ JSON 分享卡片中的 B 站链接。
- 默认视频发送：被动解析和订阅推送都会默认优先尝试发送视频文件；失败时自动回退为图文卡片。
- 会话控制：支持被动解析会话白名单 / 黑名单，只在你配置的群聊或用户会话内触发。
- QQ 表情提示：在 `aiocqhttp` 平台下，检测到 QQ 链接/卡片解析时，结果消息前自动附带 QQ `/zy` 表情。
- 登录持久化：支持在配置中直接填写 Cookie，也支持通过指令扫码登录，凭证自动持久化到 `data/plugin_data/astrbot_plugin_bilibili_push/`。
- 主动推送：支持按 UP 主 UID / 空间链接轮询新投稿，并向指定会话主动推送。

## 快速开始

1. 将插件目录放入 `AstrBot/data/plugins/astrbot_plugin_bilibili_push`。
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 建议在宿主环境安装 `ffmpeg`。

说明：
- 当 B 站返回 DASH 分离音视频流时，插件需要 `ffmpeg` 合并后再发送完整视频。
- 如果没有 `ffmpeg` 或视频下载失败，插件会自动回退为文本加封面的图文卡片，不会中断主功能。

4. 在 AstrBot WebUI 中加载或重载插件。
5. 先配置 `passive_settings`，确保主功能只在你需要的会话中触发。
6. 如需订阅推送，再配置 `monitoring_settings.subscription_rules`。
7. 如需登录态，使用配置 Cookie 或执行 `/bili_login` 扫码登录。

## 被动解析主链路

这是插件的首要功能。

默认行为：
- 用户在允许的群聊 / 私聊会话中发送 B 站链接、分享卡片或 `BV` 号。
- 插件识别后优先尝试发送视频文件。
- 若视频无法下载、超出大小限制、缺少 `ffmpeg` 或出现请求异常，则回退为文本加封面图的解析结果。

被动解析会话控制：
- `passive_settings.session_whitelist`: 留空表示不限制；填写后仅白名单会话触发。
- `passive_settings.session_blacklist`: 黑名单中的会话不会触发。
- 也可在当前会话使用 `/bili_parse_on` 和 `/bili_parse_off` 快速开启或关闭。

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

## 常用配置

- `passive_settings.session_whitelist`: 被动解析会话白名单。
- `passive_settings.session_blacklist`: 被动解析会话黑名单。
- `passive_settings.qq_link_emoji_enabled`: 是否在 QQ 链接/卡片解析结果前附带 `/zy` 表情。
- `content_settings.send_direct_video`: 默认是否优先发送视频文件。
- `content_settings.send_cover`: 未发送视频时是否附带封面图。
- `content_settings.video_quality`: 视频清晰度优先级。
- `content_settings.video_codecs`: 视频编码优先级。
- `runtime_settings.video_max_size_mb`: 视频发送体积上限。
- `runtime_settings.video_download_timeout`: 视频下载超时秒数。
- `runtime_settings.debounce_seconds`: 同一会话内的被动解析防抖时间。

## 订阅推送

在插件配置的 `monitoring_settings.subscription_rules` 中添加规则：

- `source`: UP 主 UID 或空间链接，可多个，支持逗号或换行分隔。
- `allowed_targets`: 允许接收推送的会话 ID，可多个，支持逗号或换行分隔。

会话 ID 可通过 AstrBot 内置命令 `/sid` 获取。

监控逻辑说明：
- 首轮启动会为每个 UID 建立“最新视频基线”，不会补发历史内容。
- 后续轮询仅推送基线之后的新投稿。
- 手动执行 `/bili_check` 或 `/bili_check_all` 时，不会改写监控基线。

## 指令

- `/bili_login`
- `/bili_verify`
- `/bili_logout`
- `/bili_parse_on`
- `/bili_parse_off`
- `/bili_check`
- `/bili_check <UID或空间链接>`
- `/bili_check_all`

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
- `qq_link_emoji_enabled` 当前仅在 `aiocqhttp` 平台生效。
- 若 B 站返回 DASH 分离音视频且宿主环境缺少 `ffmpeg`，插件会回退为图文卡片。

## 官方参考

- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [Zhalslar/astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)
- [ZASENJC/astrbot_plugin_weibo_push](https://github.com/ZASENJC/astrbot_plugin_weibo_push)
