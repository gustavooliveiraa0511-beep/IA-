/**
 * Video Composer Module
 * Assembles script segments into a final vertical viral video.
 *
 * Pipeline:
 *   1. QuickCuts breaks each segment into 1.5–2 s micro-clips.
 *   2. Every 3rd clip gets a flash frame inserted (QuickCuts.addFlashFrame).
 *   3. All clips are concatenated via FFmpeg concat demuxer.
 *   4. A light dark filter (curves + vignette) is applied.
 *   5. ASS subtitles are burned in via subtitleRenderer.js.
 *   6. Whoosh sound effects (FxEngine) are placed at each cut timestamp.
 *   7. Final audio mix: narration + music + SFX.
 *
 * Memory budget: Railway free tier ~512 MB.
 * All FFmpeg calls use -threads 2 / ultrafast / crf 28 / 720x1280.
 */

'use strict';

const { createCanvas }   = require('canvas');
const { execSync }       = require('child_process');
const fs                 = require('fs');
const path               = require('path');
const { QuickCuts }      = require('./quickCuts');
const FxEngine           = require('./fxEngine');

class VideoComposer {
  /**
   * @param {object} [options]
   * @param {number} [options.width]
   * @param {number} [options.height]
   * @param {number} [options.fps]
   * @param {string} [options.outputDir]
   * @param {string} [options.tempDir]
   */
  constructor(options = {}) {
    const isLowMem = process.env.RAILWAY_ENVIRONMENT || process.env.LOW_MEMORY;

    this.width          = options.width     || (isLowMem ? 720  : 1080);
    this.height         = options.height    || (isLowMem ? 1280 : 1920);
    this.fps            = options.fps       || (isLowMem ? 24   : 30);
    this.ffmpegThreads  = isLowMem ? '2' : '0';
    this.outputDir      = options.outputDir || path.join(__dirname, '../output');
    this.tempDir        = options.tempDir   || path.join(__dirname, '../temp');
    this.ffmpegPath     = this.findFFmpeg();

    this._ensureDirs();
  }

  // ---------------------------------------------------------------------------
  // FFmpeg helpers
  // ---------------------------------------------------------------------------

  /**
   * Locate the FFmpeg binary.
   * Order: `which ffmpeg` → common paths → ffmpeg-static npm package.
   *
   * @returns {string} Absolute path (or bare 'ffmpeg' as last resort)
   */
  findFFmpeg() {
    // 1. PATH lookup
    try {
      const result = execSync('which ffmpeg', { encoding: 'utf8', stdio: 'pipe' }).trim();
      if (result) return result;
    } catch (_) {}

    // 2. Common install locations
    const commonPaths = [
      '/usr/bin/ffmpeg',
      '/usr/local/bin/ffmpeg',
      '/opt/homebrew/bin/ffmpeg',
      '/snap/bin/ffmpeg',
    ];
    for (const p of commonPaths) {
      if (fs.existsSync(p)) return p;
    }

    // 3. ffmpeg-static npm package
    try {
      const ffmpegStatic = require('ffmpeg-static');
      if (ffmpegStatic) return ffmpegStatic;
    } catch (_) {}

    console.warn('VideoComposer: FFmpeg not found — falling back to bare "ffmpeg" in PATH.');
    return 'ffmpeg';
  }

  /**
   * Returns the base FFmpeg invocation array: [binary, '-threads', N]
   * Spread this at the start of every execSync command string.
   *
   * @returns {string[]}
   */
  get ff() {
    return [this.ffmpegPath, '-threads', this.ffmpegThreads];
  }

  // ---------------------------------------------------------------------------
  // Main pipeline
  // ---------------------------------------------------------------------------

  /**
   * Compose a final video from a script + media assets.
   *
   * @param {object}   options
   * @param {object}   options.script         - Parsed script: { title, hook, segments[] }
   * @param {Array}    [options.mediaFiles]   - Per-segment media: [{ localPath, type }]
   * @param {string}   [options.audioFile]    - Narrator audio path
   * @param {string}   [options.outputFileName]
   * @param {string}   [options.visualStyle]  - Unused visually (filter is fixed); kept for compat
   * @param {string}   [options.musicFile]    - Background music path
   * @param {number}   [options.musicVolume]  - Music volume 0–1 (default 0.15)
   *
   * @returns {Promise<{ success: boolean, outputPath: string, fileSizeMB: string, fallback?: boolean }>}
   */
  async compose(options) {
    const {
      script,
      mediaFiles    = [],
      audioFile,
      outputFileName,
      musicFile     = null,
      musicVolume   = 0.15,
    } = options;

    const sessionId  = Date.now();
    const outputPath = path.join(
      this.outputDir,
      outputFileName || `video_${sessionId}.mp4`
    );

    console.log('\nVideoComposer: starting pipeline...');
    console.log(`  Resolution : ${this.width}x${this.height}`);
    console.log(`  FPS        : ${this.fps}`);
    console.log(`  Output     : ${outputPath}`);

    try {
      // ── Step a: Break each segment into micro-clips ──────────────────────
      const quickCuts = new QuickCuts({
        ffmpegPath   : this.ffmpegPath,
        width        : this.width,
        height       : this.height,
        fps          : this.fps,
        tempDir      : this.tempDir,
        ffmpegThreads: this.ffmpegThreads,
      });

      /** @type {Array<{ path: string, duration: number, emotion: string }>} */
      const allClips = [];

      for (let s = 0; s < script.segments.length; s++) {
        const segment   = { ...script.segments[s], _index: s };
        const mediaFile = mediaFiles[s]?.localPath || null;
        const clips     = await quickCuts.breakSegmentIntoClips(segment, mediaFile, sessionId);
        allClips.push(...clips);
      }

      console.log(`  Micro-clips generated: ${allClips.length}`);

      // ── Step b: Insert flash frames every 3rd clip ───────────────────────
      const flashColors    = ['white', 'red'];
      /** Final ordered list of clip paths after flash insertions */
      const processedPaths = [];

      for (let i = 0; i < allClips.length; i++) {
        processedPaths.push(allClips[i].path);

        // Insert flash between clip i and i+1 every 3rd clip
        if ((i + 1) % 3 === 0 && i + 1 < allClips.length) {
          const flashColor  = flashColors[Math.floor(i / 3) % flashColors.length];
          const flashedPath = path.join(
            this.tempDir,
            `flashed_${sessionId}_${i}.mp4`
          );

          try {
            await quickCuts.addFlashFrame(
              allClips[i].path,
              allClips[i + 1].path,
              flashedPath,
              flashColor
            );
            // Replace last appended path + skip next clip (it's merged into flashedPath)
            processedPaths.pop();         // remove clip[i]
            processedPaths.push(flashedPath);
            i++;                          // skip clip[i+1] — already merged
          } catch (flashErr) {
            console.warn(`  Flash frame skipped at clip ${i}: ${flashErr.message}`);
            // Leave processedPaths as-is, continue normally
          }
        }
      }

      // ── Step c: Concatenate all clips ────────────────────────────────────
      const concatPath = path.join(this.tempDir, `concat_${sessionId}.mp4`);
      await this.concatenateClips(processedPaths, concatPath);

      // ── Step d: Apply dark filter (curves + vignette) ────────────────────
      const filteredPath = path.join(this.tempDir, `filtered_${sessionId}.mp4`);
      await this.applyDarkFilter(concatPath, filteredPath);

      // ── Step e: Burn subtitles ────────────────────────────────────────────
      const subtitledPath = path.join(this.tempDir, `subtitled_${sessionId}.mp4`);
      await this.burnSubtitles(filteredPath, script, subtitledPath, sessionId);

      // ── Step f: Generate whoosh SFX at each cut timestamp ────────────────
      const cutTimestamps = this._computeCutTimestamps(allClips);
      const sfxTimings    = await this._generateWhooshEffects(cutTimestamps, sessionId);

      let videoWithSfx = subtitledPath;
      if (sfxTimings.length > 0) {
        const sfxPath = path.join(this.tempDir, `sfx_${sessionId}.mp4`);
        try {
          FxEngine.addSoundEffectsToVideo(
            subtitledPath,
            sfxTimings,
            sfxPath,
            this.ffmpegPath,
            this.ffmpegThreads
          );
          videoWithSfx = sfxPath;
        } catch (sfxErr) {
          console.warn(`  SFX mixing failed (continuing without): ${sfxErr.message}`);
        }
      }

      // ── Step g: Mix final audio ───────────────────────────────────────────
      await this.mixAudio(videoWithSfx, audioFile, musicFile, musicVolume, outputPath);

      // ── Cleanup ───────────────────────────────────────────────────────────
      await this.cleanup(sessionId);

      const stats      = fs.statSync(outputPath);
      const fileSizeMB = (stats.size / 1024 / 1024).toFixed(2);
      console.log(`\nVideoComposer: done — ${outputPath} (${fileSizeMB} MB)`);

      return { success: true, outputPath, fileSizeMB };

    } catch (err) {
      console.error(`VideoComposer: pipeline error — ${err.message}`);

      // Fallback: simple canvas title card
      try {
        console.log('VideoComposer: attempting fallback title card video...');
        const fallbackPath = await this.createFallbackVideo(script, audioFile, outputPath);
        const stats        = fs.statSync(fallbackPath);
        const fileSizeMB   = (stats.size / 1024 / 1024).toFixed(2);
        return { success: true, outputPath: fallbackPath, fileSizeMB, fallback: true };
      } catch (fallbackErr) {
        throw new Error(
          `VideoComposer pipeline failed: ${err.message}. ` +
          `Fallback also failed: ${fallbackErr.message}`
        );
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Step c — concatenate clips
  // ---------------------------------------------------------------------------

  /**
   * Concatenate a list of video clip paths using the FFmpeg concat demuxer.
   *
   * @param {string[]} clipPaths
   * @param {string}   outputPath
   */
  async concatenateClips(clipPaths, outputPath) {
    if (!clipPaths || clipPaths.length === 0) {
      throw new Error('concatenateClips: no clips provided');
    }

    const listPath = path.join(this.tempDir, `concatlist_${Date.now()}.txt`);
    const listContent = clipPaths
      .map(p => `file '${p.replace(/'/g, "'\\''")}'`)
      .join('\n');
    fs.writeFileSync(listPath, listContent, 'utf8');

    const cmd = [
      ...this.ff,
      '-f', 'concat',
      '-safe', '0',
      '-i', `"${listPath}"`,
      '-c:v', 'libx264',
      '-preset', 'ultrafast',
      '-crf', '28',
      '-pix_fmt', 'yuv420p',
      '-an',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    try {
      execSync(cmd, { stdio: 'pipe', timeout: 120000 });
    } finally {
      try { fs.unlinkSync(listPath); } catch (_) {}
    }
  }

  // ---------------------------------------------------------------------------
  // Step d — dark filter
  // ---------------------------------------------------------------------------

  /**
   * Apply a light dark/moody filter: curves darkening + vignette.
   * Deliberately lighter than before to avoid crushing the image on low-end
   * displays.
   *
   * @param {string} inputPath
   * @param {string} outputPath
   */
  async applyDarkFilter(inputPath, outputPath) {
    const filter = 'curves=preset=slightly_darker,vignette=PI/5';

    const cmd = [
      ...this.ff,
      '-i', `"${inputPath}"`,
      '-vf', `"${filter}"`,
      '-c:v', 'libx264',
      '-preset', 'ultrafast',
      '-crf', '28',
      '-pix_fmt', 'yuv420p',
      '-an',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    execSync(cmd, { stdio: 'pipe', timeout: 120000 });
  }

  // ---------------------------------------------------------------------------
  // Step e — subtitle burning
  // ---------------------------------------------------------------------------

  /**
   * Generate an ASS subtitle file from the script and burn it into the video.
   * Falls back to passing the video through unchanged if FFmpeg reports an error.
   *
   * @param {string} inputPath
   * @param {object} script      - { segments: [{ text, duration, emotion, emphasis }] }
   * @param {string} outputPath
   * @param {string|number} sessionId
   */
  async burnSubtitles(inputPath, script, outputPath, sessionId) {
    const SubtitleRenderer = require('./subtitleRenderer');
    const renderer = new SubtitleRenderer({
      width  : this.width,
      height : this.height,
      fps    : this.fps,
    });

    // Build per-word timing data from segment durations
    const timingData  = [];
    let currentTime   = 0;

    (script.segments || []).forEach((seg, segIdx) => {
      const words       = (seg.text || '').split(/\s+/).filter(Boolean);
      const segDuration = seg.duration || 3;
      const wordDur     = words.length > 0 ? segDuration / words.length : segDuration;

      words.forEach((word, wIdx) => {
        timingData.push({
          word,
          startTime : currentTime + wIdx * wordDur,
          duration  : wordDur,
          segment   : segIdx,
          isEmphasis: Array.isArray(seg.emphasis)
            ? seg.emphasis.includes(word.toLowerCase())
            : false,
          emotion   : seg.emotion || 'suspense',
        });
      });

      currentTime += segDuration;
    });

    const assPath = path.join(this.tempDir, `subs_${sessionId}.ass`);

    try {
      const subtitleFrames = renderer.generateSubtitleData(timingData, 'tiktok');
      renderer.generateASSFile(subtitleFrames, assPath, 'tiktok');
    } catch (genErr) {
      console.warn(`  Subtitle generation failed: ${genErr.message}`);
      // Copy input to output unmodified
      fs.copyFileSync(inputPath, outputPath);
      return;
    }

    // Escape path for FFmpeg ass filter (colons must be escaped on Windows/cross-platform)
    const escapedAss = assPath.replace(/\\/g, '/').replace(/:/g, '\\:');

    const cmd = [
      ...this.ff,
      '-i', `"${inputPath}"`,
      '-vf', `"ass='${escapedAss}'"`,
      '-c:v', 'libx264',
      '-preset', 'ultrafast',
      '-crf', '28',
      '-pix_fmt', 'yuv420p',
      '-an',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    try {
      execSync(cmd, { stdio: 'pipe', timeout: 120000 });
    } catch (burnErr) {
      console.warn(`  Subtitle burn failed (using video without subs): ${burnErr.message}`);
      fs.copyFileSync(inputPath, outputPath);
    } finally {
      try { fs.unlinkSync(assPath); } catch (_) {}
    }
  }

  // ---------------------------------------------------------------------------
  // Step g — audio mixing
  // ---------------------------------------------------------------------------

  /**
   * Mix the video's existing track (or silence) with narration + background music.
   *
   * Supported combinations:
   *   - narrator + music  → amix of both (narrator at 1.0, music at musicVolume)
   *   - narrator only     → direct map
   *   - music only        → music at musicVolume
   *   - neither           → -an (no audio)
   *
   * @param {string} videoPath
   * @param {string|null} narratorAudio
   * @param {string|null} musicFile
   * @param {number} musicVolume
   * @param {string} outputPath
   */
  async mixAudio(videoPath, narratorAudio, musicFile, musicVolume, outputPath) {
    const hasNarrator = narratorAudio && fs.existsSync(narratorAudio);
    const hasMusic    = musicFile     && fs.existsSync(musicFile);

    let audioFilter;
    const extraInputs = [];

    if (hasNarrator && hasMusic) {
      extraInputs.push(`-i "${narratorAudio}"`);
      extraInputs.push(`-i "${musicFile}"`);
      audioFilter =
        `-filter_complex "[1:a]volume=1.0[nar];[2:a]volume=${musicVolume}[mus];` +
        `[nar][mus]amix=inputs=2:duration=first:dropout_transition=2[audio]" ` +
        `-map 0:v -map "[audio]"`;

    } else if (hasNarrator) {
      extraInputs.push(`-i "${narratorAudio}"`);
      audioFilter = `-map 0:v -map 1:a`;

    } else if (hasMusic) {
      extraInputs.push(`-i "${musicFile}"`);
      audioFilter =
        `-filter_complex "[1:a]volume=${musicVolume}[audio]" ` +
        `-map 0:v -map "[audio]"`;

    } else {
      audioFilter = '-an';
    }

    const cmd = [
      ...this.ff,
      `-i "${videoPath}"`,
      ...extraInputs,
      audioFilter,
      '-c:v', 'copy',
      '-c:a', 'aac',
      '-b:a', '192k',
      '-shortest',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    execSync(cmd, { stdio: 'pipe', timeout: 120000 });
  }

  // ---------------------------------------------------------------------------
  // Cleanup
  // ---------------------------------------------------------------------------

  /**
   * Remove all temp files created for a given session.
   *
   * @param {string|number} sessionId
   */
  async cleanup(sessionId) {
    const sid = String(sessionId);

    const prefixes = [
      'concat_',
      'filtered_',
      'subtitled_',
      'sfx_',
      'flashed_',
      'fallback_frame_',
      'concatlist_',
    ];

    // Delete named temp files by pattern
    let entries = [];
    try {
      entries = fs.readdirSync(this.tempDir);
    } catch (_) {}

    for (const entry of entries) {
      if (!entry.includes(sid)) continue;
      const inKnownPrefix = prefixes.some(p => entry.startsWith(p));
      // Also cover QuickCuts' qc_* files from this session
      if (inKnownPrefix || entry.startsWith(`qc_${sid}`)) {
        try {
          fs.unlinkSync(path.join(this.tempDir, entry));
        } catch (_) {}
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Fallback
  // ---------------------------------------------------------------------------

  /**
   * Create a minimal title card video using canvas (no external media needed).
   * Used when the main pipeline fails entirely.
   *
   * @param {object} script
   * @param {string|null} audioFile
   * @param {string} outputPath
   * @returns {Promise<string>} outputPath
   */
  async createFallbackVideo(script, audioFile, outputPath) {
    const canvas = createCanvas(this.width, this.height);
    const ctx    = canvas.getContext('2d');

    // Dark gradient background
    const bg = ctx.createLinearGradient(0, 0, 0, this.height);
    bg.addColorStop(0,   '#050010');
    bg.addColorStop(0.5, '#0a0520');
    bg.addColorStop(1,   '#000005');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, this.width, this.height);

    // Title
    ctx.fillStyle    = '#FFFFFF';
    ctx.font         = `bold ${Math.floor(this.width * 0.11)}px Arial`;
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.shadowColor  = 'rgba(0,0,0,0.9)';
    ctx.shadowBlur   = 20;
    ctx.fillText(
      (script.title || 'Viral Video').slice(0, 40),
      this.width / 2,
      this.height * 0.28
    );

    // Hook text (word-wrapped)
    ctx.fillStyle = '#FFD700';
    ctx.font      = `bold ${Math.floor(this.width * 0.08)}px Arial`;
    const hookLines = this._wrapText(ctx, script.hook || '', this.width - 100);
    hookLines.forEach((line, i) => {
      ctx.fillText(line, this.width / 2, this.height * 0.5 + i * Math.floor(this.width * 0.1));
    });

    // Save frame as JPEG
    const framePath = path.join(this.tempDir, `fallback_frame_${Date.now()}.jpg`);
    fs.writeFileSync(framePath, canvas.toBuffer('image/jpeg', { quality: 0.9 }));

    const totalDuration = (script.segments || []).reduce((s, seg) => s + (seg.duration || 3), 0) || 30;
    const hasAudio      = audioFile && fs.existsSync(audioFile);

    const cmd = [
      ...this.ff,
      '-loop', '1',
      `-i "${framePath}"`,
      hasAudio ? `-i "${audioFile}"` : '',
      '-t', String(totalDuration),
      '-vf', `"scale=${this.width}:${this.height}"`,
      '-c:v', 'libx264',
      '-preset', 'ultrafast',
      '-crf', '28',
      '-pix_fmt', 'yuv420p',
      hasAudio ? '-c:a aac -b:a 192k -shortest' : '-an',
      `"${outputPath}"`,
      '-y',
    ].filter(Boolean).join(' ');

    execSync(cmd, { stdio: 'pipe', timeout: 60000 });

    try { fs.unlinkSync(framePath); } catch (_) {}

    return outputPath;
  }

  // ---------------------------------------------------------------------------
  // Private helpers
  // ---------------------------------------------------------------------------

  _ensureDirs() {
    [this.outputDir, this.tempDir].forEach(dir => {
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    });
  }

  /**
   * Compute the absolute start timestamp (ms) of each cut between clips.
   *
   * @param {Array<{ duration: number }>} clips
   * @returns {number[]} Array of timestamps in milliseconds
   */
  _computeCutTimestamps(clips) {
    const timestamps = [];
    let elapsed      = 0;
    for (let i = 0; i < clips.length - 1; i++) {
      elapsed += (clips[i].duration || 1.5) * 1000;
      timestamps.push(Math.round(elapsed));
    }
    return timestamps;
  }

  /**
   * Generate a whoosh .mp3 for every cut timestamp and return the timing array
   * expected by FxEngine.addSoundEffectsToVideo.
   *
   * @param {number[]} timestamps  - Cut positions in ms
   * @param {string|number} sessionId
   * @returns {Promise<Array<{ file: string, timeMs: number }>>}
   */
  async _generateWhooshEffects(timestamps, sessionId) {
    const timings = [];

    for (let i = 0; i < timestamps.length; i++) {
      const sfxPath = path.join(this.tempDir, `whoosh_${sessionId}_${i}.mp3`);
      try {
        FxEngine.generateWhoosh(sfxPath, this.ffmpegPath);
        timings.push({ file: sfxPath, timeMs: timestamps[i] });
      } catch (err) {
        console.warn(`  Whoosh ${i} skipped: ${err.message}`);
      }
    }

    return timings;
  }

  /**
   * Wrap `text` into lines no wider than `maxWidth` pixels.
   *
   * @param {CanvasRenderingContext2D} ctx
   * @param {string} text
   * @param {number} maxWidth
   * @returns {string[]}
   */
  _wrapText(ctx, text, maxWidth) {
    const words = String(text || '').split(' ');
    const lines = [];
    let line    = '';

    for (const word of words) {
      const test = line ? `${line} ${word}` : word;
      if (ctx.measureText(test).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else {
        line = test;
      }
    }
    if (line) lines.push(line);
    return lines;
  }
}

module.exports = VideoComposer;
