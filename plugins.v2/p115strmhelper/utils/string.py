__all__ = ["StringUtils"]


from re import sub as re_sub
from urllib.parse import urlparse, urlunparse, quote, parse_qs, urlencode

from ..core.i18n import i18n


class StringUtils:
    """
    类型转换辅助类
    """

    @staticmethod
    def format_size(size: float, precision: int = 2) -> str:
        """
        字节数转换
        """
        if not isinstance(size, (int, float)) or size < 0:
            return "N/A"
        suffixes = ["B", "KB", "MB", "GB", "TB"]
        suffix_index = 0
        while size >= 1024 and suffix_index < 4:
            suffix_index += 1
            size /= 1024.0
        return f"{size:.{precision}f} {suffixes[suffix_index]}"

    @staticmethod
    def to_emoji_number(n: int) -> str:
        """
        将一个整数转换为对应的带圈数字 Emoji 字符串 (例如 ①, ②, ⑩)。
        """
        if not isinstance(n, int):
            return "❓"
        if n == 10:
            return "⑩"
        emoji_map = {
            "0": "⓪",
            "1": "①",
            "2": "②",
            "3": "③",
            "4": "④",
            "5": "⑤",
            "6": "⑥",
            "7": "⑦",
            "8": "⑧",
            "9": "⑨",
        }
        return "".join(emoji_map.get(digit, digit) for digit in str(n))

    @staticmethod
    def replace_markdown_with_space(text: str) -> str:
        """
        将字符串中所有常见的 Markdown 特殊字符替换为空格

        :param text: 需要处理的带md特殊字符的文案
        """
        if not isinstance(text, str):
            return ""

        # 需要处理的字符串，必须字符：` * [ ]
        md_chars_list = ["*", "[", "]", "`", "."]

        # 修剪特殊字符
        for char in md_chars_list:
            if char == ".":
                text = text.replace(char, "·")
            else:
                text = text.replace(char, " ")

        # 整合连续空格
        normalized_text = re_sub(r"\s+", " ", text)

        return normalized_text.strip()

    @staticmethod
    def media_type_i18n(media_type: str | None) -> str:
        """
        媒体类型
        """
        if media_type is None:
            return "未知类型"
        raw = str(media_type).strip()
        if not raw:
            return "未知类型"
        low = raw.lower()

        if low == "movie" or raw == "电影":
            return i18n.translate("media_type_movie")
        if low == "tv" or raw == "电视剧":
            return i18n.translate("media_type_tv")
        if low == "collection" or raw == "系列":
            return i18n.translate("media_type_collection")
        if low == "unknown" or raw == "未知":
            return "未知类型"

        return raw

    @staticmethod
    def _extract_year_from_media_item(item: dict) -> str:
        """
        从 MoviePilot ``MediaInfo.to_dict()`` 结果中取展示用年份
        """
        y = item.get("year")
        if y is not None and str(y).strip():
            s = str(y).strip()
            if len(s) >= 4 and s[:4].isdigit():
                return s[:4]
            return s
        for key in ("release_date", "first_air_date"):
            d = item.get(key)
            if isinstance(d, str) and len(d) >= 4 and d[:4].isdigit():
                return d[:4]
        return ""

    @staticmethod
    def format_sh_search_media_line(index: int, item: dict) -> str:
        """
        媒体列表单行：带圈序号、类型、【】、标题、年份与评分

        类型文案与原先列表一致，走 ``media_type_*`` 的 i18n（如猫娘语言包）
        """
        emoji = StringUtils.to_emoji_number(index)
        raw_type = item.get("type") if isinstance(item, dict) else None
        bracket = StringUtils.media_type_i18n(raw_type)
        title = (
            (item.get("title") or "未知").strip() if isinstance(item, dict) else "未知"
        )
        title_part = StringUtils.replace_markdown_with_space(text=title)
        year = (
            StringUtils._extract_year_from_media_item(item)
            if isinstance(item, dict)
            else ""
        )
        vote = item.get("vote_average") if isinstance(item, dict) else None
        score_str = f"{float(vote):.1f}" if isinstance(vote, (int, float)) else "0.0"
        year_part = year if year else "—"
        return f"{emoji} 【{bracket}】{title_part}（{year_part} / ⭐️ {score_str}）"

    @staticmethod
    def encode_url_fully(url: str) -> str:
        """
        编码标准 URL
        """
        try:
            parsed_url = urlparse(url)
            encoded_path = quote(parsed_url.path, safe="/")
            query_dict = parse_qs(parsed_url.query, keep_blank_values=True)
            encoded_query = urlencode(query_dict, doseq=True)
            encoded_fragment = quote(parsed_url.fragment)
            encoded_url_parts = (
                parsed_url.scheme,
                parsed_url.netloc,
                encoded_path,
                parsed_url.params,
                encoded_query,
                encoded_fragment,
            )
            return urlunparse(encoded_url_parts)
        except Exception:
            return url
