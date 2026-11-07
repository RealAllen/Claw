# -*- coding: utf-8 -*-
"""规则导出：JSON -> Python 字面量（人工中转，禁止实时桥接）。"""
import hashlib
import io
import os
import pprint
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from research import rules_engine  # noqa: E402

DEFAULT_OUT = os.path.join(_ROOT, "execution", "qmt", "rules_snapshot.py")


def file_sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def export(rules, out=None):
    """生成 execution/qmt/rules_snapshot.py，返回路径。"""
    out = out or DEFAULT_OUT
    payload = dict(rules)
    payload.pop("_source_path", None)
    core_path = os.path.join(_ROOT, "shared", "strategy_core.py")
    digest = rules_engine.rule_digest(rules)
    core_hash = file_sha256(core_path) if os.path.isfile(core_path) else "missing"
    buf = io.StringIO()
    pprint.pprint(payload, stream=buf, width=100)
    literal = buf.getvalue()

    text = (
        "# -*- coding: utf-8 -*-\n"
        "# 由 `python research/cli.py export` 生成，请勿手工编辑。\n"
        "# 盘前由人工导入券商标准版 QMT 内置 Python 沙箱；禁止实时桥接。\n"
        "# 规则指纹：%s\n"
        "# 策略内核哈希：%s\n"
        "RULE_DIGEST = %r\n"
        "CORE_SHA256 = %r\n"
        "RULES = %s\n"
    ) % (digest, core_hash, digest, core_hash, literal)

    parent = os.path.dirname(os.path.abspath(out))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(text)

    core_out = os.path.join(os.path.dirname(os.path.abspath(out)), "strategy_core.py")
    indicators_src = os.path.join(_ROOT, "research", "indicators.py")
    indicators_out = os.path.join(os.path.dirname(os.path.abspath(out)), "indicators.py")
    for src, dst in ((core_path, core_out), (indicators_src, indicators_out)):
        if os.path.isfile(src):
            with open(src, "rb") as handle:
                content = handle.read()
            with open(dst, "wb") as handle:
                handle.write(content)
    return os.path.abspath(out)
