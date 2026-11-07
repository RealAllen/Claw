# -*- coding: utf-8 -*-
"""订单状态机：幂等去重、dry-run 闸门、超时未回报处理。

真实下单接口只在 live_config.live_order_allowed() 为真且风控通过后调用。
所有券商专属签名都隔离在 broker_api.py，本模块不感知 QMT 版本差异。
"""
from __future__ import unicode_literals

try:
    from . import broker_api
except (ImportError, ValueError):
    import broker_api

STATES = ("pending", "submitted", "partial", "filled", "cancelled", "failed", "unknown", "dry_run")


class OrderManager(object):
    def __init__(self, dry_run=True, kill_switch=True, audit=None):
        self.dry_run = bool(dry_run)
        self.kill_switch = bool(kill_switch)
        self.audit = audit
        self._seen = set()
        self._orders = {}

    def _log(self, kind, payload, as_of):
        if self.audit is not None:
            self.audit.append(kind, payload, as_of)

    def submit(self, signal, C, as_of, now_ts=None):
        """返回 (order_or_None, reason)。默认不会调用任何真实下单接口。"""
        cid = signal.get("client_order_id")
        if self.kill_switch:
            reason = "blocked:kill_switch"
            self._log("blocked", {"id": cid, "reason": reason, "symbol": signal.get("symbol")}, as_of)
            return None, reason
        if cid in self._seen:
            reason = "blocked:duplicate_signal"
            self._log("blocked", {"id": cid, "reason": reason}, as_of)
            return None, reason

        order = {
            "client_order_id": cid,
            "symbol": signal.get("symbol"),
            "side": signal.get("side"),
            "price": signal.get("price"),
            "qty": signal.get("qty"),
            "as_of": as_of,
            "state": "pending",
            "broker_order_id": None,
            "submitted_ts": now_ts,
        }
        self._seen.add(cid)
        self._orders[cid] = order

        if self.dry_run:
            order["state"] = "dry_run"
            self._log("dry_run", dict(order), as_of)
            return order, "dry_run:no_broker_call"

        try:
            broker_id = broker_api.place_order(C, signal)
            order["broker_order_id"] = broker_id
            order["state"] = "submitted"
            self._log("submitted", dict(order), as_of)
            return order, "submitted"
        except Exception as exc:
            order["state"] = "failed"
            self._log("failed", {"id": cid, "reason": str(exc)}, as_of)
            return order, "error:%s" % exc

    def on_report(self, cid, state, as_of, filled_qty=None):
        """成交回报更新状态。"""
        order = self._orders.get(cid)
        if order is None:
            return None
        if state in STATES:
            order["state"] = state
        if filled_qty is not None:
            order["filled_qty"] = filled_qty
        self._log("report", dict(order), as_of)
        return order

    def unconfirmed(self):
        return [o for o in self._orders.values() if o["state"] in ("pending", "submitted", "partial", "unknown")]

    def timed_out(self, now_ts, timeout_seconds):
        """超时未了结的委托，用于主动撤单，防止幽灵单与重复报单。"""
        out = []
        for order in self._orders.values():
            if order["state"] not in ("pending", "submitted", "partial", "unknown"):
                continue
            ts = order.get("submitted_ts")
            if ts is not None and (now_ts - ts) > timeout_seconds:
                out.append(order)
        return out

    def get(self, cid):
        return self._orders.get(cid)

    def __len__(self):
        return len(self._orders)
