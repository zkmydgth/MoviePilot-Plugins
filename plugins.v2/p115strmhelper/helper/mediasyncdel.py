from typing import List

import jieba

from app.db import get_db
from app.db.models.transferhistory import TransferHistory
from app.db.downloadhistory_oper import DownloadHistoryOper


class MediaSyncDelHelper:
    """
    媒体文件同步删除
    """

    @staticmethod
    def get_transfer_his_by_path_title(path: str) -> List[TransferHistory]:
        """
        通过路径查询转移记录
        所有匹配项
        """
        words = jieba.cut(path, HMM=False)
        title = "%".join(words)

        db_generator = get_db()
        db_session = next(db_generator)

        try:
            total = TransferHistory.count_by_title(db_session, title=title)
            result = TransferHistory.list_by_title(
                db_session, title=title, page=1, count=total
            )
        finally:
            try:
                next(db_generator)
            except StopIteration:
                pass
        return result
