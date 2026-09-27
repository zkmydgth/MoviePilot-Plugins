"""
MoviePilot V3 宿主桩（stub）—— ConfigBackup 专用最小集。

用途：让 ConfigBackup 的单元测试在没有真实 MoviePilot 的环境下可跑。
本包**不是**宿主的替代实现，只提供插件 import 期需要的模块与符号：

* ``app.runtime.config.settings``  —— 配置读取
* ``app.runtime.log.logger``       —— 日志
* ``app.application.directory``    —— DirectoryHelper
* ``app.sdk.string``               —— StringUtils
* ``app.schemas.MessageType``      —— 消息类型（V3 由 NotificationType 更名而来）
* ``app.plugins._PluginBase``      —— 插件基类（含 get_data_path / post_message）

插件与 V2 的 ``plugins.v2/configbackup`` 共用同一套测试用例，因此这里刻意
保持与 V2 桩一致的行为约定（如 ``settings`` 为可写单例、
``_PluginBase.get_data_path`` 返回测试注入的临时目录）。
"""

__version__ = "3.0.0"
IS_STUB = True
