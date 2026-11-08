/* Claw 策略配置台：编辑本地规则 JSON。无外部依赖，不与 QMT 建立任何实时通道。 */
(function () {
  'use strict';

  var DATA = null;
  var RISK_LABELS = {
    max_order_amount: '单笔上限(元)',
    max_position_pct: '单票仓位上限(%)',
    max_total_position_pct: '总仓位上限(%)',
    max_daily_orders: '日内最大委托笔数',
    max_daily_loss_pct: '日内最大亏损(%)',
    max_daily_turnover: '日内成交总额(元)',
    lot_size: '最小交易单位(股)'
  };
  var CONTAINERS = {
    'buy-cond': 'buy.conditions',
    'buy-precision': 'buy.precision_conditions',
    'sell-cond': 'sell.conditions'
  };

  function el(id) { return document.getElementById(id); }
  function esc(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function msg(text, bad) { el('msg').textContent = text; el('msg').style.color = bad ? '#a32d2d' : '#6b7280'; }

  function parseValue(text) {
    var raw = String(text === null || text === undefined ? '' : text).trim();
    if (raw === '') { return ''; }
    try { return JSON.parse(raw); } catch (e) { /* 继续按字符串/数字处理 */ }
    var num = Number(raw);
    if (isFinite(num)) { return num; }
    return raw;
  }
  function showValue(value) {
    if (value === null || value === undefined) { return ''; }
    if (typeof value === 'object') { return JSON.stringify(value); }
    return String(value);
  }

  function allFields() {
    var groups = [
      ['一级 · 快照指标', DATA.fields.level1],
      ['二级 · K 线指标', DATA.fields.level2],
      ['持仓 · 卖出指标', DATA.fields.position]
    ];
    var html = '';
    for (var i = 0; i < groups.length; i++) {
      html += '<optgroup label="' + esc(groups[i][0]) + '">';
      for (var j = 0; j < groups[i][1].length; j++) {
        var f = groups[i][1][j];
        html += '<option value="' + esc(f.key) + '">' + esc(f.label) + ' (' + esc(f.key) + ')</option>';
      }
      html += '</optgroup>';
    }
    return html;
  }

  function renderRow(containerId, condition) {
    var wrap = document.createElement('div');
    wrap.className = 'cond';
    var fields = '<select data-role="field">' + allFields() + '</select>';
    var ops = '<select data-role="op">';
    for (var i = 0; i < DATA.operators.length; i++) {
      var o = DATA.operators[i];
      ops += '<option value="' + esc(o.key) + '">' + esc(o.label) + ' (' + esc(o.key) + ')</option>';
    }
    ops += '</select>';
    wrap.innerHTML = fields + ops +
      '<input type="text" data-role="value" placeholder="阈值，区间写 [0.5, 6]，布尔写 true">' +
      '<button class="btn danger" data-remove="1" title="删除">×</button>';
    wrap.querySelector('[data-role=field]').value = condition.field || '';
    wrap.querySelector('[data-role=op]').value = condition.op || '>=';
    wrap.querySelector('[data-role=value]').value = showValue(condition.value);
    el(containerId).appendChild(wrap);
  }

  function readConditions(containerId) {
    var rows = el(containerId).querySelectorAll('.cond');
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      out.push({
        field: row.querySelector('[data-role=field]').value,
        op: row.querySelector('[data-role=op]').value,
        value: parseValue(row.querySelector('[data-role=value]').value)
      });
    }
    return out;
  }

  function formatWindows(windows) {
    return (windows || []).map(function (p) { return p[0] + '-' + p[1]; }).join(', ');
  }
  function parseWindows(text) {
    return String(text || '').split(/[,，]/).map(function (s) { return s.trim(); })
      .filter(function (s) { return s; })
      .map(function (part) {
        var bits = part.split(/[-~—]/).map(function (s) { return s.trim(); });
        return [bits[0], bits[1] || bits[0]];
      });
  }

  function renderCapabilities(list) {
    var body = el('cap-config').getElementsByTagName('tbody')[0];
    var html = '';
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      var local = c.in_local_web === false
        ? '<span class="tag no">否 · QMT 客户端</span>'
        : '<span class="tag yes">' + esc(c.in_local_web === true ? '是' : c.in_local_web) + '</span>';
      html += '<tr><td>' + esc(c.goal) + '</td><td class="where">' + esc(c.where) + '</td>' +
        '<td>' + esc(c.how) + '</td><td>' + local + '</td></tr>';
    }
    body.innerHTML = html;
  }

  function renderRisk(risk) {
    var html = '';
    Object.keys(RISK_LABELS).forEach(function (key) {
      html += '<label>' + esc(RISK_LABELS[key]) +
        '<input type="number" step="any" data-risk="' + esc(key) + '" value="' +
        esc(risk[key] === undefined ? '' : risk[key]) + '"></label>';
    });
    el('risk-row').innerHTML = html;
  }

  function renderForm(rules) {
    el('f-name').value = rules.name || '';
    el('f-version').value = rules.version || '';
    el('f-mode').value = rules.mode || 'semi_auto';
    el('f-cooldown').value = rules.cooldown_minutes || 0;
    el('f-windows').value = formatWindows(rules.time_windows);
    var u = rules.universe || {};
    el('f-ex-st').checked = !!u.exclude_st;
    el('f-ex-susp').checked = !!u.exclude_suspended;
    el('f-ex-up').checked = !!u.exclude_limit_up;
    el('f-ex-down').checked = !!u.exclude_limit_down;
    el('f-boards').value = (u.boards || []).join(', ');
    el('f-days').value = u.min_list_days === undefined ? '' : u.min_list_days;
    el('f-minamount').value = u.min_amount === undefined ? '' : u.min_amount;
    el('f-buy-logic').value = (rules.buy || {}).logic || 'AND';
    el('f-sell-logic').value = (rules.sell || {}).logic || 'OR';
    renderRisk(rules.risk || {});
    Object.keys(CONTAINERS).forEach(function (containerId) {
      el(containerId).innerHTML = '';
    });
    renderConditions(rules);
  }

  function renderConditions(rules) {
    var map = {
      'buy-cond': (rules.buy || {}).conditions || [],
      'buy-precision': (rules.buy || {}).precision_conditions || [],
      'sell-cond': (rules.sell || {}).conditions || []
    };
    Object.keys(map).forEach(function (containerId) {
      map[containerId].forEach(function (condition) { renderRow(containerId, condition); });
    });
  }

  function readForm() {
    var rules = JSON.parse(JSON.stringify(DATA.rules));
    delete rules._source_path;
    rules.name = el('f-name').value;
    rules.version = el('f-version').value;
    rules.mode = el('f-mode').value;
    rules.cooldown_minutes = Number(el('f-cooldown').value) || 0;
    rules.time_windows = parseWindows(el('f-windows').value);
    rules.universe = rules.universe || {};
    rules.universe.exclude_st = el('f-ex-st').checked;
    rules.universe.exclude_suspended = el('f-ex-susp').checked;
    rules.universe.exclude_limit_up = el('f-ex-up').checked;
    rules.universe.exclude_limit_down = el('f-ex-down').checked;
    rules.universe.boards = el('f-boards').value.split(/[,，]/).map(function (s) { return s.trim(); })
      .filter(function (s) { return s; });
    rules.universe.min_list_days = Number(el('f-days').value) || 0;
    rules.universe.min_amount = Number(el('f-minamount').value) || 0;
    rules.risk = rules.risk || {};
    var inputs = el('risk-row').querySelectorAll('[data-risk]');
    for (var i = 0; i < inputs.length; i++) {
      rules.risk[inputs[i].getAttribute('data-risk')] = Number(inputs[i].value) || 0;
    }
    rules.buy = rules.buy || {};
    rules.buy.logic = el('f-buy-logic').value;
    rules.buy.conditions = readConditions('buy-cond');
    rules.buy.precision_conditions = readConditions('buy-precision');
    rules.buy.precision_logic = rules.buy.precision_logic || 'AND';
    rules.sell = rules.sell || {};
    rules.sell.logic = el('f-sell-logic').value;
    rules.sell.conditions = readConditions('sell-cond');
    rules.sell.precision_conditions = rules.sell.precision_conditions || [];
    rules.sell.precision_logic = rules.sell.precision_logic || 'AND';
    return rules;
  }

  function syncRaw() {
    try { el('raw').value = JSON.stringify(readForm(), null, 2); }
    catch (e) { msg('表单解析失败：' + e.message, true); }
  }

  function render(data) {
    DATA = data;
    el('digest').textContent = '规则指纹 ' + String(data.digest || '').slice(0, 16) + ' · ' + (data.path || '');
    renderCapabilities(data.capabilities || []);
    renderForm(data.rules || {});
    syncRaw();
  }

  function load() {
    fetch('api/rules', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) { throw new Error('HTTP ' + r.status); } return r.json(); })
      .then(function (data) { render(data); msg('已载入本地规则，可开始编辑'); })
      .catch(function (e) {
        msg('无法读取本地服务：请先运行 python research/cli.py serve 后访问 http://127.0.0.1:8765/#config（' + e.message + '）', true);
      });
  }

  function save() {
    var payload;
    try { payload = JSON.parse(el('raw').value); }
    catch (e) { msg('JSON 语法错误：' + e.message, true); return; }
    msg('保存中…');
    fetch('api/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) { throw new Error(res.body.error || '保存失败'); }
        DATA.rules = payload;
        el('digest').textContent = '规则指纹 ' + String(res.body.digest || '').slice(0, 16) + ' · ' + (res.body.path || '');
        msg('已保存到本地规则文件。请执行 python research/cli.py export 并人工导入 QMT。');
      })
      .catch(function (e) { msg(e.message, true); });
  }

  document.addEventListener('input', function (ev) {
    if (ev.target && ev.target.id !== 'raw' && DATA) { syncRaw(); }
  });
  document.addEventListener('change', function (ev) {
    if (ev.target && ev.target.id !== 'raw' && DATA) { syncRaw(); }
  });
  document.addEventListener('click', function (ev) {
    var add = ev.target.getAttribute && ev.target.getAttribute('data-add');
    if (add) {
      renderRow(add, { field: '', op: '>=', value: 0 });
      syncRaw();
      return;
    }
    if (ev.target.getAttribute && ev.target.getAttribute('data-remove')) {
      var row = ev.target.parentNode;
      if (row && row.parentNode) { row.parentNode.removeChild(row); syncRaw(); }
    }
  });
  el('save').addEventListener('click', save);
  el('reload').addEventListener('click', function () { load(); });
  el('from-json').addEventListener('click', function () {
    try {
      var rules = JSON.parse(el('raw').value);
      DATA.rules = rules;
      renderForm(rules);
      syncRaw();
      msg('已把 JSON 载入到表单');
    } catch (e) { msg('JSON 语法错误：' + e.message, true); }
  });
  load();
})();
