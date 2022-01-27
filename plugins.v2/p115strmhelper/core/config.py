from typing import Dict, Any, Optional, List
import json

from pydantic import BaseModel, ValidationError

from app.log import logger


class BaseConfig(BaseModel):
    """
    基础配置
    """

    class Config:
        extra = "ignore"

    # 插件总开关
    enabled: bool = False
    # 通知开关
    notify: bool = False
    # 生成 STRM URL 格式
    strm_url_format: str = "pickcode"
    # 302 跳转方式
    link_redirect_mode: str = "cookie"
    # 115 Cookie
    cookies: Optional[str] = None
    # 115 安全码
    password: Optional[str] = None
    # MoviePilot 地址
    moviepilot_address: Optional[str] = None
    # 可识别媒体后缀
    user_rmt_mediaext: str = (
        "mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v"
    )
    # 可识别下载后缀
    user_download_mediaext: str = "srt,ssa,ass"

    # 整理事件监控开关
    transfer_monitor_enabled: bool = False
    # 刮削 STRM 开关
    transfer_monitor_scrape_metadata_enabled: bool = False
    # 刮削排除目录
    transfer_monitor_scrape_metadata_exclude_paths: Optional[str] = None
    # 监控目录
    transfer_monitor_paths: Optional[str] = None
    # MP-媒体库 目录转换
    transfer_mp_mediaserver_paths: Optional[str] = None
    # 刷新媒体服务器
    transfer_monitor_mediaservers: Optional[List[str]] = None
    # 刷新媒体服务器开关
    transfer_monitor_media_server_refresh_enabled: bool = False

    # 全量同步覆盖模式
    full_sync_overwrite_mode: str = "never"
    # 清理无效 STRM 文件
    full_sync_remove_unless_strm: bool = False
    # 定期全量同步开关
    timing_full_sync_strm: bool = False
    # 下载媒体信息文件开关
    full_sync_auto_download_mediainfo_enabled: bool = False
    # 定期全量同步周期
    cron_full_sync_strm: str = "0 */7 * * *"
    # 全量同步路径
    full_sync_strm_paths: Optional[str] = None

    increment_sync_strm_enabled: bool = False
    increment_sync_auto_download_mediainfo_enabled: bool = False
    increment_sync_cron: str = "0 * * * *"
    increment_sync_strm_paths: Optional[str] = None
    increment_sync_mp_mediaserver_paths: Optional[str] = None
    increment_sync_scrape_metadata_enabled: bool = False
    increment_sync_scrape_metadata_exclude_paths: Optional[str] = None
    increment_sync_media_server_refresh_enabled: bool = False
    increment_sync_mediaservers: Optional[List[str]] = None

    monitor_life_enabled: bool = False
    monitor_life_auto_download_mediainfo_enabled: bool = False
    monitor_life_paths: Optional[str] = None
    monitor_life_mp_mediaserver_paths: Optional[str] = None
    monitor_life_media_server_refresh_enabled: bool = False
    monitor_life_mediaservers: Optional[List[str]] = None
    monitor_life_event_modes: Optional[List[str]] = None
    monitor_life_scrape_metadata_enabled: bool = False
    monitor_life_scrape_metadata_exclude_paths: Optional[str] = None

    share_strm_auto_download_mediainfo_enabled: bool = False
    user_share_code: Optional[str] = None
    user_receive_code: Optional[str] = None
    user_share_link: Optional[str] = None
    user_share_pan_path: Optional[str] = None
    user_share_local_path: Optional[str] = None

    clear_recyclebin_enabled: bool = False
    clear_receive_path_enabled: bool = False
    cron_clear: str = "0 */7 * * *"

    pan_transfer_enabled: bool = False
    pan_transfer_paths: Optional[str] = None

    directory_upload_enabled: bool = False
    directory_upload_mode: str = "compatibility"
    directory_upload_uploadext: str = (
        "mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v"
    )
    directory_upload_copyext: str = "srt,ssa,ass"
    directory_upload_path: Optional[List[Dict]] = None


class ConfigManager:
    """
    配置操作器
    """

    def __init__(self):
        self._configs = {}

    def fix_bool_config(self, config_dict: Dict[str, Any]) -> Dict:
        """
        修复非法的布尔值
        """
        fixed_dict = config_dict
        for field_name, field in BaseConfig.__fields__.items():
            if field.type_ is bool and field_name in fixed_dict:
                value = fixed_dict[field_name]
                if not isinstance(value, bool):
                    default_value = field.default
                    logger.warning(
                        f"【配置管理器】配置项 {field_name} 的值 {value} 不是布尔类型，已替换为默认值 {default_value}"
                    )
                    fixed_dict[field_name] = default_value
        return fixed_dict

    def load_from_dict(self, config_dict: Dict[str, Any]) -> bool:
        """
        从字典加载配置
        """
        try:
            fixed_dict = self.fix_bool_config(config_dict.copy())
            validated = BaseConfig(**fixed_dict)
            self._configs = validated.dict()
            return True
        except ValidationError as e:
            logger.error(f"【配置管理器】配置验证失败: {e}")
            return False

    def load_from_json(self, json_str: str) -> bool:
        """
        从JSON字符串加载配置
        """
        try:
            return self.load_from_dict(json.loads(json_str))
        except json.JSONDecodeError:
            logger.error("【配置管理器】无效的JSON格式")
            return False

    def get_config(self, key: str) -> Optional[Any]:
        """
        获取单个配置值
        """
        return self._configs.get(key)

    def get_all_configs(self) -> Dict[str, Any]:
        """
        获取所有配置的副本
        """
        self._configs = self.fix_bool_config(self._configs)
        return self._configs.copy()

    def update_config(self, updates: Dict[str, Any]) -> bool:
        """
        更新部分配置
        """
        try:
            # 合并现有配置和更新
            self._configs = self.fix_bool_config(self._configs)
            current = BaseConfig(**self._configs)
            updated = current.copy(update=updates)
            self._configs.update(updated.dict())
            return True
        except ValidationError as e:
            logger.error(f"【配置管理器】配置更新失败: {e.json()}")
            return False


configer = ConfigManager()
