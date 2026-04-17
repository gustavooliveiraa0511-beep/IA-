/**
 * Auth Manager Module
 * JWT-based authentication + bcrypt password hashing.
 *
 * Usage:
 *   const auth = require('./authManager');
 *
 *   // Register
 *   const hash = await auth.hashPassword('mysecret');
 *   // Login
 *   const ok    = await auth.comparePassword('mysecret', hash);  // true
 *   const token = auth.generateToken(userId);
 *   // Middleware
 *   app.get('/protected', auth.authMiddleware, (req, res) => { ... });
 */

'use strict';

const jwt     = require('jsonwebtoken');
const bcrypt  = require('bcryptjs');

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const JWT_SECRET = process.env.JWT_SECRET || 'dark-viral-video-jwt-secret-change-in-prod-2024';
const JWT_EXPIRY = process.env.JWT_EXPIRY  || '7d';   // tokens last 7 days
const BCRYPT_ROUNDS = 10;                              // salt rounds (cost factor)

// ---------------------------------------------------------------------------
// Password helpers
// ---------------------------------------------------------------------------

/**
 * Hash a plain-text password using bcrypt.
 *
 * @param {string} password
 * @returns {Promise<string>}  bcrypt hash
 */
async function hashPassword(password) {
  if (!password || typeof password !== 'string') {
    throw new Error('Password must be a non-empty string');
  }
  return bcrypt.hash(password, BCRYPT_ROUNDS);
}

/**
 * Compare a plain-text password against a stored bcrypt hash.
 *
 * @param {string} password   - Plain-text input from the user
 * @param {string} hash       - Stored bcrypt hash
 * @returns {Promise<boolean>}
 */
async function comparePassword(password, hash) {
  if (!password || !hash) return false;
  return bcrypt.compare(password, hash);
}

// ---------------------------------------------------------------------------
// Token helpers
// ---------------------------------------------------------------------------

/**
 * Generate a signed JWT for the given user ID.
 *
 * @param {string} userId
 * @param {object} [extraClaims={}]  - Optional additional payload fields
 * @returns {string}  Signed JWT string
 */
function generateToken(userId, extraClaims = {}) {
  if (!userId) throw new Error('userId is required to generate a token');

  const payload = {
    sub: userId,     // standard JWT "subject" claim
    iat: Math.floor(Date.now() / 1000),
    ...extraClaims,
  };

  return jwt.sign(payload, JWT_SECRET, { expiresIn: JWT_EXPIRY });
}

/**
 * Verify and decode a JWT.
 *
 * @param {string} token
 * @returns {{ sub:string, iat:number, exp:number, [key:string]: any }}
 * @throws {Error} if the token is invalid or expired
 */
function verifyToken(token) {
  if (!token) throw new Error('No token provided');
  // Will throw JsonWebTokenError / TokenExpiredError on failure
  return jwt.verify(token, JWT_SECRET);
}

// ---------------------------------------------------------------------------
// Express middleware
// ---------------------------------------------------------------------------

/**
 * Express middleware that validates the Bearer token in the Authorization
 * header and sets `req.userId` and `req.tokenPayload` on success.
 *
 * On failure it responds immediately with 401 JSON and does NOT call next().
 *
 * @example
 *   app.get('/api/me', authMiddleware, (req, res) => res.json({ userId: req.userId }));
 *
 * @param {import('express').Request}  req
 * @param {import('express').Response} res
 * @param {import('express').NextFunction} next
 */
function authMiddleware(req, res, next) {
  const authHeader = req.headers['authorization'] || req.headers['Authorization'] || '';

  if (!authHeader.startsWith('Bearer ')) {
    return res.status(401).json({
      error: 'Unauthorized',
      message: 'Missing or malformed Authorization header. Expected: Bearer <token>',
    });
  }

  const token = authHeader.slice(7).trim(); // strip "Bearer "

  if (!token) {
    return res.status(401).json({
      error: 'Unauthorized',
      message: 'Token is empty',
    });
  }

  try {
    const payload = verifyToken(token);
    req.userId       = payload.sub;   // the user UUID
    req.tokenPayload = payload;
    next();
  } catch (err) {
    const isExpired = err.name === 'TokenExpiredError';
    return res.status(401).json({
      error  : 'Unauthorized',
      message: isExpired ? 'Token has expired' : 'Invalid token',
      detail : err.message,
    });
  }
}

/**
 * Optional: middleware factory that also checks a minimum plan level.
 * Requires authMiddleware to have run first (so req.userId is set).
 *
 * @param {string[]} allowedPlans  - e.g. ['pro', 'elite']
 * @returns {import('express').RequestHandler}
 *
 * @example
 *   app.post('/api/export', authMiddleware, requirePlan(['pro', 'elite']), handler);
 */
function requirePlan(allowedPlans = []) {
  return function planGuard(req, res, next) {
    // Lazily load database to avoid circular dependencies at module load time
    let db;
    try {
      db = require('./database');
    } catch (_) {
      return next(); // can't check plan — allow through
    }

    const user = db.getUserById(req.userId);
    if (!user) {
      return res.status(401).json({ error: 'User not found' });
    }

    if (allowedPlans.length > 0 && !allowedPlans.includes(user.plan)) {
      return res.status(403).json({
        error  : 'Forbidden',
        message: `This action requires one of the following plans: ${allowedPlans.join(', ')}`,
        current: user.plan,
      });
    }

    req.user = user; // attach full user object for downstream handlers
    next();
  };
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = {
  hashPassword,
  comparePassword,
  generateToken,
  verifyToken,
  authMiddleware,
  requirePlan,

  // Expose config so tests / other modules can read them (read-only copies)
  JWT_EXPIRY,
  BCRYPT_ROUNDS,
};
