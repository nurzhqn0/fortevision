/* Upload -> poll -> review workflow. Shared helpers live on window.fv. */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (str) => String(str ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ic = (name) => `<svg class="ico" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const MAX_BYTES = 50 * 1024 * 1024;
  const LONG_VIDEO_SEC = 150;

  let selectedFile = null, objectUrl = null, pollTimer = null, clockTimer = null, startedAt = 0;
  let phase = 'idle', uploadAbort = null, runId = 0;
  let violations = [], decisions = {}, activeIdx = null;

  const dropzone = $('dropzone'), fileInput = $('videoFileInput'), startBtn = $('startAnalysisBtn');
  const apiKeyInput = $('apiKeyInput'), keyBox = $('keyBox'), resultsSection = $('resultsSection'), violationsList = $('violationsList');
  const progressSection = $('progressSection'), noticeEl = $('notice');

  // --- shared helpers ---
  const apiKey = () => apiKeyInput.value.trim();
  function updateKeyState() {
    const has = !!apiKey();
    keyBox.dataset.state = has ? 'set' : 'missing';
    $('keyState').textContent = has ? 'Ключ задан' : 'Нужен ключ';
  }
  try { apiKeyInput.value = localStorage.getItem('fortevision_api_key') || ''; } catch (e) {}
  updateKeyState();
  apiKeyInput.addEventListener('input', () => {
    updateKeyState();
    try { localStorage.setItem('fortevision_api_key', apiKey()); } catch (e) {}
  });
  apiKeyInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') keyBox.open = false; });
  document.addEventListener('click', (e) => { if (keyBox.open && !keyBox.contains(e.target)) keyBox.open = false; });

  function requireKey() {
    if (apiKey()) return true;
    keyBox.open = true;
    apiKeyInput.focus();
    notice('Введите ключ доступа в правом верхнем углу: он нужен для запуска проверки и просмотра видео датасета.', { kind: 'error' });
    return false;
  }

  function notice(text, { kind = 'warn', action = null } = {}) {
    noticeEl.className = 'notice' + (kind === 'error' ? ' error' : '');
    noticeEl.innerHTML = `${ic(kind === 'error' ? 'alert' : 'info')}<p></p>`;
    noticeEl.querySelector('p').textContent = text;
    if (action) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn';
      b.textContent = action.label;
      b.addEventListener('click', action.fn);
      noticeEl.appendChild(b);
    }
    noticeEl.hidden = false;
  }
  const clearNotice = () => { noticeEl.hidden = true; };

  const dlg = $('imageModal');
  function openModal(src, caption) {
    const img = document.createElement('img');
    img.src = src;
    img.alt = caption;
    $('modalMedia').replaceChildren(img);
    $('modalCaption').textContent = caption;
    dlg.showModal();
  }
  $('closeModalBtn').addEventListener('click', () => dlg.close());
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });

  const fmtTime = (s) => (window.vpFmt ? window.vpFmt(s) : String(s));
  window.fv = { $, esc, ic, apiKey, requireKey, notice, clearNotice, openModal, reduceMotion };

  // --- tabs ---
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function showTab(name, focus) {
    tabs.forEach((b) => {
      const on = b.dataset.tab === name;
      b.setAttribute('aria-selected', String(on));
      b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    $('paneAnalyze').hidden = name !== 'analyze';
    $('paneJournal').hidden = name !== 'journal';
    if (name !== 'journal' && window.fvJournalPlayer) window.fvJournalPlayer.video.pause();
    if (name !== 'analyze') player.video.pause();
    if (name === 'journal' && window.fvLoadJournal) window.fvLoadJournal();
    if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  }
  tabs.forEach((b, i) => {
    b.addEventListener('click', () => showTab(b.dataset.tab));
    b.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      e.preventDefault();
      showTab(tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length].dataset.tab, true);
    });
  });

  const player = new ViolationPlayer($('analysisPlayer'));
  player.root.style.scrollMarginTop = '76px';

  // --- file selection ---
  const pick = () => fileInput.click();
  dropzone.addEventListener('click', pick);
  dropzone.addEventListener('keydown', (e) => { if (e.target === dropzone && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); pick(); } });
  dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('drag'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag');
    if (e.dataTransfer.files.length) handleFileSelect(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener('change', (e) => { if (e.target.files.length) handleFileSelect(e.target.files[0]); });
  $('clearFileBtn').addEventListener('click', (e) => { e.stopPropagation(); resetFileSelection(); });

  function setStartHint(text) { $('startHint').textContent = text; }

  function handleFileSelect(file) {
    clearNotice();
    if (!/\.(mov|mp4)$/i.test(file.name)) { notice('Нужен видеофайл в формате .mov или .mp4.', { kind: 'error' }); return; }
    if (file.size > MAX_BYTES) { notice(`Файл ${(file.size / 1048576).toFixed(0)} МБ: максимум 50 МБ. Выберите запись покороче.`, { kind: 'error' }); return; }
    selectedFile = file;
    resultsSection.hidden = true;
    player.video.pause();
    $('fileName').textContent = file.name;
    const size = (file.size / 1048576).toFixed(1).replace('.', ',') + ' МБ';
    $('fileSize').textContent = size;
    $('dropzonePrompt').hidden = true;
    $('selectedFileInfo').hidden = false;
    startBtn.disabled = false;
    setStartHint('Готово к запуску.');
    // duration check: the brief's clips are 10-145 s, longer ones may miss the 5-minute limit
    const probe = document.createElement('video');
    const url = URL.createObjectURL(file);
    probe.preload = 'metadata';
    probe.onloadedmetadata = () => {
      if (selectedFile === file && isFinite(probe.duration)) {
        $('fileSize').textContent = `${size} · ${fmtTime(probe.duration).slice(0, 5)}`;
        if (probe.duration > LONG_VIDEO_SEC) notice(`Видео длиннее ${LONG_VIDEO_SEC} с: обработка может не уложиться в 5 минут.`);
      }
      URL.revokeObjectURL(url);
    };
    probe.onerror = () => URL.revokeObjectURL(url);
    probe.src = url;
  }
  function resetFileSelection() {
    selectedFile = null;
    fileInput.value = '';
    $('dropzonePrompt').hidden = false;
    $('selectedFileInfo').hidden = true;
    startBtn.disabled = true;
    setStartHint('Сначала выберите видеофайл.');
  }

  // --- progress ---
  function stageText() {
    const s = (Date.now() - startedAt) / 1000;
    if (phase === 'upload') return 'Загружаем видео';
    if (s < 25) return 'Запускаем анализ';
    if (s < 120) return 'Анализируем кадры';
    return 'Всё ещё работаем: возможно, модель загружается после простоя';
  }
  function startClock() {
    startedAt = Date.now();
    clearInterval(clockTimer);
    clockTimer = setInterval(() => {
      const d = Math.floor((Date.now() - startedAt) / 1000);
      $('elapsedTimer').textContent = `${String(Math.floor(d / 60)).padStart(2, '0')}:${String(d % 60).padStart(2, '0')}`;
      $('progressStatusText').textContent = stageText();
    }, 1000);
  }
  function stopWork() {
    clearInterval(clockTimer);
    clearTimeout(pollTimer);
    pollTimer = null;
    phase = 'idle';
    progressSection.hidden = true;
    progressSection.setAttribute('aria-busy', 'false');
    startBtn.disabled = !selectedFile;
    $('btnText').textContent = 'Запустить проверку';
  }

  $('cancelBtn').addEventListener('click', () => {
    runId++;
    if (uploadAbort) uploadAbort.abort();
    stopWork();
    notice('Проверка отменена. Сервер мог уже начать обработку этого видео, но результат не будет показан.');
  });

  // --- analysis ---
  startBtn.addEventListener('click', startAnalysis);
  async function startAnalysis() {
    if (!selectedFile || phase !== 'idle') return;
    clearNotice();
    if (!requireKey()) return;

    const myRun = ++runId;
    phase = 'upload';
    startBtn.disabled = true;
    $('btnText').textContent = 'Выполняется…';
    resultsSection.hidden = true;
    violationsList.replaceChildren();
    progressSection.hidden = false;
    progressSection.setAttribute('aria-busy', 'true');
    $('progressStatusText').textContent = stageText();
    $('elapsedTimer').textContent = '00:00';
    startClock();

    // the same file feeds the player: no server-side storage needed
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(selectedFile);

    uploadAbort = new AbortController();
    try {
      const formData = new FormData();
      formData.append('file', selectedFile);
      const response = await fetch('/api/v1/analyze', { method: 'POST', headers: { 'X-API-Key': apiKey() }, body: formData, signal: uploadAbort.signal });
      if (myRun !== runId) return;
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        const msg = response.status === 401 ? 'Ключ доступа не подошёл. Проверьте его в правом верхнем углу.' : (errData.detail || 'Не удалось запустить анализ.');
        throw new Error(msg);
      }
      const { job_id } = await response.json();
      phase = 'poll';
      startedAt = Date.now();
      $('currentJobId').textContent = job_id;
      $('progressStatusText').textContent = stageText();
      poll(job_id, myRun, 0);
    } catch (err) {
      if (myRun !== runId) return;
      stopWork();
      notice(err.name === 'TypeError' ? 'Нет связи с сервисом. Проверьте соединение и повторите.' : err.message, {
        kind: 'error', action: { label: 'Повторить', fn: startAnalysis },
      });
    }
  }

  async function poll(jobId, myRun, fails) {
    if (myRun !== runId) return;
    try {
      const response = await fetch(`/api/v1/jobs/${jobId}`, { headers: { 'X-API-Key': apiKey() } });
      if (myRun !== runId) return;
      if (response.status === 401 || response.status === 404) {
        stopWork();
        notice(response.status === 401 ? 'Ключ доступа не подошёл. Проверьте его в правом верхнем углу.' : 'Сервер не нашёл эту задачу. Запустите проверку ещё раз.', {
          kind: 'error', action: { label: 'Повторить', fn: startAnalysis },
        });
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      if (fails >= 3) clearNotice();
      const data = await response.json();
      if (data.status === 'done' || data.status === 'failed') {
        stopWork();
        renderResults(data);
        return;
      }
      fails = 0;
    } catch (e) {
      fails++;
      if (fails === 3) notice('Нет связи с сервисом. Продолжаем попытки, анализ на сервере не прерван.');
    }
    pollTimer = setTimeout(() => poll(jobId, myRun, fails), 2000);
  }

  // --- results ---
  // description: "Что видно: ... Почему это может быть нарушением: ... Нормативный источник: ... (url)."
  function renderDescription(text) {
    const seen = /Что видно:\s*([\s\S]*?)(?=\s*Почему это может быть нарушением:|\s*Нормативный источник:|$)/.exec(text);
    const why = /Почему это может быть нарушением:\s*([\s\S]*?)(?=\s*Нормативный источник:|$)/.exec(text);
    const src = /Нормативный источник:\s*([\s\S]*)$/.exec(text);
    if (!seen && !why && !src) return `<p>${esc(text)}</p>`;
    const link = (t) => esc(t).replace(/(https?:\/\/[^\s)]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
    const row = (label, body) => (body ? `<p><b>${label}:</b> ${body}</p>` : '');
    return row('Что видно', esc(seen ? seen[1] : '')) + row('Почему это может быть нарушением', esc(why ? why[1] : '')) + row('Нормативный источник', link(src ? src[1] : ''));
  }

  function setBanner(kind, icon, title, sub) {
    $('verdictBanner').className = 'verdict' + (kind ? ' ' + kind : '');
    $('verdictIcon').innerHTML = `<use href="#i-${icon}"/>`;
    $('verdictTitle').textContent = title;
    $('verdictSub').textContent = sub;
  }

  function updateReviewCount() {
    const done = Object.keys(decisions).length, total = violations.length;
    const yes = Object.values(decisions).filter((d) => d === 'yes').length;
    $('reviewCount').textContent = done === total
      ? `Проверено ${done} из ${total}: подтверждено ${yes}, отклонено ${done - yes}`
      : `Проверено ${done} из ${total}`;
  }

  function setActive(idx, scroll) {
    if (idx === activeIdx) return;
    activeIdx = idx;
    violationsList.querySelectorAll('.flag').forEach((c) => c.classList.toggle('active', Number(c.dataset.idx) === idx));
    const card = violationsList.querySelector(`.flag[data-idx="${idx}"]`);
    if (card && scroll) card.scrollIntoView({ block: 'nearest', behavior: reduceMotion() ? 'auto' : 'smooth' });
  }

  function decide(idx, value) {
    if (idx == null || !violations[idx]) return;
    if (decisions[idx] === value) delete decisions[idx]; else decisions[idx] = value;
    const card = violationsList.querySelector(`.flag[data-idx="${idx}"]`);
    card.classList.toggle('confirmed', decisions[idx] === 'yes');
    card.classList.toggle('dismissed', decisions[idx] === 'no');
    card.querySelectorAll('.decide .btn').forEach((b) => b.setAttribute('aria-pressed', String(decisions[idx] === b.dataset.v)));
    updateReviewCount();
    if (decisions[idx]) {
      const next = violations.findIndex((_, i) => i > idx && !decisions[i]);
      if (next >= 0) player.focusMarker(player.markers[next], false);
    }
  }

  function toPlayer() {
    if (window.matchMedia('(max-width: 1099px)').matches) player.root.scrollIntoView({ block: 'start', behavior: reduceMotion() ? 'auto' : 'smooth' });
  }

  function renderResults(data) {
    resultsSection.hidden = false;
    $('rawJsonResponse').textContent = JSON.stringify(data, null, 2);
    $('currentJobId').textContent = data.job_id || '';
    violations = data.violations || [];
    decisions = {};
    activeIdx = null;
    violationsList.replaceChildren();
    $('reviewCount').textContent = '';
    const hasFlags = data.verdict === 'violation' && violations.length > 0;
    const named = selectedFile ? selectedFile.name : 'video';

    player.load(objectUrl, {
      name: named,
      markers: hasFlags ? violations.map((v, i) => ({ id: i, time: v.timecode_sec, label: v.type, kind: 'pred' })) : [],
      autoSeek: hasFlags ? Math.max(0, violations[0].timecode_sec - 1) : null,
    });

    if (data.status === 'failed') {
      setBanner('failed', 'alert', 'Анализ не выполнен', 'Видео не удалось обработать. Попробуйте ещё раз.');
      $('reviewTitle').textContent = 'Причина';
      const box = document.createElement('div');
      box.className = 'empty';
      box.innerHTML = '<strong></strong><p></p>';
      box.querySelector('strong').textContent = data.error || 'Причина не указана';
      box.querySelector('p').textContent = 'Если ошибка повторяется, откройте «Технические детали» ниже и передайте их администратору.';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'btn primary';
      retry.style.marginTop = '12px';
      retry.textContent = 'Повторить проверку';
      retry.addEventListener('click', startAnalysis);
      box.appendChild(retry);
      violationsList.appendChild(box);
      return;
    }
    if (!hasFlags) {
      setBanner('clear', 'ok', 'Признаков нарушений не выявлено', 'Это подсказка, а не гарантия: запись можно просмотреть целиком в плеере.');
      $('reviewTitle').textContent = 'Отметки';
      violationsList.innerHTML = '<div class="empty"><strong>Отметок нет</strong>Сервис не нашёл эпизодов, похожих на нарушение. Если сомневаетесь, просмотрите запись вручную.</div>';
      return;
    }

    setBanner('', 'alert', 'Обнаружены признаки возможных нарушений', `Отметок: ${violations.length}. Проверьте каждую на видео и подтвердите или отклоните. Решение остаётся за вами.`);
    $('reviewTitle').textContent = 'Отметки для проверки';
    violations.forEach((v, idx) => violationsList.appendChild(buildFlag(v, idx)));
    updateReviewCount();
    setActive(0, false);
    $('verdictBanner').scrollIntoView({ block: 'start', behavior: reduceMotion() ? 'auto' : 'smooth' });
  }

  function buildFlag(v, idx) {
    const card = document.createElement('article');
    card.className = 'flag';
    card.dataset.idx = idx;
    card.innerHTML = `
      <div class="flag-head">
        <span class="flag-no" aria-hidden="true">${idx + 1}</span>
        <h3></h3>
        <button type="button" class="tc-btn" title="Перейти к моменту на видео">${ic('play')}<span>${fmtTime(v.timecode_sec)}</span></button>
      </div>
      <div class="flag-body">${renderDescription(v.description || '')}</div>
      <p class="ev-title">Кадры-доказательства (${(v.evidence_frames || []).length}). Клик по кадру: перейти к моменту</p>
      <div class="ev-grid"></div>
      <div class="decide" role="group" aria-label="Решение по отметке ${idx + 1}">
        <button type="button" class="btn yes" data-v="yes" aria-pressed="false">${ic('check')}Подтвердить <kbd>Y</kbd></button>
        <button type="button" class="btn no" data-v="no" aria-pressed="false">${ic('x')}Отклонить <kbd>X</kbd></button>
      </div>`;
    card.querySelector('h3').textContent = v.type;
    card.querySelector('.tc-btn').addEventListener('click', () => { player.focusMarker(player.markers[idx]); toPlayer(); });
    card.querySelectorAll('.decide .btn').forEach((b) => b.addEventListener('click', () => decide(idx, b.dataset.v)));
    const grid = card.querySelector('.ev-grid');
    (v.evidence_frames || []).forEach((ef) => {
      const src = ef.image_base64.startsWith('data:') ? ef.image_base64 : `data:image/jpeg;base64,${ef.image_base64}`;
      const wrap = document.createElement('div');
      wrap.className = 'ev';
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ev-thumb';
      b.title = `Перейти к ${fmtTime(ef.timestamp_sec)}`;
      const img = document.createElement('img');
      img.src = src;
      img.alt = `Кадр на ${ef.timestamp_sec} с`;
      const lab = document.createElement('span');
      lab.textContent = fmtTime(ef.timestamp_sec);
      b.append(img, lab);
      b.addEventListener('click', () => { player.video.pause(); player.seek(ef.timestamp_sec); setActive(idx, false); toPlayer(); });
      const zoom = document.createElement('button');
      zoom.type = 'button';
      zoom.className = 'ev-zoom';
      zoom.innerHTML = ic('zoom');
      zoom.title = 'Увеличить кадр';
      zoom.setAttribute('aria-label', 'Увеличить кадр');
      zoom.addEventListener('click', () => openModal(src, `${v.type}, ${fmtTime(ef.timestamp_sec)}`));
      wrap.append(b, zoom);
      grid.appendChild(wrap);
    });
    return card;
  }

  player.root.addEventListener('markerenter', (e) => setActive(e.detail.id, true));
  player.root.addEventListener('markerfocus', (e) => setActive(e.detail.id, true));

  // Y / X confirm or dismiss the active mark (outside text fields)
  document.addEventListener('keydown', (e) => {
    if (resultsSection.hidden || $('paneAnalyze').hidden || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.closest('input, textarea, select') || !violations.length) return;
    const k = e.key.toLowerCase();
    if (k === 'y' || k === 'н') decide(activeIdx ?? 0, 'yes');
    else if (k === 'x' || k === 'ч') decide(activeIdx ?? 0, 'no');
  });

  $('copyJsonBtn').addEventListener('click', async () => {
    const text = $('rawJsonResponse').textContent;
    try { await navigator.clipboard.writeText(text); } catch (e) {
      const r = document.createRange();
      r.selectNodeContents($('rawJsonResponse'));
      const s = getSelection();
      s.removeAllRanges();
      s.addRange(r);
    }
  });

  window.fvShowTab = showTab;
  showTab(location.hash === '#journal' ? 'journal' : 'analyze');
})();
