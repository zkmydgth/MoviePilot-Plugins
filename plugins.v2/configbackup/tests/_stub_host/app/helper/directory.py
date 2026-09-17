# -*- coding: utf-8 -*-
"""宿主桩：app.helper.directory.DirectoryHelper。

ConfigBackup 只用它列出「下载目录」候选（作为备份目录下拉项），
测试中返回空列表即可，不参与本次改动逻辑。
"""


class DirectoryHelper:
    """目录助手桩。"""

    @staticmethod
    def get_download_dirs():
        return []

    @staticmethod
    def get_library_dirs():
        return []
