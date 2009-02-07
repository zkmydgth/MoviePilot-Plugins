import os
import platform
import subprocess
import hashlib
import uuid
import json
from pathlib import Path


class MachineIDGenerator:
    """
    Machine ID 生成器
    """

    def __init__(self):
        """
        初始化机器ID生成器

        :param cache_path: 自定义缓存文件路径，默认使用系统合适位置
        """
        self.cache_path = self.default_cache_path()
        self.cache_file = Path(self.cache_path) / "machine_id.json"
        self.id = None

    @staticmethod
    def default_cache_path():
        """
        确定不同OS下的默认缓存路径
        """
        system = platform.system()
        if Path("/.dockerenv").exists():
            # MP 容器
            return "/var/lib/nginx"
        elif system == "Windows":
            # Windows 运行环境
            return os.environ.get("APPDATA", os.path.expanduser("~"))
        elif system == "Darwin":
            # MacOS 运行环境
            return os.path.expanduser("~/Library/Application Support")
        else:
            # Linux和其他Unix-like系统
            return os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))

    def get_id(self):
        """
        获取机器唯一ID，优先使用缓存
        """
        if self.id:
            return self.id

        # 尝试从缓存读取
        if self.try_read_cache():
            return self.id

        # 生成新ID
        self.id = self.generate_id()

        # 写入缓存
        self.save_cache()

        return self.id

    def try_read_cache(self):
        """
        尝试从缓存文件读取ID
        """
        try:
            if self.cache_file.exists():
                with open(self.cache_file, "r") as f:
                    data = json.load(f)
                    if "machine_id" in data:
                        self.id = data["machine_id"]
                        return True
        except Exception:
            pass
        return False

    def save_cache(self):
        """
        保存ID到缓存文件
        """
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump({"machine_id": self.id}, f)
        except Exception:
            pass

    def generate_id(self):
        """
        生成机器唯一ID，按优先级尝试不同方法
        """
        # 1. 尝试容器环境特定标识
        container_id = self.get_container_id()
        if container_id:
            return self.hash_id(container_id)

        # 2. 尝试系统原生机器ID
        os_id = self.get_os_machine_id()
        if os_id:
            return self.hash_id(os_id)

        # 3. 生成基于硬件的ID
        hw_id = self.get_hardware_id()
        if hw_id:
            return self.hash_id(hw_id)

        # 4. 回退：生成持久化UUID
        return self.generate_persistent_uuid()

    def get_container_id(self):
        """
        获取容器环境唯一标识
        """
        if Path("/.dockerenv").exists():
            try:
                with open("/proc/self/cgroup", "r") as f:
                    for line in f:
                        if "docker" in line:
                            parts = line.strip().split("/")
                            if len(parts) > 2:
                                return parts[-1][:64]
            except Exception:
                pass

        # Kubernetes环境
        if "KUBERNETES_SERVICE_HOST" in os.environ:
            # 1. 尝试Pod UID
            pod_uid = os.environ.get("POD_UID")
            if pod_uid:
                return pod_uid
            # 2. 使用Pod名称（需确保唯一性）
            pod_name = os.environ.get("POD_NAME")
            if pod_name:
                return pod_name

        return None

    def get_os_machine_id(self):
        """
        获取操作系统级别的机器ID
        """
        system = platform.system()

        # Linux系统
        if system == "Linux":
            # 1. 标准machine-id
            machine_id_path = Path("/etc/machine-id")
            if machine_id_path.exists():
                with open(machine_id_path, "r") as f:
                    return f.read().strip()

            # 2. 旧版dbus machine-id
            dbus_id_path = Path("/var/lib/dbus/machine-id")
            if dbus_id_path.exists():
                with open(dbus_id_path, "r") as f:
                    return f.read().strip()

        # Windows系统
        elif system == "Windows":
            try:
                # 从注册表获取机器GUID
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                    0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                )
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                winreg.CloseKey(key)
                return value
            except Exception:
                pass

        # macOS系统
        elif system == "Darwin":
            try:
                # 获取硬件UUID
                output = subprocess.check_output(
                    ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], text=True
                )
                for line in output.splitlines():
                    if "IOPlatformUUID" in line:
                        return line.split('"')[-2]
            except Exception:
                pass

        return None

    def get_hardware_id(self):
        """
        生成基于硬件的ID
        """
        identifiers = []

        # 1. 系统序列号
        try:
            if platform.system() == "Linux":
                with open("/sys/class/dmi/id/product_uuid", "r") as f:
                    identifiers.append(f.read().strip())
            elif platform.system() == "Darwin":
                output = subprocess.check_output(
                    ["system_profiler", "SPHardwareDataType"], text=True
                )
                for line in output.splitlines():
                    if "Serial Number" in line:
                        identifiers.append(line.split(":")[1].strip())
        except Exception:
            pass

        # 2. 磁盘序列号（第一个磁盘）
        try:
            if platform.system() == "Linux":
                disk_id = subprocess.check_output(
                    ["lsblk", "-dno", "SERIAL", "/dev/sda"], text=True
                ).strip()
                if disk_id:
                    identifiers.append(disk_id)
        except Exception:
            pass

        # 3. MAC地址（第一个活跃接口）
        try:
            import netifaces

            interfaces = netifaces.interfaces()
            for iface in interfaces:
                if iface.startswith(("lo", "docker", "veth")):
                    continue  # 跳过虚拟接口
                addrs = netifaces.ifaddresses(iface)
                if netifaces.AF_LINK in addrs:
                    mac = addrs[netifaces.AF_LINK][0].get("addr")
                    if mac and mac != "00:00:00:00:00:00":
                        identifiers.append(mac)
                        break
        except Exception:
            pass

        # 如果收集到任何硬件标识符，则组合并哈希
        if identifiers:
            combined = "-".join(identifiers)
            return combined

        return None

    def generate_persistent_uuid(self):
        """
        生成持久化的UUID
        """
        # 尝试从缓存读取之前生成的UUID
        try:
            if self.cache_file.exists():
                with open(self.cache_file, "r") as f:
                    data = json.load(f)
                    if "persistent_uuid" in data:
                        return data["persistent_uuid"]
        except Exception:
            pass

        # 生成新的UUID并保存
        new_uuid = str(uuid.uuid4())
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump({"persistent_uuid": new_uuid}, f)
        except Exception:
            pass

        return new_uuid

    @staticmethod
    def hash_id(identifier):
        """
        对标识符进行哈希处理，返回固定长度ID
        """
        # 使用SHA-256哈希，取前16个字节（32个十六进制字符）
        return hashlib.sha256(identifier.encode()).hexdigest()[:32]
