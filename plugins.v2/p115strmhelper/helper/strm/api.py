from pathlib import Path
from time import sleep
from typing import Tuple, List

from p115client import P115Client
from p115client.tool import iter_files_with_path_skim
from p115pickcode import to_id, to_pickcode

from ...core.u115_open import U115OpenHelper
from ...core.p115 import get_pid_by_path
from ...core.config import configer
from ...core.scrape import media_scrape_metadata
from ...helper.mediaserver import MediaServerRefresh
from ...schemas.strm_api import (
    StrmApiPayloadData,
    StrmApiPayloadByPathData,
    StrmApiResponseData,
    StrmApiResponseFail,
    StrmApiData,
    StrmApiStatusCode,
)
from ...utils.strm import StrmUrlGetter, StrmGenerater
from ...utils.path import PathUtils
from ...utils.sentry import sentry_manager

from app.log import logger


class ApiSyncStrmHelper:
    """
    API 调用生成 STRM
    """

    cooldown: int | float = 1

    def __init__(self, client: P115Client):
        self.client = client
        self.open_client = U115OpenHelper()

        self.strm_url_getter = StrmUrlGetter()

        self.rmt_mediaext = {
            f".{ext.strip()}"
            for ext in configer.user_rmt_mediaext.replace("，", ",").split(",")
        }

    def generate_strm_files(
        self, payload: StrmApiPayloadData
    ) -> Tuple[int, str, StrmApiResponseData]:
        """
        生成 STRM 文件
        """
        if not payload.data:
            return StrmApiStatusCode.MissPayload, "未传有效参数", StrmApiResponseData()

        success_strm_count = 0
        fail_strm_count = 0

        success_data: List[StrmApiData] = []
        fail_data: List[StrmApiResponseFail] = []

        for item in payload.data:
            if not item.pick_code and not item.id and not item.pan_path:
                fail_data.append(
                    StrmApiResponseFail(
                        **item.model_dump(),
                        code=StrmApiStatusCode.MissPcOrId,
                        reason="缺失必要参数，pick_code，id 或 pan_path 参数",
                    )
                )
                fail_strm_count += 1
                continue

            file_id = None
            pick_code = None
            if item.id or item.pick_code:
                file_id = item.id if item.id else to_id(item.pick_code)
                pick_code = item.pick_code if item.pick_code else to_pickcode(item.id)
            name = item.name
            pan_path = item.pan_path
            sha1 = item.sha1
            size = item.size
            local_path = item.local_path
            pan_media_path = item.pan_media_path

            if not pan_path or not sha1 or not size or not file_id:
                try:
                    if file_id:
                        file_info = self.open_client.get_item_info(file_id)
                    else:
                        file_info = self.open_client.get_item_info(pan_path)
                    if not file_info:
                        fail_data.append(
                            StrmApiResponseFail(
                                **item.model_dump(),
                                code=StrmApiStatusCode.GetPanMediaPathError,
                                reason="无法获取文件信息",
                            )
                        )
                        fail_strm_count += 1
                        continue
                    file_id = file_info.get("file_id")
                    pick_code = file_info.get("pick_code")
                    pan_path = file_info.get("path")
                    sha1 = file_info.get("sha1")
                    size = file_info.get("size_byte")
                except Exception as e:
                    logger.error(f"【API_STRM生成】获取文件信息失败: {e}")
                    fail_data.append(
                        StrmApiResponseFail(
                            **item.model_dump(),
                            code=StrmApiStatusCode.GetPanMediaPathError,
                            reason=f"获取文件信息失败: {e}",
                        )
                    )
                    fail_strm_count += 1
                    continue

            if not pan_path:
                fail_data.append(
                    StrmApiResponseFail(
                        **item.model_dump(),
                        code=StrmApiStatusCode.GetPanMediaPathError,
                        reason="无法获取文件路径",
                    )
                )
                fail_strm_count += 1
                continue

            if not name:
                name = Path(pan_path).name

            if not local_path:
                for paths in configer.api_strm_config:
                    if PathUtils.has_prefix(pan_path, paths.pan_path):
                        local_path = paths.local_path
                        pan_media_path = paths.pan_path
                        break
                if not local_path:
                    fail_data.append(
                        StrmApiResponseFail(
                            **item.model_dump(),
                            code=StrmApiStatusCode.GetLocalPathError,
                            reason="无法获取本地生成 STRM 路径",
                        )
                    )
                    fail_strm_count += 1
                    continue

            if not pan_media_path:
                for paths in configer.api_strm_config:
                    if local_path == paths.local_path:
                        pan_media_path = paths.pan_path
                        break
                if not pan_media_path:
                    fail_data.append(
                        StrmApiResponseFail(
                            **item.model_dump(),
                            code=StrmApiStatusCode.GetPanMediaPathError,
                            reason="无法获取网盘媒体库路径",
                        )
                    )
                    fail_strm_count += 1
                    continue

            if item.scrape_metadata is None:
                scrape_metadata = configer.api_strm_scrape_metadata_enabled
            else:
                scrape_metadata = item.scrape_metadata

            if item.media_server_refresh is None:
                media_server_refresh = configer.api_strm_media_server_refresh_enabled
            else:
                media_server_refresh = item.media_server_refresh

            local_path_obj = Path(local_path)
            pan_path_obj = Path(pan_path)
            try:
                file_path = local_path_obj / pan_path_obj.relative_to(pan_media_path)
            except ValueError as e:
                logger.error(
                    f"【API_STRM生成】路径计算失败: pan_path={pan_path}, pan_media_path={pan_media_path}, {e}"
                )
                fail_data.append(
                    StrmApiResponseFail(
                        **item.model_dump(),
                        code=StrmApiStatusCode.GetPanMediaPathError,
                        reason="路径计算失败: pan_media_path 不是 pan_path 的前缀",
                    )
                )
                fail_strm_count += 1
                continue
            new_file_path = file_path.parent / StrmGenerater.get_strm_filename(
                file_path
            )

            if pan_path_obj.suffix.lower() not in self.rmt_mediaext:
                fail_data.append(
                    StrmApiResponseFail(
                        **item.model_dump(),
                        code=StrmApiStatusCode.NotRmtMediaExt,
                        reason="文件扩展名不属于可整理媒体文件扩展名",
                    )
                )
                fail_strm_count += 1
                continue

            strm_api_data = StrmApiData(
                name=name,
                sha1=sha1,
                size=size,
                pick_code=pick_code,
                pan_path=pan_path,
                id=file_id,
                local_path=local_path,
                pan_media_path=pan_media_path,
                scrape_metadata=scrape_metadata,
                media_server_refresh=media_server_refresh,
            )

            strm_url = self.strm_url_getter.get_strm_url(pick_code, name, pan_path)
            try:
                new_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(new_file_path, "w", encoding="utf-8") as file:
                    file.write(strm_url)
            except Exception as e:
                sentry_manager.sentry_hub.capture_exception(e)
                fail_data.append(
                    StrmApiResponseFail(
                        **strm_api_data.model_dump(),
                        code=StrmApiStatusCode.CreateStrmError,
                        reason=f"STRM 文件生成失败: {e}",
                    )
                )
                fail_strm_count += 1
                logger.error(
                    f"【API_STRM生成】{new_file_path.as_posix()} 文件生成失败: {e}"
                )
                continue

            logger.info(f"【API_STRM生成】{new_file_path.as_posix()} 文件生成成功")
            success_data.append(strm_api_data)
            success_strm_count += 1

            if scrape_metadata:
                logger.info(f"【API_STRM生成】{new_file_path.as_posix()} 开始刮削...")
                media_scrape_metadata(new_file_path.as_posix())

            if media_server_refresh:
                media_refresh_helper = MediaServerRefresh(
                    func_name="【API_STRM生成】",
                    enabled=media_server_refresh,
                    mp_mediaserver=configer.api_strm_mp_mediaserver_paths,
                    mediaservers=configer.api_strm_mediaservers,
                )
                media_refresh_helper.refresh_mediaserver(
                    file_path=new_file_path.as_posix(), file_name=new_file_path.name
                )

            sleep(self.cooldown)

        return (
            StrmApiStatusCode.Success,
            "生成完成",
            StrmApiResponseData(
                success=success_data,
                fail=fail_data,
                success_count=success_strm_count,
                fail_count=fail_strm_count,
            ),
        )

    def generate_strm_paths(
        self, payload: StrmApiPayloadByPathData
    ) -> Tuple[int, str, StrmApiResponseData]:
        """
        根据文件夹列表生成 STRM 文件
        """
        if not payload.data:
            return StrmApiStatusCode.MissPayload, "未传有效参数", StrmApiResponseData()

        file_list: List[StrmApiData] = []

        for path in payload.data:
            try:
                parent_id = get_pid_by_path(self.client, path, True, False, False)
                logger.info(
                    f"【API_STRM生成】网盘媒体目录 ID 获取成功: {path} {parent_id}"
                )
            except Exception as e:
                sentry_manager.sentry_hub.capture_exception(e)
                logger.error(f"【API_STRM生成】网盘媒体目录 ID 获取失败: {path} {e}")
                continue

            for item in iter_files_with_path_skim(
                self.client,
                cid=parent_id,
                with_ancestors=True,
            ):
                try:
                    file_list.append(
                        StrmApiData(
                            id=item["id"],
                            pick_code=item["pickcode"],
                            name=item["name"],
                            sha1=item["sha1"],
                            size=item["size"],
                            pan_path=item["path"],
                            media_server_refresh=payload.media_server_refresh,
                            scrape_metadata=payload.scrape_metadata,
                        )
                    )
                except ValueError:
                    logger.error(f"【API_STRM生成】文件信息缺失: {item}")
                    continue

        logger.info(
            f"【API_STRM生成】提取 {len(file_list)} 个文件数据，开始生成 STRM ..."
        )

        return self.generate_strm_files(
            StrmApiPayloadData(
                data=file_list,
            )
        )
