/* ViolationPlayer: custom video player with markers, AI HUD, snapshot and clip export.
 * Knows nothing about the API. Usage:
 *   const p = new ViolationPlayer(rootEl);
 *   p.load(src, {name, markers: [{id, time, label, kind: 'pred'|'truth', window: 3}], autoSeek});
 * Events on rootEl: 'markerenter' / 'markerleave' (playhead enters/leaves a window),
 *                   'markerfocus' (a marker was jumped to by tick click or N/P). detail: marker.
 * Wording is deliberately cautious: the HUD says "possible sign", never "violation".
 */
(function () {
  const FPS = 30;
  const CLIP_PAD = 3;
  const SPEEDS = [0.25, 0.5, 1, 2];
  const HUD_TEXT = 'ВОЗМОЖНЫЙ ПРИЗНАК';
  const AMBER = '#f6b24e';

  const pad = (n, l = 2) => String(n).padStart(l, '0');
  function fmt(t) {
    t = Math.max(0, t || 0);
    return `${pad(Math.floor(t / 60))}:${pad(Math.floor(t % 60))}.${pad(Math.floor((t % 1) * 100))}`;
  }
  const ic = (name) => `<svg class="ico" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function download(blob, filename) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  class ViolationPlayer {
    constructor(root) {
      this.root = root;
      this.markers = [];
      this.name = 'video';
      this.active = null;
      this.speedIdx = 2;
      this.recording = null;
      this._build();
      this._bind();
    }

    _build() {
      const r = this.root;
      r.classList.add('vp');
      r.tabIndex = 0;
      r.setAttribute('role', 'group');
      r.setAttribute('aria-label', 'Видеоплеер с отметками');
      r.innerHTML = '';

      this.stage = el('div', 'vp-stage');
      this.video = el('video');
      this.video.playsInline = true;
      this.video.preload = 'metadata';
      this.video.setAttribute('aria-label', 'Видеозапись кассы');
      this.hud = el('div', 'vp-hud');
      this.hudTag = el('div', 'vp-tag', 'AI · FORTEVISION');
      this.hudTc = el('div', 'vp-tc', '00:00.00');
      this.hudAlert = el('div', 'vp-alert', '<i></i><i></i><i></i><i></i><b></b><u>подсказка для проверки человеком</u>');
      this.hudLabel = this.hudAlert.querySelector('b');
      this.hud.append(this.hudTag, this.hudTc, this.hudAlert);
      this.err = el('div', 'vp-err');
      this.err.hidden = true;
      this.loading = el('div', 'vp-load', '<span class="spin" aria-hidden="true"></span><span></span>');
      this.loading.hidden = true;
      this.stage.append(this.video, this.hud, this.err, this.loading);

      this.timeline = el('div', 'vp-timeline');
      this.timeline.tabIndex = 0;
      this.timeline.setAttribute('role', 'slider');
      this.timeline.setAttribute('aria-label', 'Перемотка (стрелки: 5 секунд)');
      this.timeline.setAttribute('aria-valuemin', '0');
      this.track = el('div', 'vp-track');
      this.buf = el('div', 'vp-buf');
      this.fill = el('div', 'vp-fill');
      this.marks = el('div');
      this.knob = el('div', 'vp-knob');
      this.hover = el('div', 'vp-hover');
      this.track.append(this.buf, this.fill, this.marks);
      this.timeline.append(this.track, this.knob, this.hover);

      const b = (icon, label, title, fn, cls) => {
        const x = el('button', 'vp-btn' + (cls ? ' ' + cls : ''), ic(icon) + (label ? `<span>${label}</span>` : ''));
        x.type = 'button';
        x.title = title;
        x.setAttribute('aria-label', title);
        x.addEventListener('click', (e) => { e.stopPropagation(); fn(); r.focus({ preventScroll: true }); });
        return x;
      };

      this.btnPlay = b('play', '', 'Пуск / пауза (Пробел)', () => this.toggle(), 'primary');
      this.time = el('span', 'vp-time', '00:00.00 / 00:00.00');
      this.frameLbl = el('span', 'vp-time vp-frame', 'кадр 0');
      this.btnMore = b('more', '', 'Ещё действия', () => {
        const open = r.classList.toggle('more-open');
        this.btnMore.setAttribute('aria-expanded', String(open));
      }, 'vp-more');
      this.btnMore.setAttribute('aria-expanded', 'false');
      this.controls = el('div', 'vp-controls');
      this.controls.append(
        this.btnPlay,
        b('back5', '', 'Назад на 5 с (←)', () => this.seek(this.video.currentTime - 5)),
        b('fwd5', '', 'Вперёд на 5 с (→)', () => this.seek(this.video.currentTime + 5)),
        b('prev', '', 'Предыдущая отметка (P)', () => this.jumpMarker(-1)),
        b('next', '', 'Следующая отметка (N)', () => this.jumpMarker(1)),
        this.time, this.frameLbl, el('span', 'vp-spacer'), this.btnMore
      );

      this.btnSpeed = b('', '1×', 'Скорость воспроизведения', () => this.cycleSpeed());
      this.btnSnap = b('camera', 'Фото', 'Сохранить кадр (S)', () => this.snapshot());
      this.btnClip = b('clip', 'Клип', 'Вырезать фрагмент ±3 с вокруг отметки (K)', () => this.exportClip());
      this.btnCine = b('cinema', 'Кино', 'Кинорежим (C)', () => this.toggleCinema());
      this.btnKeys = b('keys', '', 'Клавиши (?)', () => this.toggleHelp());
      this.sec = el('div', 'vp-sec');
      this.sec.append(
        b('stepb', '', 'Кадр назад (,)', () => this.step(-1)),
        b('stepf', '', 'Кадр вперёд (.)', () => this.step(1)),
        this.btnSpeed, el('span', 'vp-spacer'),
        this.btnSnap, this.btnClip, this.btnCine,
        b('full', '', 'Полный экран (F)', () => this.fullscreen()),
        this.btnKeys
      );

      this.legend = el('div', 'vp-legend', '<span>окно ±3 с вокруг отметки</span>');
      this.legendTruth = el('span', 't', 'разметка датасета');
      this.legendTruth.hidden = true;
      this.legend.appendChild(this.legendTruth);

      this.help = el('div', 'vp-help', [
        ['Пробел', 'пуск / пауза'], ['← →', '±5 секунд'], [', .', 'кадр назад / вперёд'], ['N P', 'следующая / предыдущая отметка'],
        ['S', 'сохранить кадр'], ['K', 'клип с отметкой'], ['C', 'кинорежим'], ['F', 'полный экран'], ['Y X', 'подтвердить / отклонить отметку'],
      ].map(([k, t]) => `<div><kbd>${k}</kbd>${t}</div>`).join(''));

      this.clipbar = el('div', 'vp-clipbar');
      this.clipText = el('span');
      this.clipProg = el('progress');
      this.clipProg.max = 1;
      this.clipCancel = el('button', 'vp-btn', 'Отмена');
      this.clipCancel.type = 'button';
      this.clipbar.append(this.clipText, this.clipProg, this.clipCancel);

      r.append(this.stage, this.timeline, this.controls, this.sec, this.legend, this.help, this.clipbar);
    }

    _bind() {
      const v = this.video;
      v.addEventListener('play', () => (this.btnPlay.innerHTML = ic('pause')));
      v.addEventListener('pause', () => (this.btnPlay.innerHTML = ic('play')));
      v.addEventListener('loadedmetadata', () => { this._renderMarkers(); this._tick(); });
      v.addEventListener('progress', () => this._buffered());
      v.addEventListener('timeupdate', () => this._tick());
      v.addEventListener('seeked', () => this._tick());
      v.addEventListener('error', () => this._showError());
      if ('requestVideoFrameCallback' in v) {
        const loop = () => { this._tick(); v.requestVideoFrameCallback(loop); };
        v.requestVideoFrameCallback(loop);
      }
      v.addEventListener('click', () => this.toggle());
      v.addEventListener('dblclick', () => this.fullscreen());

      const seekFromEvent = (e) => {
        const rect = this.track.getBoundingClientRect();
        const x = Math.min(Math.max(e.clientX - rect.left, 0), rect.width);
        return (x / rect.width) * (v.duration || 0);
      };
      let dragging = false;
      this.timeline.addEventListener('pointerdown', (e) => {
        if (e.target.closest('.vp-tick')) return;
        dragging = true;
        this.timeline.setPointerCapture(e.pointerId);
        this.seek(seekFromEvent(e));
      });
      this.timeline.addEventListener('pointermove', (e) => {
        const rect = this.track.getBoundingClientRect();
        this.hover.style.display = 'block';
        this.hover.style.left = Math.min(Math.max(e.clientX - rect.left, 0), rect.width) + 'px';
        this.hover.textContent = fmt(seekFromEvent(e));
        if (dragging) this.seek(seekFromEvent(e));
      });
      this.timeline.addEventListener('pointerup', () => (dragging = false));
      this.timeline.addEventListener('pointerleave', () => (this.hover.style.display = 'none'));
      this.clipCancel.addEventListener('click', () => this.recording && this.recording.cancel());

      this.root.addEventListener('keydown', (e) => {
        if (e.target.closest('input, textarea, select') || e.ctrlKey || e.metaKey || e.altKey) return;
        // Space/Enter on a focused button must keep activating that button
        if ((e.key === ' ' || e.key === 'Enter') && e.target.closest('button')) return;
        const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
        const map = {
          ' ': () => this.toggle(), ArrowLeft: () => this.seek(v.currentTime - 5), ArrowRight: () => this.seek(v.currentTime + 5),
          ',': () => this.step(-1), '.': () => this.step(1), n: () => this.jumpMarker(1), p: () => this.jumpMarker(-1),
          s: () => this.snapshot(), k: () => this.exportClip(), c: () => this.toggleCinema(), f: () => this.fullscreen(),
          '?': () => this.toggleHelp(),
        };
        if (map[k]) { e.preventDefault(); map[k](); }
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.root.classList.contains('vp-cinema')) this.toggleCinema(false);
      });
    }

    // --- public API ---

    load(src, { name = 'video', markers = [], autoSeek = null } = {}) {
      this.cancelClip();
      this.name = String(name).replace(/\.[^.]+$/, '');
      this.markers = markers.map((m, i) => ({ id: i, window: CLIP_PAD, kind: 'pred', ...m }));
      this.active = null;
      this.err.hidden = true;
      this.legendTruth.hidden = !this.markers.some((m) => m.kind === 'truth');
      this.video.src = src;
      this.video.playbackRate = SPEEDS[this.speedIdx];
      this._renderMarkers();
      this._setAlert(null);
      if (autoSeek != null) {
        const go = () => { this.seek(autoSeek); this.video.removeEventListener('loadedmetadata', go); };
        this.video.addEventListener('loadedmetadata', go);
      }
    }

    setLoading(text) {
      this.loading.hidden = !text;
      this.loading.lastChild.textContent = text || '';
    }

    destroy() { this.cancelClip(); this.video.pause(); this.video.removeAttribute('src'); this.video.load(); }
    toggle() { this.video.paused ? this.video.play().catch(() => {}) : this.video.pause(); }
    seek(t) { const d = this.video.duration || 0; this.video.currentTime = Math.min(Math.max(t, 0), d || t); this._tick(); }
    step(n) { this.video.pause(); this.seek(this.video.currentTime + n / FPS); }
    toggleHelp() { const on = this.help.classList.toggle('on'); this.btnKeys.classList.toggle('on', on); }
    cycleSpeed() {
      this.speedIdx = (this.speedIdx + 1) % SPEEDS.length;
      this.video.playbackRate = SPEEDS[this.speedIdx];
      this.btnSpeed.innerHTML = `<span>${SPEEDS[this.speedIdx]}×</span>`;
    }
    jumpMarker(dir) {
      const t = this.video.currentTime, ms = this._preds().sort((a, b) => a.time - b.time);
      const m = dir > 0 ? ms.find((x) => x.time > t + 0.5) : [...ms].reverse().find((x) => x.time < t - 0.5);
      if (m) this.focusMarker(m);
    }
    focusMarker(m, play = true) {
      this.seek(Math.max(0, m.time - 1));
      if (play) this.video.play().catch(() => {});
      this.root.dispatchEvent(new CustomEvent('markerfocus', { detail: m }));
    }
    toggleCinema(force) {
      const on = force == null ? !this.root.classList.contains('vp-cinema') : force;
      this.root.classList.toggle('vp-cinema', on);
      document.body.classList.toggle('vp-cinema-open', on);
      this.btnCine.innerHTML = on ? ic('x') + '<span>Выйти</span>' : ic('cinema') + '<span>Кино</span>';
    }
    fullscreen() {
      if (document.fullscreenElement) document.exitFullscreen();
      else if (this.root.requestFullscreen) this.root.requestFullscreen().catch(() => {});
    }

    // --- internals ---

    _preds() { return this.markers.filter((m) => m.kind === 'pred'); }

    _showError() {
      if (!this.video.getAttribute('src')) return;
      this.err.hidden = false;
      this.err.textContent = 'Браузер не воспроизводит этот файл. Откройте страницу в Chrome или Safari; кадры-доказательства остаются доступными.';
    }

    _renderMarkers() {
      this.marks.innerHTML = '';
      const d = this.video.duration;
      if (!d || !isFinite(d)) return;
      this.markers.forEach((m) => {
        if (m.kind === 'pred') {
          const band = el('div', 'vp-band');
          const a = Math.max(0, m.time - m.window), z = Math.min(d, m.time + m.window);
          band.style.left = (a / d) * 100 + '%';
          band.style.width = ((z - a) / d) * 100 + '%';
          this.marks.appendChild(band);
        }
        const t = el('button', 'vp-tick' + (m.kind === 'truth' ? ' truth' : ''));
        t.type = 'button';
        t.style.left = (m.time / d) * 100 + '%';
        const title = `${m.kind === 'truth' ? 'Разметка' : 'Отметка'}: ${m.label || 'возможный признак'}, ${fmt(m.time)}`;
        t.title = title;
        t.setAttribute('aria-label', title);
        t.addEventListener('click', (e) => { e.stopPropagation(); this.focusMarker(m); this.root.focus({ preventScroll: true }); });
        this.marks.appendChild(t);
      });
    }

    _buffered() {
      const v = this.video, d = v.duration;
      if (!d || !v.buffered.length) return;
      this.buf.style.width = (v.buffered.end(v.buffered.length - 1) / d) * 100 + '%';
    }

    _setAlert(m) {
      if (m === this.active) return;
      const prev = this.active;
      this.active = m;
      this.hudAlert.classList.toggle('on', !!m);
      if (m) this.hudLabel.textContent = `${HUD_TEXT} · ${m.label || ''} · ${fmt(m.time)}`;
      if (prev) this.root.dispatchEvent(new CustomEvent('markerleave', { detail: prev }));
      if (m) this.root.dispatchEvent(new CustomEvent('markerenter', { detail: m }));
    }

    _tick() {
      const v = this.video, t = v.currentTime || 0, d = v.duration || 0;
      const pct = d ? (t / d) * 100 : 0;
      this.fill.style.width = pct + '%';
      this.knob.style.left = pct + '%';
      this.timeline.setAttribute('aria-valuenow', String(Math.round(t)));
      this.timeline.setAttribute('aria-valuemax', String(Math.round(d)));
      this.timeline.setAttribute('aria-valuetext', `${fmt(t)} из ${fmt(d)}`);
      const tc = fmt(t);
      this.hudTc.textContent = tc;
      this.time.textContent = `${tc} / ${fmt(d)}`;
      this.frameLbl.textContent = `кадр ${Math.floor(t * FPS)}`;
      this._setAlert(this._preds().find((m) => Math.abs(t - m.time) <= m.window) || null);
      this._buffered();
    }

    _drawFrame(ctx, w, h, withHud) {
      ctx.drawImage(this.video, 0, 0, w, h);
      const t = this.video.currentTime;
      const m = withHud ? this.active : null;
      ctx.font = `500 ${Math.round(w / 38)}px "Plex Mono", monospace`;
      ctx.textBaseline = 'middle';
      if (m) {
        const lw = Math.max(4, w / 120);
        ctx.strokeStyle = AMBER;
        ctx.lineWidth = lw;
        ctx.strokeRect(lw / 2, lw / 2, w - lw, h - lw);
        const text = `${HUD_TEXT} · ${m.label || ''} · ${fmt(m.time)}`;
        const tw = Math.min(w - 20, ctx.measureText(text).width + 20);
        ctx.fillStyle = AMBER;
        ctx.fillRect((w - tw) / 2, h * 0.04, tw, w / 24);
        ctx.fillStyle = '#1d1300';
        ctx.fillText(text, (w - tw) / 2 + 10, h * 0.04 + w / 48, tw - 20);
      }
      ctx.fillStyle = 'rgba(0,0,0,.65)';
      ctx.fillRect(0, h - w / 20, w, w / 20);
      ctx.fillStyle = '#fff';
      ctx.fillText(`${this.name} · ${fmt(t)} · кадр ${Math.floor(t * FPS)}`, 10, h - w / 40, w - 20);
    }

    snapshot() {
      const v = this.video;
      if (!v.videoWidth) return;
      const c = document.createElement('canvas');
      c.width = v.videoWidth;
      c.height = v.videoHeight;
      this._drawFrame(c.getContext('2d'), c.width, c.height, true);
      const tc = fmt(v.currentTime).replace(':', '-');
      c.toBlob((b) => b && download(b, `${this.name}_${tc}.jpg`), 'image/jpeg', 0.92);
    }

    cancelClip() { if (this.recording) this.recording.cancel(); }

    async exportClip() {
      const v = this.video;
      if (this.recording || !v.videoWidth) return;
      const mime = typeof MediaRecorder === 'undefined' ? null
        : ['video/webm;codecs=vp9', 'video/webm', 'video/mp4'].find((m) => MediaRecorder.isTypeSupported(m));
      if (!mime) {
        this.clipbar.classList.add('on');
        this.clipText.textContent = 'Этот браузер не умеет записывать клип. Используйте Chrome, Edge или Safari.';
        this.clipProg.hidden = true;
        this.clipCancel.textContent = 'Закрыть';
        this.clipCancel.onclick = () => this.clipbar.classList.remove('on');
        return;
      }
      this.clipProg.hidden = false;
      this.clipCancel.textContent = 'Отмена';
      this.clipCancel.onclick = null;
      const preds = this._preds();
      const center = this.active ? this.active.time : preds.length ? preds[0].time : v.currentTime;
      const start = Math.max(0, center - CLIP_PAD), end = Math.min(v.duration, center + CLIP_PAD);
      const ext = mime.startsWith('video/mp4') ? 'mp4' : 'webm';

      const canvas = document.createElement('canvas');
      canvas.width = v.videoWidth;
      canvas.height = v.videoHeight;
      const ctx = canvas.getContext('2d');
      const rec = new MediaRecorder(canvas.captureStream(FPS), { mimeType: mime, videoBitsPerSecond: 4e6 });
      const chunks = [];
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);

      const prevTime = v.currentTime, wasMuted = v.muted;
      let cancelled = false, raf = 0;
      const onTime = () => {
        this.clipProg.value = Math.min(1, (v.currentTime - start) / (end - start));
        if (v.currentTime >= end - 0.03 && rec.state === 'recording') rec.stop();
      };
      const done = new Promise((resolve) => { rec.onstop = resolve; });
      this.recording = { cancel: () => { cancelled = true; if (rec.state === 'recording') rec.stop(); } };

      this.btnClip.disabled = true;
      this.clipbar.classList.add('on');
      this.clipText.textContent = `Запись ${fmt(start)}–${fmt(end)}…`;
      this.clipProg.value = 0;

      v.pause();
      v.muted = true;
      v.playbackRate = 1;
      await new Promise((res) => { v.addEventListener('seeked', res, { once: true }); v.currentTime = start; });
      rec.start();
      v.addEventListener('timeupdate', onTime);
      const draw = () => { this._drawFrame(ctx, canvas.width, canvas.height, true); raf = requestAnimationFrame(draw); };
      draw();
      try { await v.play(); } catch (e) { cancelled = true; rec.stop(); }
      await done;

      cancelAnimationFrame(raf);
      v.pause();
      v.removeEventListener('timeupdate', onTime);
      this.recording = null;
      this.clipbar.classList.remove('on');
      this.btnClip.disabled = false;
      v.muted = wasMuted;
      v.playbackRate = SPEEDS[this.speedIdx];
      this.seek(prevTime);
      if (!cancelled && chunks.length) {
        download(new Blob(chunks, { type: mime }), `${this.name}_clip_${fmt(center).replace(':', '-')}.${ext}`);
      }
    }
  }

  window.ViolationPlayer = ViolationPlayer;
  window.vpFmt = fmt;
})();
