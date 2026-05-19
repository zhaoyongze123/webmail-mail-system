"""后台运维适配层。

本模块负责封装 P1 后台需要的外部系统探测能力，当前先提供：

- 白名单命令执行封装
- DNS 记录检测
- Postfix 队列探测与基础操作
- Dovecot 配额读取与重算
- Postfix / Dovecot 日志读取
- 磁盘使用量检测
- Postfix / Dovecot / Rspamd 服务状态检测

设计目标是：

- 开发环境无真实 mailserver 组件时也能返回可解释的降级结果
- 真实服务器环境存在 `dig` / `nslookup` 时优先复用系统命令
- 业务路由只消费统一结果结构，不直接处理子进程细节
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path
import re
from shutil import which
import shutil
import subprocess
import time

from app.config import get_settings


DEFAULT_COMMAND_TIMEOUT_SECONDS = 5.0
ALLOWED_COMMANDS = {"certbot", "df", "dig", "doveadm", "journalctl", "nslookup", "openssl", "pgrep", "postqueue", "postsuper", "systemctl"}
DEFAULT_DKIM_SELECTOR = "default"
DEFAULT_LOG_TAIL_LINES = 40

SERVICE_UNITS = {
    "postfix": ["postfix", "postfix.service"],
    "dovecot": ["dovecot", "dovecot.service"],
    "rspamd": ["rspamd", "rspamd.service"],
}
SERVICE_PROCESS_PATTERNS = {
    "postfix": "postfix/master",
    "dovecot": "dovecot",
    "rspamd": "rspamd",
}
LOG_CANDIDATE_PATHS = {
    "postfix": [
        "/var/log/mail.log",
        "/var/log/maillog",
        "/var/log/mail.err",
        "/var/log/mail/mail.log",
    ],
    "dovecot": [
        "/var/log/dovecot.log",
        "/var/log/mail.log",
        "/var/log/maillog",
        "/var/log/mail.err",
    ],
}
LOG_LABELS = {
    "postfix": "Postfix 错误日志",
    "dovecot": "Dovecot 错误日志",
}
RSPAMD_ACTION_DEFAULTS = {
    "reject": 15.0,
    "add_header": 6.0,
    "greylist": 4.0,
}


@dataclass(frozen=True)
class CommandResult:
    """标准化的命令执行结果。"""

    command: list[str]
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int

    @property
    def ok(self) -> bool:
        """是否成功执行。"""
        return self.exit_code == 0


def _normalize_lines(text: str) -> list[str]:
    """将输出文本按行清洗为非空列表。"""
    return [line.strip() for line in text.splitlines() if line.strip()]


def run_allowed_command(command: list[str], *, timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> CommandResult:
    """执行后台允许的系统命令。"""
    if not command:
        raise ValueError("命令不能为空")
    binary = command[0]
    if binary not in ALLOWED_COMMANDS:
        raise ValueError(f"不允许执行命令: {binary}")
    if which(binary) is None:
        return CommandResult(
            command=command,
            stdout="",
            stderr=f"命令不存在: {binary}",
            exit_code=127,
            duration_ms=0,
        )
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return CommandResult(
            command=command,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "").strip() or "命令执行超时",
            exit_code=124,
            duration_ms=duration_ms,
        )
    duration_ms = int((time.perf_counter() - start) * 1000)
    return CommandResult(
        command=command,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        exit_code=completed.returncode,
        duration_ms=duration_ms,
    )


def _query_with_dig(name: str, record_type: str) -> CommandResult:
    """使用 dig 查询 DNS。"""
    return run_allowed_command(["dig", "+short", name, record_type])


def _query_with_nslookup(name: str, record_type: str) -> CommandResult:
    """使用 nslookup 查询 DNS。"""
    return run_allowed_command(["nslookup", "-type=" + record_type, name])


def query_dns_records(name: str, record_type: str) -> dict[str, object]:
    """查询单个 DNS 记录类型并返回统一结构。"""
    result = _query_with_dig(name, record_type)
    backend = "dig"
    lines = _normalize_lines(result.stdout)
    if result.exit_code == 127:
        fallback = _query_with_nslookup(name, record_type)
        backend = "nslookup"
        result = fallback
        lines = _normalize_lines(fallback.stdout)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 dig / nslookup，无法执行真实 DNS 检测",
            "records": [],
            "backend": "none",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or f"{record_type} 查询失败",
            "records": [],
            "backend": backend,
            "command_result": _command_result_to_dict(result),
        }
    cleaned_records = [line.strip('"') for line in lines]
    if backend == "nslookup":
        cleaned_records = [
            line.split("=", 1)[1].strip().strip('"')
            for line in lines
            if "=" in line and not line.lower().startswith("server:")
        ]
    status = "ok" if cleaned_records else "missing"
    detail = f"检测到 {len(cleaned_records)} 条 {record_type} 记录" if cleaned_records else f"未检测到 {record_type} 记录"
    return {
        "status": status,
        "detail": detail,
        "records": cleaned_records,
        "backend": backend,
        "command_result": _command_result_to_dict(result),
    }


def _command_result_to_dict(result: CommandResult) -> dict[str, object]:
    """将命令结果序列化为接口可返回的字典。"""
    return {
        "command": result.command,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "ok": result.ok,
    }


def _build_unavailable_command_result(command: list[str], detail: str) -> dict[str, object]:
    """构造命令不可用时的兼容返回结构。"""
    return {
        "command": command,
        "stdout": "",
        "stderr": detail,
        "exit_code": 127,
        "duration_ms": 0,
        "ok": False,
    }


def run_domain_dns_check(domain_name: str, *, dkim_selector: str = DEFAULT_DKIM_SELECTOR) -> dict[str, object]:
    """执行域名 DNS 检测。"""
    mx = query_dns_records(domain_name, "MX")
    txt = query_dns_records(domain_name, "TXT")
    dmarc = query_dns_records(f"_dmarc.{domain_name}", "TXT")
    dkim = query_dns_records(f"{dkim_selector}._domainkey.{domain_name}", "TXT")

    spf_records = [record for record in txt["records"] if isinstance(record, str) and "v=spf1" in record.lower()]
    spf_status = "ok" if spf_records else ("unavailable" if txt["status"] == "unavailable" else "missing")

    checks = [
        {
            "key": "mx",
            "label": "MX",
            **mx,
        },
        {
            "key": "spf",
            "label": "SPF",
            "status": spf_status,
            "detail": "检测到 SPF 记录" if spf_records else ("当前环境无法检测 SPF" if txt["status"] == "unavailable" else "未检测到 SPF 记录"),
            "records": spf_records,
            "backend": txt["backend"],
            "command_result": txt["command_result"],
        },
        {
            "key": "dmarc",
            "label": "DMARC",
            **dmarc,
        },
        {
            "key": "dkim",
            "label": f"DKIM ({dkim_selector})",
            **dkim,
        },
    ]
    summary_status = "ok"
    if any(item["status"] == "error" for item in checks):
        summary_status = "error"
    elif any(item["status"] == "unavailable" for item in checks):
        summary_status = "unavailable"
    elif any(item["status"] == "missing" for item in checks):
        summary_status = "warning"
    return {
        "domain": domain_name,
        "checked_at": int(time.time()),
        "status": summary_status,
        "checks": checks,
    }


def _read_last_lines(path: Path, *, line_limit: int) -> list[str]:
    """读取文本文件最后 N 行，避免一次性载入整个日志。"""
    lines: deque[str] = deque(maxlen=max(line_limit, 1))
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if line.strip():
                lines.append(line)
    return list(lines)


def _read_log_from_file(log_key: str, *, line_limit: int) -> dict[str, object] | None:
    """优先从标准日志文件路径读取最近几行。"""
    for raw_path in LOG_CANDIDATE_PATHS.get(log_key, []):
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            continue
        try:
            lines = _read_last_lines(path, line_limit=line_limit)
        except OSError as exc:
            return {
                "key": log_key,
                "label": LOG_LABELS.get(log_key, log_key),
                "status": "error",
                "detail": f"读取日志文件失败: {exc}",
                "source": f"file:{path}",
                "lines": [],
                "line_count": 0,
                "command_result": None,
            }
        return {
            "key": log_key,
            "label": LOG_LABELS.get(log_key, log_key),
            "status": "ok",
            "detail": f"已从 {path} 读取最近 {len(lines)} 行日志",
            "source": f"file:{path}",
            "lines": lines,
            "line_count": len(lines),
            "command_result": None,
        }
    return None


def _read_log_from_journal(log_key: str, *, line_limit: int) -> dict[str, object]:
    """在日志文件缺失时回退到 journalctl。"""
    unit_names = SERVICE_UNITS.get(log_key, [])
    if which("journalctl") is None:
        return {
            "key": log_key,
            "label": LOG_LABELS.get(log_key, log_key),
            "status": "unavailable",
            "detail": "当前环境未安装 journalctl，且未找到可读日志文件",
            "source": "none",
            "lines": [],
            "line_count": 0,
            "command_result": _build_unavailable_command_result(["journalctl"], "命令不存在: journalctl"),
        }
    last_error: dict[str, object] | None = None
    for unit_name in unit_names:
        result = run_allowed_command(["journalctl", "-u", unit_name, "-n", str(max(line_limit, 1)), "--no-pager"], timeout_seconds=10.0)
        lines = _normalize_lines(result.stdout)
        if result.ok:
            return {
                "key": log_key,
                "label": LOG_LABELS.get(log_key, log_key),
                "status": "ok",
                "detail": f"已从 journalctl 读取最近 {len(lines)} 行日志",
                "source": f"journalctl:{unit_name}",
                "lines": lines,
                "line_count": len(lines),
                "command_result": _command_result_to_dict(result),
            }
        last_error = {
            "key": log_key,
            "label": LOG_LABELS.get(log_key, log_key),
            "status": "error",
            "detail": result.stderr or f"{unit_name} 日志读取失败",
            "source": f"journalctl:{unit_name}",
            "lines": [],
            "line_count": 0,
            "command_result": _command_result_to_dict(result),
        }
    return last_error or {
        "key": log_key,
        "label": LOG_LABELS.get(log_key, log_key),
        "status": "unavailable",
        "detail": "未配置可读取的日志来源",
        "source": "none",
        "lines": [],
        "line_count": 0,
        "command_result": None,
    }


def read_mail_service_log(log_key: str, *, line_limit: int = DEFAULT_LOG_TAIL_LINES) -> dict[str, object]:
    """读取单类邮件服务日志，优先文件，缺失时回退 journalctl。"""
    if log_key not in LOG_CANDIDATE_PATHS:
        return {
            "key": log_key,
            "label": log_key,
            "status": "error",
            "detail": "不支持的日志类型",
            "source": "none",
            "lines": [],
            "line_count": 0,
            "command_result": None,
        }
    file_result = _read_log_from_file(log_key, line_limit=line_limit)
    if file_result is not None:
        return file_result
    return _read_log_from_journal(log_key, line_limit=line_limit)


def list_mail_service_logs(*, line_limit: int = DEFAULT_LOG_TAIL_LINES) -> list[dict[str, object]]:
    """批量读取后台监控页面需要的邮件服务日志。"""
    return [
        read_mail_service_log("postfix", line_limit=line_limit),
        read_mail_service_log("dovecot", line_limit=line_limit),
    ]


def _check_service_with_systemctl(service_key: str) -> dict[str, object] | None:
    """优先通过 systemctl 探测服务运行状态。"""
    if which("systemctl") is None:
        return None
    for unit_name in SERVICE_UNITS.get(service_key, []):
        result = run_allowed_command(["systemctl", "is-active", unit_name], timeout_seconds=5.0)
        output = (result.stdout or result.stderr).strip().lower()
        if result.ok and output == "active":
            return {
                "name": service_key,
                "status": "ok",
                "detail": f"systemctl 显示 {unit_name} 正在运行",
                "source": f"systemctl:{unit_name}",
                "command_result": _command_result_to_dict(result),
            }
        if "could not be found" in output or "not been booted" in output:
            continue
        if output:
            return {
                "name": service_key,
                "status": "down",
                "detail": f"systemctl 显示 {unit_name} 状态为 {output}",
                "source": f"systemctl:{unit_name}",
                "command_result": _command_result_to_dict(result),
            }
    return None


def _check_service_with_pgrep(service_key: str) -> dict[str, object]:
    """在 systemctl 不可用时，通过进程匹配做最小探测。"""
    pattern = SERVICE_PROCESS_PATTERNS.get(service_key)
    if not pattern:
        return {
            "name": service_key,
            "status": "unavailable",
            "detail": "未配置服务进程匹配规则",
            "source": "none",
            "command_result": None,
        }
    result = run_allowed_command(["pgrep", "-f", pattern], timeout_seconds=5.0)
    if result.exit_code == 127:
        return {
            "name": service_key,
            "status": "unavailable",
            "detail": "当前环境未安装 pgrep，无法探测服务进程",
            "source": "none",
            "command_result": _command_result_to_dict(result),
        }
    pids = _normalize_lines(result.stdout)
    if result.ok and pids:
        return {
            "name": service_key,
            "status": "ok",
            "detail": f"检测到 {len(pids)} 个相关进程",
            "source": f"pgrep:{pattern}",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "name": service_key,
        "status": "down",
        "detail": f"未检测到 {service_key} 相关进程",
        "source": f"pgrep:{pattern}",
        "command_result": _command_result_to_dict(result),
    }


def get_service_health(service_key: str) -> dict[str, object]:
    """获取单个后台依赖服务的健康状态。"""
    systemctl_result = _check_service_with_systemctl(service_key)
    if systemctl_result is not None:
        return systemctl_result
    return _check_service_with_pgrep(service_key)


def list_service_health() -> list[dict[str, object]]:
    """列出后台关注的核心邮件服务健康状态。"""
    return [
        get_service_health("postfix"),
        get_service_health("dovecot"),
        get_service_health("rspamd"),
    ]


def _parse_df_output(text: str, path: str) -> dict[str, object] | None:
    """解析 `df -k` 的输出。"""
    lines = _normalize_lines(text)
    if len(lines) < 2:
        return None
    data_line = lines[-1]
    parts = data_line.split()
    if len(parts) < 6:
        return None
    filesystem, total_kb, used_kb, available_kb, usage_percent, mount_point = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
    total_gb = round(int(total_kb) / (1024 * 1024), 2)
    used_gb = round(int(used_kb) / (1024 * 1024), 2)
    free_gb = round(int(available_kb) / (1024 * 1024), 2)
    usage_ratio = float(str(usage_percent).rstrip("%") or 0)
    return {
        "name": path,
        "mount_point": mount_point,
        "filesystem": filesystem,
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "usage_percent": usage_ratio,
        "status": "critical" if usage_ratio >= 95 else ("warning" if usage_ratio >= 80 else "ok"),
    }


def get_disk_usage(path: str) -> dict[str, object]:
    """获取指定路径所在磁盘的使用情况。"""
    df_result = run_allowed_command(["df", "-k", path], timeout_seconds=5.0)
    if df_result.exit_code != 127 and df_result.ok:
        parsed = _parse_df_output(df_result.stdout, path)
        if parsed is not None:
            return {
                **parsed,
                "detail": f"{parsed['mount_point']} 已使用 {parsed['usage_percent']}%",
                "source": "df",
                "command_result": _command_result_to_dict(df_result),
            }
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return {
            "name": path,
            "mount_point": path,
            "filesystem": "unknown",
            "total_gb": 0.0,
            "used_gb": 0.0,
            "free_gb": 0.0,
            "usage_percent": 0.0,
            "status": "unavailable",
            "detail": f"无法读取磁盘使用量: {exc}",
            "source": "none",
            "command_result": _command_result_to_dict(df_result) if df_result.exit_code != 127 else None,
        }
    total_gb = round(usage.total / (1024 * 1024 * 1024), 2)
    used_gb = round((usage.total - usage.free) / (1024 * 1024 * 1024), 2)
    free_gb = round(usage.free / (1024 * 1024 * 1024), 2)
    usage_ratio = round(((usage.total - usage.free) / usage.total) * 100, 2) if usage.total else 0.0
    return {
        "name": path,
        "mount_point": path,
        "filesystem": "python",
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "usage_percent": usage_ratio,
        "status": "critical" if usage_ratio >= 95 else ("warning" if usage_ratio >= 80 else "ok"),
        "detail": f"{path} 已使用 {usage_ratio}%",
        "source": "shutil.disk_usage",
        "command_result": _command_result_to_dict(df_result) if df_result.exit_code != 127 else None,
    }


def list_disk_usage(paths: list[str] | None = None) -> list[dict[str, object]]:
    """批量获取后台监控页需要展示的磁盘使用量。"""
    requested_paths = paths or ["/", "/var"]
    seen_mounts: set[tuple[str, str]] = set()
    items: list[dict[str, object]] = []
    for path in requested_paths:
        item = get_disk_usage(path)
        identity = (str(item.get("mount_point") or path), str(item.get("filesystem") or ""))
        if identity in seen_mounts:
            continue
        seen_mounts.add(identity)
        items.append(item)
    return items


def _read_rspamd_actions_config() -> tuple[Path, str] | tuple[None, None]:
    """读取 Rspamd actions.conf 的原始文本。"""
    settings = get_settings()
    if not getattr(settings, "rspamd_enabled", True):
        return None, None
    path = Path(settings.rspamd_actions_config_path)
    if not path.exists() or not path.is_file():
        return None, None
    try:
        return path, path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return path, None


def get_rspamd_thresholds() -> dict[str, object]:
    """读取 Rspamd 全局垃圾分阈值。"""
    settings = get_settings()
    if not getattr(settings, "rspamd_enabled", True):
        return {
            "status": "unavailable",
            "detail": "当前环境已关闭 Rspamd 适配器",
            "thresholds": dict(RSPAMD_ACTION_DEFAULTS),
            "source": "disabled",
        }
    path, raw_text = _read_rspamd_actions_config()
    if path is None:
        return {
            "status": "unavailable",
            "detail": "未找到 Rspamd actions.conf，无法读取真实阈值",
            "thresholds": dict(RSPAMD_ACTION_DEFAULTS),
            "source": "defaults",
        }
    if raw_text is None:
        return {
            "status": "error",
            "detail": f"无法读取 {path}",
            "thresholds": dict(RSPAMD_ACTION_DEFAULTS),
            "source": f"file:{path}",
        }
    thresholds = dict(RSPAMD_ACTION_DEFAULTS)
    for key in list(thresholds):
        matched = re.search(rf"{re.escape(key)}\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*;", raw_text)
        if matched:
            thresholds[key] = float(matched.group(1))
    return {
        "status": "ok",
        "detail": f"已从 {path} 读取 Rspamd 阈值",
        "thresholds": thresholds,
        "source": f"file:{path}",
    }


def update_rspamd_thresholds(thresholds: dict[str, float]) -> dict[str, object]:
    """更新 Rspamd 全局垃圾分阈值。"""
    settings = get_settings()
    if not getattr(settings, "rspamd_enabled", True):
        return {
            "status": "unavailable",
            "detail": "当前环境已关闭 Rspamd 适配器",
            "thresholds": thresholds,
            "source": "disabled",
        }
    path = Path(settings.rspamd_actions_config_path)
    if not path.exists() or not path.is_file():
        return {
            "status": "unavailable",
            "detail": f"未找到 {path}，无法更新真实阈值",
            "thresholds": thresholds,
            "source": "none",
        }
    try:
        current_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "status": "error",
            "detail": f"读取配置失败: {exc}",
            "thresholds": thresholds,
            "source": f"file:{path}",
        }
    next_text = current_text
    for key, value in thresholds.items():
        pattern = rf"({re.escape(key)}\s*=\s*)([0-9]+(?:\.[0-9]+)?)(\s*;)"
        replacement = rf"\g<1>{value:.1f}\g<3>"
        if re.search(pattern, next_text):
            next_text = re.sub(pattern, replacement, next_text, count=1)
        else:
            next_text = next_text.rstrip() + f"\n{key} = {value:.1f};\n"
    try:
        path.write_text(next_text, encoding="utf-8")
    except OSError as exc:
        return {
            "status": "error",
            "detail": f"写入配置失败: {exc}",
            "thresholds": thresholds,
            "source": f"file:{path}",
        }
    return {
        "status": "ok",
        "detail": f"已更新 {path} 中的 Rspamd 阈值",
        "thresholds": thresholds,
        "source": f"file:{path}",
    }


def _safe_domain_key_filename(domain_name: str, selector: str) -> str:
    """构造可落盘的 DKIM 密钥文件名。"""
    normalized_domain = domain_name.strip().lower().replace("*", "wildcard")
    return f"{normalized_domain}.{selector}.key"


def get_domain_dkim_info(domain_name: str, *, selector: str | None = None) -> dict[str, object]:
    """读取域名对应的 DKIM 私钥信息。"""
    settings = get_settings()
    resolved_selector = selector or settings.rspamd_default_dkim_selector
    key_dir = Path(settings.rspamd_dkim_key_dir)
    key_path = key_dir / _safe_domain_key_filename(domain_name, resolved_selector)
    if not key_path.exists():
        return {
            "status": "unavailable",
            "detail": f"未找到 DKIM 私钥文件 {key_path}",
            "selector": resolved_selector,
            "path": str(key_path),
            "public_key": None,
            "exists": False,
        }
    public_key_result = run_allowed_command(["openssl", "rsa", "-in", str(key_path), "-pubout"], timeout_seconds=10.0)
    if public_key_result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 openssl，无法导出 DKIM 公钥",
            "selector": resolved_selector,
            "path": str(key_path),
            "public_key": None,
            "exists": True,
            "command_result": _command_result_to_dict(public_key_result),
        }
    if not public_key_result.ok:
        return {
            "status": "error",
            "detail": public_key_result.stderr or "导出 DKIM 公钥失败",
            "selector": resolved_selector,
            "path": str(key_path),
            "public_key": None,
            "exists": True,
            "command_result": _command_result_to_dict(public_key_result),
        }
    return {
        "status": "ok",
        "detail": f"已读取 {domain_name} 的 DKIM 公钥",
        "selector": resolved_selector,
        "path": str(key_path),
        "public_key": public_key_result.stdout.strip(),
        "exists": True,
        "command_result": _command_result_to_dict(public_key_result),
    }


def rotate_domain_dkim_key(domain_name: str, *, selector: str | None = None) -> dict[str, object]:
    """为指定域名重新生成 DKIM 私钥。"""
    settings = get_settings()
    resolved_selector = selector or settings.rspamd_default_dkim_selector
    key_dir = Path(settings.rspamd_dkim_key_dir)
    key_path = key_dir / _safe_domain_key_filename(domain_name, resolved_selector)
    try:
        key_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {
            "status": "error",
            "detail": f"创建 DKIM 目录失败: {exc}",
            "selector": resolved_selector,
            "path": str(key_path),
        }
    result = run_allowed_command(["openssl", "genrsa", "-out", str(key_path), "2048"], timeout_seconds=15.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 openssl，无法轮换 DKIM 私钥",
            "selector": resolved_selector,
            "path": str(key_path),
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "生成 DKIM 私钥失败",
            "selector": resolved_selector,
            "path": str(key_path),
            "command_result": _command_result_to_dict(result),
        }
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    info = get_domain_dkim_info(domain_name, selector=resolved_selector)
    return {
        "status": "ok" if info["status"] == "ok" else info["status"],
        "detail": f"已为 {domain_name} 重新生成 DKIM 私钥",
        "selector": resolved_selector,
        "path": str(key_path),
        "public_key": info.get("public_key"),
        "command_result": _command_result_to_dict(result),
    }


def _parse_certificate_domains(text: str) -> list[str]:
    """从 openssl 输出中提取证书覆盖域名。"""
    domains: list[str] = []
    dns_matches = re.findall(r"DNS:([^\s,]+)", text)
    for item in dns_matches:
        candidate = item.strip()
        if candidate and candidate not in domains:
            domains.append(candidate)
    if not domains:
        subject_match = re.search(r"Subject:.*?CN\s*=\s*([^\s,/]+)", text)
        if subject_match:
            domains.append(subject_match.group(1).strip())
    return domains


def get_tls_certificates() -> dict[str, object]:
    """读取 Let’s Encrypt live 目录中的证书状态。"""
    settings = get_settings()
    if not getattr(settings, "tls_enabled", True):
        return {
            "status": "unavailable",
            "detail": "当前环境已关闭 TLS 适配器",
            "items": [],
        }
    live_dir = Path(settings.tls_live_dir)
    if not live_dir.exists() or not live_dir.is_dir():
        return {
            "status": "unavailable",
            "detail": f"未找到证书目录 {live_dir}",
            "items": [],
        }
    items: list[dict[str, object]] = []
    for certificate_dir in sorted([path for path in live_dir.iterdir() if path.is_dir()], key=lambda item: item.name):
        cert_path = certificate_dir / "fullchain.pem"
        if not cert_path.exists():
            continue
        enddate_result = run_allowed_command(["openssl", "x509", "-in", str(cert_path), "-noout", "-enddate"], timeout_seconds=10.0)
        text_result = run_allowed_command(["openssl", "x509", "-in", str(cert_path), "-noout", "-text"], timeout_seconds=10.0)
        if enddate_result.exit_code == 127 or text_result.exit_code == 127:
            return {
                "status": "unavailable",
                "detail": "当前环境未安装 openssl，无法读取证书状态",
                "items": [],
            }
        if not enddate_result.ok or not text_result.ok:
            items.append(
                {
                    "name": certificate_dir.name,
                    "status": "error",
                    "detail": enddate_result.stderr or text_result.stderr or "证书读取失败",
                    "certificate_path": str(cert_path),
                    "expires_at": None,
                    "domains": [],
                }
            )
            continue
        enddate_text = enddate_result.stdout.strip()
        expires_at = enddate_text.split("=", 1)[1].strip() if "=" in enddate_text else enddate_text
        domains = _parse_certificate_domains(text_result.stdout)
        items.append(
            {
                "name": certificate_dir.name,
                "status": "ok",
                "detail": f"证书将于 {expires_at} 到期",
                "certificate_path": str(cert_path),
                "expires_at": expires_at,
                "domains": domains,
            }
        )
    if not items:
        return {
            "status": "unavailable",
            "detail": f"{live_dir} 下暂无可读取证书",
            "items": [],
        }
    return {
        "status": "ok",
        "detail": f"已读取 {len(items)} 份证书",
        "items": items,
    }


def renew_tls_certificates() -> dict[str, object]:
    """触发 certbot 续签。"""
    settings = get_settings()
    if not getattr(settings, "tls_enabled", True):
        return {
            "status": "unavailable",
            "detail": "当前环境已关闭 TLS 适配器",
            "command_result": None,
        }
    certbot_command = settings.tls_certbot_command.strip() or "certbot"
    result = run_allowed_command([certbot_command, "renew"], timeout_seconds=60.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 certbot，无法触发续签",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "证书续签执行失败",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": "已触发 certbot renew",
        "command_result": _command_result_to_dict(result),
    }


def _queue_status_from_name(queue_name: str | None) -> str:
    """将 Postfix 队列名标准化为后台状态枚举。"""
    normalized = (queue_name or "").strip().lower()
    if normalized in {"active", "deferred", "hold", "incoming", "maildrop"}:
        return normalized
    return "unknown"


def _parse_postqueue_json_lines(text: str) -> list[dict[str, object]]:
    """解析 `postqueue -j` 的 JSON Lines 输出。"""
    items: list[dict[str, object]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        queue_id = str(payload.get("queue_id") or payload.get("queue_name") or "").strip()
        sender = str(payload.get("sender") or payload.get("from") or "-").strip() or "-"
        recipients = [str(item).strip() for item in payload.get("recipients") or [] if str(item).strip()]
        queue_name = str(payload.get("queue_name") or payload.get("queue") or "").strip()
        arrival_time = payload.get("arrival_time")
        message_size = payload.get("message_size")
        items.append(
            {
                "id": queue_id or "-",
                "queue_id": queue_id or "-",
                "status": _queue_status_from_name(queue_name),
                "queue_name": queue_name or "unknown",
                "sender": sender,
                "recipients": recipients,
                "recipient_count": len(recipients),
                "message_size": int(message_size or 0),
                "arrival_time": int(arrival_time or 0),
                "created_at": int(arrival_time or 0),
                "name": queue_id or "-",
                "description": f"{sender} -> {', '.join(recipients[:2]) if recipients else '-'}",
            }
        )
    return items


def list_mail_queue() -> dict[str, object]:
    """列出 Postfix 队列。"""
    result = run_allowed_command(["postqueue", "-j"])
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 postqueue，无法查看真实队列",
            "items": [],
            "summary": {"total": 0},
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "队列查询失败",
            "items": [],
            "summary": {"total": 0},
            "command_result": _command_result_to_dict(result),
        }
    items = _parse_postqueue_json_lines(result.stdout)
    summary: dict[str, int] = {"total": len(items)}
    for item in items:
        status = str(item["status"])
        summary[status] = summary.get(status, 0) + 1
    return {
        "status": "ok",
        "detail": f"当前检测到 {len(items)} 条队列邮件",
        "items": items,
        "summary": summary,
        "command_result": _command_result_to_dict(result),
    }


def flush_mail_queue() -> dict[str, object]:
    """触发 Postfix 队列重投递。"""
    result = run_allowed_command(["postqueue", "-f"])
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 postqueue，无法执行 flush",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "队列 flush 失败",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": "已触发 Postfix 队列 flush",
        "command_result": _command_result_to_dict(result),
    }


def delete_mail_queue_item(queue_id: str) -> dict[str, object]:
    """删除指定队列邮件。"""
    normalized_id = queue_id.strip()
    if not normalized_id:
        return {
            "status": "error",
            "detail": "队列 ID 不能为空",
            "command_result": {
                "command": ["postsuper", "-d", normalized_id],
                "stdout": "",
                "stderr": "队列 ID 不能为空",
                "exit_code": 1,
                "duration_ms": 0,
                "ok": False,
            },
        }
    result = run_allowed_command(["postsuper", "-d", normalized_id])
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 postsuper，无法删除队列邮件",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "删除队列邮件失败",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": f"已请求删除队列邮件 {normalized_id}",
        "command_result": _command_result_to_dict(result),
    }


def _parse_quota_bytes(text: str) -> int | None:
    """从 doveadm quota 输出中提取字节值。"""
    for line in text.splitlines():
        if "storage" not in line.lower():
            continue
        matched = re.search(r"(\d+)\s+(\d+)\s*$", line.strip())
        if matched:
            return int(matched.group(1))
    generic = re.search(r"\b(\d+)\b", text)
    if generic:
        return int(generic.group(1))
    return None


def get_mailbox_quota_usage(email: str) -> dict[str, object]:
    """读取单个邮箱当前配额使用量。"""
    settings = get_settings()
    if not getattr(settings, "mail_quota_enabled", True):
        return {
            "status": "unavailable",
            "detail": "当前环境已关闭 doveadm quota 适配器",
            "used_quota_mb": None,
            "usage_source": "disabled",
            "command_result": None,
        }
    result = run_allowed_command(["doveadm", "quota", "get", "-u", email], timeout_seconds=10.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 doveadm，无法读取真实配额",
            "used_quota_mb": None,
            "usage_source": "unavailable",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "doveadm quota get 执行失败",
            "used_quota_mb": None,
            "usage_source": "error",
            "command_result": _command_result_to_dict(result),
        }
    used_bytes = _parse_quota_bytes(result.stdout)
    if used_bytes is None:
        return {
            "status": "error",
            "detail": "无法解析 doveadm quota get 输出",
            "used_quota_mb": None,
            "usage_source": "parse_error",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": "已读取 Dovecot 配额使用量",
        "used_quota_mb": round(used_bytes / (1024 * 1024), 2),
        "usage_source": "doveadm",
        "command_result": _command_result_to_dict(result),
    }


def recalc_mailbox_quota_usage(email: str) -> dict[str, object]:
    """触发单个邮箱配额重算。"""
    settings = get_settings()
    if not getattr(settings, "mail_quota_enabled", True):
        return {
            "status": "unavailable",
            "detail": "当前环境已关闭 doveadm quota 适配器",
            "command_result": None,
        }
    result = run_allowed_command(["doveadm", "quota", "recalc", "-u", email], timeout_seconds=30.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 doveadm，无法重算真实配额",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "doveadm quota recalc 执行失败",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": f"已触发 {email} 的配额重算",
        "command_result": _command_result_to_dict(result),
    }
