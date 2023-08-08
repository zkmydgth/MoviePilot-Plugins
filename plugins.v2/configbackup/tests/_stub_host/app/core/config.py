# -*- coding: utf-8 -*-
"""宿主桩：app.core.config.settings。"""


class _Settings:
    """
    可写的配置桩。

    测试中按需覆盖属性（如 DB_TYPE、TEMP_PATH、CONFIG_PATH），
    默认值刻意取"非 PostgreSQL"，让数据库相关分支默认跳过，
    避免测试依赖真实数据库。
    """

    API_TOKEN = "test-token"
    TEMP_PATH = "/tmp"
    CONFIG_PATH = "/tmp"
    DB_TYPE = "sqlite"
    DB_POSTGRESQL_HOST = ""
    DB_POSTGRESQL_PORT = 5432
    DB_POSTGRESQL_USERNAME = ""
    DB_POSTGRESQL_PASSWORD = ""
    DB_POSTGRESQL_DATABASE = ""


settings = _Settings()
