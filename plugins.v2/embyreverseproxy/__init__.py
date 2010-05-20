from threading import Thread
from typing import Any, Dict, List, Tuple

from uvicorn import Config, Server

from app.log import logger
from app.plugins import _PluginBase

from .proxy_app import create_app


class EmbyReverseProxy(_PluginBase):
    """
    Emby 302 反向代理
    """

    plugin_name = "Emby 302 反向代理"
    plugin_desc = (
        "Emby 302 反向代理，自动代理 HTTP 链接，跳转最终地址，简单易用无需过多配置。"
    )
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/refs/heads/main/icons/Emby_A.png"
    plugin_version = "0.0.4"
    plugin_author = "DDSRem"
    author_url = "https://github.com/DDSRem"
    plugin_config_prefix = "embyreverseproxy_"
    plugin_order = 20
    auth_level = 1

    _enabled = False
    _emby_host = ""
    _host = "0.0.0.0"
    _port = 8099
    _server = None
    _thread = None

    def init_plugin(self, config: Dict[str, Any] | None = None) -> None:
        """
        初始化插件：解析配置，启用时在独立线程启动 uvicorn，否则停止服务。

        :param config: 插件配置字典。
        """
        if config:
            self._enabled = config.get("enabled", False)
            self._emby_host = (config.get("emby_host") or "").strip()
            self._host = (config.get("host") or "0.0.0.0").strip() or "0.0.0.0"
            try:
                self._port = int(config.get("port") or 8099)
            except (TypeError, ValueError):
                self._port = 8099
            self._update_config()

        self.stop_service()

        if self._enabled and self._emby_host:
            if not self._emby_host.startswith(("http://", "https://")):
                self._emby_host = "http://" + self._emby_host
            app = create_app(self._emby_host)
            try:
                uv_config = Config(
                    app=app,
                    host=self._host,
                    port=self._port,
                    log_config=None,
                )
                self._server = Server(uv_config)
                self._thread = Thread(target=self._server.run, daemon=True)
                self._thread.start()
                logger.info(
                    "EmbyReverseProxy 代理已启动: %s:%s -> %s",
                    self._host,
                    self._port,
                    self._emby_host,
                )
            except Exception as e:
                logger.error("EmbyReverseProxy 启动失败: %s", e, exc_info=True)
                self._server = None
                self._thread = None
        elif self._enabled and not self._emby_host:
            logger.warning("EmbyReverseProxy 已启用但未配置 Emby 地址，代理未启动")

    def _update_config(self) -> None:
        """
        将当前配置写回插件配置存储。
        """
        self.update_config(
            {
                "enabled": self._enabled,
                "emby_host": self._emby_host,
                "host": self._host,
                "port": self._port,
            }
        )

    def stop_service(self) -> None:
        """
        停止代理服务：设置 server.should_exit 并等待线程结束。
        """
        if self._server is not None:
            try:
                self._server.should_exit = True
                if self._thread is not None and self._thread.is_alive():
                    self._thread.join(timeout=5.0)
                logger.info("EmbyReverseProxy 代理已停止")
            except Exception as e:
                logger.error("EmbyReverseProxy 停止异常: %s", e, exc_info=True)
            finally:
                self._server = None
                self._thread = None

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面。

        :return: (页面配置列表, 表单默认值字典)。
        """
        return [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "enabled",
                                    "label": "启用插件",
                                    "hint": "开启后将在独立端口运行 Emby 反向代理",
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
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {
                                    "model": "emby_host",
                                    "label": "Emby 服务器地址",
                                    "placeholder": "http://192.168.1.100:8096",
                                    "hint": "Emby 服务器根地址，必填",
                                    "persistent-hint": True,
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {
                                    "model": "host",
                                    "label": "监听地址",
                                    "placeholder": "0.0.0.0",
                                    "hint": "代理监听地址",
                                    "persistent-hint": True,
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {
                                    "model": "port",
                                    "label": "监听端口",
                                    "placeholder": "8099",
                                    "hint": "代理监听端口",
                                    "persistent-hint": True,
                                },
                            }
                        ],
                    },
                ],
            },
        ], {
            "enabled": False,
            "emby_host": "",
            "host": "0.0.0.0",
            "port": 8099,
        }
