from functools import wraps
from inspect import Parameter, signature
from pathlib import Path
from threading import Lock
from typing import Callable, Optional, Tuple, TYPE_CHECKING

from app.sdk.logging import logger

from ..utils.patch_guard import PatchTargetError, check_patch_target
from ..utils.transfer_compat import (
    JobViewAdapter,
    abandon_taken_over_admission,
    checkpoint_planning_rejection,
    latest_transfer_history,
    record_uncheckpointed_failure,
    request_durable_transfer_retry,
    resolve_jobview,
    resolve_scrape_batch_finish,
    scrape_batch_finish_names,
)

if TYPE_CHECKING:
    from ..helper.transfer import TransferTaskManager, TransferHandler


# 补丁目标在宿主上的归属模块；TransferChain 由多个 owner 混入组合而成，
# 方法物理上分散在 execution/plan/scrape 等文件，这里逐一登记以便自检。
_TRANSFER_MODULE_HINT = "app.chain.transfer"

# 宿主 __handle_transfer 的期望形参（不含 self）
_HANDLE_TRANSFER_PARAMS = ["task", "callback"]

# 宿主 durable 收口入口（补丁复制旧流程时最容易漏掉的契约）。
# 这两处是宿主 V3「失败/提前返回必须结算」的唯一路径，缺失时补丁必须回退原生整理，
# 否则任务会停在 accepted 状态被恢复调度每 ~15 秒回放一次。
_DURABLE_SETTLEMENT_TARGETS = (
    "_TransferChain__checkpoint_planning_rejection",
    "_TransferChain__record_uncheckpointed_failure",
)
_SETTLEMENT_PARAMS = ["task", "error"]


class TransferChainPatcher:
    """
    TransferChain 补丁管理器
    """

    _original_handle_transfer = None
    _enabled = False
    # 宿主缺少刮削批次收尾方法时只告警一次，避免每个任务刷屏
    _scrape_finish_warned = False
    # jobview 接口是否已在真实实例上复核过（只做一次）
    _jobview_verified = False

    @classmethod
    def is_enabled(cls) -> bool:
        """
        补丁是否真正生效。

        仅当目标校验通过且宿主方法已成功替换时才为 True；校验失败会保持 False，
        调用方据此判断"接管"是真启用还是已回退原生整理，不再打印假成功日志。

        :return bool: 补丁是否生效
        """
        return bool(cls._enabled)

    @classmethod
    def _warn_scrape_batch_finish_missing(cls) -> None:
        """刮削批次收尾方法不可用时只告警一次。"""
        if cls._scrape_finish_warned:
            return
        cls._scrape_finish_warned = True
        logger.warning(
            "【整理接管】宿主未提供刮削批次收尾方法"
            f"（候选名：{scrape_batch_finish_names()}），本批次跳过 pending 清理"
        )

    @classmethod
    def _verify_jobview_once(cls, chain_self) -> None:
        """
        运行期首单复核 jobview 接口（用真实实例，避免补丁前构造宿主单例的副作用）。

        复核只做一次；接口不完整时记一条 error 后继续——作业队列操作由
        ``JobViewAdapter`` 逐方法降级，不因此中断整理主流程。
        """
        if cls._jobview_verified:
            return
        cls._jobview_verified = True
        if not cls._jobview_of(chain_self).verify():
            logger.error(
                "【整理接管】宿主 jobview 接口不完整，作业队列状态同步将走降级路径"
                "（整理主流程继续）"
            )

    _task_manager: Optional["TransferTaskManager"] = None
    _handler: Optional["TransferHandler"] = None
    _storage_module: str = ""
    _jobview_adapters: dict = {}
    _lock = Lock()

    @classmethod
    def _jobview_of(cls, chain_self) -> JobViewAdapter:
        """
        取得宿主链对象的 JobView 适配器（按对象缓存）。

        :param chain_self: 宿主 TransferChain 实例
        :return: JobViewAdapter
        """
        key = id(chain_self)
        adapter = cls._jobview_adapters.get(key)
        if adapter is None:
            adapter = JobViewAdapter(
                getattr(chain_self, "jobview", None), owner="TransferChain"
            )
            cls._jobview_adapters[key] = adapter
        return adapter

    @classmethod
    def _verify_targets(cls, transfer_chain) -> None:
        """
        校验补丁依赖的全部宿主方法。

        宿主把 ``__handle_transfer`` 拆分到 owner 后，私有方法名（``_TransferChain__xxx``）
        由名字改写决定，跨版本最易漂移；这里在打补丁前一次性校验，
        避免补丁套上去后调用链在运行期才炸。
        """
        check_patch_target(
            transfer_chain,
            "_TransferChain__handle_transfer",
            expected_params=_HANDLE_TRANSFER_PARAMS,
            module_hint=_TRANSFER_MODULE_HINT,
        )
        # durable 收口依赖：未识别、缺集数、重复投递等提前返回路径必须按宿主语义结算，
        # 否则宿主恢复调度每 ~15 秒回放同一文件（2026-10-09 实测）。缺失即整体放弃补丁，
        # 而不是打上补丁后"接管却永不结算"。
        for name in _DURABLE_SETTLEMENT_TARGETS:
            check_patch_target(
                transfer_chain,
                name,
                expected_params=_SETTLEMENT_PARAMS,
                module_hint=_TRANSFER_MODULE_HINT,
            )
        # finally 分支依赖：每次处理完清理批次 pending 集合。
        # 该方法的宿主名跨版本漂移过（V2 私有名 → V3 单下划线公开名），
        # 按候选名解析，解析不到才报错，避免补丁被"目标缺失"整体放弃。
        if resolve_scrape_batch_finish(transfer_chain) is None:
            raise PatchTargetError(
                "补丁目标缺失：TransferChain 上找不到刮削批次收尾方法"
                f"（候选名：{scrape_batch_finish_names()}）。"
                "宿主可能已重命名或移除该方法，请检查补丁适配。"
            )

        # jobview 是宿主作业队列，补丁大量读写其状态。
        # 宿主把它挂在实例上（类上取不到），故按实例视角解析；这里**不主动构造**宿主链
        # （构造失败会被宿主 metaclass 缓存成半成品单例），查不到已存在实例时跳过校验，
        # 改由运行期首单用真实实例复核（_verify_jobview_once）。
        jobview, probe_ok = resolve_jobview(transfer_chain)
        if jobview is None and probe_ok:
            raise PatchTargetError(
                "补丁目标缺失：TransferChain 上取不到 jobview（宿主作业队列）。"
                "宿主可能已把作业队列移出整理链，请检查补丁适配。"
            )
        if jobview is None:
            logger.info(
                "【整理接管】宿主链实例尚未创建，补丁前跳过 jobview 接口校验"
                "（运行期首单会按真实实例复核）"
            )
        else:
            for name in (
                "add_task",
                "migrate_task",
                "running_task",
                "finish_task",
                "remove_task",
                "remove_job",
                "try_remove_job",
                "is_done",
            ):
                check_patch_target(jobview, name)

        # 回退到原方法时需要，缺失会导致"非 115→115"路径整体失效
        for name in ("transfer",):
            check_patch_target(transfer_chain, name)

    @classmethod
    def enable(
        cls,
        task_manager: "TransferTaskManager",
        handler: "TransferHandler",
        storage_module: str,
    ):
        """
        启用补丁

        :param task_manager: TransferTaskManager 实例
        :param handler: TransferHandler 实例
        :param storage_module: 存储模块名称
        """
        with cls._lock:
            if cls._enabled:
                logger.debug("【整理接管】补丁已启用，跳过")
                return

            try:
                from app.chain.transfer import TransferChain

                cls._verify_targets(TransferChain)

                cls._task_manager = task_manager
                cls._handler = handler
                cls._storage_module = storage_module

                # 保存原方法
                cls._original_handle_transfer = (
                    TransferChain._TransferChain__handle_transfer
                )

                # 创建 patched 方法
                @wraps(cls._original_handle_transfer)
                def patched_handle_transfer(
                    self, task, callback: Optional[Callable] = None
                ) -> Optional[Tuple[bool, str]]:
                    """
                    补丁版 TransferChain 整理方法，拦截 115 → 115 的整理任务并委托给插件处理

                    :param self: TransferChain 实例
                    :param task: MoviePilot TransferTask
                    :param callback: 可选的完成回调
                    :return: (成功状态, 消息) 或 None
                    """
                    return cls._patched_handle_transfer(self, task, callback)

                # 应用补丁
                TransferChain._TransferChain__handle_transfer = patched_handle_transfer
                cls._scrape_finish_warned = False
                cls._jobview_verified = False
                cls._enabled = True
                logger.info("【整理接管】TransferChain 补丁已启用")

            except PatchTargetError as e:
                logger.error(
                    f"【整理接管】补丁目标校验失败，已放弃接管整理（插件将回退为"
                    f"不拦截 115→115 任务）: {e}"
                )
            except Exception as e:
                logger.error(f"【整理接管】启用补丁失败: {e}", exc_info=True)

    @classmethod
    def disable(cls):
        """
        禁用补丁
        """
        with cls._lock:
            if not cls._enabled:
                return

            try:
                from app.chain.transfer import TransferChain

                # 恢复原方法
                if cls._original_handle_transfer:
                    TransferChain._TransferChain__handle_transfer = (
                        cls._original_handle_transfer
                    )
                    cls._original_handle_transfer = None

                cls._task_manager = None
                cls._handler = None
                cls._storage_module = ""
                cls._enabled = False
                logger.info("【整理接管】TransferChain 补丁已禁用")

            except Exception as e:
                logger.error(f"【整理接管】禁用补丁失败: {e}", exc_info=True)

    @classmethod
    def _patched_handle_transfer(
        cls, chain_self, task, callback: Optional[Callable] = None
    ) -> Optional[Tuple[bool, str]]:
        """
        Patched 版本的 __handle_transfer
        """
        from app.application.directory import DirectoryHelper
        from app.chain.media import MediaChain
        from app.chain.tmdb import TmdbChain
        from app.sdk.config import settings
        from app.sdk.media import MediaInfo
        from app.db.oper.transferhistory import TransferHistoryOper
        from app.schemas.types import MediaSource, MediaType

        from ..schemas.transfer import TransferTask as PluginTransferTask

        # 运行期首单复核作业队列接口（补丁前可能拿不到宿主实例）
        cls._verify_jobview_once(chain_self)

        try:
            # 宿主已提交冻结计划的恢复任务（含「settling 中重放」）必须交回宿主原生
            # 执行：补丁若按当前配置重新识别、重新算目标目录，会与已冻结的 durable
            # 计划漂移，且宿主终态判定（terminal = plan_checkpoint is not None）依赖
            # 它，重新规划等于丢掉唯一可结算的快照。
            if getattr(task, "plan_checkpoint", None) is not None:
                logger.debug(
                    f"【整理接管】{task.fileitem.name} 已存在冻结整理计划，交回宿主原生执行"
                )
                return cls._call_original(chain_self, task, callback)

            ########## 原始方法执行部分 ##########

            transferhis = TransferHistoryOper()
            mediainfo = task.mediainfo
            mediainfo_changed = False
            need_obtain_images = False
            if not mediainfo:
                download_history = task.download_history
                # 下载用户
                if download_history:
                    task.username = download_history.username
                    # 识别媒体信息
                    history_year_conflict = cls._is_movie_year_conflict(
                        task.meta, download_history
                    )
                    if (
                        download_history.media_source and download_history.media_id
                    ) and not history_year_conflict:
                        # 下载记录中已存在识别信息
                        mediainfo: Optional[MediaInfo] = chain_self.recognize_media(
                            mtype=MediaType(download_history.type),
                            media_source=download_history.media_source,
                            media_id=download_history.media_id,
                            episode_group=download_history.episode_group,
                        )
                        need_obtain_images = True
                        if mediainfo:
                            # 更新自定义媒体类别
                            if download_history.media_category:
                                mediainfo.category = download_history.media_category
                    else:
                        if history_year_conflict:
                            logger.info(
                                f"【整理接管】{task.fileitem.name} 文件年份 "
                                f"{task.meta.year} 与下载记录年份 "
                                f"{download_history.year} 不一致，按文件名重新识别"
                            )
                        mediainfo = MediaChain().recognize_by_meta(
                            task.meta, obtain_images=True
                        )
                        if mediainfo and download_history.media_category:
                            mediainfo.category = download_history.media_category
                else:
                    # 识别媒体信息（obtain_images=True 内部已完成图片获取）
                    mediainfo = MediaChain().recognize_by_meta(
                        task.meta, obtain_images=True
                    )

                # 按名称识别时已在识别链路补图，这里只补齐显式ID识别的场景
                if mediainfo and need_obtain_images:
                    chain_self.obtain_images(mediainfo=mediainfo)

                if not mediainfo:
                    # preview 模式下不创建历史记录
                    if task.preview:
                        return False, "未识别到媒体信息"
                    # 未识别属于「确定性规划拒绝」：必须按宿主语义把拒绝冻结成零文件
                    # 副作用的 durable 计划并交回 callback 结算，否则宿主会认为任务
                    # 未结算，每 ~15 秒把同一文件重投一次（2026-10-09 实测：同一未
                    # 识别文件被反复回放，每轮新增/更新一条失败历史）。
                    settled = cls._settle_planning_rejection(
                        chain_self, task, callback, "未识别到媒体信息"
                    )
                    if settled is None:
                        # 宿主缺少收口入口：交回原生流程，绝不自行返回失败，
                        # 否则任务悬空被恢复调度反复回放。
                        return cls._call_original(chain_self, task, callback)
                    return settled

                mediainfo_changed = True

            # 如果未开启新增已入库媒体是否跟随TMDB信息变化则根据媒体身份查询之前的title
            if not settings.SCRAP_FOLLOW_TMDB:
                transfer_history = transferhis.get_by_media_identity(
                    media_source=MediaSource.TMDB,
                    media_id=str(mediainfo.tmdb_id),
                    mtype=mediainfo.type.value,
                )
                if transfer_history and mediainfo.title != transfer_history.title:
                    mediainfo.title = transfer_history.title
                    mediainfo_changed = True

            if mediainfo_changed:
                # 更新任务信息
                task.mediainfo = mediainfo
                # 更新队列任务。宿主语义：migrate_task 返回 False 表示该任务已存在于
                # 整理队列，属于重复投递，应中止本次处理而不是继续加入批量队列，
                # 否则同一文件会被插件重复整理。
                if not cls._jobview_of(chain_self).migrate_task(task):
                    logger.info(
                        f"【整理接管】{task.fileitem.name} 已存在整理任务，跳过重复处理"
                    )
                    # 宿主语义：重复投递属于 checkpoint 之前的失败，要登记失败原因
                    # （保留 accepted 任务供后续重新规划），否则恢复调度只看到
                    # 「无租约的 accepted」并反复回放，却永远读不到原因。
                    record_uncheckpointed_failure(
                        chain_self, task, f"{task.fileitem.name} 已在整理队列中"
                    )
                    return False, f"{task.fileitem.name} 已在整理队列中"

            # 获取集数据
            if task.mediainfo.type == MediaType.TV and not task.episodes_info:
                # 判断注意season为0的情况
                season_num = task.mediainfo.season
                if season_num is None and task.meta.season_seq:
                    if task.meta.season_seq.isdigit():
                        season_num = int(task.meta.season_seq)
                # 默认值1
                if season_num is None:
                    season_num = 1
                task.episodes_info = TmdbChain().tmdb_episodes(
                    tmdbid=task.mediainfo.tmdb_id,
                    season=season_num,
                    episode_group=task.mediainfo.episode_group,
                )

            # 查询整理目标目录
            if not task.target_directory:
                if task.target_path:
                    # 指定目标路径，`手动整理`场景下使用，忽略源目录匹配，使用指定目录匹配
                    task.target_directory = DirectoryHelper().get_dir(
                        media=task.mediainfo,
                        dest_path=task.target_path,
                        target_storage=task.target_storage,
                    )
                else:
                    # 启用源目录匹配时，根据源目录匹配下载目录，否则按源目录同盘优先原则，如无源目录，则根据媒体信息获取目标目录
                    task.target_directory = DirectoryHelper().get_dir(
                        media=task.mediainfo,
                        storage=task.fileitem.storage,
                        src_path=Path(task.fileitem.path),
                        target_storage=task.target_storage,
                    )
            if not task.target_storage and task.target_directory:
                task.target_storage = task.target_directory.library_storage

            source_storage = task.fileitem.storage
            target_storage = task.target_storage

            ########## 原始方法执行结束 ##########

            # 如果是目录（蓝光原盘），回退到宿主原生方法处理
            # （原生方法自带 durable 规划与终态结算；补丁不得在此直接返回失败）
            if task.fileitem.type == "dir":
                logger.debug(
                    f"【整理接管】检测到目录类型任务（可能是蓝光原盘），回退到原方法: {task.fileitem.path}"
                )
                return cls._call_original(chain_self, task, callback)

            if (
                cls._should_intercept(source_storage, target_storage)
                and cls._task_manager is not None
            ):
                logger.debug(
                    f"【整理接管】检测到 115 → 115 整理任务: {task.fileitem.name}"
                )

                from ..core.config import configer
                from ..helper.transfer.linked_subtitle_audio import (
                    is_subtitle_or_audio_file,
                )

                if (
                    configer.pan_transfer_linked_subtitle_audio
                    and is_subtitle_or_audio_file(task.fileitem)
                ):
                    logger.debug(
                        f"【整理接管】忽略字幕/音频文件（将跟随主文件一起处理）: {task.fileitem.name}"
                    )
                    # 与主文件接管同一收口：先把宿主对该文件的恢复责任摘除，
                    # 否则同一字幕/音频文件会被恢复调度每 ~15 秒重新送回整理入口。
                    if not abandon_taken_over_admission(
                        chain_self,
                        task,
                        reason=f"字幕/音频跟随主文件处理：{task.fileitem.path}",
                    ):
                        return cls._call_original(chain_self, task, callback)
                    cls._jobview_of(chain_self).running_task(task)
                    cls._jobview_of(chain_self).finish_task(task)
                    if cls._jobview_of(chain_self).is_done(task):
                        cls._jobview_of(chain_self).remove_job(task)
                    return True, "已由插件接管（字幕/音频文件，跟随主文件处理）"

                need_rename, need_notify, need_scrape = cls._derive_transfer_flags(task)

                # preview 模式：只计算目标路径并返回预览结果，不执行实际整理
                if task.preview:
                    return cls._handle_preview(
                        chain_self,
                        task,
                        callback,
                        need_rename,
                        need_notify,
                        need_scrape,
                    )

                # 注意：这个验证在 transfer_media 中进行，但由于我们拦截了，需要在这里进行
                if task.mediainfo.type == MediaType.TV and task.fileitem.type == "file":
                    if task.meta.begin_episode is None:
                        logger.warn(
                            f"【整理接管】文件 {task.fileitem.path} 整理失败：未识别到文件集数"
                        )
                        # 与宿主模块抛 TransferPlanningRejectedError 同等语义：冻结为
                        # 零文件副作用的拒绝计划并交回 callback 结算。不再自行写一条
                        # 游离的失败历史 —— 那会让任务永不结算，被恢复调度每 ~15 秒回放。
                        settled = cls._settle_planning_rejection(
                            chain_self, task, callback, "未识别到文件集数"
                        )
                        if settled is None:
                            return cls._call_original(chain_self, task, callback)
                        return settled

                    # 文件结束季为空
                    task.meta.end_season = None
                    # 文件总季数为1
                    if task.meta.total_season:
                        task.meta.total_season = 1
                    # 文件不可能超过2集
                    if task.meta.total_episode and task.meta.total_episode > 2:
                        task.meta.total_episode = 1
                        task.meta.end_episode = None

                # 计算目标路径
                target_path = cls._compute_target_path(task, need_rename=need_rename)
                if not target_path:
                    logger.error(f"【整理接管】计算目标路径失败: {task.fileitem.path}")
                    # 交回宿主原生方法（自带 durable 规划与终态结算）
                    return cls._call_original(chain_self, task, callback)

                # 确定整理方式
                transfer_type = task.transfer_type
                if not transfer_type and task.target_directory:
                    transfer_type = task.target_directory.transfer_type

                # 获取覆盖模式
                overwrite_mode = None
                if task.target_directory:
                    overwrite_mode = task.target_directory.overwrite_mode

                # 从宿主 durable 恢复责任中摘除该文件：插件接管后文件流转全部由
                # 插件负责，宿主 worker 的 terminal 判定（plan_checkpoint is not None）
                # 对本路径恒为 False，摘不掉就会被恢复调度每 ~15 秒重新投递。
                # 摘不掉（宿主缺少入口或任务状态已变化）则不接管，交回宿主原生整理。
                if not abandon_taken_over_admission(
                    chain_self, task, reason=f"插件接管 115→115 整理：{target_path}"
                ):
                    return cls._call_original(chain_self, task, callback)

                # 正在处理
                cls._jobview_of(chain_self).running_task(task)

                # 创建插件的 TransferTask
                plugin_task = PluginTransferTask(
                    fileitem=task.fileitem,
                    target_path=target_path,
                    mediainfo=task.mediainfo,
                    meta=task.meta,
                    transfer_type=transfer_type or "move",
                    overwrite_mode=overwrite_mode,
                    need_rename=need_rename,
                    need_notify=need_notify,
                    need_scrape=need_scrape,
                    scrape=task.scrape,
                    manual=task.manual,
                    background=task.background,
                    username=task.username,
                    downloader=task.downloader,
                    download_hash=task.download_hash,
                )

                # 加入批量队列
                cls._task_manager.add_task(plugin_task)

                logger.info(
                    f"【整理接管】任务已加入批量队列: {task.fileitem.name} -> {target_path}"
                )

                return True, "已由插件接管"

            # 非 115 -> 115 原方法整理
            # 必须走宿主原生方法：它自带 durable 规划、执行检查点与终态结算；
            # 旧版这里只调用「原方法的 transfer 片段」，回调因缺少 execution_checkpoint
            # 直接抛错，任务永远不结算 → 被恢复调度每 ~15 秒回放。
            return cls._call_original(chain_self, task, callback)

        except Exception as e:
            logger.error(
                f"【整理接管】Patched handle_transfer 异常: {e}", exc_info=True
            )
            try:
                result = cls._call_original(chain_self, task, callback)
            except Exception as fallback_error:
                logger.error(f"【整理接管】回退到原方法也失败: {fallback_error}")
                result = None
            if result is None:
                # 原生方法不可用（补丁期间宿主方法被还原等）：按宿主语义登记
                # checkpoint 之前的失败，保留 accepted 任务供后续重新规划，
                # 避免任务悬空且无原因可查。
                record_uncheckpointed_failure(chain_self, task, e)
                return False, f"整理异常: {e}"
            return result
        finally:
            # 与原生 __handle_transfer 一致：每次处理完尝试移除已完成作业，并清理批次 pending 集合
            cls._jobview_of(chain_self).try_remove_job(task)
            finish_scrape_batch = resolve_scrape_batch_finish(chain_self)
            if finish_scrape_batch is None:
                cls._warn_scrape_batch_finish_missing()
            else:
                try:
                    finish_scrape_batch(task)
                except Exception as finish_error:  # noqa: BLE001
                    # finally 内绝不外抛：否则会顶掉返回值，并把异常抛给宿主整理队列
                    logger.error(f"【整理接管】清理刮削批次 pending 失败: {finish_error}")

    @classmethod
    def _derive_transfer_flags(cls, task) -> Tuple[bool, bool, bool]:
        """
        与 app.modules.filemanager 中 transfer() 一致，推导 need_rename / need_notify / need_scrape

        :param task: MoviePilot TransferTask
        :return: (need_rename, need_notify, need_scrape)
        """
        if task.target_directory:
            need_rename = bool(task.target_directory.renaming)
            need_notify = bool(task.target_directory.notify)
            if task.scrape is None:
                need_scrape = bool(task.target_directory.scraping)
            else:
                need_scrape = bool(task.scrape)
            return need_rename, need_notify, need_scrape
        if task.target_path:
            need_rename = True
            need_notify = False
            need_scrape = bool(task.scrape) if task.scrape is not None else False
            return need_rename, need_notify, need_scrape
        need_rename = True
        need_notify = True
        need_scrape = bool(task.scrape) if task.scrape is not None else False
        return need_rename, need_notify, need_scrape

    @classmethod
    def _should_intercept(cls, source_storage: str, target_storage: str) -> bool:
        """
        判断是否应该拦截

        :param source_storage: 源存储
        :param target_storage: 目标存储
        :return: 是否应该拦截
        """
        return (
            cls._enabled
            and cls._storage_module
            and source_storage == cls._storage_module
            and target_storage == cls._storage_module
        )

    @staticmethod
    def _is_movie_year_conflict(file_meta, media) -> bool:
        """
        判断文件名年份是否与已识别电影年份冲突
        """
        from app.schemas.types import MediaType

        file_year = getattr(file_meta, "year", None)
        media_year = getattr(media, "year", None)
        if not file_meta or not media or not file_year or not media_year:
            return False
        media_type = getattr(media, "type", None)
        if not isinstance(media_type, MediaType):
            try:
                media_type = MediaType(media_type)
            except (TypeError, ValueError):
                return False
        return media_type == MediaType.MOVIE and str(file_year) != str(media_year)

    @classmethod
    def _compute_target_path(cls, task, need_rename: bool = True) -> Optional[Path]:
        """
        与 TransHandler.transfer_media 单文件分支一致的目标路径

        :param task: MoviePilot 的 TransferTask
        :param need_rename: 是否与 MP 目录 renaming 一致
        :return: 目标路径，失败返回 None
        """
        from app.sdk.config import settings
        from app.modules.filemanager.transhandler import TransHandler
        from app.schemas.types import MediaType

        try:
            handler = TransHandler()

            target_dir = handler.get_dest_dir(
                mediainfo=task.mediainfo,
                target_dir=task.target_directory,
                need_type_folder=task.library_type_folder,
                need_category_folder=task.library_category_folder,
            )

            if not target_dir:
                logger.error("【整理接管】计算目标目录失败")
                return None

            if not need_rename:
                return target_dir / task.fileitem.name

            if task.mediainfo.type == MediaType.TV:
                rename_format = settings.TV_RENAME_FORMAT
            else:
                rename_format = settings.MOVIE_RENAME_FORMAT

            file_ext = Path(task.fileitem.name).suffix

            naming_dict = handler.get_naming_dict(
                meta=task.meta,
                mediainfo=task.mediainfo,
                file_ext=file_ext,
                episodes_info=task.episodes_info,
            )

            # 触发 TransferRenameBuild 事件，允许插件注入命名字段
            try:
                from app.sdk.events import eventmanager
                from app.schemas import TransferRenameBuildEventData
                from app.schemas.types import ChainEventType

                build_event_data = TransferRenameBuildEventData(
                    rename_dict=naming_dict,
                    meta=task.meta,
                    mediainfo=task.mediainfo,
                    file_ext=file_ext,
                    episodes_info=task.episodes_info,
                )
                build_event = eventmanager.send_event(
                    ChainEventType.TransferRenameBuild, build_event_data
                )
                if build_event and build_event.event_data:
                    naming_dict = build_event.event_data.rename_dict
            except Exception:
                pass

            rename_kwargs = {
                "template_string": rename_format,
                "rename_dict": naming_dict,
                "path": target_dir,
            }
            try:
                sig = signature(handler.get_rename_path)
                has_varkw = any(
                    p.kind == Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                if "source_path" in sig.parameters or has_varkw:
                    rename_kwargs["source_path"] = task.fileitem.path
                if "source_item" in sig.parameters or has_varkw:
                    rename_kwargs["source_item"] = task.fileitem
            except (TypeError, ValueError):
                pass
            rename_path = handler.get_rename_path(**rename_kwargs)

            if not rename_path:
                return None

            new_file = (
                Path(rename_path) if not isinstance(rename_path, Path) else rename_path
            )

            from ..helper.transfer.handler import TransferHandler

            ext = TransferHandler._normalize_ext(task.fileitem.extension)
            if not ext and task.fileitem.path:
                ext = TransferHandler._normalize_ext(Path(task.fileitem.path).suffix)
            if not ext:
                return new_file
            if ext in {
                TransferHandler._normalize_ext(ext_item)
                for ext_item in settings.RMT_SUBEXT
            }:
                # 宿主私有静态方法，跨版本可能改名；取不到时保留原目标路径，
                # 不因字幕附加信息缺失而中断整个整理流程
                rename_subtitles = getattr(
                    TransHandler, "_TransHandler__rename_subtitles", None
                )
                if rename_subtitles is None:
                    logger.warning(
                        "【整理接管】宿主 TransHandler 缺少字幕重命名私有方法，"
                        "本次跳过字幕附加信息处理"
                    )
                else:
                    new_file = rename_subtitles(task.fileitem, new_file)

            return new_file

        except Exception as e:
            logger.error(f"【整理接管】计算目标路径失败: {e}", exc_info=True)
            return None

    @classmethod
    def _handle_preview(
        cls,
        chain_self,
        task,
        callback: Optional[Callable],
        need_rename: bool,
        need_notify: bool,
        need_scrape: bool,
    ) -> Optional[Tuple[bool, str]]:
        """
        Preview 模式：只计算目标路径，不执行实际文件操作

        :param chain_self: TransferChain 实例
        :param task: MoviePilot TransferTask
        :param callback: 回调函数（preview 模式下为 _preview_callback）
        :param need_rename: 是否需要重命名
        :param need_notify: 是否需要通知
        :param need_scrape: 是否需要刮削
        :return: 回调结果或 (True, target_path)
        """
        try:
            target_path = cls._compute_target_path(task, need_rename=need_rename)
            if not target_path:
                logger.error(
                    f"【整理接管】Preview 模式计算目标路径失败: {task.fileitem.path}"
                )
                return False, "计算目标路径失败"

            transfer_type = task.transfer_type
            if not transfer_type and task.target_directory:
                transfer_type = task.target_directory.transfer_type

            target_storage = task.target_storage or ""
            from app.schemas import FileItem, TransferInfo

            transferinfo = TransferInfo(
                success=True,
                fileitem=task.fileitem,
                target_item=FileItem(
                    storage=target_storage,
                    path=str(target_path),
                    name=target_path.name,
                    type="file",
                ),
                target_diritem=FileItem(
                    storage=target_storage,
                    path=str(target_path.parent) + "/",
                    name=target_path.parent.name,
                    type="dir",
                ),
                transfer_type=transfer_type or "move",
                file_list=[task.fileitem.path],
                file_list_new=[str(target_path)],
                need_scrape=need_scrape,
                need_notify=need_notify,
            )

            if callback:
                return callback(task, transferinfo)
            return True, str(target_path)
        except Exception as e:
            logger.error(f"【整理接管】Preview 模式异常: {e}", exc_info=True)
            return False, f"Preview 异常: {e}"

    @classmethod
    def _call_original(
        cls, chain_self, task, callback: Optional[Callable]
    ) -> Optional[Tuple[bool, str]]:
        """
        调用原方法

        :param chain_self: TransferChain 实例
        :param task: 任务
        :param callback: 回调
        :return: 原方法的返回值
        """
        if cls._original_handle_transfer:
            return cls._original_handle_transfer(chain_self, task, callback)
        return None

    @classmethod
    def _settle_planning_rejection(
        cls,
        chain_self,
        task,
        callback: Optional[Callable],
        message: str,
    ) -> Optional[Tuple[bool, str]]:
        """
        按宿主 V3 语义收口「确定性规划拒绝」（未识别媒体信息、未识别文件集数等）。

        宿主 ``__handle_transfer`` 在这类分支上不是直接返回失败，而是
        ``__checkpoint_planning_rejection(task, reason)``：把拒绝冻结成 **零文件副作用**
        的 durable 计划、提交执行检查点，再由 ``callback`` 走统一终态结算。补丁复制的是
        旧版规划前流程，必须显式补上这一步，否则宿主认为任务未结算并每 ~15 秒回放。

        :param chain_self: 宿主 TransferChain 实例
        :param task: 宿主整理任务
        :param callback: 宿主回调（队列默认回调即原子终态结算入口）
        :param message: 拒绝原因，同时作为失败消息与历史记录原因
        :return: ``(成功状态, 消息)``；宿主缺少收口入口时返回 ``None``，
            调用方必须回退宿主原生流程，而不是自行返回失败
        """
        result = checkpoint_planning_rejection(chain_self, task, message, callback)
        if result is None:
            return None
        cls._register_ai_retry(chain_self, task)
        return result

    @classmethod
    def _register_ai_retry(cls, chain_self, task) -> None:
        """
        失败结算后按配置登记 AI 智能体自动重试（宿主 durable 重试入口）。

        :param chain_self: 宿主 TransferChain 实例
        :param task: 宿主整理任务（结算后按其 durable 身份回查历史记录）
        """
        from app.sdk.config import settings

        if not (settings.AI_AGENT_ENABLE and settings.AI_AGENT_RETRY_TRANSFER):
            return
        history = latest_transfer_history(chain_self, task)
        if history is None:
            logger.debug("【整理接管】未取到本次失败的历史记录，跳过 AI 智能体重试登记")
            return
        retry_result = request_durable_transfer_retry(
            chain_self, history, requested_by="p115strmhelper_ai_retry"
        )
        if retry_result is None:
            # None 有两种来源：宿主没有该入口（compat 层已告警），或该历史不是
            # durable 任务（旧记录，无自动重试可言）。
            logger.info(
                f"【整理接管】该历史无 durable 整理任务（旧记录或宿主未提供入口），"
                f"跳过 AI 智能体重试登记（历史 #{history.id}）"
            )
        elif retry_result[0]:
            logger.info(f"【整理接管】已登记AI智能体重试整理历史记录 #{history.id}")
        else:
            logger.warning(
                f"【整理接管】AI智能体重试未受理（历史 #{history.id}）：{retry_result[1]}"
            )
