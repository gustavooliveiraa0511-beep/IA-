/**
 * FX Engine Module
 * Generates sound effects and visual flash frames for viral fast-cut videos.
 * All FFmpeg commands are constrained to -threads 2 / ultrafast / crf 28 / 720x1280
 * so they fit within the Railway 512 MB RAM envelope.
 */

'use strict';

const { execSync } = require('child_process');
const fs   = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Run a shell command, bubbling up a clean error on failure.
 * @param {string} cmd
 * @param {number} [timeoutMs=30000]
 */
function run(cmd, timeoutMs = 30000) {
  try {
    execSync(cmd, { stdio: 'pipe', timeout: timeoutMs });
  } catch (err) {
    const stderr = err.stderr ? err.stderr.toString().slice(0, 400) : '';
    throw new Error(`FFmpeg command failed: ${stderr || err.message}`);
  }
}

/**
 * Ensure the directory that will hold `filePath` exists.
 * @param {string} filePath
 */
function ensureDir(filePath) {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Generate a 0.3-second "whoosh" sound effect.
 * Uses a sine sweep from 2 000 Hz down to 200 Hz so it sounds like a fast
 * camera or cut whip.
 *
 * @param {string} outputPath  - Destination .mp3 / .aac / .wav path
 * @param {string} ffmpegPath  - Path to the ffmpeg binary
 */
function generateWhoosh(outputPath, ffmpegPath) {
  ensureDir(outputPath);

  // sine sweep: freq linearly interpolates from 2000 → 200 over 0.3 s
  // volume envelope: short fade-out so the tail doesn't click
  const cmd = [
    `"${ffmpegPath}"`,
    '-threads', '2',
    '-f', 'lavfi',
    '-i', '"sine=frequency=2000:beep_factor=1:sample_rate=44100"',
    '-af', '"aeval=\'sin(2*PI*(2000-(2000-200)*t/0.3)*t)\'|same:c=stereo,afade=t=out:st=0.15:d=0.15,volume=0.6"',
    '-t', '0.3',
    '-ar', '44100',
    '-ac', '2',
    '-b:a', '128k',
    `"${outputPath}"`,
    '-y',
  ].join(' ');

  // Fallback: aeval can be finicky on some builds — use a simpler sine filter
  // with a hand-picked frequency as the "whoosh" approximation.
  try {
    const cmdSimple = [
      `"${ffmpegPath}"`,
      '-threads', '2',
      '-f', 'lavfi',
      '-i', '"sine=frequency=800:sample_rate=44100"',
      '-af', '"afade=t=out:st=0.0:d=0.3,volume=0.5"',
      '-t', '0.3',
      '-ar', '44100',
      '-ac', '2',
      '-b:a', '128k',
      `"${outputPath}"`,
      '-y',
    ].join(' ');
    run(cmdSimple);
  } catch (_) {
    run(cmd); // try the sweep version as a last resort
  }
}

/**
 * Generate a 0.2-second "impact" sound effect.
 * A 100 Hz sine burst with a very fast amplitude decay (punchy low thud).
 *
 * @param {string} outputPath  - Destination audio path
 * @param {string} ffmpegPath  - Path to the ffmpeg binary
 */
function generateImpact(outputPath, ffmpegPath) {
  ensureDir(outputPath);

  // 100 Hz tone with exponential fade-out — mimics a kick/impact hit
  const cmd = [
    `"${ffmpegPath}"`,
    '-threads', '2',
    '-f', 'lavfi',
    '-i', '"sine=frequency=100:sample_rate=44100"',
    '-af', '"afade=t=out:st=0.0:d=0.2,volume=0.9"',
    '-t', '0.2',
    '-ar', '44100',
    '-ac', '2',
    '-b:a', '128k',
    `"${outputPath}"`,
    '-y',
  ].join(' ');

  run(cmd);
}

/**
 * Generate a single-color video frame that lasts 0.08 seconds.
 * Used as the "flash frame" between fast cuts (white flash, red flash, etc.).
 *
 * @param {string} outputPath  - Destination .mp4 path
 * @param {string} ffmpegPath  - Path to the ffmpeg binary
 * @param {number} [width=720]
 * @param {number} [height=1280]
 * @param {string} [color='white']  - Any color accepted by FFmpeg (white, red, yellow, 0xFFFFFF…)
 */
function generateFlashFrame(outputPath, ffmpegPath, width = 720, height = 1280, color = 'white') {
  ensureDir(outputPath);

  const safeColor = color || 'white';
  const duration  = '0.08';

  const cmd = [
    `"${ffmpegPath}"`,
    '-threads', '2',
    '-f', 'lavfi',
    '-i', `"color=c=${safeColor}:s=${width}x${height}:r=24"`,
    '-t', duration,
    '-c:v', 'libx264',
    '-preset', 'ultrafast',
    '-crf', '28',
    '-pix_fmt', 'yuv420p',
    '-an',
    `"${outputPath}"`,
    '-y',
  ].join(' ');

  run(cmd);
}

/**
 * Mix pre-generated sound effects into a video at specific timestamps.
 *
 * `effectTimings` is an array of:
 *   { file: '/path/to/effect.mp3', timeMs: 1500 }   // timeMs = milliseconds from start
 *
 * Implementation strategy:
 *   1. Extract audio track from the source video.
 *   2. Build an FFmpeg filter_complex that adelay-shifts each effect to the
 *      correct offset and then amix everything together.
 *   3. Re-mux the mixed audio back onto the original video stream (copy codec).
 *
 * @param {string}   videoPath      - Source video (must already have an audio track, or be silent)
 * @param {Array}    effectTimings  - Array of { file, timeMs }
 * @param {string}   outputPath     - Destination video path
 * @param {string}   ffmpegPath     - Path to the ffmpeg binary
 * @param {string}   [threads='2']
 */
function addSoundEffectsToVideo(videoPath, effectTimings, outputPath, ffmpegPath, threads = '2') {
  ensureDir(outputPath);

  if (!effectTimings || effectTimings.length === 0) {
    // Nothing to do — just copy the source
    const cmd = [
      `"${ffmpegPath}"`,
      '-threads', threads,
      '-i', `"${videoPath}"`,
      '-c', 'copy',
      `"${outputPath}"`,
      '-y',
    ].join(' ');
    run(cmd);
    return;
  }

  // Build inputs list: [0] = source video, [1..N] = effect files
  const inputs = [`-i "${videoPath}"`];
  effectTimings.forEach(e => {
    inputs.push(`-i "${e.file}"`);
  });

  // Build filter_complex
  // • [0:a] is the original audio (may not exist; use aevalsrc=0 as silence fallback handled below)
  // • Each effect [N:a] gets adelay applied in milliseconds
  // • Everything goes into amix

  const filterParts = [];
  // Check whether the source video has an audio stream
  let hasSourceAudio = true;
  try {
    const probe = execSync(
      `"${ffmpegPath}" -i "${videoPath}" -hide_banner 2>&1 | grep "Audio"`,
      { encoding: 'utf8', stdio: 'pipe' }
    );
    if (!probe.trim()) hasSourceAudio = false;
  } catch (_) {
    // grep exits non-zero when no match — treat as no audio
    hasSourceAudio = false;
  }

  const mixInputs = [];

  if (hasSourceAudio) {
    filterParts.push('[0:a]volume=1.0[origaudio]');
    mixInputs.push('[origaudio]');
  } else {
    // Generate silence the same length as the video using aevalsrc
    filterParts.push('aevalsrc=0:c=stereo:s=44100[silence]');
    mixInputs.push('[silence]');
  }

  effectTimings.forEach((e, idx) => {
    const inputIdx = idx + 1; // 0 is video
    const delayMs  = Math.max(0, Math.round(e.timeMs));
    const label    = `fx${idx}`;
    filterParts.push(`[${inputIdx}:a]adelay=${delayMs}|${delayMs},volume=0.8[${label}]`);
    mixInputs.push(`[${label}]`);
  });

  const numInputs = mixInputs.length;
  filterParts.push(
    `${mixInputs.join('')}amix=inputs=${numInputs}:duration=first:dropout_transition=1[mixout]`
  );

  const filterComplex = filterParts.join(';');

  const cmd = [
    `"${ffmpegPath}"`,
    '-threads', threads,
    ...inputs,
    '-filter_complex', `"${filterComplex}"`,
    '-map', '0:v',
    '-map', '"[mixout]"',
    '-c:v', 'copy',
    '-c:a', 'aac',
    '-b:a', '192k',
    '-preset', 'ultrafast',
    '-shortest',
    `"${outputPath}"`,
    '-y',
  ].join(' ');

  run(cmd, 120000);
}

// ---------------------------------------------------------------------------
module.exports = {
  generateWhoosh,
  generateImpact,
  generateFlashFrame,
  addSoundEffectsToVideo,
};
