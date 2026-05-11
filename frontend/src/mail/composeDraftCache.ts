import type { ComposeValues } from './ComposePanel';

export type ComposeDraftCacheSnapshot = {
  draft_id: string | null;
  values: ComposeValues;
  updated_at: string;
};

const STORAGE_PREFIX = 'webmail-compose-draft';

function getCacheKey(scope: string) {
  return `${STORAGE_PREFIX}:${scope.trim().toLowerCase() || 'default'}`;
}

export function readComposeDraftCache(scope: string): ComposeDraftCacheSnapshot | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const raw = window.localStorage.getItem(getCacheKey(scope));
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<ComposeDraftCacheSnapshot>;
    if (!parsed || typeof parsed !== 'object' || typeof parsed.updated_at !== 'string') {
      return null;
    }
    return {
      draft_id: typeof parsed.draft_id === 'string' ? parsed.draft_id : null,
      values: (parsed.values ?? {}) as ComposeValues,
      updated_at: parsed.updated_at,
    };
  } catch {
    return null;
  }
}

export function writeComposeDraftCache(scope: string, snapshot: ComposeDraftCacheSnapshot) {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(getCacheKey(scope), JSON.stringify(snapshot));
}

export function clearComposeDraftCache(scope: string) {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.removeItem(getCacheKey(scope));
}
