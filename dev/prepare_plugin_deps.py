"""
插件内嵌依赖准备脚本

功能:
- 支持可选的顶层白名单 (`top_level_packages`) 进行性能优化。
- 支持基于作者 (`author_match`) 和包名 (`package_name_match`) 的核心匹配规则。
- 支持黑名单 (`exclude_packages`) 进行细粒度排除。
- 采用“信任边界”剪枝策略，高效地进行依赖分析。
- 在遍历时捕获并遵守子依赖的版本限制，确保兼容性。
- 自动下载所有目标平台的 .whl 文件。
- 如果特定平台的 .whl 文件不存在，则尝试从源码构建（仅限当前环境或纯 Python 包）。
- 支持在清理 wheels 目录时保留指定包的 .whl 文件
- 不修改原始的 `requirements.txt` 文件。
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests


try:
    PLUGIN_ID = "p115strmhelper"
    PLUGIN_SOURCE_DIR = (
        Path("/Users/rem/Documents/Git/MoviePilot-Plugins/plugins.v2") / PLUGIN_ID
    )
except KeyError:
    raise ValueError("错误: 环境变量 PLUGIN_FULL_PATH 未设置。")


PLUGIN_REQUIREMENTS_FILE = PLUGIN_SOURCE_DIR / "requirements.txt"
RESOLVED_REQUIREMENTS_FILE = PLUGIN_SOURCE_DIR / "requirements.lock.txt"
CONFIG_FILE = PLUGIN_SOURCE_DIR / "bundle.json"
TARGET_PLATFORMS = {
    "win_amd64",
    "win32",
    "macosx_11_0_arm64",
    "macosx_10_9_x86_64",
    "manylinux2014_x86_64",
    "manylinux2014_aarch64",
    "manylinux_2_24_armv7l",
}
TARGET_PYTHON_VERSION = "312"
TARGET_ABI = f"cp{TARGET_PYTHON_VERSION}"


# 用于缓存已查询过的包信息，避免重复网络请求
package_info_cache: Dict[str, Optional[str]] = {}


def normalize_package_name(package_name: str) -> str:
    """
    规范化 Python 包名

    :param package_name (str): 包名

    :return str: 规范化后的包名
    """
    return re.sub(r"[-_.]+", "-", package_name.strip()).lower()


def log(*args, **kwargs):
    """
    将消息打印到标准错误流 (stderr)，避免污染标准输出。
    """
    print(*args, file=sys.stderr, **kwargs)


def get_package_author(package_name: str) -> Optional[str]:
    """
    通过 PyPI 的 JSON API 查询并缓存指定包的作者信息。

    Args:
        package_name (str): 需要查询的包名。

    Returns:
        Optional[str]: 包的作者信息字符串，如果查询失败或不存在则返回 None。
    """
    normalized_name = package_name.lower()
    # 检查缓存中是否已有该包的信息
    if normalized_name in package_info_cache:
        return package_info_cache[normalized_name]

    log(f"   L 正在查询 PyPI API: '{package_name}'...")
    try:
        # 发起网络请求
        response = requests.get(
            f"https://pypi.org/pypi/{package_name}/json", timeout=15
        )
        response.raise_for_status()  # 如果请求失败 (如 404)，则抛出异常
        # 解析 JSON 数据并提取作者信息
        author = response.json().get("info", {}).get("author")
        package_info_cache[normalized_name] = author
        return author
    except requests.RequestException as e:
        # 处理网络请求或解析过程中的异常
        log(f"    [警告] 无法获取 '{package_name}' 的信息: {e}")
        package_info_cache[normalized_name] = None
        return None


def generate_lock_file() -> bool:
    """
    使用 pip-compile 解析依赖，并生成一个跨平台的、包含哈希值的锁文件。

    Returns:
        bool: 如果锁文件成功生成则返回 True，否则返回 False。
    """
    log("[Info] 正在生成依赖锁文件")
    try:
        # 调用 pip-compile 命令
        subprocess.run(
            [
                # 注意: 在不同环境中，此路径可能需要调整
                "pip-compile",
                str(PLUGIN_REQUIREMENTS_FILE),
                "--output-file",
                str(RESOLVED_REQUIREMENTS_FILE),
                "--resolver=backtracking",  # 使用回溯算法解决复杂的依赖冲突
                "--generate-hashes",  # 为每个包生成哈希值，确保安全性
                "--quiet",  # 精简输出
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        log(f"  -> 成功生成通用的依赖锁文件: {RESOLVED_REQUIREMENTS_FILE.name}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # 捕获命令执行失败或找不到命令的异常
        log(
            "[Error] pip-compile 执行失败。请确保 pip-tools 已安装且 Python 环境配置正确。"
        )
        if hasattr(e, "stderr"):
            log(f"[Error]错误详情: {e.stderr}")
        return False


def parse_resolved_requirements(file_path: Path) -> Dict[str, str]:
    """
    解析由 pip-compile 生成的、完全固定的 requirements.lock.txt 文件。

    Args:
        file_path (Path): 锁文件的路径。

    Returns:
        Dict[str, str]: 一个字典，键是小写的包名，值是完整的包版本声明 (如 'requests==2.28.1')。
    """
    specs = {}
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 忽略注释、哈希值、编辑模式等无关行
            if line and not line.startswith(("#", "-e", "-r", "--hash")):
                # 通过 ' \' 分割，去除行尾的哈希信息
                spec_line = line.split(" \\")[0].strip()
                # 正则匹配 "包名==版本号" 格式
                match = re.match(r"([a-zA-Z0-9_.-]+)==([0-9a-zA-Z_.-]+)", spec_line)
                if match:
                    package_name = normalize_package_name(match.group(1))
                    specs[package_name] = spec_line
    return specs


def load_bundling_config(config_file: Path) -> Dict:
    """
    加载并解析 bundle.json 配置文件。

    Args:
        config_file (Path): bundle.json 文件的路径。

    Returns:
        Dict: 包含所有捆绑规则的配置字典。
    """
    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    keep_wheel_packages = set(config.get("keep_wheel_packages", []))
    keep_wheel_packages.update(config.get("preserve_wheel_packages", []))

    # 提取并处理各项配置
    return {
        "top_level_whitelist": {
            normalize_package_name(pkg) for pkg in config.get("top_level_packages", [])
        },
        "author_match_list": {
            rule["value"]
            for rule in config.get("rules", [])
            if rule.get("strategy") == "author_match"
        },
        "package_match_list": {
            normalize_package_name(pkg)
            for rule in config.get("rules", [])
            if rule.get("strategy") == "package_name_match"
            for pkg in rule.get("value", [])
        },
        "exclude_list": {
            normalize_package_name(pkg) for pkg in config.get("exclude_packages", [])
        },
        "keep_wheel_packages": {
            normalize_package_name(pkg) for pkg in keep_wheel_packages
        },
    }


def get_top_level_packages(requirements_file: Path) -> Set[str]:
    """
    从原始的 requirements.txt 文件中解析出顶层依赖包的名称。

    Args:
        requirements_file (Path): 原始依赖文件的路径。

    Returns:
        Set[str]: 一个包含所有顶层依赖包名（小写）的集合。
    """
    top_level_packages = set()
    with open(requirements_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 忽略注释等非包声明行
            if line and not line.startswith(("#", "-e", "-r", "--")):
                # 正则提取包名部分
                match = re.match(r"([a-zA-Z0-9_.-]+)", line)
                if match:
                    top_level_packages.add(normalize_package_name(match.group(1)))
    return top_level_packages


def filter_packages_to_bundle(
    resolved_specs: Dict[str, str], config: Dict, top_level_packages: Set[str]
) -> Set[str]:
    """
    根据加载的规则，从所有解析出的依赖中筛选出需要被捆绑的包。

    Args:
        resolved_specs (Dict[str, str]): 所有已解析的依赖包及其版本声明。
        config (Dict): 从 bundle.json 加载的配置规则。
        top_level_packages (Set[str]): 顶层依赖包名集合。

    Returns:
        Set[str]: 需要被捆绑的包名（小写）集合。
    """
    packages_to_bundle = set()
    log("\n[Info] 正在根据规则筛选需要捆绑的包...")

    for package, spec in resolved_specs.items():
        # 规则 1: 应用顶层白名单过滤
        is_top_level = package in top_level_packages
        if (
            config["top_level_whitelist"]
            and is_top_level
            and package not in config["top_level_whitelist"]
        ):
            log(f"ⓘ [顶层过滤] '{package}' 不在 top_level_packages 白名单中，跳过。")
            continue
        if is_top_level:
            log(f"ⓘ [顶层过滤] '{package}' 通过顶层过滤，开始检查内部子依赖...")

        # 规则 2: 应用黑名单排除
        if package in config["exclude_list"]:
            log(f"🚫 [排除列表] '{package}' 在 exclude_packages 中，跳过。")
            continue

        # 规则 3: 应用核心匹配规则（作者或包名）
        author = get_package_author(package)
        if (author and author in config["author_match_list"]) or (
            package in config["package_match_list"]
        ):
            log(f"✔️  [匹配成功] '{package}' (作者: {author})。将被捆绑。")
            packages_to_bundle.add(package)

    return packages_to_bundle


def get_wheel_package_name(wheel_file: Path) -> Optional[str]:
    """
    从 wheel 文件名中提取包名

    :param wheel_file (Path): wheel 文件路径

    :return str: 规范化后的包名，无法识别时返回 None
    """
    if wheel_file.suffix != ".whl":
        return None

    package_name = wheel_file.name.split("-", 1)[0]
    if not package_name:
        return None

    return normalize_package_name(package_name)


def prepare_wheels_dir(wheels_dir: Path, keep_wheel_packages: Set[str]) -> None:
    """
    准备 wheels 目录并保留指定包的 whl 文件

    :param wheels_dir (Path): wheels 目录路径
    :param keep_wheel_packages (Set): 需要保留 wheel 文件的包名集合
    """
    if not wheels_dir.exists():
        wheels_dir.mkdir(parents=True)
        return

    if not keep_wheel_packages:
        shutil.rmtree(wheels_dir)
        wheels_dir.mkdir(parents=True)
        log("[Info] 已清理旧的 wheels 目录")
        return

    removed_count = 0
    kept_count = 0
    for item in wheels_dir.iterdir():
        wheel_package_name = get_wheel_package_name(item)
        if wheel_package_name and wheel_package_name in keep_wheel_packages:
            kept_count += 1
            continue

        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()
        removed_count += 1

    log(
        "[Info] 已清理旧的 wheels 目录，"
        f"删除 {removed_count} 个条目，保留 {kept_count} 个 wheel 文件"
    )
    log(f"[Info] wheel 保留包: {sorted(keep_wheel_packages)}")


def download_wheels(package_specs: List[str], wheels_dir: Path):
    """
    为给定的包列表，逐个下载或构建所有目标平台的 .whl 文件。
    此方法通过逐一处理来避免因单个包的失败而导致整个流程中断。

    Args:
        package_specs (List[str]): 需要下载的包的精确版本声明列表 (e.g., ['requests==2.28.1'])。
        wheels_dir (Path): 用于存放下载的 .whl 文件的目录。
    """
    log("\n[Info] 开始逐个下载或构建 Wheels 文件...")
    log(
        "     注意：从源码构建仅能生成当前运行环境的 wheel，或纯 Python 包的通用 wheel。"
    )
    log("     此脚本无法为其他操作系统或架构进行交叉编译。")

    # 构建通用的平台参数列表，供后续重复使用
    platform_args = []
    for platform in TARGET_PLATFORMS:
        platform_args.extend(["--platform", platform])

    for spec in sorted(package_specs):
        log(f"\n[+] 正在处理: {spec}")
        package_name = re.match(r"([a-zA-Z0-9_.-]+)", spec).group(1)
        normalized_package_name = re.sub(r"[-_.]+", "_", package_name, re.IGNORECASE)

        # 阶段 1: 尝试为当前包下载所有目标平台的二进制文件
        log(f"  -> 正在尝试为 '{package_name}' 下载预构建的二进制 wheel...")
        subprocess.run(
            [
                "pip3",
                "download",
                "--only-binary=:all:",
                "--python-version",
                TARGET_PYTHON_VERSION,
                "--abi",
                TARGET_ABI,
                "--no-deps",
                "-d",
                str(wheels_dir),
            ]
            + platform_args
            + [spec],  # 只处理当前这一个包
            check=False,  # 关键：不检查退出码，允许命令“失败”
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        # 阶段 2: 检查下载结果。如果一个 wheel 文件都没有下载到，则尝试从源码构建
        wheels_for_package = list(wheels_dir.glob(f"{normalized_package_name}-*.whl"))

        if not wheels_for_package:
            log(
                f"  ⚠️  [警告] 未找到 '{package_name}' 的任何预构建二进制包。尝试从源码构建..."
            )

            # 使用 pip wheel 命令，它会自动下载源码并构建
            build_process = subprocess.run(
                [
                    "pip3",
                    "wheel",
                    "--no-deps",
                    "--wheel-dir",
                    str(wheels_dir),
                    spec,
                ],
                check=False,  # 构建也可能失败，不中断脚本
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            if build_process.returncode == 0:
                log(f"  ✅ [成功] 成功从源码为 '{package_name}' 构建 wheel。")
            else:
                log(
                    f"  ❌ [失败] 无法为 '{package_name}' 构建 wheel。可能缺少编译工具链 (如 C++ 编译器, Rust 等)。"
                )
                log(f"      错误详情:\n{build_process.stderr}")
        else:
            log(f"  ✅ [成功] 成功下载 '{package_name}' 的预构建 wheel 文件。")


def cleanup(files_to_remove: List[Path]):
    """
    清理脚本运行过程中产生的临时文件。

    Args:
        files_to_remove (List[Path]): 需要被删除的文件路径列表。
    """
    log("\n[Info] 正在清理临时文件...")
    for file_path in files_to_remove:
        if file_path.exists():
            file_path.unlink()
            log(f"[Info] -> 已删除: {file_path.name}")


def main():
    """
    主执行函数，协调整个依赖准备流程。
    """
    log(f"[Info] 开始为插件 '{PLUGIN_SOURCE_DIR.name}' 准备内嵌依赖")

    # 检查必要文件是否存在，如果不存在则提前退出
    if not PLUGIN_REQUIREMENTS_FILE.exists():
        log(
            f"[Info] 未找到插件的依赖文件 '{PLUGIN_REQUIREMENTS_FILE.name}'，无需处理。"
        )
        return
    if not CONFIG_FILE.exists():
        log(f"[Info] 未找到捆绑规则文件 '{CONFIG_FILE.name}'，无需处理。")
        return

    # 生成依赖锁文件
    if not generate_lock_file():
        return  # 如果生成失败，则终止脚本

    # 解析锁文件和配置文件
    resolved_specs = parse_resolved_requirements(RESOLVED_REQUIREMENTS_FILE)
    config = load_bundling_config(CONFIG_FILE)
    top_level_packages = get_top_level_packages(PLUGIN_REQUIREMENTS_FILE)

    # 根据规则筛选需要捆绑的包
    packages_to_bundle = filter_packages_to_bundle(
        resolved_specs, config, top_level_packages
    )

    # 如果没有需要捆绑的包，则清理并退出
    if not packages_to_bundle:
        log("\n[Info] 分析后，未发现没有需要捆绑的包。")
        cleanup([RESOLVED_REQUIREMENTS_FILE])
        return False

    log(f"\n[info] 分析完成！共找到 {len(packages_to_bundle)} 个需要捆绑的包:")
    # 使用 sorted 确保每次输出顺序一致
    log(sorted(list(packages_to_bundle)))

    # 准备下载目录并执行下载
    wheels_dir_in_plugin = PLUGIN_SOURCE_DIR / "wheels"
    prepare_wheels_dir(wheels_dir_in_plugin, config["keep_wheel_packages"])

    # 从筛选结果中提取精确的版本声明
    specs_to_download = [resolved_specs[pkg] for pkg in packages_to_bundle]
    log(f"\n[Info] 将要下载或构建以下包的精确版本: {specs_to_download}")
    download_wheels(specs_to_download, wheels_dir_in_plugin)

    # 清理临时的锁文件
    cleanup([RESOLVED_REQUIREMENTS_FILE])

    log(f"\n[Info] '{PLUGIN_SOURCE_DIR.name}' 目录现在已包含 'wheels' 目录。")
    log("[Info] 内嵌依赖准备成功!")
    return True


if __name__ == "__main__":
    main()
