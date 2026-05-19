export type AdminUser = {
  id: string;
  email: string;
  name: string;
  role: string;
  totp_enabled?: boolean;
};

export type AdminAuthPayload = {
  access_token: string;
  refresh_token?: string | null;
  expires_at?: string | null;
  user: AdminUser;
};

export type AdminTokenPair = {
  accessToken: string;
  refreshToken: string | null;
};

export type AdminOverviewStats = {
  active_users: number;
  mail_domains: number;
  aliases: number;
  queued_jobs: number;
};

export type AdminListItem = {
  id: string;
  name: string;
  status: string;
  updated_at: string;
  description?: string;
};

export type AdminDomain = AdminListItem & {
  quota_limit_mb: number;
  user_count: number;
  alias_count: number;
  used_quota_mb: number;
};

export type AdminMailboxUser = AdminListItem & {
  email: string;
  display_name?: string | null;
  domain_id?: string | null;
  quota_mb: number;
  is_admin: boolean;
  last_login_at?: string | null;
};

export type AdminAlias = AdminListItem & {
  domain_id: string;
  source_address: string;
  target_addresses: string[];
  is_active: boolean;
};

export type AdminQuotaItem = AdminListItem & {
  domain_id?: string | null;
  domain_name?: string | null;
  default_quota_mb: number;
  quota_limit_mb?: number;
  used_quota_mb?: number;
  usage_percent?: number;
  warn_80_enabled: boolean;
  warn_90_enabled: boolean;
  warn_95_enabled: boolean;
};

export type AdminAuditLogItem = {
  id: string;
  actor: string;
  action: string;
  target: string;
  created_at: string;
};

export type AdminHealthItem = {
  name: string;
  status: 'ok' | 'degraded' | 'down';
  detail: string;
};
