from datetime import datetime
from pathlib import Path
from tarfile import open as tarfile_open
from time import sleep
from typing import List, Optional, Tuple

from httpx import Client as HttpxClient
from p115client import P115Client, check_response

from app.chain.storage import StorageChain
from app.log import logger

from ...core.config import configer
from ...schemas.backup import BackupHistory, BackupTargetType, StrmBackupItem


class BackupStrmHelper:
    """
    STRM 备份核心逻辑
    """

    def __init__(self):
        self._storage_chain = StorageChain()

    @property
    def _storage_name(self) -> str:
        """
        获取当前存储模块名称
        """
        return configer.storage_module

    @staticmethod
    def _generate_filename(task_name: str) -> str:
        """
        生成备份文件名

        :param task_name: 备份任务名称
        :return: 备份文件名
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = task_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        return f"{safe_name}_{timestamp}.tar.gz"

    @staticmethod
    def _create_tar_gz(
        source_paths: List[str],
        output_path: Path,
    ) -> Tuple[bool, Optional[str]]:
        """
        将多个源目录打包为 tar.gz 文件

        :param source_paths: 要打包的源目录列表
        :param output_path: 输出文件路径
        :return: (是否成功, 错误信息)
        """
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with tarfile_open(output_path, "w:gz") as tar:
                for source_path in source_paths:
                    source = Path(source_path)
                    if not source.exists():
                        logger.warning(f"【STRM备份】源目录不存在，跳过: {source_path}")
                        continue
                    if not source.is_dir():
                        logger.warning(
                            f"【STRM备份】源路径不是目录，跳过: {source_path}"
                        )
                        continue
                    arcname = source.name
                    tar.add(source, arcname=arcname)
                    logger.info(f"【STRM备份】已添加目录: {source_path} -> {arcname}")
            return True, None
        except Exception as e:
            error_msg = f"创建 tar.gz 失败: {str(e)}"
            logger.error(f"【STRM备份】{error_msg}", exc_info=True)
            return False, error_msg

    @staticmethod
    def _extract_tar_gz(
        archive_path: Path,
        target_dir: Path,
    ) -> Tuple[bool, Optional[str]]:
        """
        解压 tar.gz 文件到目标目录

        :param archive_path: 备份文件路径
        :param target_dir: 解压目标目录
        :return: (是否成功, 错误信息)
        """
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            with tarfile_open(archive_path, "r:gz") as tar:
                tar.extractall(path=target_dir, filter="data")
            return True, None
        except Exception as e:
            error_msg = f"解压 tar.gz 失败: {str(e)}"
            logger.error(f"【STRM备份】{error_msg}", exc_info=True)
            return False, error_msg

    @staticmethod
    def _clean_old_backups(
        backup_dir: Path,
        task_name: str,
        retain_count: int,
    ) -> int:
        """
        清理旧的备份文件，保留最新的 N 个

        :param backup_dir: 备份目录
        :param task_name: 备份任务名称
        :param retain_count: 保留数量
        :return: 删除的文件数量
        """
        safe_name = task_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        prefix = f"{safe_name}_"
        backup_files = sorted(
            [
                f
                for f in backup_dir.iterdir()
                if f.name.startswith(prefix) and f.name.endswith(".tar.gz")
            ],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        deleted_count = 0
        for old_file in backup_files[retain_count:]:
            try:
                old_file.unlink()
                deleted_count += 1
                logger.info(f"【STRM备份】已删除旧备份: {old_file.name}")
            except Exception as e:
                logger.error(f"【STRM备份】删除旧备份失败: {old_file.name}, {str(e)}")
        return deleted_count

    def backup_to_local(
        self,
        task: StrmBackupItem,
    ) -> BackupHistory:
        """
        执行本地备份

        :param task: 备份任务配置
        :return: 备份历史记录
        """
        filename = self._generate_filename(task.name)
        target_dir = Path(task.local_target_path)
        output_path = target_dir / filename

        success, error_msg = self._create_tar_gz(
            source_paths=task.source_paths,
            output_path=output_path,
        )

        file_size = (
            output_path.stat().st_size if success and output_path.exists() else 0
        )

        if success:
            self._clean_old_backups(
                backup_dir=target_dir,
                task_name=task.name,
                retain_count=task.retain_count,
            )

        return BackupHistory(
            task_name=task.name,
            filename=filename,
            target_type=BackupTargetType.LOCAL,
            target_path=str(output_path),
            file_size=file_size,
            source_paths=task.source_paths,
            status="success" if success else "error",
            error_msg=error_msg,
        )

    def backup_to_cloud(
        self,
        task: StrmBackupItem,
        client: P115Client,
    ) -> BackupHistory:
        """
        执行 115 网盘备份

        :param task: 备份任务配置
        :param client: P115Client 实例
        :return: 备份历史记录
        """
        filename = self._generate_filename(task.name)
        temp_dir = configer.PLUGIN_TEMP_PATH / "backup"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / filename

        success, error_msg = self._create_tar_gz(
            source_paths=task.source_paths,
            output_path=temp_file,
        )

        if not success:
            return BackupHistory(
                task_name=task.name,
                filename=filename,
                target_type=BackupTargetType.CLOUD_115,
                target_path=f"{task.cloud_target_path}/{filename}",
                file_size=0,
                source_paths=task.source_paths,
                status="error",
                error_msg=error_msg,
            )

        file_size = temp_file.stat().st_size
        cloud_path = f"{task.cloud_target_path}/{filename}"

        try:
            target_dir_item = self._storage_chain.get_file_item(
                storage=self._storage_name, path=Path(task.cloud_target_path)
            )
            if not target_dir_item:
                raise Exception(f"115 网盘目标目录不存在: {task.cloud_target_path}")

            pid = target_dir_item.fileid

            max_retries = 3
            last_error = None
            for attempt in range(max_retries):
                try:
                    resp = client.upload_file(temp_file, pid=pid, filename=filename)
                    check_response(resp)
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"【STRM备份】上传失败，第 {attempt + 1}/{max_retries} 次重试: {e}"
                    )
                    if attempt < max_retries - 1:
                        sleep(2 * (attempt + 1))

            if last_error:
                raise last_error

            logger.info(f"【STRM备份】上传到 115 网盘成功: {cloud_path}")
            temp_file.unlink(missing_ok=True)

            # 清理旧云端备份
            self._clean_old_cloud_backups(
                task_name=task.name,
                cloud_path=task.cloud_target_path,
                retain_count=task.retain_count,
            )

            return BackupHistory(
                task_name=task.name,
                filename=filename,
                target_type=BackupTargetType.CLOUD_115,
                target_path=cloud_path,
                file_size=file_size,
                source_paths=task.source_paths,
                status="success",
            )
        except Exception as e:
            error_msg = f"上传到 115 网盘失败: {str(e)}"
            logger.error(f"【STRM备份】{error_msg}", exc_info=True)
            temp_file.unlink(missing_ok=True)
            return BackupHistory(
                task_name=task.name,
                filename=filename,
                target_type=BackupTargetType.CLOUD_115,
                target_path=cloud_path,
                file_size=file_size,
                source_paths=task.source_paths,
                status="error",
                error_msg=error_msg,
            )

    def _clean_old_cloud_backups(
        self,
        task_name: str,
        cloud_path: str,
        retain_count: int,
    ):
        """
        清理 115 网盘上旧的备份文件

        :param task_name: 备份任务名称
        :param cloud_path: 115 网盘备份目录
        :param retain_count: 保留数量
        """
        try:
            dir_item = self._storage_chain.get_file_item(
                storage=self._storage_name, path=Path(cloud_path)
            )
            if not dir_item:
                return

            files = self._storage_chain.list_files(dir_item) or []
            safe_name = task_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
            prefix = f"{safe_name}_"
            backup_files = sorted(
                [
                    f
                    for f in files
                    if f.name.startswith(prefix) and f.name.endswith(".tar.gz")
                ],
                key=lambda f: f.name,
                reverse=True,
            )

            for old_file in backup_files[retain_count:]:
                try:
                    self._storage_chain.delete_file(old_file)
                    logger.info(f"【STRM备份】已删除云端旧备份: {old_file.name}")
                except Exception as e:
                    logger.error(
                        f"【STRM备份】删除云端旧备份失败: {old_file.name}, {str(e)}"
                    )
        except Exception as e:
            logger.error(f"【STRM备份】清理云端旧备份失败: {str(e)}", exc_info=True)

    def list_local_backups(self, task: StrmBackupItem) -> List[BackupHistory]:
        """
        列出本地备份文件

        :param task: 备份任务配置
        :return: 备份历史记录列表
        """
        if not task.local_target_path:
            return []

        backup_dir = Path(task.local_target_path)
        if not backup_dir.exists():
            return []

        safe_name = task.name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        prefix = f"{safe_name}_"
        results = []

        for f in sorted(
            backup_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True
        ):
            if f.name.startswith(prefix) and f.name.endswith(".tar.gz"):
                results.append(
                    BackupHistory(
                        task_name=task.name,
                        filename=f.name,
                        target_type=BackupTargetType.LOCAL,
                        target_path=str(f),
                        file_size=f.stat().st_size,
                        source_paths=task.source_paths,
                    )
                )

        return results

    def list_cloud_backups(
        self,
        task: StrmBackupItem,
    ) -> List[BackupHistory]:
        """
        列出 115 网盘备份文件

        :param task: 备份任务配置
        :return: 备份历史记录列表
        """
        if not task.cloud_target_path:
            return []

        try:
            dir_item = self._storage_chain.get_file_item(
                storage=self._storage_name, path=Path(task.cloud_target_path)
            )
            if not dir_item:
                return []

            files = self._storage_chain.list_files(dir_item) or []
            safe_name = task.name.replace("/", "_").replace("\\", "_").replace(" ", "_")
            prefix = f"{safe_name}_"
            results = []

            for f in files:
                if f.name.startswith(prefix) and f.name.endswith(".tar.gz"):
                    results.append(
                        BackupHistory(
                            task_name=task.name,
                            filename=f.name,
                            target_type=BackupTargetType.CLOUD_115,
                            target_path=f"{task.cloud_target_path}/{f.name}",
                            file_size=f.size,
                            source_paths=task.source_paths,
                        )
                    )

            return sorted(results, key=lambda x: x.filename, reverse=True)
        except Exception as e:
            logger.error(f"【STRM备份】列出 115 网盘备份失败: {str(e)}", exc_info=True)
            return []

    @staticmethod
    def restore_from_local(
        backup_path: str,
        source_paths: List[str],
    ) -> Tuple[bool, Optional[str]]:
        """
        从本地备份恢复

        :param backup_path: 备份文件路径
        :param source_paths: 恢复目标目录列表（取第一个的父目录作为解压根目录）
        :return: (是否成功, 错误信息)
        """
        archive_path = Path(backup_path)
        if not archive_path.exists():
            return False, f"备份文件不存在: {backup_path}"

        if not source_paths:
            return False, "未指定恢复目标目录"

        target_dir = Path(source_paths[0]).parent
        target_dir.mkdir(parents=True, exist_ok=True)
        return BackupStrmHelper._extract_tar_gz(archive_path, target_dir)

    def restore_from_cloud(
        self,
        cloud_path: str,
        source_paths: List[str],
        client: Optional[P115Client],
    ) -> Tuple[bool, Optional[str]]:
        """
        从 115 网盘备份恢复

        :param cloud_path: 115 网盘备份文件路径
        :param source_paths: 恢复目标目录列表（取第一个作为恢复根目录）
        :param client: P115Client 实例
        :return: (是否成功, 错误信息)
        """
        if not source_paths:
            return False, "未指定恢复目标目录"

        if not client:
            return False, "115 客户端未初始化"

        try:
            target_file = Path(cloud_path)
            parent_path = target_file.parent
            filename = target_file.name

            parent_dir = self._storage_chain.get_file_item(
                storage=self._storage_name, path=parent_path
            )
            if not parent_dir:
                return False, f"115 网盘父目录不存在: {parent_path}"

            files = self._storage_chain.list_files(parent_dir) or []
            file_item = None
            for f in files:
                if f.name == filename and f.type == "file":
                    file_item = f
                    break

            if not file_item:
                return False, f"115 网盘备份文件不存在: {cloud_path}"

            pickcode = getattr(file_item, "pickcode", None)
            if not pickcode:
                return False, f"无法获取文件 pickcode: {cloud_path}"

            temp_dir = configer.PLUGIN_TEMP_PATH / "restore"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_file = temp_dir / filename

            download_url = client.download_url(
                pickcode, user_agent=configer.get_user_agent()
            )
            headers = {"User-Agent": configer.get_user_agent()}
            with HttpxClient(headers=headers, follow_redirects=True) as hc:
                with hc.stream("GET", str(download_url)) as resp:
                    resp.raise_for_status()
                    with open(temp_file, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=8 * 1024 * 1024):
                            f.write(chunk)
            logger.info(f"【STRM备份】从 115 网盘下载备份文件成功: {cloud_path}")

            target_dir = Path(source_paths[0]).parent
            target_dir.mkdir(parents=True, exist_ok=True)
            success, error_msg = self._extract_tar_gz(temp_file, target_dir)
            temp_file.unlink(missing_ok=True)
            return success, error_msg
        except Exception as e:
            error_msg = f"从 115 网盘恢复失败: {str(e)}"
            logger.error(f"【STRM备份】{error_msg}", exc_info=True)
            return False, error_msg

    def execute_backup(
        self,
        task: StrmBackupItem,
        client: Optional[P115Client] = None,
    ) -> BackupHistory:
        """
        执行备份任务

        :param task: 备份任务配置
        :param client: P115Client 实例
        :return: 备份历史记录
        """
        if not task.enabled:
            return BackupHistory(
                task_name=task.name,
                filename="",
                target_type=task.target_type,
                target_path="",
                source_paths=task.source_paths,
                status="skipped",
                error_msg="备份任务未启用",
            )

        if not task.source_paths:
            return BackupHistory(
                task_name=task.name,
                filename="",
                target_type=task.target_type,
                target_path="",
                source_paths=[],
                status="error",
                error_msg="未配置源目录",
            )

        if task.target_type == BackupTargetType.LOCAL:
            if not task.local_target_path:
                return BackupHistory(
                    task_name=task.name,
                    filename="",
                    target_type=task.target_type,
                    target_path="",
                    source_paths=task.source_paths,
                    status="error",
                    error_msg="未配置本地备份目录",
                )
            return self.backup_to_local(task)
        elif task.target_type == BackupTargetType.CLOUD_115:
            if not task.cloud_target_path:
                return BackupHistory(
                    task_name=task.name,
                    filename="",
                    target_type=task.target_type,
                    target_path="",
                    source_paths=task.source_paths,
                    status="error",
                    error_msg="未配置 115 网盘备份目录",
                )
            if not client:
                return BackupHistory(
                    task_name=task.name,
                    filename="",
                    target_type=task.target_type,
                    target_path="",
                    source_paths=task.source_paths,
                    status="error",
                    error_msg="115 客户端未初始化",
                )
            return self.backup_to_cloud(task, client)
        else:
            return BackupHistory(
                task_name=task.name,
                filename="",
                target_type=task.target_type,
                target_path="",
                source_paths=task.source_paths,
                status="error",
                error_msg=f"不支持的备份目标类型: {task.target_type}",
            )


backup_helper = BackupStrmHelper()
