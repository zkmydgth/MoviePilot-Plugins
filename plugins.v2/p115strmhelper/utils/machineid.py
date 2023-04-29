import uuid
import hashlib
import json
from pathlib import Path
from typing import Optional


class MachineID:
    """
    使用 pathlib 实现的静态 Machine ID 管理类
    生成和读取 64 字符的唯一机器标识符
    """

    @staticmethod
    def has_machine_id(config_path: Optional[Path] = None) -> bool:
        """
        检查是否已经生成过 machine id

        :param config_path: 存储 machine id 的文件路径，默认为 machine_id.json
        :return: 如果存在返回 True，否则 False
        """
        path = config_path
        return path.exists()

    @staticmethod
    def generate_machine_id(config_path: Optional[Path] = None) -> str:
        """
        生成一个新的 64 字符 machine id 并保存到文件

        :param config_path: 存储 machine id 的文件路径，默认为 machine_id.json
        :return: 生成的 machine id (64 字符十六进制字符串)
        :raises RuntimeError: 如果文件已存在
        """
        path = config_path

        if path.exists():
            raise RuntimeError(f"Machine ID already exists at {path}")

        unique_data = (
            str(uuid.uuid1())
            + str(uuid.uuid4())
            + str(Path.cwd())
            + str(hashlib.sha256(Path("/").read_bytes()).hexdigest())
        )

        hash_obj = hashlib.sha3_512(unique_data.encode("utf-8"))
        machine_id = hash_obj.hexdigest()

        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump({"machine_id": machine_id}, f)
        except OSError as e:
            raise RuntimeError(f"Failed to write machine ID to {path}: {e}")

        return machine_id

    @staticmethod
    def read_machine_id(config_path: Optional[Path] = None) -> str:
        """
        从文件中读取 machine id

        :param config_path: 存储 machine id 的文件路径，默认为 machine_id.json
        :return: 读取到的 machine id (64 字符十六进制字符串)
        :raises FileNotFoundError: 如果文件不存在
        :raises ValueError: 如果文件内容无效
        """
        path = config_path

        if not path.exists():
            raise FileNotFoundError(f"Machine ID file not found at {path}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            machine_id = data.get("machine_id")

            if not machine_id or len(machine_id) != 64:
                raise ValueError(f"Invalid machine ID format in {path}")

            return machine_id
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid machine ID file format in {path}: {e}")
        except OSError as e:
            raise ValueError(f"Failed to read machine ID from {path}: {e}")
