import argparse
from pathlib import Path

from p115client import P115Client
from p115updatedb import updatedb


def generate_file_list_db(cookies, dbfile, media_path):
    """
    文件列表导出到数据库
    """
    client = P115Client(cookies, check_for_relogin=True, ensure_cookies=True)
    updatedb(
        client,
        dbfile=dbfile,
        top_dirs=media_path,
        interval=2,
        no_dir_moved=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P115 Update DB")
    parser.add_argument("--cookies", type=str, required=True, help="Cookie Path")
    parser.add_argument("--dbfile_path", type=str, required=True, help="DB File Path")
    parser.add_argument("--media_path", type=str, required=True, help="Media Path")
    args = parser.parse_args()
    generate_file_list_db(args.cookies, args.dbfile_path, str(Path(args.media_path)))
