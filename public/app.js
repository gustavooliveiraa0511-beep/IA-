/* Dark Viral Video Generator — Frontend Logic */

let selectedStyle = 'dark';

// ── Init ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Duration slider
  const slider = document.getElementById('durationSlider');
  slider.addEventListener('input', () => {
    document.getElementById('durationVal').textContent = slider.value + 's';
  });

  // Style pills
  document.querySelectorAll('.pill').forEach(p => {
    p.addEventListener('click', () => {
      document.querySelectorAll('.pill').forEach(x => x.classList.remove('active'));
      p.classList.add('active');
      selectedStyle = p.dataset.val;
    });
  });

  // Load topic suggestions
  loadTopics();
});

// ── Topics ────────────────────────────────────────────
async function loadTopics() {
  try {
    const res    = await fetch('/api/topics');
    const topics = await res.json();
    const wrap   = document.getElementById('topicsWrap');

    topics.slice(0, 6).forEach(t => {
      const chip = document.createElement('div');
      chip.className   = 'topic-chip';
      chip.textContent = t;
      chip.onclick     = () => { document.getElementById('topicInput').value = t; };
      wrap.appendChild(chip);
    });
  } catch {}
}

// ── Config toggle ─────────────────────────────────────
function toggleConfig() {
  const panel = document.getElementById('configPanel');
  const arrow = document.getElementById('configArrow');
  const open  = panel.style.display === 'block';
  panel.style.display = open ? 'none' : 'block';
  arrow.textContent   = open ? '▼' : '▲';
}

// ── Generate ──────────────────────────────────────────
async function generate() {
  const topic    = document.getElementById('topicInput').value.trim();
  const duration = parseInt(document.getElementById('durationSlider').value);
  const voice    = document.getElementById('voiceSelect').value;
  const visual   = document.getElementById('visualSelect').value;
  const groqKey  = document.getElementById('groqKey').value.trim();
  const pexKey   = document.getElementById('pexelsKey').value.trim();

  // Mostra tela de progresso
  show('progressCard');
  hide('formCard');
  hide('resultCard');
  hide('errorCard');

  document.getElementById('logBox').innerHTML      = '';
  document.getElementById('progressBar').style.width = '0%';
  document.getElementById('progressPct').textContent = '0%';
  document.getElementById('btnGenerate').disabled   = true;

  try {
    // Inicia o job
    const startRes = await fetch('/api/generate', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic:       topic || undefined,
        style:       selectedStyle,
        duration,
        voiceProfile: voice,
        visualStyle:  visual,
        groqApiKey:   groqKey,
        pexelsApiKey: pexKey,
      }),
    });

    if (!startRes.ok) throw new Error('Falha ao iniciar geração');
    const { jobId } = await startRes.json();

    // Escuta progresso via SSE
    await watchProgress(jobId);

  } catch (err) {
    showError(err.message);
  }
}

// ── SSE Progress ──────────────────────────────────────
function watchProgress(jobId) {
  return new Promise((resolve, reject) => {
    const es = new EventSource(`/api/status/${jobId}`);

    es.onmessage = (e) => {
      const data = JSON.parse(e.data);

      if (data.error && !data.status) {
        es.close();
        reject(new Error(data.error));
        return;
      }

      // Update progress bar
      if (data.progress !== undefined) {
        document.getElementById('progressBar').style.width = data.progress + '%';
        document.getElementById('progressPct').textContent = data.progress + '%';
      }

      // Update log
      if (data.log?.length) {
        const box = document.getElementById('logBox');
        box.innerHTML = data.log.map(l => {
          if (l.startsWith('❌')) return `<div class="err">${esc(l)}</div>`;
          if (l.startsWith('⚠️')) return `<div class="warn">${esc(l)}</div>`;
          return `<div>${esc(l)}</div>`;
        }).join('');
        box.scrollTop = box.scrollHeight;
      }

      if (data.status === 'done') {
        es.close();
        showResult(jobId, data.outputPath);
        resolve();
      } else if (data.status === 'error') {
        es.close();
        reject(new Error(data.error || 'Erro desconhecido'));
      }
    };

    es.onerror = () => {
      es.close();
      // Tenta polling manual se SSE falhar
      pollFallback(jobId, resolve, reject);
    };
  });
}

// ── Polling fallback (se SSE não funcionar) ───────────
async function pollFallback(jobId, resolve, reject) {
  for (let i = 0; i < 120; i++) {
    await sleep(2000);
    try {
      const r = await fetch(`/api/status/${jobId}`);
      // SSE endpoint — tenta ler resposta como texto
      const text = await r.text();
      const lines = text.split('\n').filter(l => l.startsWith('data:'));
      if (lines.length) {
        const last = JSON.parse(lines[lines.length - 1].slice(5));
        if (last.status === 'done') { showResult(jobId, last.outputPath); return resolve(); }
        if (last.status === 'error') return reject(new Error(last.error));
        if (last.progress !== undefined) {
          document.getElementById('progressBar').style.width = last.progress + '%';
          document.getElementById('progressPct').textContent = last.progress + '%';
        }
      }
    } catch {}
  }
  reject(new Error('Tempo esgotado. Tente novamente.'));
}

// ── Show result ───────────────────────────────────────
async function showResult(jobId, outputPath) {
  const videoUrl    = `/api/download/${jobId}`;
  const player      = document.getElementById('videoPlayer');
  const btnDownload = document.getElementById('btnDownload');

  player.src         = videoUrl;
  btnDownload.href   = videoUrl;

  show('resultCard');
  hide('progressCard');
  document.getElementById('btnGenerate').disabled = false;
}

// ── Error ─────────────────────────────────────────────
function showError(msg) {
  document.getElementById('errorMsg').textContent = msg;
  show('errorCard');
  hide('progressCard');
  document.getElementById('btnGenerate').disabled = false;
}

// ── New video ─────────────────────────────────────────
function newVideo() {
  hide('resultCard');
  hide('errorCard');
  hide('progressCard');
  show('formCard');
}

// ── Helpers ───────────────────────────────────────────
function show(id) { document.getElementById(id).style.display = 'block'; }
function hide(id) { document.getElementById(id).style.display = 'none'; }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
