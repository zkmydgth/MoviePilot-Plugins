from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, cast

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from clouddrive2_client import CloudDriveClient
from pytz import timezone

from app.core.config import settings
from app.core.event import Event, eventmanager
from app.helper.storage import StorageHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import (
    FileItem,
    NotificationType,
    Response,
    StorageOperSelectionEventData,
    StorageUsage,
)
from app.schemas.types import ChainEventType, EventType

from .assistant import (
    add_offline_files,
    check_cookie,
    check_upload_tasks,
    get_cd2_system_info,
    restart_cd2,
)
from .clouddrive_api import CloudDriveApi
from .version import VERSION


class CloudDriveDisk(_PluginBase):
    """
    CloudDrive2 储存插件
    """

    plugin_name = "CloudDrive2储存"
    plugin_desc = "使存储支持 CloudDrive2，grpc 原生 API 操作。"
    plugin_icon = "Cloudrive_A.png"
    plugin_version = VERSION
    plugin_author = "DDSRem"
    author_url = "https://github.com/DDSRem"
    plugin_config_prefix = "clouddrivedisk_"
    plugin_order = 99
    auth_level = 1

    _enabled = False
    _client: Optional[CloudDriveClient] = None
    _disk_name = "CloudDrive储存"
    _clouddrive_api: Optional[CloudDriveApi] = None
    _host = ""
    _port = "19798"
    _username = ""
    _password = ""
    UploadMode = Literal["remote_upload", "direct_write"]
    _upload_mode: UploadMode = "direct_write"

    _cron = None
    _notify = False
    _msgtype = None
    _keyword = None
    _black_dir = ""
    _cloud_path = ""
    _onlyonce = False
    _cd2_restart = False
    _scheduler: Optional[BackgroundScheduler] = None

    def __init__(self) -> None:
        super().__init__()

    def init_plugin(self, config: Optional[Dict] = None) -> None:
        """
        初始化插件

        :param config: 插件配置
        """
        if not config:
            return
        storage_helper = StorageHelper()
        storages = storage_helper.get_storagies()
        if not any(
            s.type == self._disk_name and s.name == self._disk_name for s in storages
        ):
            storage_helper.add_storage(
                storage=self._disk_name, name=self._disk_name, conf={}
            )
        self._enabled = config.get("enabled", False)
        self._host = (config.get("host") or "localhost").strip()
        self._port = str(config.get("port") or "19798").strip()
        self._username = (config.get("username") or "").strip()
        self._password = config.get("password") or ""
        self._upload_mode = cast(
            self.UploadMode,
            (config.get("upload_mode") or "direct_write").strip() or "direct_write",
        )

        self._cron = config.get("cron")
        self._notify = config.get("notify", False)
        self._msgtype = config.get("msgtype")
        self._keyword = config.get("keyword")
        self._black_dir = config.get("black_dir") or ""
        self._cloud_path = config.get("cloud_path") or ""
        self._onlyonce = config.get("onlyonce", False)
        self._cd2_restart = config.get("cd2_restart", False)

        self._client = None
        self._clouddrive_api = None

        # 停止现有任务
        self.stop_service()

        if not self._enabled and not self._onlyonce and not self._cd2_restart:
            return
        if not self._username or not self._password:
            logger.warning("【CloudDrive】未配置用户名或密码，储存模块将不可用")
            return

        address = f"{self._host}:{self._port}"
        try:
            self._client = CloudDriveClient(
                address,
                options=[
                    ("grpc.keepalive_time_ms", 30000),
                    ("grpc.keepalive_timeout_ms", 10000),
                    ("grpc.keepalive_permit_without_calls", True),
                    ("grpc.http2.max_pings_without_data", 0),
                ],
            )
            if not self._client.authenticate(self._username, self._password):
                logger.error("【CloudDrive】认证失败，请检查用户名与密码")
                self._client.close()
                self._client = None
                return
            if self._enabled:
                download_base = f"http://{self._host}:{self._port}"
                self._clouddrive_api = CloudDriveApi(
                    self._client,
                    disk_name=self._disk_name,
                    download_base=download_base,
                    upload_mode=self._upload_mode,
                )
        except Exception as e:
            logger.error("【CloudDrive】客户端创建失败: %s", e)
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None
            return

        # 周期运行
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._cron:
            try:
                self._scheduler.add_job(
                    func=self.check,
                    trigger=CronTrigger.from_crontab(self._cron),
                    name="CloudDrive2助手定时任务",
                )
            except Exception as err:
                logger.error("定时任务配置错误: %s", err)

        # 立即运行一次
        if self._onlyonce:
            logger.info("CloudDrive2助手定时任务，立即运行一次")
            self._scheduler.add_job(
                self.check,
                "date",
                run_date=datetime.now(tz=timezone(settings.TZ)) + timedelta(seconds=3),
                name="CloudDrive2助手定时任务",
            )
            self._onlyonce = False
            self.__update_config()

        # 立即重启一次
        if self._cd2_restart:
            logger.info("CloudDrive2重启任务，立即运行一次")
            self._scheduler.add_job(
                self.restart_cd2,
                "date",
                run_date=datetime.now(tz=timezone(settings.TZ)) + timedelta(seconds=3),
                name="CloudDrive2重启任务",
            )
            self._cd2_restart = False
            self.__update_config()

        # 启动任务
        if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

    def __update_config(self) -> None:
        """
        更新插件配置
        """
        self.update_config(
            {
                "enabled": self._enabled,
                "host": self._host,
                "port": self._port,
                "username": self._username,
                "password": self._password,
                "upload_mode": self._upload_mode,
                "cron": self._cron,
                "notify": self._notify,
                "msgtype": self._msgtype,
                "keyword": self._keyword,
                "black_dir": self._black_dir,
                "cloud_path": self._cloud_path,
                "onlyonce": self._onlyonce,
                "cd2_restart": self._cd2_restart,
            }
        )

    def get_state(self) -> bool:
        """
        返回插件是否已启用
        """
        return self._enabled

    def check(self) -> None:
        """
        检查cookie和上传任务
        """
        if not self._client:
            return
        cookie_error = check_cookie(self._client, self._black_dir)
        if cookie_error and self._notify:
            self.__send_notify(cookie_error)
        task_error = check_upload_tasks(self._client, self._keyword or "")
        if task_error and self._notify:
            self.__send_notify(task_error)

    def __send_notify(self, msg: str) -> None:
        """
        发送通知

        :param msg: 通知内容
        """
        mtype = NotificationType.Manual
        if self._msgtype:
            try:
                mtype = NotificationType.__getitem__(str(self._msgtype))
            except Exception:
                pass
        self.post_message(
            title="CloudDrive2助手通知",
            mtype=mtype,
            text=msg,
        )

    @eventmanager.register(EventType.PluginAction)
    def restart_cd2(self, event: Event = None) -> None:
        """
        重启CloudDrive2

        :param event: 事件对象
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "cd2_restart":
                return
            if restart_cd2(self._client):
                self.post_message(
                    channel=event.event_data.get("channel"),
                    title="CloudDrive2重启成功！",
                    userid=event.event_data.get("user"),
                )
            else:
                self.post_message(
                    channel=event.event_data.get("channel"),
                    title="CloudDrive2重启失败！",
                    userid=event.event_data.get("user"),
                )
        else:
            restart_cd2(self._client)

    @eventmanager.register(EventType.PluginAction)
    def add_offline_files(self, event: Event = None) -> None:
        """
        离线下载

        :param event: 事件对象
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "cloud_download":
                return
            args = event_data.get("arg_str")
            if not args:
                logger.error("缺少参数: %s", event_data)
                return

            args = args.replace(" ", "\n")
            _cloud_path = self._cloud_path.strip()
            if args.split("\n")[0].startswith("/"):
                _cloud_path = str(args.split("\n")[0])
                args = args.replace(f"{_cloud_path}\n", "")

            if not _cloud_path:
                logger.error("请先设置云盘路径")
                if event.event_data.get("user"):
                    self.post_message(
                        channel=event.event_data.get("channel"),
                        title="请先设置云盘路径！",
                        userid=event.event_data.get("user"),
                    )
                return

            logger.info("获取到离线云盘路径：%s", _cloud_path)
            logger.info("开始离线下载：%s", args)

            success, error_message = add_offline_files(self._client, args, _cloud_path)
            if success:
                logger.info("离线下载成功")
                if event.event_data.get("user"):
                    self.post_message(
                        channel=event.event_data.get("channel"),
                        title=f"{_cloud_path} 离线下载成功！",
                        userid=event.event_data.get("user"),
                    )
            else:
                logger.error("离线下载失败：%s", error_message)
                if event.event_data.get("user"):
                    self.post_message(
                        channel=event.event_data.get("channel"),
                        title="离线下载失败！",
                        userid=event.event_data.get("user"),
                        text=f"错误信息：{error_message}",
                    )

    @eventmanager.register(EventType.PluginAction)
    def cd2_info(self, event: Event = None) -> None:
        """
        获取CloudDrive2信息

        :param event: 事件对象
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "cd2_info":
                return

            info = get_cd2_system_info(self._client, self._black_dir)
            text = (
                f"CPU占用：{info.get('cpuUsage')}\n"
                f"内存占用：{info.get('memUsageKB')}\n"
                f"运行时间：{info.get('uptime')}\n"
                f"打开文件数量：{info.get('fhTableCount')}\n"
                f"目录缓存数量：{info.get('dirCacheCount')}\n"
                f"临时文件数量：{info.get('tempFileCount')}\n"
                f"上传任务数量：{info.get('upload_count')}\n"
                f"下载任务数量：{info.get('download_count')}\n"
                f"下载速度：{info.get('download_speed')}\n"
                f"上传速度：{info.get('upload_speed')}\n"
                f"存储空间：{info.get('cloud_space')}\n"
            )
            self.post_message(
                channel=event.event_data.get("channel"),
                title="CloudDrive2系统信息",
                userid=event.event_data.get("user"),
                text=text,
            )

    def homepage(self, apikey: str, name: Optional[str] = None) -> Any:
        """
        homepage自定义api

        :param apikey: API密钥
        :param name: 配置名称（unused，保持兼容）
        :return: 系统信息字典或错误响应
        """
        if apikey != settings.API_TOKEN:
            return Response(success=False, message="API密钥错误")
        if not self._client:
            return Response(success=False, message="CloudDrive2未连接")
        return get_cd2_system_info(self._client, self._black_dir)

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        返回插件远程命令列表

        :return: /cd2_restart、/cd2_info、/cd 命令
        """
        return [
            {
                "cmd": "/cd2_restart",
                "event": EventType.PluginAction,
                "desc": "CloudDrive2重启",
                "category": "",
                "data": {"action": "cd2_restart"},
            },
            {
                "cmd": "/cd2_info",
                "event": EventType.PluginAction,
                "desc": "CloudDrive2系统信息",
                "category": "",
                "data": {"action": "cd2_info"},
            },
            {
                "cmd": "/cd",
                "event": EventType.PluginAction,
                "desc": "云下载",
                "category": "",
                "data": {"action": "cloud_download"},
            },
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """
        返回插件 API 端点列表

        :return: homepage 自定义 API
        """
        return [
            {
                "path": "/homepage",
                "endpoint": self.homepage,
                "methods": ["GET"],
                "summary": "HomePage",
                "description": "HomePage自定义api",
            }
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面

        :return: (页面配置列表, 表单默认值字典)
        """
        # 编历 NotificationType 枚举，生成消息类型选项
        MsgTypeOptions = []
        for item in NotificationType:
            MsgTypeOptions.append({"title": item.value, "value": item.name})

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "开启通知",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "cd2_restart",
                                            "label": "cd2重启一次",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "host",
                                            "label": "CloudDrive 地址",
                                            "hint": "如 localhost 或 192.168.1.100，不要带 http(s)",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "port",
                                            "label": "端口",
                                            "hint": "默认 19798",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "upload_mode",
                                            "label": "上传模式",
                                            "items": [
                                                {
                                                    "title": "远程上传",
                                                    "value": "remote_upload",
                                                },
                                                {
                                                    "title": "直写上传",
                                                    "value": "direct_write",
                                                },
                                            ],
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "username",
                                            "label": "用户名",
                                            "hint": "CloudDrive 登录用户名",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "password",
                                            "label": "密码",
                                            "type": "{{ 'password' }}",
                                            "hint": "CloudDrive 登录密码",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VCronField",
                                        "props": {
                                            "model": "cron",
                                            "label": "检测周期",
                                            "placeholder": "5位cron表达式",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "keyword",
                                            "label": "检测关键字",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "multiple": False,
                                            "chips": True,
                                            "model": "msgtype",
                                            "label": "消息类型",
                                            "items": MsgTypeOptions,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "black_dir",
                                            "label": "cd2黑名单目录",
                                            "placeholder": "cd2上添加的本地目录(多个目录用英文逗号分隔)",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cloud_path",
                                            "label": "云下载路径",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "density": "compact",
                                            "class": "mt-2",
                                        },
                                        "content": [
                                            {
                                                "component": "div",
                                                "text": "上传模式说明：",
                                            },
                                            {
                                                "component": "div",
                                                "text": "• 远程上传：CloudDrive2 Remote Upload 协议，兼容性更好（默认）。",
                                            },
                                            {
                                                "component": "div",
                                                "text": "• 直写上传：CreateFile/WriteToFile/CloseFile 方式，且在 CloseFile 后轮询等待云端上传完成。",
                                            },
                                            {
                                                "component": "div",
                                                "text": "默认使用直写上传；如果远程上传出现不稳定（如超时、失败重试频繁等），可以切换为直写上传尝试。",
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "周期检测CloudDrive2上传任务，检测是否命中检测关键词，发送通知。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "周期检测CloudDrive2云盘CK是否过期，发送通知（挂载的本地路径可添加黑名单）。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "success",
                                            "variant": "tonal",
                                        },
                                        "content": [
                                            {
                                                "component": "span",
                                                "text": "HomePage配置教程请参考：",
                                            },
                                            {
                                                "component": "a",
                                                "props": {
                                                    "href": "https://raw.githubusercontent.com/thsrite/MoviePilot-Plugins/main/docs/Cd2Assistant.md",
                                                    "target": "_blank",
                                                },
                                                "text": "https://raw.githubusercontent.com/thsrite/MoviePilot-Plugins/main/docs/Cd2Assistant.md",
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "如安装完启用插件后，HomePage提示404，重启MoviePilot即可。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "host": "localhost",
            "port": "19798",
            "username": "",
            "password": "",
            "upload_mode": "direct_write",
            "cron": "*/10 * * * *",
            "keyword": "账号异常",
            "msgtype": "Manual",
            "notify": False,
            "onlyonce": False,
            "cd2_restart": False,
            "black_dir": "",
            "cloud_path": "",
        }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面

        :return: CloudDrive2 仪表盘页面配置，含系统状态卡片
        """
        if not self._client:
            return []
        cd2_info = get_cd2_system_info(self._client, self._black_dir)
        cd2_url = f"http://{self._host}:{self._port}"
        return [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {"class": "text-h6"},
                                                        "text": self._disk_name,
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "a",
                                                                "props": {
                                                                    "class": "text-caption",
                                                                    "href": cd2_url,
                                                                    "target": "_blank",
                                                                },
                                                                "text": cd2_url,
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-caption"
                                                        },
                                                        "text": "CPU占用",
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-h6"
                                                                },
                                                                "text": cd2_info.get(
                                                                    "cpuUsage"
                                                                ),
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-caption"
                                                        },
                                                        "text": "内存占用",
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-h6"
                                                                },
                                                                "text": cd2_info.get(
                                                                    "memUsageKB"
                                                                ),
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-caption"
                                                        },
                                                        "text": "运行时间",
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-h6"
                                                                },
                                                                "text": cd2_info.get(
                                                                    "uptime"
                                                                ),
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-caption"
                                                        },
                                                        "text": "打开文件数",
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-h6"
                                                                },
                                                                "text": cd2_info.get(
                                                                    "fhTableCount"
                                                                ),
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-caption"
                                                        },
                                                        "text": "缓存目录数",
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-h6"
                                                                },
                                                                "text": cd2_info.get(
                                                                    "dirCacheCount"
                                                                ),
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-caption"
                                                        },
                                                        "text": "临时文件数",
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-h6"
                                                                },
                                                                "text": cd2_info.get(
                                                                    "tempFileCount"
                                                                ),
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-caption"
                                                        },
                                                        "text": "下载任务数",
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-h6"
                                                                },
                                                                "text": cd2_info.get(
                                                                    "download_count"
                                                                ),
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-caption"
                                                        },
                                                        "text": "上传任务数",
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-h6"
                                                                },
                                                                "text": cd2_info.get(
                                                                    "upload_count"
                                                                ),
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-caption"
                                                        },
                                                        "text": "实时速率",
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-h6"
                                                                },
                                                                "text": f"↑ {cd2_info.get('download_speed')}  ↓ {cd2_info.get('upload_speed')}",
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4, "sm": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "d-flex align-center"},
                                        "content": [
                                            {
                                                "component": "div",
                                                "content": [
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-caption"
                                                        },
                                                        "text": "存储空间",
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "d-flex align-center flex-wrap"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-h6"
                                                                },
                                                                "text": cd2_info.get(
                                                                    "cloud_space"
                                                                ),
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ]

    def get_module(self) -> Dict[str, Any]:
        """
        返回储存模块能力映射
        """
        return {
            "list_files": self.list_files,
            "any_files": self.any_files,
            "download_file": self.download_file,
            "upload_file": self.upload_file,
            "delete_file": self.delete_file,
            "rename_file": self.rename_file,
            "get_file_item": self.get_file_item,
            "get_parent_item": self.get_parent_item,
            "snapshot_storage": self.snapshot_storage,
            "storage_usage": self.storage_usage,
            "support_transtype": self.support_transtype,
            "create_folder": self.create_folder,
            "exists": self.exists,
            "get_item": self.get_item,
        }

    @eventmanager.register(ChainEventType.StorageOperSelection)
    def storage_oper_selection(self, event: Event) -> None:
        """
        监听储存选择事件，当所选储存为本插件时注入 storage_oper 为 CloudDriveApi

        :param event: 事件对象，event.event_data 含 storage、storage_oper
        """
        if not self._enabled or not self._clouddrive_api:
            return
        event_data: StorageOperSelectionEventData = event.event_data
        if event_data.storage == self._disk_name:
            event_data.storage_oper = self._clouddrive_api  # noqa

    def list_files(
        self, fileitem: FileItem, recursion: bool = False
    ) -> Optional[List[FileItem]]:
        """
        列出目录下文件（及可选递归子目录）

        :param fileitem: 目录或文件项，storage 需为本插件储存名
        :param recursion: 是否递归列出子目录中的文件
        :return: 文件项列表；非本储存或未就绪时返回空列表
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return []
        if recursion:
            result = self._clouddrive_api.iter_files(fileitem)
            if result is not None:
                return result
        result: List[FileItem] = []

        def __get_files(_item: FileItem, _r: bool = False) -> None:
            _items = self._clouddrive_api.list(_item)  # type: ignore[union-attr]
            if _items:
                if _r:
                    for t in _items:
                        if t.type == "dir":
                            __get_files(t, _r)
                        else:
                            result.append(t)
                else:
                    result.extend(_items)

        __get_files(fileitem, recursion)
        return result

    def any_files(
        self, fileitem: FileItem, extensions: Optional[list] = None
    ) -> Optional[bool]:
        """
        判断目录（含子目录）下是否存在文件；可限定扩展名

        :param fileitem: 目录项
        :param extensions: 扩展名列表（如 [".mp4", ".mkv"]），None 表示任意文件
        :return: 存在返回 True，不存在返回 False；非本储存或未就绪返回 None
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None

        def __any_file(_item: FileItem) -> bool:
            _items = self._clouddrive_api.list(_item)  # type: ignore[union-attr]
            if _items:
                if not extensions:
                    return True
                for t in _items:
                    if (
                        t.type == "file"
                        and t.extension
                        and f".{t.extension.lower()}" in extensions
                    ):
                        return True
                    if t.type == "dir" and __any_file(t):
                        return True
            return False

        return __any_file(fileitem)

    def create_folder(self, fileitem: FileItem, name: str) -> Optional[FileItem]:
        """
        在指定目录下创建文件夹

        :param fileitem: 父目录项
        :param name: 新文件夹名称
        :return: 新目录的 FileItem；失败或非本储存时返回 None
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.create_folder(fileitem, name)

    def download_file(
        self, fileitem: FileItem, path: Optional[Path] = None
    ) -> Optional[Path]:
        """
        将云端文件下载到本地

        :param fileitem: 要下载的文件项
        :param path: 本地保存目录，None 时使用临时目录
        :return: 本地文件路径；失败或非本储存时返回 None
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.download(fileitem, path)

    def upload_file(
        self,
        fileitem: FileItem,
        path: Path,
        new_name: Optional[str] = None,
    ) -> Optional[FileItem]:
        """
        将本地文件上传到云端指定目录

        :param fileitem: 目标目录项
        :param path: 本地文件路径
        :param new_name: 云端文件名，None 时使用本地文件名
        :return: 上传成功后的云端文件 FileItem；失败或非本储存时返回 None
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.upload(fileitem, path, new_name)

    def delete_file(self, fileitem: FileItem) -> Optional[bool]:
        """
        删除云端文件或目录

        :param fileitem: 要删除的文件或目录项
        :return: 成功返回 True，失败返回 False；非本储存或未就绪返回 None
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.delete(fileitem)

    def rename_file(self, fileitem: FileItem, name: str) -> Optional[bool]:
        """
        重命名云端文件或目录

        :param fileitem: 要重命名的项
        :param name: 新名称
        :return: 成功返回 True，失败返回 False；非本储存或未就绪返回 None
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.rename(fileitem, name)

    def exists(self, fileitem: FileItem) -> Optional[bool]:
        """
        判断指定路径在云端是否存在

        :param fileitem: 文件或目录项（含 storage、path）
        :return: 存在返回 True，不存在返回 False；非本储存返回 None
        """
        if fileitem.storage != self._disk_name:
            return None
        return True if self.get_item(fileitem) else False

    def get_item(self, fileitem: FileItem) -> Optional[FileItem]:
        """
        按文件项获取对应的云端项（用于校验或取详情）

        :param fileitem: 含 storage、path 的文件项
        :return: 云端 FileItem；不存在或非本储存时返回 None
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self.get_file_item(storage=fileitem.storage, path=Path(fileitem.path))

    def get_file_item(self, storage: str, path: Path) -> Optional[FileItem]:
        """
        按储存名与路径获取云端文件或目录项

        :param storage: 储存名称，需为本插件储存名
        :param path: 云端路径
        :return: FileItem；不存在或非本储存时返回 None
        """
        if storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.get_item(path)

    def get_parent_item(self, fileitem: FileItem) -> Optional[FileItem]:
        """
        获取指定文件或目录的父目录项

        :param fileitem: 当前项
        :return: 父目录 FileItem；非本储存或未就绪时返回 None
        """
        if fileitem.storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.get_parent(fileitem)

    def snapshot_storage(
        self,
        storage: str,
        path: Path,
        last_snapshot_time: Optional[float] = None,
        max_depth: int = 5,
    ) -> Optional[Dict[str, Dict]]:
        """
        对指定目录做快照，收集路径下的文件信息（路径、大小、修改时间等）

        :param storage: 储存名称
        :param path: 快照根路径
        :param last_snapshot_time: 仅收录修改时间大于此时间戳的文件，用于增量快照
        :param max_depth: 最大递归深度
        :return: 路径到文件信息字典的映射；非本储存或未就绪时返回 None，根路径不存在时返回空字典
        """
        if storage != self._disk_name or not self._clouddrive_api:
            return None
        files_info: Dict[str, Dict] = {}

        def __snapshot_file(_fileitem: FileItem, current_depth: int = 0) -> None:
            try:
                if _fileitem.type == "dir":
                    if current_depth >= max_depth:
                        return
                    if (
                        last_snapshot_time
                        and _fileitem.modify_time
                        and _fileitem.modify_time <= last_snapshot_time
                    ):
                        return
                    sub_files = self._clouddrive_api.list(  # type: ignore[union-attr]
                        _fileitem
                    )
                    for sub_file in sub_files:
                        __snapshot_file(sub_file, current_depth + 1)
                else:
                    if (getattr(_fileitem, "modify_time", 0) or 0) > (
                        last_snapshot_time or 0
                    ):
                        files_info[_fileitem.path] = {
                            "size": _fileitem.size or 0,
                            "modify_time": getattr(_fileitem, "modify_time", 0),
                            "type": _fileitem.type,
                        }
            except Exception as e:
                logger.debug("Snapshot error for %s: %s", _fileitem.path, e)

        fileitem = self._clouddrive_api.get_item(path)
        if not fileitem:
            return {}
        __snapshot_file(fileitem)
        return files_info

    def storage_usage(self, storage: str) -> Optional[StorageUsage]:
        """
        获取储存空间用量（总空间、已用、可用）

        :param storage: 储存名称
        :return: StorageUsage；非本储存或未就绪时返回 None
        """
        if storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.usage()

    def support_transtype(self, storage: str) -> Optional[Dict[str, str]]:
        """
        返回该储存支持的整理方式（如移动、复制）及展示名称

        :param storage: 储存名称
        :return: 如 {"move": "移动", "copy": "复制"}；非本储存或未就绪时返回 None
        """
        if storage != self._disk_name or not self._clouddrive_api:
            return None
        return self._clouddrive_api.transtype

    def stop_service(self) -> None:
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s", e)
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.debug("【CloudDrive】关闭客户端: %s", e)
            self._client = None
        self._clouddrive_api = None
