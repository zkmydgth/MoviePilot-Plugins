import sys
import subprocess
import atexit
from functools import partial

from app.log import logger


class Pan115:
    """
    115 网盘 302 服务
    """

    def __init__(self, cookie):
        self.cookie = cookie

    def start(self, log_file_path):
        """
        302 服务启动
        """
        log_file = open(log_file_path, "w", encoding="utf-8")
        command = [
            sys.executable,
            "-c",
            "from uvicorn import run; "
            "from p115nano302 import make_application; "
            f"run(make_application('{self.cookie}', debug=True), host='0.0.0.0', port=29876, "
            "proxy_headers=True, server_header=False, forwarded_allow_ips='*', "
            "timeout_graceful_shutdown=1)",
        ]
        process = subprocess.Popen(
            command, stdout=log_file, stderr=log_file, start_new_session=True
        )
        logger.info("302 服务已启动")
        return process

    def cleanup_302_process(self, _process):
        """
        清理 302 服务进程
        """
        if _process is not None and _process.stdout is not None:
            _process.stdout.close()

    def stop(self, _process):
        """
        302 服务停止
        """
        atexit.register(partial(self.cleanup_302_process, _process))
        logger.info("302 服务已停止")
