"""宿主枚举与常量替身。"""

from enum import Enum


class MediaType(Enum):
    """媒体类型。"""

    MOVIE = "电影"
    TV = "电视剧"
    MUSIC = "音乐"
    UNKNOWN = "未知"


class MediaSource(str, Enum):
    """媒体数据源（成员名与取值都对齐真机 ``app/schemas/types.py``）。"""

    # 桩宿主必须与真机同形，否则「按 media_source 值分派」的代码会在测试里看似
    # 通过、到真机才失效。2026-10-09 实测踩到两处失真：TMDB 的规范值是
    # "themoviedb"（不是 "tmdb"）、成员名是 IMDb（不是 IMDB）。
    TMDB = "themoviedb"
    Douban = "douban"
    Bangumi = "bangumi"
    AniList = "anilist"
    IMDb = "imdb"
    TVDB = "tvdb"
    MusicBrainz = "musicbrainz"
    TheAudioDB = "theaudiodb"
    DoubanMusic = "doubanmusic"


class MessageType(Enum):
    """通知场景（V3 名；旧名 NotificationType）。"""

    Download = "资源下载"
    Organize = "整理入库"
    Subscribe = "订阅"
    SiteMessage = "站点"
    MediaServer = "媒体服务器"
    Manual = "手动处理"
    Plugin = "插件"
    Agent = "智能体"
    Other = "其它"


# 旧名别名：宿主兼容层同样保留，供仍按旧名书写的调用点使用
NotificationType = MessageType


class NotificationChannel(Enum):
    """通知渠道（V3 名；旧名 MessageChannel）。"""

    Telegram = "Telegram"
    WeChat = "微信"
    Webhook = "Webhook"
    Slave = "Slave"


MessageChannel = NotificationChannel


class EventType(Enum):
    """事件类型。

    注意：本桩仅补充插件 P115StrmHelper 在 V3 实际用到的成员，
    取值对齐真实 MoviePilot V3（英文串）。其余成员维持原占位。
    """

    PluginReload = "plugin.reload"
    PluginData = "plugin.data"
    PluginAction = "plugin.action"
    TransferComplete = "transfer.complete"
    TransferFailed = "transfer.failed"
    SubtitleTransferComplete = "transfer.subtitle.complete"
    AudioTransferComplete = "transfer.audio.complete"
    DownloadFileDeleted = "downloadfile.deleted"
    UserMessage = "user.message"
    WebhookMessage = "webhook.message"
    MessageAction = "message.action"


class ChainEventType(Enum):
    """链事件类型。"""

    TransferRenameBuild = "整理重命名构建"
    StorageOperSelection = "存储操作选择"
    TransferIntercept = "整理拦截"
    TransferOverwriteCheck = "整理覆盖检查"


class MediaImageType(Enum):
    """媒体图片类型。"""

    Poster = "海报"
    Backdrop = "背景图"
    Logo = "Logo"


class ContentType(Enum):
    """消息内容类型。"""

    Text = "text"
    Image = "image"


class ModuleType(Enum):
    """模块类型。"""

    Other = "其它"


class OtherModulesType(Enum):
    """其它模块子类型。"""

    Other = "其它"


class StorageAction(Enum):
    """存储操作。"""

    List = "list"
    Upload = "upload"


MUSIC_ENTITY_ALBUM = "album"
