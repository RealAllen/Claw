# -*- coding: utf-8 -*-
"""统一命令行入口。

    python research/cli.py demo
    python research/cli.py backtest [--rules ...] [--data ...]
    python research/cli.py scan [--rules ...] [--data ...]
    python research/cli.py export [--rules ...]
    python research/cli.py qmt-sim            # 无券商演练：跑完整实时自动买卖链路
    python research/cli.py serve [--port 8765]

演示数据只有显式执行 demo 才会生成，且所有输出都标注 DEMO。
"""
from __future__ import print_function

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from research import backtest, data, rules_engine, server  # noqa: E402
from tools import export_rules, qmt_sim  # noqa: E402


def _load(csv_path, rules_path):
    if not os.path.isfile(csv_path):
        print("[claw] 未找到数据文件，生成演示数据：%s" % csv_path)
        data.write_csv(csv_path, data.generate_demo_bars())
    rules = rules_engine.load_rules(rules_path)
    grouped = data.group_by_symbol(data.read_csv(csv_path))
    return grouped, rules


def cmd_demo(args):
    rows = data.generate_demo_bars(symbol_count=args.symbols, day_count=args.days, seed=args.seed)
    count = data.write_csv(data.DEMO_CSV, rows)
    meta = data.demo_meta(rows)
    data.write_json(os.path.join(data.DATA_DIR, "demo_meta.json"), meta)
    payload = server.build_dashboard(rules_path=args.rules, csv_path=data.DEMO_CSV)
    js_path = os.path.join(_ROOT, "research", "web", "demo_data.js")
    with open(js_path, "w", encoding="utf-8") as handle:
        handle.write("/* 演示数据，由 `python research/cli.py demo` 生成，不是真实行情 */\n")
        handle.write("window.DEMO_DATA = ")
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write(";\n")
    print("[claw] 演示数据 %s 行 / %s 只标的 -> %s" % (count, meta["symbols"], data.DEMO_CSV))
    print("[claw] 看板离线数据 -> %s" % js_path)
    print("[claw] 注意：DEMO 数据为确定性合成数据，不得作为生产输入或投资依据")


def cmd_backtest(args):
    grouped, rules = _load(args.data, args.rules)
    result = backtest.run_backtest(grouped, rules)
    out = args.out or os.path.join(data.DATA_DIR, "backtest_result.json")
    data.write_json(out, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print("[claw] 规则指纹 %s" % result["rules"]["digest"][:16])
    print("[claw] 成交信号 %s 笔 / 风控或精算拦截 %s 笔" %
          (len(result["events"]), len(result["rejections"])))
    print("[claw] 明细 -> %s" % out)


def cmd_scan(args):
    grouped, rules = _load(args.data, args.rules)
    result = backtest.scan_latest(grouped, rules)
    out = args.out or os.path.join(data.DATA_DIR, "scan_result.json")
    data.write_json(out, result)
    print("[claw] 扫描 %s 只标的，命中 %s 个信号（is_demo=%s）" %
          (result["count"], len(result["signals"]), result["is_demo"]))
    for item in result["signals"]:
        print("  %s %s %s %s" % (item["as_of"], item["symbol"],
                                 item["signal"]["side"], item["signal"]["client_order_id"]))
    print("[claw] 明细 -> %s" % out)


def cmd_export(args):
    rules = rules_engine.load_rules(args.rules)
    path = export_rules.export(rules, args.out)
    print("[claw] 规则已导出为 Python 字面量：%s" % path)
    print("[claw] 请人工复制进券商标准版 QMT 内置 Python 沙箱；禁止建立实时桥接")


def cmd_qmt_sim(args):
    print("[claw] QMT 执行层无券商演练：假 ContextInfo，不连账户、不发真实委托")
    result = qmt_sim.run_demo(print)
    sides = {}
    for item in result["signals"]:
        sides[item.get("side")] = sides.get(item.get("side"), 0) + 1
    print("[claw] 买入信号 %s / 卖出信号 %s / 模拟下单 %s 笔 / 风控拒单 %s" %
          (sides.get("buy", 0), sides.get("sell", 0),
           result["broker_calls"], result["rejects"]))
    print("[claw] 定时器注册：%s" % (result["timer"] or "无（假对象未实现 run_time）"))
    print("[claw] 演练结束，所有安全开关已恢复默认（KILL_SWITCH / DRY_RUN / 报告备案）")


def cmd_serve(args):
    srv = server.serve(args.host, args.port)
    print("[claw] 本地看板 http://%s:%s/ （只读，Ctrl+C 退出）" % (args.host, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[claw] 已停止")
    finally:
        srv.server_close()


def build_parser():
    parser = argparse.ArgumentParser(description="Claw 本地量化研究层（只读、零依赖）")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("demo", help="生成明确标注的演示数据")
    p.add_argument("--symbols", type=int, default=12)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--seed", type=int, default=20261107)
    p.add_argument("--rules", default=None)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("backtest", help="跑回测 / 模拟盘")
    p.add_argument("--rules", default=None)
    p.add_argument("--data", default=data.DEMO_CSV)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("scan", help="扫描最新一根 bar")
    p.add_argument("--rules", default=None)
    p.add_argument("--data", default=data.DEMO_CSV)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("export", help="导出 QMT 沙箱规则字面量")
    p.add_argument("--rules", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("qmt-sim", help="无券商演练：跑完整实时自动买卖链路（假账户）")
    p.set_defaults(func=cmd_qmt_sim)

    p = sub.add_parser("serve", help="启动仅 127.0.0.1 的本地看板")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
