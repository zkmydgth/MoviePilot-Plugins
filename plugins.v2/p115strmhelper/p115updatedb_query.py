#!/usr/bin/env python3
# encoding: utf-8

__author__ = "ChenyangGao <https://chenyanggao.github.io>"


from collections.abc import Iterator, Sequence
from datetime import datetime
from errno import ENOENT, ENOTDIR
from sqlite3 import Connection, Cursor
from typing import Final

from posixpatht import escape, path_is_dir_form, splits
from .sqlitetools import find, query


FIELDS: Final = (
    "id",
    "parent_id",
    "pickcode",
    "sha1",
    "name",
    "size",
    "is_dir",
    "type",
    "ctime",
    "mtime",
    "is_collect",
    "is_alive",
    "updated_at",
)


def get_attr(
    con: Connection | Cursor,
    id: int = 0,
    /,
) -> dict:
    """获取某个文件或目录的信息

    :param con: 数据库连接或游标
    :param id: 当前节点的 id

    :return: 当前节点的信息字典
    """
    if not id:
        return {
            "id": 0,
            "parent_id": 0,
            "pickcode": "",
            "sha1": "",
            "name": "",
            "size": 0,
            "is_dir": 1,
            "type": 0,
            "ctime": 0,
            "mtime": 0,
            "is_collect": 0,
            "is_alive": 1,
            "updated_at": datetime.fromtimestamp(0),
        }
    return find(
        con,
        f"SELECT {','.join(FIELDS)} FROM data WHERE id=? LIMIT 1",
        id,
        FileNotFoundError(ENOENT, id),
        row_factory="dict",
    )


def iter_id_to_path(
    con: Connection | Cursor,
    /,
    path: str | Sequence[str] = "",
    ensure_file: None | bool = None,
    parent_id: int = 0,
) -> Iterator[int]:
    """查询匹配某个路径的文件或目录的信息字典

    .. note::
        同一个路径可以有多条对应的数据

    :param con: 数据库连接或游标
    :param path: 路径
    :param ensure_file: 是否文件

        - 如果为 True，必须是文件
        - 如果为 False，必须是目录
        - 如果为 None，可以是文件或目录

    :param parent_id: 顶层目录的 id

    :return: 迭代器，产生一组匹配指定路径的（文件或目录）节点的 id
    """
    patht: Sequence[str]
    if isinstance(path, str):
        if ensure_file is None and path_is_dir_form(path):
            ensure_file = False
        patht, _ = splits("/" + path)
    else:
        patht = ("", *filter(None, path))
    if not parent_id and len(patht) == 1:
        return iter((0,))
    if len(patht) > 2:
        sql = "SELECT id FROM data WHERE parent_id=? AND name=? AND is_alive AND is_dir LIMIT 1"
        for name in patht[1:-1]:
            parent_id = find(con, sql, (parent_id, name), default=-1)
            if parent_id < 0:
                return iter(())
    sql = "SELECT id FROM data WHERE parent_id=? AND name=? AND is_alive"
    if ensure_file is None:
        sql += " ORDER BY is_dir DESC"
    elif ensure_file:
        sql += " AND NOT is_dir"
    else:
        sql += " AND is_dir LIMIT 1"
    return query(con, sql, (parent_id, patht[-1]), row_factory="one")


def id_to_path(
    con: Connection | Cursor,
    /,
    path: str | Sequence[str] = "",
    ensure_file: None | bool = None,
    parent_id: int = 0,
) -> int:
    """查询匹配某个路径的文件或目录的信息字典，只返回找到的第 1 个

    :param con: 数据库连接或游标
    :param path: 路径
    :param ensure_file: 是否文件

        - 如果为 True，必须是文件
        - 如果为 False，必须是目录
        - 如果为 None，可以是文件或目录

    :param parent_id: 顶层目录的 id

    :return: 找到的第 1 个匹配的节点 id
    """
    try:
        return next(iter_id_to_path(con, path, ensure_file, parent_id))
    except StopIteration:
        raise FileNotFoundError(ENOENT, path) from None


def iter_children(
    con: Connection | Cursor,
    parent_id: int | dict = 0,
    /,
    ensure_file: None | bool = None,
) -> Iterator[dict]:
    """获取某个目录之下的文件或目录的信息

    :param con: 数据库连接或游标
    :param parent_id: 父目录的 id
    :param ensure_file: 是否仅输出文件

        - 如果为 True，仅输出文件
        - 如果为 False，仅输出目录
        - 如果为 None，全部输出

    :return: 迭代器，产生一组信息的字典
    """
    if isinstance(parent_id, int):
        attr = get_attr(con, parent_id)
    else:
        attr = parent_id
    if not attr["is_dir"]:
        raise NotADirectoryError(ENOTDIR, attr)
    sql = f"SELECT {','.join(FIELDS)} FROM data WHERE parent_id=? AND is_alive"
    if ensure_file is not None:
        if ensure_file:
            sql += " AND NOT is_dir"
        else:
            sql += " AND is_dir"
    return query(con, sql, attr["id"], row_factory="dict")


def get_path(
    con: Connection | Cursor,
    id: int = 0,
    /,
) -> str:
    """获取某个文件或目录的路径

    :param con: 数据库连接或游标
    :param id: 当前节点的 id

    :return: 当前节点的路径
    """
    if not id:
        return "/"
    ancestors = get_ancestors(con, id)
    return "/".join(escape(a["name"]) for a in ancestors)


def get_ancestors(
    con: Connection | Cursor,
    id: int = 0,
    /,
) -> list[dict]:
    """获取某个文件或目录的祖先节点信息，包括 id、parent_id 和 name

    :param con: 数据库连接或游标
    :param id: 当前节点的 id

    :return: 当前节点的祖先节点列表，从根目录开始（id 为 0）直到当前节点
    """
    ancestors = [{"id": 0, "parent_id": 0, "name": ""}]
    if not id:
        return ancestors
    ls = list(
        query(
            con,
            """\
WITH t AS (
    SELECT id, parent_id, name FROM data WHERE id = ?
    UNION ALL
    SELECT data.id, data.parent_id, data.name FROM t JOIN data ON (t.parent_id = data.id)
)
SELECT id, parent_id, name FROM t;""",
            id,
        )
    )
    if not ls:
        raise FileNotFoundError(ENOENT, id)
    if ls[-1][1]:
        raise ValueError(f"dangling id: {id}")
    ancestors.extend(
        dict(zip(("id", "parent_id", "name"), record)) for record in reversed(ls)
    )
    return ancestors


def get_pickcode(
    con: Connection | Cursor,
    /,
    id: int = -1,
    sha1: str = "",
    path: str = "",
    is_alive: bool = True,
) -> str:
    """查询匹配某个字段的文件或目录的提取码

    :param con: 数据库连接或游标
    :param id: 当前节点的 id，优先级高于 sha1
    :param sha1: 当前节点的 sha1 校验散列值，优先级高于 path
    :param path: 当前节点的路径

    :return: 当前节点的提取码
    """
    insertion = " AND is_alive" if is_alive else ""
    if id >= 0:
        if not id:
            return ""
        return find(
            con,
            f"SELECT pickcode FROM data WHERE id=?{insertion} LIMIT 1;",
            id,
            default=FileNotFoundError(id),
        )
    elif sha1:
        return find(
            con,
            f"SELECT pickcode FROM data WHERE sha1=?{insertion} LIMIT 1;",
            sha1,
            default=FileNotFoundError(sha1),
        )
    else:
        if path in ("", "/"):
            return ""
        return get_pickcode(con, id_to_path(con, path))
