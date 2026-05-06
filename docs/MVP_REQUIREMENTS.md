# Webmail 邮件系统 MVP 需求文档

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档名称 | Webmail 邮件系统 MVP 需求文档 |
| 版本 | v1.0 |
| 日期 | 2026-05-06 |
| 产品阶段 | MVP 概念验证与可用性验证 |
| 目标读者 | 产品、设计、前端、后端、测试、运维、业务负责人 |
| 技术栈 | Python FastAPI、React、PostgreSQL、Redis、imaplib、smtplib |
| 邮件服务器 | `14.103.117.188` |
| 参考产品 | `http://14.103.117.188:3000/`，当前响应头显示服务端为 RainLoop |

## 2. 背景与目标

### 2.1 项目背景

企业已有邮箱服务器，需要快速搭建一款现代化 Web 邮件客户端，使用户可以通过浏览器访问现有邮箱，完成基础的收件、读件、写信、发信和邮件管理。MVP 的核心不是替代完整企业邮箱套件，而是在有限周期内验证以下问题：

1. 通过 IMAP 和 SMTP 连接现有邮箱服务器的技术可行性。
2. Web 单页体验是否能满足日常基础办公邮件需求。
3. 邮件列表、正文读取、附件、发信和草稿等关键链路的稳定性。
4. FastAPI、React、PostgreSQL、Redis 的组合是否适合后续扩展成企业级产品。

### 2.2 产品目标

| 目标编号 | 产品目标 | 成功标准 |
| --- | --- | --- |
| G1 | 用户可以使用现有邮箱账号登录 Webmail | 正确账号可登录，错误账号返回明确错误，登录耗时 P95 小于 3 秒 |
| G2 | 用户可以查看邮箱文件夹和邮件列表 | 收件箱、已发送、草稿、垃圾邮件、已删除、归档可展示，列表分页稳定 |
| G3 | 用户可以打开并阅读邮件 | 正文、发件人、收件人、时间、附件信息展示正确 |
| G4 | 用户可以发送新邮件 | SMTP 发信成功后进入已发送邮件，可处理失败提示 |
| G5 | 用户可以保存和继续编辑草稿 | 未发送内容可保存，刷新页面后仍可恢复 |
| G6 | 系统具备企业级安全底线 | 会话隔离、密码不明文持久化、HTML 邮件安全净化、基础审计可用 |
| G7 | 系统具备可运维性 | 有健康检查、结构化日志、关键指标和错误追踪 |

### 2.3 MVP 原则

1. 先完成邮件收发闭环，再扩展高级协作能力。
2. 优先保证安全、稳定、可观测，不追求一次性覆盖所有邮箱客户端功能。
3. 前端交互参考 RainLoop 的三栏工作台结构，但视觉与代码独立实现。
4. 邮件正文、附件、登录凭证属于敏感数据，所有设计默认按企业数据处理。
5. IMAP 和 SMTP 适配层必须可替换，避免业务逻辑与协议细节耦合。

## 3. 参考页面调研结论

### 3.1 参考页面证据

通过访问 `http://14.103.117.188:3000/` 得到以下事实：

1. HTTP 响应状态为 `200 OK`。
2. 响应头 `Server` 为 `RainLoop`。
3. 页面 `AppBootData` 语言为 `zh_CN`。
4. 页面模板包含 `Login`、`MailFolderList`、`MailMessageList`、`MailMessageView`、`PopupsCompose`、`ComposeAttachment`、`SettingsContacts`、`SettingsFolders`、`PopupsAdvancedSearch` 等模块。
5. 用户提供截图显示已登录态下采用左侧文件夹、中间邮件列表、右侧阅读区的三栏布局，顶部有新信、刷新、文件夹、归档、警告、删除、菜单和用户入口。

### 3.2 对 MVP 的借鉴

| 参考能力 | MVP 采用策略 | 说明 |
| --- | --- | --- |
| 三栏邮件工作台 | 必做 | 左侧文件夹、中间列表、右侧阅读区，提高桌面端处理效率 |
| 新信按钮 | 必做 | 进入写信弹窗或侧栏编辑器 |
| 搜索框 | 必做 | MVP 先支持当前文件夹关键词搜索 |
| 刷新按钮 | 必做 | 手动拉取最新邮件 |
| 批量选择 | 必做 | 支持删除、标记已读、标记未读 |
| 星标 | MVP 可选 | 若 IMAP Flag 支持稳定，则纳入首版 |
| 联系人 | MVP 可选 | 首版可先做最近联系人自动补全 |
| 设置中心 | 后续版本 | 首版只保留退出登录和基础账号信息 |
| 过滤器、OpenPGP、双因素 | 后续版本 | 不进入 MVP，避免拉长交付周期 |

## 4. 用户与使用场景

### 4.1 目标用户

| 用户类型 | 典型诉求 | 关键痛点 |
| --- | --- | --- |
| 普通员工 | 快速收发公司邮箱 | 不想配置桌面客户端，浏览器即开即用 |
| 业务人员 | 查找历史邮件和附件 | 邮件列表要清晰，搜索要可用 |
| 管理人员 | 稳定处理重要邮件 | 登录安全、邮件不丢失、发送失败要明确 |
| 运维人员 | 管理部署与排障 | 需要健康检查、日志、错误码和指标 |
| 产品负责人 | 快速验证体验和方案 | 首版范围要聚焦，可迭代 |

### 4.2 核心用户故事

| 编号 | 用户故事 | 优先级 |
| --- | --- | --- |
| US-001 | 作为用户，我希望用邮箱账号和密码登录 Webmail，以便访问现有邮箱 | P0 |
| US-002 | 作为用户，我希望看到常用文件夹和未读数量，以便快速定位邮件 | P0 |
| US-003 | 作为用户，我希望按时间倒序查看邮件列表，以便处理最新邮件 | P0 |
| US-004 | 作为用户，我希望点击邮件后在右侧阅读正文，以便不离开列表完成阅读 | P0 |
| US-005 | 作为用户，我希望可以发送新邮件并添加附件，以便完成基础办公沟通 | P0 |
| US-006 | 作为用户，我希望写信中断时自动保存草稿，以便避免内容丢失 | P0 |
| US-007 | 作为用户，我希望搜索当前文件夹邮件，以便快速定位历史邮件 | P1 |
| US-008 | 作为用户，我希望批量删除或标记邮件，以便高效整理邮箱 | P1 |
| US-009 | 作为用户，我希望收到错误提示时知道如何处理，以便减少困惑 | P1 |
| US-010 | 作为运维，我希望系统有健康检查和日志，以便判断故障位置 | P0 |

## 5. MVP 范围

### 5.1 必做范围

| 模块 | 必做能力 |
| --- | --- |
| 登录与会话 | 邮箱账号密码登录、退出登录、会话续期、会话过期处理 |
| 文件夹 | 系统文件夹展示、未读数展示、刷新 |
| 邮件列表 | 分页、排序、发件人、主题、时间、已读状态、附件标识 |
| 邮件详情 | 头部信息、HTML 和纯文本正文、安全净化、附件列表、下载附件 |
| 写信发信 | 收件人、抄送、密送、主题、正文、附件上传、SMTP 发送 |
| 草稿 | 手动保存、自动保存、草稿继续编辑、发送后删除草稿 |
| 邮件操作 | 删除、标记已读、标记未读、移动到文件夹 |
| 搜索 | 当前文件夹按关键词搜索主题、发件人、正文摘要 |
| 缓存与元数据 | PostgreSQL 缓存邮件元数据，Redis 缓存会话和热数据 |
| 安全 | 密码不明文入库、HTML 净化、接口鉴权、基础限流 |
| 运维 | 健康检查、结构化日志、错误码、部署配置 |

### 5.2 MVP 可选范围

| 模块 | 可选能力 | 纳入条件 |
| --- | --- | --- |
| 星标邮件 | IMAP `\Flagged` 映射 | 邮件服务器 Flag 行为验证通过 |
| 联系人补全 | 最近发件人和收件人补全 | 不引入复杂通讯录管理 |
| 回复与转发 | 从详情页快捷回复、转发 | 写信组件完成后复用实现 |
| 桌面通知 | 新邮件浏览器通知 | 用户授权和 HTTPS 部署就绪 |

### 5.3 明确不做范围

| 能力 | 不纳入 MVP 原因 |
| --- | --- |
| 多账号聚合 | 增加会话、缓存和权限复杂度 |
| 邮件过滤规则 | 依赖服务器能力和规则引擎，适合后续版本 |
| OpenPGP 加密 | 安全设计复杂，首版不验证该问题 |
| 双因素认证 | 当前目标是连接现有邮箱认证，不改造身份体系 |
| 管理后台 | MVP 聚焦用户侧邮件收发 |
| 全文搜索引擎 | PostgreSQL 和 IMAP 搜索先满足首版，后续再引入 OpenSearch |
| 日历、网盘、协作文档 | 超出 Webmail MVP 范围 |
| 移动端 App | 首版提供响应式 Web，不做原生 App |

## 6. 信息架构与界面需求

### 6.1 页面结构

| 页面 | 路由 | 说明 |
| --- | --- | --- |
| 登录页 | `/login` | 邮箱、密码、记住登录、语言入口 |
| 邮件工作台 | `/mail` | 三栏主界面，默认进入收件箱 |
| 邮件详情 | `/mail/:folder/:uid` | 桌面端右侧展示，移动端单页展示 |
| 写信 | `/compose` 或弹窗状态 | 新邮件、回复、转发共用 |
| 草稿编辑 | `/compose/:draftId` | 从草稿箱恢复编辑 |
| 设置简页 | `/settings` | MVP 仅账号信息、退出登录、偏好 |
| 错误页 | `/error` | 网络错误、登录过期、服务异常 |

### 6.2 桌面端工作台布局

| 区域 | 位置 | 内容 |
| --- | --- | --- |
| 顶部工具栏 | 页面顶部 | 新信、刷新、批量操作、搜索、更多菜单、当前账号 |
| 文件夹栏 | 左侧，宽度 220px 到 260px | 收件箱、已发送、草稿、垃圾邮件、已删除、归档，显示未读数 |
| 邮件列表 | 中间，宽度 420px 到 560px | 选择框、发件人、主题、时间、附件、星标或状态 |
| 阅读区 | 右侧，自适应 | 未选择提示、邮件头、正文、附件、回复转发入口 |
| 写信层 | 弹窗或右侧抽屉 | 收件人、主题、富文本正文、附件、发送和保存草稿 |

### 6.3 移动端布局

MVP 移动端采用单列层级导航：

1. 默认显示文件夹和邮件列表。
2. 点击邮件进入详情页。
3. 写信使用全屏页面。
4. 批量操作收敛到底部工具栏。
5. 左侧文件夹通过抽屉展开。

### 6.4 交互原则

1. 邮件列表加载时展示骨架屏，不用空白页面。
2. 邮件正文加载失败时保留列表状态，并在阅读区显示可重试错误。
3. 发送邮件时按钮进入 loading 状态，防止重复提交。
4. 草稿保存状态必须可见，至少包含“保存中”“已保存”“保存失败”。
5. 附件上传需要展示进度、大小和失败原因。
6. 会话过期时跳转登录页，并提示用户重新登录。
7. 所有危险操作需要可撤销或二次确认，MVP 对永久删除采用二次确认。

## 7. 功能需求

### 7.1 登录与会话

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| AUTH-001 | 用户输入邮箱地址和密码登录 | P0 | 正确凭证登录成功，进入收件箱 |
| AUTH-002 | 后端使用 IMAP 验证凭证有效性 | P0 | IMAP 登录失败时返回统一错误码 |
| AUTH-003 | 登录成功后创建服务端会话 | P0 | Redis 存储 session，前端只持有 HttpOnly Cookie |
| AUTH-004 | 支持退出登录 | P0 | 退出后 Redis 会话失效，接口返回 401 |
| AUTH-005 | 支持会话过期 | P0 | 超过配置 TTL 后需要重新登录 |
| AUTH-006 | 登录失败限流 | P0 | 同账号或同 IP 连续失败触发短期限制 |
| AUTH-007 | 不将邮箱密码明文写入 PostgreSQL | P0 | 数据库中无法查询到原始密码 |

#### 登录会话策略

1. Redis 保存会话主体，Key 格式为 `session:{session_id}`。
2. 会话中保存用户邮箱、IMAP 连接配置、SMTP 连接配置和加密后的临时凭证。
3. 凭证加密使用服务端密钥，密钥来自环境变量或密钥管理服务。
4. Cookie 设置 `HttpOnly`、`Secure`、`SameSite=Lax`。
5. MVP 默认会话有效期 8 小时，记住登录可配置为 7 天，但需要加密保存刷新令牌。

### 7.2 文件夹管理

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| FOLDER-001 | 展示邮箱文件夹列表 | P0 | 登录后可以看到系统文件夹 |
| FOLDER-002 | 映射常用系统文件夹 | P0 | 收件箱、已发送、草稿、垃圾邮件、已删除、归档映射正确 |
| FOLDER-003 | 展示未读数量 | P0 | 未读数与 IMAP 返回结果一致 |
| FOLDER-004 | 切换文件夹刷新邮件列表 | P0 | 点击文件夹后列表展示对应邮件 |
| FOLDER-005 | 支持手动刷新文件夹 | P0 | 刷新后新邮件可见 |
| FOLDER-006 | 支持文件夹为空状态 | P1 | 空文件夹显示清晰空状态 |

### 7.3 邮件列表

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| LIST-001 | 按时间倒序展示邮件 | P0 | 最新邮件在列表顶部 |
| LIST-002 | 支持分页加载 | P0 | 默认每页 30 封，可继续加载 |
| LIST-003 | 展示关键摘要字段 | P0 | 发件人、主题、时间、已读、附件标识可见 |
| LIST-004 | 支持选择单封或多封邮件 | P1 | 批量工具栏状态正确 |
| LIST-005 | 支持标记已读和未读 | P1 | 操作后 IMAP Flag 与 UI 同步 |
| LIST-006 | 支持删除到已删除邮件 | P1 | 删除后邮件从当前列表移除 |
| LIST-007 | 支持移动到其他文件夹 | P1 | 目标文件夹存在时移动成功 |
| LIST-008 | 支持列表刷新 | P0 | 手动刷新不丢失当前文件夹上下文 |

### 7.4 邮件阅读

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| VIEW-001 | 点击列表邮件后展示详情 | P0 | 右侧阅读区展示正文 |
| VIEW-002 | 展示邮件头信息 | P0 | 发件人、收件人、抄送、主题、时间展示完整 |
| VIEW-003 | 支持 HTML 邮件安全展示 | P0 | 脚本、危险链接、内联事件被移除 |
| VIEW-004 | 支持纯文本邮件展示 | P0 | 换行和链接处理正确 |
| VIEW-005 | 展示附件列表 | P0 | 文件名、大小、下载按钮展示正确 |
| VIEW-006 | 支持附件下载 | P0 | 下载文件内容与邮件附件一致 |
| VIEW-007 | 打开邮件后自动标记已读 | P1 | 行为可配置，默认开启 |
| VIEW-008 | 支持回复和转发入口 | P1 | 能打开写信组件并带入原邮件上下文 |

### 7.5 写信与发送

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| COMPOSE-001 | 支持新建邮件 | P0 | 点击新信后打开写信界面 |
| COMPOSE-002 | 支持收件人、抄送、密送 | P0 | 邮箱格式校验正确 |
| COMPOSE-003 | 支持主题和正文 | P0 | 主题可为空但发送前提示确认 |
| COMPOSE-004 | 支持富文本编辑 | P1 | 加粗、斜体、链接、列表、引用可用 |
| COMPOSE-005 | 支持附件上传 | P0 | 可上传一个或多个附件 |
| COMPOSE-006 | 限制附件大小 | P0 | 单封邮件附件总大小默认不超过 25 MB |
| COMPOSE-007 | 通过 SMTP 发送邮件 | P0 | 发送成功后提示成功 |
| COMPOSE-008 | 发送后保存到已发送邮件 | P0 | 已发送文件夹可看到该邮件 |
| COMPOSE-009 | 发送失败给出明确提示 | P0 | 网络、认证、收件人错误分开提示 |
| COMPOSE-010 | 防止重复发送 | P0 | 发送中再次点击无效 |

### 7.6 草稿

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| DRAFT-001 | 写信内容自动保存 | P0 | 输入后 5 秒内触发保存 |
| DRAFT-002 | 支持手动保存草稿 | P0 | 点击保存后状态变为已保存 |
| DRAFT-003 | 草稿保存到 IMAP 草稿箱 | P0 | 其他客户端可看到草稿 |
| DRAFT-004 | 草稿可继续编辑 | P0 | 从草稿箱打开后恢复收件人、主题、正文、附件 |
| DRAFT-005 | 发送成功后删除对应草稿 | P1 | 草稿箱不再显示已发送草稿 |

### 7.7 搜索

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| SEARCH-001 | 当前文件夹关键词搜索 | P1 | 按主题、发件人、收件人、正文摘要搜索 |
| SEARCH-002 | 搜索结果分页 | P1 | 大量结果可继续加载 |
| SEARCH-003 | 搜索为空状态 | P1 | 展示无匹配结果 |
| SEARCH-004 | 清空搜索恢复列表 | P1 | 当前文件夹普通列表恢复 |

#### 搜索策略

1. 优先使用本地 PostgreSQL 元数据搜索主题、发件人、收件人和摘要。
2. 本地无结果或缓存过期时，可触发 IMAP SEARCH。
3. MVP 不做跨文件夹全量正文索引。
4. 邮件正文摘要最长保存 500 字符，避免过度持久化敏感内容。

### 7.8 附件

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| ATTACH-001 | 写信时上传附件 | P0 | 附件列表显示名称和大小 |
| ATTACH-002 | 发送邮件包含附件 | P0 | 收件方可收到并打开附件 |
| ATTACH-003 | 阅读邮件时下载附件 | P0 | 下载内容正确 |
| ATTACH-004 | 附件大小限制 | P0 | 超限时前后端均拦截 |
| ATTACH-005 | 附件临时存储清理 | P0 | 未发送附件超过 TTL 后清理 |

### 7.9 联系人与地址补全

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| CONTACT-001 | 最近联系人自动记录 | P2 | 发送成功后记录收件人 |
| CONTACT-002 | 写信地址补全 | P2 | 输入关键字展示最近联系人 |
| CONTACT-003 | 不做完整通讯录管理 | P2 | MVP 不提供联系人增删改查页面 |

### 7.10 设置与账号

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| SETTING-001 | 展示当前登录邮箱 | P0 | 顶部或设置页显示邮箱地址 |
| SETTING-002 | 支持退出登录 | P0 | 所有本地状态清理 |
| SETTING-003 | 支持基础偏好 | P2 | 每页数量、阅读后标记已读可配置 |

## 8. 后端需求

### 8.1 后端分层

| 层级 | 职责 |
| --- | --- |
| API 层 | FastAPI 路由、请求校验、响应封装、鉴权 |
| Service 层 | 登录、邮件列表、详情、发送、草稿等业务编排 |
| Mail Adapter 层 | 封装 IMAP 和 SMTP 操作 |
| Repository 层 | PostgreSQL 数据读写 |
| Cache 层 | Redis 会话、热数据、锁、限流 |
| Security 层 | 凭证加密、HTML 净化、权限校验 |
| Observability 层 | 日志、指标、链路追踪 |

### 8.2 API 设计草案

统一响应格式：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "request_id": "req_20260506_abcdef"
}
```

统一错误格式：

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "AUTH_INVALID_CREDENTIALS",
    "message": "邮箱或密码不正确",
    "details": {}
  },
  "request_id": "req_20260506_abcdef"
}
```

| 方法 | 路径 | 说明 | 鉴权 |
| --- | --- | --- | --- |
| `POST` | `/api/auth/login` | 邮箱登录 | 否 |
| `POST` | `/api/auth/logout` | 退出登录 | 是 |
| `GET` | `/api/auth/me` | 当前用户 | 是 |
| `GET` | `/api/folders` | 文件夹列表 | 是 |
| `POST` | `/api/folders/refresh` | 刷新文件夹与未读数 | 是 |
| `GET` | `/api/messages` | 邮件列表 | 是 |
| `GET` | `/api/messages/{folder}/{uid}` | 邮件详情 | 是 |
| `POST` | `/api/messages/{folder}/{uid}/read` | 标记已读 | 是 |
| `POST` | `/api/messages/{folder}/{uid}/unread` | 标记未读 | 是 |
| `POST` | `/api/messages/move` | 批量移动邮件 | 是 |
| `POST` | `/api/messages/delete` | 批量删除邮件 | 是 |
| `GET` | `/api/search` | 当前文件夹搜索 | 是 |
| `POST` | `/api/compose/send` | 发送邮件 | 是 |
| `POST` | `/api/drafts` | 保存草稿 | 是 |
| `GET` | `/api/drafts/{draft_id}` | 获取草稿 | 是 |
| `DELETE` | `/api/drafts/{draft_id}` | 删除草稿 | 是 |
| `POST` | `/api/attachments/upload` | 上传待发送附件 | 是 |
| `GET` | `/api/attachments/{message_ref}/{part_id}` | 下载邮件附件 | 是 |
| `GET` | `/api/health` | 服务健康检查 | 否 |
| `GET` | `/api/ready` | 依赖就绪检查 | 否 |

### 8.3 核心请求模型

登录请求：

```json
{
  "email": "user@example.com",
  "password": "password",
  "remember": false
}
```

邮件列表查询：

```json
{
  "folder": "INBOX",
  "page": 1,
  "page_size": 30,
  "sort": "date_desc",
  "refresh": false
}
```

发送邮件请求：

```json
{
  "to": ["receiver@example.com"],
  "cc": [],
  "bcc": [],
  "subject": "邮件主题",
  "html": "<p>邮件正文</p>",
  "text": "邮件正文",
  "attachment_ids": ["att_123"],
  "draft_id": "draft_123"
}
```

### 8.4 错误码

| 错误码 | HTTP 状态 | 场景 |
| --- | --- | --- |
| `AUTH_INVALID_CREDENTIALS` | 401 | 邮箱或密码错误 |
| `AUTH_SESSION_EXPIRED` | 401 | 会话过期 |
| `AUTH_RATE_LIMITED` | 429 | 登录失败过多 |
| `MAIL_IMAP_CONNECT_FAILED` | 502 | IMAP 连接失败 |
| `MAIL_IMAP_AUTH_FAILED` | 401 | IMAP 认证失败 |
| `MAIL_IMAP_TIMEOUT` | 504 | IMAP 超时 |
| `MAIL_SMTP_CONNECT_FAILED` | 502 | SMTP 连接失败 |
| `MAIL_SMTP_SEND_FAILED` | 502 | SMTP 发送失败 |
| `MAIL_FOLDER_NOT_FOUND` | 404 | 文件夹不存在 |
| `MAIL_MESSAGE_NOT_FOUND` | 404 | 邮件不存在 |
| `ATTACHMENT_TOO_LARGE` | 413 | 附件超限 |
| `ATTACHMENT_NOT_FOUND` | 404 | 附件不存在 |
| `VALIDATION_ERROR` | 422 | 请求参数错误 |
| `INTERNAL_ERROR` | 500 | 未预期错误 |

## 9. 数据模型

### 9.1 PostgreSQL 表

#### `mail_accounts`

保存邮箱账号的非敏感配置和偏好，不保存明文密码。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `email` | VARCHAR(320) | 邮箱地址，唯一 |
| `display_name` | VARCHAR(255) | 显示名 |
| `imap_host` | VARCHAR(255) | IMAP 主机 |
| `imap_port` | INTEGER | IMAP 端口 |
| `imap_ssl` | BOOLEAN | 是否 SSL |
| `smtp_host` | VARCHAR(255) | SMTP 主机 |
| `smtp_port` | INTEGER | SMTP 端口 |
| `smtp_ssl` | BOOLEAN | 是否 SSL |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `updated_at` | TIMESTAMPTZ | 更新时间 |

#### `mail_folders`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `account_id` | UUID | 账号 ID |
| `name` | VARCHAR(512) | IMAP 原始文件夹名 |
| `display_name` | VARCHAR(255) | 展示名称 |
| `type` | VARCHAR(50) | `inbox`、`sent`、`drafts`、`trash`、`spam`、`archive`、`custom` |
| `delimiter` | VARCHAR(10) | IMAP 文件夹分隔符 |
| `uid_validity` | BIGINT | IMAP UIDVALIDITY |
| `unread_count` | INTEGER | 未读数 |
| `total_count` | INTEGER | 总数 |
| `last_synced_at` | TIMESTAMPTZ | 最近同步时间 |

#### `mail_messages`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `account_id` | UUID | 账号 ID |
| `folder_id` | UUID | 文件夹 ID |
| `imap_uid` | BIGINT | IMAP UID |
| `message_id` | TEXT | 邮件 Message-ID |
| `subject` | TEXT | 主题 |
| `sender_name` | TEXT | 发件人名称 |
| `sender_email` | VARCHAR(320) | 发件人邮箱 |
| `to_emails` | JSONB | 收件人 |
| `cc_emails` | JSONB | 抄送 |
| `sent_at` | TIMESTAMPTZ | 邮件时间 |
| `received_at` | TIMESTAMPTZ | 接收时间 |
| `snippet` | TEXT | 正文摘要 |
| `has_attachments` | BOOLEAN | 是否有附件 |
| `is_read` | BOOLEAN | 是否已读 |
| `is_flagged` | BOOLEAN | 是否星标 |
| `size_bytes` | BIGINT | 邮件大小 |
| `flags` | JSONB | IMAP Flags |
| `cached_at` | TIMESTAMPTZ | 缓存时间 |

唯一索引：`account_id + folder_id + imap_uid`。

#### `mail_drafts`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `account_id` | UUID | 账号 ID |
| `imap_uid` | BIGINT | 草稿箱 UID，可为空 |
| `to_emails` | JSONB | 收件人 |
| `cc_emails` | JSONB | 抄送 |
| `bcc_emails` | JSONB | 密送 |
| `subject` | TEXT | 主题 |
| `html_body` | TEXT | HTML 正文 |
| `text_body` | TEXT | 纯文本正文 |
| `attachment_refs` | JSONB | 附件引用 |
| `status` | VARCHAR(50) | `editing`、`saved`、`sending`、`sent`、`failed` |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `updated_at` | TIMESTAMPTZ | 更新时间 |

#### `mail_attachments`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `account_id` | UUID | 账号 ID |
| `message_id` | UUID | 邮件 ID，可为空 |
| `draft_id` | UUID | 草稿 ID，可为空 |
| `filename` | TEXT | 文件名 |
| `content_type` | VARCHAR(255) | MIME 类型 |
| `size_bytes` | BIGINT | 文件大小 |
| `storage_key` | TEXT | 临时存储路径或对象存储 Key |
| `status` | VARCHAR(50) | `temp`、`attached`、`deleted` |
| `expires_at` | TIMESTAMPTZ | 临时附件过期时间 |

#### `audit_logs`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `account_id` | UUID | 账号 ID，可为空 |
| `event_type` | VARCHAR(100) | 事件类型 |
| `request_id` | VARCHAR(100) | 请求 ID |
| `ip` | INET | 来源 IP |
| `user_agent` | TEXT | 用户代理 |
| `success` | BOOLEAN | 是否成功 |
| `metadata` | JSONB | 脱敏元数据 |
| `created_at` | TIMESTAMPTZ | 创建时间 |

### 9.2 Redis Key 设计

| Key | 类型 | TTL | 说明 |
| --- | --- | --- | --- |
| `session:{session_id}` | Hash | 8 小时 | 登录会话 |
| `login_fail:{ip}:{email}` | Counter | 15 分钟 | 登录失败限流 |
| `mail_lock:{account_id}:{folder}` | String | 60 秒 | 文件夹同步锁 |
| `folder_cache:{account_id}` | JSON | 60 秒 | 文件夹列表热缓存 |
| `message_list:{account_id}:{folder}:{page}` | JSON | 60 秒 | 邮件列表热缓存 |
| `compose_upload:{attachment_id}` | Hash | 24 小时 | 待发送附件 |

## 10. 系统架构

### 10.1 逻辑架构

```mermaid
flowchart LR
  User["用户浏览器"] --> React["React SPA"]
  React --> API["FastAPI API"]
  API --> Auth["会话与鉴权"]
  API --> MailService["邮件业务服务"]
  API --> Pg["PostgreSQL"]
  API --> Redis["Redis"]
  MailService --> ImapAdapter["IMAP Adapter"]
  MailService --> SmtpAdapter["SMTP Adapter"]
  ImapAdapter --> MailServer["邮箱服务器 14.103.117.188"]
  SmtpAdapter --> MailServer
  API --> Logs["结构化日志与指标"]
```

### 10.2 登录流程

```mermaid
sequenceDiagram
  participant U as 用户
  participant F as React
  participant A as FastAPI
  participant R as Redis
  participant I as IMAP
  U->>F: 输入邮箱和密码
  F->>A: POST /api/auth/login
  A->>I: 尝试 IMAP 登录
  I-->>A: 登录成功或失败
  A->>R: 写入 session
  A-->>F: 设置 HttpOnly Cookie
  F-->>U: 进入收件箱
```

### 10.3 收信流程

```mermaid
sequenceDiagram
  participant F as React
  participant A as FastAPI
  participant R as Redis
  participant P as PostgreSQL
  participant I as IMAP
  F->>A: GET /api/messages?folder=INBOX
  A->>R: 查询列表热缓存
  alt 缓存命中
    R-->>A: 返回列表
  else 缓存未命中
    A->>P: 查询元数据缓存
    A->>I: 必要时同步最新 UID
    A->>P: upsert 邮件元数据
    A->>R: 写入热缓存
  end
  A-->>F: 返回邮件列表
```

### 10.4 发信流程

```mermaid
sequenceDiagram
  participant F as React
  participant A as FastAPI
  participant S as SMTP
  participant I as IMAP
  participant P as PostgreSQL
  F->>A: POST /api/compose/send
  A->>A: 校验收件人、附件、正文
  A->>S: SMTP 发送 MIME 邮件
  S-->>A: 发送结果
  A->>I: APPEND 到已发送文件夹
  A->>P: 更新草稿和发送审计
  A-->>F: 返回发送成功
```

### 10.5 部署架构

MVP 推荐单环境部署，支持后续水平扩展：

1. 前端静态资源由 Nginx 托管。
2. FastAPI 使用 Gunicorn 或 Uvicorn Worker 运行。
3. PostgreSQL 独立服务，启用定期备份。
4. Redis 独立服务，开启密码和内网访问控制。
5. Nginx 统一公网 HTTPS 入口和反向代理，Webmail 不提供明文 HTTP 业务访问。
6. 邮件服务器地址、端口、TLS 策略通过环境变量配置。
7. Webmail 后端到邮件服务器允许继续使用当前明文 IMAP `143` 和明文 SMTP `25`。

## 11. 前端需求

### 11.1 技术约束

1. 使用 React 构建 SPA。
2. 使用 TypeScript，降低接口和状态维护风险。
3. 使用 React Router 管理路由。
4. 使用 TanStack Query 或等价方案管理服务端状态。
5. 使用 Zustand、Redux Toolkit 或等价轻量方案管理客户端 UI 状态。
6. 富文本编辑器优先选用成熟组件，例如 TipTap、Lexical 或 Quill，最终选型需要以包体、维护状态和安全能力为依据。

### 11.2 状态模型

| 状态域 | 内容 |
| --- | --- |
| `auth` | 当前用户、登录状态、会话过期 |
| `folders` | 文件夹树、当前文件夹、未读数 |
| `messages` | 当前列表、分页、选择项、加载状态 |
| `messageView` | 当前邮件详情、附件、错误状态 |
| `compose` | 写信窗口、草稿、附件上传、保存状态 |
| `ui` | 侧栏开关、移动端抽屉、弹窗状态 |

### 11.3 组件拆分

| 组件 | 职责 |
| --- | --- |
| `AppShell` | 主框架和路由出口 |
| `LoginPage` | 登录表单 |
| `MailLayout` | 三栏布局 |
| `FolderSidebar` | 文件夹列表 |
| `Toolbar` | 顶部工具栏 |
| `MessageList` | 邮件列表容器 |
| `MessageListItem` | 单封邮件列表项 |
| `MessageReader` | 邮件详情 |
| `ComposeEditor` | 写信编辑器 |
| `AttachmentUploader` | 附件上传 |
| `AttachmentList` | 附件展示和下载 |
| `EmptyState` | 空状态 |
| `ErrorBanner` | 错误提示 |

### 11.4 UI 质量要求

1. 桌面端最小宽度按 1024px 设计，移动端从 375px 适配。
2. 邮件列表项高度固定，避免加载状态造成跳动。
3. 主题和发件人超长时单行截断，详情页完整展示。
4. 所有图标按钮必须有可访问标签。
5. 操作成功使用轻量 Toast，失败使用明确可读错误提示。
6. 颜色不依赖单一色相表达状态，错误、警告、成功需有文本或图标辅助。
7. 浏览器只支持现代 Chrome、Edge、Safari、Firefox，不兼容 IE 和其他旧版浏览器。

## 12. 安全与合规需求

### 12.1 凭证安全

1. 邮箱密码不得明文写入 PostgreSQL。
2. Redis 中的临时凭证必须加密。
3. 服务端加密密钥不得提交到代码仓库。
4. 登录接口需要限流和审计。
5. 日志不得记录密码、完整 Cookie、邮件正文和附件内容。

### 12.2 邮件内容安全

1. HTML 邮件展示前必须进行净化。
2. 移除 `script`、`iframe`、内联事件、危险协议链接。
3. 外部图片默认可配置为不自动加载，避免泄露阅读行为。
4. 附件下载使用鉴权接口，不暴露真实临时路径。
5. 附件文件名需要防路径穿越处理。

### 12.3 Web 安全

1. Cookie 设置 `HttpOnly`、`Secure`、`SameSite=Lax`。
2. API 开启 CSRF 防护或采用 SameSite 加 CSRF Token。
3. CORS 白名单由配置控制。
4. 设置安全响应头：`Content-Security-Policy`、`X-Content-Type-Options`、`Referrer-Policy`。
5. 所有写操作校验会话和用户权限。

## 13. 性能需求

| 指标 | 目标 |
| --- | --- |
| 登录耗时 | P95 小于 3 秒 |
| 首屏加载 | P95 小于 2.5 秒，不含首次静态资源下载 |
| 邮件列表接口 | 缓存命中 P95 小于 300ms，缓存未命中 P95 小于 2 秒 |
| 邮件详情接口 | 普通邮件 P95 小于 2 秒 |
| 发信接口 | 不含大附件 P95 小于 5 秒 |
| 支持邮箱大小 | MVP 单账号至少支持 1 万封邮件元数据缓存 |
| 列表分页 | 默认 30 封，最大 100 封 |
| 附件限制 | 单封邮件默认 25 MB，可配置 |

## 14. 可观测性与运维需求

### 14.1 日志

结构化日志字段：

| 字段 | 说明 |
| --- | --- |
| `timestamp` | 时间 |
| `level` | 日志等级 |
| `request_id` | 请求 ID |
| `account_hash` | 邮箱哈希，避免直接暴露邮箱 |
| `endpoint` | API 路径 |
| `event_type` | 事件类型 |
| `duration_ms` | 耗时 |
| `error_code` | 错误码 |
| `remote_addr` | 来源 IP |

### 14.2 指标

| 指标 | 说明 |
| --- | --- |
| `api_request_total` | API 请求数 |
| `api_request_duration_ms` | API 耗时 |
| `imap_connect_total` | IMAP 连接次数 |
| `imap_error_total` | IMAP 错误次数 |
| `smtp_send_total` | SMTP 发送次数 |
| `smtp_send_error_total` | SMTP 发送错误次数 |
| `session_active_total` | 活跃会话数 |
| `mail_sync_duration_ms` | 邮件同步耗时 |
| `attachment_upload_bytes` | 附件上传大小 |

### 14.3 健康检查

| 接口 | 检查内容 |
| --- | --- |
| `/api/health` | 应用进程存活 |
| `/api/ready` | PostgreSQL、Redis 可连接，配置完整 |
| `/api/version` | 应用版本、构建时间、提交哈希 |

## 15. 配置需求

### 15.1 当前邮件服务器探测结果

2026-05-06 通过 SSH 登录 `14.103.117.188` 并从外部协议探测确认：

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 操作系统 | Ubuntu 22.04.5 LTS | `/etc/os-release` |
| 主机名 | `mail.mdaemon.cc` | `hostname` |
| IMAP 服务 | Dovecot 2.3.16 | `dovecot --version` |
| SMTP 服务 | Postfix 3.6.4 | `postconf -h mail_version` |
| IMAP 端口 | `143` | `ss -ltnp` 显示 Dovecot 监听 `0.0.0.0:143` 和 `[::]:143` |
| IMAP TLS | 未启用 TLS，未公告 STARTTLS | Dovecot `ssl = no`，`openssl s_client -starttls imap` 未发现 STARTTLS |
| IMAP 认证 | `AUTH=PLAIN`、`AUTH=LOGIN` | IMAP `CAPABILITY` 返回 `AUTH=PLAIN AUTH=LOGIN` |
| IMAP 明文认证 | 允许 | Dovecot `disable_plaintext_auth = no` |
| SMTP 端口 | `25`，同时监听 `587` | `ss -ltnp` 和 `postconf -M` |
| SMTP TLS | 未启用 TLS，未公告 STARTTLS | Postfix `smtpd_tls_security_level = none`，`openssl s_client -starttls smtp` 未发现 STARTTLS |
| SMTP 认证 | `AUTH PLAIN LOGIN` | SMTP `EHLO` 返回 `250-AUTH PLAIN LOGIN` |
| SMTP 认证策略 | 允许非 TLS 认证 | Postfix `smtpd_tls_auth_only = no` |
| SMTP Relay 策略 | 本机网络或认证用户可中继 | `permit_mynetworks, permit_sasl_authenticated, reject_unauth_destination` |
| 单封邮件大小限制 | `10240000` 字节，约 9.77 MiB | SMTP `EHLO` 返回 `SIZE 10240000`，`postconf -h message_size_limit` |
| 客户端连接数限制 | 单客户端 `50` | `postconf -h smtpd_client_connection_count_limit` |
| 客户端连接速率限制 | 未限制 | `smtpd_client_connection_rate_limit = 0` |
| 客户端发信速率限制 | 未限制 | `smtpd_client_message_rate_limit = 0` |
| 客户端收件人速率限制 | 未限制 | `smtpd_client_recipient_rate_limit = 0` |
| 系统文件夹 | `.Archive`、`.Drafts`、`.Junk`、`.Sent`、`.Trash` | `/var/vmail/mdaemon.cc/test/mail` |
| IMAP APPEND | 测试账号允许 APPEND 到草稿箱和已发送文件夹 | 用户确认 |

当前服务器未启用 IMAP/SMTP TLS，MVP 按现状接入；用户已确认允许 Webmail 后端到邮件服务器继续使用明文 IMAP/SMTP。Webmail 用户访问入口必须使用公网 HTTPS，避免浏览器到 Webmail 链路出现明文传输。

### 15.2 环境变量建议

当前服务器按已探测配置接入：

```bash
APP_ENV=production
APP_SECRET_KEY=change-me
DATABASE_URL=postgresql://user:password@postgres:5432/webmail
REDIS_URL=redis://:password@redis:6379/0
CORS_ORIGINS=https://mail.example.com
MAIL_IMAP_HOST=14.103.117.188
MAIL_IMAP_PORT=143
MAIL_IMAP_SSL=false
MAIL_IMAP_STARTTLS=false
MAIL_SMTP_HOST=14.103.117.188
MAIL_SMTP_PORT=25
MAIL_SMTP_SSL=false
MAIL_SMTP_STARTTLS=false
SESSION_TTL_SECONDS=28800
ATTACHMENT_MAX_MB=9
TEMP_ATTACHMENT_TTL_SECONDS=86400
LOG_LEVEL=INFO
```

### 15.3 已确认配置项

1. IMAP 使用 `143` 明文连接，不启用 SSL，不支持 STARTTLS。
2. SMTP 使用 `25` 明文连接，不启用 SSL，不支持 STARTTLS。
3. SMTP 认证方式为 `PLAIN` 和 `LOGIN`，认证由 Postfix 通过 Dovecot SASL 完成。
4. 已发送、草稿、垃圾邮件、已删除、归档文件夹分别映射为 `.Sent`、`.Drafts`、`.Junk`、`.Trash`、`.Archive`。
5. IMAP 支持 `IDLE`，CAPABILITY 中包含 `IDLE`。
6. 测试账号允许 IMAP `APPEND` 到草稿箱和已发送文件夹。
7. 单封邮件大小上限按服务器 `message_size_limit = 10240000` 控制，前端和后端默认按 `9 MB` 做保守限制。

## 16. 测试与验收

### 16.1 测试类型

| 类型 | 范围 |
| --- | --- |
| 单元测试 | 邮件解析、地址校验、HTML 净化、错误码映射 |
| 集成测试 | IMAP 登录、文件夹同步、邮件列表、邮件详情、SMTP 发信 |
| API 测试 | 鉴权、参数校验、分页、错误响应 |
| 前端测试 | 登录页、工作台布局、写信流程、附件上传 |
| E2E 测试 | 登录、收信、读信、写信、保存草稿、发送邮件 |
| 安全测试 | XSS 邮件、附件路径穿越、越权下载、会话过期 |
| 性能冒烟 | 1 万封元数据列表分页、并发登录、并发列表读取 |

### 16.2 核心验收用例

| 用例编号 | 场景 | 前置条件 | 操作 | 期望结果 |
| --- | --- | --- | --- | --- |
| AC-001 | 正常登录 | 邮箱账号有效 | 输入邮箱密码登录 | 进入收件箱，显示当前邮箱 |
| AC-002 | 错误密码 | 邮箱账号存在 | 输入错误密码 | 返回“邮箱或密码不正确” |
| AC-003 | 查看收件箱 | 已登录 | 进入收件箱 | 显示邮件列表和未读数 |
| AC-004 | 阅读邮件 | 收件箱有邮件 | 点击邮件 | 右侧显示正文和附件 |
| AC-005 | 下载附件 | 邮件有附件 | 点击下载 | 文件下载成功且内容正确 |
| AC-006 | 发送纯文本邮件 | SMTP 可用 | 输入收件人和正文发送 | 收件方收到邮件，已发送可见 |
| AC-007 | 发送带附件邮件 | SMTP 可用 | 上传附件后发送 | 收件方收到附件 |
| AC-008 | 保存草稿 | 写信未发送 | 输入内容等待自动保存 | 草稿箱可恢复编辑 |
| AC-009 | 删除邮件 | 邮件存在 | 选择邮件删除 | 邮件进入已删除邮件 |
| AC-010 | 会话过期 | 会话 TTL 到期 | 调用列表接口 | 返回 401 并跳转登录 |
| AC-011 | HTML 安全 | 邮件含脚本 | 打开邮件 | 脚本不执行，危险内容被移除 |
| AC-012 | IMAP 失败 | 邮件服务器不可达 | 请求列表 | 返回可读错误并记录日志 |

### 16.3 上线前验收门槛

1. P0 需求全部通过。
2. 核心 E2E 用例 AC-001 到 AC-012 全部通过。
3. 安全测试未发现 P0 或 P1 漏洞。
4. 生产配置不包含默认密钥。
5. 日志中不出现密码、Cookie、邮件正文和附件内容。
6. 真实邮箱账号完成至少 30 分钟连续收发测试。
7. PostgreSQL 和 Redis 重启后系统可恢复。
8. 公网访问入口启用 HTTPS，HTTP 访问自动跳转 HTTPS 或直接拒绝。
9. 浏览器验收覆盖最新版 Chrome、Edge、Safari、Firefox。

## 17. 里程碑计划

| 阶段 | 时间建议 | 交付物 |
| --- | --- | --- |
| M0 技术验证 | 2 到 3 天 | IMAP 登录、文件夹列表、SMTP 发信脚本验证 |
| M1 后端骨架 | 3 到 5 天 | FastAPI 项目、鉴权、会话、数据库迁移、健康检查 |
| M2 邮件读取 | 5 到 7 天 | 文件夹、列表、详情、附件下载、元数据缓存 |
| M3 写信发送 | 5 到 7 天 | 写信、附件上传、SMTP 发送、已发送归档 |
| M4 前端工作台 | 7 到 10 天 | 登录页、三栏布局、列表、阅读、写信、草稿 |
| M5 安全与运维 | 3 到 5 天 | HTML 净化、限流、日志、指标、部署脚本 |
| M6 联调验收 | 3 到 5 天 | E2E 测试、真实账号验收、问题修复 |

MVP 总周期建议为 4 到 6 周，具体取决于邮箱服务器协议兼容性、附件策略和前端交互精细度。

## 18. 风险与应对

| 风险 | 影响 | 应对策略 |
| --- | --- | --- |
| IMAP 文件夹命名不标准 | 系统文件夹映射错误 | 首次登录后读取 SPECIAL-USE，缺失时使用配置映射 |
| SMTP 保存已发送失败 | 用户看不到发信记录 | SMTP 成功后使用 IMAP APPEND，失败时提示并记录补偿任务 |
| 大邮箱列表加载慢 | 首屏体验差 | UID 分页、元数据缓存、热缓存、懒加载详情 |
| HTML 邮件 XSS | 安全风险高 | 服务端净化加前端隔离渲染 |
| 密码处理不当 | 严重安全风险 | 不明文入库，Redis 加密存储，日志脱敏 |
| 附件占用磁盘 | 存储膨胀 | 限制大小、TTL 清理、后续迁移对象存储 |
| 邮箱服务器连接数限制 | 并发失败 | 连接池上限、限流、短连接释放、同步锁 |
| 浏览器兼容问题 | 用户无法使用 | 明确支持最新版 Chrome、Edge、Safari、Firefox |

## 19. 研发实施建议

### 19.1 后端目录建议

```text
backend/
  app/
    api/
      routes/
    core/
    services/
    adapters/
      imap_adapter.py
      smtp_adapter.py
    repositories/
    schemas/
    security/
    observability/
  tests/
  alembic/
```

### 19.2 前端目录建议

```text
frontend/
  src/
    app/
    pages/
    components/
    features/
      auth/
      folders/
      messages/
      compose/
      attachments/
    api/
    hooks/
    styles/
    tests/
```

### 19.3 开发优先级

1. 先做 IMAP 和 SMTP 最小脚本验证，确认真实服务器参数。
2. 再做后端 API 和数据模型，不先陷入前端细节。
3. 前端先完成三栏主流程，再补齐视觉细节。
4. 所有邮件协议操作必须有可复现测试账号和测试邮件。
5. 安全净化和凭证处理必须在首轮实现，不作为后补项。

## 20. 后续版本路线

| 版本 | 能力 |
| --- | --- |
| v1.1 | 回复、转发、星标、联系人补全、快捷键 |
| v1.2 | 高级搜索、跨文件夹搜索、邮件线程 |
| v1.3 | 过滤规则、签名、模板、多身份发信 |
| v1.4 | 管理后台、账号策略、审计报表 |
| v1.5 | OpenPGP、双因素认证、企业通讯录 |
| v2.0 | 多账号聚合、全文搜索引擎、移动端深度优化 |

## 21. 已确认与待确认问题

以下问题已在 M0 技术验证阶段通过真实服务器补齐证据：

1. `14.103.117.188` 的 IMAP 端口为 `143`，TLS 未启用，未配置可用于 IMAP STARTTLS 的证书。
2. `14.103.117.188` 的 SMTP 端口为 `25`，同时监听 `587`；SMTP TLS 未启用，未配置可用于 SMTP STARTTLS 的证书。
3. IMAP 认证能力为 `AUTH=PLAIN`、`AUTH=LOGIN`；SMTP 认证能力为 `AUTH PLAIN LOGIN`。
4. Postfix 发信大小限制为 `10240000` 字节；客户端连接速率、发信速率和收件人速率未启用限制。
5. 测试账号允许 IMAP `APPEND` 到草稿箱和已发送文件夹。
6. 系统文件夹真实名称为 `.Archive`、`.Drafts`、`.Junk`、`.Sent`、`.Trash`。
7. 用户已确认其他不做限制。

新增确认项：

1. Webmail 必须支持公网 HTTPS 访问，公网业务入口不允许使用明文 HTTP。
2. Webmail 后端到邮件服务器允许继续使用明文 IMAP `143` 和明文 SMTP `25`。
3. 浏览器只支持现代 Chrome、Edge、Safari、Firefox。

## 22. MVP 完成定义

当以下条件全部满足时，MVP 视为完成：

1. 用户可通过浏览器登录现有邮箱账号。
2. 用户可查看文件夹、邮件列表和邮件详情。
3. 用户可发送纯文本、HTML 和带附件邮件。
4. 用户可保存、恢复和发送草稿。
5. 用户可删除、移动、标记已读和未读邮件。
6. 会话、凭证、HTML 邮件和附件处理满足安全底线。
7. 系统具备健康检查、日志、错误码和基础指标。
8. 使用真实邮箱服务器完成端到端验收测试。
9. P0 缺陷清零，P1 缺陷有明确规避方案或排期。
10. 部署文档、配置文档和测试报告齐全。
