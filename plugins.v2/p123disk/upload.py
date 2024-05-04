import time
from collections.abc import (
    AsyncIterable,
    Buffer,
    Coroutine,
    Iterable,
)
from hashlib import md5
from os import fsdecode, fstat, PathLike
from os.path import basename
from platform import system
from tempfile import TemporaryFile
from typing import Any, Literal
from uuid import uuid4

from httpfile import HTTPFileReader
from hashtools import file_digest
from iterutils import run_gen_step
from filewrap import (
    bio_chunk_iter,
    bytes_iter_to_reader,
    copyfileobj,
    SupportsRead,
)
from http_request import SupportsGeturl
from yarl import URL

from p123 import check_response, buffer_length


# 当前的系统平台
SYS_PLATFORM = system()
# 替换表，用于半角转全角，包括了 Windows 中不允许出现在文件名中的字符
match SYS_PLATFORM:
    case "Windows":
        NAME_TANSTAB_FULLWIDH = {c: chr(c + 65248) for c in b"\\/:*?|><"}
    case "Darwin":
        NAME_TANSTAB_FULLWIDH = {ord("/"): ":", ord(":"): "："}
    case _:
        NAME_TANSTAB_FULLWIDH = {ord("/"): "／"}


def upload_file(
    client,
    /,
    file: (
        str
        | PathLike
        | URL
        | SupportsGeturl
        | Buffer
        | SupportsRead[Buffer]
        | Iterable[Buffer]
        | AsyncIterable[Buffer]
    ),
    file_md5: str = "",
    file_name: str = "",
    file_size: int = -1,
    parent_id: int = 0,
    duplicate: Literal[0, 1, 2] = 0,
    base_url: str = "",
    **request_kwargs,
) -> dict | Coroutine[Any, Any, dict]:
    """上传文件

    .. note::
        如果文件名中包含 Windows 文件名非法字符，则转换为对应的全角字符

    :param file: 待上传的文件

        - 如果为 `collections.abc.Buffer`，则作为二进制数据上传
        - 如果为 `filewrap.SupportsRead`，则作为可读的二进制文件上传
        - 如果为 `str` 或 `os.PathLike`，则视为路径，打开后作为文件上传
        - 如果为 `yarl.URL` 或 `http_request.SupportsGeturl` (`pip install python-http_request`)，则视为超链接，打开后作为文件上传
        - 如果为 `collections.abc.Iterable[collections.abc.Buffer]` 或 `collections.abc.AsyncIterable[collections.abc.Buffer]`，则迭代以获取二进制数据，逐步上传

    :param file_md5: 文件的 MD5 散列值
    :param file_name: 文件名
    :param file_size: 文件大小
    :param parent_id: 要上传的目标目录
    :param duplicate: 处理同名：0: 提示/忽略 1: 保留两者 2: 替换
    :param request_kwargs: 其它请求参数

    :return: 接口响应
    """

    def gen_step():
        nonlocal file, file_md5, file_name, file_size

        def do_upload(file):
            return upload_file(
                client,
                file=file,
                file_md5=file_md5,
                file_name=file_name,
                file_size=file_size,
                parent_id=parent_id,
                duplicate=duplicate,
                base_url=base_url,
                **request_kwargs,
            )

        try:
            file = getattr(file, "getbuffer")()
        except (AttributeError, TypeError):
            pass
        if isinstance(file, Buffer):
            file_size = buffer_length(file)
            if not file_md5:
                file_md5 = md5(file).hexdigest()
        elif isinstance(file, (str, PathLike)):
            path = fsdecode(file)
            if not file_name:
                file_name = basename(path)
            return do_upload(open(path, "rb"))
        elif isinstance(file, SupportsRead):
            seek = getattr(file, "seek", None)
            seekable = False
            curpos = 0
            if callable(seek):
                try:
                    seekable = getattr(file, "seekable")()
                except (AttributeError, TypeError):
                    try:
                        curpos = yield seek(0, 1)
                        seekable = True
                    except Exception:
                        seekable = False
            if not file_md5:
                if not seekable:
                    fsrc = file
                    file = TemporaryFile()
                    copyfileobj(fsrc, file)
                    file.seek(0)
                    return do_upload(file)
                try:
                    file_size, hashobj = file_digest(file)
                finally:
                    yield seek(curpos)
                file_md5 = hashobj.hexdigest()
            if file_size < 0:
                try:
                    fileno = getattr(file, "fileno")()
                    file_size = fstat(fileno).st_size - curpos
                except (AttributeError, TypeError, OSError):
                    try:
                        file_size = len(file) - curpos  # type: ignore
                    except TypeError:
                        if seekable:
                            try:
                                file_size = (yield seek(0, 2)) - curpos
                            finally:
                                yield seek(curpos)
        elif isinstance(file, (URL, SupportsGeturl)):
            if isinstance(file, URL):
                url = str(file)
            else:
                url = file.geturl()

            with HTTPFileReader(url) as file:
                return do_upload(file)
        elif not file_md5 or file_size < 0:
            file = bytes_iter_to_reader(file)  # type: ignore
            return do_upload(file)
        if not file_name:
            file_name = getattr(file, "name", "")
            file_name = basename(file_name)
        if file_name:
            file_name = file_name.translate(NAME_TANSTAB_FULLWIDH)
        else:
            file_name = str(uuid4())
        if file_size < 0:
            file_size = getattr(file, "length", 0)
        resp = yield client.upload_request(
            {
                "etag": file_md5,
                "fileName": file_name,
                "size": file_size,
                "parentFileId": parent_id,
                "type": 0,
                "duplicate": duplicate,
            },
            base_url=base_url,
            **request_kwargs,
        )
        if resp.get("code", 0) not in (0, 200):
            return resp
        upload_data = resp["data"]
        if upload_data["Reuse"]:
            return resp
        slice_size = int(upload_data["SliceSize"])
        upload_request_kwargs = {
            **request_kwargs,
            "timeout": 300,
            "method": "PUT",
            "headers": {"authorization": ""},
            "parse": ...,
        }
        if file_size > slice_size:
            upload_data["partNumberStart"] = 1
            q, r = divmod(file_size, slice_size)
            total_parts = q + (1 if r > 0 else 0)

            current_part = 1
            last_refresh_time = 0
            current_batch_size = 5
            urls = {}
            chunks = bio_chunk_iter(file, chunksize=slice_size)

            while current_part <= total_parts:
                if (
                    not urls
                    or current_part > max(urls.keys())
                    or time.time() - last_refresh_time > 600
                ):
                    batch_end = min(current_part + current_batch_size, total_parts + 1)

                    upload_data["partNumberStart"] = current_part
                    upload_data["partNumberEnd"] = batch_end
                    resp = client.upload_prepare_parts(
                        upload_data,
                        base_url=base_url,
                        **request_kwargs,
                    )
                    check_response(resp)

                    urls = {int(k): v for k, v in resp["data"]["presignedUrls"].items()}
                    last_refresh_time = time.time()

                    time_remaining = 600 - (time.time() - last_refresh_time)
                    if time_remaining < 120:
                        current_batch_size = max(1, min(3, current_batch_size // 2))

                # 上传当前part
                chunk = next(chunks)
                client.request(urls[current_part], data=chunk, **upload_request_kwargs)
                current_part += 1
        else:
            resp = yield client.upload_auth(
                upload_data,
                base_url=base_url,
                **request_kwargs,
            )
            check_response(resp)
            url = resp["data"]["presignedUrls"]["1"]
            yield client.request(url, data=file, **upload_request_kwargs)
        upload_data["isMultipart"] = file_size > slice_size
        return client.upload_complete(
            upload_data,
            base_url=base_url,
            **request_kwargs,
        )

    return run_gen_step(gen_step, async_=False)
