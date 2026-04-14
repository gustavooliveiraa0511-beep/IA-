/**
 * Subtitle Renderer Module
 * Creates TikTok-style dynamic captions with animations
 * Ken Burns effect, word-by-word reveals, emphasis highlights
 */

const { createCanvas, loadImage } = require('canvas');
const fs = require('fs');
const path = require('path');
const { execSync, exec } = require('child_process');

class SubtitleRenderer {
  constructor(options = {}) {
    this.width = options.width || 1080;
    this.height = options.height || 1920;
    this.fps = options.fps || 30;
    this.outputDir = options.outputDir || path.join(__dirname, '../temp');

    // TikTok-style subtitle configuration
    this.SUBTITLE_STYLES = {
      tiktok: {
        fontFamily: 'Arial Black',
        fontSize: 72,
        fontWeight: 'bold',
        textColor: '#FFFFFF',
        strokeColor: '#000000',
        strokeWidth: 6,
        highlightColor: '#FFD700', // Gold for emphasis
        secondaryHighlight: '#FF4444', // Red for shock words
        position: 'center', // vertical center-bottom area
        bottomMargin: 350,
        maxWidth: 900,
        lineHeight: 90,
        shadowBlur: 20,
        shadowColor: 'rgba(0,0,0,0.9)',
        animation: 'pop', // pop, slide, fade
        wordSpacing: 10,
      },
      minimal: {
        fontFamily: 'Arial',
        fontSize: 56,
        fontWeight: 'bold',
        textColor: '#FFFFFF',
        strokeColor: '#000000',
        strokeWidth: 4,
        highlightColor: '#00FF88',
        position: 'bottom',
        bottomMargin: 200,
        maxWidth: 850,
        lineHeight: 70,
        shadowBlur: 15,
        shadowColor: 'rgba(0,0,0,0.8)',
        animation: 'fade',
      },
      dramatic: {
        fontFamily: 'Arial Black',
        fontSize: 80,
        fontWeight: '900',
        textColor: '#FF4444',
        strokeColor: '#000000',
        strokeWidth: 8,
        highlightColor: '#FFFFFF',
        position: 'center',
        bottomMargin: 400,
        maxWidth: 920,
        lineHeight: 100,
        shadowBlur: 30,
        shadowColor: 'rgba(255,0,0,0.3)',
        animation: 'zoom',
      },
    };

    // Dark color palettes
    this.DARK_OVERLAYS = [
      'rgba(0,0,0,0.5)',
      'rgba(10,0,20,0.6)',
      'rgba(0,10,30,0.5)',
      'rgba(20,0,0,0.5)',
    ];

    // Ken Burns motion presets
    this.KEN_BURNS_PRESETS = [
      { startScale: 1.0, endScale: 1.15, startX: 0, startY: 0, endX: -30, endY: -20 },
      { startScale: 1.15, endScale: 1.0, startX: -30, startY: -20, endX: 0, endY: 0 },
      { startScale: 1.0, endScale: 1.1, startX: 0, startY: 0, endX: 20, endY: -15 },
      { startScale: 1.05, endScale: 1.05, startX: -20, startY: 0, endX: 20, endY: 0 },
      { startScale: 1.1, endScale: 1.0, startX: 15, startY: 10, endX: -15, endY: -10 },
    ];

    this.ensureOutputDir();
  }

  ensureOutputDir() {
    if (!fs.existsSync(this.outputDir)) {
      fs.mkdirSync(this.outputDir, { recursive: true });
    }
  }

  /**
   * Generate subtitle frames for canvas-based rendering
   */
  generateSubtitleData(subtitleTiming, style = 'tiktok') {
    const styleConfig = this.SUBTITLE_STYLES[style] || this.SUBTITLE_STYLES.tiktok;
    const subtitleFrames = [];

    // Group words into display chunks (2-4 words at a time)
    const chunks = this.groupIntoChunks(subtitleTiming, 3);

    let frameTime = 0;
    chunks.forEach(chunk => {
      const text = chunk.words.join(' ');
      const startTime = chunk.startTime;
      const endTime = chunk.startTime + chunk.duration;
      const hasEmphasis = chunk.words.some(w => chunk.emphasis?.includes(w.toLowerCase()));

      subtitleFrames.push({
        text,
        words: chunk.words,
        startTime,
        endTime,
        duration: chunk.duration,
        hasEmphasis,
        emotion: chunk.emotion,
        style: styleConfig,
        animation: this.getAnimationForEmotion(chunk.emotion, styleConfig.animation),
        textColor: hasEmphasis ? styleConfig.highlightColor : styleConfig.textColor,
      });
    });

    return subtitleFrames;
  }

  /**
   * Group subtitle timing into visual chunks
   */
  groupIntoChunks(subtitleTiming, wordsPerChunk = 3) {
    const chunks = [];
    let i = 0;

    while (i < subtitleTiming.length) {
      const chunk = {
        words: [],
        startTime: subtitleTiming[i].startTime,
        duration: 0,
        emotion: subtitleTiming[i].emotion,
        emphasis: [],
      };

      const end = Math.min(i + wordsPerChunk, subtitleTiming.length);
      for (let j = i; j < end; j++) {
        chunk.words.push(subtitleTiming[j].word);
        chunk.duration += subtitleTiming[j].duration;
        if (subtitleTiming[j].isEmphasis) {
          chunk.emphasis.push(subtitleTiming[j].word);
        }
      }

      chunks.push(chunk);
      i = end;
    }

    return chunks;
  }

  /**
   * Get animation type based on emotion
   */
  getAnimationForEmotion(emotion, defaultAnimation = 'pop') {
    const emotionAnimations = {
      'gancho': 'zoom',
      'suspense': 'fade',
      'revelação': 'pop',
      'choque': 'shake',
      'reflexão': 'fade',
    };
    return emotionAnimations[emotion] || defaultAnimation;
  }

  /**
   * Create a video frame with subtitle overlay using canvas
   */
  async createSubtitleFrame(backgroundImagePath, subtitleData, frameProgress) {
    const canvas = createCanvas(this.width, this.height);
    const ctx = canvas.getContext('2d');

    // Draw background (will be handled by video composer)
    ctx.fillStyle = '#000000';
    ctx.fillRect(0, 0, this.width, this.height);

    // Load and draw background image if provided
    if (backgroundImagePath && fs.existsSync(backgroundImagePath)) {
      try {
        const bgImage = await loadImage(backgroundImagePath);
        this.drawKenBurns(ctx, bgImage, frameProgress, this.KEN_BURNS_PRESETS[0]);
      } catch (err) {
        // Use gradient background on error
        this.drawDarkGradient(ctx);
      }
    } else {
      this.drawDarkGradient(ctx);
    }

    // Dark overlay for text readability
    const overlayGradient = ctx.createLinearGradient(0, this.height * 0.4, 0, this.height);
    overlayGradient.addColorStop(0, 'rgba(0,0,0,0)');
    overlayGradient.addColorStop(0.5, 'rgba(0,0,0,0.4)');
    overlayGradient.addColorStop(1, 'rgba(0,0,0,0.85)');
    ctx.fillStyle = overlayGradient;
    ctx.fillRect(0, 0, this.width, this.height);

    // Draw subtitle if provided
    if (subtitleData) {
      this.drawSubtitle(ctx, subtitleData, frameProgress);
    }

    return canvas;
  }

  /**
   * Draw text with Ken Burns pan effect
   */
  drawKenBurns(ctx, image, progress, preset) {
    const scale = preset.startScale + (preset.endScale - preset.startScale) * progress;
    const x = preset.startX + (preset.endX - preset.startX) * progress;
    const y = preset.startY + (preset.endY - preset.startY) * progress;

    const scaledWidth = this.width * scale;
    const scaledHeight = this.height * scale;
    const offsetX = (this.width - scaledWidth) / 2 + x;
    const offsetY = (this.height - scaledHeight) / 2 + y;

    // Draw image maintaining aspect ratio
    const imgRatio = image.width / image.height;
    const canvasRatio = this.width / this.height;

    let drawWidth, drawHeight, drawX, drawY;
    if (imgRatio > canvasRatio) {
      drawHeight = scaledHeight;
      drawWidth = scaledHeight * imgRatio;
      drawX = (this.width - drawWidth) / 2 + x;
      drawY = offsetY;
    } else {
      drawWidth = scaledWidth;
      drawHeight = scaledWidth / imgRatio;
      drawX = offsetX;
      drawY = (this.height - drawHeight) / 2 + y;
    }

    ctx.drawImage(image, drawX, drawY, drawWidth, drawHeight);
  }

  /**
   * Draw dark gradient background
   */
  drawDarkGradient(ctx) {
    const gradient = ctx.createLinearGradient(0, 0, 0, this.height);
    gradient.addColorStop(0, '#0a0010');
    gradient.addColorStop(0.5, '#050020');
    gradient.addColorStop(1, '#000005');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, this.width, this.height);

    // Add some atmospheric particles
    ctx.fillStyle = 'rgba(100, 0, 255, 0.05)';
    for (let i = 0; i < 50; i++) {
      const x = Math.random() * this.width;
      const y = Math.random() * this.height;
      const r = Math.random() * 3;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /**
   * Draw animated subtitle text
   */
  drawSubtitle(ctx, subtitleData, progress) {
    const { text, style, animation, textColor, hasEmphasis, emotion } = subtitleData;

    if (!text || !text.trim()) return;

    // Calculate animation values
    const animValues = this.calculateAnimation(animation, progress);

    ctx.save();
    ctx.globalAlpha = animValues.alpha;

    // Apply transform
    const centerX = this.width / 2;
    const textY = this.height - (style.bottomMargin || 350);

    ctx.translate(centerX, textY);
    ctx.scale(animValues.scale, animValues.scale);
    ctx.translate(-centerX, -textY);

    // Word-by-word rendering with emphasis
    const words = text.split(' ');
    const displayWords = words.slice(0, Math.ceil(words.length * Math.min(progress * 3, 1)));

    // Set font
    ctx.font = `${style.fontWeight} ${style.fontSize}px "${style.fontFamily}", Arial, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    // Draw background pill for better readability
    const textMetrics = ctx.measureText(text);
    const pillWidth = Math.min(textMetrics.width + 40, style.maxWidth + 40);
    const pillHeight = style.fontSize * 1.4;
    const pillX = centerX - pillWidth / 2;
    const pillY = textY - pillHeight / 2;

    ctx.fillStyle = 'rgba(0,0,0,0.75)';
    this.roundRect(ctx, pillX, pillY, pillWidth, pillHeight, 16);
    ctx.fill();

    // Draw text shadow
    ctx.shadowBlur = style.shadowBlur || 20;
    ctx.shadowColor = style.shadowColor || 'rgba(0,0,0,0.9)';

    // Draw text stroke (outline)
    ctx.strokeStyle = style.strokeColor || '#000000';
    ctx.lineWidth = style.strokeWidth || 6;
    ctx.lineJoin = 'round';
    ctx.strokeText(displayWords.join(' '), centerX, textY);

    // Draw main text
    ctx.fillStyle = hasEmphasis ? style.highlightColor : textColor;
    ctx.shadowBlur = 0;
    ctx.fillText(displayWords.join(' '), centerX, textY);

    // Add emphasis glow effect
    if (hasEmphasis && progress > 0.3) {
      ctx.shadowBlur = 30;
      ctx.shadowColor = style.highlightColor || '#FFD700';
      ctx.fillStyle = style.highlightColor || '#FFD700';
      ctx.globalAlpha = 0.3 * animValues.alpha;
      ctx.fillText(displayWords.join(' '), centerX, textY);
    }

    ctx.restore();
  }

  /**
   * Calculate animation values based on progress
   */
  calculateAnimation(animationType, progress) {
    switch (animationType) {
      case 'pop': {
        // Bouncy pop in, hold, quick out
        let scale = 1.0;
        let alpha = 1.0;
        if (progress < 0.15) {
          scale = 0.5 + (progress / 0.15) * 0.6;
          alpha = progress / 0.15;
        } else if (progress < 0.2) {
          scale = 1.1 - ((progress - 0.15) / 0.05) * 0.1;
        } else if (progress > 0.85) {
          alpha = 1.0 - ((progress - 0.85) / 0.15);
          scale = 1.0 - ((progress - 0.85) / 0.15) * 0.1;
        }
        return { scale, alpha };
      }
      case 'zoom': {
        let scale = 1.0;
        let alpha = 1.0;
        if (progress < 0.2) {
          scale = 1.3 - (progress / 0.2) * 0.3;
          alpha = progress / 0.2;
        } else if (progress > 0.8) {
          alpha = 1.0 - ((progress - 0.8) / 0.2);
        }
        return { scale, alpha };
      }
      case 'shake': {
        const shake = Math.sin(progress * Math.PI * 8) * (1 - progress) * 0.02;
        return { scale: 1.0 + shake, alpha: Math.min(1, progress * 5, (1 - progress) * 5) };
      }
      case 'fade':
      default: {
        const alpha = progress < 0.15 ? progress / 0.15
          : progress > 0.85 ? 1.0 - ((progress - 0.85) / 0.15)
          : 1.0;
        return { scale: 1.0, alpha };
      }
    }
  }

  /**
   * Generate SRT subtitle file
   */
  generateSRTFile(subtitleFrames, outputPath) {
    let srt = '';
    subtitleFrames.forEach((frame, index) => {
      const startTime = this.secondsToSRTTime(frame.startTime);
      const endTime = this.secondsToSRTTime(frame.endTime);
      srt += `${index + 1}\n${startTime} --> ${endTime}\n${frame.text}\n\n`;
    });

    fs.writeFileSync(outputPath, srt, 'utf8');
    return outputPath;
  }

  /**
   * Convert seconds to SRT timestamp format
   */
  secondsToSRTTime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    const ms = Math.floor((seconds % 1) * 1000);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')},${String(ms).padStart(3, '0')}`;
  }

  /**
   * Generate ASS subtitle file with styling (better for FFmpeg)
   */
  generateASSFile(subtitleFrames, outputPath, style = 'tiktok') {
    const styleConfig = this.SUBTITLE_STYLES[style] || this.SUBTITLE_STYLES.tiktok;
    const fontSize = Math.floor(styleConfig.fontSize * 0.7); // Scale for video dimensions

    const header = `[Script Info]
ScriptType: v4.00+
PlayResX: ${this.width}
PlayResY: ${this.height}
Collisions: Normal

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,${styleConfig.fontFamily},${fontSize},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,30,30,60,1
Style: Emphasis,${styleConfig.fontFamily},${fontSize},&H0000D4FF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,110,110,0,0,1,4,2,2,30,30,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
`;

    let events = '';
    subtitleFrames.forEach((frame) => {
      const start = this.secondsToASSTime(frame.startTime);
      const end = this.secondsToASSTime(frame.endTime);
      const styleName = frame.hasEmphasis ? 'Emphasis' : 'Default';
      const escapedText = frame.text.replace(/\n/g, '\\N');

      // Add fade animation
      const fadeIn = 150;
      const fadeOut = 150;
      const effect = `{\\fad(${fadeIn},${fadeOut})}`;

      events += `Dialogue: 0,${start},${end},${styleName},,0,0,0,,${effect}${escapedText}\n`;
    });

    const assContent = header + events;
    fs.writeFileSync(outputPath, assContent, 'utf8');
    return outputPath;
  }

  /**
   * Convert seconds to ASS timestamp format
   */
  secondsToASSTime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    const cs = Math.floor((seconds % 1) * 100); // Centiseconds
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}`;
  }

  /**
   * Helper: draw rounded rectangle
   */
  roundRect(ctx, x, y, width, height, radius) {
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + width - radius, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
    ctx.lineTo(x + width, y + height - radius);
    ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
    ctx.lineTo(x + radius, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
  }

  getAvailableStyles() {
    return Object.keys(this.SUBTITLE_STYLES);
  }
}

module.exports = SubtitleRenderer;
