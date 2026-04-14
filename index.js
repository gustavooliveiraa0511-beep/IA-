#!/usr/bin/env node
/**
 * Dark Viral Video Generator
 * ============================================================
 * Gerador automático de vídeos virais estilo "dark TikTok"
 * com máximo foco em retenção e engajamento.
 *
 * 100% GRATUITO — Groq AI (llama-3.3-70b) + Edge TTS + FFmpeg
 * ============================================================
 */

'use strict';

const fs = require('fs');
const path = require('path');
const readline = require('readline');

// Load environment variables
const envPath = path.join(__dirname, '.env');
if (fs.existsSync(envPath)) {
  require('dotenv').config({ path: envPath });
}

// Import modules
const ScriptGenerator = require('./modules/scriptGenerator');
const VoiceGenerator = require('./modules/voiceGenerator');
const MediaFetcher = require('./modules/mediaFetcher');
const VideoComposer = require('./modules/videoComposer');
const MusicLibrary = require('./modules/musicLibrary');

// ANSI colors for beautiful terminal output
const colors = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  magenta: '\x1b[35m',
  cyan: '\x1b[36m',
  white: '\x1b[37m',
  bgDark: '\x1b[40m',
  bgMagenta: '\x1b[45m',
};

const c = (color, text) => `${colors[color]}${text}${colors.reset}`;

/**
 * Print beautiful banner
 */
function printBanner() {
  const banner = `
${c('magenta', '╔══════════════════════════════════════════════════════════════╗')}
${c('magenta', '║')}  ${c('bold', '🎬 DARK VIRAL VIDEO GENERATOR')} ${c('dim', '100% GRATUITO')}              ${c('magenta', '║')}
${c('magenta', '║')}  ${c('dim', 'Gerador Automático de Vídeos Virais Estilo TikTok Dark')}     ${c('magenta', '║')}
${c('magenta', '╚══════════════════════════════════════════════════════════════╝')}
`;
  console.log(banner);
}

/**
 * Print progress step
 */
function step(emoji, message, color = 'cyan') {
  console.log(`\n${c(color, emoji)} ${c('bold', message)}`);
}

/**
 * Print info line
 */
function info(message) {
  console.log(`   ${c('dim', '→')} ${message}`);
}

/**
 * Format duration
 */
function formatDuration(seconds) {
  if (!seconds) return 'N/A';
  return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
}

/**
 * Main video generation pipeline
 */
async function generateVideo(options = {}) {
  const {
    topic = null,
    style = 'dark',
    duration = 30,
    voiceProfile = 'dark-male',
    visualStyle = 'cinematic',
    subtitleStyle = 'tiktok',
    usePexels = true,
    preferVideo = false,
    outputDir = path.join(__dirname, 'output'),
    tempDir = path.join(__dirname, 'temp'),
    language = 'pt-BR',
    dryRun = false,
  } = options;

  // Ensure directories
  [outputDir, tempDir, path.join(__dirname, 'temp/media')].forEach(dir => {
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  });

  const startTime = Date.now();
  const sessionId = Date.now();

  printBanner();

  // ═══════════════════════════════════════════════
  // STEP 1: Generate Script (Groq AI ou templates)
  // ═══════════════════════════════════════════════
  const groqKey = process.env.GROQ_API_KEY;
  const aiMode = groqKey ? 'Groq AI (llama-3.3-70b)' : 'Templates Locais';
  step('🧠', `PASSO 1: Gerando roteiro viral com ${aiMode}...`, 'magenta');

  const scriptGen = new ScriptGenerator({ apiKey: groqKey });
  let script;

  try {
    script = await scriptGen.generateScript({
      topic,
      style,
      duration,
      language,
    });

    info(`Gerado por: ${c('green', script.generatedBy || 'template')}`);
    info(`Título: ${c('white', script.title)}`);
    info(`Gancho: ${c('cyan', script.hook)}`);
    info(`Segmentos: ${script.segments?.length || 0}`);
    info(`Duração: ${formatDuration(script.totalDuration)}`);
    info(`Técnicas de retenção: ${script.retentionTechniques?.join(', ')}`);

    console.log(`\n${c('dim', '─'.repeat(60))}`);
    console.log(c('yellow', '📜 ROTEIRO GERADO:'));
    if (script.segments?.length) {
      console.log(c('white', script.segments.map(s => s.text).join(' ').substring(0, 400) + '...'));
    }

  } catch (err) {
    throw new Error(`Falha ao gerar roteiro: ${err.message}`);
  }

  if (dryRun) {
    console.log('\n' + c('green', '✅ DRY RUN: Roteiro gerado com sucesso!'));
    return { script, dryRun: true };
  }

  // ═══════════════════════════════════════════════
  // STEP 2: Generate Voice Narration
  // ═══════════════════════════════════════════════
  step('🎙️', 'PASSO 2: Gerando narração com voz dark...', 'blue');

  const voiceGen = new VoiceGenerator({
    outputDir: tempDir,
  });

  let voiceResult = null;
  let audioFile = null;

  try {
    voiceResult = await voiceGen.generateScriptVoice(script, voiceProfile);
    audioFile = voiceResult.fullAudioFile;

    if (audioFile && fs.existsSync(audioFile)) {
      info(`✅ Narração gerada: ${path.basename(audioFile)}`);
      info(`Perfil de voz: ${voiceResult.profile.description}`);
    } else {
      info('⚠️  Narração não disponível - vídeo será gerado sem narração');
    }
  } catch (err) {
    console.error(`   ⚠️  Erro na narração: ${err.message}`);
    info('Continuando sem narração...');
  }

  // ═══════════════════════════════════════════════
  // STEP 3: Fetch Visual Media
  // ═══════════════════════════════════════════════
  step('🔍', 'PASSO 3: Buscando imagens e vídeos para cada cena...', 'cyan');

  const mediaFetcher = new MediaFetcher({
    pexelsApiKey: process.env.PEXELS_API_KEY,
    pixabayApiKey: process.env.PIXABAY_API_KEY,
    outputDir: path.join(tempDir, 'media'),
  });

  let mediaFiles = [];

  try {
    const mediaItems = await mediaFetcher.fetchMediaForScript(script, preferVideo);
    mediaFiles = await mediaFetcher.downloadAllMedia(mediaItems);

    const downloaded = mediaFiles.filter(m => m.localPath).length;
    info(`✅ ${downloaded}/${mediaFiles.length} arquivos de mídia baixados`);
  } catch (err) {
    console.error(`   ⚠️  Erro na busca de mídia: ${err.message}`);
    info('Continuando com backgrounds gerados...');
  }

  // ═══════════════════════════════════════════════
  // STEP 4: Get Background Music
  // ═══════════════════════════════════════════════
  step('🎵', 'PASSO 4: Preparando música de fundo dark...', 'yellow');

  const musicLib = new MusicLibrary({
    musicDir: path.join(__dirname, 'assets/music'),
    tempDir,
  });

  let musicFile = null;

  try {
    const mood = musicLib.getMoodFromScript(script);
    musicFile = await musicLib.getMusicForMood(mood);
    if (musicFile && fs.existsSync(musicFile)) {
      info(`✅ Música: ${path.basename(musicFile)} (mood: ${mood})`);
    }
  } catch (err) {
    console.error(`   ⚠️  Erro na música: ${err.message}`);
  }

  // ═══════════════════════════════════════════════
  // STEP 5: Compose Final Video
  // ═══════════════════════════════════════════════
  step('🎬', 'PASSO 5: Compondo vídeo final...', 'magenta');

  info(`Formato: 1080x1920 (9:16 vertical)`);
  info(`Estilo visual: ${visualStyle}`);
  info(`Legendas: TikTok-style dinâmicas`);
  info(`Efeito Ken Burns: ativo`);
  info(`Filtros dark: ${visualStyle}`);

  const videoComposer = new VideoComposer({
    width: 1080,
    height: 1920,
    fps: 30,
    outputDir,
    tempDir,
  });

  const outputFileName = `dark_viral_${script.topic?.substring(0, 20).replace(/\s+/g, '_')}_${sessionId}.mp4`;

  let result;
  try {
    result = await videoComposer.compose({
      script,
      mediaFiles,
      audioFile,
      subtitleData: voiceResult?.segments,
      outputFileName,
      visualStyle,
      musicFile,
      musicVolume: 0.15,
    });

    // ═══════════════════════════════════════════════
    // SUCCESS REPORT
    // ═══════════════════════════════════════════════
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

    console.log(`\n${c('green', '╔══════════════════════════════════════════════════╗')}`);
    console.log(`${c('green', '║')} ${c('bold', '✅ VÍDEO GERADO COM SUCESSO!')}                     ${c('green', '║')}`);
    console.log(`${c('green', '╚══════════════════════════════════════════════════╝')}`);

    console.log(`\n${c('bold', '📊 RELATÓRIO FINAL:')}`);
    console.log(`   ${c('cyan', '→')} Arquivo: ${c('white', result.outputPath)}`);
    console.log(`   ${c('cyan', '→')} Tamanho: ${c('yellow', result.fileSizeMB + ' MB')}`);
    console.log(`   ${c('cyan', '→')} Tempo total: ${c('yellow', elapsed + 's')}`);
    console.log(`   ${c('cyan', '→')} Formato: ${c('white', '1080x1920 | 9:16 | 30fps')}`);
    console.log(`\n${c('bold', '📱 PRONTO PARA POSTAR:')}`);
    console.log(`   ${c('green', '✓')} TikTok`);
    console.log(`   ${c('green', '✓')} Instagram Reels`);
    console.log(`   ${c('green', '✓')} YouTube Shorts`);

    if (script.hashtags?.length) {
      console.log(`\n${c('bold', '🏷️  HASHTAGS SUGERIDAS:')}`);
      console.log(`   ${c('dim', script.hashtags.join(' '))}`);
    }

    if (script.callToAction) {
      console.log(`\n${c('bold', '💬 CALL TO ACTION:')}`);
      console.log(`   ${c('yellow', script.callToAction)}`);
    }

  } catch (err) {
    throw new Error(`Falha na composição: ${err.message}`);
  }

  // Save metadata
  const metadataPath = path.join(outputDir, `metadata_${sessionId}.json`);
  fs.writeFileSync(metadataPath, JSON.stringify({
    script,
    outputFile: result.outputPath,
    options: {
      style,
      duration,
      voiceProfile,
      visualStyle,
      subtitleStyle,
    },
    generatedAt: new Date().toISOString(),
    processingTime: (Date.now() - startTime) / 1000,
  }, null, 2));

  return result;
}

/**
 * Interactive CLI Menu
 */
async function interactiveCLI() {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  const question = (q) => new Promise(resolve => rl.question(q, resolve));

  printBanner();

  console.log(c('cyan', '🎯 Bem-vindo ao Gerador de Vídeos Virais Dark!\n'));

  // Check Groq API key (optional)
  if (!process.env.GROQ_API_KEY) {
    console.log(c('yellow', '💡 Dica: sem GROQ_API_KEY o app usa templates locais.'));
    console.log(c('dim', '   Para IA grátis, cadastre-se em console.groq.com\n'));
    const key = await question('   Cole sua Groq API key (ou Enter para usar templates): ');
    if (key.trim()) {
      process.env.GROQ_API_KEY = key.trim();
    }
  }

  // Topic selection
  console.log(c('bold', '\n📋 OPÇÕES DE TEMA:'));
  console.log('   1. Gerar tema automático (recomendado)');
  console.log('   2. Digitar meu próprio tema');

  const topicChoice = await question('\n   Escolha (1-2): ');

  let topic = null;
  if (topicChoice.trim() === '2') {
    topic = await question('   Digite o tema do vídeo: ');
  }

  // Style selection
  console.log(c('bold', '\n🎭 ESTILO DO VÍDEO:'));
  console.log('   1. Dark (misterioso e sombrio) ← recomendado');
  console.log('   2. Emotional (emocionante e tocante)');
  console.log('   3. Informative (revelador e educativo)');

  const styleChoice = await question('\n   Escolha (1-3): ');
  const styleMap = { '1': 'dark', '2': 'emotional', '3': 'informative' };
  const style = styleMap[styleChoice.trim()] || 'dark';

  // Duration
  console.log(c('bold', '\n⏱️  DURAÇÃO:'));
  console.log('   1. 15 segundos (máximo engajamento)');
  console.log('   2. 30 segundos (recomendado)');
  console.log('   3. 45 segundos');
  console.log('   4. 60 segundos');

  const durationChoice = await question('\n   Escolha (1-4): ');
  const durationMap = { '1': 15, '2': 30, '3': 45, '4': 60 };
  const duration = durationMap[durationChoice.trim()] || 30;

  // Voice
  console.log(c('bold', '\n🎙️  VOZ:'));
  console.log('   1. Dark Male (grave e misterioso) ← recomendado');
  console.log('   2. Dark Female (sussurrante e intensa)');
  console.log('   3. Narrator (dramático)');

  const voiceChoice = await question('\n   Escolha (1-3): ');
  const voiceMap = { '1': 'dark-male', '2': 'dark-female', '3': 'narrator' };
  const voiceProfile = voiceMap[voiceChoice.trim()] || 'dark-male';

  rl.close();

  console.log('\n' + c('yellow', '━'.repeat(60)));
  console.log(c('bold', '🚀 INICIANDO GERAÇÃO DO VÍDEO...'));
  console.log(c('yellow', '━'.repeat(60)));

  return generateVideo({
    topic: topic?.trim() || null,
    style,
    duration,
    voiceProfile,
    visualStyle: 'cinematic',
    subtitleStyle: 'tiktok',
  });
}

/**
 * Command line argument parsing
 */
function parseArgs() {
  const args = process.argv.slice(2);
  const options = {};

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--topic': options.topic = args[++i]; break;
      case '--style': options.style = args[++i]; break;
      case '--duration': options.duration = parseInt(args[++i]); break;
      case '--voice': options.voiceProfile = args[++i]; break;
      case '--visual': options.visualStyle = args[++i]; break;
      case '--dry-run': options.dryRun = true; break;
      case '--help': printHelp(); process.exit(0); break;
    }
  }

  return options;
}

function printHelp() {
  printBanner();
  console.log(c('bold', 'USO:'));
  console.log('   node index.js [opções]\n');
  console.log(c('bold', 'OPÇÕES:'));
  console.log('   --topic <tema>     Tema do vídeo (default: automático)');
  console.log('   --style <estilo>   dark | emotional | informative');
  console.log('   --duration <seg>   Duração em segundos (15-60)');
  console.log('   --voice <perfil>   dark-male | dark-female | narrator');
  console.log('   --visual <estilo>  cinematic | moody | horror | subtle');
  console.log('   --dry-run          Apenas gera o roteiro, sem o vídeo');
  console.log('   --help             Exibe esta ajuda\n');
  console.log(c('bold', 'EXEMPLOS:'));
  console.log('   node index.js');
  console.log('   node index.js --topic "segredos da mente humana" --duration 30');
  console.log('   node index.js --style emotional --voice dark-female\n');
  console.log(c('bold', 'VARIÁVEIS DE AMBIENTE (.env) — TODAS OPCIONAIS:'));
  console.log('   GROQ_API_KEY=gsk_...            (grátis em console.groq.com — IA para roteiros)');
  console.log('   PEXELS_API_KEY=...              (grátis em pexels.com/api — melhora mídia)');
  console.log('   PIXABAY_API_KEY=...             (grátis em pixabay.com — mídia alternativa)');
  console.log(c('dim', '\n   Sem nenhuma chave: o app funciona 100% offline com templates!\n'));
}

// ═══════════════════════════════════════════════
// ENTRY POINT
// ═══════════════════════════════════════════════
async function main() {
  try {
    const args = parseArgs();

    // If CLI args provided, use them directly
    if (Object.keys(args).length > 0 && args.topic !== undefined) {
      await generateVideo(args);
    } else if (process.argv[2] === '--help') {
      printHelp();
    } else if (process.argv.length > 2) {
      // Some args but no topic - use args with defaults
      await generateVideo(args);
    } else {
      // Interactive mode
      await interactiveCLI();
    }
  } catch (err) {
    console.error('\n' + c('red', '❌ ERRO:'), err.message);
    if (process.env.DEBUG) {
      console.error(err.stack);
    }
    process.exit(1);
  }
}

main();
