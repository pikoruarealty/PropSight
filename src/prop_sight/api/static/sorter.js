/* PropSight — lead sorter.
 *
 * Rules bind to canonical fields, never to raw spreadsheet headers, so one rule
 * set classifies every export format. Field values come from the server already
 * folded (case variants merged, synonyms resolved), which is why a rule listing
 * "Bungalow" also matches a lead recorded as "Bungalow only".
 *
 * Depends on app.js for esc / fetchJSON / num / withBtnLoading.
 */

let FIELDS = [];         // [{field, label, values, fill_rate, from_notes}]
let OPERATORS = {};      // op -> human phrase
let rules = [];          // [{conditions: [{field, op, values}], category}]
let exportColumns = [];  // columns the user ticked

const VALUELESS_OPS = new Set(['is_empty', 'is_not_empty']);

const fieldByName = (name) => FIELDS.find(f => f.field === name);

// Spreadsheet-style column letters (A, B, ... Z, AA, AB, ...) purely as a
// faded visual anchor beside the field name — simpler at a glance than a
// fill-rate percentage, and familiar from Excel.
function colLetter(index) {
  let n = index, s = '';
  do {
    s = String.fromCharCode(65 + (n % 26)) + s;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return s;
}

/* ── init ── */
async function initSorter() {
  try {
    const [fieldsRes, rulesRes] = await Promise.all([
      fetchJSON(`/api/reports/${window.REPORT_ID}/fields`),
      fetchJSON('/api/rules'),
    ]);
    FIELDS = fieldsRes.fields || [];
    OPERATORS = rulesRes.operators || {};
    rules = rulesRes.rules || [];

    document.getElementById('loading').classList.add('hidden');
    document.getElementById('sorter').classList.remove('hidden');

    renderRules();
    wireButtons();
    await refreshClassification();
  } catch (err) {
    document.getElementById('loading').textContent = '⚠ ' + err.message;
  }
}

/* ── classification summary + preview ── */
async function refreshClassification() {
  const data = await fetchJSON(`/api/reports/${window.REPORT_ID}/classification`);
  const s = data.summary || {};

  document.getElementById('class-tiles').innerHTML =
    statTile('Total leads', num(s.total)) +
    statTile('Good', num(s.good), s.total ? pct(s.good / s.total) + ' of leads' : '') +
    statTile('Bad', num(s.bad), s.total ? pct(s.bad / s.total) + ' of leads' : '') +
    statTile('Unclassified', num(s.unclassified), `${data.rule_count} rule${data.rule_count === 1 ? '' : 's'} defined`);

  renderUnclassified(s, data.rule_count);

  if (!exportColumns.length) exportColumns = (data.columns || []).slice();
  renderColumns(data.columns || []);
  renderDownloads(s);
}

function renderUnclassified(s, ruleCount) {
  const el = document.getElementById('card-unclassified');
  if (!s.unclassified) {
    el.classList.add('hidden');
    return;
  }
  el.classList.remove('hidden');

  // The reason is the point: almost always the rule fields are simply empty for
  // that lead, which is a data problem, not a rule problem.
  const rows = (s.unclassified_reasons || []).map(r => `
    <div class="flex items-center gap-3 text-sm">
      <div class="flex-1 min-w-0" style="color: var(--ink-2);">${esc(r.reason)}</div>
      <b class="tabular-nums shrink-0">${num(r.count)}</b>
      <span class="text-xs tabular-nums shrink-0" style="color: var(--muted);">${(r.count / s.unclassified * 100).toFixed(0)}%</span>
    </div>`).join('');

  el.innerHTML = cardTitle('Why leads are unclassified',
    ruleCount
      ? `${num(s.unclassified)} of ${num(s.total)} leads matched no rule`
      : 'no rules are defined yet, so nothing can be classified') +
    `<div class="space-y-2.5">${rows}</div>` +
    `<p class="text-xs mt-4" style="color: var(--muted);">
       Unclassified is a statement about the rules, not about the lead. It is left out of the
       dashboard's charts on purpose — comparing it against Good and Bad would mean nothing.
     </p>`;
}

/* ── export column picker ── */
function renderColumns(columns) {
  const grid = document.getElementById('columns-grid');
  grid.innerHTML = columns.map(c => `
    <label class="flex items-center gap-2 text-xs" style="color: var(--ink-2);">
      <input type="checkbox" class="col-cb" value="${esc(c)}" ${exportColumns.includes(c) ? 'checked' : ''}>
      <span class="truncate" title="${esc(c)}">${esc(c)}</span>
    </label>`).join('');

  grid.querySelectorAll('.col-cb').forEach(cb => cb.addEventListener('change', () => {
    exportColumns = [...grid.querySelectorAll('.col-cb:checked')].map(x => x.value);
    document.getElementById('col-count').textContent = exportColumns.length;
    renderDownloads();
  }));
  document.getElementById('col-count').textContent = exportColumns.length;
}

let lastSummary = {};
function renderDownloads(summary) {
  if (summary) lastSummary = summary;
  const s = lastSummary;
  const q = exportColumns.length ? `?columns=${encodeURIComponent(exportColumns.join(','))}` : '';
  const base = `/api/reports/${window.REPORT_ID}/export`;

  // Tinted background + solid-colour text, not a solid fill with white text:
  // --success/--tertiary/etc flip to pastel in dark mode (they're chart-series
  // colours, not button fills), so white text on them was unreadable. Text in
  // the token's own colour stays legible against a low-opacity tint either way.
  const btn = (href, label, count, tokenVar) => count
    ? `<a href="${href}" download class="px-3 py-1.5 rounded-lg text-sm font-semibold"
         style="background: rgb(var(${tokenVar}) / 0.15); color: rgb(var(${tokenVar}));">${label} (${num(count)})</a>`
    : `<span class="px-3 py-1.5 rounded-lg text-sm" style="background: var(--page); color: var(--muted);">${label} (0)</span>`;

  document.getElementById('download-buttons').innerHTML =
    btn(`${base}/good.xlsx${q}`, '⬇ Good leads', s.good, '--success') +
    btn(`${base}/bad.xlsx${q}`, '⬇ Bad leads', s.bad, '--error') +
    btn(`${base}/unclassified.xlsx${q}`, '⬇ Unclassified', s.unclassified, '--tertiary') +
    btn(`${base}/meta-audience.csv`, '⬇ Meta audience CSV', s.good, '--primary');
}

/* ── rule editor ── */
function renderRules() {
  const list = document.getElementById('rules-list');
  if (!rules.length) {
    list.innerHTML = `<p class="text-sm" style="color: var(--muted);">
      No rules yet. Every lead is unclassified until you add one.</p>`;
    return;
  }
  list.innerHTML = rules.map((rule, ri) => ruleCard(rule, ri)).join('');
  wireRuleCard();
}

const FIELD_SELECT_CLS = 'h-10 px-3 rounded-lg text-body-sm w-full border border-outline-variant ' +
  'bg-surface-container-lowest text-on-surface focus:outline-none focus:border-primary ' +
  'focus:ring-2 focus:ring-primary/20';

function ruleCard(rule, ri) {
  const conditions = rule.conditions.map((c, ci) => conditionRow(c, ri, ci)).join('');
  const isGood = rule.category === 'good';
  return `<div class="rounded-xl border border-outline-variant bg-surface-container-low p-4" data-rule="${ri}">
    <div class="flex flex-wrap items-center gap-3 mb-4">
      <span class="text-body-md font-bold text-on-surface">Rule ${ri + 1}</span>
      <span class="text-body-sm text-on-surface-variant">classify as</span>
      <select class="rule-cat h-8 pl-3 pr-7 rounded-full text-label-xs font-bold border-0 cursor-pointer appearance-none
                     ${isGood ? 'bg-success/15 text-success' : 'bg-error/15 text-error'}" data-rule="${ri}">
        <option value="good" ${isGood ? 'selected' : ''}>Good lead</option>
        <option value="bad"  ${!isGood ? 'selected' : ''}>Bad lead</option>
      </select>
      <button class="rule-del ml-auto h-8 px-3 rounded-lg text-label-xs font-semibold text-error
                     hover:bg-error/10 flex items-center gap-1" data-rule="${ri}">
        <span class="material-symbols-outlined" style="font-size:16px;">delete</span> Delete rule
      </button>
    </div>
    <div class="space-y-2.5">${conditions}</div>
    <button class="cond-add mt-3 h-8 px-3 rounded-lg text-label-xs font-semibold text-primary
                   hover:bg-primary/10 flex items-center gap-1" data-rule="${ri}">
      <span class="material-symbols-outlined" style="font-size:16px;">add</span> Add condition (AND)
    </button>
  </div>`;
}

function conditionRow(cond, ri, ci) {
  const field = fieldByName(cond.field);
  const opts = FIELDS.map((f, i) =>
    `<option value="${esc(f.field)}" ${f.field === cond.field ? 'selected' : ''}>${colLetter(i)} — ${esc(f.label)}</option>`).join('');
  const ops = Object.entries(OPERATORS).map(([k, v]) =>
    `<option value="${k}" ${k === cond.op ? 'selected' : ''}>${esc(v)}</option>`).join('');

  const needsValues = !VALUELESS_OPS.has(cond.op);
  const values = field
    ? field.values.map(v => `
        <label class="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-surface-container cursor-pointer text-body-sm text-on-surface">
          <input type="checkbox" class="cond-value-cb h-4 w-4 rounded border-outline text-primary focus:ring-primary/40"
                 value="${esc(v)}" ${cond.values.includes(v) ? 'checked' : ''}>
          <span class="truncate">${esc(v)}</span>
        </label>`).join('')
    : '';

  return `<div class="grid md:grid-cols-[minmax(0,1fr)_minmax(0,0.85fr)_minmax(0,1.4fr)_auto] gap-2.5 items-start
              bg-surface-container-lowest rounded-lg border border-outline-variant p-3" data-rule="${ri}" data-cond="${ci}">
    <select class="cond-field ${FIELD_SELECT_CLS}">
      <option value="">— field —</option>${opts}
    </select>
    <select class="cond-op ${FIELD_SELECT_CLS}">${ops}</select>
    <div class="min-w-0">
      ${needsValues
        ? `<div class="cond-values border border-outline-variant rounded-lg w-full max-h-36 overflow-y-auto bg-surface-container-lowest p-1">
             ${values || `<p class="text-body-sm text-on-surface-variant px-2 py-1.5">pick a field first</p>`}
           </div>
           ${field ? `<p class="text-label-xs text-on-surface-variant mt-1">${field.values.length} distinct</p>` : ''}`
        : `<p class="text-body-sm text-on-surface-variant py-2.5">no values needed</p>`}
    </div>
    <button class="cond-del h-10 w-10 flex items-center justify-center rounded-lg border border-outline-variant
                   text-error hover:bg-error/10 shrink-0" title="Remove condition">
      <span class="material-symbols-outlined" style="font-size:18px;">close</span>
    </button>
  </div>`;
}

function wireRuleCard() {
  const list = document.getElementById('rules-list');

  list.querySelectorAll('.rule-cat').forEach(sel => sel.addEventListener('change', () => {
    rules[+sel.dataset.rule].category = sel.value;
    const isGood = sel.value === 'good';
    sel.classList.toggle('bg-success/15', isGood);
    sel.classList.toggle('text-success', isGood);
    sel.classList.toggle('bg-error/15', !isGood);
    sel.classList.toggle('text-error', !isGood);
  }));
  list.querySelectorAll('.rule-del').forEach(btn => btn.addEventListener('click', () => {
    rules.splice(+btn.dataset.rule, 1);
    renderRules();
  }));
  list.querySelectorAll('.cond-add').forEach(btn => btn.addEventListener('click', () => {
    rules[+btn.dataset.rule].conditions.push({ field: '', op: 'is', values: [] });
    renderRules();
  }));

  list.querySelectorAll('[data-cond]').forEach(row => {
    const ri = +row.dataset.rule, ci = +row.dataset.cond;
    const cond = rules[ri].conditions[ci];

    row.querySelector('.cond-field').addEventListener('change', e => {
      cond.field = e.target.value;
      cond.values = [];   // the old values belong to the old field
      renderRules();
    });
    row.querySelector('.cond-op').addEventListener('change', e => {
      cond.op = e.target.value;
      if (VALUELESS_OPS.has(cond.op)) cond.values = [];
      renderRules();
    });
    row.querySelectorAll('.cond-value-cb').forEach(cb => cb.addEventListener('change', () => {
      cond.values = [...row.querySelectorAll('.cond-value-cb:checked')].map(x => x.value);
    }));
    row.querySelector('.cond-del').addEventListener('click', () => {
      rules[ri].conditions.splice(ci, 1);
      if (!rules[ri].conditions.length) rules.splice(ri, 1);
      renderRules();
    });
  });
}

/* ── toolbar ── */
function wireButtons() {
  document.getElementById('btn-add-rule').addEventListener('click', () => {
    rules.push({ conditions: [{ field: '', op: 'is', values: [] }], category: 'good' });
    renderRules();
  });

  document.getElementById('btn-save').addEventListener('click', async (e) => {
    const status = document.getElementById('save-status');
    status.classList.remove('hidden');
    await withBtnLoading(e.currentTarget, 'Applying…', async () => {
      try {
        const res = await fetch('/api/rules', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rules }),
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
        const saved = await res.json();
        rules = saved.rules;
        renderRules();
        await refreshClassification();
        status.textContent = `✓ Saved ${saved.count} rule${saved.count === 1 ? '' : 's'}. The dashboard now reflects them.`;
        status.style.color = 'var(--good)';
      } catch (err) {
        status.textContent = '⚠ ' + err.message;
        status.style.color = 'var(--critical)';
      }
    });
  });

  document.getElementById('btn-export').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(rules, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'categorization_rules.json';
    a.click();
    URL.revokeObjectURL(a.href);
  });

  const input = document.getElementById('import-input');
  document.getElementById('btn-import').addEventListener('click', () => input.click());
  input.addEventListener('change', async () => {
    const file = input.files[0];
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      if (!Array.isArray(parsed)) throw new Error('Expected a JSON array of rules.');
      rules = parsed.map(normalizeImportedRule);
      renderRules();
    } catch (err) {
      alert('Could not import rules: ' + err.message);
    }
    input.value = '';
  });

  document.getElementById('btn-cols-all').addEventListener('click', () => {
    document.querySelectorAll('.col-cb').forEach(cb => { cb.checked = true; });
    exportColumns = [...document.querySelectorAll('.col-cb')].map(cb => cb.value);
    document.getElementById('col-count').textContent = exportColumns.length;
    renderDownloads();
  });
  document.getElementById('btn-cols-none').addEventListener('click', () => {
    document.querySelectorAll('.col-cb').forEach(cb => { cb.checked = false; });
    exportColumns = [];
    document.getElementById('col-count').textContent = 0;
    renderDownloads();
  });
}

/* The standalone sorter stored a rule as a flat {col, vals, category}. Accept it
 * so an existing rules file can be imported, and drop 'unclassified' rules —
 * unclassified is now the absence of a match, not something you assign. */
function normalizeImportedRule(rule) {
  const category = rule.category === 'bad' ? 'bad' : 'good';
  if (rule.col) return { conditions: [{ field: rule.col, op: 'is', values: rule.vals || [] }], category };
  const conditions = (rule.conditions || []).map(c => ({
    field: c.field || c.col || '',
    op: c.op || 'is',
    values: c.values || c.vals || [],
  }));
  return { conditions, category };
}
