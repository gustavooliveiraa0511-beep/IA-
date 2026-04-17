#!/usr/bin/env node
/**
 * Dark Viral Video Generator — Web Server (with Auth + DB)
 */

'use strict';

const express = require('express');
const fs      = require('fs');
const path    = require('path');

// Load .env
const envPath = path.join(__dirname, '.env');
if (fs.existsSync(envPath)) require('dotenv').config({ path: envPath });

const ScriptGenerator = require('./modules/scriptGenerator');
const VoiceGenerator  = require('./modules/voiceGenerator');
const MediaFetcher    = require('./modules/mediaFetcher');
const VideoComposer   = require('./modules/videoComposer');
const MusicLibrary    = require('./modules/musicLibrary');
const db              = require('./modules/database');
const { authMiddleware, hashPassword, comparePassword, generateToken } = require('./modules/authManager');

const app  = express();
const PORT = process.env.PORT || 3000;

// ── Directories ──────────────────────────────────────
const OUTPUT_DIR = path.join(__dirname, 'output');
const TEMP_DIR   = path.join(__dirname, 'temp');
[OUTPUT_DIR, TEMP_DIR, path.join(TEMP_DIR, 'media'),
 path.join(__dirname, 'assets/music')].forEach(d => {
  if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
});

// ── Middleware ───────────────────────────────────────
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// CORS for same-origin (allow credentials via Authorization header)
app.use((req, res, next) => {
  const origin = req.headers.origin || '';
  // Allow same origin or no origin (direct requests)
  res.setHeader('Access-Control-Allow-Origin', origin || '*');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

// ── In-memory jobs store ─────────────────────────────
const jobs = {};   // jobId → { status, progress, log, outputPath, error, dbVideoId, userId }

// ── SSE helper ───────────────────────────────────────
function sendEvent(res, data) {
  res.write(`data: ${JSON.stringify(data)}\n\n`);
}

// ── Email validator ──────────────────────────────────
function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

// ════════════════════════════════════════════════════
//  AUTH ROUTES
// ════════════════════════════════════════════════════

// POST /api/auth/register
app.post('/api/auth/register', async (req, res) => {
  try {
    const { email, password } = req.body || {};

    if (!email || !password) {
      return res.status(400).json({ error: 'Email e senha são obrigatórios' });
    }
    if (!isValidEmail(email)) {
      return res.status(400).json({ error: 'Formato de email inválido' });
    }
    if (password.length < 6) {
      return res.status(400).json({ error: 'A senha deve ter no mínimo 6 caracteres' });
    }

    // Check if email already exists
    const existing = db.getUserByEmail(email);
    if (existing) {
      return res.status(409).json({ error: 'Email já cadastrado' });
    }

    const hash = await hashPassword(password);
    const user = db.createUser(email, hash);
    const token = generateToken(user.id);

    const quota = db.canGenerateVideo(user.id);

    res.status(201).json({
      token,
      user: {
        id:    user.id,
        email: user.email,
        plan:  user.plan,
        videosUsed:  quota.current,
        videosLimit: quota.limit,
      },
    });
  } catch (err) {
    console.error('Register error:', err);
    res.status(500).json({ error: 'Erro interno ao criar conta' });
  }
});

// POST /api/auth/login
app.post('/api/auth/login', async (req, res) => {
  try {
    const { email, password } = req.body || {};

    if (!email || !password) {
      return res.status(400).json({ error: 'Email e senha são obrigatórios' });
    }

    const user = db.getUserByEmail(email);
    if (!user) {
      return res.status(401).json({ error: 'Credenciais inválidas' });
    }

    const valid = await comparePassword(password, user.password_hash);
    if (!valid) {
      return res.status(401).json({ error: 'Credenciais inválidas' });
    }

    const token = generateToken(user.id);
    const quota = db.canGenerateVideo(user.id);

    res.json({
      token,
      user: {
        id:    user.id,
        email: user.email,
        plan:  user.plan,
        videosUsed:  quota.current,
        videosLimit: quota.limit,
      },
    });
  } catch (err) {
    console.error('Login error:', err);
    res.status(500).json({ error: 'Erro interno ao fazer login' });
  }
});

// GET /api/auth/me — (authenticated)
app.get('/api/auth/me', authMiddleware, (req, res) => {
  try {
    const user = db.getUserById(req.userId);
    if (!user) return res.status(404).json({ error: 'Usuário não encontrado' });

    const quota      = db.canGenerateVideo(user.id);
    const videoCount = db.getUserVideos(user.id).length;

    res.json({
      id:          user.id,
      email:       user.email,
      plan:        user.plan,
      videoCount,
      videosUsed:  quota.current,
      videosLimit: quota.limit,
    });
  } catch (err) {
    console.error('Me error:', err);
    res.status(500).json({ error: 'Erro interno' });
  }
});

// ════════════════════════════════════════════════════
//  VIDEO ROUTES (all require auth)
// ════════════════════════════════════════════════════

// POST /api/generate
app.post('/api/generate', authMiddleware, async (req, res) => {
  try {
    const userId = req.userId;

    // Check quota
    const quota = db.canGenerateVideo(userId);
    if (!quota.allowed) {
      return res.status(429).json({
        error: `Limite mensal atingido (${quota.current}/${quota.limit} vídeos). Faça upgrade para o plano Pro.`,
        quota,
      });
    }

    const jobId = `job_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;

    // Create DB record with status 'processing'
    const { id: dbVideoId } = db.saveVideo(userId, {
      title:  req.body.topic || 'Vídeo dark viral',
      topic:  req.body.topic || '',
      status: 'processing',
    });

    jobs[jobId] = {
      status: 'running',
      progress: 0,
      log: [],
      outputPath: null,
      error: null,
      dbVideoId,
      userId,
    };

    res.json({ jobId, dbVideoId });

    // Run pipeline in background
    runPipeline(jobId, req.body, userId).catch(err => {
      jobs[jobId].status = 'error';
      jobs[jobId].error  = err.message;
    });
  } catch (err) {
    console.error('Generate error:', err);
    res.status(500).json({ error: 'Erro interno ao iniciar geração' });
  }
});

// GET /api/status/:jobId — SSE endpoint
app.get('/api/status/:jobId', authMiddleware, (req, res) => {
  const { jobId } = req.params;

  res.setHeader('Content-Type',  'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection',    'keep-alive');
  res.flushHeaders();

  const job = jobs[jobId];
  if (!job) {
    sendEvent(res, { error: 'Job não encontrado' });
    return res.end();
  }

  // Verify ownership
  if (job.userId !== req.userId) {
    sendEvent(res, { error: 'Acesso negado' });
    return res.end();
  }

  // Send current state immediately
  sendEvent(res, {
    progress:   job.progress,
    log:        job.log,
    status:     job.status,
    outputPath: job.outputPath,
    error:      job.error,
    dbVideoId:  job.dbVideoId,
  });

  if (job.status !== 'running') return res.end();

  // Poll for updates
  const iv = setInterval(() => {
    const j = jobs[jobId];
    sendEvent(res, {
      progress:   j.progress,
      log:        j.log,
      status:     j.status,
      outputPath: j.outputPath,
      error:      j.error,
      dbVideoId:  j.dbVideoId,
    });
    if (j.status !== 'running') { clearInterval(iv); res.end(); }
  }, 600);

  req.on('close', () => clearInterval(iv));
});

// GET /api/download/:jobId
app.get('/api/download/:jobId', authMiddleware, (req, res) => {
  const job = jobs[req.params.jobId];
  if (!job) return res.status(404).json({ error: 'Job não encontrado' });
  if (job.userId !== req.userId) return res.status(403).json({ error: 'Acesso negado' });
  if (!job.outputPath || !fs.existsSync(job.outputPath)) {
    return res.status(404).json({ error: 'Vídeo não encontrado' });
  }
  res.download(job.outputPath);
});

// GET /api/videos — user's video history
app.get('/api/videos', authMiddleware, (req, res) => {
  try {
    const videos = db.getUserVideos(req.userId);
    res.json(videos);
  } catch (err) {
    console.error('Videos list error:', err);
    res.status(500).json({ error: 'Erro ao buscar vídeos' });
  }
});

// DELETE /api/videos/:videoId
app.delete('/api/videos/:videoId', authMiddleware, (req, res) => {
  try {
    const { videoId } = req.params;
    const video = db.getVideoById(videoId);

    if (!video) return res.status(404).json({ error: 'Vídeo não encontrado' });
    if (video.user_id !== req.userId) return res.status(403).json({ error: 'Acesso negado' });

    // Delete file from disk if it exists
    if (video.file_path && fs.existsSync(video.file_path)) {
      try { fs.unlinkSync(video.file_path); } catch (e) { console.warn('Could not delete file:', e.message); }
    }

    db.deleteVideo(videoId);
    res.json({ success: true });
  } catch (err) {
    console.error('Delete video error:', err);
    res.status(500).json({ error: 'Erro ao deletar vídeo' });
  }
});

// ════════════════════════════════════════════════════
//  PUBLIC ROUTES
// ════════════════════════════════════════════════════

// GET /api/topics
app.get('/api/topics', (_req, res) => {
  try {
    const gen = new ScriptGenerator({});
    res.json(gen.getViralTopics());
  } catch (err) {
    res.json([
      'segredos da mente humana',
      'fatos que a mídia esconde',
      'o lado sombrio da história',
      'mistérios não resolvidos',
      'experimentos proibidos',
      'verdades sobre o poder',
    ]);
  }
});

// GET / — landing page
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// GET /app — dashboard (auth handled client-side)
app.get('/app', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'app.html'));
});

// ════════════════════════════════════════════════════
//  PIPELINE
// ════════════════════════════════════════════════════

async function runPipeline(jobId, options, userId) {
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

  // Inject user-supplied keys
  if (groqApiKey)   process.env.GROQ_API_KEY   = groqApiKey;
  if (pexelsApiKey) process.env.PEXELS_API_KEY  = pexelsApiKey;

  try {
    // Check quota again before starting heavy work
    const quota = db.canGenerateVideo(userId);
    if (!quota.allowed) {
      throw new Error(`Limite mensal atingido (${quota.current}/${quota.limit} vídeos)`);
    }

    // ── STEP 1: Script ──────────────────────────────
    log('🧠 Gerando roteiro viral...', 5);
    const scriptGen = new ScriptGenerator({ apiKey: groqApiKey || process.env.GROQ_API_KEY });
    const script    = await scriptGen.generateScript({ topic, style, duration });
    log(`✅ Roteiro: "${script.title}" (${script.segments.length} cenas)`, 20);

    // ── STEP 2: Voice ───────────────────────────────
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

    // ── STEP 3: Media ───────────────────────────────
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

    // ── STEP 4: Music ───────────────────────────────
    log('🎵 Preparando música dark...', 70);
    const musicLib  = new MusicLibrary({ musicDir: path.join(__dirname, 'assets/music'), tempDir: TEMP_DIR });
    let musicFile   = null;
    try {
      musicFile = await musicLib.getMusicForMood(musicLib.getMoodFromScript(script));
      log('✅ Música pronta', 75);
    } catch (e) {
      log(`⚠️ Sem música: ${e.message}`, 75);
    }

    // ── STEP 5: Video ───────────────────────────────
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

    // Persist to DB
    const fileSize = fs.existsSync(result.outputPath)
      ? fs.statSync(result.outputPath).size
      : null;

    db.updateVideo(job.dbVideoId, {
      title:    script.title || topic,
      topic,
      filePath: result.outputPath,
      fileSize,
      status:   'done',
    });

    db.incrementVideoCount(userId);

  } catch (err) {
    job.status = 'error';
    job.error  = err.message;
    log(`❌ Erro: ${err.message}`);

    // Update DB record to error state
    if (job.dbVideoId) {
      try { db.updateVideo(job.dbVideoId, { status: 'error' }); } catch (_) {}
    }

    throw err;
  }
}

// ── Start ────────────────────────────────────────────
app.listen(PORT, () => {
  console.log('\n╔══════════════════════════════════════════════╗');
  console.log('║  🎬 DARK VIRAL VIDEO GENERATOR — WEB UI      ║');
  console.log('╚══════════════════════════════════════════════╝');
  console.log(`\n🌐 Abra no navegador: http://localhost:${PORT}`);
  console.log('   (pressione Ctrl+C para parar)\n');
});
