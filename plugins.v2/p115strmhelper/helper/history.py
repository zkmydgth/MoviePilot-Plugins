from p115client.tool.history import iter_history_once


class MonitorHistory:
    """
    监控历史事件

    {
        2: "offline_download",  离线下载
        3: "browse_video",      浏览视频 无操作
        4: "upload",            上传文件 无操作
        7: "receive",           接收文件 无操作
        8: "move",              移动文件 无操作
    }
    """
