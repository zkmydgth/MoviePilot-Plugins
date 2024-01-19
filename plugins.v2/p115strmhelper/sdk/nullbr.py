from nullbr import NullbrSDK

from ..core.config import configer


class NullbrHelper:
    """
    Nullbr 资源搜索
    """

    def __init__(self):
        self.client = NullbrSDK(
            app_id=configer.get_config("nullbr_app_id"),
            api_key=configer.get_config("nullbr_api_key"),
        )

    def get_media_list(self, name: str):
        """
        获取信息列表
        """
        search_results = self.client.search(name, page=1)
        return search_results.items

    def search_resource(self, tmdb_id: int, media_type: str):
        """
        搜索资源
        """
        if media_type == "movie":
            result = self.client.get_movie(tmdb_id)
            if result.has_115:
                result = self.client.get_movie_115(tmdb_id)
                return [
                    {"taskname": "【Nullbr】" + item.title, "shareurl": item.share_link}
                    for item in result.items
                ]
        elif media_type == "tv":
            result = self.client.get_tv(tmdb_id)
            if result.has_115:
                result = self.client.get_tv_115(tmdb_id)
                return [
                    {"taskname": "【Nullbr】" + item.title, "shareurl": item.share_link}
                    for item in result.items
                ]
        elif media_type == "collection":
            result = self.client.get_collection(tmdb_id)
            if result.has_115:
                result = self.client.get_collection_115(tmdb_id)
                return [
                    {"taskname": "【Nullbr】" + item.title, "shareurl": item.share_link}
                    for item in result.items
                ]
        return []
