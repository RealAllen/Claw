# -*- coding: utf-8 -*-
"""内存 append-only 审计日志。

券商沙箱禁止文件读写，因此日志只存内存，由使用者在 QMT 界面人工导出。
不含任何账号、密码、资金密码字段。
"""
from __future__ import unicode_literals


def mask(value):
    """脱敏：只保留首尾各 2 位，中间用星号。"""
    if value is None:
        return ""
    text = str(value)
    if len(text) <= 4:
        return "****"
    return text[:2] + "*" * (len(text) - 4) + text[-2:]


class AuditLog(object):
    """定长环形缓冲，append-only（不提供修改/删除单条的接口）。"""

    def __init__(self, capacity=5000):
        self.capacity = int(capacity)
        self._items = []

    def append(self, kind, payload, as_of):
        item = {
            "seq": len(self._items) + 1,
            "as_of": as_of,
            "kind": kind,
            "payload": dict(payload or {}),
        }
        self._items.append(item)
        if len(self._items) > self.capacity:
            self._items = self._items[-self.capacity:]
        return item

    def dump(self):
        return list(self._items)

    def drain(self):
        """导出并清空（人工导出用，不自动落盘）。"""
        items = list(self._items)
        self._items = []
        return items

    def __len__(self):
        return len(self._items)
