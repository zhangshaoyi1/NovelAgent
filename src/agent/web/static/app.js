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
          <span class="rc-timer"></span>
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
  const tmEl = overlay.querySelector('.rc-timer');
  logEl.innerHTML = ''; tlEl.innerHTML = ''; stEl.innerHTML = ''; tmEl.innerHTML = '';
  // logCursor：SSE 与轮询两条通道共享的日志消费游标，避免同一条日志被渲染两次
  return { logEl, timelineEl: tlEl, statusEl: stEl, timerEl: tmEl, overlay, logCursor: 0 };
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

/* 运行命令：argv 为数组（推荐）或字符串（兼容）。
   profile：可选模型档案 id（写作间等场景按次指定模型，经 NOVEL_MODEL_PROFILE 注入子进程）。 */
async function runCommand(project, command, argv, consoleObj, onDone, profile) {
  const fd = new FormData();
  fd.append('project', project);
  fd.append('command', command);
  if (Array.isArray(argv)) fd.append('argv_json', JSON.stringify(argv));
  else fd.append('args', argv || '');
  if (profile) fd.append('profile', profile);
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
    stopTimer();
    const ok = d.exit_code === 0;
    const code = d.exit_code;
    let badge = ok
      ? '<span class="badge ok">完成 ✓</span>'
      : `<span class="badge err">失败（退出码 ${code}）</span>`;
    // 门禁拒绝（退出码 2）常是「该步骤已完成/当前阶段不可执行」，给个友好说明
    if (code === 2) {
      badge = '<span class="badge err">拦截：当前阶段不可执行该操作（可能早已完成）</span>';
    }
    consoleObj.statusEl.innerHTML = badge;
    if (d.state) consoleObj.statusEl.insertAdjacentHTML('beforeend',
      ` <span class="badge">新状态：${d.state}</span>`);
    if (typeof onDone === 'function') onDone(d);
  };

  /* 耗时计时器：长时间运行的命令（如 M1 生成世界观 1-2 分钟）若无任何反馈，
     界面容易被视为「卡死」。这里持续显示已耗时，让等待可预期。 */
  const t0 = Date.now();
  let timerId = null;
  function stopTimer() {
    if (timerId !== null) { clearInterval(timerId); timerId = null; }
  }
  if (consoleObj.timerEl) {
    const fmt = (s) => {
      const m = Math.floor(s / 60);
      return m > 0 ? `${m} 分 ${s % 60} 秒` : `${s} 秒`;
    };
    const tickTimer = () => {
      const s = Math.floor((Date.now() - t0) / 1000);
      consoleObj.timerEl.textContent = `已耗时 ${fmt(s)}`;
    };
    tickTimer();
    timerId = setInterval(tickTimer, 1000);
  }

  es = new EventSource('/api/runs/' + runId + '/events');
  es.addEventListener('log', (e) => {
    appendLog(consoleObj.logEl, JSON.parse(e.data).text);
    // SSE 推来的日志同样会进后端 logs 数组，游标同步递增，轮询才不会重复渲染
    consoleObj.logCursor = (consoleObj.logCursor || 0) + 1;
  });
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
    // 日志补齐：只渲染游标之后的新增行。SSE 正常时游标已同步，这里渲染 0 行；
    // SSE 丢事件时则补上缺口，避免旧实现「整份 logs 重复刷一遍」。
    if (j && Array.isArray(j.logs)) {
      const cursor = consoleObj.logCursor || 0;
      for (const txt of j.logs.slice(cursor)) appendLog(consoleObj.logEl, txt);
      consoleObj.logCursor = j.logs.length;
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
  // 预期管理：M1 需调用 LLM 生成世界观，实测 1-2 分钟，先给出提示避免误判卡死
  appendLog(c.logEl, '已提交。正在生成世界观（需调用 LLM，通常 1-2 分钟），请稍候…');
  streamEvents(runId, c, onDone);
}

/* 向导步骤：根据当前状态渲染不同的可用动作 */
function guideStep(project,  command, argv, label) {
  const c = startRunConsole(label || command);
  runCommand(project, command, argv, c, () => location.reload());
}

/* 阶段生成：跑对应命令让 Agent 生成真实内容，完成后刷新回显 */
function genStage(project, command, argv, label) {
  const c = startRunConsole('生成：' + (label || command));
  runCommand(project, command.replace(/^\//, ''), argv, c, () => location.reload());
}

/* 事件委托：阶段生成按钮通过 data-* 属性传参（替代内联 onclick 拼 JSON，
   修复 gen_argv 含双引号时 HTML 属性被截断、按钮点击无反应的 bug） */
document.addEventListener('click', function (e) {
  const btn = e.target.closest('button[data-gen-stage]');
  if (!btn) return;
  let argv = [];
  try { argv = JSON.parse(btn.getAttribute('data-gen-argv') || '[]'); } catch (err) { argv = []; }
  genStage(
    btn.getAttribute('data-gen-project') || '',
    btn.getAttribute('data-gen-cmd') || '',
    argv,
    btn.getAttribute('data-gen-label') || ''
  );
});

/* 脉络讨论：读取讨论输入框的内容，作为预设讨论发给 /discuss --message，完成后刷新回显讨论纪要 */
function sendDiscussion(project) {
  const el = document.getElementById('discuss-message');
  const msg = (el && el.value.trim()) || '';
  if (!msg) { alert('请先在上方输入你想讨论的内容'); return; }
  const c = startRunConsole('讨论（脉络）');
  runCommand(project, 'discuss', ['--message', msg], c, () => location.reload());
}

/* 反馈修改：把用户意见作为 feedback 传给对应命令，由 LLM 按其意见迭代修改并回显 */
function reviseStage(project, stage, taId) {
  const el = document.getElementById(taId);
  const feedback = (el && el.value.trim()) || '';
  if (!feedback) { alert('请先输入你想怎么改'); return; }
  let command, argv, label;
  if (stage === 'architecture') {
    command = 'architecture'; argv = ['--feedback', feedback]; label = '迭代故事架构';
  } else if (stage === 'outline') {
    command = 'outline'; argv = ['--feedback', feedback]; label = '迭代创作大纲';
  } else if (stage === 'characters') {
    command = 'design_characters'; argv = ['--feedback', feedback]; label = '迭代角色设计';
  } else {
    alert('暂不支持该阶段的反馈修改'); return;
  }
  const c = startRunConsole(label);
  runCommand(project, command, argv, c, () => location.reload());
}

/* 富文本切换：编辑（textarea）<-> 预览（MD 渲染）。保存到本地后页面刷新，预览自动同步。 */
function toggleStageEdit(key, btn) {
  const ta = document.getElementById(key);
  const pv = document.getElementById('prev-' + key);
  if (!ta || !pv) return;
  const editing = !ta.hidden;
  ta.hidden = editing;       // 编辑态 → 切回预览
  pv.hidden = !editing;      // 预览态 → 切到编辑
  if (btn) btn.textContent = editing ? '✏️ 编辑' : '👁 预览';
}

/* 阶段产物保存到本地：把编辑后的 textarea 内容通过接口写回项目文件 */
async function saveStage(project, rel, taId) {
  const el = document.getElementById(taId);
  if (!el) { alert('未找到编辑框'); return; }
  const fd = new FormData();
  fd.append('rel', rel);
  fd.append('content', el.value);
  try {
    const res = await fetch('/p/' + project + '/save-stage', { method: 'POST', body: fd });
    const data = await res.json();
    if (data && data.ok) { alert('已保存到本地：' + data.message); }
    else { alert('保存失败：' + ((data && data.message) || res.status)); }
  } catch (e) {
    alert('保存失败：' + e);
  }
}

/* 确认某阶段：记录上游基线，消除「待复核」标记，随后刷新标签 */
async function confirmStage(project, stageKey) {
  const fd = new FormData();
  fd.append('stage', stageKey);
  let data;
  try {
    const res = await fetch('/api/stages/' + project + '/confirm', { method: 'POST', body: fd });
    data = await res.json();
  } catch (e) {
    alert('确认失败：' + e);
    return;
  }
  if (data && data.ok) {
    refreshStageTags(project);
  } else {
    alert('确认失败：' + ((data && data.message) || '未知错误'));
  }
}

/* 刷新页面上的阶段标签（已确认 / 待复核）与受影响提示 */
/* ============================================================
 * 调整体量（resize_scope）：改多大体量并重生成大纲
 * ============================================================ */
function toggleResizeBox() {
  const box = document.getElementById('resize-box');
  if (!box) return;
  box.hidden = !box.hidden;
  syncResizeCustom();
}

function syncResizeCustom() {
  const sel = document.getElementById('resize-scope');
  const custom = document.getElementById('resize-custom-fields');
  if (sel && custom) custom.hidden = sel.value !== 'custom';
}

function resizeScope(project) {
  const scope = document.getElementById('resize-scope').value;
  const totalWords = document.getElementById('resize-total-words').value.trim();
  const chapterLength = document.getElementById('resize-chapter-length').value.trim();

  if (scope === 'custom' && !totalWords) {
    alert('自定义体量必须填写目标总字数。');
    return;
  }
  if (chapterLength) {
    const cl = Number(chapterLength);
    if (cl < 1500 || cl > 5000) {
      alert('单章字数需在 1500-5000 字之间（推荐 2000-2500）。');
      return;
    }
  }
  const message = scope === 'custom'
    ? `调整为：${scope}，总字数 ${totalWords} 字，单章 ${chapterLength || '未指定'} 字。确定继续？`
    : `调整为：${scope === 'mega' ? '百万字（100万字以上）' : scope}${chapterLength ? '，单章 ' + chapterLength + ' 字' : ''}。确定继续？`;
  if (!confirm(message)) return;

  const argv = ['--scope', scope];
  if (scope === 'custom') {
    argv.push('--total-words', totalWords);
  }
  if (chapterLength) {
    argv.push('--chapter-length', chapterLength);
  }
  const c = startRunConsole('调整体量（resize_scope）');
  runCommand(project, 'resize-scope', argv, c, () => { document.getElementById('resize-box').hidden = true; location.reload(); });
}

async function refreshStageTags(project) {
  let list = [];
  try {
    const res = await fetch('/api/stages/' + project);
    const j = await res.json();
    list = Array.isArray(j) ? j : [];
  } catch (e) {
    return;
  }
  const byKey = {};
  list.forEach(function (s) { byKey[s.key] = s; });
  document.querySelectorAll('.stage-card').forEach(function (card) {
    // 阶段卡上找确认按钮，其 data-stage 标识阶段
    const btn = card.querySelector('button[data-confirm-stage]');
    if (!btn) return;
    const key = btn.getAttribute('data-confirm-stage');
    const st = byKey[key];
    if (!st) return;
    // 更新 h2 内的标签
    const h2 = card.querySelector('h2');
    if (!h2) return;
    h2.querySelectorAll('.stage-tag').forEach(function (t) { t.remove(); });
    if (st.confirmed) {
      const span = document.createElement('span');
      span.className = 'badge ok stage-tag';
      span.textContent = '已确认';
      h2.appendChild(span);
    }
    if (st.affected) {
      const span = document.createElement('span');
      span.className = 'badge warn stage-tag';
      span.title = st.reason || '';
      span.textContent = '⚠ 待复核';
      h2.appendChild(span);
    }
    // 更新受影响提示行
    let hint = card.querySelector('.stage-affected-hint');
    if (st.affected) {
      if (!hint) {
        hint = document.createElement('p');
        hint.className = 'muted small stage-affected-hint';
        card.insertBefore(hint, card.querySelector('p.muted'));
      }
      hint.textContent = st.reason + '。点「🔍 复核检查单」让 Agent 找出未覆盖/冲突项，处理后重新确认。';
    } else if (hint) {
      hint.remove();
    }
    // 更新「复核检查单」按钮：仅受影响时显示
    const rvBtn = card.querySelector('button[data-review-stage]');
    if (rvBtn) rvBtn.style.display = st.affected ? '' : 'none';
  });
}

/* ============================================================
 * 复核检查单：上游改动后，逐条裁决 LLM 找出的未覆盖/冲突项
 * ============================================================ */
function ensureReviewModal() {
  let overlay = document.getElementById('review-modal');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'review-modal';
    overlay.className = 'review-modal-overlay';
    overlay.innerHTML = `
      <div class="review-modal">
        <div class="rm-head">
          <span class="rm-title">复核检查单</span>
          <button class="rm-close" onclick="closeReviewChecklist()">×</button>
        </div>
        <div class="rm-sub"></div>
        <div class="rm-body"></div>
      </div>`;
    document.body.appendChild(overlay);
  }
  return overlay;
}

function closeReviewChecklist() {
  const o = document.getElementById('review-modal');
  if (o) o.style.display = 'none';
}

async function openReviewChecklist(project, stageKey) {
  const overlay = ensureReviewModal();
  overlay.style.display = 'flex';
  overlay.querySelector('.rm-sub').textContent = '';
  overlay.querySelector('.rm-body').innerHTML =
    '<div class="rm-loading">读取复核检查单…</div>';
  // 先读已保存条目；若自上次复核后上游又改动（needs_review）则重新生成
  let items = [], summary = '', adopted = [], chapters = [], needs = false;
  try {
    const res = await fetch('/api/review/' + project + '/items?stage=' + encodeURIComponent(stageKey));
    const j = await res.json();
    if (j && j.ok) {
      items = j.items || []; summary = j.summary || '';
      adopted = j.adopted || []; chapters = j.affected_chapters || [];
      needs = !!j.needs_review;
    }
  } catch (e) { /* 网络抖动忽略，走重新生成兜底 */ needs = true; }
  if (needs) {
    generateReviewChecklist(overlay, project, stageKey);
  } else {
    renderReviewChecklist(overlay, project, stageKey, '', items, summary, chapters, adopted);
  }
}

async function generateReviewChecklist(overlay, project, stageKey) {
  const body = overlay.querySelector('.rm-body');
  body.innerHTML = '<div class="rm-loading">Agent 正在对比上游改动，找出未覆盖 / 冲突项…</div>';
  let j;
  try {
    const res = await fetch('/api/review/' + project + '?stage=' + encodeURIComponent(stageKey));
    j = await res.json();
  } catch (e) {
    body.innerHTML = '<p class="rm-empty rm-empty-err">生成失败：' + e + '</p>';
    return;
  }
  if (!(j && j.ok)) {
    body.innerHTML = '<p class="rm-empty rm-empty-err">' + ((j && j.message) || '生成失败') + '</p>';
    return;
  }
  renderReviewChecklist(overlay, project, stageKey,
    (j.changed_upstreams || []).join('、'), j.items || [], j.summary || '',
    j.affected_chapters || [], j.adopted || []);
  // 复核已写入数据 → 刷新页面阶段标签（无待处理项时「待复核」自动消除）
  if (typeof refreshStageTags === 'function') refreshStageTags(project);
}

function renderReviewChecklist(overlay, project, stageKey, changedLabel, items, summary, chapters, adopted) {
  overlay.querySelector('.rm-sub').textContent = changedLabel ? '上游改动：' + changedLabel : '';
  adopted = adopted || [];
  const body = overlay.querySelector('.rm-body');
  const kindText = (k) => k === 'conflict' ? '冲突' : '未覆盖';
  const sevText = { high: '高', medium: '中', low: '低' };
  const statusText = { accepted: '已采纳', ignored: '已忽略' };
  let html = '';
  if (chapters && chapters.length) {
    const sample = chapters.slice(0, 8).map(function (c) { return '第' + c.num + '章'; }).join('、');
    const more = chapters.length > 8 ? '…等 ' + chapters.length + ' 章' : '';
    html += `<p class="rm-chapters">⚠ 已写 ${chapters.length} 章，上游改动可能影响已写正文：${sample}${more}。建议抽查相关章节是否需要同步修订。</p>`;
  }
  if (items.length) {
    html += '<p class="rm-total">共 ' + items.length + ' 条，请逐条裁决（采纳 = 据此调整下游内容；忽略 = 确认无需处理）。</p>';
    items.forEach(function (it) {
      const status = it.status || 'pending';
      html += `<div class="rm-item" data-item-id="${it.id}">
        <div class="rm-item-head">
          <span class="rm-kind ${it.kind === 'conflict' ? 'rm-kind-conflict' : 'rm-kind-uncovered'}">${kindText(it.kind)}</span>
          <span class="rm-sev rm-sev-${it.severity}">${sevText[it.severity] || it.severity}</span>
          <strong class="rm-target">${escapeHtml(it.target || '')}</strong>
        </div>
        <p class="rm-issue">${escapeHtml(it.issue || '')}</p>
        ${it.upstream_ref ? `<p class="rm-ref muted small">上游：${escapeHtml(it.upstream_ref)}</p>` : ''}
        ${it.suggestion ? `<p class="rm-sug muted small">建议：${escapeHtml(it.suggestion)}</p>` : ''}
        <div class="rm-actions">
          ${status === 'pending'
            ? `<button class="btn small" onclick="reviewDecision('${project}','${stageKey}','${it.id}','accepted')">✔ 采纳</button>
               <button class="btn small" onclick="reviewDecision('${project}','${stageKey}','${it.id}','ignored')">忽略</button>`
            : `<span class="badge ${status === 'accepted' ? 'ok' : ''}">${statusText[status]}</span>`}
        </div>
      </div>`;
    });
  } else if (items.length === 0 && !adopted.length) {
    html = '<p class="rm-empty">✓ 当前无待复核改动。</p>';
  } else {
    html = '<p class="rm-empty">✓ 未发现需要调整的问题。</p>';
  }
  if (adopted.length) {
    html += '<div class="rm-adopted"><p class="rm-adopted-title">✔ 已采纳的处理记录（带入下次复核）</p>';
    adopted.forEach(function (a) {
      const sug = a.suggestion ? `<span class="rm-adopted-sug">建议：${escapeHtml(a.suggestion)}</span>` : '';
      html += `<div class="rm-adopted-item"><span class="rm-adopted-target">${escapeHtml(a.target || '')}</span>` +
        `<span class="rm-adopted-issue">${escapeHtml(a.issue || '')}</span>${sug}</div>`;
    });
    html += '</div>';
  }
  if (summary) html += `<p class="rm-summary">${escapeHtml(summary)}</p>`;
  html += `<div class="rm-foot"><button class="btn small" onclick="generateReviewChecklist(
    document.getElementById('review-modal'), '${project}', '${stageKey}')">🔄 重新生成</button></div>`;
  body.innerHTML = html;
}

async function reviewDecision(project, stageKey, itemId, action) {
  const fd = new FormData();
  fd.append('stage', stageKey);
  fd.append('item_id', itemId);
  fd.append('action', action);
  let j;
  try {
    const res = await fetch('/api/review/' + project + '/decision', { method: 'POST', body: fd });
    j = await res.json();
  } catch (e) {
    alert('裁决失败：' + e);
    return;
  }
  if (!(j && j.ok)) { alert('裁决失败：' + ((j && j.message) || '未知错误')); return; }
  const item = document.querySelector('.rm-item[data-item-id="' + itemId + '"] .rm-actions');
  if (item) {
    const label = action === 'accepted' ? '已采纳' : '已忽略';
    item.innerHTML = `<span class="badge ${action === 'accepted' ? 'ok' : ''}">${label}</span>`;
  }
  // 裁决改动会影响「待复核」标记（全部裁决完自动消除），刷新页面标签
  if (typeof refreshStageTags === 'function') refreshStageTags(project);
}

/* 转义 HTML，防止 LLM 输出注入 */
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* 向导：一键自动写书（compose 命令） */
let composeMode = 'new'; // 'new' 开新书 / 'resume' 续写已有项目

/* 开新书 / 续写已有项目 切换：只展示当前模式相关字段，减少选择负担 */
function toggleComposeMode(mode) {
  composeMode = mode;
  document.querySelectorAll('.mode-switch-btn').forEach(function (b) {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
  const newEl = document.querySelector('.compose-new');
  const resumeEl = document.querySelector('.compose-resume');
  if (newEl) newEl.hidden = mode !== 'new';
  if (resumeEl) resumeEl.hidden = mode !== 'resume';
}

function guideCompose(project) {
  const mode = composeMode;
  if (mode === 'resume') {
    const dir = document.getElementById('compose-dir').value.trim();
    if (!dir) { alert('请先在「已有项目」下拉框中选择要续写的项目'); return; }
    const argv = ['--dir', dir, '--mode', document.getElementById('compose-mode').value];
    const c = startRunConsole('一键续写（compose）');
    runCommand(project, 'compose', argv, c, () => location.reload());
    return;
  }

  const name = document.getElementById('compose-name').value.trim();
  const core = document.getElementById('compose-core').value.trim();
  const genre = document.getElementById('compose-genre').value;
  const scope = document.getElementById('compose-scope').value;
  const chapters = document.getElementById('compose-chapters').value.trim();
  const cmode = document.getElementById('compose-mode').value;
  if (!name) { alert('请填写「书名」'); return; }
  if (!core) { alert('请填写「一句话核心梗」'); return; }

  const argv = ['--name', name, '--story-core', core, '--scope', scope, '--mode', cmode];
  if (genre) argv.push('--genre', genre);
  if (chapters && chapters !== '0') argv.push('--chapters', chapters);
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

/* ============================================================
 * Agent 引导式问答面板：挨个询问 → 选项 → 跳过(用默认) → 末轮补充 → 保存 / 据此生成
 * ============================================================ */
let qaState = null; // { project, stageKey, title, questions, answers, skipped, idx, sup }

function ensureQaModal() {
  let o = document.getElementById('qa-modal');
  if (!o) {
    o = document.createElement('div');
    o.id = 'qa-modal';
    o.className = 'qa-modal-overlay';
    o.innerHTML = `
      <div class="qa-modal">
        <div class="qa-head"><span class="qa-title">问答引导</span>
          <button class="qa-close" onclick="closeQaPanel()">×</button></div>
        <div class="qa-progress"></div>
        <div class="qa-body"></div>
        <div class="qa-foot"></div>
      </div>`;
    document.body.appendChild(o);
  }
  return o;
}

function closeQaPanel() {
  const o = document.getElementById('qa-modal');
  if (o) o.style.display = 'none';
}

/* 打开问答面板：拉取模板 + 已保存结果，从第一问开始 */
async function openQaPanel(project, stageKey) {
  const o = ensureQaModal();
  o.style.display = 'flex';
  o.querySelector('.qa-progress').textContent = '';
  o.querySelector('.qa-body').innerHTML = '<div class="qa-loading">加载问答模板…</div>';
  o.querySelector('.qa-foot').innerHTML = '';
  let j;
  try {
    const res = await fetch('/api/qa/' + project);
    j = await res.json();
  } catch (e) {
    o.querySelector('.qa-body').innerHTML = '<p class="rm-empty">加载失败：' + escapeHtml(e) + '</p>';
    return;
  }
  const tpl = j && j[stageKey];
  if (!tpl || !tpl.questions || !tpl.questions.length) {
    o.querySelector('.qa-body').innerHTML = '<p class="rm-empty">该阶段暂无可引导的问题。</p>';
    return;
  }
  const saved = (tpl.saved && tpl.saved.answers) || {};
  const savedSkip = (tpl.saved && tpl.saved.skipped) || {};
  qaState = {
    project: project,
    stageKey: stageKey,
    title: tpl.title || '',
    questions: tpl.questions,
    answers: {},
    skipped: {},
    sup: (tpl.saved && tpl.saved.supplementary) || '',
    idx: 0,
  };
  // 预填已保存的回答，便于中途调整后重新保存
  tpl.questions.forEach(function (q) {
    if (saved[q.key]) { qaState.answers[q.key] = saved[q.key]; qaState.skipped[q.key] = !!savedSkip[q.key]; }
  });
  renderQaStep(o);
}

/* 渲染当前一问 + （末问）补充框，并重建底部按钮 */
function renderQaStep(o) {
  const s = qaState;
  if (!s) return;
  const total = s.questions.length;
  const q = s.questions[s.idx];
  const isLast = s.idx === total - 1;
  const body = o.querySelector('.qa-body');
  // 先取回补充框现值（重渲染时保留）
  const supTa = body.querySelector('#qa-supplementary');
  if (supTa) s.sup = supTa.value;

  o.querySelector('.qa-progress').textContent =
    (s.idx + 1) + ' / ' + total + ' · ' + s.title + ' · ' + q.key;

  const cur = s.answers[q.key] || '';
  const optsHtml = (q.options || []).map(function (op) {
    const on = cur === op.label ? ' on' : '';
    return `<button type="button" class="chip qa-opt${on}" data-label="${escapeHtml(op.label)}">${escapeHtml(op.label)}</button>`;
  }).join('');

  let html = `<div class="qa-q">${escapeHtml(q.question)}</div>`;
  html += `<div class="qa-opts">${optsHtml}</div>`;
  html += `<div class="qa-default muted small">💡 不选择则视为跳过，采用默认：<code>${escapeHtml(q.default_label || q.default || '无')}</code></div>`;
  if (isLast) {
    html += `<div class="qa-supp">
      <label class="muted small">补充说明（可选）：把你额外想告诉 Agent 的偏好写在这里</label>
      <textarea id="qa-supplementary" rows="3" placeholder="例如：男主再幽默一点；结局一定要留白……">${escapeHtml(s.sup)}</textarea>
    </div>`;
  }
  body.innerHTML = html;

  body.querySelectorAll('.qa-opt').forEach(function (b) {
    b.addEventListener('click', function () {
      body.querySelectorAll('.qa-opt').forEach(function (x) { x.classList.remove('on'); });
      b.classList.add('on');
      s.answers[q.key] = b.getAttribute('data-label');
      s.skipped[q.key] = false;
      renderQaFoot(o);
    });
  });
  const supTa2 = body.querySelector('#qa-supplementary');
  if (supTa2) supTa2.addEventListener('input', function () { s.sup = supTa2.value; });

  renderQaFoot(o);
}

/* 底部按钮：上一问 / 跳过(用默认) / 下一问 / 保存 / 保存并据此生成 */
function renderQaFoot(o) {
  const s = qaState;
  if (!s) return;
  const q = s.questions[s.idx];
  const isLast = s.idx === s.questions.length - 1;
  const hasAnswer = !!(s.answers[q.key]);
  const def = q.default_label || q.default || '默认';
  let html = '<div class="qa-actions">';
  if (s.idx > 0) html += `<button class="btn" onclick="qaPrev()">← 上一问</button>`;
  if (!hasAnswer) html += `<button class="btn" onclick="qaSkip()">跳过（用默认：${escapeHtml(def)}）</button>`;
  if (isLast) {
    html += `<button class="btn primary" onclick="qaSave(false)">✔ 保存问答</button>`;
    html += `<button class="btn primary" onclick="qaSave(true)">🚀 保存并据此生成</button>`;
  } else {
    html += `<button class="btn primary" onclick="qaNext()">下一问 →</button>`;
  }
  html += '</div>';
  o.querySelector('.qa-foot').innerHTML = html;
}

function qaSkip() {
  const s = qaState;
  if (!s) return;
  const q = s.questions[s.idx];
  s.answers[q.key] = q.default_label || q.default || '';
  s.skipped[q.key] = true;
  if (s.idx < s.questions.length - 1) { s.idx++; renderQaStep(ensureQaModal()); }
  else { renderQaStep(ensureQaModal()); }
}

/* 下一问：未选则视为跳过（用默认），避免卡住 */
function qaNext() {
  const s = qaState;
  if (!s) return;
  const q = s.questions[s.idx];
  if (!s.answers[q.key]) {
    s.answers[q.key] = q.default_label || q.default || '';
    s.skipped[q.key] = true;
  }
  if (s.idx < s.questions.length - 1) { s.idx++; renderQaStep(ensureQaModal()); }
}

function qaPrev() {
  const s = qaState;
  if (!s) return;
  if (s.idx > 0) { s.idx--; renderQaStep(ensureQaModal()); }
}

/* 保存问答；generate=true 时保存后直接触发该阶段生成命令 */
async function qaSave(generate) {
  const s = qaState;
  if (!s) return;
  const o = ensureQaModal();
  const supTa = o.querySelector('#qa-supplementary');
  if (supTa) s.sup = supTa.value;
  // 兜底：未作答的题目一律按「跳过用默认」处理
  s.questions.forEach(function (q) {
    if (!s.answers[q.key]) {
      s.answers[q.key] = q.default_label || q.default || '';
      s.skipped[q.key] = true;
    }
  });
  const fd = new FormData();
  fd.append('stage', s.stageKey);
  fd.append('payload', JSON.stringify({ answers: s.answers, skipped: s.skipped, supplementary: s.sup }));
  let j;
  try {
    const res = await fetch('/api/qa/' + s.project, { method: 'POST', body: fd });
    j = await res.json();
  } catch (e) {
    alert('保存失败：' + e);
    return;
  }
  if (!(j && j.ok)) { alert('保存失败：' + ((j && j.message) || '未知错误')); return; }
  refreshQaBadges();
  if (generate) {
    closeQaPanel();
    // 复用阶段卡上的「生成」按钮，与手动点击走同一链路
    const genBtn = document.querySelector('.stage-card button[data-gen-stage="' + s.stageKey + '"]');
    if (genBtn) { genBtn.click(); return; }
    // 兜底提示：找不到生成按钮时必须显式告知，避免「保存了却没生成」的静默失败
    alert('问答已保存，但未找到「' + (s.title || s.stageKey) + '」阶段的生成按钮（可能当前状态暂不可执行）。请到对应阶段页手动点击生成按钮。');
    location.reload();
  } else {
    o.querySelector('.qa-body').innerHTML = '<p class="rm-empty">✓ 问答已保存</p>';
    o.querySelector('.qa-foot').innerHTML = '';
    setTimeout(closeQaPanel, 700);
  }
}

/* 刷新各阶段卡上的「已问答」徽标 */
async function refreshQaBadges() {
  const project = currentProjectName();
  if (!project) return;
  let j;
  try {
    const res = await fetch('/api/qa/' + project);
    j = await res.json();
  } catch (e) { return; }
  if (!j) return;
  document.querySelectorAll('.stage-card').forEach(function (card) {
    const btn = card.querySelector('button[data-qa-btn]');
    if (!btn) return;
    const key = btn.getAttribute('data-qa-btn');
    const saved = j[key] && j[key].saved;
    const has = saved && saved.answers && Object.keys(saved.answers).length > 0;
    const h2 = card.querySelector('h2');
    if (!h2) return;
    h2.querySelectorAll('.qa-tag').forEach(function (t) { t.remove(); });
    if (has) {
      const span = document.createElement('span');
      span.className = 'badge ok qa-tag';
      span.textContent = '已问答';
      h2.appendChild(span);
    }
  });
}

document.addEventListener('DOMContentLoaded', function () {
  refreshQaBadges();
});
