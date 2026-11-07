# -*- coding: utf-8 -*-
"""包标记。策略内核需同时能被研究层导入与被人工复制进券商沙箱，故保持零依赖。"""
from . import strategy_core

__all__ = ["strategy_core"]
