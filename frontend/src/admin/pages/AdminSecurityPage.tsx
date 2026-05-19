import { useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { z } from 'zod';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { adminChangePassword, adminTotpDisable, adminTotpEnable, adminTotpSetup } from '../api';
import { useAdminAuth } from '../auth';
import { AdminDialog } from '../components/AdminHelpers';

const passwordSchema = z.object({
  current_password: z.string().min(1, '请输入当前密码'),
  new_password: z.string().min(8, '新密码至少 8 位'),
  confirm_password: z.string().min(1, '请再次输入新密码'),
}).refine((value) => value.new_password === value.confirm_password, {
  message: '两次输入的新密码不一致',
  path: ['confirm_password'],
});

const totpCodeSchema = z.object({
  code: z.string().min(6, '请输入 6 位验证码'),
});

type PasswordFormValues = z.infer<typeof passwordSchema>;
type TotpCodeFormValues = z.infer<typeof totpCodeSchema>;

function formatProvisioningUri(uri: string) {
  try {
    const url = new URL(uri);
    const label = url.pathname.replace(/^\/+/, '');
    const issuer = url.searchParams.get('issuer');
    return `${issuer ? `${issuer} · ` : ''}${decodeURIComponent(label)}`;
  } catch {
    return uri;
  }
}

export function AdminSecurityPage() {
  const { user } = useAdminAuth();
  const [totpSecret, setTotpSecret] = useState<string | null>(null);
  const [provisioningUri, setProvisioningUri] = useState<string | null>(null);
  const [totpEnabled, setTotpEnabled] = useState<boolean>(Boolean((user as { totp_enabled?: boolean } | null)?.totp_enabled));
  const [disableDialogOpen, setDisableDialogOpen] = useState(false);
  const [disableCode, setDisableCode] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const passwordForm = useForm<PasswordFormValues>({
    resolver: zodResolver(passwordSchema),
    defaultValues: {
      current_password: '',
      new_password: '',
      confirm_password: '',
    },
  });

  const totpForm = useForm<TotpCodeFormValues>({
    resolver: zodResolver(totpCodeSchema),
    defaultValues: {
      code: '',
    },
  });

  const changePasswordMutation = useMutation({
    mutationFn: adminChangePassword,
    onSuccess: async () => {
      passwordForm.reset();
      setError(null);
      setMessage('密码已更新。当前登录态保留，建议完成 TOTP 配置后重新确认登录。');
    },
    onError: (err) => {
      setMessage(null);
      setError((err as Error).message || '修改密码失败');
    },
  });

  const totpSetupMutation = useMutation({
    mutationFn: adminTotpSetup,
    onSuccess: (payload) => {
      setTotpSecret(payload.secret);
      setProvisioningUri(payload.provisioning_uri);
      setTotpEnabled(payload.enabled);
      setMessage('TOTP 已初始化，请用验证器扫描二维码链接或手动输入密钥后完成启用。');
      setError(null);
    },
    onError: (err) => {
      setMessage(null);
      setError((err as Error).message || 'TOTP 初始化失败');
    },
  });

  const totpEnableMutation = useMutation({
    mutationFn: adminTotpEnable,
    onSuccess: (payload) => {
      totpForm.reset();
      setTotpEnabled(payload.enabled);
      setMessage('TOTP 已启用。');
      setError(null);
    },
    onError: (err) => {
      setMessage(null);
      setError((err as Error).message || 'TOTP 启用失败');
    },
  });

  const totpDisableMutation = useMutation({
    mutationFn: adminTotpDisable,
    onSuccess: (payload) => {
      totpForm.reset();
      setDisableCode('');
      setDisableDialogOpen(false);
      setTotpEnabled(!payload.enabled ? false : payload.enabled);
      setMessage('TOTP 已停用。');
      setError(null);
    },
    onError: (err) => {
      setMessage(null);
      setError((err as Error).message || 'TOTP 停用失败');
    },
  });

  const provisioningLabel = useMemo(() => {
    if (!provisioningUri) return '尚未初始化';
    return formatProvisioningUri(provisioningUri);
  }, [provisioningUri]);

  const submitPassword = passwordForm.handleSubmit(async (values) => {
    await changePasswordMutation.mutateAsync({
      current_password: values.current_password,
      new_password: values.new_password,
    });
  });

  const submitTotp = totpForm.handleSubmit(async (values) => {
    await totpEnableMutation.mutateAsync({ code: values.code });
  });

  return (
    <div className="admin-section-stack">
      <section className="admin-form-card">
        <div className="admin-form-card__header">
          <div>
            <h2>修改密码</h2>
            <p>调用 `/api/admin/auth/change-password`。提交后不会自动注销当前会话。</p>
          </div>
          <span className={`admin-status-pill ${changePasswordMutation.isPending ? 'is-working' : 'is-idle'}`}>
            {changePasswordMutation.isPending ? '提交中' : '可操作'}
          </span>
        </div>
        <form className="admin-form-grid" onSubmit={submitPassword}>
          <input type="text" autoComplete="username" value={user?.email || user?.name || 'admin'} readOnly hidden aria-hidden="true" />
          <label>
            <span>当前密码</span>
            <input type="password" autoComplete="current-password" {...passwordForm.register('current_password')} />
            {passwordForm.formState.errors.current_password ? <em>{passwordForm.formState.errors.current_password.message}</em> : null}
          </label>
          <label>
            <span>新密码</span>
            <input type="password" autoComplete="new-password" {...passwordForm.register('new_password')} />
            {passwordForm.formState.errors.new_password ? <em>{passwordForm.formState.errors.new_password.message}</em> : null}
          </label>
          <label>
            <span>确认新密码</span>
            <input type="password" autoComplete="new-password" {...passwordForm.register('confirm_password')} />
            {passwordForm.formState.errors.confirm_password ? <em>{passwordForm.formState.errors.confirm_password.message}</em> : null}
          </label>
          <div className="admin-inline-actions">
            <button type="submit" className="admin-button admin-button-primary" disabled={changePasswordMutation.isPending}>
              {changePasswordMutation.isPending ? '保存中...' : '更新密码'}
            </button>
          </div>
        </form>
      </section>

      <section className="admin-form-card">
        <div className="admin-form-card__header">
          <div>
            <h2>TOTP 管理</h2>
            <p>先初始化，再用验证码启用或停用。调用 `/api/admin/auth/totp/setup`、`/enable`、`/disable`。</p>
          </div>
          <span className={`admin-status-pill ${totpEnabled ? 'is-success' : 'is-warning'}`}>
            {totpEnabled ? '已启用' : '未启用'}
          </span>
        </div>
        <div className="admin-section-stack">
          <div className="admin-info-card">
            <strong>初始化状态</strong>
            <p>当前密钥：{totpSecret ? '已生成' : '未生成'}</p>
            <p>配置标识：{provisioningLabel}</p>
            {totpSecret ? <p className="admin-mono">Secret: {totpSecret}</p> : null}
            {provisioningUri ? <p className="admin-mono">URI: {provisioningUri}</p> : null}
          </div>
          <div className="admin-inline-actions">
            <button
              type="button"
              className="admin-button admin-button-secondary"
              onClick={() => totpSetupMutation.mutate()}
              disabled={totpSetupMutation.isPending}
            >
              {totpSetupMutation.isPending ? '初始化中...' : '初始化 TOTP'}
            </button>
          </div>
          <form className="admin-form-grid" onSubmit={submitTotp}>
            <label>
              <span>验证码</span>
              <input inputMode="numeric" autoComplete="one-time-code" placeholder="123456" {...totpForm.register('code')} />
              {totpForm.formState.errors.code ? <em>{totpForm.formState.errors.code.message}</em> : null}
            </label>
            <div className="admin-inline-actions">
              <button type="submit" className="admin-button admin-button-primary" disabled={totpEnableMutation.isPending}>
                {totpEnableMutation.isPending ? '提交中...' : '启用 TOTP'}
              </button>
              <button
                type="button"
                className="admin-button admin-button-danger"
                disabled={totpDisableMutation.isPending}
                onClick={() => {
                  setDisableCode('');
                  setDisableDialogOpen(true);
                  setMessage(null);
                  setError(null);
                }}
              >
                {totpDisableMutation.isPending ? '停用中...' : '停用 TOTP'}
              </button>
            </div>
          </form>
        </div>
      </section>

      <AdminDialog
        open={disableDialogOpen}
        title="确认停用 TOTP"
        description="请输入当前验证器生成的 6 位验证码。校验通过后会立即停用当前管理员账号的 TOTP。"
        onClose={() => {
          if (totpDisableMutation.isPending) return;
          setDisableDialogOpen(false);
          setDisableCode('');
        }}
        actions={(
          <>
            <button
              type="button"
              className="admin-button admin-button-secondary"
              disabled={totpDisableMutation.isPending}
              onClick={() => {
                setDisableDialogOpen(false);
                setDisableCode('');
              }}
            >
              取消
            </button>
            <button
              type="button"
              className="admin-button admin-button-danger"
              disabled={totpDisableMutation.isPending || disableCode.trim().length < 6}
              onClick={() => {
                const normalizedCode = disableCode.trim();
                if (normalizedCode.length < 6) {
                  setMessage(null);
                  setError('请输入 6 位验证码后再停用 TOTP');
                  return;
                }
                totpDisableMutation.mutate({ code: normalizedCode });
              }}
            >
              {totpDisableMutation.isPending ? '停用中...' : '确认停用'}
            </button>
          </>
        )}
      >
        <label className="admin-dialog-field">
          <span>验证码</span>
          <input
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="123456"
            value={disableCode}
            onChange={(event) => setDisableCode(event.target.value)}
          />
        </label>
      </AdminDialog>

      {message ? <p className="admin-success-text">{message}</p> : null}
      {error ? <p className="admin-error-text">{error}</p> : null}
    </div>
  );
}
