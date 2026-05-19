const ACCESS_TOKEN_KEY = 'webmail_admin_access_token';
const REFRESH_TOKEN_KEY = 'webmail_admin_refresh_token';

export function getAdminAccessToken() {
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getAdminRefreshToken() {
  return window.localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setAdminTokens(accessToken: string, refreshToken: string | null) {
  window.localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  if (refreshToken) {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  } else {
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
  }
}

export function clearAdminTokens() {
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export function hasAdminToken() {
  return Boolean(getAdminAccessToken());
}
