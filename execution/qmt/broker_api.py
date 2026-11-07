# -*- coding: utf-8 -*-
"""QMT 券商 API 适配层：把所有「因券商而异」的调用集中在这一个文件里。

设计目的
--------
1. 策略逻辑（strategy.py / strategy_core.py）不依赖任何具体 API 签名；
   换券商或 QMT 版本升级时，只改本文件。
2. 所有调用都容错：在没有 QMT 运行时的普通 Python 里，用假 C 对象也能跑通
   全链路测试；真实环境下接口名/参数不符时返回空值或抛明确异常，不静默下单。
3. 遵守沙箱约束：无 open()、无文件读写、无 socket/HTTP、无数据库、无第三方库。
   Python 3.6.8 语法。

⚠️ 必须实测核对（浙商证券定制版 QMT）
------------------------------------
下面这些名称与参数来自迅投 QMT 通用约定，**不同券商定制版会有差异**：
  - passorder(opType, orderType, accountid, orderCode, prType, price, volume,
              strategyName, quickTrade, userOrderId, ContextInfo)
  - get_trade_detail_data(accountid, 'stock', 'account'|'position'|'order'|'deal')
  - get_market_data_ex(fields, stock_list, period, count) 的返回结构
  - get_full_tick(stock_list) 的字段名
  - get_instrument_detail(code) 的上市日期字段
  - run_time(func_name, period, start_time) 的定时器周期写法
第一次在 QMT 里运行时先调用 probe(C)，把输出与券商官方 QMT 文档逐项对照。
任何不一致都改本文件，不要改策略内核。

本模块不读取、不保存、不输出交易密码或资金密码；账号在日志里一律脱敏。
"""
from __future__ import unicode_literals

try:
    from . import live_config as cfg
    from . import audit as audit_mod
except (ImportError, ValueError):
    import live_config as cfg
    import audit as audit_mod

_PASSORDER = None
_CANCEL = None

_API_NAMES = (
    "get_full_tick", "subscribe_whole_quote", "get_stock_list_in_sector",
    "get_market_data_ex", "get_instrument_detail", "get_trade_detail_data",
    "run_time", "get_account", "get_account_list", "passorder", "cancel",
)


# ------------------------------------------------------------------ 注入 / 探测
def register_passorder(fn):
    """由 strategy.init 把 QMT 注入的全局 passorder 注册进来。"""
    global _PASSORDER
    _PASSORDER = fn


def register_cancel(fn):
    global _CANCEL
    _CANCEL = fn


def _attr(obj, *names):
    """同时兼容 QMT 返回对象（m_xxx 属性）与 dict（含测试替身）。"""
    for name in names:
        if isinstance(obj, dict) and name in obj and obj[name] is not None:
            return obj[name]
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _as_list(obj):
    """把 pandas Series / numpy array / list 统一成 float list，不 import 任何第三方库。"""
    if obj is None:
        return None
    if hasattr(obj, "tolist"):
        try:
            return [float(x) for x in obj.tolist()]
        except Exception:
            pass
    if hasattr(obj, "values"):
        try:
            return [float(x) for x in obj.values.tolist()]
        except Exception:
            pass
    try:
        return [float(x) for x in obj]
    except Exception:
        return None


def _rows(raw):
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return [raw]


def detect_account(C):
    """返回资金账号字符串（非密码）。探测不到返回空串。"""
    if cfg.ACCOUNT_ID:
        return str(cfg.ACCOUNT_ID)
    direct = getattr(C, "account_id", None) or getattr(C, "fund_account", None)
    if direct:
        return str(direct)
    for name in ("get_account_list", "get_accounts", "get_account"):
        fn = getattr(C, name, None)
        if not callable(fn):
            continue
        try:
            value = fn()
        except Exception:
            continue
        if isinstance(value, (list, tuple)) and value:
            first = value[0]
            found = _attr(first, "m_strAccountID", "account_id", "account", "accountId")
            if found:
                return str(found)
        elif value:
            found = _attr(value, "m_strAccountID", "account_id", "account", "accountId")
            if found:
                return str(found)
    return ""


def probe(C):
    """首次运行诊断：列出可用 API、探测到的账号（脱敏）与定时器状态。"""
    available = []
    for name in _API_NAMES:
        if hasattr(C, name):
            available.append(name)
    account = detect_account(C)
    return {
        "broker": cfg.BROKER_NAME,
        "available_api": available,
        "passorder_registered": _PASSORDER is not None or callable(getattr(C, "passorder", None)),
        "cancel_registered": _CANCEL is not None or callable(getattr(C, "cancel", None)),
        "account_id": audit_mod.mask(account) if account else "(未探测到，请填写 live_config.ACCOUNT_ID)",
        "account_detected": bool(account),
        "note": "把 available_api 与本文件顶部注释及浙商证券官方 QMT 文档逐项核对",
    }


# ------------------------------------------------------------------ 行情
def _normalize_code(code):
    text = str(code or "").strip()
    if not text:
        return ""
    if "." in text:
        return text
    if text[0] in ("6", "9"):
        return text + ".SH"
    if text[0] in ("0", "1", "2", "3"):
        return text + ".SZ"
    if text[0] in ("4", "8"):
        return text + ".BJ"
    return text


def get_stock_list(C):
    """全市场代码池。返回 code 列表；探测失败返回空列表。"""
    fake = getattr(C, "stock_list", None)
    if fake:
        return [_normalize_code(x) for x in fake]
    fn = getattr(C, "get_stock_list_in_sector", None)
    if not callable(fn):
        return []
    for sector in cfg.UNIVERSE_SECTORS:
        try:
            codes = fn(sector) or []
        except Exception:
            continue
        if codes:
            return [_normalize_code(x) for x in codes]
    return []


def get_full_tick(C, codes):
    """全市场 L1 快照（QMT 全推，约 50ms 增量）。返回 {code: tick_dict}。"""
    fake = getattr(C, "full_tick", None)
    if fake is not None:
        return fake or {}
    fn = getattr(C, "get_full_tick", None)
    if fn is None:
        return {}
    try:
        return fn(codes) or {}
    except TypeError:
        try:
            return fn() or {}
        except Exception:
            return {}
    except Exception:
        return {}


def _series(container, field, code):
    """从 get_market_data_ex 的多种返回结构里取出某一字段序列。"""
    candidates = []
    try:
        candidates.append(container[field])
    except Exception:
        pass
    try:
        candidates.append(container[code])
    except Exception:
        pass
    for obj in candidates:
        if obj is None:
            continue
        sub = None
        try:
            sub = obj[code]
        except Exception:
            pass
        if sub is None:
            try:
                sub = obj[field]
            except Exception:
                pass
        for item in (sub, obj):
            values = _as_list(item)
            if values:
                return values
    return None


def _to_bars(raw, code):
    """把 QMT 历史行情转换成 indicators.precision_metrics 需要的 bar list。"""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        if raw and isinstance(raw[0], dict):
            return [dict(b) for b in raw]
        return []
    if not isinstance(raw, dict):
        return []
    closes = _series(raw, "close", code)
    if not closes:
        return []
    series = {}
    for field in ("open", "high", "low", "volume", "amount"):
        series[field] = _series(raw, field, code)
    bars = []
    for i, close in enumerate(closes):
        bar = {"close": close, "high": close, "low": close, "volume": 0.0}
        for field, seq in series.items():
            if seq and i < len(seq):
                bar[field] = seq[i]
        bars.append(bar)
    return bars


def get_history(C, code):
    """取单只标的历史 K 线；只对一级筛选命中的候选调用。"""
    fake = getattr(C, "history", None)
    if callable(fake):
        try:
            return _to_bars(fake(code), code)
        except Exception:
            return []
    fn = getattr(C, "get_market_data_ex", None)
    if not callable(fn):
        return []
    try:
        raw = fn([], [code], period=cfg.HISTORY_PERIOD, count=cfg.HISTORY_COUNT)
    except TypeError:
        try:
            raw = fn([], [code], cfg.HISTORY_PERIOD, cfg.HISTORY_COUNT)
        except Exception:
            return []
    except Exception:
        return []
    return _to_bars(raw, code)


def list_days(C, code):
    """上市天数，用于排除次新。QMT 无该接口时返回 None。"""
    fake = getattr(C, "list_days", None)
    if isinstance(fake, dict) and code in fake:
        return fake[code]
    fn = getattr(C, "get_instrument_detail", None)
    if not callable(fn):
        return None
    try:
        info = fn(code) or {}
    except Exception:
        return None
    text = _attr(info, "OpenDate", "open_date", "openDate", "ListingDate", "listing_date")
    if not text:
        return None
    digits = str(text).replace("-", "").replace("/", "").strip()[:8]
    if len(digits) != 8 or not digits.isdigit():
        return None
    try:
        import datetime
        start = datetime.date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
        return max(0, (datetime.date.today() - start).days)
    except Exception:
        return None


def subscribe_whole_quote(C):
    """若券商版本支持全推订阅则订阅；不支持时由 get_full_tick 兜底。"""
    fn = getattr(C, "subscribe_whole_quote", None)
    if not callable(fn):
        return False
    try:
        fn()
        return True
    except TypeError:
        for codes in (get_stock_list(C), []):
            try:
                fn(codes)
                return True
            except Exception:
                continue
    except Exception:
        return False
    return False


# ------------------------------------------------------------------ 账户 / 持仓 / 委托
def query_account(C, account_id):
    """返回 cash / total_equity / day_* 等风控所需字段。"""
    fake = getattr(C, "account", None)
    if isinstance(fake, dict) and fake:
        return dict(fake)
    fn = getattr(C, "get_trade_detail_data", None)
    if not callable(fn) or not account_id:
        return {}
    rows = []
    for acct_type in (cfg.ACCOUNT_TYPE, "stock", "STOCK"):
        try:
            rows = _rows(fn(account_id, acct_type, "account"))
        except Exception:
            rows = []
        if rows:
            break
    for row in rows:
        cash = _attr(row, "m_dAvailable", "m_dBalance", "available", "cash")
        equity = _attr(row, "m_dAssetBalance", "m_dTotalAsset", "total_equity")
        market_value = _attr(row, "m_dMarketValue", "market_value")
        if cash is None and equity is None:
            continue
        cash = float(cash or 0.0)
        equity = float(equity if equity is not None else (cash + float(market_value or 0.0)))
        return {"cash": cash, "total_equity": equity}
    return {}


def query_positions(C, account_id):
    """返回 {code: {qty, available, cost, last, market_value}}。"""
    fake = getattr(C, "account", None)
    if isinstance(fake, dict) and "positions" in fake:
        result = {}
        prices = fake.get("position_prices", {}) or {}
        costs = fake.get("position_cost", {}) or {}
        locked = fake.get("t1_locked", {}) or {}
        highs = fake.get("high_since_entry", {}) or {}
        for symbol, qty in (fake.get("positions") or {}).items():
            result[str(symbol)] = {
                "qty": float(qty or 0),
                "available": max(0.0, float(qty or 0) - float(locked.get(symbol, 0) or 0)),
                "cost": costs.get(symbol),
                "last": prices.get(symbol),
                "market_value": None,
                "high_since_entry": highs.get(symbol),
            }
        return result
    fn = getattr(C, "get_trade_detail_data", None)
    if not callable(fn) or not account_id:
        return {}
    rows = []
    for acct_type in (cfg.ACCOUNT_TYPE, "stock", "STOCK"):
        try:
            rows = _rows(fn(account_id, acct_type, "position"))
        except Exception:
            rows = []
        if rows:
            break
    result = {}
    for row in rows:
        code = _attr(row, "m_strInstrumentID", "m_strStockCode", "symbol", "instrument_id", "code")
        if not code:
            continue
        code = _normalize_code(code)
        qty = _attr(row, "m_nVolume", "m_nTotalVol", "qty", "volume") or 0
        available = _attr(row, "m_nCanUseVolume", "m_nEnableVolume", "available", "can_use_volume")
        available = qty if available is None else available
        result[code] = {
            "qty": float(qty),
            "available": float(available),
            "cost": _attr(row, "m_dOpenPrice", "m_dAvgOpenPrice", "cost", "open_price"),
            "last": _attr(row, "m_dLastPrice", "last_price", "last"),
            "market_value": _attr(row, "m_dMarketValue", "market_value"),
            "high_since_entry": None,
        }
    return result


def query_orders(C, account_id):
    """返回当日委托列表，用于回报轮询与超时撤单。"""
    fake = getattr(C, "orders", None)
    if isinstance(fake, (list, tuple)):
        return [dict(x) for x in fake]
    fn = getattr(C, "get_trade_detail_data", None)
    if not callable(fn) or not account_id:
        return []
    rows = []
    for acct_type in (cfg.ACCOUNT_TYPE, "stock", "STOCK"):
        try:
            rows = _rows(fn(account_id, acct_type, "order"))
        except Exception:
            rows = []
        if rows:
            break
    result = []
    for row in rows:
        status = _attr(row, "m_nOrderStatus", "order_status", "status")
        filled = _attr(row, "m_nVolumeTraded", "filled_qty", "traded_volume") or 0
        total = _attr(row, "m_nVolumeTotalOriginal", "qty", "volume") or 0
        state = "unknown"
        text = str(status or "").lower()
        if "已成" in str(status or "") or text in ("filled", "2", "52"):
            state = "filled"
        elif "部成" in str(status or "") or text in ("partial", "1", "51"):
            state = "partial"
        elif "已撤" in str(status or "") or "撤单" in str(status or "") or text in ("cancelled", "3", "54"):
            state = "cancelled"
        elif "废单" in str(status or "") or text in ("failed", "rejected", "4", "57"):
            state = "failed"
        elif float(total or 0) > 0 and float(filled or 0) >= float(total or 0):
            state = "filled"
        elif float(filled or 0) > 0:
            state = "partial"
        else:
            state = "submitted"
        result.append({
            "broker_order_id": _attr(row, "m_strOrderSysID", "order_id", "broker_order_id"),
            "client_order_id": _attr(row, "m_strOrderRef", "m_strRemark", "m_strOrderRemark",
                                     "client_order_id", "user_order_id"),
            "symbol": _normalize_code(_attr(row, "m_strInstrumentID", "symbol", "code") or ""),
            "state": state,
            "qty": float(total or 0),
            "filled_qty": float(filled or 0),
        })
    return result


# ------------------------------------------------------------------ 下单 / 撤单 / 定时器
def place_order(C, signal):
    """真正调用券商下单。仅在 live_config.live_order_allowed() 为真时由订单管理器调用。"""
    legacy = getattr(C, "passorder", None)
    if callable(legacy):
        # 测试替身 / 券商若把 passorder 挂在 ContextInfo 上的简化签名
        return legacy(signal["side"], signal["symbol"], signal["price"],
                      signal["qty"], signal["client_order_id"])
    fn = _PASSORDER
    if fn is None:
        raise RuntimeError("passorder_unavailable: 需在 QMT 内置策略中注册全局 passorder")
    account_id = detect_account(C)
    if not account_id:
        raise RuntimeError("account_id_unavailable: 请填写 live_config.ACCOUNT_ID")
    op = cfg.OP_BUY if signal.get("side") == "buy" else cfg.OP_SELL
    return fn(op, cfg.ORDER_TYPE, account_id, signal["symbol"], cfg.PRICE_TYPE,
              signal["price"], signal["qty"], cfg.STRATEGY_NAME,
              cfg.QUICK_TRADE, signal["client_order_id"], C)


def cancel_order(C, broker_order_id):
    legacy = getattr(C, "cancel", None)
    if callable(legacy):
        try:
            legacy(broker_order_id)
            return True
        except Exception:
            return False
    fn = _CANCEL
    if fn is None or not broker_order_id:
        return False
    account_id = detect_account(C)
    for args in ((broker_order_id, account_id, cfg.ACCOUNT_TYPE, C),
                 (broker_order_id, account_id, cfg.ACCOUNT_TYPE),
                 (broker_order_id,)):
        try:
            fn(*args)
            return True
        except Exception:
            continue
    return False


def start_timer(C, func_name):
    """注册 QMT 定时器；func_name 必须是 strategy.py 里的模块级函数名。"""
    fn = getattr(C, "run_time", None)
    if not callable(fn):
        return False
    for args in ((func_name, cfg.SCAN_PERIOD, cfg.TIMER_START),
                 (func_name, cfg.SCAN_PERIOD)):
        try:
            fn(*args)
            return True
        except Exception:
            continue
    return False


def now_parts():
    """实时时钟（仅实盘用；handlebar 回测用 C.date / C.hhmm）。"""
    import datetime
    import time
    current = datetime.datetime.now()
    return current.strftime("%Y%m%d"), current.strftime("%H:%M"), time.time()
