/* PropSight — focused dashboard rendering.
 *
 * Columns in scope:
 *   Primary  : budget, configuration_required, hwc
 *   Secondary: call_status, buying_status
 *   AI input : qualitative_remarks (fed to Groq)
 *
 * Charting conventions:
 *   1D rankings : hand-rolled div bars (4 px rounded data end).
 *   2D matrices : Chart.js stacked horizontal bars, fixed categorical hue
 *                 order (> 8 series fold into "Other"), 2 px surface gap,
 *                 legend always present, table fallback for accessibility.
 */

const PALETTE = ['#2a78d6', '#1baf7a', '#eda100', '#008300', '#4a3aa7', '#e34948', '#e87ba4', '#eb6834'];
const SURFACE = '#fcfcfb';
const INK     = '#0b0b0b';
const INK2    = '#52514e';
const MUTED   = '#898781';
const GRID    = '#e1e0d9';

/* ── button helpers ── */
function setBtnLoading(btn, loading, loadingText) {
  if (loading) {
    if (btn.dataset.origHtml === undefined) btn.dataset.origHtml = btn.innerHTML;
    btn.disabled = true; btn.dataset.loading = 'true';
    btn.innerHTML = `<span class="spinner"></span> ${esc(loadingText || 'Working…')}`;
  } else {
    btn.disabled = false; delete btn.dataset.loading;
    if (btn.dataset.origHtml !== undefined) btn.innerHTML = btn.dataset.origHtml;
  }
}
async function withBtnLoading(btn, loadingText, fn) {
  setBtnLoading(btn, true, loadingText);
  try { return await fn(); } finally { setBtnLoading(btn, false); }
}
function setBtnBusy(btn, busy) {
  if (!btn) return;
  if (busy) {
    if (btn.dataset.origHtml === undefined) btn.dataset.origHtml = btn.innerHTML;
    btn.disabled = true; btn.dataset.loading = 'true';
    btn.innerHTML = `<span class="spinner"></span> ${btn.dataset.origHtml}`;
  } else {
    btn.disabled = false; delete btn.dataset.loading;
    if (btn.dataset.origHtml !== undefined) { btn.innerHTML = btn.dataset.origHtml; delete btn.dataset.origHtml; }
  }
}
async function withBtnBusy(btn, fn) {
  setBtnBusy(btn, true);
  try { return await fn(); } finally { setBtnBusy(btn, false); }
}

/* ── tiny utilities ── */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}
function pct(x) {
  return x === null || x === undefined ? '—' : (x * 100).toFixed(1) + '%';
}
function cardTitle(title, subtitle) {
  return `<h2 class="font-semibold mb-1">${esc(title)}</h2>` +
    (subtitle ? `<p class="text-xs mb-3" style="color: var(--muted);">${esc(subtitle)}</p>` : '<div class="mb-3"></div>');
}
function noData(msg) {
  return `<p class="text-sm py-4" style="color: var(--muted);">${esc(msg || 'No data available in this slice.')}</p>`;
}

/* ── 1-D bar chart ── */
function bars(items, { color = PALETTE[0], maxItems = 12 } = {}) {
  if (!items || !items.length) return noData();
  const shown = items.slice(0, maxItems);
  const max   = Math.max(...shown.map(i => i.count));
  return '<div class="space-y-2.5">' + shown.map(i => `
    <div class="flex items-center gap-2 sm:gap-3 text-sm" title="${esc(i.label)}: ${i.count}${i.extra ? ' · ' + esc(i.extra) : ''}">
      <div class="w-24 sm:w-40 truncate text-right shrink-0" style="color: var(--ink-2);">${esc(i.label)}</div>
      <div class="flex-1 bar-track">
        <div class="bar-fill" style="width:${Math.max(2, (i.count / max) * 100)}%; background:${color};"></div>
      </div>
      <div class="w-16 sm:w-24 text-xs shrink-0" style="color: var(--ink-2);">
        ${i.count}${i.extra ? ` <span style="color: var(--muted);">· ${esc(i.extra)}</span>` : ''}
      </div>
    </div>`).join('') + '</div>' +
    (items.length > maxItems ? `<p class="text-xs mt-3" style="color: var(--muted);">+ ${items.length - maxItems} more</p>` : '');
}

/* ── stacked-bar matrix helpers ── */
function foldMatrix(m) {
  if (!m || !m.cols || m.cols.length <= PALETTE.length) return m;
  const keep = PALETTE.length - 1;
  const cols   = m.cols.slice(0, keep).concat(['Other']);
  const matrix = m.matrix.map(row =>
    row.slice(0, keep).concat([row.slice(keep).reduce((a, b) => a + b, 0)]));
  return { rows: m.rows, cols, matrix };
}
function matrixTable(m) {
  return `<details class="mt-2"><summary class="text-xs cursor-pointer" style="color: var(--muted);">View as table</summary>
    <div class="overflow-x-auto mt-2"><table class="matrix text-xs">
      <tr><th></th>${m.cols.map(c => `<th>${esc(c)}</th>`).join('')}<th>Total</th></tr>
      ${m.rows.map((r, i) => `<tr><th class="text-left">${esc(r)}</th>${m.matrix[i].map(v => `<td>${v}</td>`).join('')}<td><b>${m.matrix[i].reduce((a, b) => a + b, 0)}</b></td></tr>`).join('')}
    </table></div></details>`;
}
const _charts = {};
function matrixChart(containerId, m, { height } = {}) {
  const el = document.getElementById(containerId);
  if (!m || !m.rows || !m.rows.length) { el.insertAdjacentHTML('beforeend', noData()); return; }
  m = foldMatrix(m);
  const canvasId = containerId + '-canvas';
  const h = height || Math.max(140, m.rows.length * 34 + 70);
  el.insertAdjacentHTML('beforeend',
    `<div style="height:${h}px"><canvas id="${canvasId}"></canvas></div>` + matrixTable(m));
  if (_charts[canvasId]) _charts[canvasId].destroy();
  _charts[canvasId] = new Chart(document.getElementById(canvasId), {
    type: 'bar',
    data: {
      labels: m.rows,
      datasets: m.cols.map((c, i) => ({
        label: c,
        data: m.matrix.map(row => row[i]),
        backgroundColor: PALETTE[i % PALETTE.length],
        borderColor: SURFACE, borderWidth: 2, borderRadius: 2, barThickness: 18,
      })),
    },
    options: {
      indexAxis: 'y', maintainAspectRatio: false, responsive: true,
      scales: {
        x: { stacked: true, grid: { color: GRID }, ticks: { color: MUTED, precision: 0 } },
        y: { stacked: true, grid: { display: false }, ticks: { color: INK2 } },
      },
      plugins: {
        legend: { position: 'bottom', labels: { color: INK2, boxWidth: 12, boxHeight: 12 } },
        tooltip: { backgroundColor: INK, titleColor: '#fff', bodyColor: '#fff' },
      },
    },
  });
}

/* ── stat tile ── */
function statTile(label, value, note) {
  return `<div class="card p-4">
    <div class="text-xs" style="color: var(--muted);">${esc(label)}</div>
    <div class="text-2xl font-semibold mt-1">${esc(value)}</div>
    ${note ? `<div class="text-xs mt-1" style="color: var(--ink-2);">${esc(note)}</div>` : ''}
  </div>`;
}

/* ── donut chart ── */
function donutChart(containerId, slices, { colors = [PALETTE[0], GRID] } = {}) {
  const el    = document.getElementById(containerId);
  const total = slices.reduce((a, s) => a + s.count, 0);
  if (!total) { el.insertAdjacentHTML('beforeend', noData()); return; }
  const canvasId = containerId + '-canvas';
  el.insertAdjacentHTML('beforeend', `
    <div class="flex items-center gap-4">
      <div style="width:140px;height:140px;flex-shrink:0;"><canvas id="${canvasId}"></canvas></div>
      <div class="text-sm space-y-1.5">
        ${slices.map((s, i) => `<div class="flex items-center gap-2">
          <span style="width:10px;height:10px;border-radius:2px;background:${colors[i % colors.length]};display:inline-block;"></span>
          <span style="color: var(--ink-2);">${esc(s.label)}</span>
          <b>${s.count}</b>
          <span class="text-xs" style="color: var(--muted);">(${(s.count / total * 100).toFixed(0)}%)</span>
        </div>`).join('')}
      </div>
    </div>`);
  if (_charts[canvasId]) _charts[canvasId].destroy();
  _charts[canvasId] = new Chart(document.getElementById(canvasId), {
    type: 'doughnut',
    data: {
      labels: slices.map(s => s.label),
      datasets: [{ data: slices.map(s => s.count), backgroundColor: colors.slice(0, slices.length), borderColor: SURFACE, borderWidth: 2 }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: INK, titleColor: '#fff', bodyColor: '#fff' },
      },
    },
  });
}

/* ================================================================== */
/* State                                                               */
/* ================================================================== */

let currentSegment    = 'all';
let allPropertyTypes  = [];
let currentType       = 'all';
let allFiles          = [];
let currentFile       = 'all';

/* ================================================================== */
/* Init                                                                */
/* ================================================================== */

async function initDashboard() {
  try {
    const data = await fetchJSON(`/reports/${window.REPORT_ID}/data`);
    allPropertyTypes = data.meta.property_types || [];
    allFiles         = data.meta.source_files || [];
    renderChips();
    renderFileTabs();
    renderTypeTabs();
    renderAll(data);
    loadInsights();
  } catch (err) {
    document.getElementById('loading').textContent = '⚠ ' + err.message;
  }
}

function currentQuery() {
  const params = new URLSearchParams();
  if (currentType    !== 'all') params.set('property_type', currentType);
  if (currentSegment !== 'all') params.set('segment', currentSegment);
  if (currentFile    !== 'all') params.set('source_file', currentFile);
  const q = params.toString();
  return q ? `?${q}` : '';
}

async function reload(clickedBtn) {
  const dash    = document.getElementById('dashboard');
  const wasHidden = dash.classList.contains('hidden');
  if (wasHidden) document.getElementById('loading').classList.remove('hidden');
  else dash.dataset.loading = 'true';
  try {
    const run  = () => fetchJSON(`/reports/${window.REPORT_ID}/data${currentQuery()}`);
    const data = clickedBtn ? await withBtnBusy(clickedBtn, run) : await run();
    renderAll(data);
  } catch (err) {
    document.getElementById('loading').textContent = '⚠ ' + err.message;
  } finally {
    delete dash.dataset.loading;
  }
}

/* ── segment chips (HWC only) ── */
function renderChips() {
  const chips = [
    { key: 'all',  label: 'All leads' },
    { key: 'hwc',  label: 'HWC-marked only' },
  ];
  document.getElementById('focus-chips').innerHTML = chips.map(c =>
    `<button data-seg="${c.key}" class="chip-btn px-3 py-1.5 rounded-full text-sm border font-medium ${c.key === currentSegment ? 'tab-active' : ''}"
       style="border-color: var(--border); ${c.key === currentSegment ? '' : 'background: var(--surface-1); color: var(--ink-2);'}">
       ${esc(c.label)}</button>`).join('');
  document.querySelectorAll('.chip-btn').forEach(btn => btn.addEventListener('click', () => {
    currentSegment = btn.dataset.seg;
    renderChips();
    reload(document.querySelector(`.chip-btn[data-seg="${CSS.escape(currentSegment)}"]`));
  }));
}

/* ── property type tabs ── */
function renderTypeTabs() {
  const tabs = ['all', ...allPropertyTypes];
  document.getElementById('type-tabs').innerHTML = tabs.map(t =>
    `<button data-type="${esc(t)}" class="tab-btn px-3 py-1.5 rounded-full text-sm border ${t === currentType ? 'tab-active' : ''}"
       style="border-color: var(--border); ${t === currentType ? '' : 'background: var(--surface-1); color: var(--ink-2);'}">
       ${t === 'all' ? 'All types' : esc(t)}</button>`).join('');
  document.querySelectorAll('.tab-btn').forEach(btn => btn.addEventListener('click', () => {
    currentType = btn.dataset.type;
    renderTypeTabs();
    reload(document.querySelector(`.tab-btn[data-type="${CSS.escape(currentType)}"]`));
  }));
}

/* ── file tabs ── */
function renderFileTabs() {
  if (allFiles.length <= 1) {
    document.getElementById('file-tabs').innerHTML = '';
    return;
  }
  const tabs = [{ key: 'all', label: 'All files' }].concat(
    allFiles.map(f => ({ key: f, label: f })));
  document.getElementById('file-tabs').innerHTML = tabs.map(t =>
    `<button data-file="${esc(t.key)}" class="file-btn px-3 py-1.5 rounded-full text-sm border font-medium ${t.key === currentFile ? 'tab-active' : ''}"
       style="border-color: var(--border); ${t.key === currentFile ? '' : 'background: var(--surface-1); color: var(--ink-2);'}"
       title="${esc(t.label)}">
       ${esc(t.label.length > 20 ? t.label.substring(0, 17) + '...' : t.label)}</button>`).join('');
  document.querySelectorAll('.file-btn').forEach(btn => btn.addEventListener('click', () => {
    currentFile = btn.dataset.file;
    renderFileTabs();
    reload(document.querySelector(`.file-btn[data-file="${CSS.escape(currentFile)}"]`));
  }));
}

/* ================================================================== */
/* Render all cards                                                    */
/* ================================================================== */

function renderAll(d) {
  document.getElementById('loading').classList.add('hidden');
  document.getElementById('dashboard').classList.remove('hidden');
  document.getElementById('report-files').textContent = (d.meta.source_files || []).join(' · ');

  const core   = d.core   || {};
  const counts = core.counts || {};
  const b      = d.budget || {};

  // Hero KPIs
  document.getElementById('hero-tiles').innerHTML =
    statTile('Total leads',   d.meta.total_leads) +
    statTile('HWC-marked',    counts.hwc ?? 0, pct(counts.hwc_share) + ' of total') +
    statTile('Median budget',
      b.median !== null && b.median !== undefined ? `₹${b.median} Cr` : '—') +
    statTile('Top config',
      d.configuration && d.configuration.overall && d.configuration.overall.length
        ? d.configuration.overall[0].value : '—',
      d.configuration && d.configuration.overall && d.configuration.overall.length
        ? pct(d.configuration.overall[0].share) + ' of leads' : '');

  // HWC donut — hide completely when already filtered to HWC-only
  const donutCard = document.getElementById('card-hwc-donut');
  if (currentSegment === 'hwc') {
    donutCard.classList.add('hidden');
    donutCard.parentElement.classList.remove('md:grid-cols-2');
    donutCard.parentElement.classList.add('md:grid-cols-1');
  } else {
    donutCard.classList.remove('hidden');
    donutCard.parentElement.classList.add('md:grid-cols-2');
    donutCard.parentElement.classList.remove('md:grid-cols-1');
    setCard('card-hwc-donut', cardTitle('HWC clients', 'any non-empty value in the HWC column marks a client that matters'));
    donutChart('card-hwc-donut', core.hwc_donut || [], { colors: [PALETTE[5], GRID] });
  }

  renderBudget(d);
  renderConfiguration(d);
  renderCallStatus(d);
  renderBuyingStatus(d);
  renderConfigBudget(d);
  renderHwcConfig(d);
  renderHwcBudget(d);
  renderBuyingConfig(d);
  renderCallBuying(d);
}

function setCard(id, html) { document.getElementById(id).innerHTML = html; }

/* ── budget ── */
function renderBudget(d) {
  const b = d.budget || {};
  let html = cardTitle('Budget distribution',
    `fixed Crore bands · values in ${b.unit || 'Crore'}`);
  if (!b.buckets || !b.buckets.length) {
    html += noData('Not enough parseable budget values for bucketing.');
  } else {
    html += bars(b.buckets.map(x => ({
      label: x.label,
      count: x.count,
      extra: pct(x.count / b.parsed_count),
    })), { color: PALETTE[1] });
    if (b.median !== undefined)
      html += `<p class="text-xs mt-3" style="color: var(--muted);">median ₹${b.median} Cr · mean ₹${b.mean} Cr · range ₹${b.min}–${b.max} Cr · parsed ${b.parsed_count}/${b.total_rows} entries</p>`;
  }
  setCard('card-budget', html);
}

/* ── configuration ── */
function renderConfiguration(d) {
  setCard('card-configuration',
    cardTitle('Configuration required', 'BHK / unit type demanded by leads') +
    bars((d.configuration && d.configuration.overall || []).map(c => ({
      label: c.value, count: c.count, extra: pct(c.share),
    })), { color: PALETTE[0] }));
}

/* ── call status ── */
function renderCallStatus(d) {
  setCard('card-call-status',
    cardTitle('Call status', 'distribution of Latest Call Status values') +
    bars(((d.call_status && d.call_status.distribution) || []).map(x => ({
      label: x.value, count: x.count, extra: pct(x.share),
    })), { color: PALETTE[2] }));
}

/* ── buying status ── */
function renderBuyingStatus(d) {
  setCard('card-buying-status',
    cardTitle('Buying status', 'distribution of Buying Status values') +
    bars(((d.buying_status && d.buying_status.distribution) || []).map(x => ({
      label: x.value, count: x.count, extra: pct(x.share),
    })), { color: PALETTE[3] }));
}

/* ── cross-tabs ── */
function renderConfigBudget(d) {
  setCard('card-config-budget', cardTitle('Configuration × budget bucket',
    'which BHK/unit types sit in which price bands'));
  matrixChart('card-config-budget', d.config_x_budget);
}

function renderHwcConfig(d) {
  setCard('card-hwc-config', cardTitle('HWC-flagged × configuration',
    'what priority clients are specifically asking for'));
  matrixChart('card-hwc-config', d.hwc_x_config);
}

function renderHwcBudget(d) {
  setCard('card-hwc-budget', cardTitle('HWC-flagged × budget bucket',
    'do priority clients sit in higher price bands?'));
  matrixChart('card-hwc-budget', d.hwc_x_budget);
}

function renderBuyingConfig(d) {
  setCard('card-buying-config', cardTitle('Buying status × configuration',
    'do warmer buyers cluster in specific unit types?'));
  matrixChart('card-buying-config', d.buying_status_x_config);
}

function renderCallBuying(d) {
  setCard('card-call-buying', cardTitle('Call status × buying status',
    'are unreachable / busy leads actually warm buyers?'));
  matrixChart('card-call-buying', d.call_status_x_buying);
}



/* ── AI insights (Groq) ── */
async function loadInsights() {
  const el = document.getElementById('card-insights');
  el.innerHTML = cardTitle('AI insights',
    'generated by Groq from budget/config/HWC stats + sampled qualitative remarks') +
    `<p class="text-sm" style="color: var(--muted);"><span class="spinner"></span> Generating…</p>`;
  try {
    const data = await fetchJSON(`/reports/${window.REPORT_ID}/insights`);
    if (data.error) {
      el.innerHTML = cardTitle('AI insights', 'optional — set GROQ_API_KEY in .env to enable') +
        `<p class="text-sm py-2" style="color: var(--muted);">${esc(data.error)}</p>`;
      return;
    }
    
    // Formatter to highlight BHK, Cr/L numbers, and percentages
    const highlight = (text) => {
      let safe = esc(text);
      // Regex replacements for highlights (using $& for full match injection)
      safe = safe.replace(/\b\d+(\.\d+)?\s*(BHK|RK)\b/gi, '<span class="px-1.5 py-0.5 bg-blue-100 text-blue-800 rounded font-semibold whitespace-nowrap">$&</span>');
      // Matches: "5 Cr", "5-7 Cr", "5 - 7 Cr", "₹ 5 Cr"
      safe = safe.replace(/(?:(?:₹|Rs\.?)\s*)?(?:\d+(?:\.\d+)?\s*(?:-|to|–)\s*)?\d+(?:\.\d+)?\s*(?:Cr|Lakhs?|L|K|crore)\b/gi, '<span class="px-1.5 py-0.5 bg-green-100 text-green-800 rounded font-semibold whitespace-nowrap">$&</span>');
      safe = safe.replace(/\b\d+(\.\d+)?%/g, '<span class="px-1.5 py-0.5 bg-purple-100 text-purple-800 rounded font-semibold whitespace-nowrap">$&</span>');
      return safe;
    };

    el.innerHTML = cardTitle('AI insights',
      'generated from stats + sampled remarks — verify before acting') +
      `<div class="grid md:grid-cols-2 gap-3">` + data.insights.map(i => `
        <div class="border rounded-lg p-3" style="border-color: var(--grid);">
          <div class="flex items-center gap-2">
            <h3 class="text-sm font-semibold flex-1">${esc(i.title)}</h3>
            <span class="text-xs px-2 py-0.5 rounded-full" style="background: var(--page); color: var(--muted);">${esc(i.confidence)}</span>
          </div>
          <p class="text-xs mt-2 leading-relaxed" style="color: var(--ink-2);">${highlight(i.finding)}</p>
          <p class="text-xs mt-2 leading-relaxed"><b>Action:</b> ${highlight(i.action)}</p>
        </div>`).join('') + `</div>`;
  } catch (err) {
    el.innerHTML = cardTitle('AI insights', '') +
      `<p class="text-sm" style="color: var(--muted);">AI insights unavailable: ${esc(err.message)}</p>`;
  }
}
