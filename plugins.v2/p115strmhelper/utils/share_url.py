__all__ = [
    "ALIYUN_SHARE_URL_MATCH",
    "ShareUrlUtils",
    "U115_SHARE_URL_MATCH",
]


from re import compile as re_compile, finditer, match
from typing import Optional


U115_SHARE_URL_MATCH = r"^https?://(.*\.)?115[^/]*\.[a-zA-Z]{2,}(?:/|$)"
ALIYUN_SHARE_URL_MATCH = r"^https?://(.*\.)?(alipan|aliyundrive)\.[a-zA-Z]{2,}(?:/|$)"

_HTTPS_TOKEN = re_compile(r"https?://[^\s)]+")


class ShareUrlUtils:
    """
    分享链接 URL 解析工具
    """

    _TRAILING_JUNK = frozenset(".,;，。；、）)」』\"'")

    @staticmethod
    def _normalize_candidate(raw: str) -> str:
        """
        去掉 URL 末尾可能被一并匹配到的标点或全角括号

        :param raw: 原始子串
        :return: 规范化后的字符串
        """
        s = raw.strip()
        while s and s[-1] in ShareUrlUtils._TRAILING_JUNK:
            s = s[:-1].rstrip()
        return s

    @staticmethod
    def extract_share_url_from_text(text: str) -> Optional[str]:
        """
        从整段消息文本中提取第一条 115 或阿里云分享链接

        :param text: 用户消息全文
        :return: 合法分享 URL，否则 None
        """
        if not text or not isinstance(text, str):
            return None

        for m in finditer(_HTTPS_TOKEN, text):
            cand = ShareUrlUtils._normalize_candidate(m.group(0))
            if not cand:
                continue
            if bool(match(U115_SHARE_URL_MATCH, cand)) or bool(
                match(ALIYUN_SHARE_URL_MATCH, cand)
            ):
                return cand
        return None
