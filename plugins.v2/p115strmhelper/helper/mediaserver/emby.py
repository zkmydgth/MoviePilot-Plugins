from pathlib import Path
from typing import Optional, List, Dict
from urllib.parse import quote

from httpx import RequestError, post as httpx_post
from p115center import P115Center

from app.helper.mediaserver import MediaServerHelper
from app.log import logger
from app.schemas import ServiceInfo
from app.utils.http import RequestUtils

from ...utils.path import PathUtils


class EmbyOperate:
    """
    Emby 媒体服务器操作类
    """

    def __init__(self, func_name: str, media_servers: Optional[List[str]] = None):
        self.func_name = func_name
        self.media_servers = media_servers
        self.mediaserver_helper = MediaServerHelper()

    def get_series_tmdb_id(self, series_id: str) -> Optional[str]:
        """
        获取剧集 TMDB ID

        :param series_id: 剧集ID

        :return: TMDB ID
        """
        if not self.media_servers:
            return None

        emby_server = self.mediaserver_helper.get_service(
            name=self.media_servers[0], type_filter="emby"
        )
        emby_user = emby_server.instance.get_user()
        emby_apikey = emby_server.config.config.get("apikey")
        emby_host = emby_server.config.config.get("host")
        if not emby_host:
            return None
        if not emby_host.endswith("/"):
            emby_host += "/"
        if not emby_host.startswith("http"):
            emby_host = "http://" + emby_host  # noqa

        req_url = (
            f"{emby_host}emby/Users/{emby_user}/Items/{series_id}?api_key={emby_apikey}"
        )
        try:
            with RequestUtils().get_res(req_url) as res:
                if res:
                    return res.json().get("ProviderIds", {}).get("Tmdb")
                else:
                    logger.info(f"{self.func_name}获取剧集 TMDB ID 失败，无法连接 Emby")
                    return None
        except Exception as e:
            logger.error(f"{self.func_name}连接 Items 出错：{str(e)}")
            return None


class EmbyMediaInfoOperate:
    """
    Emby 媒体信息操作类
    """

    def __init__(
        self,
        func_name: str,
        mediaservers: Optional[List[str]] = None,
        mp_mediaserver: Optional[str] = None,
    ):
        self.func_name = func_name
        self.media_servers = mediaservers
        self.mp_mediaserver = mp_mediaserver
        self.center = P115Center(
            license="fa1a823e9d29cfd1e15eaaab19daaede653356b770d7dcdcdbe05b793b5cb5a8",
            file_path=str(Path(__file__).resolve().parent.parent.parent / "api.py"),
        )

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        媒体服务器服务信息

        :return: 媒体服务器服务信息
        """
        if not self.media_servers:
            logger.warning(f"{self.func_name}尚未配置媒体服务器，请检查配置")
            return None

        mediaserver_helper = MediaServerHelper()

        services = mediaserver_helper.get_services(name_filters=self.media_servers)
        if not services:
            logger.warning(f"{self.func_name}获取媒体服务器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(
                    f"{self.func_name}媒体服务器 {service_name} 未连接，请检查配置"
                )
            else:
                if service_info.type == "emby":
                    active_services[service_name] = service_info

        if not active_services:
            logger.warning(f"{self.func_name}没有已连接的 Emby 媒体服务器，请检查配置")
            return None

        return active_services

    def get_mediainfo(self, sha1: str, path: Path):
        """
        执行提取媒体信息，并上传服务器

        :param sha1: 媒体文件的 sha1 值
        :param path: 媒体路径
        """
        media_server = self.service_infos
        if not media_server:
            return

        media_data = None
        try:
            resp = self.center.download_emby_mediainfo_data([sha1])
            media_data = resp.get(sha1, None)
        except Exception as e:
            logger.error(f"{self.func_name}{path} 获取媒体信息失败: {e}")

        need_upload = True
        if media_data:
            need_upload = False

        file_path = path.as_posix()
        if self.mp_mediaserver:
            status, mediaserver_path, moviepilot_path = PathUtils.get_media_path(
                self.mp_mediaserver,
                path.as_posix(),
            )
            if not status:
                logger.error(
                    f"{self.func_name}{path} 无法确定媒体库路径，无法媒体信息提取"
                )
                return
            logger.info(f"{self.func_name}{path.name} 提取媒体信息目录替换中...")
            file_path = path.as_posix().replace(moviepilot_path, mediaserver_path)
            logger.info(
                f"{self.func_name}提取媒体信息目录替换: {moviepilot_path} --> {mediaserver_path}"
            )
            logger.info(f"{self.func_name}提取媒体信息目录: {file_path}")

        for service_name, service_info in media_server.items():
            host = service_info.config.config.get("host")
            api_key = service_info.config.config.get("apikey")
            if not host:
                continue
            if not host.endswith("/"):
                host += "/"
            if not host.startswith("http"):
                host = "http://" + host  # noqa
            path_encoded = quote(file_path, safe="")
            url = (
                f"{host}emby/Items/SyncMediaInfo?Path={path_encoded}&api_key={api_key}"
            )
            try:
                res = httpx_post(
                    url,
                    json=media_data,
                    headers={"Content-Type": "application/json"},
                )
                if (
                    res.status_code == 200
                    and res.json()
                    and res.json().get("Chapters", None)
                    and res.json().get("MediaSourceInfo", None)
                ):
                    logger.info(
                        f"{self.func_name}{service_name} 更新媒体信息成功: {file_path}"
                    )
                    if need_upload:
                        media_data = res.json()
                else:
                    logger.warning(
                        f"{self.func_name}{service_name} 更新媒体信息失败: {res.status_code} {res.json()}"
                    )
            except RequestError as e:
                logger.error(f"{self.func_name}{service_name} 更新媒体信息失败: {e}")

        if need_upload and media_data:
            try:
                self.center.upload_emby_mediainfo_data(sha1, media_data)
            except Exception as e:
                logger.warn(f"{self.func_name}上传媒体信息失败: {sha1} {file_path} {e}")
