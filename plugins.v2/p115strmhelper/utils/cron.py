from typing import Tuple

from apscheduler.triggers.cron import CronTrigger


class CronUtils:
    """
    cron 表达式工具类
    """

    @staticmethod
    def validate_cron_expression(cron_expr: str) -> Tuple[bool, str]:
        """
        验证 cron 表达式是否有效

        :param cron_expr: cron 表达式字符串
        """
        if not cron_expr or not cron_expr.strip():
            return False, ""

        try:
            CronTrigger.from_crontab(cron_expr)
            return True, ""
        except (ValueError, TypeError) as e:
            return False, f"无效的 cron 表达式 '{cron_expr}': {e}"
        except Exception as e:
            return False, f"验证 cron 表达式时发生错误 '{cron_expr}': {e}"

    @staticmethod
    def fix_cron_expression(cron_expr: str) -> str:
        """
        修复 cron 表达式错误

        :param cron_expr: 原始 cron 表达式
        :return: 修复后的 cron 表达式
        """
        if not cron_expr:
            return cron_expr

        parts = cron_expr.split()
        if len(parts) != 5:
            return cron_expr

        # 定义各字段的范围
        field_ranges = [
            (0, 59),  # 分钟 (0-59)
            (0, 23),  # 小时 (0-23)
            (1, 31),  # 日期 (1-31)
            (1, 12),  # 月份 (1-12)
            (0, 7),  # 星期 (0-7, 0和7都表示周日)
        ]

        fixed_parts = []

        for i, part in enumerate(parts):
            min_val, max_val = field_ranges[i]
            fixed_part = CronUtils._fix_cron_field(part, min_val, max_val)
            fixed_parts.append(fixed_part)

        return " ".join(fixed_parts)

    @staticmethod
    def _fix_cron_field(field: str, min_val: int, max_val: int) -> str:
        """
        修复单个 cron 字段

        :param field: 字段值
        :param min_val: 最小值
        :param max_val: 最大值
        :return: 修复后的字段值
        """
        if field == "*":
            return field

        # 处理范围表达式 (如 1-5)
        if "-" in field:
            start, end = field.split("-", 1)
            try:
                start_val = int(start)
                end_val = int(end)
                if end_val > max_val:
                    end_val = max_val
                if start_val < min_val:
                    start_val = min_val
                return f"{start_val}-{end_val}"
            except ValueError:
                return field

        # 处理步长表达式 (如 */5, 0/10)
        if "/" in field:
            start, step = field.split("/", 1)
            try:
                step_val = int(step)
                if step_val > max_val:
                    step_val = max_val
                elif step_val < 1:
                    step_val = 1

                if start == "*":
                    return f"*/{step_val}"
                else:
                    start_val = int(start)
                    if start_val > max_val:
                        start_val = max_val
                    elif start_val < min_val:
                        start_val = min_val
                    return f"{start_val}/{step_val}"
            except ValueError:
                return field

        # 处理逗号分隔的列表 (如 1,3,5)
        if "," in field:
            values = field.split(",")
            fixed_values = []
            for val in values:
                try:
                    int_val = int(val.strip())
                    if int_val > max_val:
                        int_val = max_val
                    elif int_val < min_val:
                        int_val = min_val
                    fixed_values.append(str(int_val))
                except ValueError:
                    fixed_values.append(val.strip())
            return ",".join(fixed_values)

        # 处理单个数值
        try:
            int_val = int(field)
            if int_val > max_val:
                int_val = max_val
            elif int_val < min_val:
                int_val = min_val
            return str(int_val)
        except ValueError:
            return field

    @staticmethod
    def get_default_cron() -> str:
        """
        获取默认的 cron 表达式

        :return: 默认 cron 表达式
        """
        return "0 */6 * * *"

    @staticmethod
    def is_valid_cron(cron_expr: str) -> bool:
        """
        简单检查 cron 表达式是否有效

        :param cron_expr: cron 表达式
        :return: 是否有效
        """
        status, _ = CronUtils.validate_cron_expression(cron_expr)
        return status
