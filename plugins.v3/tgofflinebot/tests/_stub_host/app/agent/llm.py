# -*- coding: utf-8 -*-
"""
``LLMHelper`` 替身：未配置模型时返回 None。
"""


class LLMHelper:
    """LLM 助手替身。"""

    @staticmethod
    def get_llm(streaming: bool = False):
        """
        返回空客户端，迫使调用方走「AI 不可用」分支。

        :param streaming: 是否流式
        :return: None
        """
        return None
