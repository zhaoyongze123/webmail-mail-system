export type AdminUser = {
  id: string;
  email: string;
  name: string;
  role: string;
  domain_id?: string | null;
  totp_enabled?: boolean;
  last_login_at?: string | null;
};

export type AdminAuthPayload = {
  access_token: string;
  refresh_token?: string | null;
  expires_at?: string | null;
  refresh_expires_at?: string | null;
  user: AdminUser;
};

export type AdminTokenPair = {
  accessToken: string;
  refreshToken: string | null;
};

export type PaginationMeta = {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

export type PaginatedResult<T> = PaginationMeta & {
  items: T[];
};

export type AdminOverviewStats = {
  active_users: number;
  mail_domains: number;
  aliases: number;
  queued_jobs: number;
  summary?: Record<string, number>;
  recent_audits?: AdminAuditLogItem[];
  scope?: {
    role: string;
    domain_id?: string | null;
  };
};

export type AdminListItem = {
  id: string;
  name: string;
  status: string;
  updated_at: string;
  created_at?: string;
  description?: string;
};

export type AdminDomain = AdminListItem & {
  quota_limit_mb: number;
  user_count: number;
  alias_count: number;
  used_quota_mb: number;
};

export type AdminDnsCheckItem = {
  key: string;
  label: string;
  status: 'ok' | 'warning' | 'missing' | 'unavailable' | 'error';
  detail: string;
  records: string[];
  backend: string;
  command_result?: {
    command?: string[];
    stdout?: string;
    stderr?: string;
    exit_code?: number;
    duration_ms?: number;
    ok?: boolean;
  };
};

export type AdminDomainDnsCheck = {
  domain: string;
  checked_at: number;
  status: 'ok' | 'warning' | 'unavailable' | 'error';
  checks: AdminDnsCheckItem[];
};

export type AdminQueueItem = {
  id: string;
  queue_id: string;
  status: string;
  queue_name: string;
  sender: string;
  recipients: string[];
  recipient_count: number;
  message_size: number;
  arrival_time: number;
  created_at: number;
  name: string;
  description: string;
};

export type AdminQueueSnapshot = {
  status: 'ok' | 'unavailable' | 'error';
  detail: string;
  items: AdminQueueItem[];
  summary: Record<string, number>;
  command_result?: {
    command?: string[];
    stdout?: string;
    stderr?: string;
    exit_code?: number;
    duration_ms?: number;
    ok?: boolean;
  };
};

export type AdminMailboxUser = AdminListItem & {
  email: string;
  display_name?: string | null;
  domain_id?: string | null;
  domain_name?: string | null;
  quota_mb: number;
  used_quota_mb?: number;
  usage_percent?: number;
  quota_status?: 'healthy' | 'warning' | 'critical';
  usage_source?: string;
  has_local_password?: boolean;
  is_admin: boolean;
  last_login_at?: string | null;
};

export type AdminAlias = AdminListItem & {
  domain_id: string;
  domain_name?: string | null;
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
  usage_source?: string;
  warn_80_enabled: boolean;
  warn_90_enabled: boolean;
  warn_95_enabled: boolean;
};

export type AdminAuditLogItem = {
  id: string;
  actor: string;
  action: string;
  target: string;
  event_type?: string;
  actor_type?: string | null;
  actor_id?: string | null;
  target_type?: string | null;
  target_id?: string | null;
  created_at: string;
};

export type AdminHealthItem = {
  name: string;
  status: 'ok' | 'degraded' | 'down' | 'warning' | 'critical' | 'unavailable' | 'error';
  detail: string;
};

export type AdminDiskUsageItem = {
  name: string;
  mount_point: string;
  filesystem: string;
  total_gb: number;
  used_gb: number;
  free_gb: number;
  usage_percent: number;
  status: 'ok' | 'warning' | 'critical' | 'unavailable' | 'error';
  detail: string;
  source?: string;
};

export type AdminLogSnapshot = {
  key: string;
  label: string;
  status: 'ok' | 'warning' | 'critical' | 'unavailable' | 'error';
  detail: string;
  source: string;
  lines: string[];
  line_count: number;
};

export type AdminSystemHealthSnapshot = {
  items: AdminHealthItem[];
  services: AdminHealthItem[];
  disks: AdminDiskUsageItem[];
  logs: AdminLogSnapshot[];
  checked_at: string;
};

export type AdminRspamdThresholds = {
  status: 'ok' | 'warning' | 'critical' | 'unavailable' | 'error';
  detail: string;
  source: string;
  thresholds: {
    reject: number;
    add_header: number;
    greylist: number;
  };
};

export type AdminRspamdDomainItem = {
  id: string;
  name: string;
  spf_status: string;
  spf_detail: string;
  spf_records: string[];
  dmarc_status: string;
  dmarc_detail: string;
  dmarc_records: string[];
  dkim_dns_status: string;
  dkim_dns_detail: string;
  dkim_dns_records: string[];
  dkim_selector?: string | null;
  dkim_local_status: string;
  dkim_local_detail: string;
  dkim_key_path?: string | null;
  dkim_key_exists: boolean;
  dkim_public_key?: string | null;
};

export type AdminRspamdSnapshot = {
  thresholds: AdminRspamdThresholds;
  domains: AdminRspamdDomainItem[];
};

export type AdminTlsItem = {
  name: string;
  status: 'ok' | 'warning' | 'critical' | 'unavailable' | 'error';
  detail: string;
  certificate_path?: string | null;
  expires_at?: string | null;
  domains: string[];
};

export type AdminTlsSnapshot = {
  status: 'ok' | 'warning' | 'critical' | 'unavailable' | 'error';
  detail: string;
  items: AdminTlsItem[];
};

export type DomainFormInput = {
  name: string;
  quota_limit_mb: number;
  status: 'active' | 'disabled';
};

export type UserFormInput = {
  email: string;
  display_name?: string;
  domain_id?: string | null;
  password: string;
  quota_mb: number;
  status: 'active' | 'disabled';
  is_admin: boolean;
};

export type UserUpdateInput = {
  display_name?: string;
  domain_id?: string | null;
  quota_mb?: number;
  status?: 'active' | 'disabled';
  is_admin?: boolean;
};

export type AliasFormInput = {
  domain_id: string;
  source_address: string;
  target_addresses: string[];
};

export type AliasUpdateInput = {
  target_addresses?: string[];
  is_active?: boolean;
};

export type QuotaPolicyFormInput = {
  domain_id?: string | null;
  default_quota_mb: number;
  warn_80_enabled: boolean;
  warn_90_enabled: boolean;
  warn_95_enabled: boolean;
};

export type ListQuery = {
  page?: number;
  page_size?: number;
  q?: string;
  domain_id?: string;
  status?: string;
  sort?: string;
};
