from __future__ import annotations

import asyncio
import ipaddress
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Iterable, Sequence
from urllib.parse import parse_qs, urlparse

import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Image, Json, Plain, Video as MessageVideo
from astrbot.api.star import Context, Star, StarTools
from bilibili_api import Credential, request_settings, select_client
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents
from bilibili_api.user import User, VideoOrder, get_self_info
from bilibili_api.video import (
    AudioStreamDownloadURL,
    Video,
    VideoCodecs,
    VideoDownloadURLDataDetecter,
    VideoQuality,
    VideoStreamDownloadURL,
)

PLUGIN_NAME = "astrbot_plugin_bilibili_push"
QQ_DEFAULT_FACE_IDS = (1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 16, 21, 24)

DEFAULT_CHECK_INTERVAL_MINUTES = 10
DEFAULT_REQUEST_INTERVAL_SECONDS = 2
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_VIDEO_DOWNLOAD_TIMEOUT_SECONDS = 300
DEFAULT_DEBOUNCE_SECONDS = 300
DEFAULT_VIDEO_SEND_FAILURE_COOLDOWN_SECONDS = 600
DEFAULT_FETCH_LIMIT = 5
DEFAULT_DESC_LENGTH = 120
DEFAULT_VIDEO_MAX_SIZE_MB = 90
DEFAULT_VIDEO_MAX_DURATION_MINUTES = 30
MEDIA_CACHE_DAILY_CLEANUP_SECONDS = 24 * 60 * 60
MEDIA_CACHE_FORCE_LIMIT_BYTES = 1024 * 1024 * 1024
STARTUP_DELAY_SECONDS = 10
QR_POLL_SECONDS = 2
QR_MAX_POLLS = 45
STATE_KEY_PASSIVE_SESSION_BLACKLIST = "runtime_session_blacklist"
STATE_KEY_PASSIVE_SESSION_WHITELIST = "runtime_session_whitelist"

DEFAULT_PARSE_TEMPLATE = (
    "📺 {title}\n"
    "UP: {up_name}\n"
    "时长: {duration}\n"
    "发布时间: {pub_time}\n"
    "播放: {view}  点赞: {like}  弹幕: {danmaku}\n"
    "简介: {desc}\n"
    "链接: {link}"
)
DEFAULT_PUSH_TEMPLATE = (
    "🔔 {up_name} 投稿了新视频\n\n"
    "{title}\n"
    "时长: {duration}\n"
    "发布时间: {pub_time}\n"
    "播放: {view}  点赞: {like}\n"
    "简介: {desc}\n"
    "链接: {link}"
)

URL_PATTERN = re.compile(
    r'(?P<url>(?:https?://)?(?:www\.)?(?:b23\.tv|bili2233\.cn|(?:m\.)?bilibili\.com|space\.bilibili\.com)[^\s<>"\']+)',
    re.IGNORECASE,
)
BV_PATTERN = re.compile(r"\b(?P<bvid>BV[0-9A-Za-z]{10})\b")
AV_PATTERN = re.compile(r"\b(?P<avid>av\d{6,})\b", re.IGNORECASE)
VIDEO_BV_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?bilibili\.com/video/(?P<bvid>BV[0-9A-Za-z]{10})",
    re.IGNORECASE,
)
VIDEO_AV_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?bilibili\.com/video/(?P<avid>av\d{6,})",
    re.IGNORECASE,
)
SHORT_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?(?:b23\.tv|bili2233\.cn)/",
    re.IGNORECASE,
)
SPACE_UID_PATTERN = re.compile(
    r"(?:https?://)?space\.bilibili\.com/(?P<uid>\d+)",
    re.IGNORECASE,
)
SPACE_UID_QUERY_PATTERN = re.compile(r"(?:uid|mid|vmid)=(?P<uid>\d+)", re.IGNORECASE)
BILIBILI_SCHEME_AV_PATTERN = re.compile(r"bilibili://video/av(?P<avid>\d+)", re.IGNORECASE)
TRAILING_PUNCTUATION = "'\"）)]】}>，。！？；：,.!?;:"
TRUSTED_COVER_HOST_SUFFIXES = (
    "hdslb.com",
    "bilibili.com",
    "acfun.cn",
    "aixifan.com",
)

ACFUN_DEFAULT_PARSE_TEMPLATE = (
    "📺 {title}\n"
    "UP: {up_name}\n"
    "时长: {duration}\n"
    "发布时间: {pub_time}\n"
    "播放: {view}  点赞: {like}  弹幕: {danmaku}  香蕉: {banana}\n"
    "简介: {desc}\n"
    "链接: {link}"
)
ACFUN_DEFAULT_PUSH_TEMPLATE = (
    "🔔 {up_name} 投稿了新视频\n\n"
    "{title}\n"
    "时长: {duration}\n"
    "发布时间: {pub_time}\n"
    "播放: {view}  点赞: {like}\n"
    "简介: {desc}\n"
    "链接: {link}"
)

ACFUN_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?acfun\.cn/v/[\?]?ac(\d+)",
    re.IGNORECASE,
)
ACFUN_USER_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?acfun\.cn/u/(\d+)",
    re.IGNORECASE,
)
ACID_PATTERN = re.compile(r"\bac(\d{1,12})\b", re.IGNORECASE)
ACFUN_VIDEOINFO_PATTERN = re.compile(
    r"window\.videoInfo\s*=\s*", re.IGNORECASE
)
ACFUN_AJAXPIPE_TRAILING = re.compile(r"/\*.*?\*/$")
ACFUN_HREF_ACID_PATTERN = re.compile(r'href="/v/(ac\d+)"', re.IGNORECASE)

try:
    select_client("curl_cffi")
    request_settings.set("impersonate", "chrome131")
except Exception as err:
    logger.warning(f"BilibiliPush: 初始化 bilibili-api 客户端失败，将使用默认客户端: {err}")


class MediaDownloadError(Exception):
    pass


class MediaSizeLimitError(MediaDownloadError):
    pass


@dataclass(frozen=True)
class MonitorRule:
    uid: int
    targets: tuple[str, ...]
    source: str


@dataclass
class FeedVideoItem:
    aid: int
    bvid: str
    title: str
    created_ts: int
    author: str
    cover_url: str = ""
    desc: str = ""


@dataclass
class ParseTarget:
    bvid: str | None = None
    aid: int | None = None
    page_num: int = 1
    raw_input: str = ""
    source_kind: str = "code"


@dataclass(frozen=True)
class AcFunMonitorRule:
    user_id: int
    targets: tuple[str, ...]
    source: str


@dataclass
class AcFunVideoItem:
    acid: str
    title: str
    created_ts: int
    author: str
    cover_url: str = ""
    desc: str = ""


@dataclass
class AcFunParseTarget:
    acid: str
    raw_input: str = ""
    source_kind: str = "code"


@dataclass
class AcFunVideoCard:
    acid: str
    title: str
    link: str
    up_name: str
    cover_url: str
    desc: str
    duration_seconds: int
    pub_ts: int
    view: int
    like: int
    danmaku: int
    banana: int
    stow: int
    comment: int
    share: int
    channel: str = ""
    video_path: Path | None = None

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration_seconds)


@dataclass
class VideoCard:
    aid: int
    bvid: str
    title: str
    link: str
    up_name: str
    cover_url: str
    desc: str
    duration_seconds: int
    pub_ts: int
    view: int
    like: int
    danmaku: int
    reply: int
    favorite: int
    coin: int
    share: int
    tname: str = ""
    part_title: str = ""
    video_path: Path | None = None

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration_seconds)


class SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class DebounceCache:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self._link_cache: dict[tuple[str, str], float] = {}
        self._resource_cache: dict[tuple[str, str], float] = {}

    def update_ttl(self, ttl_seconds: int) -> None:
        self.ttl_seconds = max(0, ttl_seconds)

    def hit_link(self, session: str, key: str) -> bool:
        return self._hit(self._link_cache, session, key)

    def hit_resource(self, session: str, key: str) -> bool:
        return self._hit(self._resource_cache, session, key)

    def _hit(self, cache: dict[tuple[str, str], float], session: str, key: str) -> bool:
        if self.ttl_seconds <= 0:
            return False
        self._cleanup(cache)
        now = time.time()
        composite = (session, key)
        expires_at = cache.get(composite, 0)
        if expires_at > now:
            return True
        cache[composite] = now + self.ttl_seconds
        return False

    def _cleanup(self, cache: dict[tuple[str, str], float]) -> None:
        if not cache:
            return
        now = time.time()
        expired = [key for key, deadline in cache.items() if deadline <= now]
        for key in expired:
            cache.pop(key, None)


class BilibiliCredentialManager:
    def __init__(self, data_dir: Path, auth_config_getter) -> None:
        self.credential_file = data_dir / "bilibili_credential.json"
        self.auth_config_getter = auth_config_getter
        self._credential: Credential | None = None
        self._qr_login: QrCodeLogin | None = None
        self._lock = asyncio.Lock()
        self._last_manual_cookie: str | None = None

    def _manual_cookie(self) -> str:
        config = self.auth_config_getter() or {}
        return str(config.get("bilibili_cookie", "") or "").strip()

    def _save_credential(self) -> None:
        if self._credential is None:
            return
        self.credential_file.write_text(
            json.dumps(self._credential.get_cookies(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_credential(self) -> None:
        if not self.credential_file.exists():
            return
        try:
            raw = json.loads(self.credential_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._credential = Credential.from_cookies(raw)
        except Exception as err:
            logger.error(f"BilibiliPush: 读取凭证文件失败: {err}")

    def _cookies_to_dict(self, cookies_str: str) -> dict[str, str]:
        cookies: dict[str, str] = {}
        for item in cookies_str.split(";"):
            part = item.strip()
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            cookies[name.strip()] = value.strip()
        return cookies

    async def get_credential(self) -> Credential | None:
        async with self._lock:
            manual_cookie = self._manual_cookie()
            if manual_cookie and manual_cookie != self._last_manual_cookie:
                self._last_manual_cookie = manual_cookie
                try:
                    manual_credential = Credential.from_cookies(
                        self._cookies_to_dict(manual_cookie)
                    )
                    if await manual_credential.check_valid():
                        self._credential = manual_credential
                        self._save_credential()
                        logger.info(
                            f"BilibiliPush: 已将配置中的 Cookie 持久化到 {self.credential_file}"
                        )
                    else:
                        logger.warning("BilibiliPush: 配置中的 Bilibili Cookie 无效，将尝试使用本地持久化凭证")
                except Exception as err:
                    logger.error(f"BilibiliPush: 校验配置中的 Cookie 失败: {err}")

            if self._credential is None:
                self._load_credential()

            if self._credential is None:
                return None

            try:
                if not await self._credential.check_valid():
                    logger.warning("BilibiliPush: 当前 Bilibili 凭证无效，请重新登录")
                    return None
                if await self._credential.check_refresh():
                    if self._credential.has_ac_time_value() and self._credential.has_bili_jct():
                        await self._credential.refresh()
                        self._save_credential()
                        logger.info("BilibiliPush: 已自动刷新并持久化 Bilibili 凭证")
                    else:
                        logger.warning(
                            "BilibiliPush: 凭证需要刷新，但缺少 bili_jct 或 ac_time_value，无法自动刷新"
                        )
            except Exception as err:
                logger.error(f"BilibiliPush: 检查或刷新凭证失败: {err}")
                return None

            return self._credential

    async def login_with_qrcode(self) -> bytes:
        self._qr_login = QrCodeLogin()
        await self._qr_login.generate_qrcode()
        return self._qr_login.get_qrcode_picture().content

    async def check_qr_state(self) -> AsyncGenerator[str, None]:
        if self._qr_login is None:
            yield "二维码尚未生成，请先执行登录指令。"
            return

        scan_tip_pending = True
        for _ in range(QR_MAX_POLLS):
            try:
                state = await self._qr_login.check_state()
            except Exception as err:
                yield f"检查二维码状态失败: {err}"
                return

            if state == QrCodeLoginEvents.DONE:
                self._credential = self._qr_login.get_credential()
                self._save_credential()
                try:
                    profile = await get_self_info(self._credential)
                    uname = str(profile.get("name") or "未知账号")
                    uid = str(profile.get("mid") or "未知UID")
                    yield f"登录成功，当前账号: {uname} (UID: {uid})"
                except Exception:
                    yield "登录成功，凭证已持久化保存。"
                return
            if state == QrCodeLoginEvents.CONF:
                if scan_tip_pending:
                    yield "二维码已扫描，请在哔哩哔哩客户端确认登录。"
                    scan_tip_pending = False
            elif state == QrCodeLoginEvents.TIMEOUT:
                yield "二维码已过期，请重新执行登录指令。"
                return

            await asyncio.sleep(QR_POLL_SECONDS)

        yield "二维码登录超时，请重新执行登录指令。"

    async def verify(self) -> tuple[bool, str]:
        credential = await self.get_credential()
        if credential is None:
            return False, "当前没有可用的 Bilibili 登录态。"
        try:
            profile = await get_self_info(credential)
            uname = str(profile.get("name") or "未知账号")
            uid = str(profile.get("mid") or "未知UID")
            return True, f"Cookie 有效，当前账号: {uname} (UID: {uid})"
        except Exception as err:
            logger.error(f"BilibiliPush: 校验登录态失败: {err}")
            return False, f"登录态校验失败: {err}"

    async def clear(self) -> None:
        async with self._lock:
            self._credential = None
            self._qr_login = None
            self._last_manual_cookie = None
            try:
                self.credential_file.unlink(missing_ok=True)
            except Exception as err:
                logger.error(f"BilibiliPush: 删除本地凭证失败: {err}")


class BilibiliService:
    def __init__(
        self,
        client: httpx.AsyncClient,
        credential_manager: BilibiliCredentialManager,
        content_config_getter,
        runtime_config_getter,
        cache_dir: Path,
    ) -> None:
        self.client = client
        self.credential_manager = credential_manager
        self.content_config_getter = content_config_getter
        self.runtime_config_getter = runtime_config_getter
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_headers = {
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }

    def _video_push_requested(self) -> bool:
        content_config = self.content_config_getter() or {}
        return bool(content_config.get("send_direct_video", True))

    def _video_duration_limit_seconds(self) -> int:
        content_config = self.content_config_getter() or {}
        limit_minutes = safe_int(
            content_config.get(
                "video_max_duration_minutes",
                DEFAULT_VIDEO_MAX_DURATION_MINUTES,
            ),
            DEFAULT_VIDEO_MAX_DURATION_MINUTES,
            minimum=0,
            maximum=24 * 60,
        )
        if limit_minutes <= 0:
            return 0
        return limit_minutes * 60

    def _should_attempt_video_download(self, duration_seconds: int) -> bool:
        if not self._video_push_requested():
            return False
        limit_seconds = self._video_duration_limit_seconds()
        if limit_seconds > 0 and duration_seconds > limit_seconds:
            return False
        return True

    def resolve_uid(self, source: str) -> int | None:
        candidate = source.strip()
        if not candidate:
            return None
        if candidate.isdigit():
            return int(candidate)
        if match := SPACE_UID_PATTERN.search(candidate):
            return int(match.group("uid"))
        if match := SPACE_UID_QUERY_PATTERN.search(candidate):
            return int(match.group("uid"))
        return None

    async def resolve_short_url(self, url: str) -> str:
        normalized = ensure_scheme(strip_trailing_punctuation(url))
        response = await self.client.get(normalized, follow_redirects=True)
        return str(response.url)

    async def fetch_recent_videos(self, uid: int, limit: int) -> list[FeedVideoItem]:
        credential = await self.credential_manager.get_credential()
        user = User(uid, credential=credential)
        page_size = max(1, min(limit, 30))
        primary_error: Exception | None = None
        try:
            payload = await user.get_videos(ps=page_size, order=VideoOrder.PUBDATE)
        except Exception as err:
            primary_error = err
            logger.warning(
                f"BilibiliPush: 获取 UID={uid} 投稿列表失败，尝试使用 medialist 接口: {type(err).__name__}"
            )
        else:
            return self._parse_user_video_items(payload)

        try:
            payload = await user.get_media_list(ps=page_size, desc=True)
        except Exception as fallback_err:
            raise RuntimeError(
                f"获取 UID={uid} 投稿列表失败，medialist 回退也失败: {type(fallback_err).__name__}"
            ) from None
        return self._parse_media_list_items(payload)

    def _parse_user_video_items(self, payload: Any) -> list[FeedVideoItem]:
        section = payload.get("list") if isinstance(payload, dict) else None
        raw_items = []
        if isinstance(section, dict):
            raw_items = section.get("vlist") or []
        elif isinstance(payload, dict):
            raw_items = payload.get("vlist") or []
        return self._build_feed_items(raw_items, source="user_video")

    def _parse_media_list_items(self, payload: Any) -> list[FeedVideoItem]:
        raw_items = []
        if isinstance(payload, dict):
            raw_items = payload.get("media_list") or []
            section = payload.get("list")
            if not raw_items and isinstance(section, dict):
                raw_items = section.get("media_list") or []
        return self._build_feed_items(raw_items, source="media_list")

    def _build_feed_items(self, raw_items: Any, *, source: str) -> list[FeedVideoItem]:
        items: list[FeedVideoItem] = []
        if not isinstance(raw_items, list):
            return items

        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            if source == "media_list":
                bvid = str(raw.get("bv_id") or raw.get("bvid") or "")
                aid = safe_int(raw.get("id") or raw.get("aid"), 0)
                created_ts = safe_int(raw.get("pubtime") or raw.get("created"), 0)
                cover_url = normalize_cover_url(raw.get("cover") or raw.get("pic") or "")
                desc = str(raw.get("intro") or raw.get("description") or "")
            else:
                bvid = str(raw.get("bvid") or raw.get("bv_id") or "")
                aid = safe_int(raw.get("aid") or raw.get("id"), 0)
                created_ts = safe_int(raw.get("created") or raw.get("pubtime"), 0)
                cover_url = normalize_cover_url(raw.get("pic") or raw.get("cover") or "")
                desc = str(raw.get("description") or raw.get("intro") or "")
            if not BV_PATTERN.fullmatch(bvid) or aid <= 0:
                continue
            items.append(
                FeedVideoItem(
                    aid=aid,
                    bvid=bvid,
                    title=str(raw.get("title") or "未命名视频"),
                    created_ts=created_ts,
                    author=self._extract_feed_author(raw),
                    cover_url=cover_url,
                    desc=desc,
                )
            )
        return items

    def _extract_feed_author(self, raw: dict[str, Any]) -> str:
        author = raw.get("author") or raw.get("up_name")
        if not author:
            upper = raw.get("upper") or raw.get("owner")
            if isinstance(upper, dict):
                author = upper.get("name") or upper.get("uname")
        return str(author or "未知UP")

    async def fetch_video_card(self, target: ParseTarget) -> VideoCard:
        credential = await self.credential_manager.get_credential()
        if target.aid:
            video = Video(aid=target.aid, credential=credential)
        elif target.bvid:
            video = Video(bvid=target.bvid, credential=credential)
        else:
            raise ValueError("缺少视频标识")

        info = await video.get_info()
        pages = info.get("pages") or []
        page_index = max(0, target.page_num - 1)
        if page_index >= len(pages):
            page_index = 0
        page_info = pages[page_index] if pages else {}

        title = str(info.get("title") or "未命名视频")
        part_title = str(page_info.get("part") or "")
        if len(pages) > 1 and part_title and part_title != title:
            title = f"{title} [P{page_index + 1} {part_title}]"

        stat = info.get("stat") or {}
        owner = info.get("owner") or {}
        pub_ts = safe_int(info.get("pubdate") or info.get("ctime"), 0)
        bvid = str(info.get("bvid") or target.bvid or "")
        aid = safe_int(info.get("aid") or target.aid, 0)
        link = f"https://www.bilibili.com/video/{bvid}"
        if page_index > 0:
            link += f"?p={page_index + 1}"

        duration_seconds = safe_int(page_info.get("duration") or info.get("duration"), 0)

        video_path: Path | None = None
        if self._should_attempt_video_download(duration_seconds):
            try:
                video_path = await self._prepare_video_file(video, bvid, page_index)
            except MediaSizeLimitError as err:
                logger.warning(f"BilibiliPush: 视频过大，回退为图文卡片: {err}")
            except Exception as err:
                logger.warning(f"BilibiliPush: 生成视频文件失败，回退为图文卡片: {err}")

        return VideoCard(
            aid=aid,
            bvid=bvid,
            title=title,
            link=link,
            up_name=str(owner.get("name") or "未知UP"),
            cover_url=normalize_cover_url(info.get("pic") or ""),
            desc=str(info.get("desc") or ""),
            duration_seconds=duration_seconds,
            pub_ts=pub_ts,
            view=safe_int(stat.get("view"), 0),
            like=safe_int(stat.get("like"), 0),
            danmaku=safe_int(stat.get("danmaku"), 0),
            reply=safe_int(stat.get("reply"), 0),
            favorite=safe_int(stat.get("favorite"), 0),
            coin=safe_int(stat.get("coin"), 0),
            share=safe_int(stat.get("share"), 0),
            tname=str(info.get("tname") or ""),
            part_title=part_title,
            video_path=video_path,
        )

    async def _prepare_video_file(self, video: Video, bvid: str, page_index: int) -> Path:
        runtime_config = self.runtime_config_getter() or {}
        content_config = self.content_config_getter() or {}

        max_size_mb = safe_int(
            content_config.get(
                "video_max_size_mb",
                runtime_config.get("video_max_size_mb", DEFAULT_VIDEO_MAX_SIZE_MB),
            ),
            DEFAULT_VIDEO_MAX_SIZE_MB,
            minimum=10,
            maximum=2048,
        )
        timeout_seconds = safe_int(
            runtime_config.get(
                "video_download_timeout",
                DEFAULT_VIDEO_DOWNLOAD_TIMEOUT_SECONDS,
            ),
            DEFAULT_VIDEO_DOWNLOAD_TIMEOUT_SECONDS,
            minimum=20,
            maximum=3600,
        )
        max_bytes = max_size_mb * 1024 * 1024

        quality_name = str(content_config.get("video_quality", "_720P")).upper()
        codec_name = str(content_config.get("video_codecs", "AVC")).upper()
        quality = getattr(VideoQuality, quality_name, VideoQuality._720P)
        codecs = getattr(VideoCodecs, codec_name, VideoCodecs.AVC)

        download_url_data = await video.get_download_url(page_index=page_index)
        detecter = VideoDownloadURLDataDetecter(download_url_data)
        streams = detecter.detect_best_streams(
            video_max_quality=quality,
            codecs=[codecs],
            no_dolby_video=True,
            no_hdr=True,
        )
        if not streams:
            raise MediaDownloadError("未找到可下载的视频流")

        video_stream = streams[0]
        video_url = self._stream_url(video_stream)
        if not video_url:
            raise MediaDownloadError("视频流解析失败")

        audio_stream = streams[1] if len(streams) > 1 else None
        audio_url = self._stream_url(audio_stream) if isinstance(audio_stream, AudioStreamDownloadURL) else None

        safe_quality = re.sub(r"[^A-Za-z0-9_]+", "_", quality_name)
        safe_codec = re.sub(r"[^A-Za-z0-9_]+", "_", codec_name)
        if isinstance(video_stream, VideoStreamDownloadURL):
            stream_label = f"{safe_quality}-{safe_codec}"
        else:
            stream_label = "direct"
        stem = f"{bvid}-p{page_index + 1}-{stream_label}"
        output_path = self.cache_dir / f"{stem}.mp4"
        if output_path.exists() and output_path.stat().st_size > 0:
            if output_path.stat().st_size > max_bytes:
                await safe_unlink(output_path)
                raise MediaSizeLimitError(f"缓存视频文件超过 {max_size_mb} MB 限制")
            return output_path

        headers = await self._build_media_headers()
        if audio_url:
            video_temp = self.cache_dir / f"{stem}.video{suffix_from_url(video_url, '.m4s')}"
            audio_temp = self.cache_dir / f"{stem}.audio{suffix_from_url(audio_url, '.m4s')}"
            try:
                await asyncio.gather(
                    self._download_stream(video_url, video_temp, headers, timeout_seconds, max_bytes),
                    self._download_stream(audio_url, audio_temp, headers, timeout_seconds, max_bytes),
                )
                if video_temp.stat().st_size + audio_temp.stat().st_size > max_bytes:
                    raise MediaSizeLimitError(
                        f"预计合并后文件超过 {max_size_mb} MB 限制"
                    )
                await merge_av(
                    video_temp,
                    audio_temp,
                    output_path,
                    timeout_seconds=timeout_seconds,
                )
            except Exception:
                await safe_unlink(video_temp)
                await safe_unlink(audio_temp)
                await safe_unlink(output_path)
                raise
        else:
            await self._download_stream(
                video_url,
                output_path,
                headers,
                timeout_seconds,
                max_bytes,
            )

        if output_path.stat().st_size > max_bytes:
            await safe_unlink(output_path)
            raise MediaSizeLimitError(f"视频文件超过 {max_size_mb} MB 限制")

        return output_path

    def _stream_url(self, stream: Any) -> str:
        url = getattr(stream, "url", "")
        return str(url or "")

    async def _build_media_headers(self) -> dict[str, str]:
        headers = dict(self.default_headers)
        credential = await self.credential_manager.get_credential()
        if credential is not None:
            cookie = "; ".join(
                f"{key}={value}"
                for key, value in credential.get_cookies().items()
                if value
            )
            if cookie:
                headers["Cookie"] = cookie
        return headers

    async def _download_stream(
        self,
        url: str,
        output_path: Path,
        headers: dict[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> Path:
        if output_path.exists() and output_path.stat().st_size > 0:
            if output_path.stat().st_size > max_bytes:
                await safe_unlink(output_path)
                raise MediaSizeLimitError("缓存流文件超过大小限制")
            return output_path

        temp_path = output_path.with_suffix(output_path.suffix + ".part")
        await safe_unlink(temp_path)

        try:
            async with self.client.stream(
                "GET",
                url,
                headers=headers,
                timeout=timeout_seconds,
                follow_redirects=True,
            ) as response:
                response.raise_for_status()
                content_length = safe_int(response.headers.get("content-length"), 0)
                if content_length and content_length > max_bytes:
                    raise MediaSizeLimitError("下载源文件超过大小限制")

                written = 0
                with open(temp_path, "wb") as file_obj:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise MediaSizeLimitError("下载过程中超过大小限制")
                        file_obj.write(chunk)

                if written <= 0:
                    raise MediaDownloadError("下载结果为空文件")

            temp_path.replace(output_path)
            return output_path
        except Exception:
            await safe_unlink(temp_path)
            raise


class LinkResolver:
    def __init__(self, service: BilibiliService) -> None:
        self.service = service

    async def extract_parse_target(
        self,
        messages: Sequence[Any],
        text: str,
    ) -> ParseTarget | None:
        candidate, source_kind = self._extract_candidate(messages, text)
        if not candidate:
            return None
        return await self._normalize_candidate(candidate, source_kind)

    def _extract_candidate(
        self,
        messages: Sequence[Any],
        text: str,
    ) -> tuple[str | None, str]:
        direct, source_kind = self._extract_from_text(text)
        if direct:
            return direct, source_kind

        for component in messages:
            if isinstance(component, Json):
                card_url = extract_json_url(component.data)
                if card_url:
                    return card_url, "card"
                candidate = self._extract_from_json(component.data)
                if candidate:
                    return candidate, "card"

        return None, "code"

    def _extract_from_text(self, text: str) -> tuple[str | None, str]:
        if not text:
            return None, "code"
        stripped = text.strip()
        if stripped.startswith("/"):
            return None, "code"
        if match := URL_PATTERN.search(stripped):
            return strip_trailing_punctuation(match.group("url")), "link"
        if match := BV_PATTERN.search(stripped):
            return match.group("bvid"), "code"
        if match := AV_PATTERN.search(stripped):
            return match.group("avid"), "code"
        return None, "code"

    def _extract_from_json(self, payload: Any) -> str | None:
        for value in iter_string_values(payload):
            candidate, _ = self._extract_from_text(value)
            if candidate:
                return candidate
        return None

    async def _normalize_candidate(
        self,
        candidate: str,
        source_kind: str,
    ) -> ParseTarget | None:
        cleaned = strip_trailing_punctuation(candidate)
        if SHORT_URL_PATTERN.search(cleaned):
            try:
                cleaned = await self.service.resolve_short_url(cleaned)
            except Exception as err:
                logger.error(f"BilibiliPush: 解析短链失败 {candidate}: {err}")
                return None

        if match := BV_PATTERN.search(cleaned):
            page_num = extract_page_num(cleaned)
            return ParseTarget(
                bvid=match.group("bvid"),
                page_num=page_num,
                raw_input=candidate,
                source_kind=source_kind,
            )

        if match := VIDEO_BV_URL_PATTERN.search(cleaned):
            page_num = extract_page_num(cleaned)
            return ParseTarget(
                bvid=match.group("bvid"),
                page_num=page_num,
                raw_input=candidate,
                source_kind=source_kind,
            )

        if match := BILIBILI_SCHEME_AV_PATTERN.search(cleaned):
            return ParseTarget(
                aid=safe_int(match.group("avid"), 0),
                raw_input=candidate,
                source_kind=source_kind,
            )

        if match := VIDEO_AV_URL_PATTERN.search(cleaned):
            return ParseTarget(
                aid=safe_int(match.group("avid").lstrip("avAV"), 0),
                page_num=extract_page_num(cleaned),
                raw_input=candidate,
                source_kind=source_kind,
            )

        if match := AV_PATTERN.search(cleaned):
            return ParseTarget(
                aid=safe_int(match.group("avid").lstrip("avAV"), 0),
                raw_input=candidate,
                source_kind=source_kind,
            )

        return None


class AcFunService:
    def __init__(
        self,
        client: httpx.AsyncClient,
        content_config_getter,
        runtime_config_getter,
        cache_dir: Path,
    ) -> None:
        self.client = client
        self.content_config_getter = content_config_getter
        self.runtime_config_getter = runtime_config_getter
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_headers = {
            "Referer": "https://www.acfun.cn/",
            "Origin": "https://www.acfun.cn",
        }

    def _video_push_requested(self) -> bool:
        content_config = self.content_config_getter() or {}
        return bool(content_config.get("acfun_send_direct_video", True))

    def _video_duration_limit_seconds(self) -> int:
        content_config = self.content_config_getter() or {}
        limit_minutes = safe_int(
            content_config.get(
                "acfun_video_max_duration_minutes",
                DEFAULT_VIDEO_MAX_DURATION_MINUTES,
            ),
            DEFAULT_VIDEO_MAX_DURATION_MINUTES,
            minimum=0,
            maximum=24 * 60,
        )
        if limit_minutes <= 0:
            return 0
        return limit_minutes * 60

    def _should_attempt_video_download(self, duration_seconds: int) -> bool:
        if not self._video_push_requested():
            return False
        limit_seconds = self._video_duration_limit_seconds()
        if limit_seconds > 0 and duration_seconds > limit_seconds:
            return False
        return True

    def resolve_user_id(self, source: str) -> int | None:
        candidate = source.strip()
        if not candidate:
            return None
        if candidate.isdigit():
            return int(candidate)
        if match := ACFUN_USER_URL_PATTERN.search(candidate):
            return int(match.group(1))
        return None

    async def fetch_recent_videos(self, user_id: int, limit: int) -> list[AcFunVideoItem]:
        page_size = max(1, min(limit, 30))
        timestamp_ms = int(time.time() * 1000)
        url = (
            f"https://www.acfun.cn/u/{user_id}"
            f"?quickViewId=ac-space-video-list&reqID=1&ajaxpipe=1"
            f"&type=video&order=newest&page=1&pageSize={page_size}&t={timestamp_ms}"
        )
        response = await self.client.get(
            url,
            headers=self.default_headers,
            follow_redirects=True,
        )
        raw_text = response.text
        cleaned = ACFUN_AJAXPIPE_TRAILING.sub("", raw_text).strip()
        if not cleaned:
            return []

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as err:
            logger.error(f"AcFunPush: 解析用户投稿列表 JSON 失败: {err}")
            return []

        html_content = ""
        if isinstance(data, dict):
            html_content = str(data.get("html") or "")
        elif isinstance(data, str):
            html_content = data

        if not html_content:
            return []

        acids: list[str] = []
        seen: set[str] = set()
        for match in ACFUN_HREF_ACID_PATTERN.finditer(html_content):
            acid = match.group(1)
            if acid not in seen:
                seen.add(acid)
                acids.append(acid)

        items: list[AcFunVideoItem] = []
        for acid in acids[:page_size]:
            try:
                info = await self._extract_video_info(acid)
                if info is None:
                    continue
                user_info = info.get("user") or {}
                items.append(
                    AcFunVideoItem(
                        acid=acid,
                        title=str(info.get("title") or "未命名视频"),
                        created_ts=safe_int(
                            info.get("createTimeMillis"), 0
                        ) // 1000 or safe_int(info.get("createTime"), 0),
                        author=str(user_info.get("name") or "未知UP"),
                        cover_url=normalize_cover_url(info.get("coverUrl") or ""),
                        desc=str(info.get("description") or ""),
                    )
                )
            except Exception as err:
                logger.debug(f"AcFunPush: 获取视频 {acid} 详情失败: {err}")
                items.append(
                    AcFunVideoItem(
                        acid=acid,
                        title="未知",
                        created_ts=0,
                        author="未知UP",
                    )
                )
        return items

    async def fetch_video_card(self, target: AcFunParseTarget) -> AcFunVideoCard:
        info = await self._extract_video_info(target.acid)
        if info is None:
            raise ValueError(f"无法获取 AcFun 视频 ac{target.acid} 的信息")

        title = str(info.get("title") or "未命名视频")
        user_info = info.get("user") or {}
        up_name = str(user_info.get("name") or "未知UP")
        duration_ms = safe_int(info.get("durationMillis"), 0)
        if duration_ms <= 0:
            duration_ms = safe_int(info.get("duration"), 0)
        duration_seconds = duration_ms // 1000 if duration_ms > 0 else 0
        pub_ts = (
            safe_int(info.get("createTimeMillis"), 0) // 1000
            or safe_int(info.get("createTime"), 0)
        )

        acid = str(info.get("id") or target.acid)
        link = f"https://www.acfun.cn/v/ac{acid}"

        video_path: Path | None = None
        if self._should_attempt_video_download(duration_seconds):
            try:
                m3u8_url = self._extract_m3u8_url(info)
                if m3u8_url:
                    video_path = await self._prepare_video_file(acid, m3u8_url)
            except MediaSizeLimitError as err:
                logger.warning(f"AcFunPush: 视频过大，回退为图文卡片: {err}")
            except Exception as err:
                logger.warning(f"AcFunPush: 生成视频文件失败，回退为图文卡片: {err}")

        return AcFunVideoCard(
            acid=acid,
            title=title,
            link=link,
            up_name=up_name,
            cover_url=normalize_cover_url(info.get("coverUrl") or ""),
            desc=str(info.get("description") or ""),
            duration_seconds=duration_seconds,
            pub_ts=pub_ts,
            view=safe_int(info.get("viewCount"), 0),
            like=safe_int(info.get("likeCount"), 0),
            danmaku=safe_int(info.get("danmakuCount"), 0),
            banana=safe_int(info.get("bananaCount"), 0),
            stow=safe_int(info.get("stowCount"), 0),
            comment=safe_int(info.get("commentCount"), 0),
            share=safe_int(info.get("shareCount"), 0),
            channel=str(info.get("channel") or "").strip(),
            video_path=video_path,
        )

    async def _extract_video_info(self, acid: str) -> dict | None:
        url = f"https://www.acfun.cn/v/ac{acid}"
        response = await self.client.get(
            url,
            headers=self.default_headers,
            follow_redirects=True,
        )
        html = response.text

        match = ACFUN_VIDEOINFO_PATTERN.search(html)
        if not match:
            logger.debug(f"AcFunPush: 页面中未找到 window.videoInfo (ac{acid})")
            return None

        start = match.end()
        json_str = _extract_json_object(html, start)
        if not json_str:
            logger.debug(f"AcFunPush: 提取 videoInfo JSON 失败 (ac{acid})")
            return None

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as err:
            logger.error(f"AcFunPush: 解析 videoInfo JSON 失败 (ac{acid}): {err}")
            return None

    def _extract_m3u8_url(self, info: dict) -> str | None:
        current = info.get("currentVideoInfo") or {}
        ks_play_str = current.get("ksPlayJson") or ""
        if not ks_play_str:
            return None
        try:
            ks_play = json.loads(ks_play_str)
        except json.JSONDecodeError as err:
            logger.error(f"AcFunPush: 解析 ksPlayJson 失败: {err}")
            return None

        adaptation_set = ks_play.get("adaptationSet") or []
        if not adaptation_set:
            return None

        representations = adaptation_set[0].get("representation") or []
        if not representations:
            return None

        best = representations[0]
        for rep in representations:
            url = rep.get("url") or ""
            if url:
                best = rep
                break

        return best.get("url")

    async def _prepare_video_file(self, acid: str, m3u8_url: str) -> Path:
        runtime_config = self.runtime_config_getter() or {}
        content_config = self.content_config_getter() or {}

        max_size_mb = safe_int(
            content_config.get(
                "acfun_video_max_size_mb",
                runtime_config.get("video_max_size_mb", DEFAULT_VIDEO_MAX_SIZE_MB),
            ),
            DEFAULT_VIDEO_MAX_SIZE_MB,
            minimum=10,
            maximum=2048,
        )
        timeout_seconds = safe_int(
            runtime_config.get(
                "video_download_timeout",
                DEFAULT_VIDEO_DOWNLOAD_TIMEOUT_SECONDS,
            ),
            DEFAULT_VIDEO_DOWNLOAD_TIMEOUT_SECONDS,
            minimum=20,
            maximum=3600,
        )

        output_path = self.cache_dir / f"ac{acid}.mp4"
        if output_path.exists() and output_path.stat().st_size > 0:
            max_bytes = max_size_mb * 1024 * 1024
            if output_path.stat().st_size > max_bytes:
                await safe_unlink(output_path)
                raise MediaSizeLimitError(f"缓存视频文件超过 {max_size_mb} MB 限制")
            return output_path

        cmd = [
            "ffmpeg",
            "-y",
            "-headers",
            f"Referer: {self.default_headers['Referer']}\r\n",
            "-i",
            m3u8_url,
            "-c",
            "copy",
            "-bsf:a",
            "aac_adtstoasc",
            str(output_path),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as err:
            raise MediaDownloadError("未安装 ffmpeg，无法下载 AcFun 视频") from err

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except asyncio.TimeoutError as err:
            process.kill()
            await process.wait()
            raise MediaDownloadError(
                f"ffmpeg 下载 AcFun 视频超时（{timeout_seconds} 秒）"
            ) from err

        if process.returncode != 0:
            error_message = stderr.decode("utf-8", errors="ignore").strip()
            await safe_unlink(output_path)
            raise MediaDownloadError(f"ffmpeg 下载 AcFun 视频失败: {error_message}")

        if not output_path.exists() or output_path.stat().st_size <= 0:
            await safe_unlink(output_path)
            raise MediaDownloadError("ffmpeg 输出文件为空")

        max_bytes = max_size_mb * 1024 * 1024
        if output_path.stat().st_size > max_bytes:
            await safe_unlink(output_path)
            raise MediaSizeLimitError(f"视频文件超过 {max_size_mb} MB 限制")

        return output_path


class AcFunLinkResolver:
    def __init__(self, service: AcFunService) -> None:
        self.service = service

    async def extract_parse_target(
        self,
        messages: Sequence[Any],
        text: str,
    ) -> AcFunParseTarget | None:
        candidate, source_kind = self._extract_candidate(messages, text)
        if not candidate:
            return None
        return self._normalize_candidate(candidate, source_kind)

    def _extract_candidate(
        self,
        messages: Sequence[Any],
        text: str,
    ) -> tuple[str | None, str]:
        direct, source_kind = self._extract_from_text(text)
        if direct:
            return direct, source_kind

        for component in messages:
            if isinstance(component, Json):
                card_url = extract_acfun_json_url(component.data)
                if card_url:
                    return card_url, "card"
                candidate = self._extract_from_json(component.data)
                if candidate:
                    return candidate, "card"

        return None, "code"

    def _extract_from_text(self, text: str) -> tuple[str | None, str]:
        if not text:
            return None, "code"
        stripped = text.strip()
        if stripped.startswith("/"):
            return None, "code"
        if match := ACFUN_URL_PATTERN.search(stripped):
            return strip_trailing_punctuation(match.group(0)), "link"
        if match := ACID_PATTERN.search(stripped):
            return match.group(0), "code"
        return None, "code"

    def _extract_from_json(self, payload: Any) -> str | None:
        for value in iter_string_values(payload):
            candidate, _ = self._extract_from_text(value)
            if candidate:
                return candidate
        return None

    def _normalize_candidate(
        self,
        candidate: str,
        source_kind: str,
    ) -> AcFunParseTarget | None:
        cleaned = strip_trailing_punctuation(candidate)

        if match := ACFUN_URL_PATTERN.search(cleaned):
            return AcFunParseTarget(
                acid=match.group(1),
                raw_input=candidate,
                source_kind=source_kind,
            )

        if match := ACID_PATTERN.search(cleaned):
            acid_digits = match.group(1)
            return AcFunParseTarget(
                acid=acid_digits,
                raw_input=candidate,
                source_kind=source_kind,
            )

        return None


class Main(Star):
    """B 站与 AcFun 视频自动解析与订阅推送插件。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.config = config or {}
        self.running = False
        self.monitor_task: asyncio.Task | None = None
        self.session_initialized_uids: set[int] = set()

        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "monitor_state.json"
        self.media_cache_dir = self.data_dir / "media_cache"
        self.media_cache_dir.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
        self._restore_passive_session_filters()

        transport = httpx.AsyncHTTPTransport(retries=2)
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        self.client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT_SECONDS,
            transport=transport,
            limits=limits,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                )
            },
        )

        self.credential_manager = BilibiliCredentialManager(
            data_dir=self.data_dir,
            auth_config_getter=lambda: self.auth_config,
        )
        self.service = BilibiliService(
            client=self.client,
            credential_manager=self.credential_manager,
            content_config_getter=lambda: self.content_config,
            runtime_config_getter=lambda: self.runtime_config,
            cache_dir=self.media_cache_dir,
        )
        self.link_resolver = LinkResolver(self.service)
        self.debouncer = DebounceCache(self.debounce_seconds)
        self._video_send_failure_until: dict[str, float] = {}

        self.acfun_service = AcFunService(
            client=self.client,
            content_config_getter=lambda: self.acfun_content_config,
            runtime_config_getter=lambda: self.runtime_config,
            cache_dir=self.media_cache_dir,
        )
        self.acfun_link_resolver = AcFunLinkResolver(self.acfun_service)
        self.acfun_debouncer = DebounceCache(self.debounce_seconds)
        self.acfun_monitor_task: asyncio.Task | None = None
        self.acfun_session_initialized_uids: set[int] = set()

    @property
    def auth_config(self) -> dict[str, Any]:
        return self.config.get("auth_settings", {}) or {}

    @property
    def passive_config(self) -> dict[str, Any]:
        return self.config.get("passive_settings", {}) or {}

    @property
    def monitoring_config(self) -> dict[str, Any]:
        return self.config.get("monitoring_settings", {}) or {}

    @property
    def content_config(self) -> dict[str, Any]:
        return self.config.get("content_settings", {}) or {}

    @property
    def runtime_config(self) -> dict[str, Any]:
        return self.config.get("runtime_settings", {}) or {}

    @property
    def auto_parse_enabled(self) -> bool:
        if "auto_parse_enabled" in self.passive_config:
            return bool(self.passive_config.get("auto_parse_enabled", True))
        return bool(self.content_config.get("auto_parse_enabled", True))

    @property
    def debounce_seconds(self) -> int:
        return safe_int(
            self.runtime_config.get("debounce_seconds", DEFAULT_DEBOUNCE_SECONDS),
            DEFAULT_DEBOUNCE_SECONDS,
            minimum=0,
            maximum=24 * 60 * 60,
        )

    @property
    def video_send_failure_cooldown_seconds(self) -> int:
        return safe_int(
            self.runtime_config.get(
                "video_send_failure_cooldown_seconds",
                DEFAULT_VIDEO_SEND_FAILURE_COOLDOWN_SECONDS,
            ),
            DEFAULT_VIDEO_SEND_FAILURE_COOLDOWN_SECONDS,
            minimum=0,
            maximum=24 * 60 * 60,
        )

    @property
    def parse_template(self) -> str:
        template = self.content_config.get(
            "rich_message_format",
            self.content_config.get("parse_message_format", DEFAULT_PARSE_TEMPLATE),
        )
        return str(template or DEFAULT_PARSE_TEMPLATE).replace("\\n", "\n")

    @property
    def push_template(self) -> str:
        template = self.content_config.get("push_message_format", DEFAULT_PUSH_TEMPLATE)
        return str(template or DEFAULT_PUSH_TEMPLATE).replace("\\n", "\n")

    @property
    def acfun_passive_config(self) -> dict[str, Any]:
        return self.config.get("acfun_passive_settings", {}) or {}

    @property
    def acfun_monitoring_config(self) -> dict[str, Any]:
        return self.config.get("acfun_monitoring_settings", {}) or {}

    @property
    def acfun_content_config(self) -> dict[str, Any]:
        return self.config.get("acfun_content_settings", {}) or {}

    @property
    def acfun_auto_parse_enabled(self) -> bool:
        return bool(self.acfun_passive_config.get("acfun_auto_parse_enabled", True))

    @property
    def acfun_parse_template(self) -> str:
        template = self.acfun_content_config.get(
            "acfun_parse_message_format", ACFUN_DEFAULT_PARSE_TEMPLATE
        )
        return str(template or ACFUN_DEFAULT_PARSE_TEMPLATE).replace("\\n", "\n")

    @property
    def acfun_push_template(self) -> str:
        template = self.acfun_content_config.get(
            "acfun_push_message_format", ACFUN_DEFAULT_PUSH_TEMPLATE
        )
        return str(template or ACFUN_DEFAULT_PUSH_TEMPLATE).replace("\\n", "\n")

    def _acfun_send_direct_video_enabled(self) -> bool:
        return bool(self.acfun_content_config.get("acfun_send_direct_video", True))

    def _acfun_send_rich_text_enabled(self) -> bool:
        return bool(self.acfun_content_config.get("acfun_send_rich_text", False))

    def _send_direct_video_enabled(self) -> bool:
        return bool(self.content_config.get("send_direct_video", True))

    def _send_rich_text_enabled(self) -> bool:
        return bool(self.content_config.get("send_rich_text", False))

    async def initialize(self):
        self.running = True
        self.debouncer.update_ttl(self.debounce_seconds)
        self.acfun_debouncer.update_ttl(self.debounce_seconds)
        await self._cleanup_media_cache(force=False)
        self.monitor_task = asyncio.create_task(self.run_monitor())
        self.acfun_monitor_task = asyncio.create_task(self._acfun_run_monitor())

    async def terminate(self):
        self.running = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        if self.acfun_monitor_task:
            self.acfun_monitor_task.cancel()
            try:
                await self.acfun_monitor_task
            except asyncio.CancelledError:
                pass
        await self.client.aclose()
        logger.info("BilibiliPush: 插件已停止")

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as err:
            logger.error(f"BilibiliPush: 加载状态文件失败: {err}")
            return {}

    def _save_state(self) -> None:
        temp_file = self.state_file.with_suffix(".tmp")
        try:
            temp_file.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_file.replace(self.state_file)
        except Exception as err:
            logger.error(f"BilibiliPush: 保存状态文件失败: {err}")
            try:
                temp_file.unlink(missing_ok=True)
            except Exception:
                pass

    def _state_get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def _state_update(self, values: dict[str, Any]) -> None:
        self._state.update(values)
        self._save_state()

    def _parse_multi_value(self, raw: Any) -> list[str]:
        if isinstance(raw, str):
            candidates = [raw]
        elif isinstance(raw, list):
            candidates = [str(item) for item in raw]
        else:
            return []

        values: list[str] = []
        for item in candidates:
            for part in item.replace("\n", ",").split(","):
                value = part.strip()
                if value:
                    values.append(value)
        return list(dict.fromkeys(values))

    def _parse_string_list(self, raw: Any) -> list[str]:
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        return self._parse_multi_value(raw)

    def _pick_interval(self, base: int, jitter: int, minimum: int = 1) -> int:
        if jitter <= 0:
            return max(minimum, base)
        return max(minimum, random.randint(base - jitter, base + jitter))

    def _get_bot_owner_id(self) -> str:
        try:
            cfg = self.context.get_config()
            admins = cfg.get("admins_id", [])
        except Exception:
            admins = []
        if isinstance(admins, list) and admins:
            return str(admins[0])
        return ""

    def _is_bot_owner(self, event: AstrMessageEvent) -> bool:
        owner_id = self._get_bot_owner_id()
        if not owner_id:
            return True
        return str(event.get_sender_id()) == owner_id

    def _supports_save_config(self) -> bool:
        return callable(getattr(self.config, "save_config", None))

    def _save_plugin_config(self) -> bool:
        save_config = getattr(self.config, "save_config", None)
        if not callable(save_config):
            return False
        try:
            save_config()
            return True
        except Exception as err:
            logger.error(f"BilibiliPush: 保存插件配置失败: {err}")
            return False

    def _restore_passive_session_filters(self) -> None:
        # 与官方文档一致：优先使用 AstrBotConfig.save_config 持久化。
        # 如果当前运行环境仅传入普通 dict，则回退到 state 文件持久化会话开关。
        if self._supports_save_config():
            return
        state_blacklist = self._parse_string_list(
            self._state_get(STATE_KEY_PASSIVE_SESSION_BLACKLIST, [])
        )
        state_whitelist = self._parse_string_list(
            self._state_get(STATE_KEY_PASSIVE_SESSION_WHITELIST, [])
        )
        if not state_blacklist and not state_whitelist:
            return

        blacklist = self._ensure_passive_blacklist()
        whitelist = self._ensure_passive_whitelist()
        for session in state_blacklist:
            if session not in blacklist:
                blacklist.append(session)
        for session in state_whitelist:
            if session not in whitelist:
                whitelist.append(session)

    def _save_passive_session_filters_to_state(self) -> None:
        self._state_update(
            {
                STATE_KEY_PASSIVE_SESSION_BLACKLIST: self._parse_string_list(
                    self.passive_config.get("session_blacklist", [])
                ),
                STATE_KEY_PASSIVE_SESSION_WHITELIST: self._parse_string_list(
                    self.passive_config.get("session_whitelist", [])
                ),
            }
        )

    def _persist_passive_session_filters(self) -> None:
        if self._save_plugin_config():
            return
        self._save_passive_session_filters_to_state()

    def _ensure_passive_blacklist(self) -> list[str]:
        passive_settings = self.config.setdefault("passive_settings", {})
        blacklist = passive_settings.setdefault("session_blacklist", [])
        if not isinstance(blacklist, list):
            blacklist = []
            passive_settings["session_blacklist"] = blacklist
        return blacklist

    def _ensure_passive_whitelist(self) -> list[str]:
        passive_settings = self.config.setdefault("passive_settings", {})
        whitelist = passive_settings.setdefault("session_whitelist", [])
        if not isinstance(whitelist, list):
            whitelist = []
            passive_settings["session_whitelist"] = whitelist
        return whitelist

    def _is_passive_session_allowed(self, event: AstrMessageEvent) -> bool:
        whitelist = self._parse_string_list(self.passive_config.get("session_whitelist", []))
        blacklist = self._parse_string_list(self.passive_config.get("session_blacklist", []))
        umo = event.unified_msg_origin
        if whitelist and umo not in whitelist:
            return False
        if blacklist and umo in blacklist:
            return False
        return True

    def _resolve_monitor_rules(self) -> list[MonitorRule]:
        raw_rules = self.monitoring_config.get("subscription_rules", []) or []
        merged_targets: dict[int, list[str]] = {}
        source_map: dict[int, str] = {}

        for item in raw_rules:
            if not isinstance(item, dict):
                continue
            targets = self._parse_multi_value(item.get("allowed_targets", ""))
            if not targets:
                continue
            for source in self._parse_multi_value(item.get("source", "")):
                uid = self.service.resolve_uid(source)
                if uid is None:
                    logger.warning(f"BilibiliPush: 无法解析监控来源 {source!r}，已跳过")
                    continue
                merged_targets.setdefault(uid, [])
                for target in targets:
                    if target not in merged_targets[uid]:
                        merged_targets[uid].append(target)
                source_map.setdefault(uid, source)

        return [
            MonitorRule(uid=uid, targets=tuple(targets), source=source_map.get(uid, str(uid)))
            for uid, targets in merged_targets.items()
        ]

    def _render_video_text(self, card: VideoCard, *, push: bool) -> str:
        desc_limit = safe_int(
            self.content_config.get("description_length", DEFAULT_DESC_LENGTH),
            DEFAULT_DESC_LENGTH,
            minimum=0,
            maximum=2000,
        )
        desc = sanitize_desc(card.desc, desc_limit)
        payload = SafeFormatDict(
            title=card.title,
            up_name=card.up_name,
            link=card.link,
            bvid=card.bvid,
            aid=card.aid,
            duration=card.duration_text,
            pub_time=format_timestamp(card.pub_ts),
            view=format_count(card.view),
            like=format_count(card.like),
            danmaku=format_count(card.danmaku),
            reply=format_count(card.reply),
            favorite=format_count(card.favorite),
            coin=format_count(card.coin),
            share=format_count(card.share),
            tname=card.tname,
            desc=desc,
            part_title=card.part_title,
        )
        template = self.push_template if push else self.parse_template
        return template.format_map(payload)

    def _should_attach_qq_emoji(self, event: AstrMessageEvent, target: ParseTarget) -> bool:
        return (
            bool(self.passive_config.get("qq_link_emoji_enabled", True))
            and event.get_platform_id() == "aiocqhttp"
            and target.source_kind in {"link", "card"}
        )

    async def _attach_qq_emoji_reaction(self, event: AstrMessageEvent, target: ParseTarget) -> bool:
        if not self._should_attach_qq_emoji(event, target):
            return False

        bot = getattr(event, "bot", None)
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if bot is None or raw is None:
            return False

        message_id = None
        for key in ("message_id", "id"):
            try:
                if isinstance(raw, dict) and raw.get(key) is not None:
                    message_id = raw.get(key)
                    break
                value = getattr(raw, key, None)
                if value is not None:
                    message_id = value
                    break
            except Exception as err:
                logger.debug(f"BilibiliPush: 读取 QQ message_id 字段 {key!r} 失败: {err}")
                continue

        if message_id is None:
            logger.debug("BilibiliPush: 当前消息缺少 message_id，无法附加 QQ 表情")
            return False

        emoji_id = str(random.choice(QQ_DEFAULT_FACE_IDS))
        message_id_variants: list[Any] = [message_id]
        try:
            message_id_int = int(message_id)
            if message_id_int not in message_id_variants:
                message_id_variants.append(message_id_int)
        except Exception as err:
            logger.debug(f"BilibiliPush: message_id 转 int 失败，继续使用原值: {err}")

        payload_candidates: list[dict[str, Any]] = []
        for message_id_variant in message_id_variants:
            payload_candidates.extend(
                [
                    {"message_id": message_id_variant, "emoji_id": emoji_id},
                    {"message_id": message_id_variant, "emoji_id": emoji_id, "set": True},
                    {
                        "message_id": message_id_variant,
                        "emoji_id": emoji_id,
                        "emoji_type": "1",
                        "set": True,
                    },
                ]
            )

        action_error: Exception | None = None
        api = getattr(bot, "set_msg_emoji_like", None)
        if callable(api):
            for payload in payload_candidates:
                try:
                    await api(**payload)
                    logger.debug(
                        f"BilibiliPush: 已为消息 {message_id} 附加 QQ 表情 emoji_id={emoji_id}"
                    )
                    return True
                except Exception as err:
                    action_error = err

        call_action = getattr(bot, "call_action", None)
        if callable(call_action):
            for payload in payload_candidates:
                try:
                    await call_action("set_msg_emoji_like", **payload)
                    logger.debug(
                        f"BilibiliPush: 已为消息 {message_id} 附加 QQ 表情 emoji_id={emoji_id}"
                    )
                    return True
                except Exception as err:
                    action_error = err

        if action_error is not None:
            logger.warning(f"BilibiliPush: 附加 QQ 表情失败: {action_error}")
        return False

    def _media_cache_files(self) -> list[Path]:
        if not self.media_cache_dir.exists():
            return []
        return [path for path in self.media_cache_dir.rglob("*") if path.is_file()]

    def _media_cache_usage_bytes(self, files: Sequence[Path] | None = None) -> int:
        total = 0
        for path in files or self._media_cache_files():
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                continue
        return total

    async def _cleanup_media_cache(
        self,
        *,
        force: bool,
        keep_paths: Sequence[Path | None] | None = None,
    ) -> dict[str, Any]:
        files = self._media_cache_files()
        before_bytes = self._media_cache_usage_bytes(files)
        last_cleanup_ts = safe_int(self._state_get("media_cache_last_cleanup_ts", 0), 0)
        now_ts = int(time.time())
        cleanup_due = now_ts - last_cleanup_ts >= MEDIA_CACHE_DAILY_CLEANUP_SECONDS
        over_limit = before_bytes >= MEDIA_CACHE_FORCE_LIMIT_BYTES
        should_cleanup = bool(files) and (force or over_limit or cleanup_due)

        if not should_cleanup:
            return {
                "cleaned": False,
                "force": force or over_limit,
                "before_bytes": before_bytes,
                "after_bytes": before_bytes,
                "removed_files": 0,
                "removed_bytes": 0,
            }

        keep_set = {
            str(path.resolve())
            for path in (keep_paths or [])
            if path is not None and path.exists()
        }
        removed_files = 0
        removed_bytes = 0

        for path in files:
            try:
                if str(path.resolve()) in keep_set:
                    continue
                size = path.stat().st_size
                path.unlink(missing_ok=True)
                removed_files += 1
                removed_bytes += size
            except FileNotFoundError:
                continue
            except Exception as err:
                logger.warning(f"BilibiliPush: 删除缓存文件失败 {path}: {err}")

        for directory in sorted(
            [path for path in self.media_cache_dir.rglob("*") if path.is_dir()],
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                continue

        self.media_cache_dir.mkdir(parents=True, exist_ok=True)
        after_bytes = self._media_cache_usage_bytes()
        self._state_update({"media_cache_last_cleanup_ts": now_ts})
        logger.info(
            "BilibiliPush: 媒体缓存清理完成 "
            f"(force={force or over_limit}, before={format_bytes(before_bytes)}, "
            f"after={format_bytes(after_bytes)}, removed_files={removed_files})"
        )
        return {
            "cleaned": True,
            "force": force or over_limit,
            "before_bytes": before_bytes,
            "after_bytes": after_bytes,
            "removed_files": removed_files,
            "removed_bytes": removed_bytes,
        }

    def _build_rich_text_chain(
        self,
        card: VideoCard,
        *,
        push: bool,
        event: AstrMessageEvent | None = None,
        target: ParseTarget | None = None,
    ) -> MessageChain:
        chain = MessageChain()
        chain.message(self._render_video_text(card, push=push))

        if bool(self.content_config.get("send_cover", True)) and card.cover_url:
            chain.url_image(card.cover_url)

        return chain

    def _build_video_chain(
        self,
        card: VideoCard,
        *,
        event: AstrMessageEvent | None = None,
        target: ParseTarget | None = None,
    ) -> MessageChain | None:
        if card.video_path is None or not self._send_direct_video_enabled():
            return None

        chain = MessageChain()
        chain.chain.append(MessageVideo.fromFileSystem(str(card.video_path)))
        return chain

    def _build_message_chains(
        self,
        card: VideoCard,
        *,
        push: bool,
        event: AstrMessageEvent | None = None,
        target: ParseTarget | None = None,
    ) -> list[MessageChain]:
        chains: list[MessageChain] = []
        video_chain = self._build_video_chain(card, event=event, target=target)
        send_rich_text = self._send_rich_text_enabled() or video_chain is None

        if send_rich_text:
            chains.append(self._build_rich_text_chain(card, push=push, event=event, target=target))
        if video_chain is not None:
            chains.append(video_chain)

        if not chains:
            chains.append(self._build_rich_text_chain(card, push=push, event=event, target=target))

        return chains

    async def _send_card_to_targets(self, card: VideoCard, targets: Sequence[str]) -> dict[str, int]:
        success = 0
        failure = 0
        for target in targets:
            try:
                chains = self._build_message_chains(card, push=True)
                send_success = True
                for chain in chains:
                    if self._is_video_only_chain(chain):
                        await self._send_chain_with_video_fallback(target, chain, card, push=True)
                        sent = True
                    else:
                        sent = await self.context.send_message(target, chain)
                    send_success = send_success and bool(sent)
                if send_success:
                    success += 1
                else:
                    failure += 1
            except Exception as err:
                logger.error(f"BilibiliPush: 发送到 {target} 失败: {err}")
                failure += 1
        return {"target_success": success, "target_failure": failure}

    async def _send_chain_with_video_fallback(
        self,
        destination: str,
        chain: MessageChain,
        card: VideoCard,
        *,
        push: bool,
    ) -> None:
        if self._is_video_send_cooldown_active(destination):
            logger.info(
                f"BilibiliPush: 目标 {destination} 仍在视频发送失败冷却期内，直接发送图文卡片"
            )
            fallback_chain = self._build_rich_text_chain(card, push=push)
            await self.context.send_message(destination, fallback_chain)
            return

        try:
            await self.context.send_message(destination, chain)
        except Exception as err:
            if not self._is_video_only_chain(chain):
                raise
            self._mark_video_send_failed(destination)
            logger.warning(
                f"BilibiliPush: 视频发送到 {destination} 失败，回退为图文卡片: {err}"
            )
            fallback_chain = self._build_rich_text_chain(card, push=push)
            await self.context.send_message(destination, fallback_chain)

    def _is_video_send_cooldown_active(self, destination: str) -> bool:
        failure_until = getattr(self, "_video_send_failure_until", {})
        now = time.time()
        expires_at = float(failure_until.get(destination, 0) or 0)
        if expires_at > now:
            return True
        failure_until.pop(destination, None)
        return False

    def _mark_video_send_failed(self, destination: str) -> None:
        cooldown_seconds = self.video_send_failure_cooldown_seconds
        if cooldown_seconds <= 0:
            return
        if not hasattr(self, "_video_send_failure_until"):
            self._video_send_failure_until = {}
        self._video_send_failure_until[destination] = time.time() + cooldown_seconds

    def _is_video_only_chain(self, chain: MessageChain) -> bool:
        if len(chain.chain) != 1:
            return False
        component = chain.chain[0]
        if isinstance(component, MessageVideo):
            return True
        component_type = getattr(component, "type", "")
        return str(component_type).lower().endswith("video")

    async def _check_uid_videos(self, uid: int, *, force_fetch: bool = False) -> list[FeedVideoItem]:
        fetch_limit = safe_int(
            self.runtime_config.get("latest_fetch_limit", DEFAULT_FETCH_LIMIT),
            DEFAULT_FETCH_LIMIT,
            minimum=1,
            maximum=20,
        )
        items = await self.service.fetch_recent_videos(uid, fetch_limit)
        if not items:
            return []
        if force_fetch:
            return items

        state_key_aid = f"last_aid_{uid}"
        state_key_bvid = f"last_bvid_{uid}"
        last_aid = safe_int(self._state_get(state_key_aid, 0), 0)
        last_bvid = str(self._state_get(state_key_bvid, "") or "")
        latest = items[0]

        if last_aid == 0 and not last_bvid:
            self._state_update({state_key_aid: latest.aid, state_key_bvid: latest.bvid})
            self.session_initialized_uids.add(uid)
            logger.info(f"BilibiliPush: 初始化 UID={uid} 的监控基线为 {latest.bvid}")
            return []

        new_items: list[FeedVideoItem] = []
        for item in items:
            if item.aid == last_aid or (last_bvid and item.bvid == last_bvid):
                break
            new_items.append(item)

        self.session_initialized_uids.add(uid)
        self._state_update({state_key_aid: latest.aid, state_key_bvid: latest.bvid})
        new_items.reverse()
        return new_items

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self.auto_parse_enabled:
            return
        if not self._is_passive_session_allowed(event):
            return
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return

        messages = event.get_messages() or []
        if messages:
            first = messages[0]
            if isinstance(first, At) and str(first.qq) != str(event.get_self_id()):
                return

        self.debouncer.update_ttl(self.debounce_seconds)

        try:
            target = await self.link_resolver.extract_parse_target(messages, event.message_str or "")
            if target is None:
                return

            debounce_key = target.raw_input or target.bvid or str(target.aid)
            if self.debouncer.hit_link(event.unified_msg_origin, debounce_key):
                return

            card = await self.service.fetch_video_card(target)
            await self._cleanup_media_cache(force=False, keep_paths=[card.video_path])
            await self._attach_qq_emoji_reaction(event, target)
            if self.debouncer.hit_resource(event.unified_msg_origin, card.bvid or str(card.aid)):
                return

            chains = self._build_message_chains(card, push=False, event=event, target=target)
            for chain in chains:
                if self._is_video_only_chain(chain):
                    await self._send_chain_with_video_fallback(
                        event.unified_msg_origin,
                        chain,
                        card,
                        push=False,
                    )
                    continue
                yield event.chain_result(chain.chain)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.error(f"BilibiliPush: 自动解析失败: {err}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bili_login", alias={"登录B站", "登录b站", "blogin"})
    async def bili_login(self, event: AstrMessageEvent):
        if not self._is_bot_owner(event):
            yield event.plain_result("❌ 此指令仅机器人主人可用。")
            return
        qrcode = await self.credential_manager.login_with_qrcode()
        yield event.chain_result([Image.fromBytes(qrcode)])
        async for message in self.credential_manager.check_qr_state():
            yield event.plain_result(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bili_verify", alias={"bili_cookie_status", "检查B站登录态", "检查b站登录态"})
    async def bili_verify(self, event: AstrMessageEvent):
        ok, message = await self.credential_manager.verify()
        prefix = "✅" if ok else "❌"
        yield event.plain_result(f"{prefix} {message}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bili_logout", alias={"清除B站登录态", "清除b站登录态"})
    async def bili_logout(self, event: AstrMessageEvent):
        if not self._is_bot_owner(event):
            yield event.plain_result("❌ 此指令仅机器人主人可用。")
            return
        await self.credential_manager.clear()
        yield event.plain_result("✅ 已清除本地持久化的 Bilibili 登录态。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bili_clear_cache", alias={"清理B站缓存", "清理b站缓存", "bili_cache_clear"})
    async def bili_clear_cache(self, event: AstrMessageEvent):
        if not self._is_bot_owner(event):
            yield event.plain_result("❌ 此指令仅机器人主人可用。")
            return
        result = await self._cleanup_media_cache(force=True)
        if not result["cleaned"]:
            yield event.plain_result(
                f"ℹ️ 当前没有可清理的媒体缓存，缓存占用 {format_bytes(result['before_bytes'])}。"
            )
            return
        yield event.plain_result(
            "✅ 媒体缓存已清理："
            f"删除 {result['removed_files']} 个文件，"
            f"释放 {format_bytes(result['removed_bytes'])}，"
            f"剩余 {format_bytes(result['after_bytes'])}。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bili_parse_on", alias={"开启B站解析", "开启b站解析"})
    async def bili_parse_on(self, event: AstrMessageEvent):
        blacklist = self._ensure_passive_blacklist()
        whitelist = self._ensure_passive_whitelist()
        umo = event.unified_msg_origin
        if umo in blacklist:
            blacklist.remove(umo)
        if whitelist and umo not in whitelist:
            whitelist.append(umo)
        self._persist_passive_session_filters()
        yield event.plain_result("✅ 已开启当前会话的 B 站被动解析。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bili_parse_off", alias={"关闭B站解析", "关闭b站解析"})
    async def bili_parse_off(self, event: AstrMessageEvent):
        blacklist = self._ensure_passive_blacklist()
        umo = event.unified_msg_origin
        if umo not in blacklist:
            blacklist.append(umo)
        self._persist_passive_session_filters()
        yield event.plain_result("✅ 已关闭当前会话的 B 站被动解析。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bili_check")
    async def bili_check(self, event: AstrMessageEvent, source: str = ""):
        if source:
            try:
                uid = self.service.resolve_uid(source)
                if uid is None:
                    yield event.plain_result("❌ 无法从输入中解析出 UID，请提供 UID 或空间链接。")
                    return
                recent = await self._check_uid_videos(uid, force_fetch=True)
                if not recent:
                    yield event.plain_result(f"ℹ️ UID {uid} 最近没有获取到视频。")
                    return
                card = await self.service.fetch_video_card(ParseTarget(bvid=recent[0].bvid))
                await self._cleanup_media_cache(force=False, keep_paths=[card.video_path])
                for chain in self._build_message_chains(card, push=True):
                    await self.context.send_message(event.unified_msg_origin, chain)
                yield event.plain_result(f"✅ 已向当前会话发送 UID {uid} 的最新视频。")
            except Exception as err:
                logger.error(f"BilibiliPush: 手动检查失败: {err}")
                yield event.plain_result(f"❌ 手动检查失败: {err}")
            return

        rules = self._resolve_monitor_rules()
        if not rules:
            yield event.plain_result("❌ 没有可用的监控规则，请先在插件配置中填写订阅规则。")
            return

        rule = rules[0]
        recent = await self._check_uid_videos(rule.uid, force_fetch=True)
        if not recent:
            yield event.plain_result(f"ℹ️ UID {rule.uid} 最近没有获取到视频。")
            return

        card = await self.service.fetch_video_card(ParseTarget(bvid=recent[0].bvid))
        await self._cleanup_media_cache(force=False, keep_paths=[card.video_path])
        result = await self._send_card_to_targets(card, list(rule.targets))
        yield event.plain_result(
            f"✅ 推送完成：成功目标 {result['target_success']}，失败目标 {result['target_failure']}。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bili_check_all")
    async def bili_check_all(self, event: AstrMessageEvent):
        rules = self._resolve_monitor_rules()
        if not rules:
            yield event.plain_result("❌ 没有可用的监控规则，请先在插件配置中填写订阅规则。")
            return

        yield event.plain_result(f"🔍 正在立即检查 {len(rules)} 条 B 站监控规则...")
        summaries: list[str] = []
        request_interval = safe_int(
            self.runtime_config.get("request_interval", DEFAULT_REQUEST_INTERVAL_SECONDS),
            DEFAULT_REQUEST_INTERVAL_SECONDS,
            minimum=1,
            maximum=60,
        )
        request_jitter = safe_int(
            self.runtime_config.get("request_interval_jitter", 0),
            0,
            minimum=0,
            maximum=30,
        )

        for index, rule in enumerate(rules):
            if index > 0:
                await asyncio.sleep(self._pick_interval(request_interval, request_jitter, minimum=1))
            try:
                recent = await self._check_uid_videos(rule.uid, force_fetch=True)
                if not recent:
                    summaries.append(f"ℹ️ UID {rule.uid} 没有获取到视频")
                    continue
                card = await self.service.fetch_video_card(ParseTarget(bvid=recent[0].bvid))
                await self._cleanup_media_cache(force=False, keep_paths=[card.video_path])
                result = await self._send_card_to_targets(card, list(rule.targets))
                if result["target_failure"] > 0:
                    summaries.append(
                        f"⚠️ UID {rule.uid} 部分成功：成功 {result['target_success']}，失败 {result['target_failure']}"
                    )
                else:
                    summaries.append(f"✅ UID {rule.uid} 推送成功")
            except Exception as err:
                logger.error(f"BilibiliPush: 检查 UID {rule.uid} 失败: {err}")
                summaries.append(f"❌ UID {rule.uid} 检查失败: {err}")

        yield event.plain_result("\n".join(summaries))

    async def run_monitor(self):
        logger.info("BilibiliPush: 监控任务已启动")
        await asyncio.sleep(STARTUP_DELAY_SECONDS)

        while self.running:
            try:
                rules = self._resolve_monitor_rules()
                interval = safe_int(
                    self.runtime_config.get("check_interval", DEFAULT_CHECK_INTERVAL_MINUTES),
                    DEFAULT_CHECK_INTERVAL_MINUTES,
                    minimum=1,
                    maximum=24 * 60,
                )
                jitter = safe_int(
                    self.runtime_config.get("check_interval_jitter", 0),
                    0,
                    minimum=0,
                    maximum=180,
                )
                sleep_minutes = self._pick_interval(interval, jitter, minimum=1)

                if not rules:
                    logger.debug("BilibiliPush: 当前无可用监控规则")
                else:
                    credential = await self.credential_manager.get_credential()
                    if credential is None:
                        logger.warning("BilibiliPush: 当前没有可用的 Bilibili 登录态，跳过本轮轮询")
                    else:
                        await self._cleanup_media_cache(force=False)
                        await self._run_monitor_cycle(rules)

                logger.debug(f"BilibiliPush: 下次检查将在 {sleep_minutes} 分钟后执行")
                await asyncio.sleep(sleep_minutes * 60)
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.error(f"BilibiliPush: 监控循环异常: {err}")
                await asyncio.sleep(60)

    async def _run_monitor_cycle(self, rules: Sequence[MonitorRule]) -> None:
        request_interval = safe_int(
            self.runtime_config.get("request_interval", DEFAULT_REQUEST_INTERVAL_SECONDS),
            DEFAULT_REQUEST_INTERVAL_SECONDS,
            minimum=1,
            maximum=60,
        )
        request_jitter = safe_int(
            self.runtime_config.get("request_interval_jitter", 0),
            0,
            minimum=0,
            maximum=30,
        )

        for index, rule in enumerate(rules):
            if index > 0:
                await asyncio.sleep(self._pick_interval(request_interval, request_jitter, minimum=1))
            try:
                new_items = await self._check_uid_videos(rule.uid)
                if not new_items:
                    continue
                for item in new_items:
                    card = await self.service.fetch_video_card(ParseTarget(bvid=item.bvid))
                    await self._cleanup_media_cache(force=False, keep_paths=[card.video_path])
                    await self._send_card_to_targets(card, list(rule.targets))
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.error(f"BilibiliPush: 轮询 UID {rule.uid} 失败: {err}")

    def _is_acfun_passive_session_allowed(self, event: AstrMessageEvent) -> bool:
        whitelist = self._parse_string_list(self.acfun_passive_config.get("acfun_session_whitelist", []))
        blacklist = self._parse_string_list(self.acfun_passive_config.get("acfun_session_blacklist", []))
        umo = event.unified_msg_origin
        if whitelist and umo not in whitelist:
            return False
        if blacklist and umo in blacklist:
            return False
        return True

    def _resolve_acfun_monitor_rules(self) -> list[AcFunMonitorRule]:
        raw_rules = self.acfun_monitoring_config.get("acfun_subscription_rules", []) or []
        merged_targets: dict[int, list[str]] = {}
        source_map: dict[int, str] = {}

        for item in raw_rules:
            if not isinstance(item, dict):
                continue
            targets = self._parse_multi_value(item.get("allowed_targets", ""))
            if not targets:
                continue
            for source in self._parse_multi_value(item.get("source", "")):
                user_id = self.acfun_service.resolve_user_id(source)
                if user_id is None:
                    logger.warning(f"AcFunPush: 无法解析监控来源 {source!r}，已跳过")
                    continue
                merged_targets.setdefault(user_id, [])
                for target in targets:
                    if target not in merged_targets[user_id]:
                        merged_targets[user_id].append(target)
                source_map.setdefault(user_id, source)

        return [
            AcFunMonitorRule(
                user_id=user_id,
                targets=tuple(targets),
                source=source_map.get(user_id, str(user_id)),
            )
            for user_id, targets in merged_targets.items()
        ]

    def _render_acfun_video_text(self, card: AcFunVideoCard, *, push: bool) -> str:
        desc_limit = safe_int(
            self.acfun_content_config.get("acfun_description_length", DEFAULT_DESC_LENGTH),
            DEFAULT_DESC_LENGTH,
            minimum=0,
            maximum=2000,
        )
        desc = sanitize_desc(card.desc, desc_limit)
        payload = SafeFormatDict(
            title=card.title,
            up_name=card.up_name,
            link=card.link,
            acid=card.acid,
            duration=card.duration_text,
            pub_time=format_timestamp(card.pub_ts),
            view=format_count(card.view),
            like=format_count(card.like),
            danmaku=format_count(card.danmaku),
            banana=format_count(card.banana),
            stow=format_count(card.stow),
            comment=format_count(card.comment),
            share=format_count(card.share),
            channel=card.channel,
            desc=desc,
        )
        template = self.acfun_push_template if push else self.acfun_parse_template
        return template.format_map(payload)

    def _build_acfun_rich_text_chain(self, card: AcFunVideoCard) -> MessageChain:
        chain = MessageChain()
        chain.message(self._render_acfun_video_text(card, push=False))
        if bool(self.acfun_content_config.get("acfun_send_cover", True)) and card.cover_url:
            chain.url_image(card.cover_url)
        return chain

    def _build_acfun_video_chain(self, card: AcFunVideoCard) -> MessageChain | None:
        if card.video_path is None or not self._acfun_send_direct_video_enabled():
            return None
        chain = MessageChain()
        chain.chain.append(MessageVideo.fromFileSystem(str(card.video_path)))
        return chain

    def _build_acfun_message_chains(self, card: AcFunVideoCard, *, push: bool = False) -> list[MessageChain]:
        chains: list[MessageChain] = []
        video_chain = self._build_acfun_video_chain(card)
        send_rich_text = self._acfun_send_rich_text_enabled() or video_chain is None

        if send_rich_text:
            rich_chain = MessageChain()
            rich_chain.message(self._render_acfun_video_text(card, push=push))
            if bool(self.acfun_content_config.get("acfun_send_cover", True)) and card.cover_url:
                rich_chain.url_image(card.cover_url)
            chains.append(rich_chain)
        if video_chain is not None:
            chains.append(video_chain)

        if not chains:
            rich_chain = MessageChain()
            rich_chain.message(self._render_acfun_video_text(card, push=push))
            chains.append(rich_chain)

        return chains

    async def _send_acfun_card_to_targets(
        self, card: AcFunVideoCard, targets: Sequence[str]
    ) -> dict[str, int]:
        success = 0
        failure = 0
        for target in targets:
            try:
                chains = self._build_acfun_message_chains(card, push=True)
                send_success = True
                for chain in chains:
                    sent = await self.context.send_message(target, chain)
                    send_success = send_success and bool(sent)
                if send_success:
                    success += 1
                else:
                    failure += 1
            except Exception as err:
                logger.error(f"AcFunPush: 发送到 {target} 失败: {err}")
                failure += 1
        return {"target_success": success, "target_failure": failure}

    async def _check_acfun_user_videos(
        self, user_id: int, *, force_fetch: bool = False
    ) -> list[AcFunVideoItem]:
        fetch_limit = safe_int(
            self.runtime_config.get("latest_fetch_limit", DEFAULT_FETCH_LIMIT),
            DEFAULT_FETCH_LIMIT,
            minimum=1,
            maximum=20,
        )
        items = await self.acfun_service.fetch_recent_videos(user_id, fetch_limit)
        if not items:
            return []
        if force_fetch:
            return items

        state_key_acid = f"acfun_last_acid_{user_id}"
        last_acid = str(self._state_get(state_key_acid, "") or "")
        latest = items[0]

        if not last_acid or user_id not in self.acfun_session_initialized_uids:
            self._state_update({state_key_acid: latest.acid})
            self.acfun_session_initialized_uids.add(user_id)
            logger.info(f"AcFunPush: 初始化 UID={user_id} 的监控基线为 {latest.acid}")
            return []

        new_items: list[AcFunVideoItem] = []
        for item in items:
            if item.acid == last_acid:
                break
            new_items.append(item)

        self.acfun_session_initialized_uids.add(user_id)
        self._state_update({state_key_acid: latest.acid})
        new_items.reverse()
        return new_items

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_acfun_message(self, event: AstrMessageEvent):
        if not self.acfun_auto_parse_enabled:
            return
        if not self._is_acfun_passive_session_allowed(event):
            return
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return

        messages = event.get_messages() or []
        if messages:
            first = messages[0]
            if isinstance(first, At) and str(first.qq) != str(event.get_self_id()):
                return

        self.acfun_debouncer.update_ttl(self.debounce_seconds)

        try:
            target = await self.acfun_link_resolver.extract_parse_target(
                messages, event.message_str or ""
            )
            if target is None:
                return

            debounce_key = target.raw_input or target.acid
            if self.acfun_debouncer.hit_link(event.unified_msg_origin, debounce_key):
                return

            card = await self.acfun_service.fetch_video_card(target)
            await self._cleanup_media_cache(force=False, keep_paths=[card.video_path])

            acfun_target = AcFunParseTarget(
                acid=card.acid,
                raw_input=target.raw_input,
                source_kind=target.source_kind,
            )
            should_emoji = (
                bool(self.acfun_passive_config.get("acfun_qq_link_emoji_enabled", True))
                and event.get_platform_id() == "aiocqhttp"
                and acfun_target.source_kind in {"link", "card"}
            )
            if should_emoji:
                await self._attach_qq_emoji_reaction(event, ParseTarget(bvid=card.acid, source_kind=target.source_kind))

            if self.acfun_debouncer.hit_resource(event.unified_msg_origin, card.acid):
                return

            chains = self._build_acfun_message_chains(card, push=False)
            for chain in chains:
                yield event.chain_result(chain.chain)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.error(f"AcFunPush: 自动解析失败: {err}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("acfun_parse_on", alias={"开启A站解析", "开启acfun解析"})
    async def acfun_parse_on(self, event: AstrMessageEvent):
        passive_settings = self.config.setdefault("acfun_passive_settings", {})
        blacklist = passive_settings.setdefault("acfun_session_blacklist", [])
        if not isinstance(blacklist, list):
            blacklist = []
            passive_settings["acfun_session_blacklist"] = blacklist
        whitelist = passive_settings.setdefault("acfun_session_whitelist", [])
        if not isinstance(whitelist, list):
            whitelist = []
            passive_settings["acfun_session_whitelist"] = whitelist
        umo = event.unified_msg_origin
        if umo in blacklist:
            blacklist.remove(umo)
        if whitelist and umo not in whitelist:
            whitelist.append(umo)
        self._persist_passive_session_filters()
        yield event.plain_result("✅ 已开启当前会话的 AcFun 被动解析。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("acfun_parse_off", alias={"关闭A站解析", "关闭acfun解析"})
    async def acfun_parse_off(self, event: AstrMessageEvent):
        passive_settings = self.config.setdefault("acfun_passive_settings", {})
        blacklist = passive_settings.setdefault("acfun_session_blacklist", [])
        if not isinstance(blacklist, list):
            blacklist = []
            passive_settings["acfun_session_blacklist"] = blacklist
        umo = event.unified_msg_origin
        if umo not in blacklist:
            blacklist.append(umo)
        self._persist_passive_session_filters()
        yield event.plain_result("✅ 已关闭当前会话的 AcFun 被动解析。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("acfun_check", alias={"accheck"})
    async def acfun_check(self, event: AstrMessageEvent, source: str = ""):
        if source:
            try:
                user_id = self.acfun_service.resolve_user_id(source)
                if user_id is None:
                    yield event.plain_result("❌ 无法从输入中解析出用户 ID，请提供 UID 或 AcFun 主页链接。")
                    return
                recent = await self._check_acfun_user_videos(user_id, force_fetch=True)
                if not recent:
                    yield event.plain_result(f"ℹ️ UID {user_id} 最近没有获取到视频。")
                    return
                card = await self.acfun_service.fetch_video_card(
                    AcFunParseTarget(acid=recent[0].acid)
                )
                await self._cleanup_media_cache(force=False, keep_paths=[card.video_path])
                for chain in self._build_acfun_message_chains(card, push=True):
                    await self.context.send_message(event.unified_msg_origin, chain)
                yield event.plain_result(f"✅ 已向当前会话发送 UID {user_id} 的最新视频。")
            except Exception as err:
                logger.error(f"AcFunPush: 手动检查失败: {err}")
                yield event.plain_result(f"❌ 手动检查失败: {err}")
            return

        rules = self._resolve_acfun_monitor_rules()
        if not rules:
            yield event.plain_result("❌ 没有可用的 AcFun 监控规则，请先在插件配置中填写订阅规则。")
            return

        rule = rules[0]
        recent = await self._check_acfun_user_videos(rule.user_id, force_fetch=True)
        if not recent:
            yield event.plain_result(f"ℹ️ UID {rule.user_id} 最近没有获取到视频。")
            return

        card = await self.acfun_service.fetch_video_card(
            AcFunParseTarget(acid=recent[0].acid)
        )
        await self._cleanup_media_cache(force=False, keep_paths=[card.video_path])
        result = await self._send_acfun_card_to_targets(card, list(rule.targets))
        yield event.plain_result(
            f"✅ 推送完成：成功目标 {result['target_success']}，失败目标 {result['target_failure']}。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("acfun_check_all", alias={"accheck_all"})
    async def acfun_check_all(self, event: AstrMessageEvent):
        rules = self._resolve_acfun_monitor_rules()
        if not rules:
            yield event.plain_result("❌ 没有可用的 AcFun 监控规则，请先在插件配置中填写订阅规则。")
            return

        yield event.plain_result(f"🔍 正在立即检查 {len(rules)} 条 AcFun 监控规则...")
        summaries: list[str] = []
        request_interval = safe_int(
            self.runtime_config.get("request_interval", DEFAULT_REQUEST_INTERVAL_SECONDS),
            DEFAULT_REQUEST_INTERVAL_SECONDS,
            minimum=1,
            maximum=60,
        )
        request_jitter = safe_int(
            self.runtime_config.get("request_interval_jitter", 0),
            0,
            minimum=0,
            maximum=30,
        )

        for index, rule in enumerate(rules):
            if index > 0:
                await asyncio.sleep(self._pick_interval(request_interval, request_jitter, minimum=1))
            try:
                recent = await self._check_acfun_user_videos(rule.user_id, force_fetch=True)
                if not recent:
                    summaries.append(f"ℹ️ UID {rule.user_id} 没有获取到视频")
                    continue
                card = await self.acfun_service.fetch_video_card(
                    AcFunParseTarget(acid=recent[0].acid)
                )
                await self._cleanup_media_cache(force=False, keep_paths=[card.video_path])
                result = await self._send_acfun_card_to_targets(card, list(rule.targets))
                if result["target_failure"] > 0:
                    summaries.append(
                        f"⚠️ UID {rule.user_id} 部分成功："
                        f"成功 {result['target_success']}，失败 {result['target_failure']}"
                    )
                else:
                    summaries.append(f"✅ UID {rule.user_id} 推送成功")
            except Exception as err:
                logger.error(f"AcFunPush: 检查 UID {rule.user_id} 失败: {err}")
                summaries.append(f"❌ UID {rule.user_id} 检查失败: {err}")

        yield event.plain_result("\n".join(summaries))

    async def _acfun_run_monitor(self):
        logger.info("AcFunPush: 监控任务已启动")
        await asyncio.sleep(STARTUP_DELAY_SECONDS)

        while self.running:
            try:
                rules = self._resolve_acfun_monitor_rules()
                interval = safe_int(
                    self.runtime_config.get("check_interval", DEFAULT_CHECK_INTERVAL_MINUTES),
                    DEFAULT_CHECK_INTERVAL_MINUTES,
                    minimum=1,
                    maximum=24 * 60,
                )
                jitter = safe_int(
                    self.runtime_config.get("check_interval_jitter", 0),
                    0,
                    minimum=0,
                    maximum=180,
                )
                sleep_minutes = self._pick_interval(interval, jitter, minimum=1)

                if not rules:
                    logger.debug("AcFunPush: 当前无可用监控规则")
                else:
                    await self._cleanup_media_cache(force=False)
                    await self._acfun_run_monitor_cycle(rules)

                logger.debug(f"AcFunPush: 下次检查将在 {sleep_minutes} 分钟后执行")
                await asyncio.sleep(sleep_minutes * 60)
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.error(f"AcFunPush: 监控循环异常: {err}")
                await asyncio.sleep(60)

    async def _acfun_run_monitor_cycle(self, rules: Sequence[AcFunMonitorRule]) -> None:
        request_interval = safe_int(
            self.runtime_config.get("request_interval", DEFAULT_REQUEST_INTERVAL_SECONDS),
            DEFAULT_REQUEST_INTERVAL_SECONDS,
            minimum=1,
            maximum=60,
        )
        request_jitter = safe_int(
            self.runtime_config.get("request_interval_jitter", 0),
            0,
            minimum=0,
            maximum=30,
        )

        for index, rule in enumerate(rules):
            if index > 0:
                await asyncio.sleep(self._pick_interval(request_interval, request_jitter, minimum=1))
            try:
                new_items = await self._check_acfun_user_videos(rule.user_id)
                if not new_items:
                    continue
                for item in new_items:
                    card = await self.acfun_service.fetch_video_card(
                        AcFunParseTarget(acid=item.acid)
                    )
                    await self._cleanup_media_cache(force=False, keep_paths=[card.video_path])
                    await self._send_acfun_card_to_targets(card, list(rule.targets))
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.error(f"AcFunPush: 轮询 UID {rule.user_id} 失败: {err}")


def safe_int(value: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    if minimum is not None and number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum
    return number


def extract_page_num(value: str) -> int:
    try:
        parsed = urlparse(ensure_scheme(value))
        page = parse_qs(parsed.query).get("p", ["1"])[0]
        return max(1, int(page))
    except Exception:
        return 1


def strip_trailing_punctuation(value: str) -> str:
    return value.strip().rstrip(TRAILING_PUNCTUATION)


def ensure_scheme(value: str) -> str:
    if value.startswith("http://") or value.startswith("https://") or value.startswith("bilibili://"):
        return value
    if value.startswith("space.bilibili.com") or value.startswith("b23.tv") or value.startswith("bili2233.cn") or value.startswith("www.bilibili.com") or value.startswith("m.bilibili.com"):
        return f"https://{value}"
    return value


def iter_string_values(payload: Any) -> Iterable[str]:
    if isinstance(payload, str):
        yield payload
        return
    if isinstance(payload, dict):
        for value in payload.values():
            yield from iter_string_values(value)
        return
    if isinstance(payload, list):
        for value in payload:
            yield from iter_string_values(value)


def extract_json_url(data: Any) -> str | None:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as err:
            logger.debug(f"BilibiliPush: 解析分享卡片 JSON 失败: {err}")
            return None
    if not isinstance(data, dict):
        return None

    meta = data.get("meta")
    if isinstance(meta, dict):
        for key1, key2 in (
            ("music", "musicUrl"),
            ("detail_1", "qqdocurl"),
            ("news", "jumpUrl"),
            ("music", "jumpUrl"),
        ):
            section = meta.get(key1)
            if isinstance(section, dict):
                url = section.get(key2)
                if isinstance(url, str) and url:
                    return strip_trailing_punctuation(url)

    for value in iter_string_values(data):
        match = URL_PATTERN.search(value)
        if match:
            return strip_trailing_punctuation(match.group("url"))
        match = BV_PATTERN.search(value)
        if match:
            return match.group("bvid")
    return None


def extract_acfun_json_url(data: Any) -> str | None:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as err:
            logger.debug(f"AcFunPush: 解析分享卡片 JSON 失败: {err}")
            return None
    if not isinstance(data, dict):
        return None

    meta = data.get("meta")
    if isinstance(meta, dict):
        for key1, key2 in (
            ("detail_1", "qqdocurl"),
            ("news", "jumpUrl"),
        ):
            section = meta.get(key1)
            if isinstance(section, dict):
                url = section.get(key2)
                if isinstance(url, str) and url:
                    return strip_trailing_punctuation(url)

    for value in iter_string_values(data):
        match = ACFUN_URL_PATTERN.search(value)
        if match:
            return strip_trailing_punctuation(match.group(0))
        match = ACID_PATTERN.search(value)
        if match:
            return match.group(0)
    return None


def normalize_cover_url(url: Any) -> str:
    if not isinstance(url, str):
        return ""
    cleaned = url.strip()
    if not cleaned or len(cleaned) > 2048:
        return ""
    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    elif not cleaned.startswith("http://") and not cleaned.startswith("https://"):
        cleaned = f"https://{cleaned.lstrip('/')}"

    parsed = urlparse(cleaned)
    if not _is_trusted_cover_url(parsed):
        return ""
    return parsed.geturl()


def _is_trusted_cover_url(parsed) -> bool:
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.username or parsed.password or parsed.port is not None:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    return any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in TRUSTED_COVER_HOST_SUFFIXES
    )


def sanitize_desc(desc: str, limit: int) -> str:
    cleaned = " ".join(str(desc or "").split())
    if limit <= 0:
        return cleaned
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_timestamp(timestamp: int) -> str:
    if timestamp <= 0:
        return "未知"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def format_count(value: int) -> str:
    value = max(0, int(value))
    if value >= 100000000:
        return f"{value / 100000000:.1f}亿"
    if value >= 10000:
        return f"{value / 10000:.1f}万"
    return str(value)


def format_bytes(value: int) -> str:
    value = max(0, int(value))
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{value} B"


def suffix_from_url(url: str, default: str) -> str:
    suffix = Path(urlparse(url).path).suffix
    return suffix if suffix else default


async def safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


async def merge_av(
    v_path: Path,
    a_path: Path,
    output_path: Path,
    timeout_seconds: int = DEFAULT_VIDEO_DOWNLOAD_TIMEOUT_SECONDS,
) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(v_path),
        "-i",
        str(a_path),
        "-c",
        "copy",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        str(output_path),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as err:
        raise MediaDownloadError("未安装 ffmpeg，无法合并 B 站音视频") from err

    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as err:
        process.kill()
        await process.wait()
        raise MediaDownloadError(
            f"ffmpeg 合并超时（{timeout_seconds} 秒），已终止该进程"
        ) from err

    if process.returncode != 0:
        error_message = stderr.decode("utf-8", errors="ignore").strip()
        raise MediaDownloadError(f"ffmpeg 合并失败: {error_message}")

    await safe_unlink(v_path)
    await safe_unlink(a_path)


def _extract_json_object(text: str, start: int) -> str | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape_next = False
    i = start
    while i < len(text):
        ch = text[i]
        if escape_next:
            escape_next = False
            i += 1
            continue
        if ch == "\\":
            if in_string:
                escape_next = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            i += 1
            continue
        if in_string:
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None
