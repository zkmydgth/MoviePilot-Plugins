"""V3 宿主桩：``app.runtime`` 包。

真实 MoviePilot V3 在该包下放置运行期能力（events / cache / settings / version 等）。
ConfigBackup 只用到 ``app.runtime.config.settings`` 与 ``app.runtime.log.logger``，
两者均为独立子模块，本包本身无需额外导出。
"""
