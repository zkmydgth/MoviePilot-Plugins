"""宿主桩：``app.runtime.config.settings``。"""


class _StubSettings:
    """设置桩：只提供插件会读的字段。"""

    # 插件详情页按钮的 params 里会带它
    API_TOKEN = "stub-api-token"
    # 代理设置（桩默认空）
    PROXY_HOST = ""


settings = _StubSettings()
