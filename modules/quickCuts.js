/**
 * QuickCuts Module
 * Fast-cut video engine for viral TikTok/Reels style videos.
 *
 * Produces 1.5–2 s micro-clips for each script segment and stitches them into
 * a rapid-fire sequence.  Every FFmpeg call is limited to:
 *   -threads 2  -preset ultrafast  -crf 28   (720 × 1280 by default)
 *
 * Canvas drawing uses the `canvas` npm package (v3).
 */

'use strict';

const { createCanvas } = require('canvas');
const { execSync }     = require('child_process');
const fs   = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// Shared constants
// ---------------------------------------------------------------------------

/** Dark color palette used for solid / split-screen backgrounds */
const DARK_COLORS = {
  purple : '#0d0021',
  red    : '#1a0000',
  blue   : '#00001a',
  green  : '#001a00',
  teal   : '#001a1a',
  maroon : '#1a0010',
  indigo : '#08001a',
  black  : '#000000',
};

/** Emotion → base color mapping */
const EMOTION_COLORS = {
  suspense  : DARK_COLORS.purple,
  revelação : DARK_COLORS.red,
  choque    : DARK_COLORS.maroon,
  reflexão  : DARK_COLORS.blue,
  gancho    : DARK_COLORS.indigo,
  default   : DARK_COLORS.black,
};

/** Scene types cycled through when breaking a segment into micro-clips */
const SCENE_CYCLE = ['solid', 'pattern', 'splitScreen', 'solid'];

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function run(cmd, timeoutMs = 60000) {
  try {
    execSync(cmd, { stdio: 'pipe', timeout: timeoutMs });
  } catch (err) {
    const stderr = err.stderr ? err.stderr.toString().slice(0, 500) : '';
    throw new Error(`FFmpeg error: ${stderr || err.message}`);
  }
}

function ensureDir(p) {
  if (!fs.existsSync(p)) fs.mkdirSync(p, { recursive: true });
}

/** Convert a hex color like '#0d0021' to an { r, g, b } object */
function hexToRgb(hex) {
  const h = hex.replace('#', '');
  return {
    r: parseInt(h.slice(0, 2), 16),
    g: parseInt(h.slice(2, 4), 16),
    b: parseInt(h.slice(4, 6), 16),
  };
}

/** Wrap text into lines that fit within `maxWidth` pixels */
function wrapText(ctx, text, maxWidth) {
  const words = String(text).split(' ');
  const lines = [];
  let line = '';
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

// ---------------------------------------------------------------------------
// Canvas drawing primitives
// ---------------------------------------------------------------------------

/**
 * Draw a solid dark background with big bold white text in the center.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} width
 * @param {number} height
 * @param {string} bgColor    - CSS hex color for background
 * @param {string} [text='']  - Text to render
 * @param {number} [frame=0]  - Frame index (used for subtle animation)
 */
function drawSolidScene(ctx, width, height, bgColor, text = '', frame = 0) {
  const { r, g, b } = hexToRgb(bgColor);

  // ── Background gradient (adds subtle depth) ──────────────────────────────
  const grad = ctx.createLinearGradient(0, 0, 0, height);
  grad.addColorStop(0, `rgb(${r},${g},${b})`);
  grad.addColorStop(0.5, `rgb(${Math.min(r + 15, 60)},${Math.min(g + 10, 50)},${Math.min(b + 15, 60)})`);
  grad.addColorStop(1, `rgb(${r},${g},${b})`);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, width, height);

  // ── Subtle vignette overlay ───────────────────────────────────────────────
  const vig = ctx.createRadialGradient(width / 2, height / 2, height * 0.2, width / 2, height / 2, height * 0.9);
  vig.addColorStop(0, 'rgba(0,0,0,0)');
  vig.addColorStop(1, 'rgba(0,0,0,0.6)');
  ctx.fillStyle = vig;
  ctx.fillRect(0, 0, width, height);

  // ── Animated particles (sparse) ──────────────────────────────────────────
  for (let i = 0; i < 40; i++) {
    const px = ((i * 173 + frame * 3) % width);
    const py = ((i * 97  + frame * 5) % height);
    const alpha = 0.04 + (i % 6) * 0.008;
    ctx.fillStyle = `rgba(200,150,255,${alpha})`;
    ctx.beginPath();
    ctx.arc(px, py, (i % 3) + 1, 0, Math.PI * 2);
    ctx.fill();
  }

  // ── Text ─────────────────────────────────────────────────────────────────
  if (text) {
    const fontSize = Math.floor(width * 0.1); // ~72 px at 720 w
    ctx.save();
    ctx.font        = `bold ${fontSize}px Arial, sans-serif`;
    ctx.textAlign   = 'center';
    ctx.textBaseline = 'middle';

    const lines   = wrapText(ctx, text, width - 80);
    const lineH   = fontSize * 1.25;
    const totalH  = lines.length * lineH;
    const startY  = height / 2 - totalH / 2 + lineH / 2;

    // Shadow
    ctx.shadowColor   = 'rgba(0,0,0,0.9)';
    ctx.shadowBlur    = 20;
    ctx.shadowOffsetX = 3;
    ctx.shadowOffsetY = 3;

    lines.forEach((line, i) => {
      // Glow pass
      ctx.fillStyle = 'rgba(180,100,255,0.3)';
      ctx.fillText(line, width / 2 + 2, startY + i * lineH + 2);
      // Main text — bright white
      ctx.fillStyle = '#FFFFFF';
      ctx.fillText(line, width / 2, startY + i * lineH);
    });

    ctx.restore();
  }
}

/**
 * Draw a matrix-style pattern on a canvas (green characters raining down).
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} width
 * @param {number} height
 * @param {number} frame  - Animation frame index
 */
function drawMatrixPattern(ctx, width, height, frame = 0) {
  // Black background
  ctx.fillStyle = '#000000';
  ctx.fillRect(0, 0, width, height);

  const charSize  = 16;
  const cols      = Math.floor(width  / charSize);
  const rows      = Math.floor(height / charSize);
  const chars     = '01アイウエオカキクケコサシスセソタチツテトナニヌネノ';

  ctx.font        = `${charSize}px monospace`;
  ctx.textBaseline = 'top';

  for (let col = 0; col < cols; col++) {
    // Each column has a different "head" position that wraps around
    const speed  = 1 + (col % 4);
    const head   = Math.floor((frame * speed + col * 13) % (rows + rows)) - rows;

    for (let row = 0; row < rows; row++) {
      const distFromHead = row - head;
      if (distFromHead < 0 || distFromHead > 20) continue;

      const alpha  = Math.max(0, 1 - distFromHead / 20);
      const bright = distFromHead === 0 ? 255 : 80 + Math.floor(alpha * 120);

      ctx.fillStyle = distFromHead === 0
        ? `rgba(180,255,180,${alpha})`
        : `rgba(0,${bright},0,${alpha})`;

      const charIdx = (col * 7 + row * 3 + frame) % chars.length;
      ctx.fillText(chars[charIdx], col * charSize, row * charSize);
    }
  }
}

/**
 * Draw a particle / geometric animation on a canvas.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} width
 * @param {number} height
 * @param {string} bgColor
 * @param {number} frame
 */
function drawPatternScene(ctx, width, height, bgColor, frame = 0) {
  const { r, g, b } = hexToRgb(bgColor);

  // Deep dark base
  ctx.fillStyle = `rgb(${r},${g},${b})`;
  ctx.fillRect(0, 0, width, height);

  // ── Geometric lines (hexagonal feel) ─────────────────────────────────────
  ctx.save();
  ctx.strokeStyle = 'rgba(120,60,220,0.12)';
  ctx.lineWidth   = 1;
  const step = 60;
  for (let x = -step; x < width + step; x += step) {
    for (let y = -step; y < height + step; y += step) {
      const ox = Math.sin((frame + x) * 0.02) * 8;
      const oy = Math.cos((frame + y) * 0.02) * 8;
      ctx.beginPath();
      ctx.moveTo(x + ox, y + oy);
      ctx.lineTo(x + step + ox, y + oy);
      ctx.lineTo(x + step / 2 + ox, y + step + oy);
      ctx.closePath();
      ctx.stroke();
    }
  }
  ctx.restore();

  // ── Floating particles ────────────────────────────────────────────────────
  const particleCount = 120;
  for (let i = 0; i < particleCount; i++) {
    const seed = i * 137.508; // golden-angle spread
    const px   = ((seed + frame * (0.5 + i * 0.01)) % width);
    const py   = ((i * 89 + frame * (0.3 + i * 0.008)) % height);
    const size = 1 + (i % 4);
    const alpha = 0.05 + (i % 7) * 0.01;
    const hue  = (60 * (i % 6)) % 360; // cycle through hues

    ctx.fillStyle = `hsla(${hue},80%,70%,${alpha})`;
    ctx.beginPath();
    ctx.arc(px, py, size, 0, Math.PI * 2);
    ctx.fill();
  }

  // ── Central pulsing glow ──────────────────────────────────────────────────
  const pulse = 0.5 + 0.5 * Math.sin(frame * 0.15);
  const glow  = ctx.createRadialGradient(width / 2, height / 2, 0, width / 2, height / 2, width * 0.6);
  glow.addColorStop(0, `rgba(120,0,200,${0.08 * pulse})`);
  glow.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, width, height);
}

/**
 * Draw a split-screen canvas:
 *   - Top 60 %  : dark atmospheric scene (gradient + particles)
 *   - Bottom 40 %: matrix pattern
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} width
 * @param {number} height
 * @param {string} bgColor
 * @param {number} frame
 */
function drawSplitScreenScene(ctx, width, height, bgColor, frame = 0) {
  const topH    = Math.floor(height * 0.6);
  const bottomH = height - topH;

  // ── TOP: dark atmospheric ─────────────────────────────────────────────────
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, width, topH);
  ctx.clip();

  drawSolidScene(ctx, width, topH, bgColor, '', frame);

  ctx.restore();

  // ── BOTTOM: matrix pattern ────────────────────────────────────────────────
  ctx.save();
  ctx.translate(0, topH);
  ctx.beginPath();
  ctx.rect(0, 0, width, bottomH);
  ctx.clip();

  // Create a sub-canvas for the matrix pattern to avoid coordinate confusion
  const matrixCanvas = createCanvas(width, bottomH);
  const mCtx = matrixCanvas.getContext('2d');
  drawMatrixPattern(mCtx, width, bottomH, frame);
  ctx.drawImage(matrixCanvas, 0, 0);

  ctx.restore();

  // ── Dividing line ─────────────────────────────────────────────────────────
  ctx.strokeStyle = 'rgba(0,200,0,0.5)';
  ctx.lineWidth   = 2;
  ctx.beginPath();
  ctx.moveTo(0, topH);
  ctx.lineTo(width, topH);
  ctx.stroke();
}

// ---------------------------------------------------------------------------
// QuickCuts class
// ---------------------------------------------------------------------------

class QuickCuts {
  /**
   * @param {object}  options
   * @param {string}  options.ffmpegPath
   * @param {number}  [options.width=720]
   * @param {number}  [options.height=1280]
   * @param {number}  [options.fps=24]
   * @param {string}  options.tempDir
   * @param {string}  [options.ffmpegThreads='2']
   */
  constructor(options = {}) {
    this.ffmpegPath    = options.ffmpegPath    || 'ffmpeg';
    this.width         = options.width         || 720;
    this.height        = options.height        || 1280;
    this.fps           = options.fps           || 24;
    this.tempDir       = options.tempDir       || path.join(__dirname, '../temp');
    this.ffmpegThreads = options.ffmpegThreads || '2';

    ensureDir(this.tempDir);
  }

  // ── Public interface ──────────────────────────────────────────────────────

  /**
   * Generate a single scene micro-clip.
   *
   * @param {object} options
   * @param {string} options.type      - 'solid' | 'pattern' | 'splitScreen' | 'zoom'
   * @param {number} options.duration  - Clip length in seconds (1.5 – 2 recommended)
   * @param {string} [options.emotion] - Drives background color
   * @param {string} [options.color]   - Explicit CSS hex color (overrides emotion)
   * @param {string} [options.text]    - Text to burn into the clip (solid scenes)
   * @param {string} [options.imageFile] - Source image path (zoom scene only)
   * @param {string} outputPath        - Destination .mp4 path
   */
  async generateSceneClip(options, outputPath) {
    const {
      type      = 'solid',
      duration  = 1.5,
      emotion   = 'default',
      color,
      text      = '',
      imageFile,
    } = options;

    ensureDir(path.dirname(outputPath));

    const bgColor = color || EMOTION_COLORS[emotion] || EMOTION_COLORS.default;

    switch (type) {
      case 'zoom':
        await this._generateZoomClip(imageFile, outputPath, duration, bgColor);
        break;

      case 'splitScreen':
        await this._generateCanvasClip(
          outputPath, duration,
          (ctx, w, h, frame) => drawSplitScreenScene(ctx, w, h, bgColor, frame)
        );
        break;

      case 'pattern':
        await this._generateCanvasClip(
          outputPath, duration,
          (ctx, w, h, frame) => drawPatternScene(ctx, w, h, bgColor, frame)
        );
        break;

      case 'solid':
      default:
        await this._generateCanvasClip(
          outputPath, duration,
          (ctx, w, h, frame) => drawSolidScene(ctx, w, h, bgColor, text, frame)
        );
        break;
    }
  }

  /**
   * Break a single script segment into 3–4 rapid micro-clips.
   *
   * @param {object} segment   - Script segment: { text, duration, emotion, ... }
   * @param {string} [mediaFile] - Optional image path (used for zoom clips)
   * @param {string|number} [sessionId] - Used to namespace temp files
   * @returns {Promise<Array<{ path:string, duration:number, emotion:string }>>}
   */
  async breakSegmentIntoClips(segment, mediaFile, sessionId) {
    const totalDuration = segment.duration || 4;
    const emotion       = segment.emotion  || 'default';
    const text          = segment.text     || '';
    const sid           = sessionId || Date.now();

    // Determine how many mini-clips to produce
    const clipCount = totalDuration <= 3 ? 2 : totalDuration <= 5 ? 3 : 4;

    // Distribute duration: vary slightly so it doesn't feel mechanical
    const clipDurations = [];
    let remaining = totalDuration;
    for (let i = 0; i < clipCount; i++) {
      if (i === clipCount - 1) {
        clipDurations.push(Math.max(0.5, remaining));
      } else {
        // Alternate between 1.5 s and 2 s
        const d = (i % 2 === 0) ? 1.5 : 2.0;
        const clamped = Math.min(d, remaining - (clipCount - i - 1) * 0.5);
        clipDurations.push(Math.max(0.5, clamped));
        remaining -= clamped;
      }
    }

    const clips = [];

    for (let i = 0; i < clipCount; i++) {
      const sceneType = SCENE_CYCLE[i % SCENE_CYCLE.length];
      const clipPath  = path.join(
        this.tempDir,
        `qc_${sid}_seg${segment._index || 0}_clip${i}.mp4`
      );

      // For the first solid clip, include text; others are purely visual
      const clipText = (sceneType === 'solid' && i === 0) ? text : '';

      // Use the mediaFile for zoom if available
      const useImage = sceneType === 'zoom' && mediaFile && fs.existsSync(mediaFile);

      await this.generateSceneClip(
        {
          type      : useImage ? 'zoom' : sceneType,
          duration  : clipDurations[i],
          emotion,
          text      : clipText,
          imageFile : useImage ? mediaFile : undefined,
        },
        clipPath
      );

      clips.push({ path: clipPath, duration: clipDurations[i], emotion });
    }

    return clips;
  }

  /**
   * Insert a very short bright flash frame between two clips.
   * The output file is a new concatenated video: [before] + [flash] + [after].
   *
   * @param {string} beforePath  - Clip before the flash
   * @param {string} afterPath   - Clip after the flash
   * @param {string} outputPath  - Resulting concatenated clip
   * @param {string} [color='white']
   */
  async addFlashFrame(beforePath, afterPath, outputPath, color = 'white') {
    ensureDir(path.dirname(outputPath));

    const flashPath = path.join(
      this.tempDir,
      `flash_${Date.now()}_${Math.random().toString(36).slice(2, 6)}.mp4`
    );

    // 1. Generate flash frame video
    const flashCmd = [
      `"${this.ffmpegPath}"`,
      '-threads', this.ffmpegThreads,
      '-f', 'lavfi',
      '-i', `"color=c=${color}:s=${this.width}x${this.height}:r=${this.fps}"`,
      '-t', '0.08',
      '-c:v', 'libx264',
      '-preset', 'ultrafast',
      '-crf', '28',
      '-pix_fmt', 'yuv420p',
      '-an',
      `"${flashPath}"`,
      '-y',
    ].join(' ');
    run(flashCmd);

    // 2. Concatenate before + flash + after
    const listPath = path.join(this.tempDir, `flashlist_${Date.now()}.txt`);
    fs.writeFileSync(listPath, [
      `file '${beforePath}'`,
      `file '${flashPath}'`,
      `file '${afterPath}'`,
    ].join('\n'));

    const concatCmd = [
      `"${this.ffmpegPath}"`,
      '-threads', this.ffmpegThreads,
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
    run(concatCmd, 60000);

    // Cleanup intermediates
    [flashPath, listPath].forEach(p => {
      try { fs.unlinkSync(p); } catch (_) {}
    });
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  /**
   * Render frames via canvas draw function and encode to MP4.
   *
   * To keep memory low we sample a set of key frames (max 12) and loop /
   * duplicate them to fill the required duration rather than rendering every
   * single frame.
   *
   * @param {string}   outputPath
   * @param {number}   duration
   * @param {Function} drawFn  - (ctx, width, height, frame) => void
   */
  async _generateCanvasClip(outputPath, duration, drawFn, bgColorHex = '#0d0021') {
    // First try canvas rendering (needs libcairo on the system)
    try {
      const framesDir = path.join(
        this.tempDir,
        `qc_frames_${Date.now()}_${Math.random().toString(36).slice(2, 5)}`
      );
      ensureDir(framesDir);

      const totalFrames  = Math.max(1, Math.ceil(duration * this.fps));
      const sampleFrames = Math.min(totalFrames, 16);

      for (let f = 0; f < sampleFrames; f++) {
        const canvas = createCanvas(this.width, this.height);
        const ctx    = canvas.getContext('2d');
        const animFrame = Math.floor((f / sampleFrames) * totalFrames);
        drawFn(ctx, this.width, this.height, animFrame);
        const buf  = canvas.toBuffer('image/jpeg', { quality: 0.88 });
        fs.writeFileSync(path.join(framesDir, `frame_${String(f).padStart(5, '0')}.jpg`), buf);
      }

      for (let f = sampleFrames; f < totalFrames; f++) {
        const src = path.join(framesDir, `frame_${String(f % sampleFrames).padStart(5, '0')}.jpg`);
        const dst = path.join(framesDir, `frame_${String(f).padStart(5, '0')}.jpg`);
        fs.copyFileSync(src, dst);
      }

      const cmd = [
        `"${this.ffmpegPath}"`,
        '-threads', this.ffmpegThreads,
        '-framerate', String(this.fps),
        '-i', `"${path.join(framesDir, 'frame_%05d.jpg')}"`,
        '-t', String(duration),
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', '-pix_fmt', 'yuv420p', '-an',
        `"${outputPath}"`, '-y',
      ].join(' ');

      run(cmd, 90000);
      try { fs.rmSync(framesDir, { recursive: true, force: true }); } catch (_) {}

    } catch (canvasErr) {
      // Canvas not available (missing libcairo) — fallback to pure FFmpeg lavfi
      console.warn(`  Canvas unavailable, using FFmpeg fallback: ${canvasErr.message.slice(0, 80)}`);
      await this._generateFFmpegClip(outputPath, duration, bgColorHex);
    }
  }

  /**
   * Pure FFmpeg background clip — no canvas needed.
   * Uses lavfi color source + geq filter for animated gradient effect.
   */
  async _generateFFmpegClip(outputPath, duration, bgColorHex = '#0d0021') {
    const { r, g, b } = hexToRgb(bgColorHex);
    // Use geq to create a subtle animated gradient without canvas
    const vf = [
      `geq=r='${r}+10*sin(2*PI*T/3)':g='${g}+5*sin(2*PI*T/4)':b='${b}+15*sin(2*PI*T/2)'`,
      `scale=${this.width}:${this.height}`,
    ].join(',');

    const cmd = [
      `"${this.ffmpegPath}"`,
      '-threads', this.ffmpegThreads,
      '-f', 'lavfi',
      '-i', `color=c=black:s=${this.width}x${this.height}:r=${this.fps}`,
      '-t', String(duration),
      '-vf', `"${vf}"`,
      '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', '-pix_fmt', 'yuv420p', '-an',
      `"${outputPath}"`, '-y',
    ].join(' ');

    run(cmd, 60000);
  }

  /**
   * Create a slow-zoom-in clip from a still image.
   * Falls back to a solid dark clip if the image is missing or FFmpeg errors.
   *
   * @param {string} imagePath
   * @param {string} outputPath
   * @param {number} duration
   * @param {string} bgColor
   */
  async _generateZoomClip(imagePath, outputPath, duration, bgColor) {
    if (!imagePath || !fs.existsSync(imagePath)) {
      // Fallback to solid clip
      await this._generateCanvasClip(
        outputPath, duration,
        (ctx, w, h, frame) => drawSolidScene(ctx, w, h, bgColor, '', frame)
      );
      return;
    }

    const totalFrames = Math.ceil(duration * this.fps);

    // zoompan: start at z=1.0, end at z=1.15 (slow creep in)
    const zoomFilter = [
      `scale=${this.width * 2}:${this.height * 2}:force_original_aspect_ratio=increase`,
      `crop=${this.width * 2}:${this.height * 2}`,
      `zoompan=z='min(zoom+0.001,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'` +
        `:d=${totalFrames}:s=${this.width}x${this.height}:fps=${this.fps}`,
    ].join(',');

    const cmd = [
      `"${this.ffmpegPath}"`,
      '-threads', this.ffmpegThreads,
      '-loop', '1',
      '-i', `"${imagePath}"`,
      '-vf', `"${zoomFilter}"`,
      '-t', String(duration),
      '-c:v', 'libx264',
      '-preset', 'ultrafast',
      '-crf', '28',
      '-pix_fmt', 'yuv420p',
      '-an',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    try {
      run(cmd, 90000);
    } catch (_) {
      // Fallback: canvas solid scene
      await this._generateCanvasClip(
        outputPath, duration,
        (ctx, w, h, frame) => drawSolidScene(ctx, w, h, bgColor, '', frame)
      );
    }
  }
}

// ---------------------------------------------------------------------------
module.exports = QuickCuts;
