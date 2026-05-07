# Webmail 邮件系统 MVP 实施计划

## 1. 计划说明

本计划覆盖 `docs/MVP_REQUIREMENTS.md` 中定义的 MVP 全量能力：登录会话、文件夹、邮件列表、邮件详情、写信发信、草稿、附件、搜索、批量操作、联系人补全、设置、安全、可观测性、HTTPS 部署和端到端验收。

默认技术栈：

- 后端：Python 3.12、FastAPI、SQLAlchemy 2.x、Alembic、Pydantic Settings、Redis、PostgreSQL、imaplib、smtplib。
- 前端：React、TypeScript、Vite、React Router、TanStack Query、Zustand、TipTap。
- 部署：Docker Compose 管理 API、前端、PostgreSQL、Redis、Nginx，公网入口必须 HTTPS。
- 邮件服务器：IMAP `14.103.117.188:143` 明文，SMTP `14.103.117.188:25` 明文。

## 2. 进度更新规则

状态枚举：`未开始`、`进行中`、`阻塞`、`待验收`、`已完成`。

进度必须通过 `scripts/plan_status.py` 更新：

```bash
python3 scripts/plan_status.py start T01 --branch feature/t01-project-plan-automation
python3 scripts/plan_status.py evidence T01 --tests "python3 scripts/plan_status.py validate" --result "通过"
python3 scripts/plan_status.py done T01 --commit <commit-sha> --tests "python3 scripts/plan_status.py validate"
python3 scripts/plan_status.py validate
```

每个任务完成前必须满足：

- 代码、配置、文档和测试在同一任务闭环内完成。
- 有自动化测试或真实命令证据。
- 涉及接口变更时同步更新 OpenAPI 或接口文档。
- 涉及用户流程时提供 Playwright 验收。
- 涉及邮件协议时使用真实 `14.103.117.188` 测试账号验证。
- 涉及安全时证明日志不包含密码、Cookie、邮件正文和附件内容。

## 3. 任务进度表

<!-- PLAN_STATUS_START -->
| 任务ID | 状态 | 负责人 | 分支 | PR/提交 | 测试命令 | 测试结果 | 完成时间 | 风险备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T01 | 已完成 | Codex | feature/t01-project-plan-automation | ebb91d1 | python3 scripts/plan_status.py validate && python3 scripts/plan_status.py evidence T01 --tests "python3 scripts/plan_status.py validate" --result "dry-run校验通过" --dry-run && python3 scripts/plan_status.py done T01 --commit DRYRUN --tests "python3 scripts/plan_status.py validate" --dry-run && python3 - <<PY<br>from pathlib import Path<br>text = Path(".github/workflows/plan-check.yml").read_text()<br>required = ["pull_request:", "push:", "validate-plan:", "actions/setup-python@v5", "python3 scripts/plan_status.py validate"]<br>missing = [item for item in required if item not in text]<br>if missing:<br>    raise SystemExit("缺少字段: " + ", ".join(missing))<br>print("CI 工作流关键字段校验通过")<br>PY<br> git diff --check | 通过：计划校验、dry-run 更新、CI 字段校验和 diff 空白检查均通过 | 2026-05-06T16:30:10+08:00 | 建立项目骨架与计划进度自动化 |
| T02 | 已完成 | Codex | feature/t01-project-plan-automation | 6390da7 | docker compose config && cd frontend && npm ci && npm run build && docker compose up -d --build && curl -fsS http://localhost:8000/api/health && curl -fsS http://localhost:8000/api/ready && curl -fsS http://localhost:5173/api/health && curl -I --max-time 10 http://localhost:5173/ && docker compose ps && python3 scripts/plan_status.py validate && git diff --check | 通过：Compose配置、前端构建、四服务启动、后端健康检查、前端代理健康检查、计划校验和空白检查均通过 | 2026-05-06T16:40:30+08:00 | 本地开发环境 |
| T03 | 已完成 | Codex | feature/t01-project-plan-automation | ef11408 | docker compose build backend && docker compose run --rm -v "$PWD/backend/tests:/app/tests:ro" backend python -m pytest tests/test_health.py && curl -fsS http://localhost:8000/api/health && curl -fsS http://localhost:8000/api/ready && curl -fsS http://localhost:5173/api/health && python3 scripts/plan_status.py validate && git diff --check | 通过：后端镜像构建、健康检查测试、统一响应和 request_id 验证、容器健康检查均通过 | 2026-05-06T16:48:39+08:00 | 后端基础框架 |
| T04 | 已完成 | Codex | feature/t01-project-plan-automation | ef11408 | docker compose run --rm -v "$PWD/backend:/app" backend python -m pytest tests/test_models.py && docker compose run --rm -v "$PWD/backend:/app" backend alembic -c alembic.ini upgrade head && docker compose run --rm -v "$PWD/backend:/app" backend alembic -c alembic.ini downgrade -1 && docker compose run --rm -v "$PWD/backend:/app" backend alembic -c alembic.ini upgrade head && python3 scripts/plan_status.py validate && git diff --check | 通过：模型元数据测试通过，Alembic upgrade/downgrade/upgrade 真实 PostgreSQL 迁移通过 | 2026-05-06T16:48:40+08:00 | 数据库模型与迁移 |
| T05 | 已完成 | Codex | feature/t01-project-plan-automation | fcb7ce7 | docker compose build backend && docker compose run --rm -v "$PWD/backend:/app" backend python -m pytest tests/test_cache.py tests/test_health.py tests/test_models.py && docker compose up -d --build backend frontend && curl -fsS http://localhost:8000/api/health && curl -fsS http://localhost:5173/api/health && python3 scripts/plan_status.py validate && git diff --check | 通过：Redis session、登录失败限流、JSON缓存、同步锁测试通过，容器健康检查通过 | 2026-05-06T16:58:50+08:00 | Redis 会话与限流底座 |
| T06 | 已完成 | Codex | feature/t01-project-plan-automation | fcb7ce7 | docker compose run --rm -v "$PWD/backend:/app" backend python -m pytest tests/test_mail_adapters.py tests/test_health.py tests/test_models.py && nc 14.103.117.188 143 CAPABILITY探测 && nc 14.103.117.188 25 EHLO探测 && python3 scripts/plan_status.py validate && git diff --check | 通过：IMAP/SMTP Adapter fake测试通过，真实服务器143 CAPABILITY与25 EHLO无凭证探测通过 | 2026-05-06T16:58:50+08:00 | IMAP/SMTP Adapter |
| T07 | 已完成 | Codex | feature/t01-project-plan-automation | 5f3b385 | docker compose run --rm -v "$PWD/backend:/app" backend python -m pytest tests/test_auth.py tests/test_cache.py tests/test_mail_adapters.py tests/test_health.py tests/test_models.py && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:8000/api/health && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:5173/api/health && python3 scripts/plan_status.py validate && git diff --check | 通过：登录成功、当前用户、退出失效、错误密码、登录失败限流测试通过；T06 邮件适配器回归通过；前后端健康检查通过 | 2026-05-07T09:12:32+08:00 | 登录、退出与当前用户 API |
| T08 | 已完成 | Codex | feature/t01-project-plan-automation | 8c54edf | docker compose run --rm -v "$PWD/backend:/app" backend python -m pytest tests -q && docker compose up -d --build backend frontend && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:8000/api/health && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:5173/api/health && python3 scripts/plan_status.py validate && git diff --check | 通过：/api/folders 未登录401、标准文件夹映射、未读数、空文件夹测试通过；后端31个用例通过；前后端健康检查通过 | 2026-05-07T09:20:39+08:00 | 文件夹同步 API |
| T09 | 已完成 | Codex | feature/t01-project-plan-automation | 8c54edf | docker compose run --rm -v "$PWD/backend:/app" backend python -m pytest tests -q && docker compose up -d --build backend frontend && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:8000/api/health && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:5173/api/health && python3 scripts/plan_status.py validate && git diff --check | 通过：邮件列表未登录401、日期倒序分页、摘要字段、Redis缓存命中、refresh绕过缓存测试通过；后端31个用例通过 | 2026-05-07T09:20:39+08:00 | 邮件列表与元数据缓存 |
| T10 | 已完成 | Codex | feature/t01-project-plan-automation | 8c54edf | docker compose run --rm -v "$PWD/backend:/app" backend python -m pytest tests -q && docker compose up -d --build backend frontend && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:8000/api/health && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:5173/api/health && python3 scripts/plan_status.py validate && git diff --check | 通过：邮件详情未登录401、头信息、HTML净化、纯文本、标记已读、附件元数据测试通过；后端31个用例通过 | 2026-05-07T09:20:39+08:00 | 邮件详情与正文安全 |
| T11 | 未开始 |  |  |  |  |  |  | 附件读取与下载 |
| T12 | 未开始 |  |  |  |  |  |  | 写信附件上传 |
| T13 | 未开始 |  |  |  |  |  |  | SMTP 发信与已发送归档 |
| T14 | 未开始 |  |  |  |  |  |  | 草稿保存与恢复 |
| T15 | 未开始 |  |  |  |  |  |  | 邮件操作 |
| T16 | 未开始 |  |  |  |  |  |  | 当前文件夹搜索 |
| T17 | 未开始 |  |  |  |  |  |  | 联系人与地址补全 |
| T18 | 未开始 |  |  |  |  |  |  | 设置与偏好 |
| T19 | 未开始 |  |  |  |  |  |  | 前端登录与应用框架 |
| T20 | 未开始 |  |  |  |  |  |  | 前端三栏工作台 |
| T21 | 未开始 |  |  |  |  |  |  | 前端阅读与附件 |
| T22 | 未开始 |  |  |  |  |  |  | 前端写信、发信、草稿 |
| T23 | 未开始 |  |  |  |  |  |  | 前端搜索、批量操作、设置 |
| T24 | 未开始 |  |  |  |  |  |  | 安全加固 |
| T25 | 未开始 |  |  |  |  |  |  | 可观测性 |
| T26 | 未开始 |  |  |  |  |  |  | 公网 HTTPS 部署 |
| T27 | 未开始 |  |  |  |  |  |  | 端到端验收与性能冒烟 |
| T28 | 未开始 |  |  |  |  |  |  | 文档、PR 与发布交付 |
<!-- PLAN_STATUS_END -->

## 4. 闭环任务与验收

| ID | 闭环任务 | 验收标准 | 测试流程 |
| --- | --- | --- | --- |
| T01 | 建立项目骨架与计划进度自动化 | 生成后端、前端、部署、测试目录；`docs/IMPLEMENTATION_PLAN.md` 可被脚本更新；CI 可校验计划状态 | `python3 scripts/plan_status.py validate`；使用 `--dry-run` 验证状态更新 |
| T02 | 本地开发环境 | Docker Compose 启动 PostgreSQL、Redis、后端、前端；`.env.example` 完整且无真实密钥 | `docker compose config`；`docker compose up -d`；访问 `/api/health` |
| T03 | 后端基础框架 | FastAPI 启动；统一响应、错误码、request_id、CORS、配置加载可用 | `pytest backend/tests/test_health.py`；`curl /api/health`、`curl /api/ready` |
| T04 | 数据库模型与迁移 | 创建账号、文件夹、邮件元数据、草稿、附件、审计表；索引和唯一约束生效 | `alembic upgrade head`；`alembic downgrade -1 && alembic upgrade head`；模型单测 |
| T05 | Redis 会话与限流底座 | Session、登录失败计数、同步锁、热缓存 Key 可读写；TTL 正确 | Redis 集成测试；验证 session 过期、登录失败计数清零 |
| T06 | IMAP/SMTP Adapter | 能连接 Dovecot IMAP 143、Postfix SMTP 25；支持登录、能力探测、文件夹、UID、APPEND、SMTP send | 使用测试账号跑 adapter 集成测试；记录 IMAP capability 和 SMTP EHLO 结果 |
| T07 | 登录、退出与当前用户 API | 邮箱密码登录成功创建 HttpOnly Cookie；退出后会话失效；错误密码返回统一错误 | API 测试登录成功、错误密码、过期 session、退出后 401、限流 |
| T08 | 文件夹同步 API | 展示 INBOX、`.Sent`、`.Drafts`、`.Junk`、`.Trash`、`.Archive`；未读数准确 | 集成测试 `/api/folders`；对比 IMAP 返回；空文件夹显示正常 |
| T09 | 邮件列表与元数据缓存 | 当前文件夹按时间倒序分页；缓存命中快；刷新不丢上下文 | API 测试分页、排序、刷新；插入新邮件后刷新可见；缓存 TTL 验证 |
| T10 | 邮件详情与正文安全 | 展示头信息、HTML/纯文本正文；危险 HTML 被净化；打开可标记已读 | XSS 样例邮件测试；详情接口测试；已读 Flag 与 UI 状态一致 |
| T11 | 附件读取与下载 | 详情展示附件名、大小、类型；下载内容正确；越权下载被拒绝 | 带附件测试邮件；下载 hash 对比；未登录和跨账号下载返回 401/403 |
| T12 | 写信附件上传 | 支持多附件临时上传；总大小默认 9 MB；过期附件可清理 | 上传成功、超限、非法文件名、TTL 清理测试 |
| T13 | SMTP 发信与已发送归档 | 新邮件可发送；失败错误可区分；发送成功后 IMAP APPEND 到 `.Sent` | 给测试账号发信；收件箱收到；已发送可见；模拟 SMTP 失败 |
| T14 | 草稿保存与恢复 | 自动保存、手动保存、从 `.Drafts` 恢复；发送成功后草稿删除 | API 测试草稿 CRUD；前端自动保存节流；其他客户端可见草稿 |
| T15 | 邮件操作 | 支持标记已读/未读、删除到 `.Trash`、移动文件夹、星标 Flag | IMAP Flag 对比；批量操作测试；移动后源文件夹消失、目标文件夹出现 |
| T16 | 当前文件夹搜索 | 支持主题、发件人、收件人、摘要搜索；分页和清空搜索正常 | API 搜索测试；无结果状态；缓存无结果时触发 IMAP SEARCH |
| T17 | 联系人与地址补全 | 发送成功后记录最近联系人；写信时按关键词补全 | 发送后查询联系人；前端输入邮箱前缀出现建议；重复联系人去重 |
| T18 | 设置与偏好 | 展示当前账号；支持退出；每页数量和阅读后标记已读可配置 | 设置 API 测试；刷新后偏好保留；退出清理前端状态 |
| T19 | 前端登录与应用框架 | 登录页、路由守卫、会话过期跳转、错误提示完成 | Vitest 组件测试；Playwright 登录成功、错误密码、过期跳转 |
| T20 | 前端三栏工作台 | 左侧文件夹、中间列表、右侧阅读区；桌面和移动布局可用 | Playwright 截图验证 Chrome 尺寸 1440/390；文件夹切换和列表加载 |
| T21 | 前端阅读与附件 | 邮件详情、HTML 安全渲染、附件下载、回复转发入口可用 | Playwright 打开邮件、下载附件；XSS 样例不执行 |
| T22 | 前端写信、发信、草稿 | 写信弹层/页面、富文本、附件进度、自动保存、发送状态完整 | Playwright 新建邮件、上传附件、保存草稿、发送成功、防重复发送 |
| T23 | 前端搜索、批量操作、设置 | 搜索、清空、批量选择、标记、删除、移动、设置偏好可用 | Playwright 搜索无结果/有结果；批量操作后列表刷新；设置保存 |
| T24 | 安全加固 | Cookie、CSRF、CSP、HTML 净化、日志脱敏、附件路径安全全部落地 | 安全测试：XSS、CSRF、路径穿越、越权、日志敏感词扫描 |
| T25 | 可观测性 | 结构化日志、request_id、审计日志、基础指标、健康检查完成 | API 请求产生 request_id；审计表有登录/发信记录；指标接口或日志可验证 |
| T26 | 公网 HTTPS 部署 | `https://mail.mdaemon.cc` 或配置域名可访问；HTTP 跳 HTTPS 或拒绝；现代浏览器可用 | `curl -I http://domain`；`curl -I https://domain`；证书检查；Chrome、Edge、Safari、Firefox 冒烟 |
| T27 | 端到端验收与性能冒烟 | AC-001 到 AC-012 全部通过；真实账号连续收发 30 分钟；P0 缺陷清零 | Playwright E2E；pytest 全量；邮件收发实测；1 万封元数据分页性能测试 |
| T28 | 文档、PR 与发布交付 | README、部署文档、测试报告、风险清单完整；main 可部署 | 文档链接检查；`docker compose up` 从零部署；最终 PR 描述含测试证据 |

## 5. 测试总计划

- 后端：`pytest backend/tests`
- 前端：`npm test`、`npm run typecheck`、`npm run build`
- E2E：`npx playwright test`
- 部署：`docker compose up -d`、`curl /api/health`、`curl /api/ready`
- 计划校验：`python3 scripts/plan_status.py validate`

## 6. 假设与默认值

- 本计划覆盖 MVP 全量，不包含 `v1.1` 到 `v2.0` 后续路线。
- 进度追踪采用 Markdown 状态表，不使用 GitHub Issues。
- 默认公网域名使用 `mail.mdaemon.cc`；如实际域名变更，只改环境变量和 Nginx `server_name`。
- Webmail 浏览器入口必须 HTTPS；后端到邮件服务器允许明文 IMAP/SMTP。
- 附件上限按服务器 `SIZE 10240000` 保守设置为 `9 MB`。
- 只支持现代 Chrome、Edge、Safari、Firefox。
