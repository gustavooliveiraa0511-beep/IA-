/**
 * Media Fetcher Module
 * Fetches images and videos from Pexels, Pixabay and Unsplash
 * for dark/mysterious visual content
 */

const https = require('https');
const fs = require('fs');
const path = require('path');

class MediaFetcher {
  constructor(options = {}) {
    this.pexelsApiKey = options.pexelsApiKey || process.env.PEXELS_API_KEY;
    this.pixabayApiKey = options.pixabayApiKey || process.env.PIXABAY_API_KEY;
    this.outputDir = options.outputDir || path.join(__dirname, '../temp/media');
    this.timeout = options.timeout || 15000;

    // Dark visual keywords that enhance viral impact
    this.DARK_VISUAL_MODIFIERS = [
      'dark', 'mysterious', 'shadow', 'noir', 'dramatic',
      'atmospheric', 'moody', 'cinematic', 'eerie', 'haunting',
    ];

    // Categories for visual search
    this.VISUAL_CATEGORIES = {
      suspense: ['dark forest', 'empty road at night', 'storm clouds', 'shadow figure'],
      psychology: ['brain neurons', 'human eye closeup', 'mind maze', 'abstract thoughts'],
      mystery: ['abandoned building', 'foggy street', 'antique clock', 'cryptic symbols'],
      nature: ['deep ocean', 'space galaxy', 'volcano eruption', 'lightning storm'],
      urban: ['empty city night', 'neon lights', 'surveillance camera', 'crowd from above'],
      emotional: ['person silhouette', 'sunset solitude', 'rain window', 'lonely path'],
    };

    this.ensureOutputDir();
  }

  ensureOutputDir() {
    if (!fs.existsSync(this.outputDir)) {
      fs.mkdirSync(this.outputDir, { recursive: true });
    }
  }

  /**
   * Fetch media from Pexels API
   */
  async fetchFromPexels(keyword, type = 'photo', count = 5) {
    if (!this.pexelsApiKey) {
      console.log('   ℹ️  Pexels API key not configured, using Pixabay');
      return this.fetchFromPixabay(keyword, type, count);
    }

    const searchKeyword = this.enhanceKeyword(keyword);
    const endpoint = type === 'video'
      ? `https://api.pexels.com/videos/search?query=${encodeURIComponent(searchKeyword)}&per_page=${count}&orientation=portrait`
      : `https://api.pexels.com/v1/search?query=${encodeURIComponent(searchKeyword)}&per_page=${count}&orientation=portrait&size=large`;

    return this.makeRequest(endpoint, {
      'Authorization': this.pexelsApiKey,
    }).then(data => {
      if (type === 'video') {
        return (data.videos || []).map(v => ({
          id: v.id,
          url: v.video_files?.find(f => f.quality === 'hd')?.link || v.video_files?.[0]?.link,
          thumbnail: v.image,
          width: v.width,
          height: v.height,
          duration: v.duration,
          type: 'video',
          source: 'pexels',
          keyword: searchKeyword,
        }));
      } else {
        return (data.photos || []).map(p => ({
          id: p.id,
          url: p.src.large2x || p.src.large,
          thumbnail: p.src.medium,
          width: p.width,
          height: p.height,
          type: 'image',
          source: 'pexels',
          keyword: searchKeyword,
          photographer: p.photographer,
        }));
      }
    }).catch(err => {
      console.error(`   ⚠️  Pexels error for "${keyword}": ${err.message}`);
      return this.fetchFromPixabay(keyword, type, count);
    });
  }

  /**
   * Fetch media from Pixabay API (free tier)
   */
  async fetchFromPixabay(keyword, type = 'photo', count = 5) {
    const apiKey = this.pixabayApiKey || '46790126-c764f1b7a83a3ef1e27a19d62'; // Free demo key
    const searchKeyword = this.enhanceKeyword(keyword);

    let endpoint;
    if (type === 'video') {
      endpoint = `https://pixabay.com/api/videos/?key=${apiKey}&q=${encodeURIComponent(searchKeyword)}&per_page=${count}&orientation=vertical`;
    } else {
      endpoint = `https://pixabay.com/api/?key=${apiKey}&q=${encodeURIComponent(searchKeyword)}&per_page=${count}&image_type=photo&orientation=vertical&min_width=720`;
    }

    return this.makeRequest(endpoint).then(data => {
      if (type === 'video') {
        return (data.hits || []).map(v => ({
          id: v.id,
          url: v.videos?.medium?.url || v.videos?.small?.url,
          thumbnail: v.videos?.tiny?.thumbnail || v.previewURL,
          width: v.videos?.medium?.width || 1080,
          height: v.videos?.medium?.height || 1920,
          duration: v.duration,
          type: 'video',
          source: 'pixabay',
          keyword: searchKeyword,
        }));
      } else {
        return (data.hits || []).map(p => ({
          id: p.id,
          url: p.largeImageURL || p.webformatURL,
          thumbnail: p.previewURL,
          width: p.imageWidth,
          height: p.imageHeight,
          type: 'image',
          source: 'pixabay',
          keyword: searchKeyword,
        }));
      }
    }).catch(err => {
      console.error(`   ⚠️  Pixabay error for "${keyword}": ${err.message}`);
      return this.getPlaceholderMedia(keyword, count);
    });
  }

  /**
   * Fetch media for each script segment
   */
  async fetchMediaForScript(script, preferVideo = false) {
    console.log('\n🎬 Buscando mídia visual para cada segmento...');
    const mediaItems = [];

    for (let i = 0; i < script.segments.length; i++) {
      const segment = script.segments[i];
      const keyword = segment.visualKeyword || this.extractKeyword(segment.text);

      console.log(`   🔍 Segmento ${i + 1}: buscando "${keyword}"`);

      try {
        let items = [];

        if (preferVideo) {
          items = await this.fetchFromPexels(keyword, 'video', 3);
          if (!items.length) {
            items = await this.fetchFromPixabay(keyword, 'video', 3);
          }
        }

        // If no video or prefer images
        if (!items.length) {
          items = await this.fetchFromPexels(keyword, 'photo', 5);
          if (!items.length) {
            items = await this.fetchFromPixabay(keyword, 'photo', 5);
          }
        }

        if (items.length > 0) {
          // Pick best match (first result, or random for variety)
          const selectedItem = items[Math.floor(Math.random() * Math.min(3, items.length))];
          mediaItems.push({
            ...selectedItem,
            segmentIndex: i,
            segmentText: segment.text,
            segmentDuration: segment.duration,
          });
        } else {
          mediaItems.push(this.getPlaceholderMedia(keyword, 1)[0]);
        }
      } catch (err) {
        console.error(`   ⚠️  Erro na busca de mídia: ${err.message}`);
        mediaItems.push(this.getPlaceholderMedia(keyword, 1)[0]);
      }

      await this.sleep(300); // Rate limiting
    }

    return mediaItems;
  }

  /**
   * Download media file
   */
  async downloadMedia(url, fileName) {
    const filePath = path.join(this.outputDir, fileName);

    if (!url) {
      return null;
    }

    return new Promise((resolve, reject) => {
      const file = fs.createWriteStream(filePath);
      const protocol = url.startsWith('https') ? https : require('http');
      const timeoutHandle = setTimeout(() => {
        file.close();
        reject(new Error('Download timeout'));
      }, this.timeout);

      protocol.get(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; VideoBot/1.0)',
        },
      }, (response) => {
        if (response.statusCode === 301 || response.statusCode === 302) {
          clearTimeout(timeoutHandle);
          file.close();
          // Follow redirect
          this.downloadMedia(response.headers.location, fileName).then(resolve).catch(reject);
          return;
        }

        if (response.statusCode !== 200) {
          clearTimeout(timeoutHandle);
          file.close();
          reject(new Error(`HTTP ${response.statusCode}`));
          return;
        }

        response.pipe(file);
        file.on('finish', () => {
          clearTimeout(timeoutHandle);
          file.close();
          resolve(filePath);
        });
      }).on('error', (err) => {
        clearTimeout(timeoutHandle);
        fs.unlink(filePath, () => {});
        reject(err);
      });
    });
  }

  /**
   * Download all media for segments
   */
  async downloadAllMedia(mediaItems) {
    console.log('\n📥 Baixando arquivos de mídia...');
    const downloaded = [];

    for (let i = 0; i < mediaItems.length; i++) {
      const item = mediaItems[i];
      if (!item.url) {
        downloaded.push({ ...item, localPath: null });
        continue;
      }

      const ext = item.type === 'video' ? '.mp4' : '.jpg';
      const fileName = `media_${i}_${Date.now()}${ext}`;

      console.log(`   ⬇️  ${i + 1}/${mediaItems.length}: ${item.type} - ${item.keyword}`);

      try {
        const localPath = await this.downloadMedia(item.url, fileName);
        downloaded.push({ ...item, localPath });
        console.log(`   ✅ Baixado: ${fileName}`);
      } catch (err) {
        console.error(`   ❌ Falha no download: ${err.message}`);
        downloaded.push({ ...item, localPath: null });
      }

      await this.sleep(200);
    }

    return downloaded;
  }

  /**
   * Enhance keyword with dark modifiers
   */
  enhanceKeyword(keyword) {
    const darkModifier = this.DARK_VISUAL_MODIFIERS[
      Math.floor(Math.random() * this.DARK_VISUAL_MODIFIERS.length)
    ];
    return `${keyword} ${darkModifier}`;
  }

  /**
   * Extract visual keyword from text
   */
  extractKeyword(text) {
    // Remove common words and extract main nouns
    const stopWords = ['o', 'a', 'os', 'as', 'um', 'uma', 'de', 'do', 'da', 'em', 'no', 'na',
      'por', 'para', 'com', 'que', 'se', 'não', 'mas', 'como', 'mais', 'e', 'é'];

    const words = text.toLowerCase()
      .replace(/[^\w\s]/g, '')
      .split(' ')
      .filter(w => w.length > 3 && !stopWords.includes(w));

    return words.slice(0, 3).join(' ') || 'dark mysterious';
  }

  /**
   * Make HTTP request and return JSON
   */
  makeRequest(url, headers = {}) {
    return new Promise((resolve, reject) => {
      const protocol = url.startsWith('https') ? https : require('http');
      const options = {
        headers: {
          'User-Agent': 'ViralVideoApp/1.0',
          ...headers,
        },
      };

      const timeoutHandle = setTimeout(() => reject(new Error('Request timeout')), this.timeout);

      protocol.get(url, options, (response) => {
        let data = '';
        response.on('data', chunk => { data += chunk; });
        response.on('end', () => {
          clearTimeout(timeoutHandle);
          try {
            resolve(JSON.parse(data));
          } catch {
            reject(new Error('Invalid JSON response'));
          }
        });
      }).on('error', (err) => {
        clearTimeout(timeoutHandle);
        reject(err);
      });
    });
  }

  /**
   * Generate placeholder media when APIs fail
   */
  getPlaceholderMedia(keyword, count = 1) {
    const placeholders = [];
    for (let i = 0; i < count; i++) {
      placeholders.push({
        id: `placeholder_${i}`,
        url: null,
        type: 'placeholder',
        source: 'local',
        keyword,
        width: 1080,
        height: 1920,
      });
    }
    return placeholders;
  }

  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  getCategories() {
    return Object.keys(this.VISUAL_CATEGORIES);
  }
}

module.exports = MediaFetcher;
