"""
宿主 JobView 适配层。

MoviePilot V3 把作业队列实现从 ``app.chain.transfer`` 收敛到
``app.application.transfer.workflow.JobManager``，并把 ``TransferChain.jobview``
定义为该实现的一个**稳定伴生访问点**。插件原先在 handler / patch 里散落了 30 余处
``chain.jobview.xxx`` 直接调用，存在两类风险：

1. 宿主调整 JobManager 方法名或归属时，改动点分散、难以统一兜底；
2. 插件依赖了 JobManager 的**私有方法**（``_JobManager__get_media_id``）与私有容器
   （``_job_view``），名字改写规则一旦变化就会静默失效。

本模块把全部队列操作收口成 ``JobViewAdapter``，调用方只依赖插件自己的接口：

* 公开方法直接转发，缺失时记录明确的告警而非抛出；
* 依赖宿主私有的调试能力（任务状态详情）改为**能力探测 + 降级**，取不到就跳过
  诊断日志，绝不影响主流程。
"""

from typing import Any, Callable, List, Optional, Tuple

from app.sdk.logging import logger

__all__ = [
    "JobViewAdapter",
    "abandon_taken_over_admission",
    "checkpoint_planning_rejection",
    "get_jobview",
    "get_task_lock",
    "latest_transfer_history",
    "record_uncheckpointed_failure",
    "request_durable_transfer_retry",
    "resolve_jobview",
    "resolve_scrape_batch_finish",
    "scrape_batch_finish_names",
]

# 适配器初始化时要求宿主必须提供的方法；缺失说明宿主接口已变更
_REQUIRED_METHODS = (
    "add_task",
    "remove_task",
    "running_task",
    "finish_task",
    "fail_task",
    "remove_job",
    "try_remove_job",
    "is_done",
)


class JobViewAdapter:
    """
    对宿主 JobManager 的薄适配器。

    对宿主公开接口做直通转发；对宿主私有实现细节做能力探测与优雅降级。
    """

    __slots__ = ("_jobview", "_owner")

    def __init__(self, jobview: Any, owner: str = "TransferChain"):
        """
        :param jobview: 宿主 JobManager 实例
        :param owner: 来源描述，仅用于日志定位
        """
        self._jobview = jobview
        self._owner = owner

    @property
    def raw(self) -> Any:
        """返回原始宿主对象，供确实需要直连的场景使用。"""
        return self._jobview

    def is_available(self) -> bool:
        """宿主对象是否存在且具备必需接口。"""
        return self._jobview is not None and all(
            callable(getattr(self._jobview, name, None)) for name in _REQUIRED_METHODS
        )

    def verify(self) -> bool:
        """
        校验宿主 JobManager 接口完整性。

        返回 False 时调用方可决定降级策略；缺失项会逐条记录，便于定位宿主变更。
        """
        if self._jobview is None:
            logger.error(f"【整理接管】{self._owner} 未提供 jobview，作业队列不可用")
            return False
        missing = [
            name
            for name in _REQUIRED_METHODS
            if not callable(getattr(self._jobview, name, None))
        ]
        if missing:
            logger.error(
                f"【整理接管】宿主 jobview 缺少方法 {missing}，"
                f"作业队列接口可能已变更，请检查插件适配"
            )
            return False
        return True

    def _call(self, name: str, *args, default: Any = None, **kwargs) -> Any:
        """
        安全调用宿主公开方法。

        :param name: 宿主方法名
        :param default: 方法缺失或调用异常时的返回值
        """
        func = getattr(self._jobview, name, None)
        if not callable(func):
            logger.warning(
                f"【整理接管】宿主 jobview 不支持 {name}，本次跳过"
                f"（宿主接口可能已变更）"
            )
            return default
        try:
            return func(*args, **kwargs)
        except Exception as err:
            logger.error(f"【整理接管】调用 jobview.{name} 失败: {err}", exc_info=True)
            return default

    # ---------- 作业状态流转 ----------

    def add_task(self, task, state: str = "waiting") -> Any:
        """加入作业队列。"""
        return self._call("add_task", task, state=state)

    def migrate_task(self, task) -> Any:
        """把任务从 meta 作业迁移到 media 作业。"""
        return self._call("migrate_task", task)

    def running_task(self, task) -> Any:
        """标记任务为处理中。"""
        return self._call("running_task", task)

    def finish_task(self, task) -> Any:
        """标记任务完成。"""
        return self._call("finish_task", task)

    def fail_task(self, task) -> Any:
        """标记任务失败。"""
        return self._call("fail_task", task)

    def remove_task(self, fileitem) -> Any:
        """按文件项移除任务。"""
        return self._call("remove_task", fileitem)

    def remove_job(self, task) -> Any:
        """移除作业。"""
        return self._call("remove_job", task)

    def try_remove_job(self, task) -> Any:
        """尝试移除已完成作业。"""
        return self._call("try_remove_job", task)

    # ---------- 状态查询 ----------

    def is_done(self, task) -> bool:
        """作业是否全部完成。"""
        return bool(self._call("is_done", task, default=False))

    def is_finished(self, task) -> bool:
        """作业是否已结束。"""
        return bool(self._call("is_finished", task, default=False))

    def is_success(self, task) -> bool:
        """作业是否成功。"""
        return bool(self._call("is_success", task, default=False))

    def count(self, media, season: Optional[int] = None) -> int:
        """统计媒体对应的任务数。"""
        return self._call("count", media, season=season, default=0)

    def size(self, media, season: Optional[int] = None) -> int:
        """统计媒体对应的任务体积。"""
        return self._call("size", media, season=season, default=0)

    def season_episodes(self, *args, **kwargs) -> Any:
        """查询季集信息。"""
        return self._call("season_episodes", *args, **kwargs)

    def success_tasks(self, *args, **kwargs) -> List[Any]:
        """查询成功任务列表。"""
        result = self._call("success_tasks", *args, **kwargs)
        return result if result is not None else []

    # ---------- 依赖宿主私有实现的诊断能力（可降级） ----------

    def describe_job_tasks(self, mediainfo, season: Optional[int] = None):
        """
        获取某媒体作业下各任务的 ``(文件名, 状态)`` 列表，用于失败诊断。

        宿主把该能力放在私有方法 ``_JobManager__get_media_id`` 与私有容器
        ``_job_view`` 上，跨版本最易失效。因此这里做逐级能力探测：任一级取不到就
        返回 ``None``，调用方跳过诊断日志即可，不影响整理主流程。

        主路径走 V3 公开导出的 ``get_job_id``（它内部会按 mediainfo 分派到
        media 作业），仅在其不可用时才回退到私有名字改写方法。
        """
        jobview = self._jobview
        if jobview is None:
            return None

        job_id = self._resolve_job_id(jobview, mediainfo, season)
        if job_id is None:
            return None

        job_view = getattr(jobview, "_job_view", None)
        if not isinstance(job_view, dict) or job_id not in job_view:
            return None

        job = job_view[job_id]
        tasks = getattr(job, "tasks", None) or []
        return [
            (
                getattr(getattr(t, "fileitem", None), "name", None) or "None",
                getattr(t, "state", None),
            )
            for t in tasks
        ]

    @staticmethod
    def _resolve_job_id(jobview: Any, mediainfo, season: Optional[int]) -> Any:
        """逐级解析作业 ID，全部不可用时返回 None。"""
        # 主路径：V3 公开导出的 get_job_id，接受完整 task
        get_job_id = getattr(jobview, "get_job_id", None)
        if callable(get_job_id):
            probe = _build_probe_task(mediainfo, season)
            if probe is not None:
                try:
                    return get_job_id(probe)
                except Exception as err:
                    logger.debug(f"【整理接管】get_job_id 解析失败，尝试回退: {err}")

        # 回退：私有名字改写方法，名字随宿主类名变化
        mangled = getattr(jobview, "_JobManager__get_media_id", None)
        if callable(mangled):
            try:
                return mangled(media=mediainfo, season=season)
            except Exception as err:
                logger.debug(f"【整理接管】私有作业 ID 方法调用失败: {err}")

        logger.debug("【整理接管】宿主未提供作业 ID 查询能力，跳过任务状态诊断")
        return None


def _build_probe_task(mediainfo, season: Optional[int], fileitem=None):
    """
    构造一个仅用于计算作业 ID 的最小任务对象。

    作业 ID 由媒体身份与季信息决定，与文件项无关，因此传最小可用字段即可。
    任一步不可用时返回 ``None``，由调用方回退到私有方法或直接降级。

    :param mediainfo: 已识别的媒体信息
    :param season: 季号
    :param fileitem: 可选文件项
    :return: 最小 TransferTask；构造不可用返回 None
    """
    try:
        from app.application.transfer.models import TransferTask as MPTransferTask
        from app.sdk.media import MetaBase
    except Exception as err:
        logger.debug(f"【整理接管】探针任务所需宿主类型不可用: {err}")
        return None

    try:
        meta = MetaBase()
        if season is not None:
            try:
                meta.begin_season = season
            except Exception:
                pass
        return MPTransferTask(fileitem=fileitem, mediainfo=mediainfo, meta=meta)
    except Exception as err:
        logger.debug(f"【整理接管】构造探针任务失败: {err}")
        return None


# 宿主「刮削批次收尾」方法的候选名。
#
# MoviePilot V2 该方法是被名字改写的私有成员 ``_TransferChain__finish_scrape_batch_task``；
# V3 把 TransferChain 拆成多个 owner 后改为单下划线公开名 ``_finish_scrape_batch_task``
# （见宿主 ``app/chain/transfer/contract.py`` 对插件公开的合同）。
# 两个名字都认、优先 V3 公开名，避免宿主改名后补丁被"目标缺失"直接放弃。
_SCRAPE_BATCH_FINISH_NAMES = (
    "_finish_scrape_batch_task",
    "_TransferChain__finish_scrape_batch_task",
)


def scrape_batch_finish_names() -> List[str]:
    """返回刮削批次收尾方法的候选名，供日志与报错提示复用。"""
    return list(_SCRAPE_BATCH_FINISH_NAMES)


def resolve_scrape_batch_finish(owner: Any) -> Optional[Callable]:
    """
    解析宿主「刮削批次收尾」方法。

    :param owner: 宿主 TransferChain 类或实例
    :return: 可调用的收尾方法；候选名都不存在时返回 None
    """
    for name in _SCRAPE_BATCH_FINISH_NAMES:
        func = getattr(owner, name, None)
        if callable(func):
            return func
    return None


def resolve_jobview(owner: Any) -> Tuple[Any, bool]:
    """
    按实例视角解析宿主作业队列（jobview），**不主动构造宿主链实例**。

    宿主的 ``jobview`` 挂在 ``TransferChain`` **实例**上（``self.jobview = JobManager()``），
    类上取不到；而宿主单例在"运行上下文未就绪"时构造会抛异常，且失败实例会被
    metaclass 缓存（``_retain_failed_singleton``）——主动构造会给宿主进程留下半成品
    单例。因此这里只查**已存在**的实例（宿主 metaclase 的 ``get_existing_instance``），
    查不到就如实报告"无法探测"，由调用方决定跳过。

    :param owner: 宿主 TransferChain 类或实例
    :return: ``(jobview, probe_ok)``
        - ``jobview`` 非 None：探测成功；
        - ``jobview`` 为 None 且 ``probe_ok`` 为 True：宿主确实没有 jobview（调用方可报错）；
        - ``jobview`` 为 None 且 ``probe_ok`` 为 False：宿主实例尚未创建或无法探测
          （调用方应跳过校验而不是判失败，否则会把"探不到"当成"宿主改坏"）。
    """
    jobview = getattr(owner, "jobview", None)
    if jobview is not None:
        return jobview, True
    if isinstance(owner, type):
        getter = getattr(owner, "get_existing_instance", None)
        if not callable(getter):
            return None, False
        try:
            instance = getter()
        except Exception as err:  # noqa: BLE001 - 探测失败不改变调用方流程
            logger.warning(f"【整理接管】查询宿主链已有实例失败: {err}")
            return None, False
        if instance is None:
            return None, False
        return getattr(instance, "jobview", None), True
    return None, True


def request_durable_transfer_retry(
    chain: Any,
    history: Any,
    *,
    requested_by: str,
) -> Optional[Tuple[bool, str]]:
    """
    请求宿主登记「durable 整理重试」（V3 的失败重试入口）。

    宿主 V2 用 ``retry_scheduler.schedule_retry(history_id, group_key=...)``；
    V3 没有 ``retry_scheduler``，入口是 ``TransferChain._request_durable_transfer_retry(
    history, *, requested_by)``：同步调用，返回 ``(accepted, message)``；
    非 durable 旧历史返回 ``None``；宿主未提供该入口时同样返回 ``None``，由调用方降级。

    :param chain: 宿主 TransferChain 实例
    :param history: 整理历史快照（``add_transfer_fail`` 的返回值）
    :param requested_by: 发起重试的稳定入口身份
    :return: ``(是否受理, 消息)``；入口不可用或调用失败时返回 None
    """
    request = getattr(chain, "_request_durable_transfer_retry", None)
    if not callable(request):
        logger.warning("【整理接管】宿主未提供 durable 重试入口，跳过自动重试登记")
        return None
    try:
        result = request(history, requested_by=requested_by)
    except Exception as err:  # noqa: BLE001 - 重试登记失败不影响整理主流程
        logger.error(f"【整理接管】登记 durable 整理重试失败: {err}")
        return None
    if result is None:
        # 宿主对该历史返回 None = 旧记录没有 durable 任务可重放，并非调用失败。
        logger.debug(
            f"【整理接管】历史 #{getattr(history, 'id', None)} 无 durable 整理任务，跳过自动重试"
        )
    return result


def checkpoint_planning_rejection(
    chain: Any,
    task: Any,
    message: str,
    callback: Optional[Callable] = None,
) -> Optional[Tuple[bool, str]]:
    """
    按宿主 V3 语义收口「确定性规划拒绝」（未识别到媒体信息、未识别到文件集数等）。

    宿主 ``TransferChain.__handle_transfer`` 在未识别分支不是直接返回失败，而是
    ``__checkpoint_planning_rejection(task, reason)``：把拒绝冻结成**零文件副作用**的
    durable 计划、提交执行检查点，再由 ``callback`` 走统一终态结算。补丁复制的是规划前
    的旧流程，失败/提前返回时若漏掉这一步，宿主 ``__claim_recovery_batch`` 会认为任务
    未结算并 **每 ~15 秒回放同一文件**（2026-10-09 实测：未识别文件被反复重投、
    每轮新增/更新一条失败历史）。

    :param chain: 宿主 TransferChain 实例
    :param task: 宿主整理任务
    :param message: 拒绝原因（同时作为失败消息与历史记录原因）
    :param callback: 宿主回调；传了就由它完成 durable 终态结算
    :return: ``(成功状态, 消息)``；宿主未提供收口入口时返回 ``None``，
        调用方必须回退宿主原生流程而不是自行返回失败
    """
    rejection = getattr(chain, "_TransferChain__checkpoint_planning_rejection", None)
    if not callable(rejection):
        logger.warning(
            "【整理接管】宿主未提供规划拒绝收口入口，无法确认终态；"
            "本次交回宿主原生整理（避免任务悬空被反复回放）"
        )
        return None
    transferinfo = rejection(task, message)
    if callback:
        return callback(task, transferinfo)
    return bool(transferinfo.success), transferinfo.message or ""


def record_uncheckpointed_failure(chain: Any, task: Any, error: Any) -> bool:
    """
    按宿主语义登记「checkpoint 之前的失败」，保留 accepted 任务供后续重新规划。

    宿主 ``__handle_transfer`` 在 ``__perform_transfer`` 返回失败（如重复投递）
    或抛异常时会调用它。补丁替换了整个 ``__handle_transfer``，因此必须自己补上，
    否则宿主恢复调度看不到失败原因，只能反复回放。

    :param chain: 宿主 TransferChain 实例
    :param task: 宿主整理任务
    :param error: 失败原因（异常或消息）
    :return: 是否成功登记
    """
    record = getattr(chain, "_TransferChain__record_uncheckpointed_failure", None)
    if not callable(record):
        logger.warning(
            "【整理接管】宿主未提供未结算失败登记入口，跳过记录"
            "（宿主接口可能已变更）"
        )
        return False
    try:
        record(task, error)
    except Exception as err:  # noqa: BLE001 - 记录失败不改变整理主流程
        logger.error(f"【整理接管】登记整理规划失败原因出错: {err}")
        return False
    return True


def abandon_taken_over_admission(chain: Any, task: Any, *, reason: str) -> bool:
    """
    注销被插件接管的 durable 准入记录，使宿主不再持有该文件的恢复责任。

    插件接管 115→115 整理后，文件的实际流转（移动/复制、历史、通知）全部由插件负责，
    但宿主在内存队列里仍持有一条 ``accepted`` 状态的 durable 准入记录；宿主 worker 的
    ``terminal = plan_checkpoint is not None`` 对本路径恒为 False，于是每轮恢复扫描
    （15 秒）都会把同一文件重新送回整理入口 —— 表现为同一文件被插件反复接管、重复
    处理（copy 模式下源文件不会消失，永远不会自愈）。

    宿主没有「插件接管」的公开终态，只有 ``TransferRecoveryCommand``/准入仓储的
    ``abandon_unstarted``：仅在**任务从未执行**（accepted + 无检查点 + 无步骤 + 无历史）
    且当前租约 token 有效时删除登记，正好对应「插件在宿主规划前接管」这一时点。

    :param chain: 宿主 TransferChain 实例
    :param task: 宿主整理任务（需带 durable 身份与租约）
    :param reason: 注销原因，仅用于日志
    :return: 是否确认注销；False 时调用方**不得**接管，应回退宿主原生整理
    """
    task_id = getattr(task, "admission_task_id", None)
    lease_token = getattr(task, "lease_token", None)
    if not task_id or not lease_token:
        logger.warning(f"【整理接管】整理任务缺少 durable 身份，无法注销宿主准入：{reason}")
        return False
    repository = getattr(chain, "_transfer_admissions", None)
    abandon = getattr(repository, "abandon_unstarted", None)
    if not callable(abandon):
        logger.warning(
            "【整理接管】宿主未提供准入注销入口，无法确认接管终态"
            "（宿主接口可能已变更）"
        )
        return False
    try:
        deleted = abandon(task_id=task_id, lease_token=lease_token)
    except Exception as err:  # noqa: BLE001 - 注销失败即不接管，由调用方回退
        logger.error(f"【整理接管】注销宿主整理准入记录失败：{err}")
        return False
    if not deleted:
        logger.warning(f"【整理接管】宿主整理准入记录未注销（任务状态已变化），本次不接管：{reason}")
        return False
    logger.debug(f"【整理接管】已注销宿主整理准入记录，插件接管后续流转：{reason}")
    return True


def latest_transfer_history(chain: Any, task: Any) -> Any:
    """
    按 durable 任务身份查询刚结算的整理历史，供失败后的自动重试登记使用。

    :param chain: 宿主 TransferChain 实例
    :param task: 宿主整理任务
    :return: 整理历史快照；查不到时返回 None
    """
    task_id = getattr(task, "admission_task_id", None)
    if not task_id:
        return None
    try:
        from app.db.oper.transferhistory import TransferHistoryOper

        return TransferHistoryOper().get_by_transfer_task_id(task_id=task_id)
    except Exception as err:  # noqa: BLE001 - 查不到就跳过自动重试
        logger.debug(f"【整理接管】按整理任务查询历史记录失败: {err}")
        return None


def get_jobview(chain) -> Optional[JobViewAdapter]:
    """
    从宿主链对象取得 JobView 适配器。

    :param chain: 宿主 TransferChain 实例
    :return: 适配器；宿主未提供 jobview 时返回 None
    """
    jobview = getattr(chain, "jobview", None)
    if jobview is None:
        logger.warning("【整理接管】宿主对象未提供 jobview，作业队列功能将跳过")
        return None
    return JobViewAdapter(jobview, owner=getattr(chain, "__class__", type(chain)).__name__)


def get_task_lock() -> Optional[Callable]:
    """
    取得宿主提供的整理任务锁。

    :return: 锁对象（可作上下文管理器）；宿主未提供时返回 None
    """
    try:
        from app.chain.transfer import task_lock
    except Exception as err:
        logger.error(f"【整理接管】获取宿主 task_lock 失败: {err}")
        return None
    return task_lock
