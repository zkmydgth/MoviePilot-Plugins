import sqlite3
from pathlib import Path

from p115updatedb.query import iter_children, get_path, get_pickcode, id_to_path
from p115updatedb import updatedb
from ...db_manager.u115strmfiles_oper import U115StrmFilesOper

from app.log import logger


class U115StrmHelper:
    """
    解析数据库，生成 STRM 文件
    """

    def __init__(self, dbfile: str, client):
        self.dbfile = dbfile
        self.connection = sqlite3.connect(dbfile)
        self.client = client
        self.path_list = []
        self.rmt_mediaext = [
            ".mp4",
            ".mkv",
            ".ts",
            ".iso",
            ".rmvb",
            ".avi",
            ".mov",
            ".mpeg",
            ".mpg",
            ".wmv",
            ".3gp",
            ".asf",
            ".m4v",
            ".flv",
            ".m2ts",
            ".tp",
            ".f4v",
        ]

    def generate_file_list_db(self):
        """
        文件列表导出到数据库
        """
        updatedb(
            self.client,
            dbfile=self.dbfile,
            top_dirs=0,
        )

    def get_id_by_path(self, path):
        """
        通过文件夹路径获取文件夹 ID
        """
        return id_to_path(self.connection, path, False)

    def get_video_file_path(self, parent_id: int):
        """
        获取视频文件路径
        """
        for attr in iter_children(self.connection, parent_id):
            if attr["is_dir"] == 1:
                self.get_video_file_path(attr["id"])
            else:
                path = get_path(self.connection, attr["id"])
                file_parent_id = attr["id"]
                self.path_list.append([path, file_parent_id])
        return self.path_list

    def generate_strm_files_db(self, parent_id, target_dir, server_address):
        """
        依据数据库生成 STRM 文件
        """

        if parent_id != 0:
            removal_path = get_path(self.connection, parent_id)
        else:
            removal_path = ""
        path_list = self.get_video_file_path(parent_id)

        target_dir = target_dir.rstrip("/")
        server_address = server_address.rstrip("/")

        strm_oper = U115StrmFilesOper()

        for file_path, file_parent_id in path_list:
            file_path = Path(target_dir) / Path(file_path).relative_to(removal_path)
            file_target_dir = file_path.parent
            original_file_name = file_path.name
            file_name = file_path.stem + ".strm"
            new_file_path = file_target_dir / file_name

            if file_path.suffix not in self.rmt_mediaext:
                logger.warn(
                    "跳过网盘路径： %s", str(file_path).replace(str(target_dir), "", 1)
                )
                continue

            if strm_oper.get_by_path(str(new_file_path)):
                logger.warn("跳过 %s", str(new_file_path))
                continue

            pickcode = get_pickcode(self.connection, file_parent_id)
            new_file_path.parent.mkdir(parents=True, exist_ok=True)

            content = f"{server_address}/{pickcode}/{original_file_name}"
            with open(new_file_path, "w", encoding="utf-8") as file:
                file.write(content)
            strm_oper.add(file_path=str(new_file_path), content=content)
            logger.info("生成 %s", str(new_file_path))

    def generate_strm_files(self, pan_path, target_dir, pickcode, server_address):
        """
        依据网盘路径生成 STRM 文件
        """

        target_dir = target_dir.rstrip("/")
        server_address = server_address.rstrip("/")

        strm_oper = U115StrmFilesOper()

        file_path = Path(target_dir) / Path(pan_path)
        file_target_dir = file_path.parent
        original_file_name = file_path.name
        file_name = file_path.stem + ".strm"
        new_file_path = file_target_dir / file_name

        if file_path.suffix not in self.rmt_mediaext:
            logger.warn("跳过网盘路径： %s", pan_path)
            return

        if strm_oper.get_by_path(str(new_file_path)):
            logger.warn("跳过 %s", str(new_file_path))
            return

        new_file_path.parent.mkdir(parents=True, exist_ok=True)

        content = f"{server_address}/{pickcode}/{original_file_name}"
        with open(new_file_path, "w", encoding="utf-8") as file:
            file.write(content)

        strm_oper.add(file_path=str(new_file_path), content=content)
        logger.info("生成 %s", str(new_file_path))
