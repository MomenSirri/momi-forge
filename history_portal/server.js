const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const express = require('express');
const helmet = require('helmet');
const cookieParser = require('cookie-parser');
const bcrypt = require('bcryptjs');
const sqlite3 = require('sqlite3').verbose();

const app = express();

const ROOT_DIR = path.resolve(__dirname, '..');
const HISTORY_PORT = Number(process.env.HISTORY_PORTAL_PORT || '8199');
const HISTORY_HOST = process.env.HISTORY_PORTAL_HOST || '0.0.0.0';
const COMPANY_DOMAIN = (process.env.COMPANY_EMAIL_DOMAIN || 'brickvisual.com').trim().toLowerCase();
const DB_PATH = path.resolve(ROOT_DIR, process.env.USER_DB_PATH || 'users.db');
const IMAGE_DIR = path.resolve(ROOT_DIR, process.env.BRICKER_IMAGE_DIR || 'bricker_image');
const THUMBNAIL_DIR = path.resolve(ROOT_DIR, process.env.TASK_THUMBNAIL_DIR || 'thumbnails');
const PREVIEW_DIR = path.resolve(ROOT_DIR, process.env.TASK_PREVIEW_DIR || 'image_load_card');
const THUMBNAIL_WARN_GB = Math.max(1, Number(process.env.TASK_THUMBNAIL_WARN_GB || '50'));
const THUMBNAIL_DISK_CAP_GB = Math.max(THUMBNAIL_WARN_GB, Number(process.env.TASK_THUMBNAIL_DISK_CAP_GB || '75'));
const THUMBNAIL_WARN_BYTES = Math.floor(THUMBNAIL_WARN_GB * 1024 * 1024 * 1024);
const THUMBNAIL_DISK_CAP_BYTES = Math.floor(THUMBNAIL_DISK_CAP_GB * 1024 * 1024 * 1024);
const THUMBNAIL_STATS_CACHE_MS = Math.max(5000, Number(process.env.THUMBNAIL_STATS_CACHE_MS || '30000'));
const DEFAULT_AVATAR_FILENAME = process.env.DEFAULT_AVATAR_FILENAME || 'default_avatar.png';
const SESSION_COOKIE = process.env.HISTORY_SESSION_COOKIE || 'momi_history_sid';
const SESSION_TTL_MS = Math.max(15 * 60 * 1000, Number(process.env.HISTORY_SESSION_TTL_MS || '43200000'));
const COOKIE_SECURE = (process.env.HISTORY_COOKIE_SECURE || '0').trim() === '1';
const HISTORY_PORTAL_SSO_SECRET = String(process.env.HISTORY_PORTAL_SSO_SECRET || 'momi-forge-local-sso-secret').trim();
const STRICT_SSO_SESSION_MATCH = parseBoolean(process.env.HISTORY_STRICT_SSO_SESSION_MATCH || '1');

const DEFAULT_FAVORITE_CATEGORIES = [
  { key: 'inspiration', label: 'Inspiration', color: '#1D9BF0', sortOrder: 10 },
  { key: 'best_results', label: 'Best Results', color: '#00B894', sortOrder: 20 },
  { key: 'client_ready', label: 'Client-ready', color: '#FDBA2D', sortOrder: 30 },
  { key: 'personal_picks', label: 'Personal Picks', color: '#A855F7', sortOrder: 40 },
  { key: 'tests_keep', label: 'Tests Worth Keeping', color: '#64748B', sortOrder: 50 },
];
const DELETE_UNDO_WINDOW_MS = Math.max(1000, Number(process.env.HISTORY_DELETE_UNDO_MS || '5000'));
const ENABLE_S3_DELETE = parseBoolean(process.env.HISTORY_DELETE_S3 || process.env.HISTORY_DELETE_S3_ENABLED || '0');
const S3_DELETE_REGION = String(process.env.HISTORY_DELETE_S3_REGION || process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || '').trim();
const S3_DELETE_BUCKET = String(process.env.HISTORY_DELETE_S3_BUCKET || '').trim();
const pendingAssetDeletes = new Map();
const THUMBNAIL_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp']);
let thumbnailStatsCache = { atMs: 0, value: null };

if (!fs.existsSync(DB_PATH)) {
  console.error(`[history_portal] Database file not found at ${DB_PATH}`);
  process.exit(1);
}

const db = new sqlite3.Database(DB_PATH, sqlite3.OPEN_READWRITE, (error) => {
  if (error) {
    console.error('[history_portal] Failed to open sqlite DB:', error.message);
    process.exit(1);
  }
});

db.serialize(() => {
  db.run('PRAGMA foreign_keys = ON');
  db.run('PRAGMA journal_mode = WAL');
});

const sessions = new Map();

function nowIso() {
  return new Date().toISOString();
}

function normalizeEmail(value) {
  return String(value || '').trim().toLowerCase();
}

function isCompanyEmail(email) {
  const normalized = normalizeEmail(email);
  return normalized.endsWith(`@${COMPANY_DOMAIN}`) && normalized.split('@').length === 2;
}

function emailPrefix(email) {
  return normalizeEmail(email).split('@')[0] || '';
}

function toTitleCase(prefix) {
  if (!prefix) {
    return 'BrickVisual User';
  }
  return prefix
    .replace(/[-_]/g, '.')
    .split('.')
    .filter(Boolean)
    .map((token) => token.charAt(0).toUpperCase() + token.slice(1))
    .join(' ');
}

function resolveAvatarForPrefix(prefix) {
  const normalized = String(prefix || '').toLowerCase();
  const candidates = [
    `${normalized}.png`,
    `${normalized}.jpg`,
    `${normalized}.jpeg`,
    `${normalized}.webp`,
  ];
  for (const name of candidates) {
    const filePath = path.join(IMAGE_DIR, name);
    if (fs.existsSync(filePath)) {
      return name;
    }
  }
  return DEFAULT_AVATAR_FILENAME;
}

function queryAll(sql, params = []) {
  return new Promise((resolve, reject) => {
    db.all(sql, params, (error, rows) => {
      if (error) {
        reject(error);
        return;
      }
      resolve(rows || []);
    });
  });
}

function queryOne(sql, params = []) {
  return new Promise((resolve, reject) => {
    db.get(sql, params, (error, row) => {
      if (error) {
        reject(error);
        return;
      }
      resolve(row || null);
    });
  });
}

function execute(sql, params = []) {
  return new Promise((resolve, reject) => {
    db.run(sql, params, function onRun(error) {
      if (error) {
        reject(error);
        return;
      }
      resolve({ changes: this.changes, lastID: this.lastID });
    });
  });
}

async function tableColumns(tableName) {
  const safeName = String(tableName || '').replace(/[^a-z0-9_]/gi, '');
  const rows = await queryAll(`PRAGMA table_info(${safeName})`);
  return new Set(rows.map((row) => String(row.name || row[1] || '').toLowerCase()));
}

async function ensureColumn(tableName, columnName, ddl) {
  const columns = await tableColumns(tableName);
  if (columns.has(String(columnName || '').toLowerCase())) {
    return;
  }
  await execute(`ALTER TABLE ${tableName} ADD COLUMN ${columnName} ${ddl}`);
}

async function ensurePortalSchema() {
  await execute(
    `
    CREATE TABLE IF NOT EXISTS history_collections (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_email TEXT NOT NULL,
      name TEXT NOT NULL,
      collection_key TEXT NOT NULL,
      kind TEXT NOT NULL DEFAULT 'manual',
      is_system INTEGER NOT NULL DEFAULT 0,
      sort_order INTEGER NOT NULL DEFAULT 100,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(user_email, collection_key)
    )
    `,
  );

  await execute(
    `
    CREATE TABLE IF NOT EXISTS history_collection_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      collection_id INTEGER NOT NULL,
      task_id TEXT NOT NULL,
      user_email TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY(collection_id) REFERENCES history_collections(id) ON DELETE CASCADE,
      FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
      UNIQUE(user_email, task_id)
    )
    `,
  );

  await execute('CREATE INDEX IF NOT EXISTS idx_history_collections_user ON history_collections(user_email)');
  await execute('CREATE INDEX IF NOT EXISTS idx_history_collection_items_user ON history_collection_items(user_email)');
  await execute('CREATE INDEX IF NOT EXISTS idx_history_collection_items_collection ON history_collection_items(collection_id)');
  await ensureColumn('tasks', 'preview_url', 'TEXT');
  await ensureColumn('task_outputs', 'preview_url', 'TEXT');
}

function slugifyKey(value) {
  const slug = String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return slug.slice(0, 64) || 'custom';
}

function cleanupSessions() {
  const now = Date.now();
  for (const [token, session] of sessions.entries()) {
    if (session.expiresAt <= now) {
      sessions.delete(token);
    }
  }
}
setInterval(cleanupSessions, 5 * 60 * 1000).unref();

function createSession(email) {
  const token = crypto.randomBytes(24).toString('hex');
  const expiresAt = Date.now() + SESSION_TTL_MS;
  sessions.set(token, { email, expiresAt });
  return { token, expiresAt };
}

function secureHexEqual(left, right) {
  if (!left || !right) {
    return false;
  }
  const leftBuf = Buffer.from(String(left), 'utf8');
  const rightBuf = Buffer.from(String(right), 'utf8');
  if (leftBuf.length !== rightBuf.length) {
    return false;
  }
  return crypto.timingSafeEqual(leftBuf, rightBuf);
}

function verifySsoSignature(email, exp, nonce, sig) {
  if (!HISTORY_PORTAL_SSO_SECRET) {
    return { ok: false, reason: 'SSO_DISABLED' };
  }
  if (!isCompanyEmail(email)) {
    return { ok: false, reason: 'INVALID_EMAIL' };
  }

  const expInt = Number(exp);
  if (!Number.isFinite(expInt) || expInt <= 0) {
    return { ok: false, reason: 'INVALID_EXP' };
  }
  const now = Math.floor(Date.now() / 1000);
  if (expInt < now - 30 || expInt > now + 3600) {
    return { ok: false, reason: 'EXPIRED' };
  }

  const payload = `${normalizeEmail(email)}\n${expInt}\n${String(nonce || '')}`;
  const expected = crypto.createHmac('sha256', HISTORY_PORTAL_SSO_SECRET).update(payload, 'utf8').digest('hex');
  if (!secureHexEqual(expected, String(sig || ''))) {
    return { ok: false, reason: 'INVALID_SIG' };
  }
  return { ok: true, exp: expInt };
}

function extractSsoContext(req) {
  const email = normalizeEmail(
    (req.headers && req.headers['x-momi-sso-email']) ||
    req.query?.email ||
    '',
  );
  const exp = String(
    (req.headers && req.headers['x-momi-sso-exp']) ||
    req.query?.exp ||
    '',
  ).trim();
  const nonce = String(
    (req.headers && req.headers['x-momi-sso-nonce']) ||
    req.query?.nonce ||
    '',
  ).trim();
  const sig = String(
    (req.headers && req.headers['x-momi-sso-sig']) ||
    req.query?.sig ||
    '',
  ).trim();

  if (!email || !exp || !nonce || !sig) {
    return null;
  }

  return { email, exp, nonce, sig };
}

async function tryAuthenticateViaSso(req, res) {
  const sso = extractSsoContext(req);
  if (!sso) {
    return null;
  }

  const verification = verifySsoSignature(sso.email, sso.exp, sso.nonce, sso.sig);
  if (!verification.ok) {
    return null;
  }

  const identity = await getIdentityForEmail(sso.email);
  if (!identity || !identity.isActive) {
    return null;
  }

  const { token } = createSession(identity.email);
  res.cookie(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: COOKIE_SECURE,
    sameSite: 'lax',
    path: '/',
    maxAge: SESSION_TTL_MS,
  });

  req.identity = identity;
  req.sessionToken = token;
  return identity;
}

function destroySession(token) {
  if (!token) {
    return;
  }
  sessions.delete(token);
}

function parseBoolean(value) {
  if (typeof value === 'boolean') {
    return value;
  }
  const text = String(value || '').trim().toLowerCase();
  return ['1', 'true', 'yes', 'on'].includes(text);
}

function normalizeSort(sort) {
  const mapping = {
    newest: 'COALESCE(t.submitted_at, t.created_at) DESC',
    oldest: 'COALESCE(t.submitted_at, t.created_at) ASC',
    duration_desc: 'COALESCE(t.total_duration_ms, 0) DESC, COALESCE(t.submitted_at, t.created_at) DESC',
    duration_asc: 'COALESCE(t.total_duration_ms, 0) ASC, COALESCE(t.submitted_at, t.created_at) DESC',
  };
  return mapping[String(sort || 'newest')] || mapping.newest;
}

function normalizeDateBound(rawValue, endOfDay) {
  const value = String(rawValue || '').trim();
  if (!value) {
    return null;
  }

  let date = null;
  const simpleDateMatch = /^\d{4}-\d{2}-\d{2}$/.test(value);

  if (simpleDateMatch) {
    const suffix = endOfDay ? 'T23:59:59.000Z' : 'T00:00:00.000Z';
    date = new Date(`${value}${suffix}`);
  } else {
    date = new Date(value);
  }

  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return date.toISOString();
}

function normalizeStatus(status) {
  const value = String(status || '').trim().toLowerCase();
  if (!value || value === '__all__') {
    return null;
  }
  return value;
}

function normalizeFilterValue(value) {
  const text = String(value || '').trim();
  if (!text || text === '__all__') {
    return null;
  }
  if (text === '__none__') {
    return '';
  }
  return text;
}

function normalizeScope(value) {
  const text = String(value || 'all').trim().toLowerCase();
  if (['all', 'favorites', 'uncategorized'].includes(text)) {
    return text;
  }
  return 'all';
}

function normalizeCollectionId(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return null;
  }
  return Math.floor(numeric);
}

function normalizeAssetUrl(rawValue) {
  const value = String(rawValue || '').trim();
  if (!value) {
    return '';
  }

  if (value.startsWith('http://') || value.startsWith('https://') || value.startsWith('data:')) {
    return value;
  }

  const absCandidate = path.isAbsolute(value) ? value : path.resolve(ROOT_DIR, value);
  const allowedRoots = [ROOT_DIR, IMAGE_DIR, THUMBNAIL_DIR, PREVIEW_DIR];
  if (allowedRoots.some((rootPath) => isPathInsideRoot(absCandidate, rootPath))) {
    return `/api/asset?path=${encodeURIComponent(absCandidate)}`;
  }

  const normalized = value.replace(/\\/g, '/');
  if (normalized.startsWith('/')) {
    const rootCandidate = path.resolve(ROOT_DIR, `.${normalized}`);
    if (allowedRoots.some((rootPath) => isPathInsideRoot(rootCandidate, rootPath))) {
      return `/api/asset?path=${encodeURIComponent(rootCandidate)}`;
    }
  }

  return value;
}

function normalizePathForCompare(value) {
  return String(value || '')
    .replace(/\\/g, '/')
    .toLowerCase();
}

function isPathInsideRoot(candidatePath, rootPath) {
  const candidate = normalizePathForCompare(path.resolve(candidatePath));
  const root = normalizePathForCompare(path.resolve(rootPath));
  if (!candidate || !root) {
    return false;
  }
  return candidate === root || candidate.startsWith(`${root}/`);
}

function resolveLocalFileForDeletion(rawValue) {
  const value = String(rawValue || '').trim();
  if (!value) {
    return null;
  }

  let candidate = null;
  if (value.startsWith('/api/asset?')) {
    const query = value.split('?')[1] || '';
    const params = new URLSearchParams(query);
    const filePath = params.get('path');
    if (filePath) {
      candidate = path.resolve(filePath);
    }
  } else if (value.startsWith('file://')) {
    candidate = path.resolve(new URL(value).pathname);
  } else if (value.startsWith('http://') || value.startsWith('https://') || value.startsWith('data:')) {
    return null;
  } else if (path.isAbsolute(value)) {
    candidate = path.resolve(value);
  } else if (value.startsWith('/')) {
    candidate = path.resolve(ROOT_DIR, `.${value.replace(/\\/g, '/')}`);
  } else {
    candidate = path.resolve(ROOT_DIR, value);
  }

  if (!candidate) {
    return null;
  }

  const allowedRoots = [ROOT_DIR, IMAGE_DIR, THUMBNAIL_DIR, PREVIEW_DIR];
  const inAllowedRoot = allowedRoots.some((rootPath) => isPathInsideRoot(candidate, rootPath));
  if (!inAllowedRoot) {
    return null;
  }

  return candidate;
}

function parseS3ReferenceFromUrl(rawValue) {
  const value = String(rawValue || '').trim();
  if (!value || (!value.startsWith('http://') && !value.startsWith('https://'))) {
    return null;
  }

  let parsed;
  try {
    parsed = new URL(value);
  } catch (_error) {
    return null;
  }

  const host = String(parsed.hostname || '').toLowerCase();
  if (!host.includes('amazonaws.com')) {
    return null;
  }

  const hostParts = host.split('.');
  const pathParts = parsed.pathname.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
  let bucket = '';
  let key = '';
  let region = '';

  const s3HostIndex = hostParts.findIndex((part) => part === 's3');
  if (s3HostIndex > 0) {
    // Virtual-hosted style bucket.s3.<region>.amazonaws.com
    bucket = hostParts.slice(0, s3HostIndex).join('.');
    if (hostParts[s3HostIndex + 1] && hostParts[s3HostIndex + 1] !== 'amazonaws') {
      region = hostParts[s3HostIndex + 1];
    }
    key = pathParts.join('/');
  } else if (host.startsWith('s3.')) {
    // Path-style s3.<region>.amazonaws.com/<bucket>/<key>
    region = hostParts[1] && hostParts[1] !== 'amazonaws' ? hostParts[1] : '';
    bucket = pathParts.shift() || '';
    key = pathParts.join('/');
  } else if (host === 's3.amazonaws.com') {
    bucket = pathParts.shift() || '';
    key = pathParts.join('/');
  }

  if (!bucket || !key) {
    return null;
  }

  return { bucket, key, region };
}

let s3ModuleCache = null;
let s3ClientCache = null;
let s3ClientCacheRegion = '';

function getS3SdkModule() {
  if (s3ModuleCache !== null) {
    return s3ModuleCache;
  }
  try {
    // Optional dependency: deletion continues even if this package is unavailable.
    // eslint-disable-next-line global-require, import/no-extraneous-dependencies
    s3ModuleCache = require('@aws-sdk/client-s3');
  } catch (_error) {
    s3ModuleCache = undefined;
  }
  return s3ModuleCache;
}

function getS3DeleteClient(region) {
  const sdk = getS3SdkModule();
  if (!sdk || !region) {
    return null;
  }
  if (s3ClientCache && s3ClientCacheRegion === region) {
    return s3ClientCache;
  }
  const { S3Client } = sdk;
  s3ClientCache = new S3Client({ region });
  s3ClientCacheRegion = region;
  return s3ClientCache;
}

async function deleteS3ObjectBestEffort(location) {
  if (!location) {
    return { deleted: false, skipped: true, reason: 'NOT_S3_REFERENCE' };
  }
  if (!ENABLE_S3_DELETE) {
    return { deleted: false, skipped: true, reason: 'S3_DELETE_DISABLED' };
  }

  const bucket = S3_DELETE_BUCKET || location.bucket;
  const key = location.key;
  const region = S3_DELETE_REGION || location.region;
  if (!bucket || !key || !region) {
    return { deleted: false, skipped: true, reason: 'S3_DELETE_CONFIG_MISSING' };
  }

  const sdk = getS3SdkModule();
  if (!sdk) {
    return { deleted: false, skipped: true, reason: 'S3_SDK_NOT_AVAILABLE' };
  }

  const client = getS3DeleteClient(region);
  if (!client) {
    return { deleted: false, skipped: true, reason: 'S3_CLIENT_UNAVAILABLE' };
  }

  try {
    const { DeleteObjectCommand } = sdk;
    await client.send(new DeleteObjectCommand({ Bucket: bucket, Key: key }));
    return {
      deleted: true,
      bucket,
      key,
      region,
    };
  } catch (error) {
    return {
      deleted: false,
      bucket,
      key,
      region,
      reason: `S3_DELETE_FAILED: ${error.message}`,
    };
  }
}

async function cleanupTaskStorageArtifacts(taskRow) {
  const references = new Set(
    [taskRow?.result_url, taskRow?.thumbnail_url, taskRow?.preview_url]
      .map((item) => String(item || '').trim())
      .filter(Boolean),
  );

  const local = [];
  const s3 = [];
  const warnings = [];
  const processedLocalPaths = new Set();

  for (const reference of references) {
    const localPath = resolveLocalFileForDeletion(reference);
    if (localPath) {
      if (processedLocalPaths.has(localPath)) {
        continue;
      }
      processedLocalPaths.add(localPath);
      try {
        if (fs.existsSync(localPath)) {
          await fs.promises.unlink(localPath);
          local.push({ path: localPath, deleted: true });
        } else {
          local.push({ path: localPath, deleted: false, reason: 'NOT_FOUND' });
        }
      } catch (error) {
        local.push({ path: localPath, deleted: false, reason: error.message });
      }
      continue;
    }

    const s3Location = parseS3ReferenceFromUrl(reference);
    if (s3Location) {
      const result = await deleteS3ObjectBestEffort(s3Location);
      s3.push({
        bucket: result.bucket || s3Location.bucket,
        key: result.key || s3Location.key,
        region: result.region || s3Location.region || S3_DELETE_REGION || null,
        deleted: Boolean(result.deleted),
        skipped: Boolean(result.skipped),
        reason: result.reason || null,
      });
      if (!result.deleted && !result.skipped && result.reason) {
        warnings.push(result.reason);
      }
      continue;
    }

    warnings.push(`UNSUPPORTED_STORAGE_REFERENCE: ${reference.slice(0, 160)}`);
  }

  return {
    local,
    s3,
    warnings,
  };
}

function getPendingDeleteKey(userEmail, taskId) {
  return `${normalizeEmail(userEmail)}::${String(taskId || '').trim()}`;
}

async function getTaskForOwner(userEmail, taskId) {
  return queryOne(
    `
    SELECT
      task_id,
      user_email,
      result_url,
      thumbnail_url,
      preview_url,
      output_filename,
      COALESCE(is_deleted, 0) AS is_deleted
    FROM tasks
    WHERE task_id = ? AND LOWER(user_email) = LOWER(?)
    LIMIT 1
    `,
    [taskId, userEmail],
  );
}

async function hardDeleteTaskForOwner(userEmail, taskId) {
  const taskRow = await getTaskForOwner(userEmail, taskId);
  if (!taskRow) {
    return {
      deleted: false,
      notFound: true,
      task_id: taskId,
      storage: { local: [], s3: [], warnings: [] },
    };
  }

  const deleteResult = await execute(
    `
    DELETE FROM tasks
    WHERE task_id = ? AND LOWER(user_email) = LOWER(?)
    `,
    [taskId, userEmail],
  );

  const storage = await cleanupTaskStorageArtifacts(taskRow);
  return {
    deleted: deleteResult.changes > 0,
    notFound: false,
    task_id: taskId,
    storage,
  };
}

async function finalizePendingAssetDelete(userEmail, taskId) {
  const key = getPendingDeleteKey(userEmail, taskId);
  const pending = pendingAssetDeletes.get(key);
  if (pending && pending.timer) {
    clearTimeout(pending.timer);
  }
  pendingAssetDeletes.delete(key);
  return hardDeleteTaskForOwner(userEmail, taskId);
}

function schedulePendingAssetDelete(userEmail, taskId) {
  const key = getPendingDeleteKey(userEmail, taskId);
  const existing = pendingAssetDeletes.get(key);
  if (existing && existing.timer) {
    clearTimeout(existing.timer);
  }

  const expiresAtMs = Date.now() + DELETE_UNDO_WINDOW_MS;
  const entry = {
    key,
    userEmail: normalizeEmail(userEmail),
    taskId: String(taskId || '').trim(),
    expiresAtMs,
    timer: null,
  };

  entry.timer = setTimeout(async () => {
    try {
      await finalizePendingAssetDelete(entry.userEmail, entry.taskId);
    } catch (error) {
      console.error('[history_portal] finalize pending delete failed:', error);
    }
  }, DELETE_UNDO_WINDOW_MS);
  entry.timer.unref?.();

  pendingAssetDeletes.set(key, entry);
  return entry;
}

function cancelPendingAssetDelete(userEmail, taskId) {
  const key = getPendingDeleteKey(userEmail, taskId);
  const entry = pendingAssetDeletes.get(key);
  if (!entry) {
    return null;
  }
  if (entry.timer) {
    clearTimeout(entry.timer);
  }
  pendingAssetDeletes.delete(key);
  return entry;
}

function formatDuration(ms) {
  const value = Number(ms || 0);
  if (!Number.isFinite(value) || value <= 0) {
    return '-';
  }
  const totalSeconds = Math.floor(value / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

function buildDateRangeFromPreset(preset) {
  const normalized = String(preset || '').trim().toLowerCase();
  if (!normalized || normalized === 'all_time' || normalized === '__all__') {
    return { dateFrom: null, dateTo: null };
  }

  const now = new Date();
  const utcNow = new Date(now.toISOString());

  if (normalized === 'today') {
    const from = new Date(Date.UTC(utcNow.getUTCFullYear(), utcNow.getUTCMonth(), utcNow.getUTCDate(), 0, 0, 0));
    const to = new Date(Date.UTC(utcNow.getUTCFullYear(), utcNow.getUTCMonth(), utcNow.getUTCDate(), 23, 59, 59));
    return { dateFrom: from.toISOString(), dateTo: to.toISOString() };
  }

  const days = normalized === 'last_7_days' ? 7 : normalized === 'last_30_days' ? 30 : null;
  if (!days) {
    return { dateFrom: null, dateTo: null };
  }

  const from = new Date(utcNow.getTime() - (days - 1) * 24 * 60 * 60 * 1000);
  from.setUTCHours(0, 0, 0, 0);
  const to = new Date(utcNow);
  to.setUTCHours(23, 59, 59, 999);
  return { dateFrom: from.toISOString(), dateTo: to.toISOString() };
}

async function computeThumbnailStorageStats() {
  const summary = {
    total_bytes: 0,
    file_count: 0,
    thumbnail_bytes: 0,
    thumbnail_file_count: 0,
    preview_bytes: 0,
    preview_file_count: 0,
    warning_bytes: THUMBNAIL_WARN_BYTES,
    cap_bytes: THUMBNAIL_DISK_CAP_BYTES,
    over_warning: false,
    over_cap: false,
    usage_percent: 0,
  };

  const scannedRoots = new Set();
  const scanDir = async (rootDir, type) => {
    const normalizedRoot = path.resolve(rootDir);
    if (scannedRoots.has(normalizedRoot) || !fs.existsSync(normalizedRoot)) {
      return;
    }
    scannedRoots.add(normalizedRoot);

    const stack = [normalizedRoot];
    while (stack.length) {
      const currentDir = stack.pop();
      let entries = [];
      try {
        entries = await fs.promises.readdir(currentDir, { withFileTypes: true });
      } catch (_error) {
        continue;
      }

      for (const entry of entries) {
        const fullPath = path.join(currentDir, entry.name);
        if (entry.isDirectory()) {
          stack.push(fullPath);
          continue;
        }
        if (!entry.isFile()) {
          continue;
        }
        const extension = path.extname(entry.name).toLowerCase();
        if (!THUMBNAIL_EXTENSIONS.has(extension)) {
          continue;
        }
        try {
          const stat = await fs.promises.stat(fullPath);
          const size = Number(stat.size || 0);
          summary.file_count += 1;
          summary.total_bytes += size;
          if (type === 'preview') {
            summary.preview_file_count += 1;
            summary.preview_bytes += size;
          } else {
            summary.thumbnail_file_count += 1;
            summary.thumbnail_bytes += size;
          }
        } catch (_error) {
          // ignore missing/race files
        }
      }
    }
  };

  await scanDir(THUMBNAIL_DIR, 'thumbnail');
  await scanDir(PREVIEW_DIR, 'preview');

  summary.over_warning = summary.total_bytes >= THUMBNAIL_WARN_BYTES;
  summary.over_cap = summary.total_bytes >= THUMBNAIL_DISK_CAP_BYTES;
  if (THUMBNAIL_DISK_CAP_BYTES > 0) {
    summary.usage_percent = Math.min(100, (summary.total_bytes / THUMBNAIL_DISK_CAP_BYTES) * 100);
  }
  return summary;
}

async function getThumbnailStorageStats({ force = false } = {}) {
  const now = Date.now();
  if (
    !force &&
    thumbnailStatsCache.value &&
    now - Number(thumbnailStatsCache.atMs || 0) < THUMBNAIL_STATS_CACHE_MS
  ) {
    return thumbnailStatsCache.value;
  }

  const stats = await computeThumbnailStorageStats();
  thumbnailStatsCache = { atMs: now, value: stats };
  return stats;
}

async function ensureDefaultFavoriteCategories(userEmail) {
  const now = nowIso();
  for (const item of DEFAULT_FAVORITE_CATEGORIES) {
    await execute(
      `
      INSERT INTO favorite_categories (
        user_email, category_key, display_name, color, sort_order, is_active, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
      ON CONFLICT(user_email, category_key) DO UPDATE SET
        display_name = excluded.display_name,
        color = COALESCE(favorite_categories.color, excluded.color),
        sort_order = excluded.sort_order,
        is_active = 1,
        updated_at = excluded.updated_at
      `,
      [userEmail, item.key, item.label, item.color, item.sortOrder, now, now],
    );
  }
}

async function listFavoriteCategories(userEmail) {
  await ensureDefaultFavoriteCategories(userEmail);
  const rows = await queryAll(
    `
    SELECT category_key, display_name, color, sort_order
    FROM favorite_categories
    WHERE LOWER(user_email) = LOWER(?) AND is_active = 1
    ORDER BY sort_order ASC, display_name ASC
    `,
    [userEmail],
  );
  return rows;
}

async function getCollectionById(userEmail, collectionId) {
  if (!collectionId) {
    return null;
  }
  return queryOne(
    `
    SELECT
      id,
      user_email,
      name,
      collection_key,
      kind,
      is_system,
      sort_order,
      created_at,
      updated_at
    FROM history_collections
    WHERE id = ? AND LOWER(user_email) = LOWER(?)
    LIMIT 1
    `,
    [collectionId, userEmail],
  );
}

async function listCollectionsWithPreview(userEmail) {
  const collections = await queryAll(
    `
    SELECT
      c.id,
      c.name,
      c.collection_key,
      c.kind,
      c.is_system,
      c.sort_order,
      c.created_at,
      c.updated_at,
      COUNT(tc.task_id) AS item_count
    FROM history_collections c
    LEFT JOIN history_collection_items ci
      ON ci.collection_id = c.id AND LOWER(ci.user_email) = LOWER(?)
    LEFT JOIN tasks tc
      ON tc.task_id = ci.task_id AND LOWER(tc.user_email) = LOWER(?) AND COALESCE(tc.is_deleted, 0) = 0
    WHERE LOWER(c.user_email) = LOWER(?)
    GROUP BY c.id
    ORDER BY c.sort_order ASC, c.updated_at DESC, c.name ASC
    `,
    [userEmail, userEmail, userEmail],
  );

  if (!collections.length) {
    return [];
  }

  const placeholders = collections.map(() => '?').join(', ');
  const previewRows = await queryAll(
    `
    SELECT
      ci.collection_id,
      t.preview_url,
      t.thumbnail_url,
      COALESCE(t.submitted_at, t.created_at) AS created_at
    FROM history_collection_items ci
    JOIN tasks t ON t.task_id = ci.task_id
    WHERE LOWER(ci.user_email) = LOWER(?) AND COALESCE(t.is_deleted, 0) = 0 AND ci.collection_id IN (${placeholders})
    ORDER BY ci.collection_id ASC, COALESCE(t.submitted_at, t.created_at) DESC
    `,
    [userEmail, ...collections.map((item) => item.id)],
  );

  const previewMap = new Map();
  for (const row of previewRows) {
    const key = Number(row.collection_id);
    if (!previewMap.has(key)) {
      previewMap.set(key, []);
    }
    const list = previewMap.get(key);
    if (list.length < 4) {
      const previewUrl = normalizeAssetUrl(row.preview_url || row.thumbnail_url || '');
      if (previewUrl) {
        list.push(previewUrl);
      }
    }
  }

  return collections.map((item) => ({
    id: Number(item.id),
    name: item.name,
    collection_key: item.collection_key,
    kind: item.kind,
    is_system: Number(item.is_system || 0) === 1,
    item_count: Number(item.item_count || 0),
    preview_urls: previewMap.get(Number(item.id)) || [],
    created_at: item.created_at,
    updated_at: item.updated_at,
  }));
}

async function getIdentityForEmail(email) {
  const row = await queryOne(
    `
    SELECT
      email,
      pwd_hash,
      COALESCE(is_active, 1) AS is_active,
      role,
      username_prefix,
      display_name,
      avatar_filename
    FROM users
    WHERE LOWER(email) = LOWER(?)
    LIMIT 1
    `,
    [email],
  );

  if (!row) {
    return null;
  }

  const prefix = row.username_prefix || emailPrefix(email);
  const displayName = row.display_name || toTitleCase(prefix);
  let avatarFilename = row.avatar_filename;
  if (!avatarFilename || !fs.existsSync(path.join(IMAGE_DIR, avatarFilename))) {
    avatarFilename = resolveAvatarForPrefix(prefix);
  }

  return {
    email: normalizeEmail(row.email),
    prefix,
    displayName,
    role: String(row.role || 'user').toLowerCase() === 'admin' ? 'admin' : 'user',
    avatarFilename,
    avatarUrl: `/avatars/${encodeURIComponent(avatarFilename)}`,
    isActive: Number(row.is_active || 0) === 1,
    passwordHash: Buffer.isBuffer(row.pwd_hash) ? row.pwd_hash.toString('utf8') : String(row.pwd_hash || ''),
  };
}

async function requireAuth(req, res, next) {
  const token = req.cookies[SESSION_COOKIE];
  const session = token ? sessions.get(token) : null;
  const now = Date.now();
  const sso = extractSsoContext(req);

  // Embedded Gradio requests always include SSO context. Treat that identity
  // as authoritative to prevent a stale cookie from pinning the portal to a
  // previous user in the same browser.
  if (sso) {
    const verification = verifySsoSignature(sso.email, sso.exp, sso.nonce, sso.sig);
    if (!verification.ok) {
      if (
        session &&
        session.expiresAt > now &&
        normalizeEmail(session.email) === normalizeEmail(sso.email)
      ) {
        const sameUserIdentity = await getIdentityForEmail(session.email);
        if (sameUserIdentity && sameUserIdentity.isActive) {
          session.expiresAt = now + SESSION_TTL_MS;
          req.identity = sameUserIdentity;
          req.sessionToken = token;
          next();
          return;
        }
      }
      if (token) {
        sessions.delete(token);
      }
      res.status(401).json({ error: 'AUTH_REQUIRED' });
      return;
    }

    const ssoIdentity = await getIdentityForEmail(sso.email);
    if (!ssoIdentity || !ssoIdentity.isActive) {
      if (token) {
        sessions.delete(token);
      }
      res.status(401).json({ error: 'AUTH_REQUIRED' });
      return;
    }

    if (session && session.expiresAt > now) {
      const sessionEmail = normalizeEmail(session.email);
      if (sessionEmail === ssoIdentity.email) {
        session.expiresAt = now + SESSION_TTL_MS;
        req.identity = ssoIdentity;
        req.sessionToken = token;
        next();
        return;
      }

      if (STRICT_SSO_SESSION_MATCH) {
        console.warn(
          `[history_portal][auth] rejecting session/SSO mismatch: cookie=${sessionEmail} sso=${ssoIdentity.email}`,
        );
        if (token) {
          sessions.delete(token);
        }
        res.clearCookie(SESSION_COOKIE, { path: '/' });
        res.status(401).json({ error: 'SESSION_SSO_MISMATCH' });
        return;
      }

      console.warn(
        `[history_portal][auth] session user mismatch; rotating session from ${sessionEmail} to ${ssoIdentity.email}`,
      );
    }

    if (token) {
      sessions.delete(token);
    }
    const { token: newToken } = createSession(ssoIdentity.email);
    res.cookie(SESSION_COOKIE, newToken, {
      httpOnly: true,
      secure: COOKIE_SECURE,
      sameSite: 'lax',
      path: '/',
      maxAge: SESSION_TTL_MS,
    });
    req.identity = ssoIdentity;
    req.sessionToken = newToken;
    next();
    return;
  }

  if (session && session.expiresAt > now) {
    const identity = await getIdentityForEmail(session.email);
    if (identity && identity.isActive) {
      session.expiresAt = now + SESSION_TTL_MS;
      req.identity = identity;
      req.sessionToken = token;
      next();
      return;
    }
  }

  if (token) {
    sessions.delete(token);
  }
  res.status(401).json({ error: 'AUTH_REQUIRED' });
}

app.use(
  helmet({
    contentSecurityPolicy: false,
    // History portal is embedded inside Gradio (different port), so SAMEORIGIN blocks it.
    frameguard: false,
    crossOriginResourcePolicy: false,
  }),
);
app.use(express.json({ limit: '1mb' }));
app.use(cookieParser());
const staticNoCacheHeaders = (res) => {
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Expires', '0');
};

app.use('/avatars', express.static(IMAGE_DIR, {
  index: false,
  fallthrough: true,
  setHeaders: staticNoCacheHeaders,
}));

app.use(express.static(path.join(__dirname, 'public'), {
  index: false,
  etag: false,
  lastModified: false,
  setHeaders: staticNoCacheHeaders,
}));

app.get('/api/health', (_req, res) => {
  res.json({ ok: true, service: 'history_portal', dbPath: DB_PATH });
});

app.get('/api/media/proxy', requireAuth, async (req, res) => {
  try {
    const rawUrl = String(req.query?.url || '').trim();
    if (!rawUrl) {
      res.status(400).json({ error: 'URL_REQUIRED' });
      return;
    }

    let target = null;
    try {
      target = new URL(rawUrl);
    } catch (_error) {
      res.status(400).json({ error: 'INVALID_URL' });
      return;
    }

    if (!['http:', 'https:'].includes(target.protocol)) {
      res.status(400).json({ error: 'UNSUPPORTED_PROTOCOL' });
      return;
    }

    const blockedHosts = new Set(['localhost', '127.0.0.1', '::1']);
    const host = String(target.hostname || '').toLowerCase();
    if (blockedHosts.has(host)) {
      res.status(403).json({ error: 'HOST_BLOCKED' });
      return;
    }

    const upstream = await fetch(target.toString(), {
      method: 'GET',
      redirect: 'follow',
      headers: {
        'User-Agent': 'MomiForgeHistoryPortal/1.0',
      },
    });

    if (!upstream.ok) {
      res.status(502).json({ error: 'UPSTREAM_FETCH_FAILED', status: upstream.status });
      return;
    }

    const contentType = String(upstream.headers.get('content-type') || '').toLowerCase();
    if (!contentType.startsWith('image/')) {
      res.status(415).json({ error: 'UNSUPPORTED_MEDIA_TYPE', contentType });
      return;
    }

    const cacheControl = upstream.headers.get('cache-control');
    if (cacheControl) {
      res.setHeader('Cache-Control', cacheControl);
    } else {
      res.setHeader('Cache-Control', 'private, max-age=300');
    }
    res.setHeader('Content-Type', contentType);

    const etag = upstream.headers.get('etag');
    if (etag) {
      res.setHeader('ETag', etag);
    }
    const lastModified = upstream.headers.get('last-modified');
    if (lastModified) {
      res.setHeader('Last-Modified', lastModified);
    }

    const bytes = Buffer.from(await upstream.arrayBuffer());
    res.status(200).send(bytes);
  } catch (error) {
    console.error('[history_portal] GET /api/media/proxy failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.post('/api/auth/login', async (req, res) => {
  try {
    const email = normalizeEmail(req.body?.email);
    const password = String(req.body?.password || '');

    if (!isCompanyEmail(email)) {
      res.status(403).json({ error: 'INVALID_DOMAIN', message: `Only @${COMPANY_DOMAIN} is allowed.` });
      return;
    }

    if (!password) {
      res.status(400).json({ error: 'PASSWORD_REQUIRED' });
      return;
    }

    const identity = await getIdentityForEmail(email);
    if (!identity || !identity.isActive) {
      res.status(401).json({ error: 'INVALID_CREDENTIALS' });
      return;
    }

    const passwordOk = await bcrypt.compare(password, identity.passwordHash);
    if (!passwordOk) {
      res.status(401).json({ error: 'INVALID_CREDENTIALS' });
      return;
    }

    const { token, expiresAt } = createSession(identity.email);
    res.cookie(SESSION_COOKIE, token, {
      httpOnly: true,
      secure: COOKIE_SECURE,
      sameSite: 'lax',
      path: '/',
      maxAge: SESSION_TTL_MS,
    });

    res.json({
      ok: true,
      user: {
        email: identity.email,
        displayName: identity.displayName,
        role: identity.role,
        avatarUrl: identity.avatarUrl,
      },
      expiresAt,
    });
  } catch (error) {
    console.error('[history_portal] /api/auth/login failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.post('/api/auth/logout', async (req, res) => {
  destroySession(req.cookies[SESSION_COOKIE]);
  res.clearCookie(SESSION_COOKIE, { path: '/' });
  res.json({ ok: true });
});

app.get('/api/auth/sso', async (req, res) => {
  try {
    const email = normalizeEmail(req.query?.email);
    const exp = req.query?.exp;
    const nonce = req.query?.nonce;
    const sig = req.query?.sig;
    const redirectRaw = String(req.query?.redirect || '/');
    const redirectTo = redirectRaw.startsWith('/') ? redirectRaw : '/';

    const verification = verifySsoSignature(email, exp, nonce, sig);
    if (!verification.ok) {
      const statusCode = verification.reason === 'SSO_DISABLED' ? 503 : 401;
      res.status(statusCode).send(`SSO login failed (${verification.reason}).`);
      return;
    }

    const identity = await getIdentityForEmail(email);
    if (!identity || !identity.isActive) {
      res.status(401).send('SSO login failed (UNKNOWN_OR_INACTIVE_USER).');
      return;
    }

    const { token } = createSession(identity.email);
    res.cookie(SESSION_COOKIE, token, {
      httpOnly: true,
      secure: COOKIE_SECURE,
      sameSite: 'lax',
      path: '/',
      maxAge: SESSION_TTL_MS,
    });

    res.redirect(302, redirectTo);
  } catch (error) {
    console.error('[history_portal] /api/auth/sso failed:', error);
    res.status(500).send('SSO login failed (INTERNAL_ERROR).');
  }
});

app.get('/api/auth/me', requireAuth, async (req, res) => {
  res.json({
    ok: true,
    user: {
      email: req.identity.email,
      displayName: req.identity.displayName,
      role: req.identity.role,
      avatarUrl: req.identity.avatarUrl,
    },
  });
});

app.get('/api/asset', requireAuth, async (req, res) => {
  const rawPath = String(req.query.path || '');
  if (!rawPath) {
    res.status(400).json({ error: 'MISSING_PATH' });
    return;
  }

  const absolutePath = path.resolve(rawPath);
  const allowedRoots = [ROOT_DIR, IMAGE_DIR, THUMBNAIL_DIR, PREVIEW_DIR].map((root) => path.resolve(root));
  const inAllowedRoot = allowedRoots.some((root) => absolutePath.startsWith(root));

  if (!inAllowedRoot || !fs.existsSync(absolutePath)) {
    res.status(404).json({ error: 'NOT_FOUND' });
    return;
  }

  const extension = path.extname(absolutePath).toLowerCase();
  if (!['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'].includes(extension)) {
    res.status(403).json({ error: 'UNSUPPORTED_FILE_TYPE' });
    return;
  }

  if (isPathInsideRoot(absolutePath, THUMBNAIL_DIR) || isPathInsideRoot(absolutePath, PREVIEW_DIR)) {
    fs.promises.utimes(absolutePath, new Date(), new Date()).catch(() => {
      // Best effort only; LRU can still rely on mtime when atime updates are unavailable.
    });
  }

  res.sendFile(absolutePath);
});

app.get('/api/favorite-categories', requireAuth, async (req, res) => {
  try {
    const rows = await listFavoriteCategories(req.identity.email);
    res.json({ ok: true, categories: rows });
  } catch (error) {
    console.error('[history_portal] /api/favorite-categories failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.post('/api/favorite-categories', requireAuth, async (req, res) => {
  try {
    const displayName = String(req.body?.display_name || '').trim();
    const color = String(req.body?.color || '').trim() || null;

    if (!displayName) {
      res.status(400).json({ error: 'DISPLAY_NAME_REQUIRED' });
      return;
    }

    const key = slugifyKey(displayName);
    const now = nowIso();

    await execute(
      `
      INSERT INTO favorite_categories (
        user_email, category_key, display_name, color, sort_order, is_active, created_at, updated_at
      ) VALUES (?, ?, ?, ?, 100, 1, ?, ?)
      ON CONFLICT(user_email, category_key) DO UPDATE SET
        display_name = excluded.display_name,
        color = COALESCE(excluded.color, favorite_categories.color),
        is_active = 1,
        updated_at = excluded.updated_at
      `,
      [req.identity.email, key, displayName, color, now, now],
    );

    const categories = await listFavoriteCategories(req.identity.email);
    res.json({ ok: true, categories, added: { category_key: key, display_name: displayName } });
  } catch (error) {
    console.error('[history_portal] POST /api/favorite-categories failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.get('/api/collections', requireAuth, async (req, res) => {
  try {
    const collections = await listCollectionsWithPreview(req.identity.email);
    res.json({ ok: true, collections });
  } catch (error) {
    console.error('[history_portal] /api/collections failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.post('/api/collections', requireAuth, async (req, res) => {
  try {
    const name = String(req.body?.name || '').trim();
    if (!name) {
      res.status(400).json({ error: 'NAME_REQUIRED' });
      return;
    }

    const collectionKey = slugifyKey(name);
    const now = nowIso();

    await execute(
      `
      INSERT INTO history_collections (
        user_email, name, collection_key, kind, is_system, sort_order, created_at, updated_at
      ) VALUES (?, ?, ?, 'manual', 0, 100, ?, ?)
      ON CONFLICT(user_email, collection_key) DO UPDATE SET
        name = excluded.name,
        updated_at = excluded.updated_at
      `,
      [req.identity.email, name, collectionKey, now, now],
    );

    const created = await queryOne(
      `
      SELECT id, name, collection_key, kind, is_system, sort_order, created_at, updated_at
      FROM history_collections
      WHERE LOWER(user_email) = LOWER(?) AND collection_key = ?
      LIMIT 1
      `,
      [req.identity.email, collectionKey],
    );

    const collections = await listCollectionsWithPreview(req.identity.email);
    res.json({
      ok: true,
      created: created
        ? {
            id: Number(created.id),
            name: created.name,
            collection_key: created.collection_key,
            kind: created.kind,
            is_system: Number(created.is_system || 0) === 1,
          }
        : null,
      collections,
    });
  } catch (error) {
    console.error('[history_portal] POST /api/collections failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.post('/api/collections/reorder', requireAuth, async (req, res) => {
  try {
    const rawIds = Array.isArray(req.body?.ordered_collection_ids) ? req.body.ordered_collection_ids : [];
    const orderedIds = [];
    const seen = new Set();

    for (const raw of rawIds) {
      const numeric = Number(String(raw || '').trim());
      if (!Number.isInteger(numeric) || numeric <= 0 || seen.has(numeric)) {
        continue;
      }
      seen.add(numeric);
      orderedIds.push(numeric);
    }

    if (!orderedIds.length) {
      res.status(400).json({ error: 'ORDER_REQUIRED' });
      return;
    }

    const existingRows = await queryAll(
      `
      SELECT id
      FROM history_collections
      WHERE LOWER(user_email) = LOWER(?)
      ORDER BY sort_order ASC, updated_at DESC, name ASC
      `,
      [req.identity.email],
    );

    const existingIds = existingRows.map((row) => Number(row.id)).filter((id) => Number.isInteger(id) && id > 0);
    if (!existingIds.length) {
      res.status(404).json({ error: 'NO_COLLECTIONS' });
      return;
    }

    if (orderedIds.length !== existingIds.length) {
      res.status(400).json({ error: 'ORDER_SIZE_MISMATCH' });
      return;
    }

    const existingSet = new Set(existingIds);
    const allOwned = orderedIds.every((id) => existingSet.has(id));
    if (!allOwned) {
      res.status(403).json({ error: 'INVALID_COLLECTION_ORDER' });
      return;
    }

    const now = nowIso();
    for (let index = 0; index < orderedIds.length; index += 1) {
      const collectionId = orderedIds[index];
      await execute(
        `
        UPDATE history_collections
        SET sort_order = ?, updated_at = ?
        WHERE id = ? AND LOWER(user_email) = LOWER(?)
        `,
        [(index + 1) * 10, now, collectionId, req.identity.email],
      );
    }

    const collections = await listCollectionsWithPreview(req.identity.email);
    res.json({ ok: true, collections });
  } catch (error) {
    console.error('[history_portal] POST /api/collections/reorder failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.delete('/api/collections/:collectionId', requireAuth, async (req, res) => {
  try {
    const collectionId = normalizeCollectionId(req.params.collectionId);
    const deleteContents = req.body?.delete_contents === undefined ? true : parseBoolean(req.body?.delete_contents);
    const confirmText = String(req.body?.confirm || '').trim();

    if (!collectionId) {
      res.status(400).json({ error: 'COLLECTION_ID_REQUIRED' });
      return;
    }

    if (confirmText !== 'DELETE_PROJECT') {
      res.status(400).json({ error: 'CONFIRMATION_REQUIRED', expected: 'DELETE_PROJECT' });
      return;
    }

    const collection = await getCollectionById(req.identity.email, collectionId);
    if (!collection) {
      res.status(404).json({ error: 'COLLECTION_NOT_FOUND' });
      return;
    }

    if (Number(collection.is_system || 0) === 1) {
      res.status(403).json({ error: 'SYSTEM_COLLECTION_DELETE_BLOCKED' });
      return;
    }

    let deletedAssetCount = 0;
    const storageCleanup = [];

    if (deleteContents) {
      const taskRows = await queryAll(
        `
        SELECT DISTINCT t.task_id
        FROM history_collection_items hci
        JOIN tasks t ON t.task_id = hci.task_id
        WHERE hci.collection_id = ? AND LOWER(hci.user_email) = LOWER(?) AND LOWER(t.user_email) = LOWER(?)
        `,
        [collectionId, req.identity.email, req.identity.email],
      );

      for (const row of taskRows) {
        const taskId = String(row.task_id || '').trim();
        if (!taskId) {
          continue;
        }
        const deletion = await finalizePendingAssetDelete(req.identity.email, taskId);
        if (deletion.deleted) {
          deletedAssetCount += 1;
        }
        storageCleanup.push({
          task_id: taskId,
          deleted: Boolean(deletion.deleted),
          not_found: Boolean(deletion.notFound),
          storage: deletion.storage,
        });
      }
    }

    await execute(
      `
      DELETE FROM history_collections
      WHERE id = ? AND LOWER(user_email) = LOWER(?)
      `,
      [collectionId, req.identity.email],
    );

    const collections = await listCollectionsWithPreview(req.identity.email);
    res.json({
      ok: true,
      collection_id: collectionId,
      collection_name: collection.name,
      delete_contents: deleteContents,
      deleted_asset_count: deletedAssetCount,
      storage_cleanup: storageCleanup,
      collections,
    });
  } catch (error) {
    console.error('[history_portal] DELETE /api/collections/:collectionId failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.post('/api/history/collection-assign', requireAuth, async (req, res) => {
  try {
    const taskIds = Array.isArray(req.body?.task_ids) ? req.body.task_ids.map((item) => String(item || '').trim()).filter(Boolean) : [];
    const collectionId = normalizeCollectionId(req.body?.collection_id);

    if (!taskIds.length) {
      res.status(400).json({ error: 'TASK_IDS_REQUIRED' });
      return;
    }

    let collection = null;
    if (collectionId) {
      collection = await getCollectionById(req.identity.email, collectionId);
      if (!collection) {
        res.status(404).json({ error: 'COLLECTION_NOT_FOUND' });
        return;
      }
    }

    const ownershipRows = await queryAll(
      `
      SELECT task_id
      FROM tasks
      WHERE LOWER(user_email) = LOWER(?) AND task_id IN (${taskIds.map(() => '?').join(',')}) AND COALESCE(is_deleted, 0) = 0
      `,
      [req.identity.email, ...taskIds],
    );
    const ownedTaskIds = new Set(ownershipRows.map((row) => String(row.task_id)));
    const validTaskIds = taskIds.filter((taskId) => ownedTaskIds.has(taskId));

    if (!validTaskIds.length) {
      res.status(404).json({ error: 'NO_VALID_TASKS' });
      return;
    }

    const now = nowIso();
    for (const taskId of validTaskIds) {
      if (!collection) {
        await execute(
          `
          DELETE FROM history_collection_items
          WHERE LOWER(user_email) = LOWER(?) AND task_id = ?
          `,
          [req.identity.email, taskId],
        );
      } else {
        await execute(
          `
          INSERT INTO history_collection_items (
            collection_id, task_id, user_email, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?)
          ON CONFLICT(user_email, task_id) DO UPDATE SET
            collection_id = excluded.collection_id,
            updated_at = excluded.updated_at
          `,
          [collection.id, taskId, req.identity.email, now, now],
        );
      }
    }

    const collections = await listCollectionsWithPreview(req.identity.email);
    res.json({
      ok: true,
      updated_count: validTaskIds.length,
      collection: collection
        ? {
            id: Number(collection.id),
            name: collection.name,
          }
        : null,
      collections,
    });
  } catch (error) {
    console.error('[history_portal] POST /api/history/collection-assign failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.post('/api/history/:taskId/collection', requireAuth, async (req, res) => {
  try {
    const taskId = String(req.params.taskId || '').trim();
    const collectionId = normalizeCollectionId(req.body?.collection_id);
    if (!taskId) {
      res.status(400).json({ error: 'TASK_ID_REQUIRED' });
      return;
    }

    const payload = await queryOne(
      `
      SELECT task_id
      FROM tasks
      WHERE task_id = ? AND LOWER(user_email) = LOWER(?) AND COALESCE(is_deleted, 0) = 0
      LIMIT 1
      `,
      [taskId, req.identity.email],
    );

    if (!payload) {
      res.status(404).json({ error: 'NOT_FOUND' });
      return;
    }

    let collection = null;
    if (collectionId) {
      collection = await getCollectionById(req.identity.email, collectionId);
      if (!collection) {
        res.status(404).json({ error: 'COLLECTION_NOT_FOUND' });
        return;
      }
    }

    const now = nowIso();
    if (!collection) {
      await execute(
        `
        DELETE FROM history_collection_items
        WHERE LOWER(user_email) = LOWER(?) AND task_id = ?
        `,
        [req.identity.email, taskId],
      );
    } else {
      await execute(
        `
        INSERT INTO history_collection_items (
          collection_id, task_id, user_email, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_email, task_id) DO UPDATE SET
          collection_id = excluded.collection_id,
          updated_at = excluded.updated_at
        `,
        [collection.id, taskId, req.identity.email, now, now],
      );
    }

    res.json({
      ok: true,
      assignment: {
        task_id: taskId,
        collection_id: collection ? Number(collection.id) : null,
        collection_name: collection ? collection.name : null,
      },
    });
  } catch (error) {
    console.error('[history_portal] POST /api/history/:taskId/collection failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.get('/api/history', requireAuth, async (req, res) => {
  try {
    const page = Math.max(1, Number(req.query.page || 1));
    const pageSize = Math.min(120, Math.max(12, Number(req.query.page_size || 36)));
    const search = String(req.query.search || '').trim().toLowerCase();
    const scope = normalizeScope(req.query.scope);
    const workflowName = normalizeFilterValue(req.query.workflow_name);
    const workflowCategory = normalizeFilterValue(req.query.workflow_category);
    const status = normalizeStatus(req.query.status);
    const favoritesOnly = parseBoolean(req.query.favorites_only);
    const favoriteCategory = normalizeFilterValue(req.query.favorite_category);
    const selectedCollectionId = normalizeCollectionId(req.query.collection_id);
    const hideFolderContents = parseBoolean(req.query.hide_folder_contents);
    const datePreset = String(req.query.date_preset || '').trim().toLowerCase();
    const presetRange = buildDateRangeFromPreset(datePreset);
    const dateFrom = normalizeDateBound(req.query.date_from, false) || presetRange.dateFrom;
    const dateTo = normalizeDateBound(req.query.date_to, true) || presetRange.dateTo;
    const orderBy = normalizeSort(req.query.sort);

    const whereParts = [
      'LOWER(t.user_email) = LOWER(?)',
      'COALESCE(t.is_deleted, 0) = 0',
    ];
    const whereParams = [req.identity.email];

    if (selectedCollectionId) {
      const selectedCollection = await getCollectionById(req.identity.email, selectedCollectionId);
      if (!selectedCollection) {
        res.status(404).json({ error: 'COLLECTION_NOT_FOUND' });
        return;
      }
      whereParts.push('hci.collection_id = ?');
      whereParams.push(selectedCollectionId);
    } else if (scope === 'uncategorized') {
      whereParts.push('hci.collection_id IS NULL');
    } else if (hideFolderContents) {
      whereParts.push('hci.collection_id IS NULL');
    }

    if (search) {
      whereParts.push(
        `(
          LOWER(COALESCE(t.display_title, '')) LIKE ? OR
          LOWER(COALESCE(t.workflow_name, '')) LIKE ? OR
          LOWER(COALESCE(t.task_id, '')) LIKE ? OR
          LOWER(COALESCE(t.request_id, '')) LIKE ?
        )`,
      );
      const term = `%${search}%`;
      whereParams.push(term, term, term, term);
    }

    if (workflowName) {
      whereParts.push('t.workflow_name = ?');
      whereParams.push(workflowName);
    }

    if (workflowCategory) {
      whereParts.push("COALESCE(t.workflow_category, w.category, 'Uncategorized') = ?");
      whereParams.push(workflowCategory);
    }

    if (status) {
      whereParts.push('LOWER(COALESCE(t.status, \"\")) = LOWER(?)');
      whereParams.push(status);
    }

    if (scope === 'favorites' || favoritesOnly) {
      whereParts.push('COALESCE(tf.is_favorite, 0) = 1');
    }

    if (favoriteCategory !== null) {
      if (favoriteCategory === '') {
        whereParts.push("COALESCE(tf.favorite_category_key, '') = ''");
      } else {
        whereParts.push('COALESCE(tf.favorite_category_key, \"\") = ?');
        whereParams.push(favoriteCategory);
      }
    }

    if (dateFrom) {
      whereParts.push('COALESCE(t.submitted_at, t.created_at) >= ?');
      whereParams.push(dateFrom);
    }

    if (dateTo) {
      whereParts.push('COALESCE(t.submitted_at, t.created_at) <= ?');
      whereParams.push(dateTo);
    }

    const whereSql = whereParts.join(' AND ');
    const totalRow = await queryOne(
      `
      SELECT COUNT(*) AS total
      FROM tasks t
      LEFT JOIN workflows w ON w.workflow_key = t.workflow_key
      LEFT JOIN task_favorites tf
        ON tf.task_id = t.task_id AND LOWER(tf.user_email) = LOWER(?)
      LEFT JOIN history_collection_items hci
        ON hci.task_id = t.task_id AND LOWER(hci.user_email) = LOWER(?)
      LEFT JOIN history_collections hc
        ON hc.id = hci.collection_id AND LOWER(hc.user_email) = LOWER(?)
      WHERE ${whereSql}
      `,
      [req.identity.email, req.identity.email, req.identity.email, ...whereParams],
    );

    const totalItems = Number(totalRow?.total || 0);
    const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
    const safePage = Math.min(page, totalPages);
    const offset = (safePage - 1) * pageSize;

    const rows = await queryAll(
      `
      SELECT
        t.task_id,
        t.request_id,
        t.workflow_name,
        COALESCE(t.workflow_category, w.category, 'Uncategorized') AS workflow_category,
        t.workflow_type,
        t.status,
        COALESCE(t.submitted_at, t.created_at) AS created_at,
        t.total_duration_ms,
        t.result_url,
        t.thumbnail_url,
        t.preview_url,
        t.output_filename,
        t.output_count,
        t.output_width,
        t.output_height,
        t.failure_reason,
        t.error_message,
        COALESCE(tf.is_favorite, 0) AS is_favorite,
        tf.favorite_category_key,
        COALESCE(tf.is_pinned, 0) AS is_pinned,
        hci.collection_id,
        hc.name AS collection_name
      FROM tasks t
      LEFT JOIN workflows w ON w.workflow_key = t.workflow_key
      LEFT JOIN task_favorites tf
        ON tf.task_id = t.task_id AND LOWER(tf.user_email) = LOWER(?)
      LEFT JOIN history_collection_items hci
        ON hci.task_id = t.task_id AND LOWER(hci.user_email) = LOWER(?)
      LEFT JOIN history_collections hc
        ON hc.id = hci.collection_id AND LOWER(hc.user_email) = LOWER(?)
      WHERE ${whereSql}
      ORDER BY ${orderBy}
      LIMIT ? OFFSET ?
      `,
      [req.identity.email, req.identity.email, req.identity.email, ...whereParams, pageSize, offset],
    );

    const workflowFacets = await queryAll(
      `
      SELECT workflow_name, COUNT(*) AS total
      FROM tasks
      WHERE LOWER(user_email) = LOWER(?) AND COALESCE(is_deleted, 0) = 0
      GROUP BY workflow_name
      ORDER BY total DESC, workflow_name ASC
      `,
      [req.identity.email],
    );

    const workflowCategoryFacets = await queryAll(
      `
      SELECT COALESCE(t.workflow_category, w.category, 'Uncategorized') AS workflow_category, COUNT(*) AS total
      FROM tasks t
      LEFT JOIN workflows w ON w.workflow_key = t.workflow_key
      WHERE LOWER(t.user_email) = LOWER(?) AND COALESCE(t.is_deleted, 0) = 0
      GROUP BY COALESCE(t.workflow_category, w.category, 'Uncategorized')
      ORDER BY total DESC, workflow_category ASC
      `,
      [req.identity.email],
    );

    const statusFacets = await queryAll(
      `
      SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS total
      FROM tasks
      WHERE LOWER(user_email) = LOWER(?) AND COALESCE(is_deleted, 0) = 0
      GROUP BY COALESCE(status, 'unknown')
      ORDER BY total DESC
      `,
      [req.identity.email],
    );

    const favoritesTotalRow = await queryOne(
      `
      SELECT COUNT(*) AS total
      FROM task_favorites
      WHERE LOWER(user_email) = LOWER(?) AND COALESCE(is_favorite, 0) = 1
      `,
      [req.identity.email],
    );

    const categories = await listFavoriteCategories(req.identity.email);
    const collections = await listCollectionsWithPreview(req.identity.email);
    const thumbnailStorage = await getThumbnailStorageStats();

    const items = rows.map((row) => ({
      task_id: row.task_id,
      request_id: row.request_id || null,
      workflow_name: row.workflow_name || 'Unknown workflow',
      workflow_category: row.workflow_category || 'Uncategorized',
      workflow_type: row.workflow_type || null,
      status: row.status || 'unknown',
      created_at: row.created_at || null,
      total_duration_ms: row.total_duration_ms || 0,
      duration_text: formatDuration(row.total_duration_ms || 0),
      result_url: normalizeAssetUrl(row.result_url),
      thumbnail_url: normalizeAssetUrl(row.thumbnail_url),
      preview_url: normalizeAssetUrl(row.preview_url),
      output_filename: row.output_filename || null,
      output_count: Number(row.output_count || 0),
      output_width: row.output_width || null,
      output_height: row.output_height || null,
      failure_reason: row.failure_reason || null,
      error_message: row.error_message || null,
      is_favorite: Number(row.is_favorite || 0) === 1,
      favorite_category_key: row.favorite_category_key || null,
      is_pinned: Number(row.is_pinned || 0) === 1,
      collection_id: row.collection_id ? Number(row.collection_id) : null,
      collection_name: row.collection_name || null,
    }));

    res.json({
      ok: true,
      scope,
      items,
      page: safePage,
      page_size: pageSize,
      total_items: totalItems,
      total_pages: totalPages,
      favorites_total: Number(favoritesTotalRow?.total || 0),
      workflow_facets: workflowFacets,
      workflow_category_facets: workflowCategoryFacets,
      status_facets: statusFacets,
      favorite_categories: categories,
      collections,
      thumbnail_storage: thumbnailStorage,
      selected_collection_id: selectedCollectionId,
      hide_folder_contents: hideFolderContents,
    });
  } catch (error) {
    console.error('[history_portal] /api/history failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.get('/api/history/:taskId', requireAuth, async (req, res) => {
  try {
    const taskId = String(req.params.taskId || '').trim();
    if (!taskId) {
      res.status(400).json({ error: 'TASK_ID_REQUIRED' });
      return;
    }

    const row = await queryOne(
      `
      SELECT
        t.task_id,
        t.request_id,
        t.workflow_name,
        COALESCE(t.workflow_category, w.category, 'Uncategorized') AS workflow_category,
        t.status,
        COALESCE(t.submitted_at, t.created_at) AS created_at,
        t.total_duration_ms,
        t.result_url,
        t.thumbnail_url,
        t.preview_url,
        t.output_filename,
        t.output_count,
        t.output_width,
        t.output_height,
        t.failure_reason,
        t.error_message,
        t.latest_message,
        COALESCE(tf.is_favorite, 0) AS is_favorite,
        tf.favorite_category_key,
        COALESCE(tf.is_pinned, 0) AS is_pinned,
        hci.collection_id,
        hc.name AS collection_name
      FROM tasks t
      LEFT JOIN workflows w ON w.workflow_key = t.workflow_key
      LEFT JOIN task_favorites tf
        ON tf.task_id = t.task_id AND LOWER(tf.user_email) = LOWER(?)
      LEFT JOIN history_collection_items hci
        ON hci.task_id = t.task_id AND LOWER(hci.user_email) = LOWER(?)
      LEFT JOIN history_collections hc
        ON hc.id = hci.collection_id AND LOWER(hc.user_email) = LOWER(?)
      WHERE t.task_id = ? AND LOWER(t.user_email) = LOWER(?) AND COALESCE(t.is_deleted, 0) = 0
      LIMIT 1
      `,
      [req.identity.email, req.identity.email, req.identity.email, taskId, req.identity.email],
    );

    if (!row) {
      res.status(404).json({ error: 'NOT_FOUND' });
      return;
    }

    res.json({
      ok: true,
      item: {
        task_id: row.task_id,
        request_id: row.request_id || null,
        workflow_name: row.workflow_name || 'Unknown workflow',
        workflow_category: row.workflow_category || 'Uncategorized',
        status: row.status || 'unknown',
        created_at: row.created_at || null,
        total_duration_ms: row.total_duration_ms || 0,
        duration_text: formatDuration(row.total_duration_ms || 0),
        result_url: normalizeAssetUrl(row.result_url),
        thumbnail_url: normalizeAssetUrl(row.thumbnail_url),
        preview_url: normalizeAssetUrl(row.preview_url),
        output_filename: row.output_filename || null,
        output_count: Number(row.output_count || 0),
        output_width: row.output_width || null,
        output_height: row.output_height || null,
        failure_reason: row.failure_reason || null,
        error_message: row.error_message || null,
        latest_message: row.latest_message || null,
        is_favorite: Number(row.is_favorite || 0) === 1,
        favorite_category_key: row.favorite_category_key || null,
        is_pinned: Number(row.is_pinned || 0) === 1,
        collection_id: row.collection_id ? Number(row.collection_id) : null,
        collection_name: row.collection_name || null,
      },
    });
  } catch (error) {
    console.error('[history_portal] /api/history/:taskId failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.post('/api/history/:taskId/favorite', requireAuth, async (req, res) => {
  try {
    const taskId = String(req.params.taskId || '').trim();
    const isFavorite = parseBoolean(req.body?.is_favorite);
    const rawCategoryKey = normalizeFilterValue(req.body?.favorite_category_key);
    const favoriteCategoryKey = rawCategoryKey === '' ? null : rawCategoryKey;
    const notes = req.body?.notes ? String(req.body.notes).trim() : null;
    const isPinned = req.body?.is_pinned === undefined ? null : parseBoolean(req.body.is_pinned);

    if (!taskId) {
      res.status(400).json({ error: 'TASK_ID_REQUIRED' });
      return;
    }

    const taskRow = await queryOne(
      `
      SELECT task_id
      FROM tasks
      WHERE task_id = ? AND LOWER(user_email) = LOWER(?) AND COALESCE(is_deleted, 0) = 0
      LIMIT 1
      `,
      [taskId, req.identity.email],
    );

    if (!taskRow) {
      res.status(404).json({ error: 'NOT_FOUND' });
      return;
    }

    const now = nowIso();

    await execute(
      `
      INSERT INTO task_favorites (
        task_id, user_email, is_favorite, favorite_category_key, notes, is_pinned, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(task_id, user_email) DO UPDATE SET
        is_favorite = excluded.is_favorite,
        favorite_category_key = excluded.favorite_category_key,
        notes = COALESCE(excluded.notes, task_favorites.notes),
        is_pinned = COALESCE(excluded.is_pinned, task_favorites.is_pinned),
        updated_at = excluded.updated_at
      `,
      [
        taskId,
        req.identity.email,
        isFavorite ? 1 : 0,
        favoriteCategoryKey,
        notes,
        isPinned === null ? 0 : isPinned ? 1 : 0,
        now,
        now,
      ],
    );

    const favoriteRow = await queryOne(
      `
      SELECT
        task_id,
        user_email,
        COALESCE(is_favorite, 0) AS is_favorite,
        favorite_category_key,
        notes,
        COALESCE(is_pinned, 0) AS is_pinned,
        updated_at
      FROM task_favorites
      WHERE task_id = ? AND LOWER(user_email) = LOWER(?)
      LIMIT 1
      `,
      [taskId, req.identity.email],
    );

    res.json({
      ok: true,
      favorite: {
        task_id: favoriteRow.task_id,
        user_email: favoriteRow.user_email,
        is_favorite: Number(favoriteRow.is_favorite || 0) === 1,
        favorite_category_key: favoriteRow.favorite_category_key || null,
        notes: favoriteRow.notes || null,
        is_pinned: Number(favoriteRow.is_pinned || 0) === 1,
        updated_at: favoriteRow.updated_at,
      },
    });
  } catch (error) {
    console.error('[history_portal] POST /api/history/:taskId/favorite failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.post('/api/history/:taskId/delete', requireAuth, async (req, res) => {
  try {
    const taskId = String(req.params.taskId || '').trim();
    if (!taskId) {
      res.status(400).json({ error: 'TASK_ID_REQUIRED' });
      return;
    }

    const taskRow = await getTaskForOwner(req.identity.email, taskId);
    if (!taskRow) {
      res.status(404).json({ error: 'NOT_FOUND' });
      return;
    }

    const now = nowIso();
    await execute(
      `
      UPDATE tasks
      SET
        is_deleted = 1,
        deleted_at = COALESCE(deleted_at, ?),
        updated_at = ?
      WHERE task_id = ? AND LOWER(user_email) = LOWER(?)
      `,
      [now, now, taskId, req.identity.email],
    );

    const pending = schedulePendingAssetDelete(req.identity.email, taskId);
    const collections = await listCollectionsWithPreview(req.identity.email);

    res.json({
      ok: true,
      task_id: taskId,
      pending: true,
      undo_window_ms: DELETE_UNDO_WINDOW_MS,
      expires_at: new Date(pending.expiresAtMs).toISOString(),
      collections,
    });
  } catch (error) {
    console.error('[history_portal] POST /api/history/:taskId/delete failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.post('/api/history/:taskId/delete/undo', requireAuth, async (req, res) => {
  try {
    const taskId = String(req.params.taskId || '').trim();
    if (!taskId) {
      res.status(400).json({ error: 'TASK_ID_REQUIRED' });
      return;
    }

    const pending = cancelPendingAssetDelete(req.identity.email, taskId);
    if (!pending) {
      res.status(409).json({ error: 'NOT_PENDING_DELETE' });
      return;
    }

    await execute(
      `
      UPDATE tasks
      SET
        is_deleted = 0,
        deleted_at = NULL,
        updated_at = ?
      WHERE task_id = ? AND LOWER(user_email) = LOWER(?)
      `,
      [nowIso(), taskId, req.identity.email],
    );

    const collections = await listCollectionsWithPreview(req.identity.email);
    res.json({
      ok: true,
      task_id: taskId,
      restored: true,
      collections,
    });
  } catch (error) {
    console.error('[history_portal] POST /api/history/:taskId/delete/undo failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.delete('/api/history/:taskId/delete', requireAuth, async (req, res) => {
  try {
    const taskId = String(req.params.taskId || '').trim();
    if (!taskId) {
      res.status(400).json({ error: 'TASK_ID_REQUIRED' });
      return;
    }

    const deletion = await finalizePendingAssetDelete(req.identity.email, taskId);
    if (!deletion.deleted && deletion.notFound) {
      res.status(404).json({ error: 'NOT_FOUND' });
      return;
    }

    const collections = await listCollectionsWithPreview(req.identity.email);
    res.json({
      ok: true,
      task_id: taskId,
      deleted: Boolean(deletion.deleted),
      storage: deletion.storage,
      collections,
    });
  } catch (error) {
    console.error('[history_portal] DELETE /api/history/:taskId/delete failed:', error);
    res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

app.get('/', (_req, res) => {
  staticNoCacheHeaders(res);
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

async function startServer() {
  try {
    await ensurePortalSchema();
    app.listen(HISTORY_PORT, HISTORY_HOST, () => {
      console.log(`[history_portal] listening on http://${HISTORY_HOST}:${HISTORY_PORT}`);
      console.log(`[history_portal] DB_PATH=${DB_PATH}`);
    });
  } catch (error) {
    console.error('[history_portal] Failed to initialize schema:', error);
    process.exit(1);
  }
}

startServer();
