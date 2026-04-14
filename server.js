#!/usr/bin/env node
/**
 * Dark Viral Video Generator — Web Server
 * Abre no navegador em http://localhost:3000
 */

'use strict';

const express = require('express');
const fs = require('fs');
const path = require('path');

// Load .env
const envPath = path.join(__dirname, '.env');
if (fs.existsSync(envPath)) require('dotenv').config({ path: envPath });

const ScriptGenerator = require('./modules/scriptGenerator');
const VoiceGenerator  = require('./modules/voiceGenerator');
const MediaFetcher    = require('./modules/mediaFetcher');
const VideoComposer   = require('./modules/videoComposer');
const MusicLibrary    = require('./modules/musicLibrary');

const app  = express();
const PORT = process.env.PORT || 3000;

// ── Diretórios ──────────────────────────────────────────
const OUTPUT_DIR = path.join(__dirname, 'output');
const TEMP_DIR   = path.join(__dirname, 'temp');
[OUTPUT_DIR, TEMP_DIR, path.join(TEMP_DIR, 'media'),
 path.join(__dirname, 'assets/music')].forEach(d => {
  if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
});

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ── Em memória: jobs de geração ─────────────────────────
const jobs = {};   // jobId → { status, progress, log, outputPath, error }

// ── SSE helper ─────────────────────────────────────────
function sendEvent(res, data) {
  res.write(`data: ${JSON.stringify(data)}\n\n`);
}

// ── POST /api/generate ──────────────────────────────────
app.post('/api/generate', async (req, res) => {
  const jobId = `job_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
  jobs[jobId] = { status: 'running', progress: 0, log: [], outputPath: null, error: null };

  res.json({ jobId });

  // Roda em background
  runPipeline(jobId, req.body).catch(err => {
    jobs[jobId].status = 'error';
    jobs[jobId].error  = err.message;
  });
});

// ── GET /api/status/:jobId (SSE) ────────────────────────
app.get('/api/status/:jobId', (req, res) => {
  const { jobId } = req.params;

  res.setHeader('Content-Type',  'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection',    'keep-alive');
  res.flushHeaders();

  const job = jobs[jobId];
  if (!job) { sendEvent(res, { error: 'Job não encontrado' }); return res.end(); }

  // Envia estado atual imediatamente
  sendEvent(res, { progress: job.progress, log: job.log, status: job.status,
                   outputPath: job.outputPath, error: job.error });

  if (job.status !== 'running') return res.end();

  // Observa mudanças via polling simples
  const iv = setInterval(() => {
    const j = jobs[jobId];
    sendEvent(res, { progress: j.progress, log: j.log, status: j.status,
                     outputPath: j.outputPath, error: j.error });
    if (j.status !== 'running') { clearInterval(iv); res.end(); }
  }, 600);

  req.on('close', () => clearInterval(iv));
});

// ── GET /api/download/:jobId ────────────────────────────
app.get('/api/download/:jobId', (req, res) => {
  const job = jobs[req.params.jobId];
  if (!job?.outputPath || !fs.existsSync(job.outputPath))
    return res.status(404).json({ error: 'Vídeo não encontrado' });

  res.download(job.outputPath);
});

// ── GET /api/topics ────────────────────────────────────
app.get('/api/topics', (_req, res) => {
  const gen = new ScriptGenerator({});
  res.json(gen.getViralTopics());
});

// ── Pipeline principal ──────────────────────────────────
async function runPipeline(jobId, options) {
  const job = jobs[jobId];

  function log(msg, progress) {
    job.log.push(msg);
    if (progress !== undefined) job.progress = progress;
    console.log(`[${jobId}] ${msg}`);
  }

  const {
    topic        = 'segredos da mente humana',
    style        = 'dark',
    duration     = 30,
    voiceProfile = 'dark-male',
    visualStyle  = 'cinematic',
    groqApiKey   = process.env.GROQ_API_KEY || '',
    pexelsApiKey = process.env.PEXELS_API_KEY || '',
  } = options;

  // Injeta chaves fornecidas pelo usuário na sessão
  if (groqApiKey)   process.env.GROQ_API_KEY   = groqApiKey;
  if (pexelsApiKey) process.env.PEXELS_API_KEY  = pexelsApiKey;

  try {
    // ── PASSO 1: Roteiro ────────────────────────────────
    log('🧠 Gerando roteiro viral...', 5);
    const scriptGen = new ScriptGenerator({ apiKey: groqApiKey || process.env.GROQ_API_KEY });
    const script    = await scriptGen.generateScript({ topic, style, duration });
    log(`✅ Roteiro: "${script.title}" (${script.segments.length} cenas)`, 20);

    // ── PASSO 2: Voz ────────────────────────────────────
    log('🎙️ Gerando narração...', 25);
    const voiceGen  = new VoiceGenerator({ outputDir: TEMP_DIR });
    let audioFile   = null;
    try {
      const vr  = await voiceGen.generateScriptVoice(script, voiceProfile);
      audioFile = vr.fullAudioFile;
      log('✅ Narração gerada', 45);
    } catch (e) {
      log(`⚠️ Narração indisponível: ${e.message}`, 45);
    }

    // ── PASSO 3: Mídia ──────────────────────────────────
    log('🔍 Buscando imagens para as cenas...', 50);
    const fetcher   = new MediaFetcher({
      pexelsApiKey:  pexelsApiKey || process.env.PEXELS_API_KEY,
      pixabayApiKey: process.env.PIXABAY_API_KEY,
      outputDir:     path.join(TEMP_DIR, 'media'),
    });
    let mediaFiles  = [];
    try {
      const items = await fetcher.fetchMediaForScript(script, false);
      mediaFiles  = await fetcher.downloadAllMedia(items);
      const ok    = mediaFiles.filter(m => m.localPath).length;
      log(`✅ ${ok}/${mediaFiles.length} imagens baixadas`, 65);
    } catch (e) {
      log(`⚠️ Usará backgrounds gerados: ${e.message}`, 65);
    }

    // ── PASSO 4: Música ─────────────────────────────────
    log('🎵 Preparando música dark...', 70);
    const musicLib  = new MusicLibrary({ musicDir: path.join(__dirname, 'assets/music'), tempDir: TEMP_DIR });
    let musicFile   = null;
    try {
      musicFile = await musicLib.getMusicForMood(musicLib.getMoodFromScript(script));
      log('✅ Música pronta', 75);
    } catch (e) {
      log(`⚠️ Sem música: ${e.message}`, 75);
    }

    // ── PASSO 5: Vídeo ──────────────────────────────────
    log('🎬 Montando vídeo final...', 80);
    const composer  = new VideoComposer({ width: 1080, height: 1920, fps: 30, outputDir: OUTPUT_DIR, tempDir: TEMP_DIR });
    const fileName  = `dark_viral_${Date.now()}.mp4`;
    const result    = await composer.compose({
      script, mediaFiles, audioFile,
      outputFileName: fileName,
      visualStyle, musicFile, musicVolume: 0.15,
    });

    job.outputPath = result.outputPath;
    job.progress   = 100;
    job.status     = 'done';
    log(`🎉 Vídeo gerado! (${result.fileSizeMB} MB)`, 100);

  } catch (err) {
    job.status = 'error';
    job.error  = err.message;
    log(`❌ Erro: ${err.message}`);
    throw err;
  }
}

// ── Start ───────────────────────────────────────────────
app.listen(PORT, () => {
  console.log('\n╔══════════════════════════════════════════════╗');
  console.log('║  🎬 DARK VIRAL VIDEO GENERATOR — WEB UI      ║');
  console.log('╚══════════════════════════════════════════════╝');
  console.log(`\n🌐 Abra no navegador: http://localhost:${PORT}`);
  console.log('   (pressione Ctrl+C para parar)\n');
});
