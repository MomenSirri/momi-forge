'use strict';

const dom = {
  loginView: document.getElementById('loginView'),
  appView: document.getElementById('appView'),
  loginForm: document.getElementById('loginForm'),
  emailInput: document.getElementById('emailInput'),
  passwordInput: document.getElementById('passwordInput'),
  loginError: document.getElementById('loginError'),
  logoutBtn: document.getElementById('logoutBtn'),
  userAvatar: document.getElementById('userAvatar'),
  userName: document.getElementById('userName'),
  userEmail: document.getElementById('userEmail'),

  scopeSelect: document.getElementById('scopeSelect'),
  typeSelect: document.getElementById('typeSelect'),
  datePresetSelect: document.getElementById('datePresetSelect'),
  favoritesCheckbox: document.getElementById('favoritesCheckbox'),
  hideFolderContentCheckbox: document.getElementById('hideFolderContentCheckbox'),
  sortSelect: document.getElementById('sortSelect'),
  viewModeSelect: document.getElementById('viewModeSelect'),

  searchInput: document.getElementById('searchInput'),
  statusSelect: document.getElementById('statusSelect'),
  favoriteCategoryFilterSelect: document.getElementById('favoriteCategoryFilterSelect'),
  dateFromInput: document.getElementById('dateFromInput'),
  dateToInput: document.getElementById('dateToInput'),
  pageSizeSelect: document.getElementById('pageSizeSelect'),
  applyFiltersBtn: document.getElementById('applyFiltersBtn'),
  resetFiltersBtn: document.getElementById('resetFiltersBtn'),
  refreshBtn: document.getElementById('refreshBtn'),

  selectionBar: document.getElementById('selectionBar'),
  selectionCount: document.getElementById('selectionCount'),
  bulkCollectionSelect: document.getElementById('bulkCollectionSelect'),
  bulkAssignBtn: document.getElementById('bulkAssignBtn'),
  clearSelectionBtn: document.getElementById('clearSelectionBtn'),

  summaryPills: document.getElementById('summaryPills'),
  statusText: document.getElementById('statusText'),
  folderCount: document.getElementById('folderCount'),
  folderGrid: document.getElementById('folderGrid'),
  projectSection: document.getElementById('projectSection'),
  toggleProjectsBtn: document.getElementById('toggleProjectsBtn'),
  assetsTitle: document.getElementById('assetsTitle'),
  assetsMeta: document.getElementById('assetsMeta'),
  groupedContainer: document.getElementById('groupedContainer'),
  emptyState: document.getElementById('emptyState'),

  prevBtn: document.getElementById('prevBtn'),
  nextBtn: document.getElementById('nextBtn'),
  pageText: document.getElementById('pageText'),

  detailModal: document.getElementById('detailModal'),
  detailModalPanel: document.getElementById('detailModalPanel'),
  closeModalBtn: document.getElementById('closeModalBtn'),
  modalPreview: document.getElementById('modalPreview'),
  detailImageBackdrop: document.getElementById('detailImageBackdrop'),
  detailImage: document.getElementById('detailImage'),
  detailPreviewFallback: document.getElementById('detailPreviewFallback'),
  detailRuntimeOrb: document.getElementById('detailRuntimeOrb'),
  detailProjectChip: document.getElementById('detailProjectChip'),
  detailCreatedChip: document.getElementById('detailCreatedChip'),
  detailKicker: document.getElementById('detailKicker'),
  detailTitle: document.getElementById('detailTitle'),
  detailRequestLine: document.getElementById('detailRequestLine'),
  detailOutputStat: document.getElementById('detailOutputStat'),
  openOriginalLink: document.getElementById('openOriginalLink'),
  downloadLink: document.getElementById('downloadLink'),
  copyTaskBtn: document.getElementById('copyTaskBtn'),
  toggleFavoriteBtn: document.getElementById('toggleFavoriteBtn'),
  favoriteCategoryAssignSelect: document.getElementById('favoriteCategoryAssignSelect'),
  collectionAssignSelect: document.getElementById('collectionAssignSelect'),
  newCategoryInput: document.getElementById('newCategoryInput'),
  addCategoryBtn: document.getElementById('addCategoryBtn'),
  deleteAssetBtn: document.getElementById('deleteAssetBtn'),
  modalStatusText: document.getElementById('modalStatusText'),

  confirmModal: document.getElementById('confirmModal'),
  confirmTitle: document.getElementById('confirmTitle'),
  confirmMessage: document.getElementById('confirmMessage'),
  confirmCancelBtn: document.getElementById('confirmCancelBtn'),
  confirmDeleteBtn: document.getElementById('confirmDeleteBtn'),

  toastHost: document.getElementById('toastHost'),
};

const initialFilters = () => ({
  scope: 'all',
  workflowCategory: '',
  datePreset: 'today',
  favoritesOnly: false,
  hideFolderContents: false,
  sort: 'newest',
  viewMode: 'unified',
  search: '',
  status: '',
  favoriteCategory: '',
  dateFrom: '',
  dateTo: '',
  page: 1,
  pageSize: 36,
  collectionId: null,
});

const state = {
  user: null,
  sso: null,
  filters: initialFilters(),
  loading: false,
  items: [],
  collections: [],
  favoriteCategories: [],
  workflowCategoryFacets: [],
  statusFacets: [],
  workflowFacets: [],
  thumbnailStorage: null,
  pagination: {
    page: 1,
    pageSize: 36,
    totalPages: 1,
    totalItems: 0,
  },
  favoritesTotal: 0,
  selectedTaskIds: new Set(),
  activeTask: null,
  activeImageMetrics: null,
  detailAccentToken: 0,
  detailForm: {
    collectionValue: '',
    favoriteCategoryValue: '',
    savingCollection: false,
    savingFavoriteCategory: false,
  },
  projectsCollapsed: false,
  draggedCollectionId: null,
  confirmAction: null,
  pendingDeleteToasts: new Map(),
};

const WORKFLOW_DISPLAY_ALIASES = {
  myotherworkflow: 'Pro Upscaler',
  '5kupscale': 'Pro Upscaler',
  '5kupscalerflux': 'Pro Upscaler',
  proupscaler: 'Pro Upscaler',
  generalenhancementv04: 'General Enhancement',
  generalenhancement: 'General Enhancement',
};

const HISTORY_PROXY_PREFIX = (() => {
  const marker = '/history-proxy';
  const pathname = String(window.location.pathname || '');
  const lower = pathname.toLowerCase();
  const index = lower.indexOf(marker);
  if (index === -1) {
    return '';
  }
  return pathname.slice(0, index + marker.length);
})();

const PROJECTS_COLLAPSED_STORAGE_KEY = 'momi_history_projects_collapsed';

function withPortalPrefix(url) {
  const value = String(url || '');
  if (!value.startsWith('/')) {
    return value;
  }
  if (!HISTORY_PROXY_PREFIX) {
    return value;
  }
  if (value === HISTORY_PROXY_PREFIX || value.startsWith(`${HISTORY_PROXY_PREFIX}/`)) {
    return value;
  }
  return `${HISTORY_PROXY_PREFIX}${value}`;
}

const DEFAULT_DETAIL_THEME = {
  uiSidebarBg: [44, 64, 79],
  uiPrimaryAccent: [214, 154, 89],
  uiPrimaryAccentContrast: [37, 31, 25],
  uiSecondaryBadge: [126, 115, 96],
  uiSecondaryBadgeAlpha: 0.15,
  imageLuminance: 0.32,
  isDarkImage: true,
  bgAccent: [44, 64, 79],
  primaryAccent: [214, 154, 89],
  primaryAccentContrast: [37, 31, 25],
  subtleAccentAlpha: 0.18,
  accent: [214, 154, 89],
  accentSoft: [214, 154, 89],
  accentContrast: [37, 31, 25],
  muted: [44, 64, 79],
  mutedContrast: [245, 248, 250],
  panelText: [245, 248, 250],
  panelMuted: [173, 187, 197],
  panelCard: [67, 89, 105],
  panelCardBorder: [96, 118, 136],
  panelInput: [58, 79, 95],
  badgeText: [247, 249, 251],
};

const NEUTRAL_DETAIL_THEME = {
  ...DEFAULT_DETAIL_THEME,
  uiSidebarBg: [26, 31, 38], // #1a1f26 neutral waiting state
  uiPrimaryAccent: [95, 108, 124],
  uiPrimaryAccentContrast: [244, 247, 250],
  uiSecondaryBadge: [95, 108, 124],
  uiSecondaryBadgeAlpha: 0.15,
  imageLuminance: 0.2,
  isDarkImage: true,
  bgAccent: [26, 31, 38],
  primaryAccent: [95, 108, 124],
  primaryAccentContrast: [244, 247, 250],
  subtleAccentAlpha: 0.15,
  accent: [95, 108, 124],
  accentSoft: [95, 108, 124],
  accentContrast: [244, 247, 250],
  muted: [26, 31, 38],
  mutedContrast: [244, 247, 250],
  panelText: [242, 246, 250],
  panelMuted: [160, 171, 184],
  panelCard: [56, 68, 82],
  panelCardBorder: [90, 104, 120],
  panelInput: [49, 61, 74],
  badgeText: [244, 247, 250],
};

function parseSsoContextFromUrl() {
  const params = new URLSearchParams(window.location.search || '');
  const email = (params.get('email') || '').trim().toLowerCase();
  const exp = (params.get('exp') || '').trim();
  const nonce = (params.get('nonce') || '').trim();
  const sig = (params.get('sig') || '').trim();

  if (!email || !exp || !nonce || !sig) {
    return null;
  }

  return { email, exp, nonce, sig };
}

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function truncate(value, limit = 40) {
  const text = String(value || '');
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, Math.max(1, limit - 1))}…`;
}

function formatDate(value) {
  if (!value) {
    return '-';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '-';
  }
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatCardDateParts(value) {
  if (!value) {
    return { date: '-', time: '' };
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return { date: '-', time: '' };
  }
  return {
    date: date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    }),
    time: date.toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
    }),
  };
}

function formatCount(value) {
  const numeric = Number(value || 0);
  return numeric.toLocaleString();
}

function formatStorageGb(bytes) {
  const numeric = Number(bytes || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return '0.0';
  }
  const gb = numeric / (1024 ** 3);
  return gb >= 10 ? gb.toFixed(1) : gb.toFixed(2);
}

function formatWorkflowKicker(value) {
  const text = String(value || 'history item').replaceAll('_', ' ').trim();
  if (!text) {
    return 'History item';
  }
  return text.toUpperCase();
}

function normalizeWorkflowAliasKey(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '');
}

function formatWorkflowDisplayName(value) {
  const raw = String(value || '').trim();
  if (!raw) {
    return 'History item';
  }

  const rawAlias = WORKFLOW_DISPLAY_ALIASES[normalizeWorkflowAliasKey(raw)];
  if (rawAlias) {
    return rawAlias;
  }

  const strippedVersion = raw.replace(/(?:[_\s]+v\d+)$/i, '');
  const strippedAlias = WORKFLOW_DISPLAY_ALIASES[normalizeWorkflowAliasKey(strippedVersion)];
  if (strippedAlias) {
    return strippedAlias;
  }

  const withSpaces = strippedVersion.replaceAll('_', ' ');
  const normalized = withSpaces.replace(/\s+/g, ' ').trim();

  if (!normalized) {
    return 'History item';
  }

  return normalized
    .split(' ')
    .map((word) => {
      const token = String(word || '').trim();
      if (!token) {
        return '';
      }
      if (/^[A-Z0-9]{2,4}$/.test(token)) {
        return token;
      }
      return token.charAt(0).toUpperCase() + token.slice(1).toLowerCase();
    })
    .join(' ');
}

function formatOutputSize(width, height) {
  const safeWidth = width || '?';
  const safeHeight = height || '?';
  return `${safeWidth} x ${safeHeight}`;
}

function resolveDisplayImageUrl(rawUrl) {
  const value = String(rawUrl || '').trim();
  if (!value) {
    return '';
  }

  try {
    const target = new URL(value, window.location.origin);
    if (target.origin === window.location.origin) {
      const localPath = `${target.pathname || ''}${target.search || ''}${target.hash || ''}`;
      if (
        target.pathname.startsWith('/api/') ||
        target.pathname.startsWith('/avatars/')
      ) {
        return withPortalPrefix(localPath);
      }
      return target.toString();
    }
    return withPortalPrefix(`/api/media/proxy?url=${encodeURIComponent(target.toString())}`);
  } catch (_error) {
    if (value.startsWith('/')) {
      return withPortalPrefix(value);
    }
    return value;
  }
}

function setPanelSizeVars(nextVars = {}) {
  const panel = dom.detailModalPanel;
  if (!panel) {
    return;
  }

  const keys = [
    '--modal-card-width',
    '--modal-card-height',
    '--modal-preview-width',
    '--modal-preview-height',
    '--modal-sidebar-width',
    '--modal-sidebar-height',
    '--modal-preview-aspect',
  ];

  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(nextVars, key) && nextVars[key]) {
      panel.style.setProperty(key, nextVars[key]);
    } else {
      panel.style.removeProperty(key);
    }
  }
}

function setPreviewImageMode(width, height) {
  const preview = dom.modalPreview;
  const panel = dom.detailModalPanel;
  if (!preview || !panel) {
    return;
  }

  const numericWidth = Number(width || 0);
  const numericHeight = Number(height || 0);
  const safeWidth = numericWidth > 1 ? numericWidth : 1280;
  const safeHeight = numericHeight > 1 ? numericHeight : 832;
  const imageAspect = clamp(safeWidth / safeHeight, 0.2, 6);

  state.activeImageMetrics = {
    width: safeWidth,
    height: safeHeight,
  };

  const viewportMaxWidth = Math.max(480, window.innerWidth * 0.95);
  const viewportMaxHeight = Math.max(360, window.innerHeight * 0.92);

  // Keep the sidebar stable at 320px, but shrink only if the viewport cannot fit both columns.
  let sidebarWidth = 320;
  const minPreviewWidth = 220;
  if (viewportMaxWidth < sidebarWidth + minPreviewWidth) {
    sidebarWidth = Math.max(200, viewportMaxWidth - minPreviewWidth);
  }

  let previewWidth = Math.max(minPreviewWidth, viewportMaxWidth - sidebarWidth);
  let previewHeight = previewWidth / imageAspect;
  if (previewHeight > viewportMaxHeight) {
    previewHeight = viewportMaxHeight;
    previewWidth = previewHeight * imageAspect;
  }

  previewWidth = Math.max(minPreviewWidth, previewWidth);
  previewHeight = Math.max(260, Math.min(previewHeight, viewportMaxHeight));

  const cardWidth = previewWidth + sidebarWidth;
  const cardHeight = Math.min(viewportMaxHeight, previewHeight);

  setPanelSizeVars({
    '--modal-card-width': `${Math.round(cardWidth)}px`,
    '--modal-card-height': `${Math.round(cardHeight)}px`,
    '--modal-preview-width': `${Math.round(previewWidth)}px`,
    '--modal-preview-height': `${Math.round(cardHeight)}px`,
    '--modal-sidebar-width': `${Math.round(sidebarWidth)}px`,
    '--modal-sidebar-height': `${Math.round(cardHeight)}px`,
    '--modal-preview-aspect': `${safeWidth} / ${safeHeight}`,
  });

  preview.classList.toggle('is-portrait', safeHeight > safeWidth);
}

function truncateMiddle(value, leading = 16, trailing = 10) {
  const text = String(value || '');
  if (!text || text.length <= leading + trailing + 1) {
    return text || '-';
  }
  return `${text.slice(0, leading)}…${text.slice(-trailing)}`;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function mixRgb(left, right, amount) {
  return [
    Math.round(left[0] + (right[0] - left[0]) * amount),
    Math.round(left[1] + (right[1] - left[1]) * amount),
    Math.round(left[2] + (right[2] - left[2]) * amount),
  ];
}

function rgbToHsl(r, g, b) {
  const red = r / 255;
  const green = g / 255;
  const blue = b / 255;
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const lightness = (max + min) / 2;

  if (max === min) {
    return [0, 0, lightness];
  }

  const delta = max - min;
  const saturation = lightness > 0.5 ? delta / (2 - max - min) : delta / (max + min);

  let hue;
  switch (max) {
    case red:
      hue = (green - blue) / delta + (green < blue ? 6 : 0);
      break;
    case green:
      hue = (blue - red) / delta + 2;
      break;
    default:
      hue = (red - green) / delta + 4;
      break;
  }

  hue /= 6;
  return [hue * 360, saturation, lightness];
}

function hueDistanceDegrees(leftHue, rightHue) {
  const diff = Math.abs(leftHue - rightHue) % 360;
  return diff > 180 ? 360 - diff : diff;
}

function mixHueDegrees(leftHue, rightHue, amount) {
  const left = ((leftHue % 360) + 360) % 360;
  const right = ((rightHue % 360) + 360) % 360;
  const deltaRaw = ((right - left + 540) % 360) - 180;
  return (left + deltaRaw * clamp(amount, 0, 1) + 360) % 360;
}

function hueToRgb(p, q, t) {
  let value = t;
  if (value < 0) {
    value += 1;
  }
  if (value > 1) {
    value -= 1;
  }
  if (value < 1 / 6) {
    return p + (q - p) * 6 * value;
  }
  if (value < 1 / 2) {
    return q;
  }
  if (value < 2 / 3) {
    return p + (q - p) * (2 / 3 - value) * 6;
  }
  return p;
}

function hslToRgb(h, s, l) {
  const hue = ((h % 360) + 360) % 360 / 360;
  if (s === 0) {
    const gray = Math.round(l * 255);
    return [gray, gray, gray];
  }

  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [
    Math.round(hueToRgb(p, q, hue + 1 / 3) * 255),
    Math.round(hueToRgb(p, q, hue) * 255),
    Math.round(hueToRgb(p, q, hue - 1 / 3) * 255),
  ];
}

function rgbToCss(rgb) {
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

function rgbToCsv(rgb) {
  return `${rgb[0]}, ${rgb[1]}, ${rgb[2]}`;
}

function relativeLuminance(rgb) {
  const channels = rgb.map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.03928
      ? normalized / 12.92
      : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrastRatio(leftRgb, rightRgb) {
  const left = relativeLuminance(leftRgb);
  const right = relativeLuminance(rightRgb);
  const lighter = Math.max(left, right);
  const darker = Math.min(left, right);
  return (lighter + 0.05) / (darker + 0.05);
}

function pickAccessibleTextColor(backgroundRgb, options = {}) {
  const minRatio = options.minRatio || 4.5;
  const light = options.light || [247, 250, 252];
  const dark = options.dark || [27, 35, 42];
  const lightRatio = contrastRatio(backgroundRgb, light);
  const darkRatio = contrastRatio(backgroundRgb, dark);

  if (lightRatio >= minRatio && lightRatio >= darkRatio) {
    return light;
  }
  if (darkRatio >= minRatio) {
    return dark;
  }
  return lightRatio >= darkRatio ? light : dark;
}

function pickPrimaryAccentTextColor(accentRgb) {
  const white = [247, 250, 252];
  const dark = [24, 30, 36];
  const whiteContrast = contrastRatio(accentRgb, white);
  const darkContrast = contrastRatio(accentRgb, dark);

  // Favor dark text on very bright accents (like warm yellow), otherwise white.
  if (relativeLuminance(accentRgb) > 0.58 && darkContrast >= 3.8) {
    return dark;
  }
  if (whiteContrast >= 4.5 || whiteContrast >= darkContrast) {
    return white;
  }
  return darkContrast >= 3.8 ? dark : white;
}

function pickContrastColor(rgb) {
  return pickAccessibleTextColor(rgb, {
    minRatio: 4.5,
    light: [247, 250, 252],
    dark: [27, 35, 42],
  });
}

function buildDetailThemeFromRgb(sourceRgb) {
  return buildDetailThemeFromPalette({
    accentRgb: sourceRgb,
    mutedRgb: DEFAULT_DETAIL_THEME.bgAccent,
    dominantRgb: sourceRgb,
    imageLuminance: DEFAULT_DETAIL_THEME.imageLuminance,
    isDarkImage: DEFAULT_DETAIL_THEME.isDarkImage,
  });
}

function buildDetailThemeFromPalette({
  accentRgb,
  mutedRgb,
  dominantRgb,
  imageLuminance = DEFAULT_DETAIL_THEME.imageLuminance,
  isDarkImage = DEFAULT_DETAIL_THEME.isDarkImage,
}) {
  const dominantSource = dominantRgb || mutedRgb || accentRgb;
  const [dominantHue, dominantSatRaw, dominantLightRaw] = rgbToHsl(
    dominantSource[0],
    dominantSource[1],
    dominantSource[2],
  );

  const [accentHue, accentSatRaw, accentLightRaw] = rgbToHsl(accentRgb[0], accentRgb[1], accentRgb[2]);
  const accentSat = clamp(accentSatRaw + 0.12, 0.44, 0.88);
  const accentLight = clamp(accentLightRaw, 0.39, 0.6);
  const baseAccentHue = hueDistanceDegrees(accentHue, dominantHue) > 42
    ? mixHueDegrees(dominantHue, accentHue, 0.68)
    : accentHue;
  const primaryAccent = hslToRgb(baseAccentHue, accentSat, accentLight);
  const primaryAccentContrast = pickPrimaryAccentTextColor(primaryAccent);

  const [mutedHue, mutedSatRaw, mutedLightRaw] = rgbToHsl(mutedRgb[0], mutedRgb[1], mutedRgb[2]);
  const baseBgHue = hueDistanceDegrees(mutedHue, dominantHue) <= 28
    ? mutedHue
    : mixHueDegrees(dominantHue, mutedHue, 0.22);
  const bgSat = clamp((dominantSatRaw * 0.42) + (mutedSatRaw * 0.28) + 0.06, 0.1, 0.34);
  const bgLightTarget = isDarkImage
    ? clamp((imageLuminance * 0.75) + 0.08, 0.14, 0.2)
    : clamp((imageLuminance * 0.28) + 0.07, 0.15, 0.2);
  const uiSidebarBg = hslToRgb(baseBgHue, bgSat, bgLightTarget);
  const mutedContrast = pickContrastColor(uiSidebarBg);
  const darkSidebar = relativeLuminance(uiSidebarBg) < 0.4;
  const panelText = darkSidebar ? mutedContrast : [35, 43, 51];
  const panelMuted = darkSidebar ? mixRgb(panelText, uiSidebarBg, 0.34) : mixRgb(panelText, uiSidebarBg, 0.42);
  const panelCard = darkSidebar ? mixRgb(uiSidebarBg, panelText, 0.12) : mixRgb(uiSidebarBg, panelText, 0.72);
  const panelCardBorder = darkSidebar ? mixRgb(uiSidebarBg, panelText, 0.2) : mixRgb(uiSidebarBg, panelText, 0.14);
  const panelInput = darkSidebar ? mixRgb(uiSidebarBg, panelText, 0.08) : mixRgb(uiSidebarBg, panelText, 0.82);
  const badgeText = pickAccessibleTextColor(primaryAccent, {
    minRatio: 4.5,
    light: [247, 249, 251],
    dark: [32, 39, 46],
  });
  const [accentHueForSecondary, accentSatForSecondary, accentLightForSecondary] = rgbToHsl(
    primaryAccent[0],
    primaryAccent[1],
    primaryAccent[2],
  );
  const uiSecondaryBadge = hslToRgb(
    accentHueForSecondary,
    clamp(accentSatForSecondary * 0.22, 0.08, 0.22),
    clamp(accentLightForSecondary, 0.34, 0.58),
  );
  const uiSecondaryBadgeAlpha = 0.15;

  return {
    uiSidebarBg,
    uiPrimaryAccent: primaryAccent,
    uiPrimaryAccentContrast: primaryAccentContrast,
    uiSecondaryBadge,
    uiSecondaryBadgeAlpha,
    imageLuminance,
    isDarkImage,
    bgAccent: uiSidebarBg,
    primaryAccent,
    primaryAccentContrast,
    subtleAccentAlpha: uiSecondaryBadgeAlpha,
    accent: primaryAccent,
    accentSoft: primaryAccent,
    accentContrast: primaryAccentContrast,
    muted: uiSidebarBg,
    mutedContrast,
    panelText,
    panelMuted,
    panelCard,
    panelCardBorder,
    panelInput,
    badgeText,
  };
}

function applyDetailTheme(theme) {
  const panel = dom.detailModalPanel;
  if (!panel) {
    return;
  }
  const rootStyle = document.documentElement?.style;
  const setThemeVariable = (name, value) => {
    if (rootStyle) {
      rootStyle.setProperty(name, value);
    }
    panel.style.setProperty(name, value);
  };

  const uiSidebarBg = theme.uiSidebarBg || theme.bgAccent || theme.muted || DEFAULT_DETAIL_THEME.uiSidebarBg;
  const uiPrimaryAccent = theme.uiPrimaryAccent || theme.primaryAccent || theme.accent || DEFAULT_DETAIL_THEME.uiPrimaryAccent;
  const uiPrimaryAccentContrast = theme.uiPrimaryAccentContrast
    || theme.primaryAccentContrast
    || theme.accentContrast
    || DEFAULT_DETAIL_THEME.uiPrimaryAccentContrast;
  const uiSecondaryBadge = theme.uiSecondaryBadge || uiPrimaryAccent;
  const uiSecondaryBadgeAlpha = clamp(Number(theme.uiSecondaryBadgeAlpha ?? theme.subtleAccentAlpha ?? 0.15), 0.08, 0.3);
  const primaryAccentCsv = rgbToCsv(uiPrimaryAccent);
  const secondaryBadgeCsv = rgbToCsv(uiSecondaryBadge);
  const subtleAccent = `rgba(${secondaryBadgeCsv}, ${uiSecondaryBadgeAlpha})`;

  setThemeVariable('--ui-sidebar-bg', rgbToCss(uiSidebarBg));
  setThemeVariable('--ui-sidebar-bg-rgb', rgbToCsv(uiSidebarBg));
  setThemeVariable('--ui-primary-accent', rgbToCss(uiPrimaryAccent));
  setThemeVariable('--ui-primary-accent-rgb', primaryAccentCsv);
  setThemeVariable('--ui-primary-contrast', rgbToCss(uiPrimaryAccentContrast));
  setThemeVariable('--ui-primary-contrast-rgb', rgbToCsv(uiPrimaryAccentContrast));
  setThemeVariable('--ui-secondary-badge', subtleAccent);
  setThemeVariable('--ui-secondary-badge-rgb', secondaryBadgeCsv);

  setThemeVariable('--bg-accent', rgbToCss(uiSidebarBg));
  setThemeVariable('--bg-accent-rgb', rgbToCsv(uiSidebarBg));
  setThemeVariable('--primary-accent', rgbToCss(uiPrimaryAccent));
  setThemeVariable('--primary-accent-rgb', primaryAccentCsv);
  setThemeVariable('--primary-accent-contrast', rgbToCss(uiPrimaryAccentContrast));
  setThemeVariable('--primary-accent-contrast-rgb', rgbToCsv(uiPrimaryAccentContrast));
  setThemeVariable('--subtle-accent', subtleAccent);
  setThemeVariable('--subtle-accent-rgb', secondaryBadgeCsv);

  setThemeVariable('--asset-accent', rgbToCss(theme.accent));
  setThemeVariable('--asset-accent-rgb', rgbToCsv(theme.accent));
  setThemeVariable('--asset-accent-soft', rgbToCss(theme.accentSoft));
  setThemeVariable('--asset-accent-soft-rgb', rgbToCsv(theme.accentSoft));
  setThemeVariable('--asset-accent-contrast', rgbToCss(theme.accentContrast));
  setThemeVariable('--asset-accent-contrast-rgb', rgbToCsv(theme.accentContrast));
  setThemeVariable('--asset-muted', rgbToCss(uiSidebarBg));
  setThemeVariable('--asset-muted-rgb', rgbToCsv(uiSidebarBg));
  setThemeVariable('--asset-muted-contrast', rgbToCss(theme.mutedContrast));
  setThemeVariable('--asset-muted-contrast-rgb', rgbToCsv(theme.mutedContrast));
  setThemeVariable('--asset-panel-text', rgbToCss(theme.panelText));
  setThemeVariable('--asset-panel-text-rgb', rgbToCsv(theme.panelText));
  setThemeVariable('--asset-panel-muted', rgbToCss(theme.panelMuted));
  setThemeVariable('--asset-panel-card', rgbToCss(theme.panelCard));
  setThemeVariable('--asset-panel-card-rgb', rgbToCsv(theme.panelCard));
  setThemeVariable('--asset-panel-card-border', rgbToCss(theme.panelCardBorder));
  setThemeVariable('--asset-panel-card-border-rgb', rgbToCsv(theme.panelCardBorder));
  setThemeVariable('--asset-panel-input', rgbToCss(theme.panelInput));
  setThemeVariable('--asset-panel-input-rgb', rgbToCsv(theme.panelInput));
  setThemeVariable('--asset-badge-text', rgbToCss(theme.badgeText));
  setThemeVariable('--asset-badge-text-rgb', rgbToCsv(theme.badgeText));
}

function applyDetailThemeNextFrame(theme, token = null) {
  requestAnimationFrame(() => {
    if (typeof token === 'number' && token !== state.detailAccentToken) {
      return;
    }
    applyDetailTheme(theme);
  });
}

function extractPaletteFromImageElement(image) {
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) {
    throw new Error('Canvas unavailable');
  }

  // Performance optimization: sample from a tiny canvas.
  const sampleWidth = 16;
  const sampleHeight = 16;
  const safeNaturalWidth = Math.max(1, Number(image.naturalWidth || 1));
  const safeNaturalHeight = Math.max(1, Number(image.naturalHeight || 1));
  canvas.width = sampleWidth;
  canvas.height = sampleHeight;
  context.drawImage(image, 0, 0, safeNaturalWidth, safeNaturalHeight, 0, 0, sampleWidth, sampleHeight);

  const { data } = context.getImageData(0, 0, sampleWidth, sampleHeight);
  const brightBuckets = new Map();
  const darkBuckets = new Map();
  const dominantBuckets = new Map();
  let luminanceSum = 0;
  let luminanceWeight = 0;

  const accumulateBucket = (map, rgb, score) => {
    if (!Number.isFinite(score) || score <= 0) {
      return;
    }
    const key = [
      Math.round(rgb[0] / 24),
      Math.round(rgb[1] / 24),
      Math.round(rgb[2] / 24),
    ].join(':');
    const bucket = map.get(key) || { score: 0, red: 0, green: 0, blue: 0 };
    bucket.score += score;
    bucket.red += rgb[0] * score;
    bucket.green += rgb[1] * score;
    bucket.blue += rgb[2] * score;
    map.set(key, bucket);
  };

  for (let y = 0; y < sampleHeight; y += 1) {
    for (let x = 0; x < sampleWidth; x += 1) {
      const index = (y * sampleWidth + x) * 4;
      const alpha = data[index + 3];
      if (alpha < 170) {
        continue;
      }

      const rgb = [data[index], data[index + 1], data[index + 2]];
      const [hue, saturation, lightness] = rgbToHsl(rgb[0], rgb[1], rgb[2]);
      if (lightness < 0.06 || lightness > 0.96) {
        continue;
      }
      const lumaWeight = alpha / 255;
      luminanceSum += lightness * lumaWeight;
      luminanceWeight += lumaWeight;

      const topBias = 1 - y / Math.max(1, sampleHeight - 1);
      const bottomBias = y / Math.max(1, sampleHeight - 1);
      const centerDistance = Math.abs((x + 0.5) / sampleWidth - 0.5) * 2;
      const centerBias = 1 - clamp(centerDistance, 0, 1);

      // Bright/vibrant sampling with a slight top-center bias (sky/sun areas).
      const brightScore = clamp(
        saturation * 1.7
        + lightness * 1.5
        + topBias * 0.85
        + centerBias * 0.45,
        0,
        4,
      );
      if (lightness > 0.36 && saturation > 0.1) {
        accumulateBucket(brightBuckets, rgb, brightScore);
      }

      // Dark/muted sampling with slight lower-area bias (sea/shadows).
      const darkScore = clamp(
        (1 - lightness) * 2.1
        + (1 - saturation) * 0.6
        + bottomBias * 0.45
        + (Math.abs(hue - 210) < 50 ? 0.2 : 0),
        0,
        4,
      );
      if (lightness < 0.7) {
        accumulateBucket(darkBuckets, rgb, darkScore);
      }

      const dominantScore = clamp(
        1
        - Math.abs(lightness - 0.48) * 1.35
        + saturation * 0.55,
        0.05,
        2.2,
      );
      accumulateBucket(dominantBuckets, rgb, dominantScore);
    }
  }

  const pickTopBucket = (map) => {
    if (!map.size) {
      return null;
    }
    const ranked = Array.from(map.values()).sort((left, right) => right.score - left.score)[0];
    if (!ranked || ranked.score <= 0) {
      return null;
    }
    return [
      Math.round(ranked.red / ranked.score),
      Math.round(ranked.green / ranked.score),
      Math.round(ranked.blue / ranked.score),
    ];
  };

  const vibrantAccent = pickTopBucket(brightBuckets);
  const mutedDominant = pickTopBucket(darkBuckets);
  const fallbackDominant = pickTopBucket(dominantBuckets);

  const accentRgb = vibrantAccent || fallbackDominant || DEFAULT_DETAIL_THEME.primaryAccent;
  const mutedRgb = mutedDominant || fallbackDominant || DEFAULT_DETAIL_THEME.bgAccent;
  const dominantRgb = fallbackDominant || accentRgb || mutedRgb;
  const imageLuminance = luminanceWeight > 0
    ? clamp(luminanceSum / luminanceWeight, 0.08, 0.92)
    : DEFAULT_DETAIL_THEME.imageLuminance;
  const isDarkImage = imageLuminance < 0.48;

  const colors = { accentRgb, mutedRgb, dominantRgb, imageLuminance, isDarkImage };
  console.log('Extracted Colors:', colors);
  return colors;
}

function refreshDetailAccent(imageUrl) {
  state.detailAccentToken += 1;
  const token = state.detailAccentToken;
  // Immediately apply a neutral state while the new image palette is loading.
  applyDetailTheme(NEUTRAL_DETAIL_THEME);

  if (!imageUrl) {
    return;
  }

  // Use an off-screen image probe so the modal can theme itself from the asset
  // without blocking the main preview or breaking when cross-origin sampling fails.
  const probe = new Image();
  probe.crossOrigin = 'anonymous';
  probe.decoding = 'async';
  probe.referrerPolicy = 'no-referrer';

  const applyThemeIfCurrent = () => {
    if (token !== state.detailAccentToken) {
      return;
    }

    try {
      const palette = extractPaletteFromImageElement(probe);
      applyDetailThemeNextFrame(buildDetailThemeFromPalette(palette), token);
    } catch (_error) {
      applyDetailThemeNextFrame(NEUTRAL_DETAIL_THEME, token);
    }
  };

  probe.onload = applyThemeIfCurrent;
  probe.onerror = () => {
    if (token === state.detailAccentToken) {
      applyDetailThemeNextFrame(NEUTRAL_DETAIL_THEME, token);
    }
  };
  probe.src = imageUrl;

  if (probe.complete && probe.naturalWidth) {
    applyThemeIfCurrent();
  }
}

function setStatus(message, isError = false) {
  dom.statusText.textContent = message || '';
  dom.statusText.classList.toggle('state-error', Boolean(isError));
}

function setModalStatus(message, isError = false) {
  dom.modalStatusText.textContent = message || '';
  dom.modalStatusText.classList.toggle('state-error', Boolean(isError));
}

function resetDetailFormState() {
  state.detailForm.collectionValue = '';
  state.detailForm.favoriteCategoryValue = '';
  state.detailForm.savingCollection = false;
  state.detailForm.savingFavoriteCategory = false;
}

function hasSelectOption(selectElement, value) {
  if (!(selectElement instanceof HTMLSelectElement)) {
    return false;
  }
  return Array.from(selectElement.options).some((option) => option.value === value);
}

function syncDetailControlledSelects() {
  const hasActiveTask = Boolean(state.activeTask);

  if (dom.collectionAssignSelect instanceof HTMLSelectElement) {
    const desiredCollectionValue = hasActiveTask ? String(state.detailForm.collectionValue || '') : '';
    const safeCollectionValue = hasSelectOption(dom.collectionAssignSelect, desiredCollectionValue) ? desiredCollectionValue : '';
    if (dom.collectionAssignSelect.value !== safeCollectionValue) {
      dom.collectionAssignSelect.value = safeCollectionValue;
    }
    dom.collectionAssignSelect.disabled = !hasActiveTask || state.detailForm.savingCollection;
    dom.collectionAssignSelect.classList.toggle('is-saving', state.detailForm.savingCollection);
  }

  if (dom.favoriteCategoryAssignSelect instanceof HTMLSelectElement) {
    const desiredCategoryValue = hasActiveTask ? String(state.detailForm.favoriteCategoryValue || '') : '';
    const safeCategoryValue = hasSelectOption(dom.favoriteCategoryAssignSelect, desiredCategoryValue) ? desiredCategoryValue : '';
    if (dom.favoriteCategoryAssignSelect.value !== safeCategoryValue) {
      dom.favoriteCategoryAssignSelect.value = safeCategoryValue;
    }
    dom.favoriteCategoryAssignSelect.disabled = !hasActiveTask || state.detailForm.savingFavoriteCategory;
    dom.favoriteCategoryAssignSelect.classList.toggle('is-saving', state.detailForm.savingFavoriteCategory);
  }
}

function selectorEscape(value) {
  const text = String(value || '');
  if (window.CSS && typeof window.CSS.escape === 'function') {
    return window.CSS.escape(text);
  }
  return text.replace(/["\\]/g, '\\$&');
}

function setButtonBusy(button, isBusy, busyText = 'Deleting...') {
  if (!(button instanceof HTMLElement)) {
    return;
  }
  const iconOnlyButton = button.classList.contains('asset-delete-trigger')
    || button.classList.contains('project-delete-trigger')
    || button.classList.contains('detail-quick-btn');
  if (isBusy) {
    if (!iconOnlyButton && !button.dataset.originalText) {
      button.dataset.originalText = button.textContent || '';
    }
    if (!iconOnlyButton) {
      button.textContent = busyText;
    }
    button.disabled = true;
    button.classList.add('is-busy');
    return;
  }
  if (!iconOnlyButton && button.dataset.originalText !== undefined) {
    button.textContent = button.dataset.originalText;
    delete button.dataset.originalText;
  }
  button.disabled = false;
  button.classList.remove('is-busy');
}

function closeConfirmDialog(confirmed = false) {
  dom.confirmModal.classList.add('hidden');
  const action = state.confirmAction;
  state.confirmAction = null;
  if (action && typeof action.resolve === 'function') {
    action.resolve(Boolean(confirmed));
  }
}

function openConfirmDialog({ title, message, confirmText = 'Delete' }) {
  if (state.confirmAction) {
    closeConfirmDialog(false);
  }

  dom.confirmTitle.textContent = title || 'Confirm action';
  dom.confirmMessage.textContent = message || 'This action cannot be undone.';
  dom.confirmDeleteBtn.textContent = confirmText;
  dom.confirmDeleteBtn.disabled = false;
  dom.confirmModal.classList.remove('hidden');

  return new Promise((resolve) => {
    state.confirmAction = { resolve };
  });
}

function fadeOutTaskCard(taskId) {
  const escapedTaskId = selectorEscape(taskId);
  const card = document.querySelector(`.asset-card[data-task-id="${escapedTaskId}"]`);
  if (!card) {
    return;
  }
  card.classList.add('is-removing');
  window.setTimeout(() => {
    card.remove();
  }, 220);
}

function removeUndoToast(taskId) {
  const key = String(taskId || '');
  const toastState = state.pendingDeleteToasts.get(key);
  if (!toastState) {
    return;
  }
  if (toastState.timer) {
    clearTimeout(toastState.timer);
  }
  if (toastState.element && toastState.element.parentNode) {
    toastState.element.remove();
  }
  state.pendingDeleteToasts.delete(key);
}

async function finalizeAssetDelete(taskId) {
  try {
    await apiRequest(`/api/history/${encodeURIComponent(taskId)}/delete`, {
      method: 'DELETE',
      body: JSON.stringify({}),
    });
  } catch (error) {
    if (!String(error.message || '').includes('NOT_FOUND')) {
      throw error;
    }
  }
}

function scheduleUndoToast(taskId, undoWindowMs) {
  const key = String(taskId || '');
  removeUndoToast(key);

  const toast = createElement('div', 'toast toast-danger');
  const text = createElement('div', 'toast-text');
  text.textContent = 'Asset deleted. Undo?';
  const actions = createElement('div', 'toast-actions');
  const undoBtn = createElement('button', 'btn btn-ghost toast-btn');
  undoBtn.type = 'button';
  undoBtn.textContent = 'Undo';
  actions.appendChild(undoBtn);
  toast.appendChild(text);
  toast.appendChild(actions);
  dom.toastHost.appendChild(toast);

  let resolved = false;
  const safeUndoWindow = Math.max(1200, Number(undoWindowMs || 5000));

  const finalizeTimer = window.setTimeout(async () => {
    if (resolved) {
      return;
    }
    resolved = true;
    try {
      await finalizeAssetDelete(key);
      setStatus('Asset deleted permanently.');
      await loadHistory();
    } catch (error) {
      setStatus(`Failed to finalize deletion: ${error.message}`, true);
    } finally {
      removeUndoToast(key);
    }
  }, safeUndoWindow);

  state.pendingDeleteToasts.set(key, { element: toast, timer: finalizeTimer });

  undoBtn.addEventListener('click', async () => {
    if (resolved) {
      return;
    }
    resolved = true;
    undoBtn.disabled = true;
    undoBtn.textContent = 'Undoing...';
    try {
      await apiRequest(`/api/history/${encodeURIComponent(key)}/delete/undo`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      setStatus('Deletion undone.');
      await loadHistory();
    } catch (error) {
      setStatus(`Undo failed: ${error.message}`, true);
    } finally {
      removeUndoToast(key);
    }
  });
}

async function requestAssetDelete(item, { sourceButton = null } = {}) {
  if (!item || !item.task_id) {
    return;
  }

  const confirmed = await openConfirmDialog({
    title: 'Delete this asset permanently?',
    message: 'This will remove the image from history and cleanup stored files. This cannot be undone after the undo window.',
    confirmText: 'Delete Asset',
  });
  if (!confirmed) {
    return;
  }

  setButtonBusy(sourceButton, true, 'Deleting...');
  setButtonBusy(dom.deleteAssetBtn, true, 'Deleting...');

  try {
    const payload = await apiRequest(`/api/history/${encodeURIComponent(item.task_id)}/delete`, {
      method: 'POST',
      body: JSON.stringify({}),
    });

    state.selectedTaskIds.delete(item.task_id);
    updateSelectionBar();
    fadeOutTaskCard(item.task_id);
    if (state.activeTask?.task_id === item.task_id) {
      closeDetail();
    }

    setStatus('Asset removed. Undo is available for a few seconds.');
    scheduleUndoToast(item.task_id, payload.undo_window_ms);
    window.setTimeout(() => {
      loadHistory().catch((error) => {
        setStatus(`Failed to refresh history: ${error.message}`, true);
      });
    }, 220);
  } catch (error) {
    setStatus(`Delete failed: ${error.message}`, true);
  } finally {
    setButtonBusy(sourceButton, false);
    setButtonBusy(dom.deleteAssetBtn, false);
  }
}

async function requestProjectDelete(collection, { sourceButton = null } = {}) {
  if (!collection || !collection.id) {
    return;
  }

  const confirmed = await openConfirmDialog({
    title: `Delete project "${collection.name}"?`,
    message: 'This will delete the project and all assets inside it. This cannot be undone.',
    confirmText: 'Delete Project & All Contents',
  });
  if (!confirmed) {
    return;
  }

  setButtonBusy(sourceButton, true, 'Deleting...');
  try {
    const payload = await apiRequest(`/api/collections/${encodeURIComponent(collection.id)}`, {
      method: 'DELETE',
      body: JSON.stringify({
        confirm: 'DELETE_PROJECT',
        delete_contents: true,
      }),
    });

    if (Number(state.filters.collectionId) === Number(collection.id)) {
      state.filters.collectionId = null;
      syncControlsFromState();
    }
    state.selectedTaskIds.clear();
    updateSelectionBar();
    setStatus(`Project deleted. Removed ${Number(payload.deleted_asset_count || 0)} asset(s).`);
    await loadHistory();
  } catch (error) {
    setStatus(`Failed to delete project: ${error.message}`, true);
  } finally {
    setButtonBusy(sourceButton, false);
  }
}

function showLogin(errorMessage = '') {
  dom.appView.classList.add('hidden');
  dom.loginView.classList.remove('hidden');
  dom.loginError.textContent = errorMessage;
  for (const taskId of Array.from(state.pendingDeleteToasts.keys())) {
    removeUndoToast(taskId);
  }
}

function showApp() {
  dom.loginView.classList.add('hidden');
  dom.appView.classList.remove('hidden');
}

async function apiRequest(url, options = {}, allowSessionMismatchRetry = true) {
  const ssoHeaders = {};
  if (state.sso) {
    ssoHeaders['X-Momi-SSO-Email'] = state.sso.email;
    ssoHeaders['X-Momi-SSO-Exp'] = state.sso.exp;
    ssoHeaders['X-Momi-SSO-Nonce'] = state.sso.nonce;
    ssoHeaders['X-Momi-SSO-Sig'] = state.sso.sig;
  }

  const response = await fetch(withPortalPrefix(url), {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...ssoHeaders,
      ...(options.headers || {}),
    },
    ...options,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }

  if (response.status === 401) {
    if (
      allowSessionMismatchRetry &&
      state.sso &&
      payload?.error === 'SESSION_SSO_MISMATCH'
    ) {
      console.warn('[history_portal] Session mismatch detected. Retrying request with SSO context.');
      return apiRequest(url, options, false);
    }
    state.user = null;
    showLogin('Session expired. Please sign in again.');
    throw new Error('AUTH_REQUIRED');
  }

  if (!response.ok) {
    const serverMessage = payload?.message || payload?.error || `Request failed: ${response.status}`;
    throw new Error(serverMessage);
  }

  return payload || {};
}

function buildHistoryQuery() {
  const params = new URLSearchParams();
  params.set('page', String(state.filters.page));
  params.set('page_size', String(state.filters.pageSize));
  params.set('scope', state.filters.scope);
  params.set('sort', state.filters.sort);
  params.set('date_preset', state.filters.datePreset);
  params.set('hide_folder_contents', state.filters.hideFolderContents ? '1' : '0');
  params.set('favorites_only', state.filters.favoritesOnly ? '1' : '0');

  if (state.filters.search) {
    params.set('search', state.filters.search);
  }
  if (state.filters.workflowCategory) {
    params.set('workflow_category', state.filters.workflowCategory);
  }
  if (state.filters.status) {
    params.set('status', state.filters.status);
  }
  if (state.filters.favoriteCategory) {
    params.set('favorite_category', state.filters.favoriteCategory);
  }
  if (state.filters.dateFrom) {
    params.set('date_from', state.filters.dateFrom);
  }
  if (state.filters.dateTo) {
    params.set('date_to', state.filters.dateTo);
  }
  if (state.filters.collectionId) {
    params.set('collection_id', String(state.filters.collectionId));
  }

  return params.toString();
}

function syncControlsFromState() {
  dom.scopeSelect.value = state.filters.scope;
  dom.typeSelect.value = state.filters.workflowCategory || '__all__';
  dom.datePresetSelect.value = state.filters.datePreset;
  dom.favoritesCheckbox.checked = state.filters.favoritesOnly;
  dom.hideFolderContentCheckbox.checked = state.filters.hideFolderContents;
  dom.sortSelect.value = state.filters.sort;
  dom.viewModeSelect.value = state.filters.viewMode;
  dom.searchInput.value = state.filters.search;
  dom.statusSelect.value = state.filters.status || '__all__';
  dom.favoriteCategoryFilterSelect.value = state.filters.favoriteCategory || '__all__';
  dom.dateFromInput.value = state.filters.dateFrom;
  dom.dateToInput.value = state.filters.dateTo;
  dom.pageSizeSelect.value = String(state.filters.pageSize);
}

function updateUserHeader() {
  const user = state.user;
  if (!user) {
    dom.userName.textContent = '-';
    dom.userEmail.textContent = '-';
    dom.userAvatar.src = '';
    return;
  }
  dom.userName.textContent = user.displayName || user.email;
  dom.userEmail.textContent = user.email;
  dom.userAvatar.src = withPortalPrefix(user.avatarUrl || '/avatars/default_avatar.png');
}

function renderFacetSelects() {
  const workflowOptions = ['<option value="__all__">All types</option>'];
  for (const facet of state.workflowCategoryFacets) {
    const name = facet.workflow_category || 'Uncategorized';
    workflowOptions.push(
      `<option value="${escapeHtml(name)}">${escapeHtml(name)} (${formatCount(facet.total)})</option>`,
    );
  }
  dom.typeSelect.innerHTML = workflowOptions.join('');

  const statusOptions = ['<option value="__all__">All statuses</option>'];
  for (const facet of state.statusFacets) {
    const key = String(facet.status || 'unknown');
    statusOptions.push(`<option value="${escapeHtml(key)}">${escapeHtml(key)} (${formatCount(facet.total)})</option>`);
  }
  dom.statusSelect.innerHTML = statusOptions.join('');

  const favoriteCategoryOptions = [
    '<option value="__all__">All categories</option>',
    '<option value="__none__">No category</option>',
  ];
  for (const category of state.favoriteCategories) {
    const key = category.category_key || '';
    const label = category.display_name || key;
    favoriteCategoryOptions.push(`<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`);
  }

  dom.favoriteCategoryFilterSelect.innerHTML = favoriteCategoryOptions.join('');
  dom.favoriteCategoryAssignSelect.innerHTML = ['<option value="">No category</option>', ...favoriteCategoryOptions.slice(2)].join('');

  syncControlsFromState();
  syncDetailControlledSelects();
}

function renderCollectionSelectors() {
  const baseOptions = ['<option value="">No project</option>'];
  for (const collection of state.collections) {
    baseOptions.push(`<option value="${collection.id}">${escapeHtml(collection.name)} (${formatCount(collection.item_count)})</option>`);
  }
  dom.collectionAssignSelect.innerHTML = baseOptions.join('');
  dom.bulkCollectionSelect.innerHTML = ['<option value="">Unassign from project</option>', ...baseOptions.slice(1)].join('');
  syncDetailControlledSelects();
}

function renderSummary() {
  const pills = [
    `Results: ${formatCount(state.pagination.totalItems)}`,
    `Favorites: ${formatCount(state.favoritesTotal)}`,
    `Page size: ${formatCount(state.pagination.pageSize)}`,
  ];

  if (state.filters.collectionId) {
    const active = state.collections.find((item) => Number(item.id) === Number(state.filters.collectionId));
    pills.push(`Project: ${active ? active.name : state.filters.collectionId}`);
  }
  if (state.filters.workflowCategory) {
    pills.push(`Type: ${state.filters.workflowCategory}`);
  }
  if (state.filters.scope !== 'all') {
    pills.push(`Scope: ${state.filters.scope}`);
  }

  if (state.thumbnailStorage && Number(state.thumbnailStorage.cap_bytes || 0) > 0) {
    const used = formatStorageGb(state.thumbnailStorage.total_bytes || 0);
    const cap = formatStorageGb(state.thumbnailStorage.cap_bytes || 0);
    const thumbCount = formatCount(state.thumbnailStorage.thumbnail_file_count || 0);
    const previewCount = formatCount(state.thumbnailStorage.preview_file_count || 0);
    pills.push(`Cache: ${used} GB / ${cap} GB (${thumbCount} thumbs, ${previewCount} previews)`);
    if (state.thumbnailStorage.over_warning) {
      const warnAt = formatStorageGb(state.thumbnailStorage.warning_bytes || 0);
      pills.push(`⚠ Cache warning (${warnAt} GB)`);
    }
  }

  dom.summaryPills.innerHTML = pills.map((text) => `<span class="pill">${escapeHtml(text)}</span>`).join('');
}

function createElement(tag, className = '') {
  const element = document.createElement(tag);
  if (className) {
    element.className = className;
  }
  return element;
}

function loadProjectsCollapsedPreference() {
  try {
    return window.localStorage.getItem(PROJECTS_COLLAPSED_STORAGE_KEY) === '1';
  } catch (_error) {
    return false;
  }
}

function saveProjectsCollapsedPreference(value) {
  try {
    window.localStorage.setItem(PROJECTS_COLLAPSED_STORAGE_KEY, value ? '1' : '0');
  } catch (_error) {
    // no-op
  }
}

function applyProjectsCollapseState() {
  const collapsed = Boolean(state.projectsCollapsed);
  dom.projectSection?.classList.toggle('is-collapsed', collapsed);
  if (dom.toggleProjectsBtn) {
    dom.toggleProjectsBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    dom.toggleProjectsBtn.setAttribute(
      'aria-label',
      collapsed ? 'Expand project section' : 'Collapse project section',
    );
    dom.toggleProjectsBtn.title = collapsed ? 'Expand projects' : 'Collapse projects';
  }
}

function createFolderCreateCard() {
  const card = createElement('button', 'folder-card folder-card-create');
  card.type = 'button';
  card.innerHTML = `
    <div class="folder-preview folder-create">
      <div>
        <div class="plus">+</div>
        <div class="create-text">Create New Project</div>
      </div>
    </div>
    <div class="folder-meta">
      <p class="folder-title">Create Project</p>
      <span class="folder-count"></span>
    </div>
  `;

  card.addEventListener('click', async () => {
    const name = window.prompt('Project name');
    if (!name || !name.trim()) {
      return;
    }

    try {
      await apiRequest('/api/collections', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim() }),
      });
      setStatus('Project created.');
      await loadHistory();
    } catch (error) {
      setStatus(`Failed to create project: ${error.message}`, true);
    }
  });

  return card;
}

function createFolderEmptyCard() {
  const card = createElement('div', 'folder-card folder-card-empty');
  card.innerHTML = `
    <div class="folder-preview folder-empty">No content yet</div>
    <div class="folder-meta">
      <p class="folder-title">Empty Project</p>
      <span class="folder-count">0</span>
    </div>
  `;
  return card;
}

function createCollectionCard(collection) {
  const button = createElement('article', 'folder-card');
  button.tabIndex = 0;
  button.setAttribute('role', 'button');
  button.dataset.collectionId = String(collection.id);
  button.draggable = true;
  button.classList.add('is-draggable-project');

  if (Number(state.filters.collectionId) === Number(collection.id)) {
    button.classList.add('active');
  }

  const preview = createElement('div', 'folder-preview');
  if (Array.isArray(collection.preview_urls) && collection.preview_urls.length) {
    for (let i = 0; i < 4; i += 1) {
      const image = document.createElement('img');
      image.loading = 'lazy';
      image.alt = `${collection.name} preview ${i + 1}`;
      image.src = resolveDisplayImageUrl(
        collection.preview_urls[i] || collection.preview_urls[collection.preview_urls.length - 1],
      );
      preview.appendChild(image);
    }
  } else {
    preview.classList.add('folder-empty');
    preview.textContent = 'No content yet';
  }

  const meta = createElement('div', 'folder-meta');
  const title = createElement('p', 'folder-title');
  title.textContent = collection.name;
  const count = createElement('span', 'folder-count');
  count.textContent = formatCount(collection.item_count);

  meta.appendChild(title);
  meta.appendChild(count);
  button.appendChild(preview);
  button.appendChild(meta);

  const deleteTrigger = createElement('button', 'project-delete-trigger');
  deleteTrigger.type = 'button';
  deleteTrigger.title = `Delete project ${collection.name}`;
  deleteTrigger.setAttribute('aria-label', `Delete project ${collection.name}`);
  deleteTrigger.innerHTML = `
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 7h16"></path>
      <path d="M9 7V5h6v2"></path>
      <path d="M8 7l1 12h6l1-12"></path>
      <path d="M10 11v6"></path>
      <path d="M14 11v6"></path>
    </svg>
  `;
  deleteTrigger.addEventListener('click', async (event) => {
    event.preventDefault();
    event.stopPropagation();
    await requestProjectDelete(collection, { sourceButton: deleteTrigger });
  });
  button.appendChild(deleteTrigger);

  button.addEventListener('click', () => {
    if (Number(state.filters.collectionId) === Number(collection.id)) {
      state.filters.collectionId = null;
    } else {
      state.filters.collectionId = Number(collection.id);
      state.filters.scope = 'all';
    }

    state.filters.page = 1;
    syncControlsFromState();
    loadHistory();
  });

  button.addEventListener('dragstart', (event) => {
    state.draggedCollectionId = Number(collection.id);
    button.classList.add('dragging');
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(collection.id));
    }
  });

  button.addEventListener('dragover', (event) => {
    if (!state.draggedCollectionId || state.draggedCollectionId === Number(collection.id)) {
      return;
    }
    event.preventDefault();
    button.classList.add('drag-over');
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'move';
    }
  });

  button.addEventListener('dragleave', () => {
    button.classList.remove('drag-over');
  });

  button.addEventListener('drop', async (event) => {
    event.preventDefault();
    const draggedId = Number(state.draggedCollectionId || 0);
    const targetId = Number(collection.id);
    clearProjectDragState();
    if (!draggedId || !targetId || draggedId === targetId) {
      return;
    }
    await reorderCollections(draggedId, targetId);
  });

  button.addEventListener('dragend', () => {
    clearProjectDragState();
  });

  button.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return;
    }
    event.preventDefault();
    button.click();
  });

  return button;
}

function clearProjectDragState() {
  state.draggedCollectionId = null;
  for (const card of document.querySelectorAll('.folder-card.is-draggable-project')) {
    card.classList.remove('dragging', 'drag-over');
  }
}

async function persistCollectionOrder(orderedCollectionIds) {
  const payload = await apiRequest('/api/collections/reorder', {
    method: 'POST',
    body: JSON.stringify({ ordered_collection_ids: orderedCollectionIds }),
  });
  if (Array.isArray(payload.collections)) {
    state.collections = payload.collections;
  }
}

async function reorderCollections(draggedCollectionId, targetCollectionId) {
  const fromIndex = state.collections.findIndex((item) => Number(item.id) === Number(draggedCollectionId));
  const toIndex = state.collections.findIndex((item) => Number(item.id) === Number(targetCollectionId));
  if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) {
    return;
  }

  const previous = state.collections.slice();
  const reordered = state.collections.slice();
  const [moved] = reordered.splice(fromIndex, 1);
  reordered.splice(toIndex, 0, moved);

  state.collections = reordered;
  renderFolderGrid();
  renderCollectionSelectors();

  try {
    await persistCollectionOrder(reordered.map((item) => Number(item.id)));
    renderFolderGrid();
    renderCollectionSelectors();
  } catch (error) {
    state.collections = previous;
    renderFolderGrid();
    renderCollectionSelectors();
    setStatus(`Failed to save project order: ${error.message}`, true);
  }
}

function renderFolderGrid() {
  const cards = [createFolderCreateCard()];

  if (!state.collections.length) {
    cards.push(createFolderEmptyCard());
  } else {
    for (const collection of state.collections) {
      cards.push(createCollectionCard(collection));
    }
  }

  dom.folderGrid.replaceChildren(...cards);
  dom.folderCount.textContent = `(${state.collections.length})`;
  applyProjectsCollapseState();
  renderCollectionSelectors();
}

function statusBadgeClass(status) {
  const value = String(status || '').toLowerCase();
  if (value.includes('complete') || value.includes('success')) {
    return 'good';
  }
  if (value.includes('fail') || value.includes('error') || value.includes('cancel') || value.includes('timeout')) {
    return 'bad';
  }
  return '';
}

function normalizeStatusKey(status, options = {}) {
  const preferArchived = Boolean(options.preferArchived);
  const value = String(status || '').trim().toLowerCase();
  if (value.includes('cancel')) {
    return 'canceled';
  }
  if (value.includes('fail') || value.includes('error') || value.includes('timeout')) {
    return 'failed';
  }
  if (preferArchived) {
    return 'archived';
  }
  return 'unavailable';
}

function statusFallbackLabel(statusKey) {
  if (statusKey === 'canceled') {
    return 'Task canceled';
  }
  if (statusKey === 'failed') {
    return 'Task failed';
  }
  if (statusKey === 'archived') {
    return 'Preview archived';
  }
  return 'Preview unavailable';
}

function statusFallbackIconSvg(statusKey) {
  if (statusKey === 'canceled') {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="8"></circle>
        <path d="M8.5 8.5l7 7"></path>
        <path d="M15.5 8.5l-7 7"></path>
      </svg>
    `;
  }
  if (statusKey === 'failed') {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3l9 16H3z"></path>
        <path d="M12 9v5"></path>
        <path d="M12 17h.01"></path>
      </svg>
    `;
  }
  if (statusKey === 'archived') {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 7h16l-1.4 12.5a2 2 0 0 1-2 1.5H7.4a2 2 0 0 1-2-1.5L4 7z"></path>
        <path d="M9 11h6"></path>
        <path d="M9 15h6"></path>
        <path d="M8 4h8"></path>
      </svg>
    `;
  }
  return `
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="5" width="16" height="14" rx="2"></rect>
      <path d="M9 10l2 2 4-4"></path>
      <path d="M4 19l6-6"></path>
      <path d="M14 13l6 6"></path>
    </svg>
  `;
}

function createStatusFallback(status) {
  const statusKey = normalizeStatusKey(status, { preferArchived: true });
  const fallback = createElement('div', `asset-fallback asset-fallback-${statusKey}`);
  fallback.setAttribute('aria-hidden', 'true');
  fallback.innerHTML = `
    <div class="asset-fallback-icon">${statusFallbackIconSvg(statusKey)}</div>
    <div class="asset-fallback-label">${escapeHtml(statusFallbackLabel(statusKey))}</div>
  `;
  return fallback;
}

function renderDetailPreviewFallback(status, message) {
  if (!dom.detailPreviewFallback) {
    return;
  }
  const statusKey = normalizeStatusKey(status, { preferArchived: true });
  const label = String(message || statusFallbackLabel(statusKey) || 'Preview unavailable');
  dom.detailPreviewFallback.className = `detail-preview-fallback detail-preview-fallback-${statusKey}`;
  dom.detailPreviewFallback.innerHTML = `
    <div class="detail-preview-fallback-icon" aria-hidden="true">${statusFallbackIconSvg(statusKey)}</div>
    <div class="detail-preview-fallback-label">${escapeHtml(label)}</div>
  `;
  dom.detailPreviewFallback.classList.remove('hidden');
}

function hideDetailPreviewFallback() {
  if (!dom.detailPreviewFallback) {
    return;
  }
  dom.detailPreviewFallback.classList.add('hidden');
  dom.detailPreviewFallback.innerHTML = '';
}

function updateSelectionBar() {
  const show = state.selectedTaskIds.size > 0;
  dom.selectionBar.classList.toggle('hidden', !show);
  dom.selectionCount.textContent = `${state.selectedTaskIds.size} selected`;
}

function toggleSelection(taskId, checked) {
  if (checked) {
    state.selectedTaskIds.add(taskId);
  } else {
    state.selectedTaskIds.delete(taskId);
  }
  updateSelectionBar();
}

function getCardTitle(item) {
  return item.output_filename || item.workflow_name || item.task_id;
}

function createAssetCard(item) {
  const card = createElement('article', 'asset-card');
  card.dataset.taskId = item.task_id;
  const isSelected = state.selectedTaskIds.has(item.task_id);
  if (isSelected) {
    card.classList.add('selected');
  }

  const imageWrap = createElement('div', 'asset-image-wrap');
  const image = document.createElement('img');
  image.className = 'asset-image';
  image.loading = 'lazy';
  image.alt = item.workflow_name || 'history image';
  const fallback = createStatusFallback(item.status);
  imageWrap.appendChild(image);
  imageWrap.appendChild(fallback);
  card.appendChild(imageWrap);

  // Grid cards are local-cache only: optimized preview first, then thumbnail fallback.
  const previewSrc = resolveDisplayImageUrl(item.preview_url || '');
  const thumbSrc = resolveDisplayImageUrl(item.thumbnail_url || '');
  const primarySrc = previewSrc || thumbSrc;
  const secondarySrc = previewSrc && thumbSrc && previewSrc !== thumbSrc ? thumbSrc : '';
  let attemptedSecondary = false;

  const showFallback = () => {
    imageWrap.classList.add('is-fallback');
  };

  const hideFallback = () => {
    imageWrap.classList.remove('is-fallback');
  };

  image.addEventListener('load', () => {
    hideFallback();
  });

  image.addEventListener('error', () => {
    if (!attemptedSecondary && secondarySrc && image.src !== secondarySrc) {
      attemptedSecondary = true;
      image.src = secondarySrc;
      return;
    }
    showFallback();
  });

  if (primarySrc) {
    image.src = primarySrc;
  } else {
    showFallback();
  }

  const overlay = createElement('div', 'asset-overlay');
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'asset-check';
  checkbox.checked = isSelected;
  checkbox.title = 'Select item';
  checkbox.addEventListener('click', (event) => {
    event.stopPropagation();
    toggleSelection(item.task_id, checkbox.checked);
    card.classList.toggle('selected', checkbox.checked);
  });
  overlay.appendChild(checkbox);

  const rightActions = createElement('div', 'asset-overlay-actions');
  const badge = createElement('span', `asset-badge ${statusBadgeClass(item.status)}`.trim());
  badge.textContent = item.status || 'unknown';
  rightActions.appendChild(badge);

  const deleteTrigger = createElement('button', 'asset-delete-trigger');
  deleteTrigger.type = 'button';
  deleteTrigger.title = 'Delete asset';
  deleteTrigger.setAttribute('aria-label', 'Delete asset');
  deleteTrigger.innerHTML = `
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 7h16"></path>
      <path d="M9 7V5h6v2"></path>
      <path d="M8 7l1 12h6l1-12"></path>
      <path d="M10 11v6"></path>
      <path d="M14 11v6"></path>
    </svg>
  `;
  deleteTrigger.addEventListener('click', async (event) => {
    event.preventDefault();
    event.stopPropagation();
    await requestAssetDelete(item, { sourceButton: deleteTrigger });
  });
  rightActions.appendChild(deleteTrigger);
  overlay.appendChild(rightActions);
  card.appendChild(overlay);

  const info = createElement('div', 'asset-info');
  const titleRow = createElement('div', 'asset-title-row');
  const title = createElement('h3', 'asset-title');
  title.textContent = truncate(getCardTitle(item), 36);
  titleRow.appendChild(title);

  const favorite = createElement('button', 'asset-fav');
  favorite.type = 'button';
  favorite.title = item.is_favorite ? 'Remove favorite' : 'Add favorite';
  favorite.textContent = item.is_favorite ? '★' : '☆';
  favorite.addEventListener('click', async (event) => {
    event.stopPropagation();
    await setFavorite(item, !item.is_favorite, item.favorite_category_key);
  });
  titleRow.appendChild(favorite);
  info.appendChild(titleRow);

  const meta = createElement('div', 'asset-meta');
  const workflowDisplay = createElement('span', 'asset-workflow-name');
  workflowDisplay.textContent = formatWorkflowDisplayName(item.workflow_name || 'Unknown');
  workflowDisplay.title = workflowDisplay.textContent;

  const metaDate = createElement('span', 'asset-meta-date');
  const dateParts = formatCardDateParts(item.created_at);

  const dateLine = createElement('span', 'asset-date-line');
  dateLine.textContent = dateParts.date;
  const timeLine = createElement('span', 'asset-time-line');
  timeLine.textContent = dateParts.time || '';

  metaDate.appendChild(dateLine);
  if (dateParts.time) {
    metaDate.appendChild(timeLine);
  }

  meta.appendChild(workflowDisplay);
  meta.appendChild(metaDate);
  info.appendChild(meta);
  card.appendChild(info);

  card.addEventListener('click', () => {
    openDetail(item.task_id);
  });

  return card;
}

function groupItems(items) {
  if (state.filters.viewMode === 'workflow_grouped') {
    const map = new Map();
    for (const item of items) {
      const key = formatWorkflowDisplayName(item.workflow_name || 'Unknown workflow');
      if (!map.has(key)) {
        map.set(key, []);
      }
      map.get(key).push(item);
    }
    return Array.from(map.entries()).map(([title, rows]) => ({ title, rows }));
  }

  if (state.filters.viewMode === 'collection_grouped') {
    const map = new Map();
    for (const item of items) {
      const key = item.collection_name || 'Uncategorized';
      if (!map.has(key)) {
        map.set(key, []);
      }
      map.get(key).push(item);
    }
    return Array.from(map.entries()).map(([title, rows]) => ({ title, rows }));
  }

  return [{ title: '', rows: items }];
}

function renderGroupedAssets() {
  const items = state.items || [];
  dom.groupedContainer.innerHTML = '';

  if (!items.length) {
    dom.emptyState.classList.remove('hidden');
    dom.emptyState.innerHTML = `
      <h3>No creations found</h3>
      <p>Try changing filters, scope, or date range. You can also clear filters and refresh.</p>
    `;
    return;
  }

  dom.emptyState.classList.add('hidden');
  const groups = groupItems(items);

  for (const group of groups) {
    const block = createElement('section', 'group-block');
    if (group.title) {
      const title = createElement('div', 'group-title');
      title.textContent = `${group.title} (${group.rows.length})`;
      block.appendChild(title);
    }

    const grid = createElement('div', 'asset-grid');
    for (const item of group.rows) {
      grid.appendChild(createAssetCard(item));
    }
    block.appendChild(grid);
    dom.groupedContainer.appendChild(block);
  }
}

function renderPagination() {
  const { page, totalPages } = state.pagination;
  dom.pageText.textContent = `Page ${page} / ${totalPages}`;
  dom.prevBtn.disabled = page <= 1;
  dom.nextBtn.disabled = page >= totalPages;
}

function renderAssetsMeta() {
  dom.assetsMeta.textContent = `${formatCount(state.pagination.totalItems)} assets`;

  if (state.filters.scope === 'uncategorized' || state.filters.hideFolderContents) {
    dom.assetsTitle.textContent = 'Uncategorized Creations';
  } else if (state.filters.scope === 'favorites') {
    dom.assetsTitle.textContent = 'Favorite Creations';
  } else if (state.filters.collectionId) {
    const collection = state.collections.find((item) => Number(item.id) === Number(state.filters.collectionId));
    dom.assetsTitle.textContent = collection ? collection.name : 'Project';
  } else {
    dom.assetsTitle.textContent = 'All Creations';
  }
}

function applyFiltersFromControls() {
  state.filters.scope = dom.scopeSelect.value;
  state.filters.workflowCategory = dom.typeSelect.value === '__all__' ? '' : dom.typeSelect.value;
  state.filters.datePreset = dom.datePresetSelect.value;
  state.filters.favoritesOnly = dom.favoritesCheckbox.checked;
  state.filters.hideFolderContents = dom.hideFolderContentCheckbox.checked;
  state.filters.sort = dom.sortSelect.value;
  state.filters.viewMode = dom.viewModeSelect.value;
  state.filters.search = dom.searchInput.value.trim();
  state.filters.status = dom.statusSelect.value === '__all__' ? '' : dom.statusSelect.value;

  const favoriteCategoryValue = dom.favoriteCategoryFilterSelect.value;
  if (favoriteCategoryValue === '__all__') {
    state.filters.favoriteCategory = '';
  } else if (favoriteCategoryValue === '__none__') {
    state.filters.favoriteCategory = '__none__';
  } else {
    state.filters.favoriteCategory = favoriteCategoryValue;
  }

  state.filters.dateFrom = dom.dateFromInput.value;
  state.filters.dateTo = dom.dateToInput.value;
  state.filters.pageSize = Number(dom.pageSizeSelect.value || 36);
}

async function loadHistory() {
  if (!state.user || state.loading) {
    return;
  }
  state.loading = true;
  setStatus('Loading history...');

  try {
    const query = buildHistoryQuery();
    const payload = await apiRequest(`/api/history?${query}`);

    state.items = Array.isArray(payload.items) ? payload.items : [];
    state.collections = Array.isArray(payload.collections) ? payload.collections : [];
    state.favoriteCategories = Array.isArray(payload.favorite_categories) ? payload.favorite_categories : [];
    state.workflowCategoryFacets = Array.isArray(payload.workflow_category_facets) ? payload.workflow_category_facets : [];
    state.statusFacets = Array.isArray(payload.status_facets) ? payload.status_facets : [];
    state.workflowFacets = Array.isArray(payload.workflow_facets) ? payload.workflow_facets : [];
    state.thumbnailStorage = payload.thumbnail_storage || null;
    state.favoritesTotal = Number(payload.favorites_total || 0);

    state.pagination.page = Number(payload.page || state.filters.page || 1);
    state.pagination.pageSize = Number(payload.page_size || state.filters.pageSize);
    state.pagination.totalPages = Number(payload.total_pages || 1);
    state.pagination.totalItems = Number(payload.total_items || 0);

    if (state.filters.page > state.pagination.totalPages) {
      state.filters.page = state.pagination.totalPages;
    }

    for (const taskId of Array.from(state.selectedTaskIds)) {
      if (!state.items.some((item) => item.task_id === taskId)) {
        state.selectedTaskIds.delete(taskId);
      }
    }

    renderFacetSelects();
    renderFolderGrid();
    renderSummary();
    renderAssetsMeta();
    renderGroupedAssets();
    renderPagination();
    updateSelectionBar();

    let message = state.pagination.totalItems
      ? `Loaded ${formatCount(state.pagination.totalItems)} item(s).`
      : 'No items found for current filters.';
    if (state.thumbnailStorage?.over_warning) {
      const used = formatStorageGb(state.thumbnailStorage.total_bytes || 0);
      const cap = formatStorageGb(state.thumbnailStorage.cap_bytes || 0);
      message += ` Local media cache usage is high (${used} GB / ${cap} GB).`;
    }
    setStatus(message);
  } catch (error) {
    setStatus(`Failed to load history: ${error.message}`, true);
    dom.groupedContainer.innerHTML = '';
    dom.emptyState.classList.remove('hidden');
    dom.emptyState.innerHTML = `<h3>History unavailable</h3><p>${escapeHtml(error.message)}</p>`;
  } finally {
    state.loading = false;
  }
}

async function setFavorite(item, isFavorite, favoriteCategoryKey = null) {
  try {
    await apiRequest(`/api/history/${encodeURIComponent(item.task_id)}/favorite`, {
      method: 'POST',
      body: JSON.stringify({
        is_favorite: Boolean(isFavorite),
        favorite_category_key: favoriteCategoryKey || null,
      }),
    });

    await loadHistory();
    if (state.activeTask?.task_id === item.task_id) {
      await openDetail(item.task_id, true);
    }
  } catch (error) {
    setStatus(`Favorite update failed: ${error.message}`, true);
  }
}

function syncDetailFavoriteStar(isFavorite) {
  const active = Boolean(isFavorite);
  dom.toggleFavoriteBtn.title = active ? 'Remove from favorites' : 'Add to favorites';
  dom.toggleFavoriteBtn.setAttribute('aria-label', dom.toggleFavoriteBtn.title);
  dom.toggleFavoriteBtn.classList.toggle('active', active);
}

async function openDetail(taskId, keepOpen = false) {
  try {
    setModalStatus('Loading details...');
    const payload = await apiRequest(`/api/history/${encodeURIComponent(taskId)}`);
    const item = payload.item;
    if (!item) {
      throw new Error('History item not found');
    }

    state.activeTask = item;
    const rawResultUrl = String(item.result_url || '').trim();
    const resultUrl = resolveDisplayImageUrl(rawResultUrl);
    const previewUrl = resolveDisplayImageUrl(item.preview_url || '');
    const thumbnailUrl = resolveDisplayImageUrl(item.thumbnail_url || '');
    const themeSourceUrl = previewUrl || thumbnailUrl;
    const modalPrimarySrc = previewUrl || thumbnailUrl;
    const modalSecondarySrc =
      previewUrl && thumbnailUrl && previewUrl !== thumbnailUrl
        ? thumbnailUrl
        : '';
    const projectName = item.collection_name || 'No project';
    const createdText = formatDate(item.created_at);
    const runtimeText = item.duration_text || '-';
    const outputText = formatOutputSize(item.output_width, item.output_height);
    const requestText = item.request_id || '-';
    const requestDisplayText = truncateMiddle(requestText, 18, 10);
    const workflowTitleDisplay = formatWorkflowDisplayName(item.workflow_name);

    dom.detailKicker.textContent = formatWorkflowKicker(item.workflow_category);
    dom.detailTitle.textContent = workflowTitleDisplay;
    dom.detailTitle.title = workflowTitleDisplay;
    dom.detailProjectChip.textContent = projectName;
    dom.detailProjectChip.title = projectName;
    dom.detailCreatedChip.textContent = createdText;
    dom.detailCreatedChip.title = createdText;
    dom.detailRequestLine.textContent = requestDisplayText;
    dom.detailRequestLine.title = requestText;
    dom.detailOutputStat.title = outputText;
    if (modalPrimarySrc) {
      dom.detailImage.src = modalPrimarySrc;
      hideDetailPreviewFallback();
    } else {
      dom.detailImage.removeAttribute('src');
      renderDetailPreviewFallback(item.status, 'Preview archived (cache cleaned).');
    }
    dom.detailImage.dataset.primarySrc = modalPrimarySrc;
    dom.detailImage.dataset.secondarySrc = modalSecondarySrc;
    dom.detailImage.dataset.fallbackTried = '0';
    dom.detailImage.dataset.rawSrc = rawResultUrl;
    dom.detailImage.dataset.proxySrc = modalPrimarySrc;
    dom.detailImage.alt = item.output_filename || item.task_id || 'result';
    dom.detailRuntimeOrb.textContent = runtimeText;
    dom.detailRuntimeOrb.title = runtimeText;
    dom.detailOutputStat.textContent = outputText;
    setPreviewImageMode(item.output_width, item.output_height);
    dom.detailImageBackdrop.style.backgroundImage = themeSourceUrl ? `url("${themeSourceUrl}")` : 'none';

    dom.openOriginalLink.href = rawResultUrl || resultUrl || '#';
    dom.downloadLink.href = rawResultUrl || resultUrl || '#';
    dom.downloadLink.setAttribute('download', item.output_filename || `${item.task_id}.png`);
    dom.openOriginalLink.classList.toggle('is-disabled', !rawResultUrl && !resultUrl);
    dom.downloadLink.classList.toggle('is-disabled', !rawResultUrl && !resultUrl);
    dom.copyTaskBtn.dataset.copyValue = String(rawResultUrl || resultUrl || '').trim();
    syncDetailFavoriteStar(item.is_favorite);

    state.detailForm.collectionValue = item.collection_id ? String(item.collection_id) : '';
    state.detailForm.favoriteCategoryValue = item.favorite_category_key || '';
    state.detailForm.savingCollection = false;
    state.detailForm.savingFavoriteCategory = false;
    syncDetailControlledSelects();
    setButtonBusy(dom.deleteAssetBtn, false);
    if (!modalPrimarySrc) {
      setModalStatus('Local preview is archived. Use Open original to load the full image.');
    } else {
      setModalStatus('');
    }
    refreshDetailAccent(themeSourceUrl || '');

    if (!keepOpen) {
      dom.detailModal.classList.remove('hidden');
    }
  } catch (error) {
    setModalStatus(`Failed to load details: ${error.message}`, true);
    if (!keepOpen) {
      dom.detailModal.classList.remove('hidden');
    }
  }
}

function closeDetail() {
  dom.detailModal.classList.add('hidden');
  state.activeTask = null;
  state.activeImageMetrics = null;
  resetDetailFormState();
  state.detailAccentToken += 1;
  dom.modalPreview.classList.remove('is-portrait');
  dom.detailModalPanel.classList.remove('orientation-portrait');
  dom.detailModalPanel.classList.remove('orientation-landscape');
  setPanelSizeVars();
  dom.detailImageBackdrop.style.backgroundImage = 'none';
  dom.detailImage.removeAttribute('data-raw-src');
  dom.detailImage.removeAttribute('data-proxy-src');
  dom.detailImage.removeAttribute('data-primary-src');
  dom.detailImage.removeAttribute('data-secondary-src');
  dom.detailImage.removeAttribute('data-fallback-tried');
  dom.detailImage.removeAttribute('src');
  hideDetailPreviewFallback();
  dom.copyTaskBtn.removeAttribute('data-copy-value');
  setButtonBusy(dom.deleteAssetBtn, false);
  syncDetailControlledSelects();
  applyDetailTheme(DEFAULT_DETAIL_THEME);
  setModalStatus('');
}

async function saveDetailFavoriteCategory() {
  if (!state.activeTask) {
    return;
  }

  const taskId = state.activeTask.task_id;
  const previousCategory = state.activeTask.favorite_category_key || '';
  const nextCategory = String(state.detailForm.favoriteCategoryValue || '');

  state.activeTask.favorite_category_key = nextCategory || null;
  state.detailForm.savingFavoriteCategory = true;
  syncDetailControlledSelects();
  setModalStatus('Saving favorite category...');

  try {
    const payload = await apiRequest(`/api/history/${encodeURIComponent(taskId)}/favorite`, {
      method: 'POST',
      body: JSON.stringify({
        is_favorite: state.activeTask.is_favorite,
        favorite_category_key: nextCategory || null,
      }),
    });

    const savedCategory = payload?.favorite?.favorite_category_key || null;
    state.activeTask.favorite_category_key = savedCategory;
    state.detailForm.favoriteCategoryValue = savedCategory || '';
    setModalStatus('Favorite category updated.');
    await loadHistory();
  } catch (error) {
    state.activeTask.favorite_category_key = previousCategory || null;
    state.detailForm.favoriteCategoryValue = previousCategory || '';
    setModalStatus(`Failed to update favorite category: ${error.message}`, true);
  } finally {
    state.detailForm.savingFavoriteCategory = false;
    syncDetailControlledSelects();
  }
}

async function saveDetailCollection() {
  if (!state.activeTask) {
    return;
  }

  const taskId = state.activeTask.task_id;
  const previousCollectionId = state.activeTask.collection_id ? String(state.activeTask.collection_id) : '';
  const previousCollectionName = state.activeTask.collection_name || null;
  const nextCollectionValue = String(state.detailForm.collectionValue || '');
  const nextCollectionId = nextCollectionValue ? Number(nextCollectionValue) : null;
  const selectedCollection = nextCollectionId
    ? state.collections.find((collection) => Number(collection.id) === Number(nextCollectionId))
    : null;

  state.activeTask.collection_id = nextCollectionId;
  state.activeTask.collection_name = selectedCollection ? selectedCollection.name : null;
  dom.detailProjectChip.textContent = state.activeTask.collection_name || 'No project';
  dom.detailProjectChip.title = dom.detailProjectChip.textContent;

  state.detailForm.savingCollection = true;
  syncDetailControlledSelects();
  setModalStatus('Saving project assignment...');

  try {
    const payload = await apiRequest(`/api/history/${encodeURIComponent(taskId)}/collection`, {
      method: 'POST',
      body: JSON.stringify({
        collection_id: nextCollectionId,
      }),
    });

    const assignment = payload?.assignment || {};
    const savedCollectionId = assignment.collection_id ? String(assignment.collection_id) : '';
    const savedCollectionName = assignment.collection_name || null;
    state.activeTask.collection_id = savedCollectionId ? Number(savedCollectionId) : null;
    state.activeTask.collection_name = savedCollectionName;
    state.detailForm.collectionValue = savedCollectionId;
    dom.detailProjectChip.textContent = savedCollectionName || 'No project';
    dom.detailProjectChip.title = dom.detailProjectChip.textContent;

    setModalStatus('Project assignment saved.');
    await loadHistory();
  } catch (error) {
    state.activeTask.collection_id = previousCollectionId ? Number(previousCollectionId) : null;
    state.activeTask.collection_name = previousCollectionName;
    state.detailForm.collectionValue = previousCollectionId;
    dom.detailProjectChip.textContent = previousCollectionName || 'No project';
    dom.detailProjectChip.title = dom.detailProjectChip.textContent;
    setModalStatus(`Failed to update project assignment: ${error.message}`, true);
  } finally {
    state.detailForm.savingCollection = false;
    syncDetailControlledSelects();
  }
}

async function addFavoriteCategory() {
  const name = dom.newCategoryInput.value.trim();
  if (!name) {
    setModalStatus('Enter a category name.', true);
    return;
  }

  try {
    const payload = await apiRequest('/api/favorite-categories', {
      method: 'POST',
      body: JSON.stringify({
        display_name: name,
      }),
    });

    state.favoriteCategories = Array.isArray(payload.categories) ? payload.categories : state.favoriteCategories;
    renderFacetSelects();
    dom.newCategoryInput.value = '';
    setModalStatus('Favorite category added.');
  } catch (error) {
    setModalStatus(`Failed to add category: ${error.message}`, true);
  }
}

async function toggleDetailFavorite() {
  if (!state.activeTask) {
    return;
  }

  const nextFavorite = !state.activeTask.is_favorite;
  try {
    await apiRequest(`/api/history/${encodeURIComponent(state.activeTask.task_id)}/favorite`, {
      method: 'POST',
      body: JSON.stringify({
        is_favorite: nextFavorite,
        favorite_category_key: state.activeTask.favorite_category_key || null,
      }),
    });

    await openDetail(state.activeTask.task_id, true);
    await loadHistory();
    setModalStatus(nextFavorite ? 'Added to favorites.' : 'Removed from favorites.');
  } catch (error) {
    setModalStatus(`Failed to update favorite: ${error.message}`, true);
  }
}

async function applyBulkAssign() {
  const selected = Array.from(state.selectedTaskIds);
  if (!selected.length) {
    setStatus('Select at least one item first.', true);
    return;
  }

  const collectionValue = dom.bulkCollectionSelect.value;
  const collectionId = collectionValue ? Number(collectionValue) : null;
  try {
    await apiRequest('/api/history/collection-assign', {
      method: 'POST',
      body: JSON.stringify({
        task_ids: selected,
        collection_id: collectionId,
      }),
    });

    setStatus(`Updated ${selected.length} item(s).`);
    state.selectedTaskIds.clear();
    await loadHistory();
  } catch (error) {
    setStatus(`Bulk update failed: ${error.message}`, true);
  }
}

function copyToClipboard(value) {
  const text = String(value || '');
  if (!text) {
    return Promise.resolve(false);
  }

  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    return navigator.clipboard.writeText(text).then(() => true).catch(() => false);
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  document.body.appendChild(textarea);
  textarea.select();

  let success = false;
  try {
    success = document.execCommand('copy');
  } catch (_error) {
    success = false;
  }

  textarea.remove();
  return Promise.resolve(success);
}

function bindEvents() {
  dom.loginForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    dom.loginError.textContent = '';

    try {
      const payload = await apiRequest('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          email: dom.emailInput.value.trim(),
          password: dom.passwordInput.value,
        }),
      });

      state.user = payload.user;
      updateUserHeader();
      showApp();
      state.filters.page = 1;
      await loadHistory();
      dom.passwordInput.value = '';
    } catch (error) {
      dom.loginError.textContent = error.message || 'Sign in failed.';
    }
  });

  dom.logoutBtn.addEventListener('click', async () => {
    try {
      await apiRequest('/api/auth/logout', { method: 'POST' });
    } catch (_error) {
      // no-op
    }
    state.user = null;
    showLogin('');
  });

  dom.toggleProjectsBtn?.addEventListener('click', () => {
    state.projectsCollapsed = !state.projectsCollapsed;
    applyProjectsCollapseState();
    saveProjectsCollapsedPreference(state.projectsCollapsed);
  });

  dom.toggleProjectsBtn?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return;
    }
    event.preventDefault();
    dom.toggleProjectsBtn.click();
  });

  const immediateReloadControls = [
    dom.scopeSelect,
    dom.typeSelect,
    dom.datePresetSelect,
    dom.favoritesCheckbox,
    dom.hideFolderContentCheckbox,
    dom.sortSelect,
    dom.viewModeSelect,
    dom.statusSelect,
    dom.favoriteCategoryFilterSelect,
  ];

  for (const control of immediateReloadControls) {
    control.addEventListener('change', async () => {
      applyFiltersFromControls();
      state.filters.page = 1;
      await loadHistory();
    });
  }

  dom.applyFiltersBtn.addEventListener('click', async () => {
    applyFiltersFromControls();
    state.filters.page = 1;
    await loadHistory();
  });

  dom.resetFiltersBtn.addEventListener('click', async () => {
    const oldPageSize = Number(dom.pageSizeSelect.value || 36);
    state.filters = initialFilters();
    state.filters.pageSize = oldPageSize;
    state.filters.viewMode = 'unified';
    syncControlsFromState();
    await loadHistory();
  });

  dom.refreshBtn.addEventListener('click', async () => {
    await loadHistory();
  });

  dom.pageSizeSelect.addEventListener('change', async () => {
    applyFiltersFromControls();
    state.filters.page = 1;
    await loadHistory();
  });

  dom.searchInput.addEventListener('keydown', async (event) => {
    if (event.key !== 'Enter') {
      return;
    }
    event.preventDefault();
    applyFiltersFromControls();
    state.filters.page = 1;
    await loadHistory();
  });

  dom.clearSelectionBtn.addEventListener('click', () => {
    state.selectedTaskIds.clear();
    updateSelectionBar();
    renderGroupedAssets();
  });

  dom.bulkAssignBtn.addEventListener('click', async () => {
    await applyBulkAssign();
  });

  dom.prevBtn.addEventListener('click', async () => {
    if (state.pagination.page <= 1) {
      return;
    }
    state.filters.page = state.pagination.page - 1;
    await loadHistory();
  });

  dom.nextBtn.addEventListener('click', async () => {
    if (state.pagination.page >= state.pagination.totalPages) {
      return;
    }
    state.filters.page = state.pagination.page + 1;
    await loadHistory();
  });

  dom.closeModalBtn.addEventListener('click', closeDetail);
  dom.detailModal.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    if (target.hasAttribute('data-close-modal')) {
      closeDetail();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !dom.confirmModal.classList.contains('hidden')) {
      closeConfirmDialog(false);
      return;
    }
    if (event.key === 'Escape' && !dom.detailModal.classList.contains('hidden')) {
      closeDetail();
    }
  });

  window.addEventListener('resize', () => {
    if (dom.detailModal.classList.contains('hidden')) {
      return;
    }
    const metrics = state.activeImageMetrics;
    if (!metrics) {
      return;
    }
    setPreviewImageMode(metrics.width, metrics.height);
  });

  dom.detailImage.addEventListener('load', () => {
    hideDetailPreviewFallback();
    setPreviewImageMode(dom.detailImage.naturalWidth, dom.detailImage.naturalHeight);
  });

  dom.detailImage.addEventListener('error', () => {
    const primarySrc = String(dom.detailImage.dataset.primarySrc || '').trim();
    const secondarySrc = String(dom.detailImage.dataset.secondarySrc || '').trim();
    const triedFallback = String(dom.detailImage.dataset.fallbackTried || '0') === '1';
    const currentSrc = String(dom.detailImage.currentSrc || dom.detailImage.src || '').trim();

    if (!triedFallback && secondarySrc && currentSrc && currentSrc !== secondarySrc) {
      dom.detailImage.dataset.fallbackTried = '1';
      dom.detailImage.src = secondarySrc;
      dom.detailImageBackdrop.style.backgroundImage = `url("${secondarySrc}")`;
      refreshDetailAccent(secondarySrc);
      setModalStatus('Primary preview missing. Using cached thumbnail fallback.');
      return;
    }

    const fallbackKey = normalizeStatusKey(state.activeTask?.status, { preferArchived: true });
    const label = fallbackKey === 'archived'
      ? 'Preview archived (cache cleaned).'
      : statusFallbackLabel(fallbackKey);
    renderDetailPreviewFallback(state.activeTask?.status, label);
    if (primarySrc || secondarySrc) {
      setModalStatus('Failed to load local preview cache.', true);
    } else {
      setModalStatus('Local preview is archived. Use Open original to load the full image.');
    }
  });

  dom.copyTaskBtn.addEventListener('click', async () => {
    const value =
      String(dom.copyTaskBtn.dataset.copyValue || '').trim() ||
      String(state.activeTask?.result_url || '').trim();
    if (!value) {
      setModalStatus('No image link available to copy.', true);
      return;
    }
    const ok = await copyToClipboard(value);
    setModalStatus(ok ? 'Image link copied.' : 'Copy failed.', !ok);
  });

  dom.toggleFavoriteBtn.addEventListener('click', async () => {
    await toggleDetailFavorite();
  });

  dom.deleteAssetBtn.addEventListener('click', async () => {
    await requestAssetDelete(state.activeTask, { sourceButton: dom.deleteAssetBtn });
  });

  dom.favoriteCategoryAssignSelect.addEventListener('change', async () => {
    if (!state.activeTask) {
      return;
    }
    state.detailForm.favoriteCategoryValue = dom.favoriteCategoryAssignSelect.value || '';
    syncDetailControlledSelects();
    await saveDetailFavoriteCategory();
  });

  dom.collectionAssignSelect.addEventListener('change', async () => {
    if (!state.activeTask) {
      return;
    }
    state.detailForm.collectionValue = dom.collectionAssignSelect.value || '';
    syncDetailControlledSelects();
    await saveDetailCollection();
  });

  dom.addCategoryBtn.addEventListener('click', async () => {
    await addFavoriteCategory();
  });

  dom.confirmCancelBtn.addEventListener('click', () => {
    closeConfirmDialog(false);
  });

  dom.confirmDeleteBtn.addEventListener('click', () => {
    closeConfirmDialog(true);
  });

  dom.confirmModal.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    if (target.hasAttribute('data-close-confirm')) {
      closeConfirmDialog(false);
    }
  });
}

async function bootstrap() {
  bindEvents();
  state.projectsCollapsed = loadProjectsCollapsedPreference();
  syncControlsFromState();
  applyProjectsCollapseState();

  try {
    state.sso = parseSsoContextFromUrl();
    const payload = await apiRequest('/api/auth/me');
    state.user = payload.user;
    updateUserHeader();
    showApp();
    await loadHistory();
  } catch (_error) {
    showLogin('');
  }
}

bootstrap();
