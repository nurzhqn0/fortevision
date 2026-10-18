/* Journal tab: open-dataset labels + model results; rows play in the custom player. */
(function () {
  const { $, esc, apiKey, requireKey, notice, reduceMotion } = window.fv;
  let data = null, filter = 'all', selected = null, loading = false, openToken = 0, blobUrl = null;

  const player = new ViolationPlayer($('journalPlayer'));
  player.root.style.scrollMarginTop = '76px';
  window.fvJournalPlayer = player;

  const FILTERS = [['all', 'Все'], ['violation', 'С нарушением'], ['no_violation', 'Без нарушения'], ['err', 'Ошибки модели']];
  const OUTCOME = { TP: 'Найдено', TN: 'Верно: чисто', FP: 'Ложная тревога', FN: 'Пропуск' };
  const sec = (t) => (t == null ? '—' : window.vpFmt(t));
  const num = (x, d = 1) => x.toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (x) => num(x * 100) + ' %';

  function renderStats() {
    const s = data.summary;
    $('journalNote').textContent = data.note;
    $('journalStats').innerHTML = [
      ['Точность вердикта', `${pct(s.accuracy)} (${s.tp + s.tn} из ${s.total})`],
      ['Найдено нарушений', `${s.tp} из ${s.tp + s.fn}`],
      ['Ложных тревог', `${s.fp} из ${s.fp + s.tn} чистых`],
      ['Время в пределах ±6 с', pct(s.time_accuracy)],
    ].map(([l, v]) => `<span>${l}: <b>${v}</b></span>`).join('');
  }

  function renderFilters() {
    const box = $('journalFilters');
    box.replaceChildren(...FILTERS.map(([k, l]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = l;
      b.setAttribute('aria-pressed', String(filter === k));
      b.addEventListener('click', () => { filter = k; renderFilters(); renderRows(); });
      return b;
    }));
  }

  function visible(r) {
    if (filter === 'violation' || filter === 'no_violation') return r.label === filter;
    if (filter === 'err') return r.outcome === 'FP' || r.outcome === 'FN';
    return true;
  }

  function renderRows() {
    const body = $('journalBody');
    body.replaceChildren();
    data.rows.filter(visible).forEach((r) => {
      const tr = document.createElement('tr');
      tr.className = 'jrow' + (selected === r ? ' sel' : '');
      tr.innerHTML = `
        <td data-l="Видео"><button type="button" class="rowbtn">${esc(r.video)}</button></td>
        <td data-l="Разметка">${r.label === 'violation' ? 'нарушение' : 'без нарушения'}</td>
        <td data-l="Время разметки" class="mono">${sec(r.true_time)}</td>
        <td data-l="Модель">${r.pred_verdict === 'violation' ? 'есть отметка' : r.pred_verdict ? 'нет отметок' : '—'}</td>
        <td data-l="Время модели" class="mono">${sec(r.pred_time)}</td>
        <td data-l="Разн., с" class="mono">${r.delta == null ? '—' : num(r.delta)}</td>
        <td data-l="Итог">${r.outcome ? `<span class="badge ${r.outcome}">${OUTCOME[r.outcome]}</span>` : '—'}</td>`;
      const open = () => openRow(r);
      tr.addEventListener('click', open);
      tr.querySelector('.rowbtn').addEventListener('click', (e) => { e.stopPropagation(); open(); });
      body.appendChild(tr);
    });
  }

  async function openRow(r) {
    if (!requireKey()) return;
    selected = r;
    const token = ++openToken;
    renderRows();
    $('journalGrid').classList.add('has-player');
    $('journalSide').hidden = false;
    renderDetail(r);
    player.root.scrollIntoView({ block: 'start', behavior: reduceMotion() ? 'auto' : 'smooth' });

    const markers = [];
    if (r.true_time != null) markers.push({ id: 'truth', time: r.true_time, label: 'разметка', kind: 'truth' });
    if (r.pred_verdict === 'violation' && r.pred_time != null) markers.push({ id: 'pred', time: r.pred_time, label: 'отметка модели', kind: 'pred' });

    // header auth keeps the key out of URLs and history
    player.setLoading('Загружаем видео…');
    try {
      const res = await fetch(`/dataset/videos/${encodeURIComponent(r.file)}`, { headers: { 'X-API-Key': apiKey() } });
      if (token !== openToken) return;
      if (!res.ok) throw new Error(res.status === 401 ? 'Ключ доступа не подошёл.' : res.status === 404 ? 'Видео нет на сервере.' : `Ошибка ${res.status}.`);
      const blob = await res.blob();
      if (token !== openToken) return;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      blobUrl = URL.createObjectURL(blob);
      player.load(blobUrl, { name: r.video, markers, autoSeek: Math.max(0, (r.true_time ?? r.pred_time ?? 0) - 2) });
    } catch (e) {
      if (token === openToken) notice(`Не удалось открыть ${r.video}: ${e.message}`, { kind: 'error' });
    } finally {
      if (token === openToken) player.setLoading(null);
    }
  }

  function renderDetail(r) {
    $('journalDetail').innerHTML = `
      <h3>${esc(r.video)} ${r.outcome ? `<span class="badge ${r.outcome}">${OUTCOME[r.outcome]}</span>` : ''}</h3>
      <dl>
        <dt>Разметка</dt><dd>${r.label === 'violation' ? 'нарушение' : 'без нарушения'}</dd>
        <dt>Время разметки</dt><dd class="mono">${sec(r.true_time)}</dd>
        <dt>Модель</dt><dd>${r.pred_verdict === 'violation' ? 'есть отметка' : 'нет отметок'}</dd>
        <dt>Время модели</dt><dd class="mono">${sec(r.pred_time)}</dd>
        <dt>Расхождение</dt><dd class="mono">${r.delta == null ? '—' : num(r.delta) + ' с'}</dd>
      </dl>
      <div class="go"></div>
      <p class="hint" style="margin-top:12px">Видео открытого датасета. Использовать только в рамках хакатона.</p>`;
    const go = $('journalDetail').querySelector('.go');
    const add = (label, t) => {
      if (t == null) return;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn';
      b.textContent = label;
      b.addEventListener('click', () => { player.seek(Math.max(0, t - 1)); player.video.play().catch(() => {}); });
      go.appendChild(b);
    };
    add('К разметке', r.true_time);
    if (r.pred_verdict === 'violation') add('К отметке модели', r.pred_time);
  }

  window.fvLoadJournal = async function () {
    if (data || loading) return;
    loading = true;
    try {
      const res = await fetch('/static/journal.json');
      if (!res.ok) throw new Error(res.status);
      data = await res.json();
      renderStats();
      renderFilters();
      renderRows();
    } catch (e) {
      $('journalNote').textContent = 'Не удалось загрузить журнал: ' + e.message;
    } finally { loading = false; }
  };
  if (!$('paneJournal').hidden) window.fvLoadJournal();
})();
