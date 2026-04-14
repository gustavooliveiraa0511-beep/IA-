/**
 * Video Composer Module
 * Assembles images, videos, audio, and subtitles into final vertical video
 * Applies dark filters, Ken Burns effects, transitions, and sound effects
 */

const { createCanvas, loadImage } = require('canvas');
const { execSync, exec, spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

class VideoComposer {
  constructor(options = {}) {
    this.width = options.width || 1080;
    this.height = options.height || 1920;
    this.fps = options.fps || 30;
    this.outputDir = options.outputDir || path.join(__dirname, '../output');
    this.tempDir = options.tempDir || path.join(__dirname, '../temp');
    this.ffmpegPath = this.findFFmpeg();

    // Visual effects settings
    this.DARK_FILTERS = {
      cinematic: 'colorbalance=rs=-0.1:gs=-0.1:bs=0.1,curves=preset=darker,vignette=PI/4',
      moody: 'colorchannelmixer=rr=0.9:gg=0.9:bb=1.1,curves=preset=darker,vignette=PI/5',
      horror: 'colorbalance=rs=-0.2:bs=0.15,curves=preset=strong_contrast,vignette=PI/3',
      subtle: 'curves=preset=slightly_darker,vignette=PI/6',
    };

    // Transition effects
    this.TRANSITIONS = {
      cut: { duration: 0 },
      fade: { duration: 0.3, filter: 'fade=t=out:st=DURATION:d=0.3,fade=t=in:st=0:d=0.3' },
      slide: { duration: 0.2 },
      zoom: { duration: 0.25 },
    };

    this.ensureDirs();
  }

  ensureDirs() {
    [this.outputDir, this.tempDir].forEach(dir => {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
    });
  }

  findFFmpeg() {
    const paths = [
      '/usr/bin/ffmpeg',
      '/usr/local/bin/ffmpeg',
      '/opt/homebrew/bin/ffmpeg',
    ];

    try {
      const result = execSync('which ffmpeg 2>/dev/null', { encoding: 'utf8' }).trim();
      if (result) return result;
    } catch {}

    for (const p of paths) {
      if (fs.existsSync(p)) return p;
    }

    // Try ffmpeg-static
    try {
      const ffmpegStatic = require('ffmpeg-static');
      if (ffmpegStatic) return ffmpegStatic;
    } catch {}

    console.warn('⚠️  FFmpeg not found. Video composition will be limited.');
    return 'ffmpeg';
  }

  /**
   * Main composition pipeline
   */
  async compose(options) {
    const {
      script,
      mediaFiles,
      audioFile,
      subtitleData,
      outputFileName,
      visualStyle = 'cinematic',
      musicFile = null,
      musicVolume = 0.15,
    } = options;

    const outputPath = path.join(this.outputDir, outputFileName || `video_${Date.now()}.mp4`);
    const sessionId = Date.now();

    console.log('\n🎬 Iniciando composição do vídeo...');
    console.log(`   📐 Formato: ${this.width}x${this.height} (9:16 vertical)`);
    console.log(`   🎞️  FPS: ${this.fps}`);
    console.log(`   📁 Saída: ${outputPath}`);

    try {
      // Step 1: Create image slides with Ken Burns effect
      const slidePaths = await this.createSlides(mediaFiles, script, sessionId);

      // Step 2: Concatenate slides into video
      const rawVideoPath = await this.concatenateSlides(slidePaths, sessionId);

      // Step 3: Apply dark visual filters
      const filteredVideoPath = await this.applyDarkFilters(rawVideoPath, visualStyle, sessionId);

      // Step 4: Add subtitles
      const subtitledVideoPath = await this.burnSubtitles(
        filteredVideoPath, subtitleData, script, sessionId
      );

      // Step 5: Mix audio (narration + music)
      const finalVideoPath = await this.mixAudio(
        subtitledVideoPath, audioFile, musicFile, musicVolume, outputPath
      );

      // Cleanup temp files
      await this.cleanup(sessionId, slidePaths);

      console.log(`\n✅ Vídeo gerado com sucesso: ${outputPath}`);

      const stats = fs.statSync(finalVideoPath);
      return {
        success: true,
        outputPath: finalVideoPath,
        fileSize: stats.size,
        fileSizeMB: (stats.size / 1024 / 1024).toFixed(2),
      };
    } catch (error) {
      console.error('❌ Erro na composição:', error.message);

      // Fallback: create simple slideshow
      try {
        console.log('🔄 Tentando composição alternativa...');
        const fallbackPath = await this.createFallbackVideo(script, audioFile, outputPath, sessionId);
        return {
          success: true,
          outputPath: fallbackPath,
          fallback: true,
        };
      } catch (fallbackError) {
        throw new Error(`Composition failed: ${error.message}. Fallback also failed: ${fallbackError.message}`);
      }
    }
  }

  /**
   * Create image slides with Ken Burns effect for each segment
   */
  async createSlides(mediaFiles, script, sessionId) {
    const slidePaths = [];

    console.log('\n🖼️  Criando slides com efeito Ken Burns...');

    for (let i = 0; i < script.segments.length; i++) {
      const segment = script.segments[i];
      const mediaFile = mediaFiles?.[i];
      const duration = segment.duration || 3;
      const slidePath = path.join(this.tempDir, `slide_${sessionId}_${i}.mp4`);

      console.log(`   🎞️  Slide ${i + 1}/${script.segments.length} (${duration}s)`);

      try {
        if (mediaFile?.localPath && fs.existsSync(mediaFile.localPath)) {
          if (mediaFile.type === 'video') {
            // Use video clip
            await this.createVideoSlide(mediaFile.localPath, slidePath, duration);
          } else {
            // Create Ken Burns from image
            await this.createKenBurnsSlide(mediaFile.localPath, slidePath, duration, i);
          }
        } else {
          // Create atmospheric dark background
          await this.createAtmosphericSlide(slidePath, duration, segment.emotion || 'suspense', i);
        }
        slidePaths.push(slidePath);
      } catch (err) {
        console.error(`   ⚠️  Erro no slide ${i + 1}: ${err.message}`);
        // Create simple black slide as fallback
        await this.createBlackSlide(slidePath, duration);
        slidePaths.push(slidePath);
      }
    }

    return slidePaths;
  }

  /**
   * Create Ken Burns effect from still image
   */
  async createKenBurnsSlide(imagePath, outputPath, duration, slideIndex) {
    const kenBurnsPresets = [
      // Zoom in from center
      `zoompan=z='min(zoom+0.001,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${Math.floor(duration * this.fps)}:s=${this.width}x${this.height}:fps=${this.fps}`,
      // Pan left to right
      `zoompan=z='1.1':x='if(lte(on,1),0,x+1)':y='ih/2-(ih/zoom/2)':d=${Math.floor(duration * this.fps)}:s=${this.width}x${this.height}:fps=${this.fps}`,
      // Zoom out from top-right
      `zoompan=z='max(zoom-0.001,1)':x='iw*0.7-(iw/zoom/2)':y='ih*0.2-(ih/zoom/2)':d=${Math.floor(duration * this.fps)}:s=${this.width}x${this.height}:fps=${this.fps}`,
      // Pan diagonal
      `zoompan=z='1.08':x='if(lte(on,1),0,x+0.5)':y='if(lte(on,1),0,y+0.5)':d=${Math.floor(duration * this.fps)}:s=${this.width}x${this.height}:fps=${this.fps}`,
      // Slow zoom in from bottom
      `zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)':y='ih*0.8-(ih/zoom/2)':d=${Math.floor(duration * this.fps)}:s=${this.width}x${this.height}:fps=${this.fps}`,
    ];

    const kenBurns = kenBurnsPresets[slideIndex % kenBurnsPresets.length];

    const command = [
      this.ffmpegPath,
      '-loop', '1',
      '-i', `"${imagePath}"`,
      '-vf', `"scale=${this.width * 2}:${this.height * 2}:force_original_aspect_ratio=increase,crop=${this.width * 2}:${this.height * 2},${kenBurns}"`,
      '-t', duration.toString(),
      '-c:v', 'libx264',
      '-preset', 'fast',
      '-crf', '23',
      '-pix_fmt', 'yuv420p',
      '-an',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    execSync(command, { stdio: 'pipe', timeout: 60000 });
  }

  /**
   * Create video clip (trim and resize)
   */
  async createVideoSlide(videoPath, outputPath, duration) {
    const command = [
      this.ffmpegPath,
      '-i', `"${videoPath}"`,
      '-t', duration.toString(),
      '-vf', `"scale=${this.width}:${this.height}:force_original_aspect_ratio=increase,crop=${this.width}:${this.height}"`,
      '-c:v', 'libx264',
      '-preset', 'fast',
      '-crf', '23',
      '-pix_fmt', 'yuv420p',
      '-an',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    execSync(command, { stdio: 'pipe', timeout: 60000 });
  }

  /**
   * Create atmospheric animated background slide
   */
  async createAtmosphericSlide(outputPath, duration, emotion, index) {
    // Create using canvas then convert to video
    const framesDir = path.join(this.tempDir, `atm_${Date.now()}_${index}`);
    fs.mkdirSync(framesDir, { recursive: true });

    const totalFrames = Math.ceil(duration * this.fps);
    const frameCount = Math.min(totalFrames, 10); // Sample frames for canvas

    // Generate frames
    for (let f = 0; f < frameCount; f++) {
      const canvas = createCanvas(this.width, this.height);
      const ctx = canvas.getContext('2d');
      const progress = f / frameCount;

      this.drawAtmosphericBackground(ctx, emotion, progress, index);

      const frameBuffer = canvas.toBuffer('image/jpeg', { quality: 0.85 });
      fs.writeFileSync(path.join(framesDir, `frame_${String(f).padStart(4, '0')}.jpg`), frameBuffer);
    }

    // If only sample frames, duplicate them
    for (let f = frameCount; f < totalFrames; f++) {
      const sourceFrame = f % frameCount;
      const src = path.join(framesDir, `frame_${String(sourceFrame).padStart(4, '0')}.jpg`);
      const dst = path.join(framesDir, `frame_${String(f).padStart(4, '0')}.jpg`);
      fs.copyFileSync(src, dst);
    }

    // Convert frames to video
    const command = [
      this.ffmpegPath,
      '-framerate', this.fps.toString(),
      '-i', `"${path.join(framesDir, 'frame_%04d.jpg')}"`,
      '-c:v', 'libx264',
      '-preset', 'fast',
      '-crf', '23',
      '-pix_fmt', 'yuv420p',
      '-an',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    execSync(command, { stdio: 'pipe', timeout: 60000 });

    // Cleanup frames
    fs.rmSync(framesDir, { recursive: true, force: true });
  }

  /**
   * Draw atmospheric background on canvas
   */
  drawAtmosphericBackground(ctx, emotion, progress, seed) {
    // Base dark gradient
    const colors = {
      'suspense': ['#050010', '#0a0530'],
      'revelação': ['#100500', '#300a00'],
      'choque': ['#100000', '#200505'],
      'reflexão': ['#000510', '#001020'],
      'gancho': ['#000510', '#0a001a'],
    };

    const [color1, color2] = colors[emotion] || colors['suspense'];
    const gradient = ctx.createLinearGradient(0, 0, 0, this.height);
    gradient.addColorStop(0, color1);
    gradient.addColorStop(1, color2);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, this.width, this.height);

    // Animated particles
    const particleCount = 80;
    for (let i = 0; i < particleCount; i++) {
      const x = ((seed * 137 + i * 71 + progress * 200) % this.width);
      const y = ((seed * 89 + i * 113 + progress * 150) % this.height);
      const r = (i % 3) + 1;
      const alpha = 0.03 + (i % 5) * 0.01;

      ctx.fillStyle = emotion === 'revelação'
        ? `rgba(255, 100, 0, ${alpha})`
        : `rgba(100, 50, 255, ${alpha})`;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }

    // Light rays effect
    ctx.save();
    ctx.globalAlpha = 0.03 + Math.sin(progress * Math.PI) * 0.02;
    const rayGradient = ctx.createRadialGradient(
      this.width / 2, 0, 0,
      this.width / 2, 0, this.height * 0.8
    );
    rayGradient.addColorStop(0, 'rgba(150, 50, 255, 0.4)');
    rayGradient.addColorStop(1, 'rgba(0, 0, 0, 0)');
    ctx.fillStyle = rayGradient;
    ctx.fillRect(0, 0, this.width, this.height);
    ctx.restore();
  }

  /**
   * Create simple black slide
   */
  async createBlackSlide(outputPath, duration) {
    const command = [
      this.ffmpegPath,
      '-f', 'lavfi',
      '-i', `color=c=black:s=${this.width}x${this.height}:r=${this.fps}`,
      '-t', duration.toString(),
      '-c:v', 'libx264',
      '-preset', 'ultrafast',
      '-pix_fmt', 'yuv420p',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    execSync(command, { stdio: 'pipe', timeout: 30000 });
  }

  /**
   * Concatenate all slides with smooth transitions
   */
  async concatenateSlides(slidePaths, sessionId) {
    const listPath = path.join(this.tempDir, `concat_${sessionId}.txt`);
    const outputPath = path.join(this.tempDir, `raw_${sessionId}.mp4`);

    // Write concat list
    const listContent = slidePaths.map(p => `file '${p}'\n`).join('');
    fs.writeFileSync(listPath, listContent);

    const command = [
      this.ffmpegPath,
      '-f', 'concat',
      '-safe', '0',
      '-i', `"${listPath}"`,
      '-c:v', 'libx264',
      '-preset', 'fast',
      '-crf', '22',
      '-pix_fmt', 'yuv420p',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    execSync(command, { stdio: 'pipe', timeout: 120000 });
    return outputPath;
  }

  /**
   * Apply dark cinematic filters
   */
  async applyDarkFilters(inputPath, style, sessionId) {
    const outputPath = path.join(this.tempDir, `filtered_${sessionId}.mp4`);
    const filter = this.DARK_FILTERS[style] || this.DARK_FILTERS.cinematic;

    const command = [
      this.ffmpegPath,
      '-i', `"${inputPath}"`,
      '-vf', `"${filter}"`,
      '-c:v', 'libx264',
      '-preset', 'fast',
      '-crf', '22',
      '-pix_fmt', 'yuv420p',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    execSync(command, { stdio: 'pipe', timeout: 120000 });
    return outputPath;
  }

  /**
   * Burn subtitles into video using FFmpeg
   */
  async burnSubtitles(inputPath, subtitleData, script, sessionId) {
    const outputPath = path.join(this.tempDir, `subtitled_${sessionId}.mp4`);

    // Generate ASS subtitle file
    const assPath = path.join(this.tempDir, `subs_${sessionId}.ass`);
    const SubtitleRenderer = require('./subtitleRenderer');
    const renderer = new SubtitleRenderer({
      width: this.width,
      height: this.height,
    });

    // Create timing data from segments
    const timingData = [];
    let currentTime = 0;
    script.segments.forEach((seg, i) => {
      const words = seg.text.split(' ');
      const wordDuration = seg.duration / words.length;
      words.forEach((word, j) => {
        timingData.push({
          word,
          startTime: currentTime + j * wordDuration,
          duration: wordDuration,
          segment: i,
          isEmphasis: seg.emphasis?.includes(word.toLowerCase()) || false,
          emotion: seg.emotion,
        });
      });
      currentTime += seg.duration;
    });

    const subtitleFrames = renderer.generateSubtitleData(timingData, 'tiktok');
    renderer.generateASSFile(subtitleFrames, assPath, 'tiktok');

    // Burn subtitles
    const command = [
      this.ffmpegPath,
      '-i', `"${inputPath}"`,
      '-vf', `"ass='${assPath.replace(/\\/g, '/').replace(/:/g, '\\:')}'"`  ,
      '-c:v', 'libx264',
      '-preset', 'fast',
      '-crf', '22',
      '-pix_fmt', 'yuv420p',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    try {
      execSync(command, { stdio: 'pipe', timeout: 120000 });
      return outputPath;
    } catch (err) {
      console.warn('⚠️  Subtitle burn failed, using video without burned subs');
      return inputPath;
    }
  }

  /**
   * Mix narration audio with background music
   */
  async mixAudio(videoPath, narratorAudioPath, musicPath, musicVolume, outputPath) {
    let audioFilter = '';
    let inputArgs = ['-i', `"${videoPath}"`];

    if (narratorAudioPath && fs.existsSync(narratorAudioPath)) {
      inputArgs.push('-i', `"${narratorAudioPath}"`);

      if (musicPath && fs.existsSync(musicPath)) {
        inputArgs.push('-i', `"${musicPath}"`);

        // Mix narration + music — simple amix, no aloop (music file is pre-generated long enough)
        audioFilter = `-filter_complex "[1:a]volume=1.0[nar];[2:a]volume=${musicVolume}[mus];[nar][mus]amix=inputs=2:duration=first:dropout_transition=2[audio]" -map 0:v -map "[audio]"`;
      } else {
        // Only narration
        audioFilter = `-map 0:v -map 1:a`;
      }
    } else if (musicPath && fs.existsSync(musicPath)) {
      // Only music
      inputArgs.push('-i', `"${musicPath}"`);
      audioFilter = `-filter_complex "[1:a]volume=${musicVolume}[audio]" -map 0:v -map "[audio]"`;
    } else {
      // No audio
      audioFilter = '-an';
    }

    const command = [
      this.ffmpegPath,
      ...inputArgs,
      audioFilter,
      '-c:v', 'copy',
      '-c:a', 'aac',
      '-b:a', '192k',
      '-shortest',
      `"${outputPath}"`,
      '-y',
    ].join(' ');

    execSync(command, { stdio: 'pipe', timeout: 120000 });
    return outputPath;
  }

  /**
   * Fallback: create simple title card video
   */
  async createFallbackVideo(script, audioFile, outputPath, sessionId) {
    // Create a single title card with the script title
    const canvas = createCanvas(this.width, this.height);
    const ctx = canvas.getContext('2d');

    // Dark background
    const gradient = ctx.createLinearGradient(0, 0, 0, this.height);
    gradient.addColorStop(0, '#050010');
    gradient.addColorStop(0.5, '#0a0520');
    gradient.addColorStop(1, '#000005');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, this.width, this.height);

    // Title
    ctx.fillStyle = '#FFFFFF';
    ctx.font = 'bold 80px Arial';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(script.title || 'Vídeo Viral', this.width / 2, this.height * 0.3);

    // Hook
    ctx.fillStyle = '#FFD700';
    ctx.font = 'bold 60px Arial';
    const hookText = script.hook || '';
    const hookLines = this.wrapText(ctx, hookText, this.width - 100);
    hookLines.forEach((line, i) => {
      ctx.fillText(line, this.width / 2, this.height / 2 + i * 80);
    });

    // Save frame
    const frameBuffer = canvas.toBuffer('image/jpeg', { quality: 0.9 });
    const framePath = path.join(this.tempDir, `fallback_frame_${sessionId}.jpg`);
    fs.writeFileSync(framePath, frameBuffer);

    const totalDuration = script.segments?.reduce((s, seg) => s + (seg.duration || 3), 0) || 30;

    // Create video from frame
    const videoCommand = [
      this.ffmpegPath,
      '-loop', '1',
      '-i', `"${framePath}"`,
      audioFile && fs.existsSync(audioFile) ? `-i "${audioFile}"` : '',
      '-t', totalDuration.toString(),
      '-vf', `"scale=${this.width}:${this.height}"`,
      '-c:v', 'libx264',
      '-preset', 'ultrafast',
      '-pix_fmt', 'yuv420p',
      audioFile && fs.existsSync(audioFile) ? '-c:a aac -shortest' : '-an',
      `"${outputPath}"`,
      '-y',
    ].filter(Boolean).join(' ');

    execSync(videoCommand, { stdio: 'pipe', timeout: 60000 });
    return outputPath;
  }

  /**
   * Wrap text to multiple lines
   */
  wrapText(ctx, text, maxWidth) {
    const words = text.split(' ');
    const lines = [];
    let currentLine = '';

    words.forEach(word => {
      const testLine = currentLine ? `${currentLine} ${word}` : word;
      const metrics = ctx.measureText(testLine);
      if (metrics.width > maxWidth && currentLine) {
        lines.push(currentLine);
        currentLine = word;
      } else {
        currentLine = testLine;
      }
    });
    if (currentLine) lines.push(currentLine);
    return lines;
  }

  /**
   * Cleanup temporary files
   */
  async cleanup(sessionId, slidePaths = []) {
    const patterns = [
      path.join(this.tempDir, `*_${sessionId}.*`),
      path.join(this.tempDir, `concat_${sessionId}.txt`),
    ];

    // Delete slide files
    slidePaths.forEach(p => {
      if (fs.existsSync(p)) fs.unlinkSync(p);
    });

    // Delete temp video files
    ['raw_', 'filtered_', 'subtitled_', 'fallback_frame_'].forEach(prefix => {
      const ext = prefix.includes('frame') ? '.jpg' : '.mp4';
      const p = path.join(this.tempDir, `${prefix}${sessionId}${ext}`);
      if (fs.existsSync(p)) fs.unlinkSync(p);
    });
  }

  getAvailableStyles() {
    return Object.keys(this.DARK_FILTERS);
  }
}

module.exports = VideoComposer;
