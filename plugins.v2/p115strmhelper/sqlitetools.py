#!/usr/bin/env python3
# encoding: utf-8

__author__ = "ChenyangGao <https://chenyanggao.github.io>"


from typing_extensions import Buffer
from collections.abc import Callable, Iterable, Sequence
from enum import Enum
from re import compile as re_compile, IGNORECASE
from sqlite3 import Connection, Cursor
from typing import Any, Final, Self


CRE_SELECT_SQL_match: Final = re_compile(r"\s*SELECT\b", IGNORECASE).match
CRE_COLNAME_sub: Final = re_compile(r" \[[^]]+\]$").sub


class FetchType(Enum):
    auto = 0
    one = 1
    any = 2
    dict = 3

    @classmethod
    def ensure(cls, val, /) -> Self:
        if isinstance(val, cls):
            return val
        if isinstance(val, str):
            try:
                return cls[val]
            except KeyError:
                pass
        return cls(val)


class AutoCloseCursor(Cursor):
    """会自动关闭 Cursor"""

    def __del__(self, /):
        self.close()


def execute(
    con: Connection | Cursor,
    /,
    sql: str,
    params: Any = None,
    executemany: bool = False,
    commit: bool = False,
) -> Cursor:
    """执行一个 sql 语句

    :param con: 数据库连接或游标
    :param sql: sql 语句
    :param params: 参数，用于填充 sql 中的占位符，会根据具体情况选择使用 execute 或 executemany
    :param executemany: 强制使用 executemany
    :param commit: 是否在执行成功后进行 commit

    :return: 游标
    """
    if isinstance(con, Connection):
        cur: Cursor = con.cursor(factory=AutoCloseCursor)
    else:
        cur = con
        con = cur.connection
    is_iter = lambda x: isinstance(x, Iterable) and not isinstance(x, (str, Buffer))
    if executemany:
        cur.executemany(sql, params)
    elif params is None:
        cur.execute(sql)
    elif isinstance(params, (tuple, dict)):
        cur.execute(sql, params)
    elif is_iter(params):
        if not isinstance(params, Sequence) or not all(map(is_iter, params)):
            params = (e if is_iter(e) else (e,) for e in params)
        cur.executemany(sql, params)
    else:
        cur.execute(sql, (params,))
    if commit and con.autocommit != 1 and CRE_SELECT_SQL_match(sql) is None:
        con.commit()
    return cur


def query(
    con: Connection | Cursor,
    /,
    sql: str,
    params: Any = None,
    row_factory: None | int | str | FetchType | Callable[[Cursor, Any], Any] = None,
) -> Cursor:
    """执行一个 sql 查询语句，或者 DML 语句但有 RETURNING 子句（但不会主动 commit）

    :param con: 数据库连接或游标
    :param sql: sql 语句
    :param params: 参数，用于填充 sql 中的占位符
    :param row_factory: 对数据进行处理，然后返回处理后的值

        - 如果是 Callable，则调用然后返回它的值
        - 如果是 FetchType.auto，则当数据是 tuple 且长度为 1 时，返回第 1 个为位置的值，否则返回数据本身
        - 如果是 FetchType.any，则返回数据本身
        - 如果是 FetchType.one，则返回数据中第 1 个位置的值（索引为 0）
        - 如果是 FetchType.dict，则返回字典，键从游标中获取

    :return: 游标
    """
    cursor = execute(con, sql, params)
    if row_factory is not None:
        if callable(row_factory):
            cursor.row_factory = row_factory
        else:
            match FetchType.ensure(row_factory):
                case FetchType.auto:

                    def row_factory(_, record):
                        if isinstance(record, tuple) and len(record) == 1:
                            return record[0]
                        return record

                    cursor.row_factory = row_factory
                case FetchType.one:
                    cursor.row_factory = lambda _, record: record[0]
                case FetchType.dict:
                    fields = tuple(
                        CRE_COLNAME_sub("", f[0]) for f in cursor.description
                    )
                    cursor.row_factory = lambda _, record: dict(zip(fields, record))
    return cursor


def find(
    con: Connection | Cursor,
    /,
    sql: str,
    params: Any = None,
    default: Any = None,
    row_factory: int | str | FetchType | Callable[[Cursor, Any], Any] = "auto",
) -> Any:
    """执行一个 sql 查询语句，或者 DML 语句但有 RETURNING 子句（但不会主动 commit），返回一条数据

    :param con: 数据库连接或游标
    :param sql: sql 语句
    :param params: 参数，用于填充 sql 中的占位符
    :param default: 当没有数据返回时，作为默认值返回，如果是异常对象，则进行抛出
    :param row_factory: 对数据进行处理，然后返回处理后的值

        - 如果是 Callable，则调用然后返回它的值
        - 如果是 FetchType.auto，则当数据是 tuple 且长度为 1 时，返回第 1 个为位置的值，否则返回数据本身
        - 如果是 FetchType.any，则返回数据本身
        - 如果是 FetchType.one，则返回数据中第 1 个位置的值（索引为 0）
        - 如果是 FetchType.dict，则返回字典，键从游标中获取

    :return: 查询结果的第一条数据
    """
    cursor = query(con, sql, params)
    record = cursor.fetchone()
    cursor.close()
    if record is None:
        if isinstance(default, BaseException):
            raise default
        return default
    if callable(row_factory):
        return row_factory(cursor, record)
    else:
        match FetchType.ensure(row_factory):
            case FetchType.auto:
                if isinstance(record, tuple) and len(record) == 1:
                    return record[0]
                return record
            case FetchType.one:
                return record[0]
            case FetchType.dict:
                return dict(
                    zip((CRE_COLNAME_sub("", f[0]) for f in cursor.description), record)
                )
            case _:
                return record
