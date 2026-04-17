/**
 * Database Module
 * SQLite-backed persistence layer using better-sqlite3 (synchronous API).
 *
 * Primary path : /app/data/db.sqlite   (Railway / Docker deployments)
 * Fallback path: ./data/db.sqlite      (local development)
 *
 * Tables
 * ──────
 *   users  – accounts + subscription plan + monthly video counter
 *   videos – generated video metadata
 *
 * Free-plan limit: 5 videos per calendar month.
 */

'use strict';

const Database = require('better-sqlite3');
const fs       = require('fs');
const path     = require('path');
const { v4: uuidv4 } = require('uuid');

// ---------------------------------------------------------------------------
// Database bootstrap
// ---------------------------------------------------------------------------

/**
 * Resolve the database file path, creating the parent directory if needed.
 * Tries /app/data first (Railway volume), then falls back to ./data.
 *
 * @returns {string} Absolute path to the .sqlite file
 */
function resolveDbPath() {
  const candidates = [
    '/app/data/db.sqlite',
    path.join(process.cwd(), 'data', 'db.sqlite'),
  ];

  for (const candidate of candidates) {
    const dir = path.dirname(candidate);
    try {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      // Verify we can actually write there (will throw if not)
      fs.accessSync(dir, fs.constants.W_OK);
      return candidate;
    } catch (_) {
      // Try next candidate
    }
  }

  // Ultimate fallback: in-process temp file
  const tmpDir = path.join(__dirname, '../temp');
  if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true });
  return path.join(tmpDir, 'db.sqlite');
}

// Open the database (created automatically if it does not exist)
const dbPath = resolveDbPath();
const db     = new Database(dbPath);

// Enable WAL mode for better concurrent read performance
db.pragma('journal_mode = WAL');
// Foreign-key enforcement
db.pragma('foreign_keys = ON');

// ---------------------------------------------------------------------------
// Schema creation
// ---------------------------------------------------------------------------

db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id                 TEXT    PRIMARY KEY,
    email              TEXT    UNIQUE NOT NULL,
    password_hash      TEXT    NOT NULL,
    plan               TEXT    NOT NULL DEFAULT 'free',
    videos_this_month  INTEGER NOT NULL DEFAULT 0,
    month_reset        INTEGER NOT NULL DEFAULT 0,
    created_at         INTEGER NOT NULL
  );

  CREATE TABLE IF NOT EXISTS videos (
    id         TEXT    PRIMARY KEY,
    user_id    TEXT    NOT NULL,
    title      TEXT,
    topic      TEXT,
    file_path  TEXT,
    file_size  INTEGER,
    status     TEXT    NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
  );

  CREATE INDEX IF NOT EXISTS idx_users_email    ON users  (email);
  CREATE INDEX IF NOT EXISTS idx_videos_user_id ON videos (user_id);
`);

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const FREE_PLAN_MONTHLY_LIMIT = 5;
const PLAN_LIMITS = {
  free : FREE_PLAN_MONTHLY_LIMIT,
  pro  : 100,
  elite: Infinity,
};

// ---------------------------------------------------------------------------
// Helper: get the first epoch-millisecond of the current UTC month
// ---------------------------------------------------------------------------
function currentMonthStart() {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).getTime();
}

// ---------------------------------------------------------------------------
// User methods
// ---------------------------------------------------------------------------

/**
 * Create a new user account.
 *
 * @param {string} email
 * @param {string} passwordHash  - Pre-hashed password (use authManager.hashPassword)
 * @returns {{ id:string, email:string, plan:string }}
 */
function createUser(email, passwordHash) {
  const id  = uuidv4();
  const now = Date.now();
  // Se for o email do dono (ADMIN_EMAIL), vira elite automaticamente
  const adminEmail = (process.env.ADMIN_EMAIL || '').toLowerCase().trim();
  const plan = (adminEmail && email.toLowerCase().trim() === adminEmail) ? 'elite' : 'free';

  db.prepare(`
    INSERT INTO users (id, email, password_hash, plan, videos_this_month, month_reset, created_at)
    VALUES (?, ?, ?, ?, 0, ?, ?)
  `).run(id, email.toLowerCase().trim(), passwordHash, plan, currentMonthStart(), now);

  return { id, email: email.toLowerCase().trim(), plan };
}

/**
 * Retrieve a user by email address (case-insensitive).
 *
 * @param {string} email
 * @returns {object|null}
 */
function getUserByEmail(email) {
  return db.prepare('SELECT * FROM users WHERE email = ?')
            .get(email.toLowerCase().trim()) || null;
}

/**
 * Retrieve a user by their UUID.
 *
 * @param {string} userId
 * @returns {object|null}
 */
function getUserById(userId) {
  return db.prepare('SELECT * FROM users WHERE id = ?').get(userId) || null;
}

/**
 * Update a user's subscription plan.
 *
 * @param {string} userId
 * @param {string} plan  - 'free' | 'pro' | 'elite'
 */
function updateUserPlan(userId, plan) {
  db.prepare('UPDATE users SET plan = ? WHERE id = ?').run(plan, userId);
}

// ---------------------------------------------------------------------------
// Video-count methods
// ---------------------------------------------------------------------------

/**
 * Reset the monthly video counter if the stored month_reset timestamp is
 * older than the current month start.  Called automatically before every
 * count check or increment.
 *
 * @param {string} userId
 */
function resetMonthlyCountIfNeeded(userId) {
  const user = getUserById(userId);
  if (!user) return;

  const monthStart = currentMonthStart();
  if (user.month_reset < monthStart) {
    db.prepare(
      'UPDATE users SET videos_this_month = 0, month_reset = ? WHERE id = ?'
    ).run(monthStart, userId);
  }
}

/**
 * Get the number of videos the user has generated in the current month.
 *
 * @param {string} userId
 * @returns {number}
 */
function getVideoCount(userId) {
  resetMonthlyCountIfNeeded(userId);
  const user = getUserById(userId);
  return user ? user.videos_this_month : 0;
}

/**
 * Increment the user's monthly video counter by 1.
 * Returns the new count.
 *
 * @param {string} userId
 * @returns {number}
 */
function incrementVideoCount(userId) {
  resetMonthlyCountIfNeeded(userId);
  db.prepare(
    'UPDATE users SET videos_this_month = videos_this_month + 1 WHERE id = ?'
  ).run(userId);
  const user = getUserById(userId);
  return user ? user.videos_this_month : 0;
}

/**
 * Check whether the user is allowed to generate another video this month.
 *
 * @param {string} userId
 * @returns {{ allowed:boolean, current:number, limit:number, plan:string }}
 */
function canGenerateVideo(userId) {
  resetMonthlyCountIfNeeded(userId);
  const user = getUserById(userId);
  if (!user) return { allowed: false, current: 0, limit: 0, plan: 'free' };

  const limit   = PLAN_LIMITS[user.plan] ?? FREE_PLAN_MONTHLY_LIMIT;
  const current = user.videos_this_month;
  return {
    allowed: current < limit,
    current,
    limit,
    plan: user.plan,
  };
}

// ---------------------------------------------------------------------------
// Video methods
// ---------------------------------------------------------------------------

/**
 * Persist metadata about a generated (or in-progress) video.
 *
 * @param {string} userId
 * @param {object} videoData
 * @param {string} [videoData.title]
 * @param {string} [videoData.topic]
 * @param {string} [videoData.filePath]
 * @param {number} [videoData.fileSize]
 * @param {string} [videoData.status='pending']
 * @returns {{ id:string }}
 */
function saveVideo(userId, videoData = {}) {
  const id  = uuidv4();
  const now = Date.now();

  db.prepare(`
    INSERT INTO videos (id, user_id, title, topic, file_path, file_size, status, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    id,
    userId,
    videoData.title    || null,
    videoData.topic    || null,
    videoData.filePath || null,
    videoData.fileSize || null,
    videoData.status   || 'pending',
    now
  );

  return { id };
}

/**
 * Update an existing video record (partial update — only provided fields change).
 *
 * @param {string} videoId
 * @param {object} updates  - Any subset of { title, topic, filePath, fileSize, status }
 */
function updateVideo(videoId, updates = {}) {
  const allowed = ['title', 'topic', 'file_path', 'file_size', 'status'];
  const columnMap = {
    title    : 'title',
    topic    : 'topic',
    filePath : 'file_path',
    fileSize : 'file_size',
    status   : 'status',
  };

  const setClauses = [];
  const values     = [];

  for (const [key, col] of Object.entries(columnMap)) {
    if (Object.prototype.hasOwnProperty.call(updates, key)) {
      setClauses.push(`${col} = ?`);
      values.push(updates[key]);
    }
  }

  if (setClauses.length === 0) return; // nothing to update

  values.push(videoId);
  db.prepare(`UPDATE videos SET ${setClauses.join(', ')} WHERE id = ?`).run(...values);
}

/**
 * Get all videos for a user, ordered newest-first.
 *
 * @param {string} userId
 * @returns {Array<object>}
 */
function getUserVideos(userId) {
  return db.prepare(
    'SELECT * FROM videos WHERE user_id = ? ORDER BY created_at DESC'
  ).all(userId);
}

/**
 * Retrieve a single video by its UUID.
 *
 * @param {string} videoId
 * @returns {object|null}
 */
function getVideoById(videoId) {
  return db.prepare('SELECT * FROM videos WHERE id = ?').get(videoId) || null;
}

/**
 * Delete a video record (does NOT delete the file from disk).
 *
 * @param {string} videoId
 */
function deleteVideo(videoId) {
  db.prepare('DELETE FROM videos WHERE id = ?').run(videoId);
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = {
  // Database instance (for advanced use / migrations)
  db,

  // User operations
  createUser,
  getUserByEmail,
  getUserById,
  updateUserPlan,

  // Video count
  incrementVideoCount,
  getVideoCount,
  resetMonthlyCountIfNeeded,
  canGenerateVideo,

  // Video CRUD
  saveVideo,
  updateVideo,
  getUserVideos,
  getVideoById,
  deleteVideo,

  // Constants
  FREE_PLAN_MONTHLY_LIMIT,
  PLAN_LIMITS,
};
