import sqlite3
import os
import logging

from p115updatedb.query import iter_children, get_path, get_pickcode


class StrmHelper:
    """
    解析数据库，生成 STRM 文件
    """

    def __init__(self, db_name: str):
        self.connection = sqlite3.connect(db_name)
        self.path_list = []

    def get_video_file_path(self, parent_id: int):
        """
        获取视频文件路径
        """
        for attr in iter_children(self.connection, parent_id):
            if attr['is_dir'] == 1:
                self.get_video_file_path(attr['id'])
            else:
                path = get_path(self.connection, attr['id'])
                file_parent_id = attr['id']
                self.path_list.append([path, file_parent_id])
        return self.path_list

    def generate_strm_files(self, parent_id, target_dir, server_address, database="strm_db.sqlite"):
        """
        生成 STRM 文件并存储信息到 SQLite 数据库
        """
        rmt_mediaext = ['.mp4', '.mkv', '.ts', '.iso',
                        '.rmvb', '.avi', '.mov', '.mpeg',
                        '.mpg', '.wmv', '.3gp', '.asf',
                        '.m4v', '.flv', '.m2ts', '.tp',
                        '.f4v']

        if parent_id != 0:
            removal_path = get_path(self.connection, parent_id)
        else:
            removal_path = ''
        path_list = self.get_video_file_path(parent_id)

        target_dir = target_dir.rstrip("/")
        server_address = server_address.rstrip("/")

        conn = sqlite3.connect(database)
        cursor = conn.cursor()

        cursor.execute('''CREATE TABLE IF NOT EXISTS strm_files
                        (file_path TEXT, content TEXT)''')
        conn.commit()

        for file_path, file_parent_id in path_list:
            file_path = os.path.join(f"{target_dir}{file_path.replace(removal_path, '', 1)}")
            file_target_dir = os.path.dirname(file_path)
            original_file_name = os.path.basename(file_path)
            file_name = os.path.splitext(original_file_name)[0] + ".strm"
            new_file_path = os.path.join(file_target_dir, file_name)

            if os.path.splitext(original_file_name)[1] not in rmt_mediaext:
                logging.warning("跳过网盘路径： %s", file_path.replace(target_dir, '', 1))
                continue

            cursor.execute("SELECT 1 FROM strm_files WHERE file_path=?", (new_file_path,))
            if cursor.fetchone():
                logging.warning("跳过 %s", new_file_path)
                continue

            pickcode = get_pickcode(self.connection, file_parent_id)
            os.makedirs(os.path.dirname(new_file_path), exist_ok=True)

            content = f"{server_address}/{pickcode}/{original_file_name}"
            with open(new_file_path, 'w', encoding='utf-8') as file:
                file.write(content)

            cursor.execute('INSERT INTO strm_files VALUES (?,?)', (new_file_path, content))
            logging.info("生成 %s", new_file_path)
        conn.commit()
        conn.close()
