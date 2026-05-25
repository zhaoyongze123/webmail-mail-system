import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import { useEffect } from 'react';

export type AppLocale = 'zh-CN' | 'en-US';

export const DEFAULT_LOCALE: AppLocale = 'zh-CN';
export const USER_LOCALE_STORAGE_KEY = 'webmail-user-locale';
export const ADMIN_LOCALE_STORAGE_KEY = 'webmail-admin-locale';
const LOCALE_CHANGE_EVENT = 'webmail:locale-change';

const EXACT_TRANSLATIONS: Record<string, string> = {
  '邮件后台管理': 'Mail admin',
  '同站运营后台一期': 'Phase 1 admin console',
  '管理菜单': 'Admin navigation',
  '管理员': 'Admin',
  '控制台': 'Dashboard',
  '历史': 'History',
  '日志': 'Logs',
  '系统配置': 'System settings',
  '域名': 'Domains',
  '用户': 'Users',
  '别名': 'Aliases',
  '配额': 'Quotas',
  '反垃圾': 'Anti-spam',
  '证书': 'TLS',
  '队列': 'Queue',
  '审计': 'Audit',
  '监控': 'Monitoring',
  '安全': 'Security',
  '跟随系统': 'System',
  '浅色': 'Light',
  '深色': 'Dark',
  '简体中文': 'Chinese (Simplified)',
  '登录邮箱': 'Sign in to mail',
  '注册邮箱': 'Create mail account',
  '邮箱': 'Email',
  '密码': 'Password',
  '显示名': 'Display name',
  '记住登录': 'Keep me signed in',
  '处理中...': 'Processing...',
  '登录': 'Sign in',
  '注册并登录': 'Create account and sign in',
  '创建新账号': 'Create a new account',
  '已有账号，去登录': 'Already have an account? Sign in',
  '未命名用户': 'Unnamed user',
  '写邮件': 'Compose',
  '关键词': 'Keyword',
  '搜索邮件...': 'Search mail...',
  '搜索': 'Search',
  '收起筛选': 'Hide filters',
  '展开筛选': 'Show filters',
  '清除': 'Clear',
  '发件人': 'From',
  '开始日期': 'Start date',
  '结束日期': 'End date',
  '仅看有附件': 'Has attachments only',
  '文件夹': 'Folders',
  '联系人': 'Contacts',
  '文件夹管理': 'Folder manager',
  '设置': 'Settings',
  '签名设置': 'Signature settings',
  '退出登录': 'Sign out',
  '刷新列表': 'Refresh list',
  '全选本页': 'Select page',
  '上一页': 'Previous',
  '下一页': 'Next',
  '批量标已读': 'Mark selected as read',
  '批量标未读': 'Mark selected as unread',
  '批量删除': 'Delete selected',
  '批量移动到...': 'Move selected to...',
  '移动到...': 'Move to...',
  '标为未读': 'Mark unread',
  '标为已读': 'Mark read',
  '删除': 'Delete',
  '彻底删除': 'Delete permanently',
  '关闭': 'Close',
  '收件人': 'To',
  '附件': 'Attachments',
  '没有附件': 'No attachments',
  '下载': 'Download',
  '系统设置': 'System settings',
  '每页显示邮件数': 'Messages per page',
  '回复引用位置': 'Reply quote position',
  '底部引用': 'Quote at bottom',
  '顶部引用': 'Quote at top',
  '界面语言': 'Language',
  '时区': 'Time zone',
  '中国上海（UTC+8）': 'Asia/Shanghai (UTC+8)',
  '协调世界时（UTC）': 'UTC',
  '美国洛杉矶（UTC-8/UTC-7）': 'America/Los Angeles (UTC-8/UTC-7)',
  '英国伦敦（UTC+0/UTC+1）': 'Europe/London (UTC+0/UTC+1)',
  '自动标记为已读（打开邮件时）': 'Mark messages as read when opened',
  '用户设置': 'User settings',
  '这些资料将和当前登录账号绑定保存。': 'These details are saved with the current account.',
  '显示名称': 'Display name',
  '职位/头衔': 'Title / role',
  '上传头像': 'Upload avatar',
  '支持本地上传图片，保存后自动绑定到当前登录用户。': 'Upload an image and bind it to the current account after saving.',
  '头像地址': 'Avatar URL',
  '个人简介': 'Bio',
  '主题设置': 'Theme',
  '即时切换浅色与深色主题，保存后下次登录继续生效。': 'Switch themes instantly and keep the choice for the next login.',
  '浅色主题': 'Light theme',
  '适合明亮环境和日间浏览': 'Best for bright environments and daytime use',
  '深色主题': 'Dark theme',
  '降低夜间使用时的屏幕刺激': 'Reduce screen glare at night',
  '修改密码': 'Change password',
  '仅更新当前会话保存的密码，并用新密码重新验证收信服务登录。': 'Update the saved password for this session and re-authenticate IMAP with the new password.',
  '旧密码': 'Current password',
  '新密码': 'New password',
  '确认新密码': 'Confirm new password',
  '更新密码': 'Update password',
  '关闭设置': 'Close settings',
  '保存设置': 'Save settings',
  '新建文件夹': 'Create folder',
  '文件夹名称': 'Folder name',
  '创建': 'Create',
  '重命名或删除': 'Rename or delete',
  '选择文件夹': 'Select folder',
  '新名称': 'New name',
  '重命名': 'Rename',
  '当前文件夹': 'Current folders',
  '联系人管理': 'Contacts',
  '支持分页搜索、分组标签筛选和联系人资料维护。': 'Search, filter, and maintain contacts with pagination.',
  '分组': 'Group',
  '标签': 'Tag',
  '全部分组': 'All groups',
  '全部标签': 'All tags',
  '新建联系人': 'New contact',
  '写信': 'Compose',
  '联系人列表': 'Contacts',
  '编辑联系人': 'Edit contact',
  '修改姓名、邮箱、手机、备注、分组和标签。': 'Edit name, email, phone, notes, groups, and tags.',
  '补全联系人基本信息后保存。': 'Fill in the basic contact information and save it.',
  '从此联系人写信': 'Compose to this contact',
  '姓名': 'Name',
  '手机': 'Phone',
  '备注': 'Notes',
  '最近联系：': 'Last contacted: ',
  '来源：': 'Source: ',
  '最近联系人': 'Recent contact',
  '手动维护': 'Manually maintained',
  '未选择联系人': 'No contact selected',
  '清空表单': 'Clear form',
  '删除中...': 'Deleting...',
  '保存中...': 'Saving...',
  '保存': 'Save',
  '暂无联系人，先新建一条吧。': 'No contacts yet. Create one to get started.',
  '回复并引用': 'Reply with quote',
  '打开联系人': 'Open contacts',
  '管理文件夹': 'Manage folders',
  '打开设置': 'Open settings',
  '邮件工作台': 'Mail workspace',
  '邮件阅读区': 'Mail reader',
  '选择一封邮件': 'Select a message',
  '从左侧列表选择邮件后，正文和附件会显示在这里。': 'Select a message from the list to view its body and attachments here.',
  '正在加载邮件详情...': 'Loading message details...',
  '邮件详情': 'Message details',
  '抄送': 'Cc',
  '时间': 'Time',
  '回复': 'Reply',
  '转发': 'Forward',
  '邮件正文': 'Message body',
  '无主题': 'No subject',
  '未命名附件': 'Unnamed attachment',
  '未知类型': 'Unknown type',
  '未知大小': 'Unknown size',
  '管理员登录': 'Admin sign in',
  '使用后台管理员账号登录。': 'Sign in with an administrator account.',
  '管理员账号': 'Admin username',
  '请输入管理员账号': 'Enter the admin username',
  '请输入密码': 'Enter the password',
  '登录中...': 'Signing in...',
  '登录后台': 'Sign in',
  '操作历史': 'Action history',
  '日志中心': 'Logs',
  '域名管理': 'Domain management',
  '用户管理': 'User management',
  '别名管理': 'Alias management',
  '配额管理': 'Quota management',
  '反垃圾策略': 'Anti-spam policies',
  '传输加密与证书': 'TLS and certificates',
  '邮件队列': 'Mail queue',
  '审计日志': 'Audit logs',
  '日志与监控': 'Logs and monitoring',
  '安全设置': 'Security',
  '当前主题：': 'Theme: ',
  '主题': 'Theme',
  '语言': 'Language',
  '退出': 'Sign out',
  '动态口令已启用': 'TOTP enabled',
  '动态口令未启用': 'TOTP disabled',
  '可操作': 'Ready',
  '提交中': 'Submitting',
  '动态口令管理': 'TOTP',
  '先初始化，再用验证码启用或停用。': 'Initialize first, then enable or disable it with a verification code.',
  '已启用': 'Enabled',
  '未启用': 'Disabled',
  '初始化状态': 'Initialization',
  '尚未初始化': 'Not initialized',
  '初始化动态口令': 'Initialize TOTP',
  '验证码': 'Verification code',
  '启用动态口令': 'Enable TOTP',
  '停用动态口令': 'Disable TOTP',
  '确认停用动态口令': 'Disable TOTP',
  '取消': 'Cancel',
  '确认停用': 'Disable now',
  '新增域名': 'Create domain',
  '编辑域名': 'Edit domain',
  '支持搜索、分页、详情和批量启停。': 'Search, paginate, inspect details, and enable or disable in bulk.',
  '配额上限(MB)': 'Quota limit (MB)',
  '状态': 'Status',
  '启用': 'Active',
  '停用': 'Disabled',
  '保存修改': 'Save changes',
  '创建域名': 'Create domain',
  '取消编辑': 'Cancel edit',
  '用户数': 'Users',
  '别名数': 'Aliases',
  '已用(MB)': 'Used (MB)',
  'DNS 检测': 'DNS check',
  '检测中...': 'Checking...',
  '关闭详情': 'Close details',
  '新增用户': 'Create user',
  '编辑用户': 'Edit user',
  '邮箱管理员': 'Mailbox admin',
  '标记为邮箱管理员': 'Mark as mailbox admin',
  '名称': 'Name',
  '上限(MB)': 'Limit (MB)',
  '使用率': 'Usage',
  '最后登录': 'Last login',
  '编辑': 'Edit',
  '重置密码': 'Reset password',
  '修改配额': 'Edit quota',
  '导入 CSV': 'Import CSV',
  '开始导入': 'Start import',
  '随机重置并展示': 'Generate random password',
  '确认重置': 'Reset password',
  '创建全收别名': 'Create catch-all alias',
  '新增别名': 'Create alias',
  '编辑别名': 'Edit alias',
  '支持多目标地址、冲突提示和启停切换。': 'Support multiple targets, conflict prompts, and enable or disable toggles.',
  '所属域': 'Domain',
  '请选择域名': 'Select a domain',
  '源地址': 'Source address',
  '目标地址': 'Target addresses',
  '添加目标地址': 'Add target address',
  '创建别名': 'Create alias',
  '作用域域名': 'Domain scope',
  '全局默认': 'Global default',
  '默认配额(MB)': 'Default quota (MB)',
  '启用 80% 预警': 'Enable 80% warning',
  '启用 90% 预警': 'Enable 90% warning',
  '启用 95% 预警': 'Enable 95% warning',
  '保存策略': 'Save policy',
  '搜索用户邮箱': 'Search user email',
  '全部域名': 'All domains',
  '批量更新配额': 'Bulk update quotas',
  '重算使用量': 'Recalculate usage',
  '保存配额': 'Save quota',
  '反垃圾评分阈值': 'Rspamd thresholds',
  '拒收阈值': 'Reject threshold',
  '加头阈值': 'Add-header threshold',
  '灰名单阈值': 'Greylist threshold',
  '保存阈值': 'Save thresholds',
  '域级发信认证状态': 'Per-domain sending authentication',
  '刷新': 'Refresh',
  '域级策略详情': 'Per-domain policy details',
  '轮换签名私钥': 'Rotate DKIM key',
  '确认轮换签名私钥': 'Rotate DKIM key',
  '确认轮换': 'Rotate key',
  '轮换中...': 'Rotating...',
  '全部': 'All',
  '排队中': 'Queued',
  '投递中': 'Delivering',
  '延迟重试': 'Deferred',
  '人工挂起': 'On hold',
  '查看': 'View',
  '重投': 'Requeue',
  '按状态清空': 'Clear by status',
  '复制编号': 'Copy queue id',
  '删除此项': 'Delete item',
  '队列编号': 'Queue id',
  '大小': 'Size',
  '入队时间': 'Queued at',
  '失败原因': 'Failure reason',
  '队列正文': 'Queue message body',
  '反垃圾服务': 'Rspamd',
  '投递服务': 'Postfix',
  '收信服务': 'Dovecot',
  '数据库': 'Database',
  '缓存服务': 'Redis',
  '应用服务': 'Application',
  '运行概览': 'Overview',
  '邮件服务状态': 'Mail services',
  '磁盘用量': 'Disk usage',
  '错误日志': 'Error logs',
  '服务': 'Service',
  '详情': 'Details',
  '挂载点': 'Mount point',
  '文件系统': 'Filesystem',
  '已用(GB)': 'Used (GB)',
  '可用(GB)': 'Free (GB)',
  '投递服务错误日志': 'Postfix error log',
  '收信服务错误日志': 'Dovecot error log',
  '传输加密证书状态': 'TLS certificates',
  '触发续签': 'Renew now',
  '证书目录': 'Certificate directory',
  '到期时间': 'Expires at',
  '覆盖域名': 'Covered domains',
  '证书路径': 'Certificate path',
  '确认触发证书续签': 'Renew certificates',
  '确认续签': 'Renew',
  '审计筛选': 'Audit filters',
  '自动刷新': 'Auto refresh',
  '开启': 'On',
  '关闭自动刷新': 'Off',
  '导出审计文件': 'Export audit file',
  '审计总数': 'Audit total',
  '最新时间': 'Latest event',
  '操作者': 'Actor',
  '目标': 'Target',
  '事件类型': 'Event type',
  '关键字': 'Keyword',
  '成功状态': 'Success status',
  '成功': 'Success',
  '失败': 'Failed',
  '日志筛选': 'Log filters',
  '导出日志文件': 'Export log file',
  '最近审计': 'Recent audit',
  '趋势': 'Trends',
  '队列总数': 'Queued mail',
  '活跃用户': 'Active users',
  '在线用户': 'Online users',
  '邮件域名': 'Mail domains',
  '最近 5 条': 'Last 5',
  '最近 5 天明细': 'Last 5 days',
  '等待在线用户数据': 'Waiting for online user data',
  '查看管理后台关键概览与最近运行状态。': 'View the key overview and recent runtime status of the admin console.',
  '查看后台关键操作历史、状态与执行细节。': 'Inspect recent admin actions, states, and execution details.',
  '按来源、级别和关键字查看后台日志，便于快速排障与追踪。': 'Inspect admin logs by source, level, and keyword.',
  '预留主题、语言、队列和审计相关的统一配置入口。': 'Centralized settings for theme, language, queue, and audit behavior.',
  '维护收发信域名与基础路由信息。': 'Manage mail domains and routing basics.',
  '查看管理员和邮箱用户的基础信息。': 'Manage admin and mailbox user basics.',
  '配置邮箱别名与转发关系。': 'Configure mailbox aliases and forwarding.',
  '展示账号容量与资源阈值。': 'Review mailbox capacity and resource thresholds.',
  '查看全局垃圾评分阈值，并聚合域级发件授权、域名策略与签名状态。': 'Review global spam thresholds plus per-domain SPF, DMARC, and DKIM status.',
  '查看当前证书状态，并触发证书续签。': 'Inspect certificate status and trigger renewals.',
  '查看当前邮件队列，并执行立即投递或删除操作。': 'Inspect the current mail queue and requeue or delete items.',
  '浏览后台关键操作记录。': 'Browse key admin operation records.',
  '查看邮件服务状态、磁盘用量与最近错误日志。': 'Inspect mail service health, disk usage, and recent error logs.',
  '修改管理员密码，并管理动态口令二次验证入口。': 'Change the admin password and manage TOTP verification.',
  '保留最近的后台动作脉络，便于快速追踪。': 'Keep the latest admin activity trail visible for quick tracing.',
  '只保留关键波动，避免把列表说明再次堆满。': 'Keep only the key movement and avoid stuffing the chart with repeated copy.',
  '保留必要状态，不再把大段说明塞进主视区。': 'Keep only the necessary states instead of filling the main view with long copy.',
  '把日粒度变化压成简洁列表，替代原来那种又挤又散的趋势文本。': 'Compress day-level changes into a compact list instead of the old crowded trend text.',
  '聚合基础应用健康、邮件服务状态、磁盘用量与最近错误日志。所有系统状态优先读取真实命令或日志文件。': 'Aggregate base app health, mail service status, disk usage, and recent error logs. All statuses prefer real commands or log files.',
  '重点关注投递服务、收信服务、反垃圾服务三个后台依赖。': 'Focus on the three core dependencies: delivery, mailbox, and anti-spam services.',
  '默认读取 `/` 与 `/var` 的磁盘使用情况，优先走 `df`，失败时回退 Python 标准库。': 'Read disk usage for `/` and `/var`, prefer `df`, and fall back to the Python standard library on failure.',
  '读取投递服务与收信服务最近若干行错误日志，用于后台一期最小排障闭环。': 'Read the latest delivery and mailbox service error logs for the phase-one troubleshooting loop.',
  '优先读取并更新垃圾评分阈值配置，开发环境缺失时返回明确降级状态。': 'Prefer reading and updating spam score thresholds, and return an explicit degraded state when the environment is missing.',
  '复用域名解析检测结果，并补本地签名私钥读取状态。': 'Reuse DNS check results and append the local signing key status.',
  '正常': 'Healthy',
  '不可用': 'Unavailable',
  '未提供': 'Not provided',
  '发件人授权': 'SPF',
  '域名策略': 'DMARC',
  '签名公钥解析': 'DKIM public key',
  '签名私钥': 'DKIM private key',
  '当前环境未安装 doveadm，无法读取在线用户数': 'The current environment does not have doveadm installed, so online users cannot be read.',
  '当前环境未安装 pgrep，无法探测服务进程': 'The current environment does not have pgrep installed, so service processes cannot be detected.',
  '当前环境未安装 journalctl，且未找到可读日志文件': 'The current environment does not have journalctl installed, and no readable log file was found.',
  '未找到 反垃圾服务 actions.conf，无法读取真实阈值': 'Rspamd actions.conf was not found, so real thresholds could not be read.',
  '数据库连接正常': 'Database connection is healthy.',
  'Redis 连通正常': 'Redis connectivity is healthy.',
  '后台动作': 'Admin actions',
  '后台 · auth · login': 'Admin · auth · login',
  '后台 · auth · refresh': 'Admin · auth · refresh',
  '后台 · 反垃圾 · 概览': 'Admin · anti-spam · overview',
  '后台 · ip · policy': 'Admin · IP · policy',
  '队列 · alert': 'Queue · alert',
  'online · 用户': 'Online · users',
  'cpu': 'CPU',
  'memory': 'Memory',
  '应用健康检查时间': 'Application health checked at',
  '收件箱': 'Inbox',
  '已发送': 'Sent',
  '草稿箱': 'Drafts',
  '垃圾邮件': 'Spam',
  '已删除': 'Trash',
  '回收站': 'Trash',
  '归档': 'Archive',
  '宋体': 'SimSun',
  '微软雅黑': 'Microsoft YaHei',
  '请输入链接地址': 'Enter a URL',
  '链接地址无效': 'Invalid URL',
  '只能插入图片文件': 'Only image files are allowed',
  '图片读取失败': 'Failed to read the image',
  '请输入签名名称': 'Enter a signature name',
  '签名加载失败': 'Failed to load signatures',
  '签名保存失败': 'Failed to save the signature',
  '签名删除失败': 'Failed to delete the signature',
  '设置默认签名失败': 'Failed to set the default signature',
  '请输入颜色值，例如 #1f2937': 'Enter a color value, for example #1f2937',
  '请输入表格行数': 'Enter the number of table rows',
  '请输入表格列数': 'Enter the number of table columns',
  '草稿加载失败': 'Failed to load the draft',
  '附件上传失败': 'Attachment upload failed',
  '加载文件夹失败': 'Failed to load folders',
  '加载邮件列表失败': 'Failed to load the message list',
  '邮件操作失败': 'Mail action failed',
  '收件人不能重复': 'Recipients cannot be duplicated',
  '这封邮件没有正文内容。': 'This message has no body content.',
  '邮件详情加载失败': 'Failed to load message details',
  '请选择目标文件夹': 'Select a target folder',
  '正在加载正文...': 'Loading message body...',
  '验证中...': 'Verifying...',
  '写信面板': 'Compose panel',
  '关闭写信': 'Close compose',
  '最小化写信': 'Minimize compose',
  '发送选项': 'Send options',
  '地址栏': 'Address fields',
  '插入变量': 'Insert variable',
  '更多操作': 'More actions',
  '添加附件': 'Add attachments',
  '上传中': 'Uploading',
  '上传失败': 'Upload failed',
  '丢弃草稿': 'Discard draft',
  '保存草稿': 'Save draft',
  '表格行数需为 1-20，列数需为 1-10': 'Rows must be 1-20 and columns must be 1-10',
  '暂无最近审计': 'No recent audit entries',
  '暂无趋势数据': 'No trend data',
  '暂无域配额数据': 'No domain quota data',
  '暂无用户配额数据': 'No user quota data',
  '暂无用户数据': 'No user data',
  '暂无反垃圾域名数据': 'No anti-spam domain data',
  '暂无符合条件的审计日志': 'No matching audit logs',
  '暂无服务状态': 'No service status',
  '暂无磁盘数据': 'No disk data',
  '暂无日志内容': 'No log content',
  '暂无证书数据': 'No certificate data',
  '暂无队列数据': 'No queue data',
  '发送': 'Sent',
  '动作': 'Actions',
  '加载中...': 'Loading...',
  '加载中': 'Loading',
  '总队列数': 'Total queued items',
  '当前筛选': 'Current filtered items',
  '最近 7 天': 'Last 7 days',
  '审计总量': 'Total audits',
  '发送总量': 'Total sent',
  '峰值': 'Peak',
  '等待后端配置接口接入': 'Waiting for backend configuration API',
  '暂无配置预览': 'No configuration preview',
  '刷新预览': 'Refresh preview',
  '备份投递服务配置': 'Backup Postfix config',
  '恢复配置': 'Restore config',
  '更新映射索引': 'Rebuild postmap',
  '更新别名索引': 'Rebuild postalias',
  '重载投递服务': 'Reload Postfix',
  '重载收信服务': 'Reload Dovecot',
  '服务动作': 'Service actions',
  '服务名': 'Service name',
  '启动': 'Start',
  '停止': 'Stop',
  '重启': 'Restart',
  '执行服务操作': 'Run action',
  '最近备份': 'Latest backup',
  '最近命令结果': 'Latest command result',
};

const PHRASE_TRANSLATIONS: Array<[string, string]> = [
  ['搜索: ', 'Search: '],
  ['关键词：', 'Keyword: '],
  ['发件人：', 'From: '],
  ['发送时间：', 'Sent at: '],
  ['收件人：', 'To: '],
  ['主题：', 'Subject: '],
  ['最近刷新：', 'Last updated: '],
  ['当前配置更新时间：', 'Config updated at: '],
  ['来源：', 'Source: '],
  ['行数：', 'Lines: '],
  ['应用健康检查时间 ', 'Application health checked at '],
  ['当前密钥：', 'Current secret: '],
  ['配置标识：', 'Provisioning label: '],
  ['密钥：', 'Secret: '],
  ['配置链接：', 'Provisioning URI: '],
  ['用户 ', 'Users '],
  ['别名 ', 'Aliases '],
];

const REGEX_TRANSLATIONS: Array<[RegExp, (...matches: string[]) => string]> = [
  [/^第 (\d+) \/ (\d+) 页$/, (page, total) => `Page ${page} / ${total}`],
  [/^第 (\d+) 页 \/ 共 (\d+) 页$/, (page, total) => `Page ${page} of ${total}`],
  [/^共 (\d+) 条$/, (count) => `${count} total`],
  [/^共 (\d+) 封$/, (count) => `${count} messages`],
  [/^已选 (\d+) 封$/, (count) => `${count} selected`],
  [/^(\d+) \/ (\d+)$/, (left, right) => `${left} / ${right}`],
  [/^已创建文件夹：(.+)$/, (name) => `Folder created: ${name}`],
  [/^已重命名文件夹：(.+) → (.+)$/, (source, target) => `Folder renamed: ${source} -> ${target}`],
  [/^已删除文件夹：(.+)$/, (name) => `Folder deleted: ${name}`],
  [/^域名 (.+) DNS 检测已完成。$/, (name) => `DNS check completed for ${name}.`],
  [/^域名已删除，影响用户 (\d+) 个、别名 (\d+) 个。$/, (users, aliases) => `Domain deleted. Impact: ${users} users, ${aliases} aliases.`],
  [/^批量状态更新完成：(.+)$/, (status) => `Bulk status update completed: ${translateText(status, 'en-US')}`],
  [/^CSV 导入完成：创建 (\d+) 条，跳过 (\d+) 条。$/, (created, skipped) => `CSV import completed: ${created} created, ${skipped} skipped.`],
  [/^已批量更新 (\d+) 个用户配额。$/, (count) => `Updated quotas for ${count} users.`],
  [/^其余 (\d+) 个收件人已省略$/, (count) => `${count} more recipients hidden`],
  [/^已导出 (.+)$/, (filename) => `Exported ${filename}`],
  [/^最近联系：(.+)$/, (value) => `Last contacted: ${value}`],
  [/^来源：(.*)$/, (value) => `Source: ${translateText(value.trim(), 'en-US')}`],
  [/^共 (\d+) \/ (\d+) 页$/, (page, total) => `${page} / ${total}`],
  [/^队列摘要$/, () => 'Queue summary'],
  [/^域详情：(.+)$/, (name) => `Domain details: ${name}`],
  [/^审计 (\d+)$/, (count) => `Audits ${count}`],
  [/^发送 (\d+)$/, (count) => `Sent ${count}`],
  [/^动作 (\d+)$/, (count) => `Actions ${count}`],
  [/^后台动作 (\d+)$/, (count) => `Admin actions ${count}`],
  [/^最近 (\d+) 天审计波动$/, (count) => `Audit movement over the last ${count} days`],
  [/^当前负载 1m=(.+) \/ 5m=(.+) \/ 15m=(.+)$/, (one, five, fifteen) => `Current load 1m=${one} / 5m=${five} / 15m=${fifteen}`],
  [/^当前进程峰值常驻内存约 (.+) MB$/, (value) => `Current process peak resident memory is about ${value} MB`],
  [/^deferred 队列 (\d+) 条$/, (count) => `Deferred queue ${count} items`],
  [/^白名单 (\d+) 项，黑名单 (\d+) 项$/, (allow, deny) => `Allowlist ${allow}, denylist ${deny}`],
  [/^应用健康检查时间 (.+)$/, (value) => `Application health checked at ${value}`],
];

const TEXT_NODE_ORIGINAL = new WeakMap<Text, string>();
const ATTRIBUTE_ORIGINAL = new WeakMap<Element, Map<string, string>>();
const TRANSLATABLE_ATTRIBUTES = ['placeholder', 'title', 'aria-label', 'data-placeholder'] as const;

function normalizeResources(locale: AppLocale) {
  return locale === 'zh-CN' ? {} : EXACT_TRANSLATIONS;
}

if (!i18next.isInitialized) {
  void i18next.use(initReactI18next).init({
    resources: {
      'zh-CN': { translation: {} },
      'en-US': { translation: normalizeResources('en-US') },
    },
    lng: DEFAULT_LOCALE,
    fallbackLng: DEFAULT_LOCALE,
    interpolation: { escapeValue: false },
    keySeparator: false,
    nsSeparator: false,
    returnEmptyString: false,
  });
}

let currentLocale: AppLocale = DEFAULT_LOCALE;

export function normalizeLocale(value?: string | null): AppLocale {
  return value === 'en-US' ? 'en-US' : 'zh-CN';
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function applyPhraseTranslations(value: string) {
  let current = value;
  PHRASE_TRANSLATIONS.forEach(([source, target]) => {
    current = current.replace(new RegExp(escapeRegExp(source), 'g'), target);
  });
  return current;
}

function applyRegexTranslations(value: string) {
  let current = value;
  REGEX_TRANSLATIONS.forEach(([pattern, resolver]) => {
    current = current.replace(pattern, (_, ...matches) => resolver(...matches.slice(0, -2)));
  });
  return current;
}

export function translateText(value?: string | null, locale: AppLocale = currentLocale) {
  if (!value) {
    return '';
  }
  if (locale === 'zh-CN') {
    return value;
  }
  if (Object.prototype.hasOwnProperty.call(EXACT_TRANSLATIONS, value)) {
    return EXACT_TRANSLATIONS[value];
  }
  const exact = i18next.t(value, { lng: locale, defaultValue: value });
  if (exact !== value) {
    return exact;
  }
  const withPhrases = applyPhraseTranslations(value);
  return applyRegexTranslations(withPhrases);
}

function isKnownTextVariant(source: string, current: string) {
  return current === source || current === translateText(source, 'en-US');
}

function isSkippableElement(element: Element | null) {
  if (!element) {
    return true;
  }
  if (element.closest('[data-i18n-skip="true"]')) {
    return true;
  }
  if (element.closest('script, style, textarea, pre, code')) {
    return true;
  }
  if (element.closest('[contenteditable="true"]')) {
    return true;
  }
  if (element.closest('.reading-html-body, .reading-text-body, .message-html-body, .message-text-body, .compose-body-editor, .compose-plain-editor')) {
    return true;
  }
  return false;
}

function applyTextNode(node: Text, locale: AppLocale) {
  const parent = node.parentElement;
  if (isSkippableElement(parent)) {
    return;
  }
  const current = node.textContent ?? '';
  const stored = TEXT_NODE_ORIGINAL.get(node);
  const source = !stored || !isKnownTextVariant(stored, current) ? current : stored;
  TEXT_NODE_ORIGINAL.set(node, source);
  const translated = translateText(source, locale);
  if (current !== translated) {
    node.textContent = translated;
  }
}

function applyAttributes(element: Element, locale: AppLocale) {
  if (isSkippableElement(element)) {
    return;
  }
  let originalMap = ATTRIBUTE_ORIGINAL.get(element);
  if (!originalMap) {
    originalMap = new Map<string, string>();
    ATTRIBUTE_ORIGINAL.set(element, originalMap);
  }
  TRANSLATABLE_ATTRIBUTES.forEach((attribute) => {
    const current = element.getAttribute(attribute);
    if (!current) {
      return;
    }
    const stored = originalMap!.get(attribute);
    const source = !stored || !isKnownTextVariant(stored, current) ? current : stored;
    originalMap!.set(attribute, source);
    const translated = translateText(source, locale);
    if (current !== translated) {
      element.setAttribute(attribute, translated);
    }
  });
}

function walkAndTranslate(root: Node, locale: AppLocale) {
  if (root.nodeType === Node.TEXT_NODE) {
    applyTextNode(root as Text, locale);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) {
    return;
  }

  if (root.nodeType === Node.ELEMENT_NODE) {
    applyAttributes(root as Element, locale);
  }

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const current = walker.currentNode;
    if (current.nodeType === Node.TEXT_NODE) {
      applyTextNode(current as Text, locale);
      continue;
    }
    applyAttributes(current as Element, locale);
  }
}

export function getRuntimeLocale() {
  return currentLocale;
}

export function setRuntimeLocale(locale?: string | null) {
  currentLocale = normalizeLocale(locale);
  document.documentElement.lang = currentLocale;
  void i18next.changeLanguage(currentLocale);
  window.dispatchEvent(new CustomEvent<AppLocale>(LOCALE_CHANGE_EVENT, { detail: currentLocale }));
}

export function formatLocaleDateTime(
  value: string | number | Date | null | undefined,
  options?: Intl.DateTimeFormatOptions & { locale?: string; timeZone?: string },
) {
  if (value === null || value === undefined || value === '') {
    return translateText('未提供');
  }
  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  const locale = normalizeLocale(options?.locale || currentLocale);
  const { locale: _ignoredLocale, ...formatOptions } = options || {};
  try {
    return new Intl.DateTimeFormat(locale, formatOptions).format(parsed);
  } catch {
    return new Intl.DateTimeFormat(locale).format(parsed);
  }
}

export function formatLocaleNumber(value: number | null | undefined, locale?: string) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—';
  }
  return new Intl.NumberFormat(normalizeLocale(locale || currentLocale)).format(value);
}

export function RuntimeI18nBridge() {
  useEffect(() => {
    const applyLocale = (locale: AppLocale) => {
      if (document.body) {
        walkAndTranslate(document.body, locale);
      }
    };

    const observer = new MutationObserver((mutations) => {
      const locale = getRuntimeLocale();
      mutations.forEach((mutation) => {
        if (mutation.type === 'characterData') {
          applyTextNode(mutation.target as Text, locale);
          return;
        }
        if (mutation.type === 'attributes' && mutation.target instanceof Element) {
          applyAttributes(mutation.target, locale);
          return;
        }
        mutation.addedNodes.forEach((node) => walkAndTranslate(node, locale));
      });
    });

    const onLocaleChange = (event: Event) => {
      const detail = (event as CustomEvent<AppLocale>).detail;
      applyLocale(normalizeLocale(detail));
    };

    window.addEventListener(LOCALE_CHANGE_EVENT, onLocaleChange);
    if (document.body) {
      applyLocale(getRuntimeLocale());
      observer.observe(document.body, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
        attributeFilter: [...TRANSLATABLE_ATTRIBUTES],
      });
    }

    return () => {
      observer.disconnect();
      window.removeEventListener(LOCALE_CHANGE_EVENT, onLocaleChange);
    };
  }, []);

  return null;
}
