from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from typing import Any


class _DummyLogger:
    def __getattr__(self, _name: str):
        def _log(*_args: Any, **_kwargs: Any) -> None:
            return None

        return _log


class _DummyFilter:
    class EventMessageType:
        ALL = object()

    class PermissionType:
        ADMIN = object()

    @staticmethod
    def event_message_type(_message_type: Any):
        return lambda func: func

    @staticmethod
    def permission_type(_permission_type: Any):
        return lambda func: func

    @staticmethod
    def command(*_args: Any, **_kwargs: Any):
        return lambda func: func


class _DummyMessageChain:
    def __init__(self) -> None:
        self.chain: list[Any] = []

    def message(self, text: str):
        self.chain.append(text)
        return self

    def url_image(self, url: str):
        self.chain.append(url)
        return self


class _DummyImage:
    @staticmethod
    def fromBytes(data: bytes) -> bytes:
        return data


class _DummyVideoComponent:
    type = "Video"


class _DummyVideo:
    @staticmethod
    def fromFileSystem(path: str) -> _DummyVideoComponent:
        return _DummyVideoComponent()


class _DummyStar:
    def __init__(self, context: Any, config: dict | None = None) -> None:
        self.context = context
        self.config = config or {}


class _DummyStarTools:
    @staticmethod
    def get_data_dir(_name: str) -> Path:
        return Path("/tmp/astrbot_plugin_bilibili_push_tests")


def _install_astrbot_stubs() -> None:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = _DummyLogger()

    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    event.MessageChain = _DummyMessageChain
    event.filter = _DummyFilter

    message_components = types.ModuleType("astrbot.api.message_components")
    message_components.At = type("At", (), {})
    message_components.Image = _DummyImage
    message_components.Json = type("Json", (), {})
    message_components.Plain = type("Plain", (), {})
    message_components.Video = _DummyVideo

    star = types.ModuleType("astrbot.api.star")
    star.Context = object
    star.Star = _DummyStar
    star.StarTools = _DummyStarTools

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event
    sys.modules["astrbot.api.message_components"] = message_components
    sys.modules["astrbot.api.star"] = star


def _install_bilibili_stubs() -> None:
    bilibili_api = types.ModuleType("bilibili_api")

    class _RequestSettings:
        @staticmethod
        def set(*_args: Any, **_kwargs: Any) -> None:
            return None

    def select_client(_name: str) -> None:
        return None

    bilibili_api.Credential = type("Credential", (), {})
    bilibili_api.request_settings = _RequestSettings()
    bilibili_api.select_client = select_client

    login_v2 = types.ModuleType("bilibili_api.login_v2")
    login_v2.QrCodeLogin = type("QrCodeLogin", (), {})
    login_v2.QrCodeLoginEvents = types.SimpleNamespace(DONE="done", CONF="conf", TIMEOUT="timeout")

    user = types.ModuleType("bilibili_api.user")

    class _VideoOrder:
        PUBDATE = object()

    async def get_self_info(_credential: Any = None) -> dict[str, Any]:
        return {}

    user.User = type("User", (), {})
    user.VideoOrder = _VideoOrder
    user.get_self_info = get_self_info

    video = types.ModuleType("bilibili_api.video")
    for name in (
        "AudioStreamDownloadURL",
        "Video",
        "VideoCodecs",
        "VideoDownloadURLDataDetecter",
        "VideoQuality",
        "VideoStreamDownloadURL",
    ):
        setattr(video, name, type(name, (), {}))
    video.VideoQuality._720P = object()
    video.VideoCodecs.AVC = object()

    sys.modules["bilibili_api"] = bilibili_api
    sys.modules["bilibili_api.login_v2"] = login_v2
    sys.modules["bilibili_api.user"] = user
    sys.modules["bilibili_api.video"] = video


_install_astrbot_stubs()
_install_bilibili_stubs()

plugin = importlib.import_module("main")
