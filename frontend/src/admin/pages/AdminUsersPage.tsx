import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import {
  bulkAdminUsers,
  createAdminUser,
  deleteAdminUser,
  importAdminUsersCsv,
  fetchAdminDomains,
  fetchAdminUsers,
  resetAdminUserPassword,
  resetAdminUserPasswordRandomly,
  updateAdminUser,
  updateAdminUserQuota,
} from '../api';
import { AdminDialog, ResultMessage, SectionCard, StatusPill, useAdminListSearchParams } from '../components/AdminHelpers';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminMailboxUser, UserFormInput, UserUpdateInput } from '../types';

const emptyUserForm: UserFormInput = {
  email: '',
  display_name: '',
  domain_id: '',
  password: '',
  quota_mb: 500,
  status: 'active',
  is_admin: false,
};

const emptyImportForm = {
  csv_content: '',
  domain_id: '',
};

function formatLastLogin(value?: string | null) {
  if (!value) {
    return '—';
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'medium' });
}

export function AdminUsersPage() {
  const queryClient = useQueryClient();
  const params = useAdminListSearchParams({ q: '', status: '', domain_id: '', page: 1 });
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [editingUser, setEditingUser] = useState<AdminMailboxUser | null>(null);
  const [form, setForm] = useState<UserFormInput>(emptyUserForm);
  const [resetPasswordTarget, setResetPasswordTarget] = useState<AdminMailboxUser | null>(null);
  const [resetPasswordValue, setResetPasswordValue] = useState('');
  const [generatedPassword, setGeneratedPassword] = useState('');
  const [quotaTarget, setQuotaTarget] = useState<AdminMailboxUser | null>(null);
  const [quotaValue, setQuotaValue] = useState('');
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [importForm, setImportForm] = useState(emptyImportForm);
  const [deleteTarget, setDeleteTarget] = useState<AdminMailboxUser | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: domainData } = useQuery({
    queryKey: ['admin-domains', 'options'],
    queryFn: () => fetchAdminDomains({ page: 1, page_size: 100 }),
  });

  const { data, isLoading } = useQuery({
    queryKey: ['admin-users', params.page, params.q, params.status, params.domain_id],
    queryFn: () => fetchAdminUsers({ page: params.page, page_size: 10, q: params.q, status: params.status || undefined, domain_id: params.domain_id || undefined }),
  });

  const refreshLists = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-users'] });
    await queryClient.invalidateQueries({ queryKey: ['admin-quotas'] });
    await queryClient.invalidateQueries({ queryKey: ['admin-overview'] });
  };

  const createMutation = useMutation({
    mutationFn: createAdminUser,
    onSuccess: async () => {
      setForm(emptyUserForm);
      setSuccess('用户已创建。');
      setError(null);
      await refreshLists();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: UserUpdateInput }) => updateAdminUser(id, payload),
    onSuccess: async () => {
      setEditingUser(null);
      setSuccess('用户已更新。');
      setError(null);
      await refreshLists();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const resetPasswordMutation = useMutation({
    mutationFn: ({ id, password, generateRandom }: { id: string; password: string; generateRandom: boolean }) => generateRandom
      ? resetAdminUserPasswordRandomly(id)
      : resetAdminUserPassword(id, password),
    onSuccess: async (payload) => {
      setResetPasswordValue('');
      setGeneratedPassword(payload.generated_password || '');
      setSuccess(payload.generated_password ? '密码已重置，已返回随机密码。' : '密码已重置。');
      setError(null);
      await refreshLists();
      if (!payload.generated_password) {
        setResetPasswordTarget(null);
      }
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const importMutation = useMutation({
    mutationFn: importAdminUsersCsv,
    onSuccess: async (payload) => {
      setImportDialogOpen(false);
      setImportForm(emptyImportForm);
      setSuccess(`CSV 导入完成：创建 ${payload.created} 条，跳过 ${payload.skipped} 条。`);
      setError(null);
      await refreshLists();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteAdminUser,
    onSuccess: async () => {
      setDeleteTarget(null);
      setSuccess('用户已删除。');
      setError(null);
      await refreshLists();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const bulkMutation = useMutation({
    mutationFn: bulkAdminUsers,
    onSuccess: async (_, payload) => {
      setSelectedIds([]);
      setSuccess(`批量操作已完成：${payload.action}`);
      setError(null);
      await refreshLists();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const quotaMutation = useMutation({
    mutationFn: ({ id, quota_mb }: { id: string; quota_mb: number }) => updateAdminUserQuota(id, quota_mb),
    onSuccess: async () => {
      setQuotaTarget(null);
      setQuotaValue('');
      setSuccess('配额已更新。');
      setError(null);
      await refreshLists();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const columns = useMemo<ColumnDef<AdminMailboxUser>[]>(() => [
    {
      id: 'select',
      header: '选择',
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={selectedIds.includes(row.original.id)}
          onChange={(event) => {
            setSelectedIds((current) => event.target.checked
              ? [...current, row.original.id]
              : current.filter((item) => item !== row.original.id));
          }}
        />
      ),
    },
    { accessorKey: 'email', header: '邮箱' },
    { accessorKey: 'display_name', header: '名称', cell: (info) => info.getValue<string>() || '—' },
    { accessorKey: 'domain_name', header: '域名', cell: (info) => info.getValue<string>() || '—' },
    { accessorKey: 'quota_mb', header: '上限(MB)' },
    { accessorKey: 'used_quota_mb', header: '已用(MB)', cell: (info) => info.getValue<number>() ?? 0 },
    { accessorKey: 'usage_percent', header: '使用率', cell: (info) => `${info.getValue<number>() ?? 0}%` },
    { accessorKey: 'last_login_at', header: '最后登录', cell: (info) => formatLastLogin(info.getValue<string | null | undefined>()) },
    { accessorKey: 'status', header: '状态', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => (
        <div className="admin-inline-actions">
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => {
              setEditingUser(row.original);
              setForm({
                email: row.original.email,
                display_name: row.original.display_name || '',
                domain_id: row.original.domain_id || '',
                password: '',
                quota_mb: row.original.quota_mb,
                status: row.original.status as 'active' | 'disabled',
                is_admin: row.original.is_admin,
              });
            }}
          >
            编辑
          </button>
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => {
              setResetPasswordTarget(row.original);
              setResetPasswordValue('');
            }}
          >
            重置密码
          </button>
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => {
              const next = row.original.status === 'active' ? 'disabled' : 'active';
              updateMutation.mutate({ id: row.original.id, payload: { status: next } });
            }}
          >
            {row.original.status === 'active' ? '停用' : '启用'}
          </button>
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => {
              setQuotaTarget(row.original);
              setQuotaValue(String(row.original.quota_mb));
            }}
          >
            配额
          </button>
          <button
            type="button"
            className="admin-button admin-button-danger"
            onClick={() => setDeleteTarget(row.original)}
          >
            删除
          </button>
        </div>
      ),
    },
  ], [deleteMutation, quotaMutation, resetPasswordMutation, selectedIds, updateMutation]);

  return (
    <div className="admin-section-stack">
      <SectionCard
        title={editingUser ? '编辑用户' : '新增用户'}
        description="后台创建用户时会真实写入本地密码哈希。"
        actions={(
          <button type="button" className="admin-button admin-button-secondary" onClick={() => setImportDialogOpen(true)}>
            CSV 导入
          </button>
        )}
      >
        <form
          className="admin-form-grid admin-form-grid--two"
          onSubmit={(event) => {
            event.preventDefault();
            if (editingUser) {
              updateMutation.mutate({
                id: editingUser.id,
                payload: {
                  display_name: form.display_name,
                  domain_id: form.domain_id || null,
                  quota_mb: Number(form.quota_mb),
                  status: form.status,
                  is_admin: form.is_admin,
                },
              });
              return;
            }
            createMutation.mutate({
              ...form,
              quota_mb: Number(form.quota_mb),
            });
          }}
        >
          <label>
            <span>邮箱</span>
            <input
              value={form.email}
              disabled={Boolean(editingUser)}
              onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
            />
          </label>
          <label>
            <span>显示名称</span>
            <input
              value={form.display_name || ''}
              onChange={(event) => setForm((current) => ({ ...current, display_name: event.target.value }))}
            />
          </label>
          <label>
            <span>所属域</span>
            <select value={form.domain_id || ''} onChange={(event) => setForm((current) => ({ ...current, domain_id: event.target.value }))}>
              <option value="">自动匹配</option>
              {(domainData?.items || []).map((domain) => (
                <option key={domain.id} value={domain.id}>{domain.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span>配额(MB)</span>
            <input
              inputMode="numeric"
              value={String(form.quota_mb)}
              onChange={(event) => setForm((current) => ({ ...current, quota_mb: Number(event.target.value || 0) }))}
            />
          </label>
          {!editingUser ? (
            <label>
              <span>初始密码</span>
              <input
                type="password"
                autoComplete="new-password"
                value={form.password}
                onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
              />
            </label>
          ) : null}
          <label>
            <span>状态</span>
            <select value={form.status} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value as 'active' | 'disabled' }))}>
              <option value="active">active</option>
              <option value="disabled">disabled</option>
            </select>
          </label>
          <label className="admin-check-field">
            <input
              type="checkbox"
              checked={form.is_admin}
              onChange={(event) => setForm((current) => ({ ...current, is_admin: event.target.checked }))}
            />
            <span>标记为邮箱管理员</span>
          </label>
          <div className="admin-inline-actions">
            <button type="submit" className="admin-button admin-button-primary" disabled={createMutation.isPending || updateMutation.isPending}>
              {editingUser ? '保存修改' : '创建用户'}
            </button>
            {editingUser ? (
              <button
                type="button"
                className="admin-button admin-button-secondary"
                onClick={() => {
                  setEditingUser(null);
                  setForm(emptyUserForm);
                }}
              >
                取消编辑
              </button>
            ) : null}
          </div>
        </form>
        <ResultMessage error={error} success={success} />
      </SectionCard>

      <AdminListTable
        data={data?.items ?? []}
        emptyMessage={isLoading ? '加载中...' : '暂无用户数据'}
        columns={columns}
        toolbar={(
          <div className="admin-toolbar-grid">
            <input placeholder="搜索邮箱/名称" value={params.q} onChange={(event) => params.setQ(event.target.value)} />
            <select value={params.status} onChange={(event) => params.setStatus(event.target.value)}>
              <option value="">全部状态</option>
              <option value="active">active</option>
              <option value="disabled">disabled</option>
            </select>
            <select value={params.domain_id} onChange={(event) => params.setDomainId(event.target.value)}>
              <option value="">全部域名</option>
              {(domainData?.items || []).map((domain) => (
                <option key={domain.id} value={domain.id}>{domain.name}</option>
              ))}
            </select>
            <div className="admin-inline-actions">
              <button type="button" className="admin-button admin-button-secondary" disabled={selectedIds.length === 0} onClick={() => bulkMutation.mutate({ ids: selectedIds, action: 'activate' })}>批量启用</button>
              <button type="button" className="admin-button admin-button-secondary" disabled={selectedIds.length === 0} onClick={() => bulkMutation.mutate({ ids: selectedIds, action: 'disable' })}>批量停用</button>
              <button type="button" className="admin-button admin-button-danger" disabled={selectedIds.length === 0} onClick={() => bulkMutation.mutate({ ids: selectedIds, action: 'delete' })}>批量删除</button>
            </div>
          </div>
        )}
        pagination={data ? { ...data, onPageChange: params.setPage } : undefined}
      />

      <AdminDialog
        open={Boolean(resetPasswordTarget)}
        title="重置用户密码"
        description={resetPasswordTarget ? `为 ${resetPasswordTarget.email} 设置新密码` : undefined}
        onClose={() => {
          setResetPasswordTarget(null);
          setResetPasswordValue('');
          setGeneratedPassword('');
        }}
        actions={(
          <button
            type="button"
            className="admin-button admin-button-primary"
            disabled={!resetPasswordTarget || resetPasswordMutation.isPending}
            onClick={() => {
              if (!resetPasswordTarget) return;
              resetPasswordMutation.mutate({ id: resetPasswordTarget.id, password: resetPasswordValue, generateRandom: !resetPasswordValue });
            }}
          >
            {resetPasswordValue ? '确认重置' : '随机重置并展示'}
          </button>
        )}
      >
        <p className="admin-help-text">不填写密码时将自动生成随机密码，并在重置后展示返回结果。</p>
        <label className="admin-dialog-field">
          <span>新密码</span>
          <input
            type="password"
            autoComplete="new-password"
            value={resetPasswordValue}
            onChange={(event) => setResetPasswordValue(event.target.value)}
          />
        </label>
        {generatedPassword ? (
          <label className="admin-dialog-field">
            <span>返回密码</span>
            <input readOnly value={generatedPassword} />
          </label>
        ) : null}
      </AdminDialog>

      <AdminDialog
        open={importDialogOpen}
        title="CSV 导入用户"
        description="支持按 email,password,display_name,quota_mb,status,is_admin 的 CSV 导入。"
        onClose={() => {
          setImportDialogOpen(false);
          setImportForm(emptyImportForm);
        }}
        actions={(
          <button
            type="button"
            className="admin-button admin-button-primary"
            disabled={!importForm.csv_content.trim() || importMutation.isPending}
            onClick={() => {
              importMutation.mutate({
                csv_content: importForm.csv_content,
                domain_id: importForm.domain_id || undefined,
              });
            }}
          >
            开始导入
          </button>
        )}
      >
        <div className="admin-form-grid">
          <label>
            <span>所属域</span>
            <select value={importForm.domain_id} onChange={(event) => setImportForm((current) => ({ ...current, domain_id: event.target.value }))}>
              <option value="">自动识别</option>
              {(domainData?.items || []).map((domain) => (
                <option key={domain.id} value={domain.id}>{domain.name}</option>
              ))}
            </select>
          </label>
          <label className="admin-dialog-field">
            <span>CSV 内容</span>
            <textarea
              rows={10}
              value={importForm.csv_content}
              onChange={(event) => setImportForm((current) => ({ ...current, csv_content: event.target.value }))}
              placeholder="email,password,display_name,quota_mb,status,is_admin"
            />
          </label>
        </div>
      </AdminDialog>

      <AdminDialog
        open={Boolean(quotaTarget)}
        title="修改用户配额"
        description={quotaTarget ? `修改 ${quotaTarget.email} 的配额上限` : undefined}
        onClose={() => {
          setQuotaTarget(null);
          setQuotaValue('');
        }}
        actions={(
          <button
            type="button"
            className="admin-button admin-button-primary"
            disabled={!quotaTarget || !quotaValue || quotaMutation.isPending}
            onClick={() => {
              if (!quotaTarget) return;
              const parsed = Number(quotaValue);
              if (!Number.isFinite(parsed) || parsed <= 0) return;
              quotaMutation.mutate({ id: quotaTarget.id, quota_mb: parsed });
            }}
          >
            保存配额
          </button>
        )}
      >
        <label className="admin-dialog-field">
          <span>配额(MB)</span>
          <input inputMode="numeric" value={quotaValue} onChange={(event) => setQuotaValue(event.target.value)} />
        </label>
      </AdminDialog>

      <AdminDialog
        open={Boolean(deleteTarget)}
        title="确认删除用户"
        description={deleteTarget ? `删除 ${deleteTarget.email} 后将不可恢复。` : undefined}
        onClose={() => setDeleteTarget(null)}
        actions={(
          <button
            type="button"
            className="admin-button admin-button-danger"
            disabled={!deleteTarget || deleteMutation.isPending}
            onClick={() => {
              if (!deleteTarget) return;
              deleteMutation.mutate(deleteTarget.id);
            }}
          >
            确认删除
          </button>
        )}
      />
    </div>
  );
}
