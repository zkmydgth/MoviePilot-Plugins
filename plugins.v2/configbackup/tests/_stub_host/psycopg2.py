# -*- coding: utf-8 -*-
"""
宿主桩：psycopg2。

ConfigBackup 在模块级 ``import psycopg2``（实际只在数据库备份/还原时用到）。
为让测试无需安装真实驱动，这里提供一个最小替身：

- ``connect()`` 默认抛异常，确保**任何未预期的数据库调用都会显式失败**，
  而不是静默返回假连接导致测试误判。
- 测试若需覆盖数据库分支，可用 ``mock.patch`` 替换 ``connect``。
"""


class Error(Exception):
    """psycopg2.Error 替身。"""


class OperationalError(Error):
    """连接类错误替身。"""


def connect(*args, **kwargs):  # noqa: D103
    raise OperationalError(
        "psycopg2 桩：未提供真实数据库连接。"
        "测试若需覆盖数据库分支，请 mock.patch('psycopg2.connect')。"
    )
