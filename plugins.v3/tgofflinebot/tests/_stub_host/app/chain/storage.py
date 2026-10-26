# -*- coding: utf-8 -*-
"""
``StorageChain`` 替身：目录列举与删除的最小实现。
"""

from typing import Any, List, Optional


class StorageChain:
    """存储链替身。"""

    def get_folder(self, storage: str, path: Any) -> Optional[dict]:
        """
        返回目录项替身。

        :param storage: 存储类型
        :param path: 目录路径
        :return: 目录项
        """
        return {"path": str(path), "type": "dir", "storage": storage}

    def get_parent_item(self, fileitem: Any) -> Optional[dict]:
        """
        返回父目录替身。

        :param fileitem: 文件项
        :return: 目录项
        """
        return {"path": "/", "type": "dir"}

    def list_files(self, fileitem: Any, recursion: bool = False) -> List[Any]:
        """
        列举目录内容，替身固定返回空列表。

        :param fileitem: 目录项
        :param recursion: 是否递归
        :return: 空列表
        """
        return []

    def delete_file(self, fileitem: Any) -> bool:
        """
        删除文件或目录，替身固定成功。

        :param fileitem: 文件项
        :return: True
        """
        return True
