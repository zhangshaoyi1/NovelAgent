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
  const es = new EventSource('/api/runs/' + runId + '/events');
  es.addEventListener('log', (e) => appendLog(consoleObj.logEl, JSON.parse(e.data).text));
  es.addEventListener('progress', (e) => appendProgress(consoleObj.timelineEl, JSON.parse(e.data)));
  es.addEventListener('done', (e) => {
    const d = JSON.parse(e.data);
    es.close();
    const ok = d.exit_code === 0;
    consoleObj.statusEl.innerHTML = ok
      ? '<span class="badge ok">完成 ✓</span>'
      : `<span class="badge err">失败（退出码 ${d.exit_code}）</span>`;
    if (d.state) consoleObj.statusEl.insertAdjacentHTML('beforeend',
      ` <span class="badge">新状态：${d.state}</span>`);
    if (typeof onDone === 'function') onDone(d);
  });
  es.onerror = () => { es.close(); };
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
