/**
 * Voice Generator Module
 * Generates narration using TTS APIs with dark/mysterious tone
 * Supports multiple voices and languages
 */

const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

class VoiceGenerator {
  constructor(options = {}) {
    this.outputDir = options.outputDir || path.join(__dirname, '../temp');
    this.defaultVoice = options.voice || 'pt-BR-AntonioNeural';
    this.defaultRate = options.rate || '-10%'; // Slightly slower for drama
    this.defaultPitch = options.pitch || '-5Hz'; // Slightly lower pitch for dark feel

    // Voice options for dark content
    this.VOICE_PROFILES = {
      'dark-male': {
        voice: 'pt-BR-AntonioNeural',
        rate: '-15%',
        pitch: '-8Hz',
        description: 'Voz masculina grave e misteriosa',
      },
      'dark-female': {
        voice: 'pt-BR-FranciscaNeural',
        rate: '-10%',
        pitch: '-3Hz',
        description: 'Voz feminina sussurrante e intensa',
      },
      'narrator': {
        voice: 'pt-BR-AntonioNeural',
        rate: '-5%',
        pitch: '0Hz',
        description: 'Voz de narrador dramático',
      },
      'whisper': {
        voice: 'pt-BR-FranciscaNeural',
        rate: '-20%',
        pitch: '-10Hz',
        description: 'Sussurro misterioso',
      },
      'en-dark': {
        voice: 'en-US-GuyNeural',
        rate: '-15%',
        pitch: '-5Hz',
        description: 'English dark male voice',
      },
    };

    this.ensureOutputDir();
  }

  ensureOutputDir() {
    if (!fs.existsSync(this.outputDir)) {
      fs.mkdirSync(this.outputDir, { recursive: true });
    }
  }

  /**
   * Generate voice using edge-tts (Microsoft Edge TTS - Free)
   */
  async generateWithEdgeTTS(text, options = {}) {
    const {
      voice = this.defaultVoice,
      rate = this.defaultRate,
      pitch = this.defaultPitch,
      outputFile = null,
    } = options;

    const fileName = outputFile || `voice_${Date.now()}.mp3`;
    const filePath = path.join(this.outputDir, fileName);

    // Add dramatic SSML pauses and emphasis
    const enhancedText = this.addDramaticPauses(text);

    try {
      // Try using edge-tts Python package if available
      const command = `edge-tts --voice "${voice}" --rate "${rate}" --pitch "${pitch}" --text "${enhancedText.replace(/"/g, '\\"')}" --write-media "${filePath}" 2>/dev/null`;
      execSync(command, { stdio: 'pipe', timeout: 30000 });

      if (fs.existsSync(filePath)) {
        return {
          success: true,
          filePath,
          voice,
          method: 'edge-tts',
          duration: await this.getAudioDuration(filePath),
        };
      }
    } catch (err) {
      // Fall through to alternative methods
    }

    // Alternative: Google TTS (free, basic)
    return this.generateWithGTTS(text, options);
  }

  /**
   * Generate voice using Google TTS (fallback)
   */
  async generateWithGTTS(text, options = {}) {
    const { outputFile = null } = options;
    const fileName = outputFile || `voice_${Date.now()}.mp3`;
    const filePath = path.join(this.outputDir, fileName);

    // Use Google TTS API directly
    const googleTTSUrl = this.buildGoogleTTSUrl(text);

    return new Promise((resolve, reject) => {
      const file = fs.createWriteStream(filePath);
      const protocol = googleTTSUrl.startsWith('https') ? https : http;

      protocol.get(googleTTSUrl, (response) => {
        response.pipe(file);
        file.on('finish', () => {
          file.close();
          resolve({
            success: true,
            filePath,
            voice: 'google-tts',
            method: 'gtts',
            duration: null, // Will be set later
          });
        });
      }).on('error', (err) => {
        fs.unlink(filePath, () => {});
        reject(err);
      });
    });
  }

  /**
   * Build Google TTS URL
   */
  buildGoogleTTSUrl(text) {
    const encodedText = encodeURIComponent(text.substring(0, 200));
    return `https://translate.google.com/translate_tts?ie=UTF-8&q=${encodedText}&tl=pt-BR&client=gtx&ttsspeed=0.7`;
  }

  /**
   * Generate voice for entire script with timing
   */
  async generateScriptVoice(script, voiceProfile = 'dark-male') {
    const profile = this.VOICE_PROFILES[voiceProfile] || this.VOICE_PROFILES['dark-male'];
    const segments = [];
    let totalDuration = 0;

    console.log(`\n🎤 Gerando narração com voz: ${profile.description}`);

    for (let i = 0; i < script.segments.length; i++) {
      const segment = script.segments[i];
      const fileName = `segment_${i}_${Date.now()}.mp3`;

      console.log(`   📝 Segmento ${i + 1}/${script.segments.length}: "${segment.text.substring(0, 40)}..."`);

      try {
        const result = await this.generateWithEdgeTTS(segment.text, {
          voice: profile.voice,
          rate: profile.rate,
          pitch: profile.pitch,
          outputFile: fileName,
        });

        segments.push({
          ...segment,
          audioFile: result.filePath,
          audioDuration: result.duration || segment.duration,
          startTime: totalDuration,
        });

        totalDuration += result.duration || segment.duration;
      } catch (err) {
        console.error(`   ⚠️  Erro no segmento ${i + 1}: ${err.message}`);
        // Create silent placeholder
        segments.push({
          ...segment,
          audioFile: null,
          audioDuration: segment.duration,
          startTime: totalDuration,
        });
        totalDuration += segment.duration;
      }

      // Small delay between API calls
      await this.sleep(500);
    }

    // Generate full script audio as one file
    let fullAudioFile = null;
    try {
      const fullFileName = `full_narration_${Date.now()}.mp3`;
      const fullResult = await this.generateWithEdgeTTS(script.fullScript, {
        voice: profile.voice,
        rate: profile.rate,
        pitch: profile.pitch,
        outputFile: fullFileName,
      });
      fullAudioFile = fullResult.filePath;
    } catch (err) {
      console.error('⚠️  Erro ao gerar áudio completo:', err.message);
    }

    return {
      segments,
      fullAudioFile,
      totalDuration,
      voiceProfile,
      profile,
    };
  }

  /**
   * Add dramatic pauses to text for more impact
   */
  addDramaticPauses(text) {
    return text
      .replace(/\[PAUSA\]/gi, '... ')
      .replace(/\.\.\./g, '... ')
      .replace(/([!?])\s/g, '$1 ... ')
      .trim();
  }

  /**
   * Get audio duration using ffprobe
   */
  async getAudioDuration(filePath) {
    try {
      const result = execSync(
        `ffprobe -v quiet -print_format json -show_streams "${filePath}" 2>/dev/null`,
        { encoding: 'utf8', timeout: 5000 }
      );
      const data = JSON.parse(result);
      const audioStream = data.streams.find(s => s.codec_type === 'audio');
      return audioStream ? parseFloat(audioStream.duration) : null;
    } catch {
      return null;
    }
  }

  /**
   * Apply audio effects for dark/dramatic feel
   */
  async applyDarkEffects(inputFile, outputFile) {
    try {
      // Apply reverb, slight pitch shift and compression for dark effect
      const command = `ffmpeg -i "${inputFile}" -af "aecho=0.8:0.88:60:0.4,bass=g=3,volume=1.2" "${outputFile}" -y 2>/dev/null`;
      execSync(command, { timeout: 30000 });
      return outputFile;
    } catch {
      return inputFile; // Return original if effects fail
    }
  }

  /**
   * Generate timing data for subtitle synchronization
   */
  generateSubtitleTiming(script, voiceResult) {
    const timing = [];
    let currentTime = 0;

    script.segments.forEach((segment, index) => {
      const words = segment.text.split(' ');
      const segmentDuration = voiceResult.segments[index]?.audioDuration || segment.duration;
      const wordDuration = segmentDuration / words.length;

      words.forEach((word, wordIndex) => {
        timing.push({
          word: word.replace(/[^\w\sÀ-ÿ]/g, ''),
          startTime: currentTime + (wordIndex * wordDuration),
          duration: wordDuration,
          segment: index,
          isEmphasis: segment.emphasis?.includes(word.toLowerCase()) || false,
          emotion: segment.emotion,
        });
      });

      currentTime += segmentDuration;
    });

    return timing;
  }

  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  listVoiceProfiles() {
    return Object.entries(this.VOICE_PROFILES).map(([key, profile]) => ({
      id: key,
      ...profile,
    }));
  }
}

module.exports = VoiceGenerator;
