# -*- coding: utf-8 -*-
"""``app.runtime.log`` 替身：标准库 logger。"""

import logging

logger = logging.getLogger("varietyguard-test")
logger.addHandler(logging.NullHandler())
