/* Claw 左侧功能导航栏：功能梳理、视图切换、白名单命令执行、文档阅读、状态与日志。
   无外部依赖；离线打开时使用内置降级清单，动作与文档会提示需启动本地服务。 */
(function () {
  'use strict';

  // 离线降级清单：[id, 分组, 名称, usage, target, 层级, 说明]
  var FALLBACK = [
    ['overview', '工作台', '功能总览', 'view', 'overview', '本地', '全部功能按分组梳理。'],
    ['config', '工作台', '策略配置台', 'view', 'config', '本地', '编辑指标、阈值、标的池与风控。'],
    ['review', '工作台', '模拟盘复盘', 'view', 'review', '本地', '净值、绩效、成交流水与拦截。'],
    ['scan-view', '工作台', '最新扫描结果', 'view', 'scan', '本地', '最近一根 bar 的扫描明细。'],
    ['capabilities', '工作台', '能力归属', 'view', 'cap', '两层', '四项能力分别运行在哪一层。'],
    ['demo', '研究与演练', '生成演示数据', 'action', 'demo', '本地', '重新生成确定性合成行情。'],
    ['backtest', '研究与演练', '运行回测', 'action', 'backtest', '本地', '按当前规则跑模拟盘。'],
    ['scan', '研究与演练', '扫描最新 bar', 'action', 'scan', '本地', '做一次全市场两级筛选。'],
    ['simulate', '研究与演练', '无券商全链路演练', 'action', 'qmt-sim', '本地', '假账户跑完整自动买卖链路。'],
    ['export', '研究与演练', '导出沙箱规则', 'action', 'export', '本地', '生成 QMT 可粘贴的规则字面量。'],
    ['tests', '研究与演练', '运行单元测试', 'action', 'tests', '本地', '执行完整回归测试。'],
    ['qmt', '执行层', 'QMT 执行层状态', 'view', 'qmt', 'QMT', '实时链路、六道闸门与导入步骤。'],
    ['safety', '执行层', '安全状态', 'view', 'safety', '两层', '只读、绑定、凭据与哈希一致性。'],
    ['indicators', '文档与合规', '指标字典与规则 DSL', 'doc', 'INDICATORS', '文档', '指标字段与规则写法。'],
    ['checklist', '文档与合规', '实盘上线检查表', 'doc', 'GO_LIVE_CHECKLIST', '文档', '阶段 0~5 验收门槛。'],
    ['compliance', '文档与合规', '合规边界', 'doc', 'COMPLIANCE', '文档', '为什么必须闭环在券商沙箱。'],
    ['qmt-readme', '文档与合规', 'QMT 接入说明', 'doc', 'QMT_README', '文档', 'API 适配与启用步骤。'],
    ['runbook', '文档与合规', '运维手册', 'doc', 'RUNBOOK', '文档', '盘前 / 盘中 / 盘后流程。'],
    ['emergency', '文档与合规', '应急手册', 'doc', 'EMERGENCY', '文档', 'Kill Switch 与熔断处置。'],
    ['handoff', '文档与合规', '项目交接卡', 'doc', 'PROJECT_CONTEXT', '文档', '压缩上下文。'],
    ['readme', '文档与合规', 'README', 'doc', 'README', '文档', '项目总览与快速开始。'],
    ['actions-result', '系统', '运行结果', 'view', 'actions', '本地', '最近一次命令输出。'],
    ['action-log', '系统', '操作日志', 'view', 'log', '本地', '本次进程内的功能执行记录。']
  ];
  var GROUPS = ['工作台', '研究与演练', '执行层', '文档与合规', '系统'];
  var USAGE_LABEL = { view: '视图', action: '命令', doc: '文档' };

  var features = [];
  var byId = {};
  var current = { action: null, doc: null, id: 'overview' };
  var suppressHash = false;

  function el(id) { return document.getElementById(id); }
  function esc(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function toFeature(row) {
    return { id: row[0], group: row[1], label: row[2], usage: row[3],
             target: row[4], layer: row[5], desc: row[6] };
  }
  function fallbackFeatures() {
    return FALLBACK.map(toFeature);
  }
  function get(url, options) {
    return fetch(url, Object.assign({ cache: 'no-store' }, options || {}))
      .then(function (r) {
        if (!r.ok) { throw new Error('HTTP ' + r.status); }
        return r.json();
      });
  }
  function showMessage(text) {
    var box = el('action-output');
    if (box) { box.textContent = text; }
  }

  /* ---------------- 导航渲染 ---------------- */
  function buildNav() {
    var html = '';
    for (var g = 0; g < GROUPS.length; g++) {
      var group = GROUPS[g], items = [];
      for (var i = 0; i < features.length; i++) {
        if (features[i].group === group) { items.push(features[i]); }
      }
      if (!items.length) { continue; }
      html += '<div class="nav-group" data-group="' + esc(group) + '">' +
        '<button class="nav-title" type="button" data-toggle="1">' +
        '<span>' + esc(group) + '</span><span class="chev">▾</span></button><div class="nav-items">';
      for (var j = 0; j < items.length; j++) {
        var f = items[j];
        html += '<button class="nav-item" type="button" data-nav="' + esc(f.id) + '"' +
          ' data-usage="' + esc(f.usage) + '"><span class="dot"></span>' +
          '<span>' + esc(f.label) + '</span><span class="layer">' + esc(f.layer) + '</span></button>';
      }
      html += '</div></div>';
    }
    el('nav').innerHTML = html;
  }

  function renderOverview() {
    var host = el('feature-groups'), html = '';
    for (var g = 0; g < GROUPS.length; g++) {
      var group = GROUPS[g], items = [];
      for (var i = 0; i < features.length; i++) {
        if (features[i].group === group) { items.push(features[i]); }
      }
      if (!items.length) { continue; }
      html += '<div class="feature-group"><h2>' + esc(group) + '</h2><div class="feature-grid">';
      for (var j = 0; j < items.length; j++) {
        var f = items[j];
        html += '<div class="feature-card">' +
          '<div class="fc-head"><span class="fc-title">' + esc(f.label) + '</span>' +
          '<span class="tag">' + esc(USAGE_LABEL[f.usage] || f.usage) + '</span></div>' +
          '<div class="fc-desc">' + esc(f.desc) + '</div>' +
          '<div class="fc-foot"><span class="fc-kind">' + esc(f.layer) + '层</span>' +
          '<button class="btn" type="button" data-open="' + esc(f.id) + '">打开 →</button></div></div>';
      }
      html += '</div></div>';
    }
    host.innerHTML = html;
  }

  function setActive(id) {
    var items = document.querySelectorAll('.nav-item');
    for (var i = 0; i < items.length; i++) {
      var on = items[i].getAttribute('data-nav') === id;
      items[i].className = on ? 'nav-item active' : 'nav-item';
      if (on) {
        var group = items[i].parentNode.parentNode;
        if (group.className.indexOf('collapsed') >= 0) {
          group.className = 'nav-group';
        }
      }
    }
  }

  function showView(name) {
    var views = document.querySelectorAll('.view');
    for (var i = 0; i < views.length; i++) {
      views[i].className = (views[i].id === 'view-' + name) ? 'view active' : 'view';
    }
  }

  /* ---------------- 功能执行 ---------------- */
  function runAction(name) {
    current.action = name;
    showView('actions');
    el('page-title').textContent = '运行结果';
    el('action-meta').textContent = '正在执行…';
    el('action-output').textContent = '正在本地执行：' + name + ' …';
    el('btn-rerun').disabled = true;
    return fetch('api/actions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name })
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        var d = res.body || {};
        if (!res.ok) { throw new Error(d.error || ('HTTP 失败')); }
        el('action-meta').textContent = d.name + ' · 退出码 ' + d.exit_code +
          ' · ' + d.duration_ms + ' ms · ' + d.ts;
        el('action-output').textContent = '$ ' + d.command + '\n\n' + d.output;
      })
      .catch(function (e) {
        el('action-meta').textContent = '未执行';
        el('action-output').textContent = '无法执行「' + name + '」：' + e.message +
          '\n\n请先启动本地服务：python research/cli.py serve --port 8765\n' +
          '然后从 http://127.0.0.1:8765/ 打开本工作台（直接双击 HTML 时动作与文档不可用）。';
      })
      .then(function () { el('btn-rerun').disabled = false; });
  }

  function loadDoc(name) {
    current.doc = name;
    showView('doc');
    el('page-title').textContent = '文档阅读';
    el('doc-title').textContent = name;
    el('doc-path').textContent = '载入中…';
    el('doc-content').textContent = '正在读取…';
    return get('api/docs?name=' + encodeURIComponent(name))
      .then(function (d) {
        el('doc-title').textContent = d.name;
        el('doc-path').textContent = d.path;
        el('doc-content').textContent = d.content;
      })
      .catch(function (e) {
        el('doc-path').textContent = '';
        el('doc-content').textContent = '无法读取文档：' + e.message +
          '\n\n请启动本地服务后访问 http://127.0.0.1:8765/ 。';
      });
  }

  function fillKv(id, rows) {
    var body = el(id).getElementsByTagName('tbody')[0], html = '';
    for (var i = 0; i < rows.length; i++) {
      var cls = '';
      if (rows[i][1] === true) { cls = ' class="state-ok"'; }
      if (rows[i][1] === false) { cls = ' class="state-bad"'; }
      var shown = rows[i][1] === true ? '是' : (rows[i][1] === false ? '否' : rows[i][1]);
      html += '<tr><td>' + esc(rows[i][0]) + '</td><td' + cls + '>' + esc(shown) + '</td></tr>';
    }
    body.innerHTML = html;
  }

  function loadStatus() {
    return get('api/status').then(function (s) {
      var g = s.gates || {};
      fillKv('gate-table', [
        ['券商', s.broker || '未知'],
        ['KILL_SWITCH', g.KILL_SWITCH],
        ['DRY_RUN', g.DRY_RUN],
        ['MODE', g.MODE],
        ['REPORT_CONFIRMED', g.REPORT_CONFIRMED],
        ['LIVE_ACKNOWLEDGED', g.LIVE_ACKNOWLEDGED],
        ['扫描周期', g.SCAN_PERIOD || '—'],
        ['资金账号已写入代码', s.account_id_in_code],
        ['允许真实下单', s.live_order_allowed]
      ]);
      fillKv('status-table', [
        ['服务绑定', s.bind],
        ['只读研究层', s.read_only],
        ['策略内核副本一致', s.kernel_identical],
        ['规则快照哈希匹配', s.snapshot_hash_matches],
        ['规则指纹', String(s.rules_digest || '').slice(0, 16)],
        ['演示数据已生成', s.demo_csv_exists],
        ['Python', s.python],
        ['动作白名单', (s.action_whitelist || []).join(', ')],
        ['实时桥接', s.no_realtime_bridge ? '无（禁止）' : '存在（异常）'],
        ['更新时间', s.ts]
      ]);
    }).catch(function (e) {
      fillKv('gate-table', [['状态', '需启动本地服务：' + e.message]]);
      fillKv('status-table', [['状态', '需启动本地服务：' + e.message]]);
    });
  }

  function loadLog() {
    return get('api/action-log').then(function (d) {
      var body = el('log-list').getElementsByTagName('tbody')[0], html = '';
      var items = d.items || [];
      if (!items.length) { html = '<tr><td colspan="6" class="empty">本次服务尚未执行过功能</td></tr>'; }
      for (var i = 0; i < items.length; i++) {
        var r = items[i];
        html += '<tr><td>' + esc(r.ts) + '</td><td>' + esc(r.name) + '</td>' +
          '<td class="' + (r.ok ? 'sell' : 'buy') + '">' + (r.ok ? '成功' : '失败') + '</td>' +
          '<td>' + esc(r.exit_code) + '</td><td>' + esc(r.duration_ms) + ' ms</td>' +
          '<td>' + esc(String(r.preview || '').replace(/\s+/g, ' ').slice(0, 70)) + '</td></tr>';
      }
      body.innerHTML = html;
    }).catch(function () {
      el('log-list').getElementsByTagName('tbody')[0].innerHTML =
        '<tr><td colspan="6" class="empty">需启动本地服务</td></tr>';
    });
  }

  /* ---------------- 路由 ---------------- */
  function activate(id, fromHash) {
    var f = byId[id];
    if (!f) { f = byId['overview']; }
    current.id = f.id;
    if (!fromHash && !suppressHash) {
      suppressHash = true;
      try { window.location.hash = f.id; } catch (e) { /* file:// 下忽略 */ }
      suppressHash = false;
    }
    setActive(f.id);
    el('page-title').textContent = f.label;
    if (f.usage === 'action') { return runAction(f.target); }
    if (f.usage === 'doc') { return loadDoc(f.target); }
    showView(f.target);
    if (f.target === 'safety' || f.target === 'qmt') { loadStatus(); }
    if (f.target === 'log') { loadLog(); }
    if (window.scrollTo) { window.scrollTo(0, 0); }
  }

  function currentHash() {
    return String(window.location.hash || '').replace(/^#/, '');
  }

  function init(data, offline) {
    features = data || fallbackFeatures();
    byId = {};
    for (var i = 0; i < features.length; i++) { byId[features[i].id] = features[i]; }
    buildNav();
    renderOverview();
    el('nav-status').textContent = offline
      ? '离线模式：动作 / 文档需启动本地服务'
      : '功能 ' + features.length + ' 项 · 服务在线';
    activate(currentHash() || 'overview', true);
  }

  document.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t || !t.getAttribute) { return; }
    var nav = t.getAttribute('data-nav');
    var open = t.getAttribute('data-open');
    if (nav || open) { activate(nav || open, false); return; }
    if (t.getAttribute('data-toggle')) {
      var group = t.parentNode;
      group.className = group.className.indexOf('collapsed') >= 0
        ? 'nav-group' : 'nav-group collapsed';
      return;
    }
    if (t.id === 'btn-rerun' && current.action) { runAction(current.action); return; }
    if (t.id === 'btn-refresh-status') { loadStatus(); return; }
    if (t.id === 'btn-refresh-log') { loadLog(); return; }
  });

  window.addEventListener('hashchange', function () {
    if (!suppressHash) { activate(currentHash(), true); }
  });

  get('api/features')
    .then(function (d) { init(d.features, false); })
    .catch(function () { init(null, true); });
})();
