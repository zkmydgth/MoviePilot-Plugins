from typing import Optional, List, Dict, Any

from app.helper.mediaserver import MediaServerHelper
from app.log import logger
from app.utils.http import RequestUtils


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

    def get_item_details(
        self,
        item_id: str,
        media_server: str,
        fields: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        获取媒体信息详情
        """
        if fields:
            fields_to_request = fields
        else:
            fields_to_request = "Type,ProviderIds,People,Path,OriginalTitle,DateCreated,PremiereDate,ProductionYear,ChildCount,RecursiveItemCount,Overview,CommunityRating,OfficialRating,Genres,Studios,Taglines,MediaStreams,TagItems,Tags"

        emby_server = self.mediaserver_helper.get_service(
            name=media_server, type_filter="emby"
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

        req_url = f"{emby_host}Users/{emby_user}/Items/{item_id}"
        params = {
            "api_key": emby_apikey,
            "Fields": fields_to_request,
            "PersonFields": "ImageTags,ProviderIds",
        }
        try:
            with RequestUtils().get_res(req_url, params=params) as res:
                if res:
                    return res.json()
                else:
                    logger.info(f"{self.func_name}媒体信息详情失败，无法连接 Emby")
                    return None
        except Exception as e:
            logger.error(f"{self.func_name}连接 Items 出错：{str(e)}")
            return None

    def update_item_details(
        self,
        item_id: str,
        media_server: str,
        data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        更新媒体信息详情
        """
        if not data or not media_server or not item_id:
            return None

        current_item_details = self.get_item_details(item_id, media_server)
        if not current_item_details:
            return None

        item_to_update = current_item_details.copy()
        item_to_update.update(data)

        black_list = [
            "TagItems",
            "People",
            "MediaStreams",
            "MediaSources",
            "Chapters",
            "RecursiveItemCount",
            "ChildCount",
            "ImageTags",
            "SeriesTimerId",
            "RunTimeTicks",
        ]
        for key in black_list:
            if key not in data:
                item_to_update.pop(key, None)

        emby_server = self.mediaserver_helper.get_service(
            name=media_server, type_filter="emby"
        )
        emby_apikey = emby_server.config.config.get("apikey")
        emby_host = emby_server.config.config.get("host")
        if not emby_host:
            return None
        if not emby_host.endswith("/"):
            emby_host += "/"
        if not emby_host.startswith("http"):
            emby_host = "http://" + emby_host  # noqa
        url = f"{emby_host}Items/{item_id}"
        try:
            with RequestUtils().post_res(
                url,
                params={"api_key": emby_apikey},
                json=item_to_update,
                headers={"Content-Type": "application/json"},
            ) as res:
                if res.code == 200:
                    return item_to_update
                else:
                    logger.info(f"{self.func_name}更新媒体信息详情失败，请求 Emby 出错")
                    return None
        except Exception as e:
            logger.error(f"{self.func_name}更新 Items 出错：{str(e)}")
            return None
