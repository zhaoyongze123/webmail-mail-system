# Webmail 后台一期实现说明

## 当前结果

已完成同站 `/admin` 后台一期基础闭环，包含：

- 独立管理员认证
- 域管理
- 邮箱用户管理
- 别名管理
- 配额管理
- 审计日志
- 系统健康检查
- 基础 Dashboard

本轮交付目标是“后台一期可用后台 + 后端真实基础 API”，不是 `FUNCTION.md` 全量完成。

## 前端结构

- `/` 继续保留现有用户前台
- `/admin/login` 为管理员登录入口
- `/admin/dashboard`
- `/admin/domains`
- `/admin/users`
- `/admin/aliases`
- `/admin/quotas`
- `/admin/audit-logs`
- `/admin/system-health`

前端采用：

- `react-router-dom`
- `@tanstack/react-query`
- `react-hook-form`
- `zod`
- `@tanstack/react-table`

## 后端接口

### 认证

- `POST /api/admin/auth/login`
- `POST /api/admin/auth/refresh`
- `POST /api/admin/auth/logout`
- `GET /api/admin/auth/me`
- `POST /api/admin/auth/change-password`
- `POST /api/admin/auth/totp/setup`
- `POST /api/admin/auth/totp/enable`
- `POST /api/admin/auth/totp/disable`

### 域管理

- `GET /api/admin/domains`
- `POST /api/admin/domains`
- `GET /api/admin/domains/{domain_id}`
- `PATCH /api/admin/domains/{domain_id}`
- `DELETE /api/admin/domains/{domain_id}`
- `POST /api/admin/domains/bulk-status`

### 用户管理

- `GET /api/admin/users`
- `POST /api/admin/users`
- `GET /api/admin/users/{user_id}`
- `PATCH /api/admin/users/{user_id}`
- `DELETE /api/admin/users/{user_id}`
- `POST /api/admin/users/{user_id}/reset-password`
- `POST /api/admin/users/bulk-action`
- `PATCH /api/admin/users/{user_id}/quota`

### 别名与配额

- `GET /api/admin/aliases`
- `POST /api/admin/aliases`
- `GET /api/admin/aliases/{alias_id}`
- `PATCH /api/admin/aliases/{alias_id}`
- `DELETE /api/admin/aliases/{alias_id}`
- `POST /api/admin/aliases/{alias_id}/toggle`
- `GET /api/admin/quotas`
- `PATCH /api/admin/quotas/policy`
- `POST /api/admin/quotas/bulk-update`

### 审计与看板

- `GET /api/admin/audit-logs`
- `GET /api/admin/overview`
- `GET /api/admin/dashboard/trends`
- `GET /api/admin/system-health`

## 环境变量

- `ADMIN_JWT_SECRET`
- `ADMIN_ACCESS_TOKEN_TTL_MINUTES`
- `ADMIN_REFRESH_TOKEN_TTL_DAYS`
- `ADMIN_BOOTSTRAP_USERNAME`
- `ADMIN_BOOTSTRAP_PASSWORD`
- `ADMIN_TOTP_ISSUER`

开发环境默认会在缺省时自动 bootstrap 一个管理员：

- 用户名：`admin`
- 密码：`Admin123456!`

生产环境必须显式设置，不应依赖默认值。

## 当前边界

当前前端已具备的实际操作能力：

- 域名：新增、启停、删除
- 用户：列表查看、修改配额
- 别名：列表查看、启停
- 配额：列表查看与使用率展示

本轮未完成：

- 邮件队列管理
- maillog 检索与实时 tail
- Postfix/Dovecot 配置查看与重载
- 服务启停控制
- 配置回滚
- CSV 批量导入
- 用户/别名/配额的完整表单化 CRUD 交互
- 域详情抽屉、批量操作、筛选排序 UI

## 验证结果

- 后端：admin + auth + health + models 关键回归通过
- 前端：全量 `vitest` 通过，admin 路由测试通过
- 前端构建通过

## 镜像与容器说明

- 后端镜像现在会包含 `alembic.ini` 与 `backend/alembic/` 迁移脚本
- 后端容器启动入口会先执行 `alembic upgrade head`，再启动 `uvicorn`
- 前端镜像现在改为 `npm run build + nginx`，不再依赖 Vite dev server
- `nginx` 会直接托管静态产物，并将 `/api/*` 反向代理到 `backend:8000`
- `/admin/*` 等前端路由通过 SPA 回退到 `index.html`
- 前端代码更新后仍需重建镜像，容器不会自动热更新源码

当前已知事项：

- Vite build 有 chunk size warning，但不影响产物生成
- 现有 `ComposePanel` 测试仍有历史 `act(...)` warning，本轮未新增失败
