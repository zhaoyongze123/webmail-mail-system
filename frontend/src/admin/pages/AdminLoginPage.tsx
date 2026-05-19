import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import { useLocation, useNavigate } from 'react-router-dom';
import { adminLogin } from '../api';
import { useAdminAuth } from '../auth';

const loginSchema = z.object({
  username: z.string().min(1, '请输入管理员账号'),
  password: z.string().min(1, '请输入密码'),
});

type LoginFormValues = z.infer<typeof loginSchema>;

export function AdminLoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { hasToken, syncFromLogin } = useAdminAuth();
  const from = (location.state as { from?: string } | null)?.from || '/admin/dashboard';
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: {
      username: 'admin',
      password: '',
    },
  });

  useEffect(() => {
    if (hasToken) {
      navigate(from, { replace: true });
    }
  }, [from, hasToken, navigate]);

  const onSubmit = handleSubmit(async (values) => {
    try {
      const payload = await adminLogin(values);
      syncFromLogin(payload);
      navigate(from, { replace: true });
    } catch (error) {
      const typedError = error as Error & { code?: string };
      setError('password', {
        type: 'server',
        message: typedError.message || '登录失败',
      });
    }
  });

  return (
    <div className="admin-auth-page">
      <form className="admin-auth-card" onSubmit={onSubmit}>
        <div>
          <p className="admin-auth-kicker">Webmail Admin</p>
          <h1>管理员登录</h1>
          <p>使用后台管理员账号登录，开发环境默认账号为 `admin`。</p>
        </div>
        <label>
          <span>管理员账号</span>
          <input type="text" autoComplete="username" placeholder="admin" {...register('username')} />
          {errors.username ? <em>{errors.username.message}</em> : null}
        </label>
        <label>
          <span>密码</span>
          <input type="password" autoComplete="current-password" placeholder="请输入密码" {...register('password')} />
          {errors.password ? <em>{errors.password.message}</em> : null}
        </label>
        <button type="submit" className="admin-button admin-button-primary" disabled={isSubmitting}>
          {isSubmitting ? '登录中...' : '登录后台'}
        </button>
      </form>
    </div>
  );
}
