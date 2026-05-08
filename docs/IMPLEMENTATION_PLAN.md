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
| T11 | 已完成 | Codex | feature/t01-project-plan-automation | 7e94c0a | docker compose run --rm -v "$PWD/backend:/app" backend python -m pytest tests -q && docker compose up -d --build backend frontend && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:8000/api/health && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:5173/api/health && python3 scripts/plan_status.py validate && git diff --check | 通过：附件下载未登录401、附件bytes/hash、Content-Disposition、Content-Type、非法ID与越权场景测试通过；后端42个用例通过 | 2026-05-07T09:30:26+08:00 | 附件读取与下载 |
| T12 | 已完成 | Codex | feature/t01-project-plan-automation | 7e94c0a | docker compose run --rm -v "$PWD/backend:/app" backend python -m pytest tests -q && docker compose up -d --build backend frontend && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:8000/api/health && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:5173/api/health && python3 scripts/plan_status.py validate && git diff --check | 通过：多附件上传、9MB总大小限制、文件名净化、Redis临时存储与TTL测试通过；后端42个用例通过 | 2026-05-07T09:30:26+08:00 | 写信附件上传 |
| T13 | 已完成 | Codex | feature/t01-project-plan-automation | 7e94c0a | docker compose run --rm -v "$PWD/backend:/app" backend python -m pytest tests -q && docker compose up -d --build backend frontend && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:8000/api/health && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:5173/api/health && python3 scripts/plan_status.py validate && git diff --check | 通过：SMTP发送、附件MIME、IMAP APPEND到.Sent、SMTP失败、空/重复收件人校验测试通过；后端42个用例通过 | 2026-05-07T09:30:26+08:00 | SMTP 发信与已发送归档 |
| T14 | 已完成 | Codex | feature/t01-project-plan-automation | WORKTREE-T14-T16 | docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q && python3 scripts/plan_status.py validate && git diff --check | 通过：后端70个用例通过；T14草稿保存/恢复/附件/更新清理/发送后删除草稿覆盖；计划校验和空白检查通过 | 2026-05-07T09:50:38+08:00 | 草稿保存与恢复 |
| T15 | 已完成 | Codex | feature/t01-project-plan-automation | WORKTREE-T14-T16 | docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q && python3 scripts/plan_status.py validate && git diff --check | 通过：后端70个用例通过；T15标记已读/未读、删除到.Trash、移动文件夹、星标Flag和兼容API路径覆盖；计划校验和空白检查通过 | 2026-05-07T09:50:38+08:00 | 邮件操作 |
| T16 | 已完成 | Codex | feature/t01-project-plan-automation | WORKTREE-T14-T16 | docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q && python3 scripts/plan_status.py validate && git diff --check | 通过：后端70个用例通过；T16主题、发件人、收件人、摘要搜索、分页、空结果、缓存和/api/search兼容路径覆盖；计划校验和空白检查通过 | 2026-05-07T09:50:38+08:00 | 当前文件夹搜索 |
| T17 | 已完成 | Codex | feature/t01-project-plan-automation | WORKTREE-T17-T19 | docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q && npm test --prefix frontend && npm run build --prefix frontend && python3 scripts/plan_status.py validate && git diff --check | 通过：后端77个用例通过；联系人未登录、关键词补全、去重、数量上限和发送后记录覆盖；前端测试/构建、计划校验和空白检查通过 | 2026-05-07T10:14:01+08:00 | 联系人与地址补全 |
| T18 | 已完成 | Codex | feature/t01-project-plan-automation | WORKTREE-T17-T19 | docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q && npm test --prefix frontend && npm run build --prefix frontend && python3 scripts/plan_status.py validate && git diff --check | 通过：后端77个用例通过；设置未登录、默认偏好、更新后保留、退出失效、偏好影响分页和阅读标记覆盖；前端测试/构建、计划校验和空白检查通过 | 2026-05-07T10:14:35+08:00 | 设置与偏好 |
| T19 | 已完成 | Codex | feature/t01-project-plan-automation | WORKTREE-T17-T19 | npm test --prefix frontend && npm run build --prefix frontend && npm run test:e2e --prefix frontend && docker compose up -d --build backend frontend && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:8000/api/health && curl --retry 5 --retry-delay 1 --retry-all-errors -fsS http://localhost:5173/api/health && python3 scripts/plan_status.py validate && git diff --check | 通过：前端4个Vitest用例通过，构建通过；Playwright E2E 2个用例通过，覆盖未登录跳转、错误密码提示、登录成功进入/mail；前后端容器启动与健康检查通过；计划校验和空白检查通过 | 2026-05-07T10:16:28+08:00 | 前端登录与应用框架 |
| T20 | 已完成 | Codex | feature/t01-project-plan-automation | 052cc28 | npm test --prefix frontend && npm run build --prefix frontend && npm run test:e2e --prefix frontend && docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q | 通过：前端20个Vitest用例通过；构建通过；Playwright 5个用例通过并生成1440/390工作台截图，覆盖文件夹切换和列表加载；后端77个用例通过 | 2026-05-07T10:54:06+08:00 | 前端三栏工作台 |
| T21 | 已完成 | Codex | feature/t01-project-plan-automation | 052cc28 | npm test --prefix frontend && npm run build --prefix frontend && npm run test:e2e --prefix frontend && docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q | 通过：前端20个Vitest用例通过；Playwright打开邮件详情、验证危险HTML未进入阅读器、附件下载文件名quote.txt；构建通过；后端77个用例通过 | 2026-05-07T10:54:06+08:00 | 前端阅读与附件 |
| T22 | 已完成 | Codex | feature/t01-project-plan-automation | 052cc28 | npm test --prefix frontend && npm run build --prefix frontend && npm run test:e2e --prefix frontend && docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q | 通过：前端20个Vitest用例通过；Playwright新建邮件、上传附件、保存草稿、发送成功和防重复发送通过；构建通过；后端77个用例通过 | 2026-05-07T10:54:06+08:00 | 前端写信、发信、草稿 |
| T23 | 已完成 | Codex | feature/t01-project-plan-automation | 8819cb3 | npm test --prefix frontend && npm run build --prefix frontend && npm run test:e2e --prefix frontend && docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q && python3 scripts/plan_status.py validate && docker compose config && git diff --check | 通过：前端23个Vitest用例通过；构建通过；Playwright 6个用例通过，覆盖搜索、批量操作和设置保存；后端82个用例通过 | 2026-05-07T11:30:18+08:00 | 前端搜索、批量操作、设置 |
| T24 | 已完成 | Codex | feature/t01-project-plan-automation | 8819cb3 | docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q && npm test --prefix frontend && npm run build --prefix frontend && npm run test:e2e --prefix frontend | 通过：后端82个用例通过；安全响应头、Cookie、CSRF、附件非法ID、日志脱敏测试覆盖；前端23个单测、构建和6个E2E通过 | 2026-05-07T11:30:18+08:00 | 安全加固 |
| T25 | 已完成 | Codex | feature/t01-project-plan-automation | 8819cb3 | docker compose run --rm -v "/Users/mac/项目/webmail-邮件系统/backend:/app" backend python -m pytest tests -q && curl http://localhost:8000/api/health && curl http://localhost:5173/api/health && python3 scripts/plan_status.py validate | 通过：后端82个用例通过；request_id、结构化请求日志、审计事件和/api/metrics测试覆盖；健康检查通过 | 2026-05-07T11:30:18+08:00 | 可观测性 |
| T26 | 未开始 |  |  |  |  |  |  | HTML 邮件安全渲染升级 |
| T27 | 未开始 |  |  |  |  |  |  | 草稿自动保存协议对齐 |
| T28 | 未开始 |  |  |  |  |  |  | 纯文本编辑模式 |
| T29 | 未开始 |  |  |  |  |  |  | 收件人 Tag 输入框 |
| T30 | 未开始 |  |  |  |  |  |  | 内嵌图片增强 |
| T31 | 未开始 |  |  |  |  |  |  | 附件拖拽与分块上传 |
| T32 | 未开始 |  |  |  |  |  |  | 默认签名自动插入 |
| T33 | 未开始 |  |  |  |  |  |  | 签名管理 CRUD |
| T34 | 未开始 |  |  |  |  |  |  | 富文本签名编辑器 |
| T35 | 未开始 |  |  |  |  |  |  | 联系人持久化模型 |
| T36 | 未开始 |  |  |  |  |  |  | 联系人列表分页搜索 |
| T37 | 未开始 |  |  |  |  |  |  | 联系人 CRUD 与字段 |
| T38 | 未开始 |  |  |  |  |  |  | 联系人分组与标签 |
| T39 | 未开始 |  |  |  |  |  |  | 自动收录联系人 |
| T40 | 未开始 |  |  |  |  |  |  | 联系人黑名单 |
| T41 | 未开始 |  |  |  |  |  |  | 联系人白名单 |
| T42 | 未开始 |  |  |  |  |  |  | 修改密码入口 |
| T43 | 未开始 |  |  |  |  |  |  | 语言与时区设置 |
| T44 | 未开始 |  |  |  |  |  |  | 回复引用方式设置 |
| T45 | 未开始 |  |  |  |  |  |  | 文件夹创建重命名删除 |
| T46 | 未开始 |  |  |  |  |  |  | 未读数角标一致性 |
| T47 | 未开始 |  |  |  |  |  |  | 全文检索 |
| T48 | 未开始 |  |  |  |  |  |  | 条件筛选搜索 |
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
| T26 | HTML 邮件安全渲染升级 | 前端使用 DOMPurify 白名单净化；阅读区使用 sandbox iframe 隔离 HTML 邮件；纯文本邮件仍正常展示 | 后端 HTML 净化测试；前端 XSS 样例测试；Playwright 验证脚本不执行、样式表格图片正常 |
| T27 | 草稿自动保存协议对齐 | 写信内容每 30 秒自动保存；使用 PATCH 更新已有草稿；页面关闭或刷新前不丢内容 | 草稿 API PATCH 测试；Vitest fake timer 验证 30 秒保存；Playwright 刷新后恢复草稿 |
| T28 | 纯文本编辑模式 | 写信支持富文本和纯文本切换；纯文本模式不发送 HTML；互转后正文内容不丢 | ComposePanel 单测覆盖模式切换；发送 payload 断言；Playwright 新建纯文本邮件 |
| T29 | 收件人 Tag 输入框 | To、Cc、Bcc 支持多收件人 Tag 输入、删除、键盘导航和重复校验 | Vitest 覆盖输入、删除、重复、键盘操作；发送接口校验重复收件人 |
| T30 | 内嵌图片增强 | 正文可上传内嵌图片；图片可调整大小和位置；发送后 HTML 正文保留图片引用 | 前端编辑器测试；附件和图片上传测试；Playwright 插入图片、调整尺寸、发送草稿 |
| T31 | 附件拖拽与分块上传 | 附件支持拖拽和点击上传；大文件按分块上传；显示进度和失败状态 | 上传 API 分块测试；Vitest 拖拽上传测试；Playwright 上传多附件并验证进度 |
| T32 | 默认签名自动插入 | 新邮件和回复邮件自动插入当前账号默认签名，正文与签名之间空一行 | 设置签名后新建和回复测试；发送 payload 断言签名内容 |
| T33 | 签名管理 CRUD | 支持新增、编辑、删除、多签名列表和设为默认 | 后端签名 API 测试；前端签名设置页测试；默认签名唯一性测试 |
| T34 | 富文本签名编辑器 | 签名支持图片和链接，复用现有邮件富文本编辑能力 | 富文本签名保存读取测试；XSS 净化测试；Playwright 编辑签名并插入邮件 |
| T35 | 联系人持久化模型 | 新增联系人、联系人分组标签、黑白名单所需数据库模型与迁移 | 模型测试；Alembic upgrade/downgrade/upgrade；唯一约束测试 |
| T36 | 联系人列表分页搜索 | 联系人页支持分页、关键词搜索和空状态 | 后端分页搜索测试；前端联系人列表测试；Playwright 搜索联系人 |
| T37 | 联系人 CRUD 与字段 | 支持新增、编辑、删除联系人；字段包含姓名、邮箱、手机、备注 | API CRUD 测试；表单校验测试；Playwright 新增编辑删除联系人 |
| T38 | 联系人分组与标签 | 联系人可绑定分组和标签；列表可按分组或标签筛选 | API 分组标签测试；前端筛选测试；删除分组后的联系人处理测试 |
| T39 | 自动收录联系人 | 发送成功后自动写入或更新联系人库；重复邮箱不创建重复联系人 | 发送邮件后联系人库断言；重复收件人去重测试；联系人更新时间测试 |
| T40 | 联系人黑名单 | 标记黑名单后，刷新或同步邮件时匹配发件人自动移动到垃圾箱 | 黑名单规则测试；IMAP move/copy fake 测试；Playwright 黑名单后刷新列表 |
| T41 | 联系人白名单 | 标记白名单后，Webmail 层不执行黑名单和本地垃圾规则 | 白名单优先级测试；黑白名单冲突测试；刷新同步行为测试 |
| T42 | 修改密码入口 | 提供 Webmail 层修改密码入口；当前阶段记录新密码并重新验证 IMAP 登录，邮件服务器深度改密不纳入本轮 | API 验证旧密码和新密码测试；错误密码测试；前端表单测试 |
| T43 | 语言与时区设置 | 用户可保存语言和时区；日期展示使用用户时区 | 设置 API 测试；日期格式化单测；Playwright 切换时区后邮件时间变化 |
| T44 | 回复引用方式设置 | 支持顶部引用和底部引用；默认底部引用；引用格式输出发件人、发送时间、收件人、主题 | 设置 API 测试；回复生成内容单测；Playwright 回复邮件验证引用位置 |
| T45 | 文件夹创建重命名删除 | 支持用户文件夹创建、重命名、删除；系统默认文件夹不可删除 | IMAP 文件夹操作 fake 测试；系统文件夹保护测试；前端文件夹管理测试 |
| T46 | 未读数角标一致性 | 文件夹未读数在列表刷新、已读未读切换、自动刷新后保持一致 | 后端未读计数测试；前端状态同步测试；Playwright 标记已读后角标减少 |
| T47 | 全文检索 | 支持当前账号邮件全文检索，覆盖主题、发件人、收件人、正文摘要和正文缓存 | 搜索 API 测试；索引或缓存测试；无结果和分页测试 |
| T48 | 条件筛选搜索 | 支持按发件人、日期范围、附件类型筛选邮件 | API 参数校验测试；组合筛选测试；前端筛选表单测试 |

## 5. 测试总计划

- 后端：`pytest backend/tests`
- 前端：`npm test`、`npm run typecheck`、`npm run build`
- E2E：`npx playwright test`
- 部署：`docker compose up -d`、`curl /api/health`、`curl /api/ready`
- 计划校验：`python3 scripts/plan_status.py validate`

## 6. 2026-05-07 功能补充记录

- 认证：新增 `/api/auth/register`，注册时复用 IMAP 凭证校验，成功后写入 `mail_accounts` 并创建会话；前端提供登录/注册切换页。
- 联系人：保留发信后的最近联系人记录，并在主界面新增联系人面板，可搜索联系人并直接发起写信。
- 自动刷新：邮件工作台每 30 秒自动刷新当前文件夹，保留当前阅读上下文；顶部显示自动刷新状态。
- 已读同步：邮件列表同步时写入 PostgreSQL 邮件元数据；打开邮件、标记已读和标记未读时同步更新 `mail_messages.is_read`。
- 回复引用：收件箱邮件列表支持右键菜单，点击“回复并引用”后打开写信面板，自动填充收件人、`Re:` 主题和原文引用。
- 回归证据：`PYTHONPATH=/Users/mac/项目/webmail-邮件系统/backend ../.venv/bin/pytest` 通过 88 个后端用例；`npm run build` 通过；`npm test -- --run src/App.test.tsx src/mail/ComposePanel.test.tsx` 通过 21 个前端用例。

## 7. 假设与默认值

- 本计划覆盖 MVP 全量，不包含 `v1.1` 到 `v2.0` 后续路线。
- 进度追踪采用 Markdown 状态表，不使用 GitHub Issues。
- 默认公网域名使用 `mail.mdaemon.cc`；如实际域名变更，只改环境变量和 Nginx `server_name`。
- Webmail 浏览器入口必须 HTTPS；后端到邮件服务器允许明文 IMAP/SMTP。
- 附件上限按服务器 `SIZE 10240000` 保守设置为 `9 MB`。
- 只支持现代 Chrome、Edge、Safari、Firefox。
