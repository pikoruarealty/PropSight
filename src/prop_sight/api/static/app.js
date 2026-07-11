/* PropSight — focused dashboard rendering.
 *
 * Columns in scope:
 *   Primary  : budget, configuration_required, hwc
 *   Secondary: call_status, buying_status
 *   Timing   : date -> weekday / hour of arrival
 *   AI input : qualitative_remarks (fed to Groq)
 *
 * Charting conventions:
 *   1D distributions : donuts. Slices past the 7th fold into "Other" — the
 *                      categorical palette has 8 fixed slots and hues are never
 *                      cycled. Every slice is direct-labelled with its count and
 *                      share in the legend, because a donut alone cannot be read
 *                      accurately when two slices are close in size.
 *   2D matrices      : stacked horizontal bars. A pie cannot show two variables
 *                      at once. A "% share" toggle normalizes each row to 100%,
 *                      which is how you actually compare rows of different size.
 *   Heatmap          : one-hue sequential blue ramp, light = near zero.
 *
 * Every crosstab is captioned with the number of leads it was computed from.
 * Most leads have no budget or configuration recorded, so a crosstab silently
 * represents a minority of them; the caption is what stops that from reading as
 * if it covered everyone.
 */

/* Chart colours live in CSS variables so light and dark are defined in one place
 * (base.html), but a canvas cannot read a CSS variable — Chart.js needs a literal
 * string at draw time. These are resolved from the stylesheet on load and again
 * whenever the theme flips, hence `let` rather than `const`. */
let PALETTE, BLUE_RAMP, SURFACE, INK, INK2, MUTED, GRID;

/* Chart.js's built-in tooltip is drawn *inside the canvas's own pixel buffer*.
 * The donut canvases are a fixed 150x150 so a two-line explanation ("152 of
 * 1,519 leads with a stated budget") runs past the canvas edge and is simply
 * never drawn — not styled wrong, just off the raster entirely. An external
 * (real DOM) tooltip has no such bound: it floats over the page and wraps.
 * `enabled: false` + `external: externalTooltip` on any chart's tooltip
 * options switches it to this renderer; `callbacks.label` still supplies the
 * text exactly as before. */
function externalTooltip(context) {
  const { chart, tooltip } = context;
  let el = document.getElementById('chartjs-tooltip');
  if (!el) {
    el = document.createElement('div');
    el.id = 'chartjs-tooltip';
    document.body.appendChild(el);
  }

  if (tooltip.opacity === 0) {
    el.style.opacity = 0;
    return;
  }

  const titleLines = tooltip.title || [];
  const bodyLines = (tooltip.body || []).map(b => b.lines);
  let html = titleLines.map(t => `<div class="chartjs-tooltip-title">${esc(t)}</div>`).join('');
  bodyLines.forEach((lines, i) => {
    const swatch = tooltip.labelColors?.[i];
    lines.forEach(line => {
      html += `<div class="chartjs-tooltip-row">${
        swatch ? `<span class="chartjs-tooltip-swatch" style="background:${swatch.backgroundColor}"></span>` : ''
      }<span>${esc(line)}</span></div>`;
    });
  });
  el.innerHTML = html;

  // Positioned against the canvas's own rect (not the chart area) since the
  // canvas can be scrolled or offset within its card.
  const rect = chart.canvas.getBoundingClientRect();
  let left = rect.left + tooltip.caretX;
  let top = rect.top + tooltip.caretY;
  el.style.opacity = 1;
  el.style.left = '0px';
  el.style.top = '0px';
  el.style.transform = `translate(${left}px, ${top}px)`;

  // Keep it on-screen: a slice near the right or bottom edge of the viewport
  // would otherwise push the tooltip half off the page.
  requestAnimationFrame(() => {
    const box = el.getBoundingClientRect();
    let dx = 12, dy = -box.height - 12;
    if (left + dx + box.width > window.innerWidth - 8) dx = -box.width - 12;
    if (top + dy < 8) dy = 12;
    el.style.transform = `translate(${left + dx}px, ${top + dy}px)`;
  });
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* --ink, --muted etc. are themselves defined as `rgb(var(--on-surface))` —
 * a custom property referencing another custom property. getComputedStyle on
 * the root returns that reference literally, unresolved, because the browser
 * only substitutes var() chains when the property is actually consumed by a
 * rendered CSS property. Setting `color` on a probe element and reading it
 * back forces that resolution down to a real "rgb(r, g, b)" string. */
let _colourProbe;
function resolvedColourVar(name) {
  if (!_colourProbe) {
    _colourProbe = document.createElement('div');
    _colourProbe.style.display = 'none';
    document.body.appendChild(_colourProbe);
  }
  _colourProbe.style.color = `var(${name})`;
  return getComputedStyle(_colourProbe).color;
}

function refreshThemeColours() {
  /* Categorical slots — fixed order, never cycled, never assigned by rank. */
  PALETTE = [1, 2, 3, 4, 5, 6, 7, 8].map(i => cssVar(`--series-${i}`));
  /* Sequential ramp (low -> high) for magnitude encoding. */
  BLUE_RAMP = cssVar('--blue-ramp').split(',').map(s => s.trim());
  SURFACE = resolvedColourVar('--surface-1');
  INK     = resolvedColourVar('--ink');
  INK2    = resolvedColourVar('--ink-2');
  MUTED   = resolvedColourVar('--muted');
  GRID    = resolvedColourVar('--grid');
}
refreshThemeColours();

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
function num(n) { return (n ?? 0).toLocaleString(); }
function cardTitle(title, subtitle) {
  return `<h2 class="font-semibold mb-1">${esc(title)}</h2>` +
    (subtitle ? `<p class="text-xs mb-3" style="color: var(--muted);">${esc(subtitle)}</p>` : '<div class="mb-3"></div>');
}
function noData(msg) {
  return `<p class="text-sm py-4" style="color: var(--muted);">${esc(msg || 'No data available in this slice.')}</p>`;
}
/* Show a card only when the backend says the section has data. An empty chart
 * reads as "no leads in this category" when it really means "this export never
 * had that column". */
function toggleCard(id, visible) {
  const el = document.getElementById(id);
  if (!el) return false;
  const card = el.closest('.card') || el;
  card.classList.toggle('hidden', !visible);
  return visible;
}

const _charts = {};
function mountChart(canvasId, config) {
  if (_charts[canvasId]) _charts[canvasId].destroy();
  _charts[canvasId] = new Chart(document.getElementById(canvasId), config);
}

/* ── category folding ── */
/* The palette has 8 fixed slots. Categories past that fold into "Other" rather
 * than generating a 9th hue that would be indistinguishable under CVD. */
function foldSlices(items, limit = 7) {
  if (!items || items.length <= limit + 1) return items || [];
  const head = items.slice(0, limit);
  const tail = items.slice(limit);
  return head.concat([{
    label: 'Other',
    count: tail.reduce((a, b) => a + b.count, 0),
    folded: tail.length,
  }]);
}

/* ── donut chart (1-D distributions) ──
 * `stackLegend` puts the legend under the donut instead of beside it. Campaign
 * and ad-set names are long, and beside a donut in a 3-up grid they truncate to
 * "Bungalow ..." twice over — identical labels for different series. */
function donutChart(containerId, items, { colors = PALETTE, note, stackLegend = false, colorMap, basis = 'leads' } = {}) {
  const el = document.getElementById(containerId);
  const slices = foldSlices((items || []).filter(s => s.count > 0));
  const total = slices.reduce((a, s) => a + s.count, 0);
  if (!total) { el.insertAdjacentHTML('beforeend', noData()); return; }

  // Slices arrive sorted by count, so index-based colour would repaint a series
  // whenever the ranking changed — "Bad" would take Good's green the moment it
  // outnumbered it. `colorMap` pins a hue to the label instead.
  const colourOf = (label, i) => (colorMap && colorMap[label]) || colors[i % colors.length];

  const canvasId = containerId + '-canvas';
  // The legend carries count + share for every slice: a donut's angles cannot be
  // compared accurately by eye, so identity and magnitude are both stated in text.
  // `min-w-0` on both the legend column and the label: a flex item's default
  // min-width is `auto`, so a long campaign name refuses to shrink and pushes
  // the count and share straight out through the side of the card.
  const legend = slices.map((s, i) => `
    <div class="flex items-baseline gap-2 text-sm">
      <span class="shrink-0" style="width:10px;height:10px;border-radius:2px;background:${colourOf(s.label, i)};display:inline-block;"></span>
      <span class="flex-1 min-w-0 truncate" style="color: var(--ink-2);" title="${esc(s.label)}${s.folded ? ` (${s.folded} categories)` : ''}">${esc(s.label)}</span>
      <b class="tabular-nums shrink-0">${num(s.count)}</b>
      <span class="text-xs tabular-nums shrink-0" style="color: var(--muted);">${(s.count / total * 100).toFixed(0)}%</span>
    </div>`).join('');

  // Beside the donut the legend must flex-shrink (w-full would fight the fixed
  // 150px canvas and push the share % out of the card); stacked, it takes the
  // full width. min-w-0 in both cases so `truncate` can actually engage.
  // Side-by-side only from `lg`. The 3-up card grid starts at `md` (768px),
  // where a card is ~232px — narrower than the 150px donut plus a legend row.
  const wrap = stackLegend
    ? 'flex flex-col items-center gap-4'
    : 'flex flex-col lg:flex-row items-center gap-4';
  const legendClass = stackLegend ? 'w-full min-w-0 space-y-1.5' : 'flex-1 min-w-0 w-full lg:w-auto space-y-1.5';

  // Every percentage here is a share of `total` — the slices actually drawn —
  // which is very often NOT the report's whole lead count (a lead with no
  // budget contributes to no budget slice, for instance). `basis` names what
  // that denominator actually is, so "27.7%" doesn't default to reading as
  // "of all leads" when it's really "of leads with a stated budget".
  const caption = note || `based on ${num(total)} ${basis}`;

  el.insertAdjacentHTML('beforeend', `
    <div class="${wrap}">
      <div style="width:150px;height:150px;flex-shrink:0;"><canvas id="${canvasId}"></canvas></div>
      <div class="${legendClass}">${legend}</div>
    </div>
    <p class="text-xs mt-3" style="color: var(--muted);">${esc(caption)}</p>`);

  mountChart(canvasId, {
    type: 'doughnut',
    data: {
      labels: slices.map(s => s.label),
      datasets: [{
        data: slices.map(s => s.count),
        backgroundColor: slices.map((s, i) => colourOf(s.label, i)),
        borderColor: SURFACE, borderWidth: 2,
      }],
    },
    options: {
      maintainAspectRatio: false, cutout: '58%',
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: false, external: externalTooltip,
          callbacks: {
            // Leads with the denominator's meaning, not the slice's — "152 of
            // 1,519 leads with a stated budget are 10+ Cr", so the number the
            // percentage is *of* is never left for the reader to guess at.
            label: c => `${num(c.parsed)} of ${num(total)} ${basis} are ${c.label} (${(c.parsed / total * 100).toFixed(1)}%)`,
          },
        },
      },
    },
  });
}

/* ── stacked-bar matrix (2-D crosstabs) ── */
/* Columns become series, so they are capped at the 8 palette slots. Rows become
 * bars; past ~10 the tail is single-lead categories whose bars are invisible
 * anyway, and they only push the interesting rows off the top of the card.
 * Both axes arrive sorted by marginal total, so the tail is always the small end. */
const MAX_MATRIX_ROWS = 10;

function foldMatrix(m) {
  if (!m || !m.cols) return m;
  let { rows, cols, matrix } = m;

  if (cols.length > PALETTE.length) {
    const keep = PALETTE.length - 1;
    cols = cols.slice(0, keep).concat(['Other']);
    matrix = matrix.map(row =>
      row.slice(0, keep).concat([row.slice(keep).reduce((a, b) => a + b, 0)]));
  }
  if (rows.length > MAX_MATRIX_ROWS + 1) {
    const tail = matrix.slice(MAX_MATRIX_ROWS);
    const merged = cols.map((_, i) => tail.reduce((a, row) => a + row[i], 0));
    rows = rows.slice(0, MAX_MATRIX_ROWS).concat([`Other (${tail.length} categories)`]);
    matrix = matrix.slice(0, MAX_MATRIX_ROWS).concat([merged]);
  }
  return { ...m, rows, cols, matrix };
}
function matrixTable(m) {
  return `<details class="mt-2"><summary class="text-xs cursor-pointer" style="color: var(--muted);">View as table</summary>
    <div class="overflow-x-auto mt-2"><table class="matrix text-xs">
      <tr><th></th>${m.cols.map(c => `<th>${esc(c)}</th>`).join('')}<th>Total</th></tr>
      ${m.rows.map((r, i) => `<tr><th class="text-left">${esc(r)}</th>${m.matrix[i].map(v => `<td>${v}</td>`).join('')}<td><b>${m.matrix[i].reduce((a, b) => a + b, 0)}</b></td></tr>`).join('')}
    </table></div></details>`;
}

/* The sentence a non-analyst actually needs: what fraction of leads is this
 * chart even about? Without it, "112" looks like a count of all 5,000 leads. */
function coverageNote(m) {
  const c = m && m.coverage;
  if (!c || !c.total) return '';
  const share = c.counted / c.total;
  const thin = share < 0.25;
  return `<p class="text-xs mt-2 ${thin ? 'coverage-warn' : ''}" style="color: var(--muted);">
    Based on <b>${num(c.counted)}</b> of ${num(c.total)} leads (${(share * 100).toFixed(0)}%) that have <em>both</em> values recorded.
    ${thin ? ' Percentages describe that subset, not your whole database.' : ''}
  </p>`;
}

function matrixChart(containerId, full, { height, summary } = {}) {
  const el = document.getElementById(containerId);
  if (!full || !full.rows || !full.rows.length) { el.insertAdjacentHTML('beforeend', noData()); return; }
  // Chart the folded matrix; table the complete one — the table is the escape
  // hatch for exactly the categories folding hides.
  const m = foldMatrix(full);

  const canvasId = containerId + '-canvas';
  const toggleId = containerId + '-pct';
  const h = height || Math.max(140, m.rows.length * 34 + 70);

  el.insertAdjacentHTML('beforeend', `
    ${summary ? `<p class="text-sm mb-3 chart-summary">${esc(summary)}</p>` : ''}
    <label class="flex items-center gap-2 text-xs mb-2 cursor-pointer hide-on-print" style="color: var(--muted);">
      <input type="checkbox" id="${toggleId}"> Show as % of each row
    </label>
    <div style="height:${h}px"><canvas id="${canvasId}"></canvas></div>
    ${coverageNote(m)}
    ${matrixTable(full)}`);

  const rowTotals = m.matrix.map(r => r.reduce((a, b) => a + b, 0) || 1);
  const render = (asPct) => mountChart(canvasId, {
    type: 'bar',
    data: {
      labels: m.rows,
      datasets: m.cols.map((c, i) => ({
        label: c,
        // Colour follows the entity (the column), not its rank in this slice.
        backgroundColor: PALETTE[i % PALETTE.length],
        data: m.matrix.map((row, r) => asPct ? (row[i] / rowTotals[r]) * 100 : row[i]),
        raw: m.matrix.map(row => row[i]),
        borderColor: SURFACE, borderWidth: 2, borderRadius: 2, barThickness: 18,
      })),
    },
    options: {
      indexAxis: 'y', maintainAspectRatio: false, responsive: true,
      scales: {
        x: {
          stacked: true, grid: { color: GRID },
          max: asPct ? 100 : undefined,
          ticks: { color: MUTED, precision: 0, callback: v => asPct ? `${v}%` : v },
        },
        y: { stacked: true, grid: { display: false }, ticks: { color: INK2 } },
      },
      plugins: {
        legend: { position: 'bottom', labels: { color: INK2, boxWidth: 12, boxHeight: 12 } },
        tooltip: {
          enabled: false, external: externalTooltip,
          callbacks: {
            label: (ctx) => {
              const count = ctx.dataset.raw[ctx.dataIndex];
              const share = (count / rowTotals[ctx.dataIndex] * 100).toFixed(1);
              return `${ctx.dataset.label}: ${num(count)} (${share}% of ${esc(m.rows[ctx.dataIndex])})`;
            },
          },
        },
      },
    },
  });

  render(false);
  document.getElementById(toggleId).addEventListener('change', e => render(e.target.checked));
}

/* ── heatmap (weekday x hour) ── */
function heatmap(containerId, m, { note } = {}) {
  const el = document.getElementById(containerId);
  if (!m || !m.rows || !m.rows.length) { el.insertAdjacentHTML('beforeend', noData()); return; }
  const max = Math.max(1, ...m.matrix.flat());
  // One hue, light -> dark. The lightest step means "near zero" and recedes.
  const shade = v => v === 0 ? SURFACE : BLUE_RAMP[Math.min(BLUE_RAMP.length - 1,
    Math.max(1, Math.round((v / max) * (BLUE_RAMP.length - 1))))];

  const header = m.cols.map((c, i) => `<th class="hm-h">${i % 3 === 0 ? esc(c) : ''}</th>`).join('');
  const body = m.rows.map((r, ri) => `
    <tr><th class="hm-row">${esc(r.slice(0, 3))}</th>
    ${m.matrix[ri].map((v, ci) => `<td class="hm-cell" style="background:${shade(v)};"
        title="${esc(r)} ${esc(m.cols[ci])}:00 — ${num(v)} lead${v === 1 ? '' : 's'}"></td>`).join('')}
    </tr>`).join('');

  el.insertAdjacentHTML('beforeend', `
    <div class="overflow-x-auto"><table class="heatmap"><tr><th></th>${header}</tr>${body}</table></div>
    <div class="flex items-center gap-2 mt-3 text-xs" style="color: var(--muted);">
      <span>0</span>
      ${BLUE_RAMP.map(c => `<span style="width:14px;height:10px;background:${c};display:inline-block;border-radius:1px;"></span>`).join('')}
      <span>${num(max)} leads</span>
    </div>
    ${note ? `<p class="text-xs mt-2" style="color: var(--muted);">${esc(note)}</p>` : ''}`);
}

/* ── stat tile ── */
function statTile(label, value, note) {
  return `<div class="card p-4 flex flex-col">
    <span class="text-label-xs uppercase tracking-wider text-on-surface-variant mb-1">${esc(label)}</span>
    <span class="text-[28px] font-semibold tabular-nums leading-tight">${esc(value)}</span>
    ${note ? `<span class="text-xs mt-2 tabular-nums text-on-surface-variant">${esc(note)}</span>` : ''}
  </div>`;
}

/* ================================================================== */
/* State                                                               */
/* ================================================================== */

let currentSegment    = 'all';
let allPropertyTypes  = [];
let currentType       = 'all';
let allFiles          = [];
let currentFile       = 'all';
let chartSummaries    = {};
let hasClassification = false;

/* ── extra filters: budget range + configuration/call/buying status ──
 * Option lists are captured once from the *unfiltered* initial payload (same
 * idea as allPropertyTypes/allFiles above) so picking one filter doesn't make
 * the others' checklists shrink out from under the user mid-selection. */
let budgetMin             = null;
let budgetMax             = null;
let configFilter          = [];
let callStatusFilter      = [];
let buyingStatusFilter    = [];
let allConfigValues       = [];
let allCallStatusValues   = [];
let allBuyingStatusValues = [];

/* ================================================================== */
/* Init                                                                */
/* ================================================================== */

/* Kept so a theme flip can repaint the charts without refetching the report. */
let lastData = null;

async function initDashboard() {
  try {
    const data = await fetchJSON(`/reports/${window.REPORT_ID}/data`);
    allPropertyTypes      = data.meta.property_types || [];
    allFiles              = data.meta.source_files || [];
    allConfigValues       = (data.configuration?.overall || []).map(c => c.value);
    allCallStatusValues   = (data.call_status?.distribution || []).map(x => x.value);
    allBuyingStatusValues = (data.buying_status?.distribution || []).map(x => x.value);
    hasClassification = !!(data.availability || {}).classification;
    renderChips();
    renderFileTabs();
    renderTypeTabs();
    renderConfigFilter();
    renderCallStatusFilter();
    renderBuyingStatusFilter();
    wireBudgetFilter();
    wireFiltersClear();
    wireFilterDropdown('filters-btn', 'filters-panel');
    updateFiltersBadge();
    renderAll(data);
    loadChartSummaries();
    loadInsights();
    wirePdfExport();
  } catch (err) {
    document.getElementById('loading').textContent = '⚠ ' + err.message;
  }
}

/* ── generic collapsible filter-panel toggle (Reports + Sorter both use this) ──
 * Mirrors the user-menu dropdown pattern in theme.js: click to toggle, click
 * anywhere outside or Escape to close, aria-expanded kept in sync. */
function wireFilterDropdown(btnId, panelId) {
  const btn = document.getElementById(btnId);
  const panel = document.getElementById(panelId);
  if (!btn || !panel) return;
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const opening = panel.classList.contains('hidden');
    panel.classList.toggle('hidden', !opening);
    btn.setAttribute('aria-expanded', String(opening));
  });
  panel.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => {
    panel.classList.add('hidden');
    btn.setAttribute('aria-expanded', 'false');
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      panel.classList.add('hidden');
      btn.setAttribute('aria-expanded', 'false');
    }
  });
}

/* ── multi-select pill list, shared by configuration/call-status/buying-status ── */
function renderMultiPills(containerId, wrapId, values, selected, onToggle) {
  const wrap = document.getElementById(wrapId);
  if (wrap) wrap.classList.toggle('hidden', !values.length);
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = values.map(v => {
    const active = selected.includes(v);
    return `<button type="button" data-val="${esc(v)}"
      class="pill-btn px-3 py-1.5 rounded-full text-sm border font-medium ${active ? 'tab-active' : ''}"
      style="border-color: var(--border); ${active ? '' : 'background: var(--surface-1); color: var(--ink-2);'}">
      ${esc(v)}</button>`;
  }).join('');
  container.querySelectorAll('.pill-btn').forEach(btn => btn.addEventListener('click', () => {
    const v = btn.dataset.val;
    const idx = selected.indexOf(v);
    if (idx >= 0) selected.splice(idx, 1); else selected.push(v);
    onToggle();
  }));
}

function renderConfigFilter() {
  renderMultiPills('filter-config', 'filter-config-wrap', allConfigValues, configFilter, () => {
    renderConfigFilter();
    updateFiltersBadge();
    reload();
  });
}
function renderCallStatusFilter() {
  renderMultiPills('filter-call-status', 'filter-call-status-wrap', allCallStatusValues, callStatusFilter, () => {
    renderCallStatusFilter();
    updateFiltersBadge();
    reload();
  });
}
function renderBuyingStatusFilter() {
  renderMultiPills('filter-buying-status', 'filter-buying-status-wrap', allBuyingStatusValues, buyingStatusFilter, () => {
    renderBuyingStatusFilter();
    updateFiltersBadge();
    reload();
  });
}

function wireBudgetFilter() {
  const min = document.getElementById('filter-budget-min');
  const max = document.getElementById('filter-budget-max');
  if (!min || !max) return;
  const apply = () => {
    budgetMin = min.value !== '' ? min.value : null;
    budgetMax = max.value !== '' ? max.value : null;
    updateFiltersBadge();
    reload();
  };
  min.addEventListener('change', apply);
  max.addEventListener('change', apply);
}

function wireFiltersClear() {
  const btn = document.getElementById('filters-clear');
  if (!btn) return;
  btn.addEventListener('click', () => {
    currentSegment = 'all';
    currentType = 'all';
    currentFile = 'all';
    budgetMin = null;
    budgetMax = null;
    configFilter = [];
    callStatusFilter = [];
    buyingStatusFilter = [];
    const min = document.getElementById('filter-budget-min');
    const max = document.getElementById('filter-budget-max');
    if (min) min.value = '';
    if (max) max.value = '';
    renderChips();
    renderTypeTabs();
    renderFileTabs();
    renderConfigFilter();
    renderCallStatusFilter();
    renderBuyingStatusFilter();
    updateFiltersBadge();
    reload();
  });
}

function activeFilterCount() {
  let n = 0;
  if (currentSegment !== 'all') n++;
  if (currentType !== 'all') n++;
  if (currentFile !== 'all') n++;
  if (budgetMin !== null) n++;
  if (budgetMax !== null) n++;
  if (configFilter.length) n++;
  if (callStatusFilter.length) n++;
  if (buyingStatusFilter.length) n++;
  return n;
}

function updateFiltersBadge(badgeId = 'filters-badge') {
  const badge = document.getElementById(badgeId);
  if (!badge) return;
  const n = activeFilterCount();
  badge.textContent = String(n);
  badge.classList.toggle('hidden', n === 0);
}

/* ── PDF export: section picker, persisted in localStorage ── */
const PDF_CONFIG_KEY = 'propsight-pdf-export-config';

function pdfExportSections() {
  return Array.from(document.querySelectorAll('[data-export]')).map(el => ({
    id: el.dataset.export,
    label: el.dataset.exportLabel || el.dataset.export,
  }));
}

function loadPdfConfig() {
  try {
    const raw = localStorage.getItem(PDF_CONFIG_KEY);
    return raw ? JSON.parse(raw) : null; // null = export everything (default)
  } catch {
    return null;
  }
}

function exportPdf() {
  const cfg = loadPdfConfig();
  const hidden = [];
  if (cfg) {
    pdfExportSections().forEach(s => {
      if (cfg[s.id] === false) {
        document.querySelectorAll(`[data-export="${s.id}"]`).forEach(el => {
          el.classList.add('export-hide');
          hidden.push(el);
        });
      }
    });
  }
  const cleanup = () => hidden.forEach(el => el.classList.remove('export-hide'));
  window.addEventListener('afterprint', cleanup, { once: true });
  setTimeout(cleanup, 3000); // afterprint isn't reliable in every browser dialog flow
  window.print();
}

function wirePdfExport() {
  const popup = document.getElementById('pdf-config-popup');
  document.getElementById('download-pdf-btn').addEventListener('click', exportPdf);
  document.getElementById('pdf-config-btn').addEventListener('click', () => {
    const cfg = loadPdfConfig();
    document.getElementById('pdf-config-options').innerHTML = pdfExportSections().map(s => `
      <label class="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" data-section="${esc(s.id)}" ${(!cfg || cfg[s.id] !== false) ? 'checked' : ''}>
        ${esc(s.label)}
      </label>`).join('');
    popup.classList.remove('hidden');
  });
  document.getElementById('pdf-config-cancel').addEventListener('click', () => popup.classList.add('hidden'));
  popup.addEventListener('click', (e) => { if (e.target === popup) popup.classList.add('hidden'); });
  document.getElementById('pdf-config-save').addEventListener('click', () => {
    const cfg = {};
    document.querySelectorAll('#pdf-config-options input[type=checkbox]').forEach(cb => {
      cfg[cb.dataset.section] = cb.checked;
    });
    localStorage.setItem(PDF_CONFIG_KEY, JSON.stringify(cfg));
    popup.classList.add('hidden');
    exportPdf();
  });
}

document.addEventListener('propsight:themechange', () => {
  refreshThemeColours();
  if (lastData) {
    renderChips();
    renderFileTabs();
    renderTypeTabs();
    renderConfigFilter();
    renderCallStatusFilter();
    renderBuyingStatusFilter();
    renderAll(lastData);
  }
});

function currentQuery() {
  const params = new URLSearchParams();
  if (currentType    !== 'all') params.set('property_type', currentType);
  if (currentSegment !== 'all') params.set('segment', currentSegment);
  if (currentFile    !== 'all') params.set('source_file', currentFile);
  if (budgetMin !== null && budgetMin !== '') params.set('budget_min', budgetMin);
  if (budgetMax !== null && budgetMax !== '') params.set('budget_max', budgetMax);
  if (configFilter.length) params.set('configuration', configFilter.join(','));
  if (callStatusFilter.length) params.set('call_status', callStatusFilter.join(','));
  if (buyingStatusFilter.length) params.set('buying_status', buyingStatusFilter.join(','));
  const q = params.toString();
  return q ? `?${q}` : '';
}

async function reload(clickedBtn) {
  const dash = document.getElementById('dashboard');
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

/* ── segment chips ──
 * Good/Bad appear only once rules classify somebody. There is deliberately no
 * "Unclassified" chip: it is the absence of a verdict, not a kind of lead, and
 * a dashboard filtered to it would chart leads whose fields are empty. */
function renderChips() {
  const chips = [
    { key: 'all',  label: 'All leads' },
    { key: 'hwc',  label: 'HWC-marked only' },
  ];
  if (hasClassification) {
    chips.push({ key: 'good', label: 'Good leads' }, { key: 'bad', label: 'Bad leads' });
  }
  document.getElementById('focus-chips').innerHTML = chips.map(c =>
    `<button data-seg="${c.key}" class="chip-btn px-3 py-1.5 rounded-full text-sm border font-medium ${c.key === currentSegment ? 'tab-active' : ''}"
       style="border-color: var(--border); ${c.key === currentSegment ? '' : 'background: var(--surface-1); color: var(--ink-2);'}">
       ${esc(c.label)}</button>`).join('');
  document.querySelectorAll('.chip-btn').forEach(btn => btn.addEventListener('click', () => {
    currentSegment = btn.dataset.seg;
    renderChips();
    updateFiltersBadge();
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
    updateFiltersBadge();
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
    updateFiltersBadge();
    reload(document.querySelector(`.file-btn[data-file="${CSS.escape(currentFile)}"]`));
  }));
}

/* ================================================================== */
/* Render all cards                                                    */
/* ================================================================== */

function renderAll(d) {
  lastData = d;
  document.getElementById('loading').classList.add('hidden');
  document.getElementById('dashboard').classList.remove('hidden');
  document.getElementById('report-files').textContent = (d.meta.source_files || []).join(' · ');

  const has   = d.availability || {};
  const core  = d.core   || {};
  const counts = core.counts || {};
  const b     = d.budget || {};

  document.getElementById('hero-tiles').innerHTML =
    statTile('Total leads',   num(d.meta.total_leads)) +
    statTile('HWC-marked',    num(counts.hwc ?? 0), pct(counts.hwc_share) + ' of total') +
    statTile('Median budget',
      b.median !== null && b.median !== undefined ? `₹${b.median} Cr` : '—',
      b.parsed_count ? `from ${num(b.parsed_count)} leads with a budget` : '') +
    statTile('Top config',
      d.configuration?.overall?.length ? d.configuration.overall[0].value : '—',
      d.configuration?.overall?.length ? pct(d.configuration.overall[0].share) + ' of those who stated one' : '');

  renderDedupeBanner(d);
  renderDataQuality(d);

  // HWC donut is meaningless when the view is already filtered to HWC-only.
  if (toggleCard('card-hwc-donut', has.hwc && currentSegment !== 'hwc')) {
    setCard('card-hwc-donut', cardTitle('HWC clients',
      'any non-empty value in the HWC column marks a client that matters'));
    donutChart('card-hwc-donut', core.hwc_donut || [], { colors: [PALETTE[5], GRID] });
  }

  renderClassification(d, has);
  renderBudget(d, has);
  renderConfiguration(d, has);
  renderCallStatus(d, has);
  renderBuyingStatus(d, has);
  renderTime(d, has);
  renderMarketing(d, has);
  renderCrosstabs(d, has);
}

/* ── lead classification ── */
function renderClassification(d, has) {
  const section = document.getElementById('classification-section');
  if (section) section.classList.toggle('hidden', !has.classification);
  if (!has.classification) return;

  const s = d.classification || {};

  if (toggleCard('card-lead-class', true)) {
    setCard('card-lead-class', cardTitle('Good vs bad leads',
      'as decided by your categorization rules'));
    donutChart('card-lead-class', (d.lead_class.distribution || []).map(x => ({
      label: x.value, count: x.count,
    })), {
      // Green is Good whether or not Good is the bigger slice.
      colorMap: { Good: PALETTE[3], Bad: PALETTE[5] },
      basis: 'classified leads',
      // The unclassified remainder is stated, not plotted: it is the absence of
      // a verdict and cannot be compared with Good or Bad.
      note: s.unclassified
        ? `${num(s.unclassified)} more leads matched no rule — see the sorter for why.`
        : '',
    });
  }
}

function setCard(id, html) { document.getElementById(id).innerHTML = html; }

/* ── dedupe banner ── */
function renderDedupeBanner(d) {
  const s = d.dedupe;
  const el = document.getElementById('dedupe-banner');
  if (!el) return;
  if (!s || !(s.duplicate_rows_merged || s.invalid_phone_dropped)) {
    el.classList.add('hidden');
    return;
  }
  el.classList.remove('hidden');
  const bits = [];
  if (s.duplicate_rows_merged)
    bits.push(`<b>${num(s.duplicate_rows_merged)}</b> duplicate rows merged by mobile number`);
  if (s.invalid_phone_dropped)
    bits.push(`<b>${num(s.invalid_phone_dropped)}</b> rows dropped for an invalid mobile number`);
  if (s.missing_phone_dropped)
    bits.push(`<b>${num(s.missing_phone_dropped)}</b> rows dropped for having no mobile number`);
  if (s.cross_file_leads)
    bits.push(`<b>${num(s.cross_file_leads)}</b> leads found in more than one file`);
  el.innerHTML = `<span>Cleaned <b>${num(s.input_rows)}</b> uploaded rows into
    <b>${num(s.unique_leads)}</b> unique leads — ${bits.join(' · ')}.</span>`;
}

/* ── data quality ── */
function renderDataQuality(d) {
  const q = d.data_quality;
  if (!toggleCard('card-data-quality', !!(q && q.fields && q.fields.length))) return;

  // Sorted worst-first: the empty fields are the ones that explain thin charts.
  const rows = [...q.fields].sort((a, b) => a.fill_rate - b.fill_rate).map(f => `
    <div class="flex items-center gap-2 sm:gap-3 text-sm">
      <div class="w-32 sm:w-44 truncate text-right shrink-0" style="color: var(--ink-2);">${esc(f.field.replace(/_/g, ' '))}</div>
      <div class="flex-1 bar-track" style="background: var(--grid); border-radius: 4px;">
        <div class="bar-fill" style="width:${Math.max(1, f.fill_rate * 100)}%; background:${f.fill_rate < 0.5 ? PALETTE[5] : PALETTE[1]};"></div>
      </div>
      <div class="w-24 sm:w-32 text-xs shrink-0 tabular-nums" style="color: var(--ink-2);">
        ${num(f.filled)} <span style="color: var(--muted);">· ${pct(f.fill_rate)}</span>
      </div>
    </div>`).join('');

  setCard('card-data-quality',
    cardTitle('Data completeness',
      'how many leads actually have each field filled in — a chart can only use leads that have the fields it plots') +
    `<div class="space-y-2.5">${rows}</div>` +
    (q.merged_leads ? `<p class="text-xs mt-3" style="color: var(--muted);">
      ${num(q.merged_leads)} leads were assembled from ${num(q.rows_absorbed)} additional duplicate rows.</p>` : ''));
}

/* ── budget ── */
function renderBudget(d, has) {
  if (!toggleCard('card-budget', has.budget)) return;
  const b = d.budget;
  const note = b.median !== undefined
    ? `median ₹${b.median} Cr · mean ₹${b.mean} Cr · range ₹${b.min}–${b.max} Cr · parsed ${num(b.parsed_count)}/${num(b.total_rows)} leads`
    : '';
  setCard('card-budget', cardTitle('Budget distribution', `fixed Crore bands · values in ${b.unit || 'Crore'}`));
  donutChart('card-budget', b.buckets.map(x => ({ label: x.label, count: x.count })),
    { note, basis: 'leads with a stated budget' });
}

/* ── configuration ── */
function renderConfiguration(d, has) {
  if (!toggleCard('card-configuration', has.configuration)) return;
  setCard('card-configuration', cardTitle('Configuration required', 'BHK / unit type demanded by leads'));
  donutChart('card-configuration', d.configuration.overall.map(c => ({ label: c.value, count: c.count })),
    { basis: 'leads with a configuration recorded' });
}

/* ── call status ── */
function renderCallStatus(d, has) {
  if (!toggleCard('card-call-status', has.call_status)) return;
  setCard('card-call-status', cardTitle('Call status', 'distribution of Latest Call Status values'));
  donutChart('card-call-status', d.call_status.distribution.map(x => ({ label: x.value, count: x.count })),
    { basis: 'leads with a call status recorded' });
}

/* ── buying status ── */
function renderBuyingStatus(d, has) {
  if (!toggleCard('card-buying-status', has.buying_status)) return;
  setCard('card-buying-status', cardTitle('Buying status', 'distribution of Buying Status values'));
  donutChart('card-buying-status', d.buying_status.distribution.map(x => ({ label: x.value, count: x.count })),
    { basis: 'leads with a buying status recorded' });
}

/* ── lead timing ── */
function renderTime(d, has) {
  const t = d.time || {};
  const section = document.getElementById('time-section');
  if (section) section.classList.toggle('hidden', !has.time);
  if (!has.time) return;

  if (toggleCard('card-time-weekday', true)) {
    const peak = t.peak || {};
    setCard('card-time-weekday', cardTitle('Leads by day of week',
      `${num(t.dated_leads)} of ${num(t.total_leads)} leads carry a date`));
    donutChart('card-time-weekday', t.by_weekday || [], {
      basis: 'leads with a usable date',
      note: peak.weekday ? `Busiest day: ${peak.weekday} (${num(peak.weekday_count)} leads).` : '',
    });
  }

  // Hour-of-day exists only where a timestamp carries a clock time. Legacy sheets
  // store a bare date; charting those as midnight would fabricate a 00:00 spike.
  // When there aren't enough timed leads yet, this card explains why the space
  // beside the weekday chart isn't a chart, instead of just staying blank.
  if (!has.time_of_day && toggleCard('card-time-hour', true)) {
    setCard('card-time-hour', cardTitle('Leads by hour of day', 'not enough leads carry a clock time yet'));
    document.getElementById('card-time-hour').insertAdjacentHTML('beforeend', `
      <p class="text-sm" style="color: var(--ink-2);">
        Only <b>${num(t.timed_leads)}</b> of <b>${num(t.dated_leads)}</b> dated leads carry an actual time of
        day — the rest record just a date. At least <b>${num(t.min_timed_leads ?? 30)}</b> timed leads are
        needed before an hourly chart would mean anything.
      </p>`);
  } else if (toggleCard('card-time-hour', has.time_of_day)) {
    const peak = t.peak || {};
    setCard('card-time-hour', cardTitle('Leads by hour of day',
      `${num(t.timed_leads)} leads carry a time of day`));
    const el = document.getElementById('card-time-hour');
    const canvasId = 'card-time-hour-canvas';
    el.insertAdjacentHTML('beforeend', `<div style="height:220px"><canvas id="${canvasId}"></canvas></div>
      ${peak.hour_label ? `<p class="text-xs mt-3" style="color: var(--muted);">Peak arrival: ${esc(peak.hour_label)} (${num(peak.hour_count)} leads).</p>` : ''}`);
    mountChart(canvasId, {
      type: 'bar',
      data: {
        labels: (t.by_hour || []).map(h => h.label),
        // One series, one colour — never a value-ramp on the bars.
        datasets: [{ label: 'Leads', data: (t.by_hour || []).map(h => h.count),
                     backgroundColor: PALETTE[0], borderRadius: 2, borderColor: SURFACE, borderWidth: 1 }],
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          x: { grid: { display: false }, ticks: { color: MUTED, maxRotation: 0, autoSkipPadding: 12 } },
          y: { grid: { color: GRID }, ticks: { color: MUTED, precision: 0 } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false, external: externalTooltip },
        },
      },
    });
  }

  if (toggleCard('card-time-heatmap', has.time_of_day && !!t.weekday_x_hour)) {
    setCard('card-time-heatmap', cardTitle('When leads arrive — day × hour',
      'darker means more leads landed in that hour; use it to staff the phones'));
    heatmap('card-time-heatmap', t.weekday_x_hour);
  }

  if (toggleCard('card-time-month', !!(t.by_month && t.by_month.length > 1))) {
    setCard('card-time-month', cardTitle('Lead volume over time', 'leads created per month'));
    const canvasId = 'card-time-month-canvas';
    document.getElementById('card-time-month').insertAdjacentHTML('beforeend',
      `<div style="height:220px"><canvas id="${canvasId}"></canvas></div>`);
    mountChart(canvasId, {
      type: 'line',
      data: {
        labels: t.by_month.map(m => m.label),
        datasets: [{ label: 'Leads', data: t.by_month.map(m => m.count),
                     borderColor: PALETTE[0], backgroundColor: PALETTE[0],
                     borderWidth: 2, pointRadius: 3, tension: 0.25 }],
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          x: { grid: { display: false }, ticks: { color: MUTED, maxRotation: 0, autoSkipPadding: 16 } },
          y: { grid: { color: GRID }, ticks: { color: MUTED, precision: 0 }, beginAtZero: true },
        },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false, external: externalTooltip },
        },
      },
    });
  }
}

/* ── marketing ── */
function renderMarketing(d, has) {
  const section = document.getElementById('marketing-section');
  if (section) section.classList.toggle('hidden', !has.marketing);
  if (!has.marketing) return;
  const m = d.marketing;

  // Page / campaign / ad-set names are long, so these three stack their legends.
  const cards = [
    ['card-marketing-pages', has.marketing_pages, 'Facebook pages', 'distribution of leads by Page', m.pages, 'leads attributed to a page'],
    ['card-marketing-campaigns', has.marketing_campaigns, 'Campaigns', 'distribution of leads by Campaign', m.campaigns, 'leads attributed to a campaign'],
    ['card-marketing-adsets', has.marketing_ad_sets, 'Facebook ad sets', 'distribution of leads by Ad Set', m.ad_sets, 'leads attributed to an ad set'],
  ];
  for (const [id, visible, title, subtitle, data, basis] of cards) {
    if (!toggleCard(id, visible)) continue;
    setCard(id, cardTitle(title, subtitle));
    donutChart(id, data.map(x => ({ label: x.value, count: x.count })), { stackLegend: true, basis });
  }
}

/* ── cross-tabs ── */
const CROSSTABS = [
  ['card-class-budget',   'lead_class_x_budget',    'Lead class × budget bucket',     'do your good leads carry bigger budgets?'],
  ['card-class-config',   'lead_class_x_config',    'Lead class × configuration',     'what do your good leads actually want?'],
  ['card-class-call',     'lead_class_x_call_status', 'Lead class × call status',     'are the good leads the ones you reach?'],
  ['card-config-budget',  'config_x_budget',        'Configuration × budget bucket',  'which BHK/unit types sit in which price bands'],
  ['card-hwc-config',     'hwc_x_config',           'HWC-flagged × configuration',    'what priority clients are specifically asking for'],
  ['card-hwc-budget',     'hwc_x_budget',           'HWC-flagged × budget bucket',    'do priority clients sit in higher price bands?'],
  ['card-buying-config',  'buying_status_x_config', 'Buying status × configuration',  'do warmer buyers cluster in specific unit types?'],
  ['card-call-buying',    'call_status_x_buying',   'Call status × buying status',    'are unreachable / busy leads actually warm buyers?'],
];

function renderCrosstabs(d, has) {
  for (const [cardId, key, title, subtitle] of CROSSTABS) {
    if (!toggleCard(cardId, has[key])) continue;
    setCard(cardId, cardTitle(title, subtitle));
    matrixChart(cardId, d[key], { summary: chartSummaries[key] });
  }
}

/* Summaries arrive after the first paint — the LLM call takes seconds and must
 * never block the charts. Re-render only the crosstab captions when they land. */
async function loadChartSummaries() {
  try {
    const { summaries } = await fetchJSON(`/reports/${window.REPORT_ID}/chart-summaries`);
    chartSummaries = summaries || {};
  } catch {
    chartSummaries = {};
    return;
  }
  for (const [cardId, key] of CROSSTABS.map(c => [c[0], c[1]])) {
    const summary = chartSummaries[key];
    const card = document.getElementById(cardId);
    if (!summary || !card || card.closest('.card').classList.contains('hidden')) continue;
    let node = card.querySelector('.chart-summary');
    if (!node) {
      node = document.createElement('p');
      node.className = 'text-sm mb-3 chart-summary';
      card.querySelector('label').before(node);
    }
    node.textContent = summary;
  }
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
      const subtitle = data.retryable ? 'temporarily unavailable' : 'optional — set GROQ_API_KEY in .env to enable';
      el.innerHTML = cardTitle('AI insights', subtitle) +
        `<p class="text-sm py-2" style="color: var(--muted);">${esc(data.error)}</p>` +
        (data.retryable ? `<button id="insights-retry" class="btn-secondary h-8 px-3 text-label-xs hide-on-print">Retry</button>` : '');
      if (data.retryable) {
        document.getElementById('insights-retry').addEventListener('click', loadInsights);
      }
      return;
    }

    const highlight = (text) => {
      let safe = esc(text);
      safe = safe.replace(/\b\d+(\.\d+)?\s*(BHK|RK)\b/gi, '<span class="hl hl-config">$&</span>');
      safe = safe.replace(/(?:(?:₹|Rs\.?)\s*)?(?:\d+(?:\.\d+)?\s*(?:-|to|–)\s*)?\d+(?:\.\d+)?\s*(?:Cr|Lakhs?|L|K|crore)\b/gi, '<span class="hl hl-money">$&</span>');
      safe = safe.replace(/\b\d+(\.\d+)?%/g, '<span class="hl hl-pct">$&</span>');
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
