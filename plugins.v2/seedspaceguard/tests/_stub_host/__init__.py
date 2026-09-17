# -*- coding: utf-8 -*-
"""
保种空间守护插件的 MoviePilot V2 宿主替身。

插件本体是 V2 插件，导入路径为 ``app.core.event`` / ``app.log`` /
``app.core.module`` / ``app.helper.service`` 等；本目录提供这些模块的最小等价
实现，使测试无需安装完整 MoviePilot 即可真实导入插件并驱动其逻辑。

仅用于测试，不参与插件分发。
"""
