import os
import json
import subprocess
import zipfile
from github import Github

PACKAGE_V1_FILE = "package.json"
PACKAGE_V2_FILE = "package.v2.json"
PLUGINS_V1_DIR = "plugins"
PLUGINS_V2_DIR = "plugins.v2"


def get_changed_files():
    """
    获取在最新提交中发生变化的文件列表
    """
    try:
        output = subprocess.check_output(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"]
        ).decode("utf-8")
        return output.strip().split("\n")
    except Exception as e:
        print(f"Error getting changed files: {e}")
        return []


def get_file_content_from_commit(commit_hash, filepath):
    """
    从指定的 commit 获取文件内容
    """
    if not os.path.exists(filepath):
        return "{}"
    try:
        return subprocess.check_output(
            ["git", "show", f"{commit_hash}:{filepath}"]
        ).decode("utf-8")
    except subprocess.CalledProcessError:
        return "{}"
    except Exception as e:
        print(f"Error getting file content for {filepath} at {commit_hash}: {e}")
        return "{}"


def zip_plugin_directory(plugin_dir, zip_path):
    """
    将插件目录打包成 zip 文件
    """
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(plugin_dir):
            for file in files:
                file_path = os.path.join(root, file)
                archive_name = os.path.relpath(file_path, plugin_dir)
                zipf.write(file_path, archive_name)
    print(f"Successfully created zip file: {zip_path}")


def process_package_file(filepath, plugins_base_dir):
    """
    处理单个 package 文件，检测更新并发布
    """
    print(f"--- Processing file: {filepath} ---")

    # 获取当前和上一个 commit 的文件内容
    old_content = get_file_content_from_commit("HEAD^", filepath)
    new_content = get_file_content_from_commit("HEAD", filepath)

    if not new_content or new_content == "{}":
        print(f"Could not read new content for {filepath}. Skipping.")
        return

    try:
        old_data = json.loads(old_content)
        new_data = json.loads(new_content)
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from {filepath}: {e}")
        return

    # 初始化 GitHub API
    github_token = os.environ.get("GITHUB_TOKEN")
    repo_name = os.environ.get("GITHUB_REPOSITORY")
    if not github_token or not repo_name:
        print("GITHUB_TOKEN or GITHUB_REPOSITORY not set. Cannot create release.")
        return
    g = Github(github_token)
    repo = g.get_repo(repo_name)

    # 比较插件版本
    for plugin_id, new_plugin_info in new_data.items():
        old_plugin_info = old_data.get(plugin_id, {})
        new_version = new_plugin_info.get("version", "0.0.0")
        old_version = old_plugin_info.get("version", "0.0.0")

        # 条件：版本更新，并且有 release: true 标签
        if new_version > old_version and new_plugin_info.get("release") is True:
            print(
                f"Update detected for '{plugin_id}': {old_version} -> {new_version}. Marked for release."
            )

            plugin_dir = os.path.join(plugins_base_dir, plugin_id.lower())
            if not os.path.isdir(plugin_dir):
                print(
                    f"  [ERROR] Plugin directory not found: {plugin_dir}. Skipping release."
                )
                continue

            # 准备 Release 信息
            tag_name = f"{plugin_id.lower()}.v{new_version}"
            release_name = f"{new_plugin_info.get('name', plugin_id)} v{new_version}"
            release_notes = new_plugin_info.get("history", {}).get(
                f"v{new_version}", "没有提供更新日志。"
            )

            # 检查是否已存在同名 Tag
            try:
                repo.get_git_ref(f"tags/{tag_name}")
                print(f"[WARN] Tag '{tag_name}' already exists. Skipping release.")
                continue
            except Exception:
                pass

            # 打包插件
            zip_filename = f"{plugin_id.lower()}.v{new_version}.zip"
            zip_plugin_directory(plugin_dir, zip_filename)

            # 创建 GitHub Release
            print(f"  Creating release with tag '{tag_name}'...")
            try:
                release = repo.create_git_release(
                    tag=tag_name,
                    name=release_name,
                    message=release_notes,
                    draft=False,
                    prerelease=False,
                )
                release.upload_asset(zip_filename)
                print(
                    f"  Successfully created release and uploaded asset for '{plugin_id}'."
                )
            except Exception as e:
                print(f"  [ERROR] Failed to create release for {plugin_id}: {e}")
            finally:
                # 清理 zip 文件
                if os.path.exists(zip_filename):
                    os.remove(zip_filename)


if __name__ == "__main__":
    changed_files = get_changed_files()
    if PACKAGE_V2_FILE in changed_files:
        process_package_file(PACKAGE_V2_FILE, PLUGINS_V2_DIR)

    if PACKAGE_V1_FILE in changed_files:
        process_package_file(PACKAGE_V1_FILE, PLUGINS_V1_DIR)

    print("\n--- Script finished ---")
