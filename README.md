# B站/A站解析推送助手

面向 AstrBot `aiocqhttp`（OneBot）的 Bilibili 与 AcFun 视频解析与推送插件。

## 更新说明

- `v1.0.0`: 新增 AcFun（A站）完整支持（被动解析、主动推送、视频下载、独立会话控制）；修正 AcFun 短视频时长解析错误。Bilibili 核心功能不受影响。
- `v0.4.2`: 按审核建议增强稳定性与可维护性：补齐 FFmpeg 合并超时保护、补充关键异常调试日志、为会话黑白名单增加无 `save_config` 环境下的 state 持久化兜底，并统一升级为 Python 3.10+ 类型注解写法。
- `v0.4.1`: 修正 QQ 表情附加方式，改为尝试直接给触发解析的原消息附加随机默认表情；重写 README。
- `v0.4.0`: 新增媒体缓存自动清理与手动清理命令，并明确当前仅支持 `aiocqhttp`（OneBot）。

完整变更记录见 [CHANGELOG.md](./CHANGELOG.md)。

## 支持范围

当前版本仅支持 AstrBot 的 `aiocqhttp`（OneBot）适配器。

补充说明：

- QQ 原消息表情附加依赖协议端支持 `set_msg_emoji_like` 扩展接口。
- 如果你的协议端不支持这个接口，解析功能仍可用，但"给原消息附加表情"不会生效。

---

## Bilibili（B站）

### 登录与 Cookie

推荐直接使用扫码登录：

- 发送 `/bili_login` 或 `登录B站`
- 扫码成功后，Cookie 会自动持久化保存
- 发送 `/bili_verify` 检查当前登录状态
- 发送 `/bili_logout` 清除本地登录状态

也支持手动填写：

- 在插件配置中填写 `auth_settings.bilibili_cookie`

凭证保存位置：

- `data/plugin_data/astrbot_plugin_bilibili_push/bilibili_credential.json`

说明：

- 登录相关指令仅机器人主人可用。
- 如果你已经填了有效 Cookie，插件会优先使用它。

### 核心功能

- 被动解析：识别 B 站链接、短链、QQ 卡片、`BV` 号、`av` 号。
- 发送策略：支持只发视频、视频+图文、只发图文。
- 会话控制：支持被动解析总开关、白名单、黑名单。
- 主动推送：支持按 UP 主 UID / 空间链接轮询新投稿。
- 缓存清理：默认每天自动清理媒体缓存；缓存超过 `1 GiB` 时强制清理；也可手动清理。

### B站配置重点

`被动解析设置`：

- `auto_parse_enabled`: 被动自动解析总开关
- `session_whitelist`: 允许触发解析的会话白名单
- `session_blacklist`: 禁止触发解析的会话黑名单
- `qq_link_emoji_enabled`: 是否尝试给触发解析的原消息附加随机默认表情

`推送与解析内容`：

- `send_direct_video`: 是否优先发送视频
- `send_rich_text`: 是否同时发送图文
- `video_max_duration_minutes`: 视频发送时长上限
- `video_max_size_mb`: 视频发送大小上限
- `parse_message_format`: 图文回复模板
- `push_message_format`: 主动推送图文模板

`主动监控规则`：

- `source`: UP 主 UID 或空间链接
- `allowed_targets`: 接收推送的会话 ID

### B站常用指令

- `/bili_login`
- `/bili_verify`
- `/bili_logout`
- `/bili_parse_on`
- `/bili_parse_off`
- `/bili_check`
- `/bili_check <UID或空间链接>`
- `/bili_check_all`
- `/bili_clear_cache`

---

## AcFun（A站）

### 核心功能

- 被动解析：识别 `acfun.cn/v/acXXX` 链接、移动端链接、QQ 分享卡片中的 AcFun 链接、独立 `acXXX` 编号。
- 发送策略：与 B 站一致，支持只发视频、视频+图文、只发图文。
- 会话控制：独立的 AcFun 被动解析总开关、白名单、黑名单。
- 主动推送：支持按 UP 主 UID / 个人主页链接（`acfun.cn/u/{id}`）轮询新投稿。

### AcFun 说明

- AcFun 无官方公开 API，无成熟 Python SDK，数据获取通过 AcFun 网站 ajaxpipe 机制实现。
- AcFun 公开视频数据无需登录/Cookie。
- 视频下载需要宿主环境安装 `ffmpeg`。
- B站与AcFun共享「运行参数」配置（检查间隔、防抖等）。

### AcFun 配置重点

`AcFun 被动解析设置`：

- `acfun_auto_parse_enabled`: AcFun 被动自动解析总开关
- `acfun_session_whitelist`: 允许触发 AcFun 解析的会话白名单
- `acfun_session_blacklist`: 禁止触发 AcFun 解析的会话黑名单
- `acfun_qq_link_emoji_enabled`: AcFun 链接触发时是否附加 QQ 表情

`AcFun 主动监控规则`：

- `source`: UP 主 UID 或个人主页链接（如 `https://www.acfun.cn/u/12345678`）
- `allowed_targets`: 接收推送的会话 ID

`AcFun 推送与解析内容`：

- `acfun_send_direct_video`: 是否优先发送视频
- `acfun_send_rich_text`: 是否同时发送图文
- `acfun_send_cover`: 图文消息附带封面图
- `acfun_video_max_duration_minutes`: 视频发送时长上限
- `acfun_video_max_size_mb`: 视频发送大小上限
- `acfun_parse_message_format`: 图文回复模板（支持 `{title}` `{up_name}` `{acid}` `{link}` `{duration}` `{pub_time}` `{view}` `{like}` `{danmaku}` `{banana}` `{stow}` `{comment}` `{share}` `{channel}` `{desc}`）
- `acfun_push_message_format`: 主动推送图文模板

### AcFun 常用指令

- `/acfun_parse_on`（别名：`开启A站解析`）
- `/acfun_parse_off`（别名：`关闭A站解析`）
- `/acfun_check`（别名：`accheck`）
- `/acfun_check <UID或主页链接>`
- `/acfun_check_all`（别名：`accheck_all`）

---

## 快速开始

1. 将插件目录放入 `AstrBot/data/plugins/astrbot_plugin_bilibili_push`。
2. 确认 AstrBot 使用的是 `aiocqhttp`（OneBot）适配器。
3. 安装依赖：

```bash
pip install -r requirements.txt
```

4. 建议在宿主环境安装 `ffmpeg`。
5. 在 AstrBot WebUI 中加载或重载插件。
6. 先配置 `被动解析设置`（B站）和/或 `AcFun 被动解析设置`，再按需要配置主动监控规则和推送内容。

## 缓存目录

- `data/plugin_data/astrbot_plugin_bilibili_push/media_cache`

## 参考

- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot 插件配置说明](https://docs.astrbot.app/dev/star/guides/plugin-config.html)
- [Zhalslar/astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)
- [ZASENJC/astrbot_plugin_weibo_push](https://github.com/ZASENJC/astrbot_plugin_weibo_push)
- [zhuweitung/acfun-api-collect](https://github.com/zhuweitung/acfun-api-collect)
