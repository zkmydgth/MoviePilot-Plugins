from pathlib import Path


class DirectoryTree:
    """
    目录树
    """

    @staticmethod
    def scan_directory_to_tree(root_path, output_file, append=False, extensions=None):
        """
        扫描本地目录生成目录树到文件，可过滤指定后缀名文件

        :param root_path: 要扫描的根目录
        :param output_file: 输出文件路径
        :param append: 是否追加模式 (默认覆盖)
        :param extensions: 要包含的文件后缀名列表
        """
        root = Path(root_path).resolve()
        mode = "a" if append else "w"

        if extensions is not None:
            extensions = {
                ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                for ext in extensions
            }

        with open(output_file, mode, encoding="utf-8") as f_out:
            for path in root.rglob("*"):
                if path.is_file():
                    if extensions is None or path.suffix.lower() in extensions:
                        f_out.write(f"{str(path)}\n")

    @staticmethod
    def generate_tree_from_list(file_list, output_file, append=False):
        """
        从文件列表生成目录树到文件

        :param file_list: 文件路径列表
        :param output_file: 输出文件路径
        :param append: 是否追加模式 (默认覆盖)
        """
        mode = "a" if append else "w"
        with open(output_file, mode, encoding="utf-8") as f_out:
            for file_path in file_list:
                f_out.write(f"{file_path}\n")

    @staticmethod
    def compare_trees(tree_file1, tree_file2):
        """
        比较两个目录树文件，找出tree_file1有而tree_file2没有的文件

        :param tree_file1: 第一个目录树文件
        :param tree_file2: 第二个目录树文件
        :return: 差异文件列表
        """
        # 使用集合进行高效比较
        with open(tree_file2, "r", encoding="utf-8") as f2:
            tree2_set = set(line.strip() for line in f2)

        with open(tree_file1, "r", encoding="utf-8") as f1:
            for line in f1:
                file_path = line.strip()
                if file_path not in tree2_set:
                    yield file_path

    @staticmethod
    def compare_trees_lines(tree_file1, tree_file2):
        """
        比较两个目录树文件，找出tree_file1有而tree_file2没有的文件

        :param tree_file1: 第一个目录树文件
        :param tree_file2: 第二个目录树文件
        :return: 生成器，产生行号
        """
        with open(tree_file2, "r", encoding="utf-8") as f2:
            tree2_set = set(line.strip() for line in f2)

        with open(tree_file1, "r", encoding="utf-8") as f1:
            for line_num, line in enumerate(f1, start=1):
                file_path = line.strip()
                if file_path not in tree2_set:
                    yield line_num

    @staticmethod
    def get_path_by_line_number(tree_file, line_number):
        """
        通过行号从目录树文件中获取路径

        :param tree_file: 目录树文件
        :param line_number: 行号
        :return: 字典 {行号: 文件路径}
        """
        with open(tree_file, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                if line_num == line_number:
                    return line.strip()
