class PanPathNotFound(FileNotFoundError):
    """
    网盘路径不存在
    """


class U115NoCheckInException(Exception):
    """
    115 Open 未登录
    """


class PanDataNotInDb(Exception):
    """
    网盘数据未在数据库内
    """


class CanNotFindPathToCid(Exception):
    """
    无法找到路径对应的 cid
    """


class PathNotInKey(ValueError):
    """
    键中不包含 Path 项
    """
