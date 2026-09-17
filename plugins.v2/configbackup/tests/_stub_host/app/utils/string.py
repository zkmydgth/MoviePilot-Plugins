# -*- coding: utf-8 -*-
"""宿主桩：app.utils.string.StringUtils。"""


class StringUtils:
    """字符串工具桩：只实现插件用到的 str_filesize。"""

    @staticmethod
    def str_filesize(size: int) -> str:
        """把字节数格式化为易读字符串（与 MP 行为近似）。"""
        try:
            value = float(size)
        except (TypeError, ValueError):
            return "0 B"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{value:.1f} TB"
