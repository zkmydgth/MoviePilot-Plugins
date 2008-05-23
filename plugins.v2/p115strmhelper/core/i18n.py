import json

from app.core.config import settings


class I18N:
    """
    国际化
    """

    def __init__(self, locale="zh_CN"):
        self.locale = locale
        self.translations = {}
        self.load_translations()

    def load_translations(self):
        """
        加载语言文件
        """
        locales_dir = settings.ROOT_PATH / "app/plugins" / "p115strmhelper" / "locales"
        try:
            with open(locales_dir / f"{self.locale}.json", "r", encoding="utf-8") as f:
                self.translations = json.load(f)
        except FileNotFoundError:
            self.translations = {}

    def translate(self, key, default=None):
        """
        输出语言
        """
        return self.translations.get(key, default or key)
