import errno

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from os import fspath, remove, removedirs, scandir, stat
from os.path import dirname, normpath
from textwrap import indent
from threading import Lock
from traceback import format_exc
from typing import cast, ContextManager
from urllib.error import URLError
from pathlib import Path
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import NamedTuple, TypedDict

from concurrenttools import thread_batch
from hashtools import file_digest
from p115 import check_response, MultipartUploadAbort, MultipartResumeData
from posixpatht import escape, joinpath as pjoinpath, normpath as pnormpath, split as psplit, path_is_dir_form
from rich.progress import (
    Progress, DownloadColumn, FileSizeColumn, MofNCompleteColumn, SpinnerColumn,
    TimeElapsedColumn, TransferSpeedColumn,
)
from texttools import rotate_text
from httpx import RequestError

@dataclass
class Task:
    src_attr: Mapping
    dst_pid: int
    dst_attr: None | str | Mapping = None
    times: int = 0
    reasons: list[BaseException] = field(default_factory=list)


class Tasks(TypedDict):
    success: dict[str, Task]
    failed: dict[str, Task]
    unfinished: dict[str, Task]


class Result(NamedTuple):
    stats: dict
    tasks: Tasks


def get_status_code(e: BaseException, /) -> int:
    status = getattr(e, "status", None) or getattr(e, "code", None) or getattr(e, "status_code", None)
    if status is None and hasattr(e, "response"):
        response = e.response
        status = (
            getattr(response, "status", None) or 
            getattr(response, "code", None) or 
            getattr(response, "status_code", None)
        )
    return status or 0


def upload_files(client, src_path, dst_path):

    part_size = 1 << 30
    max_workers = 1
    max_retries = -1
    resume = False
    remove_done = False
    with_root = False

    count_lock: None | ContextManager = None
    if max_workers > 1:
        count_lock = Lock()

    do_request: None | Callable = None
    fs = client.get_fs(request=do_request)

    @contextmanager
    def ensure_cm(cm):
        if isinstance(cm, ContextManager):
            with cm as val:
                yield val
        else:
            yield cm

    stats: dict = {
        # 开始时间
        "start_time": datetime.now(), 
        # 总耗时
        "elapsed": "", 
        # 源路径
        "src_path": "",  
        # 目标路径
        "dst_path": "", 
        # 任务总数
        "tasks": {"total": 0, "files": 0, "dirs": 0, "size": 0}, 
        # 成功任务数
        "success": {"total": 0, "files": 0, "dirs": 0, "size": 0}, 
        # 失败任务数（发生错误但已抛弃）
        "failed": {"total": 0, "files": 0, "dirs": 0, "size": 0}, 
        # 重试任务数（发生错误但可重试），一个任务可以重试多次
        "retry": {"total": 0, "files": 0, "dirs": 0}, 
        # 未完成任务数：未运行、重试中或运行中
        "unfinished": {"total": 0, "files": 0, "dirs": 0, "size": 0}, 
        # 各种错误数量和分类汇总
        "errors": {"total": 0, "files": 0, "dirs": 0, "reasons": {}}, 
        # 是否执行完成：如果是 False，说明是被人为终止
        "is_completed": False, 
    }
    # 任务总数
    tasks: dict[str, int] = stats["tasks"]
    # 成功任务数
    success: dict[str, int] = stats["success"]
    # 失败任务数（发生错误但已抛弃）
    failed: dict[str, int] = stats["failed"]
    # 重试任务数（发生错误但可重试），一个任务可以重试多次
    retry: dict[str, int] = stats["retry"]
    # 未完成任务数：未运行、重试中或运行中
    unfinished: dict[str, int] = stats["unfinished"]
    # 各种错误数量和分类汇总
    errors: dict = stats["errors"]
    # 各种错误的分类汇总
    reasons: dict[str, int] = errors["reasons"]
    # 开始时间
    start_time = stats["start_time"]

    def get_path_attr(path) -> dict:
        if isinstance(path, str):
            path = Path(path)
        attr = {
            "path": fspath(path), 
            "name": path.name, 
            "is_directory": path.is_dir(), 
        }
        attr.update(zip(("mode", "inode", "dev", "nlink", "uid", "gid", "size", "atime", "mtime", "ctime"), path.stat()))
        return attr

    def update_tasks(total=1, files=0, size=0):
        dirs = total - files
        with ensure_cm(count_lock):
            tasks["total"] += total
            unfinished["total"] += total
            if dirs:
                tasks["dirs"] += dirs
                unfinished["dirs"] += dirs
            if files:
                tasks["files"] += files
                tasks["size"] += size
                unfinished["files"] += files
                unfinished["size"] += size

    def update_success(total=1, files=0, size=0):
        dirs = total - files
        with ensure_cm(count_lock):
            success["total"] += total
            unfinished["total"] -= total
            if dirs:
                success["dirs"] += dirs
                unfinished["dirs"] -= dirs
            if files:
                success["files"] += files
                success["size"] += size
                unfinished["files"] -= files
                unfinished["size"] -= size

    def update_failed(total=1, files=0, size=0):
        dirs = total - files
        with ensure_cm(count_lock):
            failed["total"] += total
            unfinished["total"] -= total
            if dirs:
                failed["dirs"] += dirs
                unfinished["dirs"] -= dirs
            if files:
                failed["files"] += files
                failed["size"] += size
                unfinished["files"] -= files
                unfinished["size"] -= size

    def update_retry(total=1, files=0):
        dirs = total - files
        with ensure_cm(count_lock):
            retry["total"] += total
            if dirs:
                retry["dirs"] += dirs
            if files:
                retry["files"] += files

    def update_errors(e, _is_directory=False):
        exctype = type(e).__module__ + "." + type(e).__qualname__
        with ensure_cm(count_lock):
            errors["total"] += 1
            if _is_directory:
                errors["dirs"] += 1
            else:
                errors["files"] += 1
            try:
                reasons[exctype] += 1
            except KeyError:
                reasons[exctype] = 1

    def hash_report(attr):
        update_desc = rotate_text(attr["name"], 22, interval=0.1).__next__
        task = progress.add_task("[bold blink red on yellow]DIGESTING[/bold blink red on yellow] " + update_desc(), total=attr["size"])
        def hash_progress(step):
            progress.update(task, description="[bold blink red on yellow]DIGESTING[/bold blink red on yellow] " + update_desc(), advance=step)
            progress.update(statistics_bar, description=get_stat_str())
        try:
            return file_digest(
                open(attr["path"], "rb"),
                "sha1", 
                callback=hash_progress,
            )
        finally:
            progress.remove_task(task)

    def add_report(_, attr):
        update_desc = rotate_text(attr["name"], 32, interval=0.1).__next__
        _task = progress.add_task(update_desc(), total=attr["size"])
        try:
            while not closed:
                step = yield
                progress.update(_task, description=update_desc(), advance=step)
                progress.update(statistics_bar, description=get_stat_str(), advance=step, total=tasks["size"])
        finally:
            progress.remove_task(_task)

    def work(task: Task, submit):
        src_attr, dst_pid, dst_attr = task.src_attr, task.dst_pid, task.dst_attr
        src_path = src_attr["path"]
        if dst_attr is None:
            name: None | str = None
        elif isinstance(dst_attr, str):
            name = dst_attr
        else:
            name = cast(str, dst_attr["name"])
        try:
            task.times += 1
            if src_attr["is_directory"]:
                subdattrs: None | dict = None
                if not name:
                    dst_id = dst_pid
                else:
                    try:
                        if isinstance(dst_attr, str):
                            resp = check_response(fs.fs_mkdir(name, dst_pid))
                            name = cast(str, resp["file_name"])
                            dst_id = int(resp["file_id"])
                            task.dst_attr = {"id": dst_id, "parent_id": dst_pid, "name": name, "is_directory": True}
                            subdattrs = {}
                            console_print(f"[bold green][GOOD][/bold green] 📂 创建目录: [blue underline]{src_path!r}[/blue underline] ➜ [blue underline]{name!r}[/blue underline] in {dst_pid}")
                        else:
                            dst_id = cast(Mapping, dst_attr)["id"]
                    except FileExistsError:
                        dst_attr = task.dst_attr = fs.attr([name], pid=dst_pid, ensure_dir=True)
                        dst_id = dst_attr["id"]
                if subdattrs is None:
                    subdattrs = {
                        (attr["name"], attr["is_directory"]): attr
                        for attr in fs.listdir_attr(dst_id)
                    }
                subattrs = [
                    a for a in map(get_path_attr, scandir(src_path))
                    if a["name"] not in (".DS_Store", "Thumbs.db") and not a["name"].startswith("._")
                ]
                update_tasks(
                    total=len(subattrs), 
                    files=sum(not a["is_directory"] for a in subattrs), 
                    size=sum(a["size"] for a in subattrs if not a["is_directory"]), 
                )
                progress.update(statistics_bar, description=get_stat_str(), total=tasks["size"])
                pending_to_remove: list[int] = []
                for subattr in subattrs:
                    subname = subattr["name"]
                    subpath = subattr["path"]
                    is_directory = subattr["is_directory"]
                    key = subname, is_directory
                    if key in subdattrs:
                        subdattr = subdattrs[key]
                        subdpath = subdattr["path"]
                        if is_directory:
                            console_print(f"[bold yellow][SKIP][/bold yellow] 📂 目录已建: [blue underline]{subpath!r}[/blue underline] ➜ [blue underline]{subdpath!r}[/blue underline]")
                            subtask = Task(subattr, dst_id, subdattr)
                        elif resume and subattr["size"] == subdattr["size"] and subattr["mtime"] <= subdattr["ctime"]:
                            console_print(f"[bold yellow][SKIP][/bold yellow] 📝 跳过文件: [blue underline]{subpath!r}[/blue underline] ➜ [blue underline]{subdpath!r}[/blue underline]")
                            update_success(1, 1, subattr["size"])
                            progress.update(statistics_bar, description=get_stat_str())
                            continue
                        else:
                            subtask = Task(subattr, dst_id, subname)
                            pending_to_remove.append(subdattr["id"])
                    else:
                        subtask = Task(subattr, dst_id, subname)
                    unfinished_tasks[subpath] = subtask
                    submit(subtask)
                if not subattrs and remove_done:
                    try:
                        removedirs(src_path)
                    except OSError:
                        pass
                if pending_to_remove:
                    for i in range(0, len(pending_to_remove), 1_000):
                        part_ids = pending_to_remove[i:i+1_000]
                        try:
                            resp = fs.fs_delete(part_ids)
                            console_print(f"""\
    [bold green][DELETE][/bold green] 📝 删除文件列表
    ├ ids({len(part_ids)}) = {part_ids}
    ├ response = {resp}""")
                        except BaseException as e:
                            console_print(f"""[bold yellow][SKIP][/bold yellow] 📝 删除文件列表失败
    ├ ids({len(part_ids)}) = {part_ids}
    ├ reason = [red]{type(e).__module__}.{type(e).__qualname__}[/red]: {e}""")
                update_success(1)
            else:
                if not name:
                    name = src_attr["name"]
                kwargs: dict = {}
                if src_attr["size"] <= 1 << 30: # 1 GB
                    # NOTE: 1 GB 以内使用网页版上传接口，这个接口的优势是上传完成后会自动产生 115 生活事件
                    kwargs["upload_directly"] = None
                elif src_attr["size"] > 1 << 34: # 16 GB
                    # NOTE: 介于 1 GB 和 16 GB 时直接流式上传，超过 16 GB 时，使用分块上传
                    kwargs["partsize"] = part_size
                # TODO: 如果 115 GB < src_attr["size"] <= 500 GB，则计算 ed2k 后离线下载
                filesize, filehash = hash_report(src_attr)
                console_print(f"[bold green][HASH][/bold green] 🧠 计算哈希: sha1([blue underline]{src_path!r}[/blue underline]) = {filehash.hexdigest()!r}")
                kwargs["filesize"] = filesize
                kwargs["filesha1"] = filehash.hexdigest()
                ticket: MultipartResumeData
                for i in range(5):
                    if i:
                        console_print(f"""\
    [bold yellow][RETRY][/bold yellow] 📝 重试上传: [blue underline]{src_path!r}[/blue underline] ➜ [blue underline]{name!r}[/blue underline] in {dst_pid}
    ├ ticket = {ticket}""")
                    try:
                        resp = client.upload_file(
                            src_path,
                            name,
                            pid=dst_pid,
                            make_reporthook=partial(add_report, attr=src_attr),
                            **kwargs,
                        )
                        break
                    except MultipartUploadAbort as e:
                        exc = e
                        ticket = kwargs["multipart_resume_data"] = e.ticket
                else:
                    raise exc
                check_response(resp)
                if resp.get("status") == 2 and resp.get("statuscode") == 0:
                    prompt = "秒传文件"
                else:
                    prompt = "上传文件"
                console_print(f"""\
    [bold green][GOOD][/bold green] 📝 {prompt}: [blue underline]{src_path!r}[/blue underline] ➜ [blue underline]{name!r}[/blue underline] in {dst_pid}
    ├ response = {resp}""")
                update_success(1, 1, src_attr["size"])
                if remove_done:
                    try:
                        remove(src_path)
                    except OSError:
                        pass
                    try:
                        removedirs(dirname(src_path))
                    except OSError:
                        pass
            progress.update(statistics_bar, description=get_stat_str())
            success_tasks[src_path] = unfinished_tasks.pop(src_path)
        except BaseException as e:
            task.reasons.append(e)
            update_errors(e, src_attr["is_directory"])
            if max_retries < 0:
                status_code = get_status_code(e)
                if status_code:
                    retryable = status_code >= 500
                else:
                    retryable = isinstance(e, (RequestError, URLError, TimeoutError))
            else:
                retryable = task.times <= max_retries
            if retryable:
                console_print(f"""\
    [bold red][FAIL][/bold red] ♻️ 发生错误（将重试）: [blue underline]{src_path!r}[/blue underline] ➜ [blue underline]{name!r}[/blue underline] in {dst_pid}
    ├ [red]{type(e).__module__}.{type(e).__qualname__}[/red]: {e}""")
                update_retry(1, not src_attr["is_directory"])
                submit(task)
            else:
                console_print(f"""\
    [bold red][FAIL][/bold red] 💀 发生错误（将抛弃）: [blue underline]{src_path!r}[/blue underline] ➜ [blue underline]{name!r}[/blue underline] in {dst_pid}
    {indent(format_exc().strip(), "    ├ ")}""")
                progress.update(statistics_bar, description=get_stat_str())
                update_failed(1, not src_attr["is_directory"], src_attr.get("size"))
                failed_tasks[src_path] = unfinished_tasks.pop(src_path)
                if len(task.reasons) == 1:
                    raise
                else:
                    raise BaseExceptionGroup("max retries exceed", task.reasons)
    src_attr = get_path_attr(normpath(src_path))
    dst_attr: None | dict = None
    name: str = src_attr["name"]
    is_directory = src_attr["is_directory"]
    with Progress(
        SpinnerColumn(),
        *Progress.get_default_columns(),
        TimeElapsedColumn(),
        MofNCompleteColumn(),
        DownloadColumn(),
        FileSizeColumn(),
        TransferSpeedColumn(),
    ) as progress:
        console_print = lambda msg: progress.console.print(f"[bold][[cyan]{datetime.now()}[/cyan]][/bold]", msg)
        if isinstance(dst_path, str):
            if dst_path == "0" or pnormpath(dst_path) in ("", "/"):
                dst_pid = 0
                dst_path = "/" + name
            elif not dst_path.startswith("0") and dst_path.isascii() and dst_path.isdecimal():
                dst_pid = int(dst_path)
            elif is_directory:
                dst_attr = fs.makedirs(dst_path, pid=0, exist_ok=True)
                dst_pid = dst_attr["id"]
            elif with_root or path_is_dir_form(dst_path):
                dst_attr = fs.makedirs(dst_path, pid=0, exist_ok=True)
                dst_pid = dst_attr["id"]
                dst_path = dst_attr["path"] + "/" + name
            else:
                dst_path = pnormpath("/" + dst_path)
                dst_dir, dst_name = psplit(dst_path)
                try:
                    dst_attr = fs.attr(dst_path)
                except FileNotFoundError:
                    dst_attr = fs.makedirs(dst_dir, pid=0, exist_ok=True)
                    dst_pid = dst_attr["id"]
                    name = dst_name
                else:
                    if dst_attr["is_directory"]:
                        dst_pid = dst_attr["id"]
                        dst_path += "/" + name
                    else:
                        dst_pid = dst_attr["parent_id"]
                        name = dst_name
        else:
            dst_pid = dst_path
        if is_directory:
            if with_root and name:
                dst_attr = fs.makedirs(name, pid=dst_pid, exist_ok=True)
                dst_pid = dst_attr["id"]
            elif not dst_attr:
                dst_attr = fs.attr(dst_pid)
                if not dst_attr["is_directory"]:
                    raise NotADirectoryError(errno.ENOTDIR, dst_path)
            dst_path = dst_attr["path"]
        elif dst_pid and not dst_attr:
            dst_attr = fs.attr(dst_pid)
            if dst_attr["is_directory"]:
                dst_path = dst_attr["path"] + "/" + name
            else:
                dst_pid = dst_attr["parent_id"]
                dst_path = dst_attr["path"]
        task = Task(src_attr, dst_pid, None if is_directory else name)
        unfinished_tasks: dict[str, Task] = {src_attr["path"]: task}
        success_tasks: dict[str, Task] = {}
        failed_tasks: dict[str, Task] = {}
        all_tasks: Tasks = {
            "success": success_tasks, 
            "failed": failed_tasks, 
            "unfinished": unfinished_tasks, 
        }
        stats["src_path"] = src_attr["path"]
        stats["dst_path"] = dst_path
        update_tasks(1, not src_attr["is_directory"], src_attr.get("size"))
        get_stat_str = lambda: f"📊 [cyan bold]statistics[/cyan bold] 🧮 {tasks['total']} = 💯 {success['total']} + ⛔ {failed['total']} + ⏳ {unfinished['total']}"
        statistics_bar = progress.add_task(get_stat_str(), total=tasks["size"])
        closed = False
        try:
            thread_batch(work, unfinished_tasks.values(), max_workers=max_workers)
            stats["is_completed"] = True
        finally:
            closed = True
            progress.remove_task(statistics_bar)
            stats["elapsed"] = str(datetime.now() - start_time)
            console_print(f"📊 [cyan bold]statistics:[/cyan bold] {stats}")

    return Result(stats, all_tasks)

if __name__ == '__main__':
    from p115 import P115Client
    test_client = P115Client(Path("cookies.txt"), check_for_relogin=True, ensure_cookies=True, app="wechatmini")
    print(upload_files(test_client, 'test/', "/test"))
