/**
 * Music Library Module
 * Manages background music for dark/viral content
 * Generates or fetches royalty-free dark music
 */

const fs = require('fs');
const path = require('path');
const https = require('https');
const { execSync } = require('child_process');

class MusicLibrary {
  constructor(options = {}) {
    this.musicDir = options.musicDir || path.join(__dirname, '../assets/music');
    this.tempDir = options.tempDir || path.join(__dirname, '../temp');

    // Curated dark/atmospheric music from free sources
    this.MUSIC_CATALOG = {
      dark: [
        {
          name: 'Dark Ambient Drone',
          url: 'https://www.soundjay.com/ambient/sounds/ambience-01.mp3',
          mood: 'dark',
          bpm: 0,
          duration: 60,
        },
        {
          name: 'Dark Atmosphere',
          url: null, // Will use generated
          mood: 'dark',
          bpm: 0,
          generated: true,
        },
      ],
      suspense: [
        {
          name: 'Ticking Clock Suspense',
          url: null,
          mood: 'suspense',
          generated: true,
        },
      ],
      emotional: [
        {
          name: 'Emotional Piano',
          url: null,
          mood: 'emotional',
          generated: true,
        },
      ],
      dramatic: [
        {
          name: 'Epic Drama',
          url: null,
          mood: 'dramatic',
          generated: true,
        },
      ],
      mysterious: [
        {
          name: 'Mystery Ambience',
          url: null,
          mood: 'mysterious',
          generated: true,
        },
      ],
    };

    this.ensureDirs();
  }

  ensureDirs() {
    [this.musicDir, this.tempDir].forEach(dir => {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
    });
  }

  /**
   * Get music for given mood
   */
  async getMusicForMood(mood = 'dark') {
    const catalog = this.MUSIC_CATALOG[mood] || this.MUSIC_CATALOG.dark;
    const track = catalog[0];

    // Check if we have cached music
    const cachedPath = path.join(this.musicDir, `${mood}_music.mp3`);
    if (fs.existsSync(cachedPath)) {
      return cachedPath;
    }

    // Try to download if URL available
    if (track.url) {
      try {
        await this.downloadMusic(track.url, cachedPath);
        return cachedPath;
      } catch (err) {
        console.log(`   ℹ️  Could not download ${track.name}, generating...`);
      }
    }

    // Generate using FFmpeg
    return this.generateDarkMusic(mood, cachedPath);
  }

  /**
   * Download music file
   */
  downloadMusic(url, outputPath) {
    return new Promise((resolve, reject) => {
      const file = fs.createWriteStream(outputPath);
      https.get(url, { headers: { 'User-Agent': 'ViralVideoApp/1.0' } }, (response) => {
        if (response.statusCode !== 200) {
          reject(new Error(`HTTP ${response.statusCode}`));
          return;
        }
        response.pipe(file);
        file.on('finish', () => { file.close(); resolve(outputPath); });
      }).on('error', reject);
    });
  }

  /**
   * Generate dark atmospheric music using FFmpeg audio synthesis
   */
  async generateDarkMusic(mood, outputPath) {
    const duration = 120; // 2 minutes loop

    // Different sine wave combinations for different moods
    const moodSettings = {
      dark: {
        freqs: [55, 110, 165], // A1, A2, E3 - dark and low
        volumes: [0.3, 0.2, 0.1],
        tremolo: 0.3,
        reverb: 0.7,
      },
      suspense: {
        freqs: [82.41, 164.81, 246], // E2, E3, B3
        volumes: [0.25, 0.2, 0.15],
        tremolo: 1.5,
        reverb: 0.5,
      },
      emotional: {
        freqs: [196, 246.94, 293.66], // G3, B3, D4
        volumes: [0.2, 0.15, 0.1],
        tremolo: 0.5,
        reverb: 0.8,
      },
      dramatic: {
        freqs: [65.41, 130.81, 196], // C2, C3, G3
        volumes: [0.35, 0.25, 0.15],
        tremolo: 0.8,
        reverb: 0.6,
      },
      mysterious: {
        freqs: [73.42, 110, 164.81], // D2, A2, E3
        volumes: [0.2, 0.15, 0.1],
        tremolo: 0.2,
        reverb: 0.9,
      },
    };

    const settings = moodSettings[mood] || moodSettings.dark;

    try {
      // Generate layered sine waves for atmospheric effect
      const filters = settings.freqs.map((freq, i) => {
        return `sine=frequency=${freq}:sample_rate=44100,volume=${settings.volumes[i]}`;
      });

      // Main audio generation command using amix
      const command = [
        'ffmpeg',
        '-f', 'lavfi',
        '-i', `sine=frequency=${settings.freqs[0]}:sample_rate=44100:duration=${duration}`,
        '-f', 'lavfi',
        '-i', `sine=frequency=${settings.freqs[1]}:sample_rate=44100:duration=${duration}`,
        '-f', 'lavfi',
        '-i', `sine=frequency=${settings.freqs[2]}:sample_rate=44100:duration=${duration}`,
        '-filter_complex',
        `"[0:a]volume=${settings.volumes[0]},atremolo=d=${settings.tremolo}:f=0.1[a0];[1:a]volume=${settings.volumes[1]}[a1];[2:a]volume=${settings.volumes[2]}[a2];[a0][a1][a2]amix=inputs=3:duration=shortest,aecho=0.8:0.9:${Math.floor(settings.reverb * 200)}:0.${Math.floor(settings.reverb * 5)},lowpass=f=800,volume=0.4"`,
        '-t', duration.toString(),
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        `"${outputPath}"`,
        '-y',
      ].join(' ');

      execSync(command, { stdio: 'pipe', timeout: 60000 });
      console.log(`   🎵 Música ${mood} gerada: ${path.basename(outputPath)}`);
      return outputPath;
    } catch (err) {
      console.error(`   ⚠️  Falha na geração de música: ${err.message}`);
      // Create a silent audio file as absolute fallback
      return this.createSilentAudio(outputPath, duration);
    }
  }

  /**
   * Create silent audio track
   */
  createSilentAudio(outputPath, duration = 60) {
    try {
      const command = `ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t ${duration} -c:a libmp3lame -b:a 64k "${outputPath}" -y 2>/dev/null`;
      execSync(command, { timeout: 30000 });
      return outputPath;
    } catch {
      return null;
    }
  }

  /**
   * List available music tracks
   */
  listTracks() {
    const tracks = [];
    Object.entries(this.MUSIC_CATALOG).forEach(([mood, items]) => {
      items.forEach(item => {
        tracks.push({ mood, ...item });
      });
    });
    return tracks;
  }

  /**
   * Get mood from script
   */
  getMoodFromScript(script) {
    return script.backgroundMusicMood || 'dark';
  }
}

module.exports = MusicLibrary;
