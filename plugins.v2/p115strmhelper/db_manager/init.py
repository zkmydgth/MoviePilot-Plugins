import json
import shutil
from pathlib import Path

from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.log import logger

from ..db_manager import P115StrmHelperBase

# 显式载入，确保表单模型已注册
from ..db_manager.models import *  # noqa


def init_db(engine):
    """
    初始化数据库
    """
    if not engine:
        raise SQLAlchemyError("数据库引擎获取失败")
    # 全量建表
    P115StrmHelperBase.metadata.create_all(engine)


def migration_db(db_path, script_location, version_locations: list):
    """
    更新数据库
    """
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(script_location))
    # 启动时的更新，调整从 /config/plugins/p115strmhelper/database/versions 中读取持久化的迁移脚本
    alembic_cfg.set_main_option("version_locations", str(" ".join(version_locations)))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    with open('app/plugins/p115strmhelper/database/migration_meta.json', 'r', encoding='utf-8') as f:
        config_data = json.load(f)
        revision = config_data.get("revision", 'head')

    upgrade(alembic_cfg, revision)


def init_migration_scripts() -> bool:
    """
    初始化持久化的迁移脚本，将源目录内容完整复制到目标目录。
    """
    # 使用 Path 对象定义路径
    source_path = Path(settings.ROOT_PATH / 'app/plugins/p115strmhelper/database/versions')
    target_path = Path(settings.PLUGIN_DATA_PATH / 'p115strmhelper' / 'database' / 'versions')

    # 确保源目录存在且是一个目录
    if not source_path.is_dir():
        logger.warning(f"迁移脚本源目录不存在或不是一个目录: {source_path}")
        return False

    # 确保目标目录存在，如果不存在则递归创建
    target_path.mkdir(parents=True, exist_ok=True)

    # 遍历源目录中的所有条目
    logger.debug(f"开始同步迁移脚本从 {source_path} 到 {target_path}...")
    files_copied = 0
    try:
        for source_item in source_path.iterdir():
            # 只处理文件，忽略任何可能的子目录（如 __pycache__）
            if source_item.is_file():
                # 构建目标文件的完整路径
                destination_file = target_path / source_item.name

                # 复制文件（会覆盖已存在的文件）
                shutil.copy2(source_item, destination_file)
                files_copied += 1
    except IOError as e:
        logger.error(f"在复制文件时发生 I/O 错误: {e}")
        return False
    except Exception as e:
        logger.error(f"发生未知错误: {e}")
        return False

    logger.debug(f"数据库迁移脚本 versions 同步完成，共复制/覆盖了 {files_copied} 个文件。")
    return True
