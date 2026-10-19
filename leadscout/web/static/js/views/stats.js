// Аналитика: точность подбора, деньги, когда приходят заказы, путь заказа, чему научился бот.

import { post } from '../api.js';
import { S, loadProfile, loadStats } from '../state.js';
import { $, html, I, paint, plural, raw, toast } from '../ui.js';
import { refreshFeed } from './feed.js';

const MONO = 'JetBrains Mono, monospace';

export async function renderStats() {
  const view = $('#view');
  if (!S.stats) paint(view, html`<section class="view"><div class="skeleton" style="height:240px;margin-top:14px"></div><div class="skeleton" style="height:140px;margin-top:12px"></div></section>`);
  try { await loadStats(); } catch (e) { toast(e.message); if (!S.stats) return; }
  if (S.tab !== 'stats') return; // пока грузилось, ушли на другую вкладку
  const st = S.stats;
  const acc = st.accuracy;
  const series = acc.series.filter((p) => p.value !== null);
  const delta = series.length >= 2 ? series[series.length - 1].value - series[0].value : null;
  const needed = Math.max(0, 5 - acc.n);

  paint(view, html`<section class="view">
    ${st.suggestions?.length ? html`<div class="sect" style="margin-top:14px">${st.suggestions.map((s) => html`<div class="glass sugg">${I.spark}<div><b>Бот предлагает</b><p>${s.text}</p>
      <div class="btnrow"><button class="btn small" type="button" data-act="sugg-no" data-id="${s.id}">Не сейчас</button><button class="btn pri small" type="button" data-act="sugg-yes" data-id="${s.id}">Принять</button></div></div></div>`)}</div>` : ''}
    <div class="sect" style="margin-top:${st.suggestions?.length ? 12 : 14}px">
      <div class="glass">
        <div class="mono">точность подбора</div>
        <div class="big">${acc.current === null ? '—' : html`${acc.current}<small>%</small>`}</div>
        ${acc.current === null
          ? html`<p class="capnote">Здесь появится ваша точность: сколько заказов, которые я назвал подходящими, вы отметили 👍. ${needed ? `Нужно ещё ${needed} ${plural(needed, 'оценка', 'оценки', 'оценок')}.` : ''}</p><div class="meter"><i style="width:${Math.min(100, acc.n * 20)}%"></i></div>`
          : html`${delta !== null ? html`<div class="delta${delta < 0 ? ' neg' : ''}">${I.trend}${delta >= 0 ? '+' : ''}${delta} п.п. за ${series.length} ${plural(series.length, 'неделю', 'недели', 'недель')}</div>` : html`<p class="capnote">По ${acc.n} вашим оценкам.</p>`}<div class="chart" id="c-acc"></div>`}
      </div>
    </div>
    <div class="sect"><div class="tile-grid">${st.tiles.map(tile)}</div></div>
    <div class="sect"><h2>Отобрано по дням <span class="mono">последние 7</span></h2><div class="glass"><div class="chart" id="c-days"></div></div></div>
    <div class="sect"><h2>Когда появляются заказы <span class="mono">за 30 дней</span></h2><div class="glass">
      ${st.hours.total >= 8 ? html`<div class="hours" id="hours"></div><div class="hrs mono"><span>0</span><span>6</span><span>12</span><span>18</span><span>23</span></div>
        ${st.hours.top.length ? html`<p class="insight">${I.spark}<span>Чаще всего заказы приходят в ${hoursText(st.hours.top)}. В это время лучше быть на связи.</span></p>` : ''}`
        : html`<p class="capnote">Нужно хотя бы 8 найденных заказов, чтобы показать, в какие часы их публикуют.</p>`}</div></div>
    <div class="sect"><h2>Путь за неделю</h2><div class="glass"><div class="rows">${funnel(st.funnel)}</div></div></div>
    <div class="sect"><h2>Откуда лучшие заказы <span class="mono">доля отобранных</span></h2><div class="glass"><div class="rows">${st.chats.length ? st.chats.map((c) => html`<div class="brow"><span class="lab">${c.title}${c.enabled ? '' : html` <span class="mute">выкл.</span>`}</span><span class="tr"><i style="width:${Math.max(c.share ? 3 : 0, c.share)}%"></i></span><span class="val">${c.share}%</span></div>`) : html`<p class="capnote">Чаты ещё не добавлены.</p>`}</div></div></div>
    <div class="sect"><h2>Как учится бот <span class="mono">${st.learned} ${plural(st.learned, 'оценка', 'оценки', 'оценок')}</span></h2><div class="glass"><ul class="log">${st.log.map((l) => html`<li><span class="mono">${l.when}</span><span>${l.text}</span></li>`)}</ul></div></div>
    ${st.usage ? html`<p class="capnote center" style="margin-top:18px">Расход на ИИ в этом месяце ≈ $${(st.usage.usd || 0).toFixed(2)} · ${st.usage.calls} ${plural(st.usage.calls, 'запрос', 'запроса', 'запросов')}</p>` : ''}
  </section>`);
  if (acc.current !== null) lineChart($('#c-acc'), acc.series);
  dayChart($('#c-days'), st.days);
  if (st.hours.total >= 8) hoursChart($('#hours'), st.hours);
}

const hoursText = (top) => [...top].sort((a, b) => a - b).map((h) => `${h}:00`).join(', ');

function funnel(rows) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return rows.map((r) => html`<div class="brow"><span class="lab">${r.label}</span><span class="tr"><i style="width:${r.value ? Math.max(3, (r.value / max) * 100) : 0}%"></i></span><span class="val">${r.value}</span></div>`);
}

function tile(t) {
  return html`<div class="glass tile"><div class="mono">${t.label}</div><div class="v">${t.value}</div><div class="d">${t.note}</div>${spark(t.spark)}</div>`;
}

function spark(vals) {
  const W = 100;
  const H = 30;
  const mx = Math.max(...vals);
  const mn = Math.min(...vals);
  if (mx === mn) return raw(`<svg viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><path d="M0 22 L100 22" fill="none" stroke="rgba(185,182,210,.3)" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`);
  const pts = vals.map((v, i) => [(i * W) / (vals.length - 1), 4 + (H - 8) * (1 - (v - mn) / (mx - mn))]);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  return raw(`<svg viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><path d="${d}" fill="none" stroke="rgba(185,182,210,.55)" stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`);
}

// ---------------------------------------------------------------- графики ----
let tip;
function showTip(host, x, y, text) {
  if (!tip) { tip = document.createElement('div'); tip.className = 'tip'; }
  host.appendChild(tip);
  tip.innerHTML = text;
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
  tip.hidden = false;
}
const hideTip = () => { if (tip) tip.hidden = true; };

function lineChart(host, series) {
  const W = 320; const H = 150; const pl = 30; const pr = 14; const pt = 16; const pb = 24;
  const n = series.length;
  const X = (i) => pl + ((W - pl - pr) * i) / Math.max(1, n - 1);
  const known = series.filter((p) => p.value !== null).map((p) => p.value);
  const lo = Math.max(0, Math.min(40, Math.floor(Math.min(...known, 40) / 10) * 10)); // низкая точность не уезжает за график
  const Y = (v) => pt + (H - pt - pb) * (1 - (v - lo) / (100 - lo));
  const pts = series.map((p, i) => (p.value === null ? null : [X(i), Y(p.value), p]));
  const segs = [];
  let cur = [];
  pts.forEach((p) => { if (p) cur.push(p); else if (cur.length) { segs.push(cur); cur = []; } });
  if (cur.length) segs.push(cur);
  const path = segs.map((s) => s.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')).join(' ');
  const area = segs.filter((s) => s.length > 1).map((s) => `M${s[0][0]} ${H - pb} ${s.map((p) => `L${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')} L${s[s.length - 1][0]} ${H - pb} Z`).join(' ');
  const grid = [Math.round((lo + 100) / 2), 100].map((g) => `<line x1="${pl}" x2="${W - pr}" y1="${Y(g)}" y2="${Y(g)}" stroke="rgba(255,255,255,.08)"/><text x="${pl - 6}" y="${Y(g) + 3.5}" text-anchor="end" fill="#8582A6" font-family="${MONO}" font-size="9.5">${g}</text>`).join('');
  const xl = series.map((p, i) => `<text x="${X(i)}" y="${H - 6}" text-anchor="middle" fill="#8582A6" font-family="${MONO}" font-size="9.5">нед ${p.week}</text>`).join('');
  const last = pts.filter(Boolean).pop();
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Точность подбора по неделям"><defs><linearGradient id="gl" x1="0" x2="1"><stop offset="0" stop-color="#8E7BFF"/><stop offset="1" stop-color="#4FD8E8"/></linearGradient><linearGradient id="ga" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8E7BFF" stop-opacity=".30"/><stop offset="1" stop-color="#8E7BFF" stop-opacity="0"/></linearGradient></defs>${grid}${xl}` +
    (area ? `<path d="${area}" fill="url(#ga)"/>` : '') + `<path d="${path}" fill="none" stroke="url(#gl)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>` +
    pts.filter(Boolean).map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="3.2" fill="#4FD8E8" stroke="#15132B" stroke-width="2"/>`).join('') +
    (last ? `<text x="${last[0] - 8}" y="${last[1] - 10}" text-anchor="end" fill="#F2F1FA" font-family="Onest, sans-serif" font-size="12" font-weight="500">${last[2].value}%</text>` : '') + '</svg>';
  const svg = host.querySelector('svg');
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round((px - pl) / ((W - pl - pr) / Math.max(1, n - 1)))));
    const p = series[i];
    showTip(host, (X(i) / W) * r.width, (p.value === null ? Y(50) : Y(p.value)) / H * r.height - 12,
      p.value === null ? `Неделя ${p.week}: мало оценок` : `Неделя ${p.week}: <b>${p.value}%</b> · ${p.n} ${plural(p.n, 'оценка', 'оценки', 'оценок')}`);
  };
  svg.addEventListener('pointermove', move);
  svg.addEventListener('pointerdown', move);
  svg.addEventListener('pointerleave', hideTip);
}

function dayChart(host, days) {
  const vals = days.values;
  const W = 320; const H = 150; const pl = 26; const pr = 6; const pt = 18; const pb = 24;
  const n = vals.length;
  const max = Math.max(4, Math.ceil(Math.max(...vals) / 4) * 4);
  const slot = (W - pl - pr) / n;
  const bw = 20;
  const Y = (v) => pt + (H - pt - pb) * (1 - v / max);
  const grid = [0, max / 2, max].map((g) => `<line x1="${pl}" x2="${W - pr}" y1="${Y(g)}" y2="${Y(g)}" stroke="rgba(255,255,255,.08)"/><text x="${pl - 6}" y="${Y(g) + 3.5}" text-anchor="end" fill="#8582A6" font-family="${MONO}" font-size="9.5">${g}</text>`).join('');
  const bars = vals.map((v, i) => {
    const x = pl + slot * i + (slot - bw) / 2;
    const y = Y(v);
    const last = i === n - 1;
    const r = Math.min(4, (H - pb - y) / 2);
    const d = v === 0 ? '' : `M${x} ${H - pb}V${y + r}a${r} ${r} 0 0 1 ${r} -${r}h${bw - 2 * r}a${r} ${r} 0 0 1 ${r} ${r}V${H - pb}Z`;
    return (d ? `<path d="${d}" fill="${last ? 'url(#cy)' : 'rgba(185,182,210,.28)'}"/>` : '') +
      `<text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle" fill="#8582A6" font-family="${MONO}" font-size="9.5">${days.labels[i]}</text>` +
      (last && v ? `<text x="${x + bw / 2}" y="${y - 6}" text-anchor="middle" fill="#F2F1FA" font-family="Onest, sans-serif" font-size="12" font-weight="500">${v}</text>` : '');
  }).join('');
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Отобрано заказов по дням">${grid}${bars}</svg>`;
  const svg = host.querySelector('svg');
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.floor((px - pl) / slot)));
    const x = pl + slot * i + slot / 2;
    showTip(host, (x / W) * r.width, (Y(vals[i]) / H) * r.height - 10, `${days.labels[i]}: <b>${vals[i]}</b>`);
  };
  svg.addEventListener('pointermove', move);
  svg.addEventListener('pointerdown', move);
  svg.addEventListener('pointerleave', hideTip);
}

function hoursChart(box, h) {
  const mx = Math.max(...h.values) || 1;
  box.innerHTML = h.values.map((v, i) => `<i class="${h.top.includes(i) ? 'pk' : ''}" style="height:${Math.max(3, (v / mx) * 100)}%"></i>`).join('');
  const host = box.parentElement;
  const move = (ev) => {
    const r = box.getBoundingClientRect();
    const g = host.getBoundingClientRect();
    const i = Math.max(0, Math.min(23, Math.floor(((ev.clientX - r.left) / r.width) * 24)));
    showTip(host, r.left - g.left + ((i + 0.5) * r.width) / 24, r.top - g.top + r.height * (1 - h.values[i] / mx) - 8, `${i}:00 · <b>${h.values[i]}</b>`);
  };
  box.addEventListener('pointermove', move);
  box.addEventListener('pointerdown', move);
  box.addEventListener('pointerleave', hideTip);
}

export const statsActions = {
  'sugg-yes': async (el) => {
    try {
      await post(`/suggestions/${el.dataset.id}`, { accept: true });
      await loadProfile().catch(() => {});
      toast('Готово, настройки изменены');
      S.stats = null;
      await renderStats();
      refreshFeed();
    } catch (e) { toast(e.message); }
  },
  'sugg-no': async (el) => {
    try {
      await post(`/suggestions/${el.dataset.id}`, { accept: false });
      S.stats = null;
      await renderStats();
    } catch (e) { toast(e.message); }
  },
};

