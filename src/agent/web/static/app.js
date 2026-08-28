/* NovelAgent Web UI 前端逻辑：SSE 客户端 + 通用命令运行器。 */

function sanitize(name) {
  return (name || '').replace(/[^a-zA-Z0-9_-]/g, '') || 'my-novel';
}

/* 打开一个运行控制台浮层，返回内部元素句柄 */
function startRunConsole(title) {
  let overlay = document.getElementById('run-console');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'run-console';
    overlay.className = 'run-console';
    overlay.innerHTML = `
      <div class="rc-box">
        <div class="rc-head"><span class="rc-title"></span>
          <button class="rc-close" onclick="closeRunConsole()">×</button></div>
        <div class="rc-log"></div>
        <div class="rc-timeline"></div>
        <div class="rc-status"></div>
      </div>`;
    document.body.appendChild(overlay);
  }
  overlay.style.display = 'flex';
  overlay.querySelector('.rc-title').textContent = title || '运行';
  const logEl = overlay.querySelector('.rc-log');
  const tlEl = overlay.querySelector('.rc-timeline');
  const stEl = overlay.querySelector('.rc-status');
  logEl.innerHTML = ''; tlEl.innerHTML = ''; stEl.innerHTML = '';
  return { logEl, timelineEl: tlEl, statusEl: stEl, overlay };
}

function closeRunConsole() {
  const o = document.getElementById('run-console');
  if (o) o.style.display = 'none';
}

function appendLog(el, text) {
  if (!text) return;
  const line = document.createElement('div');
  line.className = 'log-line';
  line.textContent = text;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function appendProgress(el, ev) {
  const d = ev || {};
  const item = document.createElement('div');
  item.className = 'tl-item';
  const phase = d.phase ? `[${d.phase}] ` : '';
  const cost = d.cost && d.cost.tokens_used != null
    ? ` · tokens ${d.cost.tokens_used}` : '';
  item.textContent = `${phase}${d.message || ''}${cost}`;
  el.appendChild(item);
  el.scrollTop = el.scrollHeight;
}

/* 运行命令：argv 为数组（推荐）或字符串（兼容） */
async function runCommand(project, command, argv, consoleObj, onDone) {
  const fd = new FormData();
  fd.append('project', project);
  fd.append('command', command);
  if (Array.isArray(argv)) fd.append('argv_json', JSON.stringify(argv));
  else fd.append('args', argv || '');
  let runId;
  try {
    const resp = await fetch('/api/run', { method: 'POST', body: fd });
    const j = await resp.json();
    runId = j.run_id;
  } catch (e) {
    appendLog(consoleObj.logEl, '✗ 请求失败：' + e);
    return;
  }
  streamEvents(runId, consoleObj, onDone);
}

function streamEvents(runId, consoleObj, onDone) {
  let finished = false;
  let es = null;

  /* 幂等收尾：SSE 与轮询兜底都可能触发，只执行一次 */
  const finish = (d) => {
    if (finished) return;
    finished = true;
    if (es) es.close();
    const ok = d.exit_code === 0;
    consoleObj.statusEl.innerHTML = ok
      ? '<span class="badge ok">完成 ✓</span>'
      : `<span class="badge err">失败（退出码 ${d.exit_code}）</span>`;
    if (d.state) consoleObj.statusEl.insertAdjacentHTML('beforeend',
      ` <span class="badge">新状态：${d.state}</span>`);
    if (typeof onDone === 'function') onDone(d);
  };

  es = new EventSource('/api/runs/' + runId + '/events');
  es.addEventListener('log', (e) => appendLog(consoleObj.logEl, JSON.parse(e.data).text));
  es.addEventListener('progress', (e) => appendProgress(consoleObj.timelineEl, JSON.parse(e.data)));
  es.addEventListener('done', (e) => finish(JSON.parse(e.data)));

  // 实时通道失败兜底：EventSource 默认自动重连，连续多次仍失败才转轮询
  let errors = 0;
  es.onerror = () => {
    if (finished) return;
    errors++;
    if (errors >= 3) {
      es.close();
      appendLog(consoleObj.logEl, '实时通道中断，转为轮询结果…');
      pollRunStatus(runId, consoleObj, finish);
    }
  };

  // 看门狗：无论如何长时间没收到 done 就启动轮询兜底，
  // 即使 SSE 丢失了 done 事件也能取回结果，避免界面「假死」。
  setTimeout(() => {
    if (!finished) pollRunStatus(runId, consoleObj, finish);
  }, 25000);
}

/* 轮询兜底：SSE 失效 / 丢事件时直接从后端取回 run 结束状态 */
const _runPollers = new Set();
function pollRunStatus(runId, consoleObj, finish) {
  if (_runPollers.has(runId)) return;
  _runPollers.add(runId);

  async function tick() {
    let j = null;
    try {
      const resp = await fetch('/api/runs/' + runId);
      if (resp.status === 404) { _runPollers.delete(runId); return; }
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      j = await resp.json();
    } catch (e) {
      j = null; // 网络抖动，稍后重试
    }
    if (j && j.done) {
      _runPollers.delete(runId);
      finish({ exit_code: j.exit_code == null ? 0 : j.exit_code, state: j.state });
      return;
    }
    setTimeout(tick, 2000);
  }
  tick();
}

/* 新建项目（POST /api/projects）并跟踪 start 进度 */
async function createProject(params, onDone) {
  const fd = new URLSearchParams();
  for (const [k, v] of params.entries()) fd.append(k, v);
  let runId;
  try {
    const resp = await fetch('/api/projects', { method: 'POST', body: fd });
    const j = await resp.json();
    runId = j.run_id;
  } catch (e) {
    alert('创建失败：' + e);
    return;
  }
  const c = startRunConsole('创建项目（start）');
  streamEvents(runId, c, onDone);
}

/* 向导步骤：根据当前状态渲染不同的可用动作 */
function guideStep(project,  command, argv, label) {
  const c = startRunConsole(label || command);
  runCommand(project, command, argv, c, () => location.reload());
}

/* 向导：一键自动写书（compose 命令） */
function guideCompose(project) {
  const name = document.getElementById('compose-name').value.trim();
  const dir = document.getElementById('compose-dir').value.trim();
  const core = document.getElementById('compose-core').value.trim();
  const scope = document.getElementById('compose-scope').value;
  const genre = document.getElementById('compose-genre').value.trim();
  const chapters = document.getElementById('compose-chapters').value.trim();
  const mode = document.getElementById('compose-mode').value;
  if (!name && !dir) {
    alert('请至少填写「书名」或「已有项目目录」之一');
    return;
  }
  if (!name && !core) {
    alert('续写模式需填写「已有项目目录」；新书模式需填写「书名」与「一句话核心梗」');
    return;
  }
  const argv = [];
  if (name) argv.push('--name', name);
  if (dir) argv.push('--dir', dir);
  if (core) argv.push('--story-core', core);
  argv.push('--scope', scope);
  if (genre) argv.push('--genre', genre);
  if (chapters && chapters !== '0') argv.push('--chapters', chapters);
  argv.push('--mode', mode);
  const c = startRunConsole('一键写书（compose）');
  runCommand(project, 'compose', argv, c, () => location.reload());
}

/* ============================================================
 * 双模式连续滑块：Agent 自主度（0-100）
 * ============================================================ */

/* 前端标签（与后端 autonomy_label 对齐，避免后端未返回时空白） */
function autonomyLabel(level) {
  if (level >= 90) return 'Auto Driver';
  if (level >= 30) return 'Co-pilot';
  return 'Director';
}

/* 设置自主度：POST /api/mode，并即时刷新滑块与标签 */
function setAutonomy(project, level) {
  const lvl = Math.max(0, Math.min(100, parseInt(level, 10) || 0));
  const fd = new FormData();
  fd.append('name', project);
  fd.append('autonomy', lvl);
  fetch('/api/mode', { method: 'POST', body: fd })
    .then((r) => r.json())
    .then((j) => {
      const applied = j.autonomy != null ? j.autonomy : lvl;
      const slider = document.getElementById('autonomy-slider');
      const valEl = document.getElementById('autonomy-value');
      const lblEl = document.getElementById('autonomy-label');
      if (slider) slider.value = applied;
      if (valEl) valEl.textContent = applied;
      if (lblEl) lblEl.textContent = j.label || autonomyLabel(applied);
      if (j.message) flashAutonomyNote(j.message);
    })
    .catch((e) => alert('设置自主度失败：' + e));
}

/* 滑块下方提示行（短暂显示后端回执） */
function flashAutonomyNote(msg) {
  let el = document.getElementById('autonomy-note');
  if (!el) {
    el = document.createElement('div');
    el.id = 'autonomy-note';
    el.className = 'muted small autonomy-note';
    const card = document.querySelector('.slider-row');
    if (card && card.parentElement) card.parentElement.appendChild(el);
  }
  el.textContent = msg;
}

/* 从当前页推断项目名（优先 data-project，回退到 URL /p/{name}） */
function currentProjectName() {
  if (document.body.dataset && document.body.dataset.project) {
    return document.body.dataset.project;
  }
  const m = location.pathname.match(/\/p\/([^/]+)/);
  return m ? m[1] : '';
}

/* 滑块实时联动：拖动即时更新数值与标签，松手（change）后落库 */
document.addEventListener('DOMContentLoaded', function () {
  const slider = document.getElementById('autonomy-slider');
  if (!slider) return;
  slider.addEventListener('input', function () {
    const v = parseInt(slider.value, 10);
    slider.style.setProperty('--fill', v + '%');
    const valEl = document.getElementById('autonomy-value');
    const lblEl = document.getElementById('autonomy-label');
    if (valEl) valEl.textContent = v;
    if (lblEl) lblEl.textContent = autonomyLabel(v);
  });
  // 初始填充
  slider.style.setProperty('--fill', slider.value + '%');
  let t;
  slider.addEventListener('change', function () {
    const v = parseInt(slider.value, 10);
    const project = currentProjectName();
    if (!project) return;
    clearTimeout(t);
    t = setTimeout(function () {
      setAutonomy(project, v);
    }, 300);
  });
});
