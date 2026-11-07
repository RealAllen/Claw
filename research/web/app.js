/* Claw 本地看板：无外部 CDN，静态打开时用 demo_data.js，服务模式用 /api/dashboard。 */
(function () {
  'use strict';

  function el(id) { return document.getElementById(id); }
  function fmt(value, digits) {
    if (value === null || value === undefined || value === '') return '—';
    var n = Number(value);
    if (isNaN(n)) return String(value);
    return n.toFixed(digits === undefined ? 2 : digits);
  }
  function esc(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function tbody(id) { return el(id).getElementsByTagName('tbody')[0]; }

  function renderKpis(d) {
    var s = d.summary || {};
    var items = [
      ['总收益', fmt(s.total_return_pct) + '%', '模拟盘'],
      ['最大回撤', fmt(s.max_drawdown_pct) + '%', '历史净值口径'],
      ['Sharpe', fmt(s.sharpe), '年化 252 期'],
      ['胜率', fmt(s.win_rate_pct) + '%', (s.closed_trades || 0) + ' 笔平仓'],
      ['成交信号', String(d.event_count || 0), '含买入与卖出'],
      ['拦截记录', String(d.rejection_count || 0), '风控 / 精算']
    ];
    var html = '';
    for (var i = 0; i < items.length; i++) {
      html += '<div class="kpi"><div class="label">' + esc(items[i][0]) + '</div>' +
        '<div class="value">' + esc(items[i][1]) + '</div>' +
        '<div class="foot">' + esc(items[i][2]) + '</div></div>';
    }
    el('kpis').innerHTML = html;
  }

  function drawChart(curve) {
    var host = el('chart');
    if (!curve || curve.length < 2) { host.innerHTML = '<p class="empty">净值数据不足</p>'; return; }
    var W = 800, H = 220, L = 56, R = 16, T = 16, B = 28;
    var min = Math.min.apply(null, curve), max = Math.max.apply(null, curve);
    if (max === min) { max = min + 1; }
    var pad = (max - min) * 0.08;
    min -= pad; max += pad;
    var xs = [], ys = [];
    for (var i = 0; i < curve.length; i++) {
      xs.push(L + (W - L - R) * i / (curve.length - 1));
      ys.push(T + (H - T - B) * (1 - (curve[i] - min) / (max - min)));
    }
    var parts = [];
    parts.push('<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="模拟盘净值曲线">');
    for (var g = 0; g <= 3; g++) {
      var gy = T + (H - T - B) * g / 3;
      var gv = max - (max - min) * g / 3;
      parts.push('<line x1="' + L + '" y1="' + gy + '" x2="' + (W - R) + '" y2="' + gy + '" stroke="#e3e6eb" stroke-width="0.5"/>');
      parts.push('<text x="' + (L - 6) + '" y="' + (gy + 4) + '" text-anchor="end" font-size="11" fill="#6b7280">' + fmt(gv, 0) + '</text>');
    }
    var poly = [];
    for (var j = 0; j < xs.length; j++) { poly.push(xs[j].toFixed(1) + ',' + ys[j].toFixed(1)); }
    parts.push('<polyline points="' + poly.join(' ') + '" fill="none" stroke="#185fa5" stroke-width="1.8" stroke-linejoin="round"/>');
    parts.push('<circle cx="' + xs[xs.length - 1].toFixed(1) + '" cy="' + ys[ys.length - 1].toFixed(1) + '" r="3" fill="#185fa5"/>');
    parts.push('<text x="' + L + '" y="' + (H - 8) + '" font-size="11" fill="#6b7280">起始</text>');
    parts.push('<text x="' + (W - R) + '" y="' + (H - 8) + '" text-anchor="end" font-size="11" fill="#6b7280">最新</text>');
    parts.push('</svg>');
    host.innerHTML = parts.join('');
  }

  function fillRows(id, rows, build) {
    var body = tbody(id), html = '';
    if (!rows || !rows.length) { html = '<tr><td colspan="7" class="empty">暂无记录</td></tr>'; }
    for (var i = 0; i < rows.length; i++) { html += build(rows[i]); }
    body.innerHTML = html;
  }

  function renderEvents(rows) {
    fillRows('events', rows, function (r) {
      var cls = r.side === 'buy' ? 'buy' : 'sell';
      return '<tr><td>' + esc(r.as_of) + '</td><td>' + esc(r.symbol) + '</td>' +
        '<td class="' + cls + '">' + (r.side === 'buy' ? '买入' : '卖出') + '</td>' +
        '<td>' + fmt(r.price, 3) + '</td><td>' + esc(r.qty) + '</td><td>' + fmt(r.amount) + '</td>' +
        '<td><span class="tag">' + esc(String(r.client_order_id || '').slice(0, 12)) + '</span></td></tr>';
    });
  }

  function renderRejections(rows) {
    fillRows('rejections', rows, function (r) {
      var reasons = (r.reasons || []).map(function (x) { return esc(x); }).join('；');
      return '<tr><td>' + esc(r.as_of) + '</td><td>' + esc(r.symbol) + '</td>' +
        '<td>' + esc(r.stage === 'risk' ? '风控' : '精算') + '</td>' +
        '<td>' + reasons + '</td></tr>';
    });
  }

  function renderScan(rows) {
    fillRows('scan', rows, function (r) {
      var sig = r.signal ? '<span class="tag">' + (r.signal.side === 'buy' ? '买入信号' : '卖出信号') + '</span>' : '—';
      return '<tr><td>' + esc(r.symbol) + '</td><td>' + fmt(r.last, 3) + '</td>' +
        '<td>' + fmt(r.pct_chg) + '%</td><td>' + fmt(r.vol_x) + '</td><td>' + sig + '</td></tr>';
    });
  }

  function renderStress(rows) {
    fillRows('stress', rows, function (r) {
      return '<tr><td>' + esc(r.scenario) + '</td><td>' + fmt(r.shock_pct) + '%</td>' +
        '<td>' + fmt(r.loss) + '</td><td>' + fmt(r.equity_after) + '</td></tr>';
    });
  }

  function renderCapabilities(list) {
    var body = tbody('cap'), html = '';
    if (!list || !list.length) {
      html = '<tr><td colspan="4" class="empty">启动本地服务后可见架构对照表</td></tr>';
    }
    for (var i = 0; i < (list || []).length; i++) {
      var c = list[i];
      var local = c.in_local_web === false
        ? '<span class="tag">否 · QMT 客户端</span>'
        : esc(c.in_local_web === true ? '是' : c.in_local_web);
      html += '<tr><td>' + esc(c.goal) + '</td><td>' + esc(c.where) + '</td>' +
        '<td>' + esc(c.how) + '</td><td>' + local + '</td></tr>';
    }
    body.innerHTML = html;
  }

  function renderSafety(d) {
    var s = d.safety || {};
    var rows = [
      ['只读研究层', s.read_only ? '是' : '否'],
      ['Kill Switch', s.kill_switch ? '开启' : '关闭'],
      ['交易模式', s.mode === 'semi_auto' ? '半自动（人工确认）' : esc(s.mode)],
      ['服务绑定', esc(s.bind || '127.0.0.1')],
      ['应用内账户凭据', s.credentials_in_app ? '存在（异常）' : '无'],
      ['实时下单接口', '无（本层不承担）'],
      ['实时交易位置', '浙商证券标准版 QMT 内置沙箱'],
      ['本页作用', '盘前配置 / 盘后复盘']
    ];
    var html = '';
    for (var i = 0; i < rows.length; i++) {
      html += '<li><span>' + esc(rows[i][0]) + '</span><b>' + rows[i][1] + '</b></li>';
    }
    el('safety').innerHTML = html;
  }

  function render(d, offline) {
    var meta = d.meta || {};
    var badge = el('badge-demo');
    var isDemo = meta.is_demo !== false;
    badge.textContent = isDemo ? 'DEMO / 演示数据' : '真实数据（本地）';
    badge.className = isDemo ? 'badge warn' : 'badge ok';
    if (offline) { badge.textContent += ' · 离线'; }

    renderKpis(d);
    renderCapabilities(d.capabilities);
    drawChart(d.equity_curve);
    el('curve-hint').textContent = (d.equity_curve || []).length + ' 个观测点';
    var a = d.assumptions || {};
    el('assumptions').textContent = '假设：初始资金 ' + fmt(a.initial_cash) +
      ' 元 / 手续费 ' + fmt(a.fee_rate, 4) + ' / 印花税 ' + fmt(a.stamp_duty, 4) +
      ' / 滑点 ' + fmt(a.slippage, 4) + ' / T+1；模拟盘结果，非真实成交。';

    renderEvents(d.events);
    renderRejections(d.rejections);
    renderScan(d.scan);
    renderStress(d.stress);
    renderSafety(d);
    el('events-hint').textContent = '共 ' + (d.event_count || 0) + ' 笔，显示前 ' + (d.events || []).length;
    el('reject-hint').textContent = '共 ' + (d.rejection_count || 0) + ' 笔';
    el('scan-hint').textContent = (d.scan_signals || []).length + ' 个信号 / ' + (d.scan || []).length + ' 只标的';
    el('meta').textContent = '数据来源：' + esc(meta.source || '本地') +
      ' · ' + esc(meta.from || '') + ' 至 ' + esc(meta.to || '') +
      ' · ' + esc(meta.rows || 0) + ' 行 / ' + esc(meta.symbols || 0) + ' 只标的' +
      ' · 规则 ' + esc((d.rules || {}).name || '') + ' v' + esc((d.rules || {}).version || '') +
      ' · 指纹 ' + esc(String((d.rules || {}).digest || '').slice(0, 12));
  }

  function fail(message) {
    el('kpis').innerHTML = '<div class="kpi"><div class="label">数据</div><div class="value">不可用</div><div class="foot">' + esc(message) + '</div></div>';
    el('chart').innerHTML = '<p class="empty">请运行 python research/cli.py demo 生成演示数据</p>';
  }

  fetch('api/dashboard', { cache: 'no-store' })
    .then(function (resp) { if (!resp.ok) { throw new Error('HTTP ' + resp.status); } return resp.json(); })
    .then(function (payload) { render(payload, false); })
    .catch(function () {
      if (window.DEMO_DATA) { render(window.DEMO_DATA, true); }
      else { fail('未找到本地服务，也未找到演示数据'); }
    });
})();
