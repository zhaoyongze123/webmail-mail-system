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
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import re
from shutil import which
import shutil
import subprocess
import time
from typing import Any

from app.config import get_settings


DEFAULT_COMMAND_TIMEOUT_SECONDS = 5.0
UTC = timezone.utc
ALLOWED_COMMANDS = {
    "certbot",
    "df",
    "dig",
    "doveadm",
    "journalctl",
    "nslookup",
    "openssl",
    "pgrep",
    "postalias",
    "postcat",
    "postfix",
    "postmap",
    "postqueue",
    "postsuper",
    "systemctl",
}
DEFAULT_DKIM_SELECTOR = "default"
DEFAULT_LOG_TAIL_LINES = 40
DEFAULT_LOG_EXPORT_LIMIT = 500
CONFIG_PREVIEW_LINE_LIMIT = 120

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
        if (
            "could not be found" in output
            or "not been booted" in output
            or "failed to connect to bus" in output
            or "host is down" in output
            or "system has not been booted" in output
        ):
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


def _summarize_delay_reason(delay_reason: str | None) -> str:
    """把 Postfix 英文延迟原因压缩成更容易理解的中文摘要。"""
    normalized = (delay_reason or "").strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    if "user unknown" in lowered or "unknown user" in lowered or "mailbox unavailable" in lowered:
        return "收件人地址不存在或邮箱不可用"
    if "mailbox full" in lowered or "quota exceeded" in lowered or "over quota" in lowered:
        return "收件人邮箱空间不足"
    if "spam" in lowered or "unsolicited" in lowered or "blocked" in lowered:
        return "邮件被对方判定为垃圾邮件或被策略拦截"
    if "rate limit" in lowered or "too many" in lowered:
        return "投递频率过高，被对方限流"
    if "refused to talk to me" in lowered:
        if "421" in lowered or "450" in lowered or "451" in lowered or "452" in lowered:
            return "对方服务器临时拒绝建立连接，稍后会继续重试"
        return "对方服务器拒绝建立连接"
    if "connection timed out" in lowered or "timed out" in lowered:
        return "连接对方服务器超时"
    if "host or domain name not found" in lowered or "name service error" in lowered:
        return "收件域名解析失败"
    if any(code in lowered for code in ("421", "450", "451", "452", "4.7.", "4.4.", "4.2.")):
        return "对方服务器临时拒收，系统稍后会继续重试"
    if any(code in lowered for code in ("550", "551", "552", "553", "554", "5.7.", "5.1.", "5.2.")):
        return "对方服务器永久拒收，需要人工处理"
    return "投递失败，等待系统重试或人工处理"


def _normalize_queue_recipient(recipient: object) -> tuple[str, str | None, str | None]:
    """标准化 Postfix recipient 项，避免前端直接展示原始 JSON。"""
    if isinstance(recipient, dict):
        address = str(
            recipient.get("address")
            or recipient.get("recipient")
            or recipient.get("original_recipient")
            or ""
        ).strip()
        delay_reason = str(recipient.get("delay_reason") or recipient.get("reason") or "").strip() or None
        return address, delay_reason, _summarize_delay_reason(delay_reason)
    address = str(recipient).strip()
    return address, None, None


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
        recipient_details: list[dict[str, str]] = []
        recipients: list[str] = []
        for raw_recipient in payload.get("recipients") or []:
            address, delay_reason, delay_reason_display = _normalize_queue_recipient(raw_recipient)
            if not address:
                continue
            recipients.append(address)
            detail = {"address": address}
            if delay_reason:
                detail["delay_reason"] = delay_reason
            if delay_reason_display:
                detail["delay_reason_display"] = delay_reason_display
            recipient_details.append(detail)
        queue_name = str(payload.get("queue_name") or payload.get("queue") or "").strip()
        arrival_time = payload.get("arrival_time")
        message_size = payload.get("message_size")
        first_failure_reason = next(
            (item.get("delay_reason_display") or item.get("delay_reason") for item in recipient_details if item.get("delay_reason") or item.get("delay_reason_display")),
            "",
        )
        items.append(
            {
                "id": queue_id or "-",
                "queue_id": queue_id or "-",
                "status": _queue_status_from_name(queue_name),
                "queue_name": queue_name or "unknown",
                "sender": sender,
                "recipients": recipients,
                "recipient_details": recipient_details,
                "recipient_count": len(recipients),
                "message_size": int(message_size or 0),
                "arrival_time": int(arrival_time or 0),
                "created_at": int(arrival_time or 0),
                "name": queue_id or "-",
                "failure_reason": first_failure_reason,
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


def _parse_quota_kib(text: str) -> int | None:
    """从 `doveadm quota get` 输出中提取 KiB 值。"""
    for line in text.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered.startswith("quota name") or lowered.startswith("quota ") or lowered.startswith("root "):
            continue
        parts = re.split(r"\s+", normalized)
        if not parts:
            continue
        # doveadm quota get 常见输出为：
        # Quota name Type    Value Limit %
        # User quota STORAGE 12    500   2
        # 这里应取 STORAGE 行的 Value 列，而不是 Limit 列。
        if len(parts) >= 4 and parts[1].lower() == "storage":
            value = parts[2]
            if value.isdigit():
                return int(value)
        if "storage" not in lowered:
            continue
        matched = re.search(r"\bstorage\b.*?(\d+)(?:\s+(\d+))?(?:\s+\d+%?)?$", normalized, flags=re.IGNORECASE)
        if matched:
            return int(matched.group(1))
    generic = re.search(r"\b(\d+)\b", text)
    if generic:
        return int(generic.group(1))
    return None


def _quota_unavailable_detail(result_stdout: str, result_stderr: str) -> bool:
    """判断 doveadm quota 是否属于能力未启用而非普通执行错误。"""
    text = f"{result_stdout}\n{result_stderr}".lower()
    return "unknown command 'quota'" in text or "plugin quota exists" in text or "try to set mail_plugins=quota" in text


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
    if _quota_unavailable_detail(result.stdout, result.stderr):
        return {
            "status": "unavailable",
            "detail": result.stderr or "当前 Dovecot 未启用 quota 命令",
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
    used_kib = _parse_quota_kib(result.stdout)
    if used_kib is None:
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
        "used_quota_mb": round(used_kib / 1024, 2),
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
    if _quota_unavailable_detail(result.stdout, result.stderr):
        return {
            "status": "unavailable",
            "detail": result.stderr or "当前 Dovecot 未启用 quota 命令",
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


def _normalize_queue_id(queue_id: str) -> str:
    """标准化队列 ID，避免危险字符进入命令行。"""
    normalized = queue_id.strip()
    if not normalized or not re.fullmatch(r"[A-Za-z0-9]+", normalized):
        raise ValueError("队列 ID 不合法")
    return normalized


def get_mail_queue_message(queue_id: str) -> dict[str, object]:
    """读取指定队列邮件的原始正文。"""
    try:
        normalized_id = _normalize_queue_id(queue_id)
    except ValueError as exc:
        return {
            "status": "error",
            "detail": str(exc),
            "queue_id": queue_id,
            "content": "",
            "command_result": _build_unavailable_command_result(["postcat", "-q", queue_id], str(exc)),
        }
    result = run_allowed_command(["postcat", "-q", normalized_id], timeout_seconds=10.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 postcat，无法查看队列邮件正文",
            "queue_id": normalized_id,
            "content": "",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "读取队列邮件正文失败",
            "queue_id": normalized_id,
            "content": result.stdout,
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": f"已读取队列邮件 {normalized_id} 的正文",
        "queue_id": normalized_id,
        "content": result.stdout,
        "command_result": _command_result_to_dict(result),
    }


def requeue_mail_queue_item(queue_id: str) -> dict[str, object]:
    """重新投递指定队列邮件。"""
    try:
        normalized_id = _normalize_queue_id(queue_id)
    except ValueError as exc:
        return {
            "status": "error",
            "detail": str(exc),
            "queue_id": queue_id,
            "command_result": _build_unavailable_command_result(["postsuper", "-r", queue_id], str(exc)),
        }
    result = run_allowed_command(["postsuper", "-r", normalized_id], timeout_seconds=10.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 postsuper，无法重新投递队列邮件",
            "queue_id": normalized_id,
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "重新投递队列邮件失败",
            "queue_id": normalized_id,
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": f"已请求重新投递队列邮件 {normalized_id}",
        "queue_id": normalized_id,
        "command_result": _command_result_to_dict(result),
    }


def delete_mail_queue_items(queue_ids: list[str]) -> dict[str, object]:
    """批量删除队列邮件。"""
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    last_result: dict[str, object] | None = None
    for queue_id in queue_ids:
        result = delete_mail_queue_item(queue_id)
        last_result = result
        if result["status"] == "ok":
            deleted.append(queue_id)
        else:
            errors.append({"queue_id": queue_id, "detail": str(result["detail"])})
    status = "ok" if not errors else ("partial" if deleted else "error")
    return {
        "status": status,
        "detail": f"已删除 {len(deleted)} 条队列邮件" if not errors else f"成功 {len(deleted)} 条，失败 {len(errors)} 条",
        "deleted_count": len(deleted),
        "deleted_ids": deleted,
        "errors": errors,
        "command_result": last_result.get("command_result") if last_result else None,
    }


def clear_mail_queue(*, statuses: list[str] | None = None) -> dict[str, object]:
    """按状态过滤后清空队列。"""
    snapshot = list_mail_queue()
    if snapshot["status"] != "ok":
        return {
            "status": snapshot["status"],
            "detail": snapshot["detail"],
            "deleted_count": 0,
            "deleted_ids": [],
            "errors": [],
            "command_result": snapshot.get("command_result"),
        }
    normalized_statuses = {item.strip().lower() for item in (statuses or []) if item.strip()}
    target_ids = [
        str(item["queue_id"])
        for item in snapshot["items"]
        if not normalized_statuses or str(item["status"]).lower() in normalized_statuses
    ]
    if not target_ids:
        return {
            "status": "ok",
            "detail": "没有匹配的队列邮件需要清空",
            "deleted_count": 0,
            "deleted_ids": [],
            "errors": [],
            "command_result": snapshot.get("command_result"),
        }
    return delete_mail_queue_items(target_ids)


def _queue_size_summary(items: list[dict[str, object]]) -> dict[str, int]:
    """按队列状态聚合总大小。"""
    size_by_status: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        size_by_status[status] = size_by_status.get(status, 0) + int(item.get("message_size") or 0)
    return size_by_status


def get_mail_queue_snapshot(*, status_filter: str | None = None, q: str | None = None) -> dict[str, object]:
    """返回支持筛选和统计增强的队列快照。"""
    snapshot = list_mail_queue()
    if snapshot["status"] != "ok":
        return snapshot
    items = list(snapshot["items"])
    if status_filter:
        normalized_status = status_filter.strip().lower()
        items = [item for item in items if str(item["status"]).lower() == normalized_status]
    if q:
        keyword = q.strip().lower()
        items = [
            item
            for item in items
            if keyword in str(item.get("queue_id", "")).lower()
            or keyword in str(item.get("sender", "")).lower()
            or any(keyword in str(recipient).lower() for recipient in item.get("recipients", []))
        ]
    summary = dict(snapshot["summary"])
    summary["visible_total"] = len(items)
    summary["total_size_bytes"] = sum(int(item.get("message_size") or 0) for item in items)
    for key, value in _queue_size_summary(items).items():
        summary[f"{key}_size_bytes"] = value
    return {
        **snapshot,
        "items": items,
        "summary": summary,
    }


def _line_matches_log_query(
    line: str,
    *,
    query: str | None,
    status_filter: str | None,
    sender: str | None,
    recipient: str | None,
) -> bool:
    """按关键字和常见 mail log 维度过滤单行日志。"""
    lowered = line.lower()
    if query and query.strip().lower() not in lowered:
        return False
    if status_filter:
        normalized_status = status_filter.strip().lower()
        status_tokens = {
            "sent": [" status=sent ", " status=sent,", " status=sent("],
            "deferred": [" status=deferred "],
            "bounced": [" status=bounced "],
            "rejected": [" reject:", " rejected", " status=rejected "],
        }
        tokens = status_tokens.get(normalized_status, [normalized_status])
        if not any(token in lowered for token in tokens):
            return False
    if sender and sender.strip().lower() not in lowered:
        return False
    if recipient and recipient.strip().lower() not in lowered:
        return False
    return True


def search_mail_service_logs(
    *,
    log_key: str | None = None,
    query: str | None = None,
    status_filter: str | None = None,
    sender: str | None = None,
    recipient: str | None = None,
    line_limit: int = DEFAULT_LOG_EXPORT_LIMIT,
) -> dict[str, object]:
    """按后台常用条件检索邮件日志。"""
    log_keys = [log_key] if log_key else list(LOG_CANDIDATE_PATHS.keys())
    items: list[dict[str, object]] = []
    for current_key in log_keys:
        snapshot = read_mail_service_log(current_key, line_limit=line_limit)
        for index, line in enumerate(snapshot.get("lines", []), start=1):
            if not _line_matches_log_query(
                line,
                query=query,
                status_filter=status_filter,
                sender=sender,
                recipient=recipient,
            ):
                continue
            items.append(
                {
                    "id": f"{current_key}:{index}",
                    "log_key": current_key,
                    "label": snapshot.get("label"),
                    "status": snapshot.get("status"),
                    "source": snapshot.get("source"),
                    "line_number": index,
                    "summary": line[:200],
                    "raw": line,
                }
            )
    return {
        "status": "ok",
        "detail": f"共匹配 {len(items)} 条日志",
        "items": items,
    }


def export_log_items(items: list[dict[str, object]], *, format_name: str) -> dict[str, object]:
    """导出日志结果为 CSV 或 JSON 文本。"""
    normalized_format = format_name.strip().lower()
    if normalized_format == "json":
        content = json.dumps(items, ensure_ascii=False, indent=2)
        media_type = "application/json"
    elif normalized_format == "csv":
        header = "id,log_key,label,status,source,line_number,summary,raw"
        rows = [header]
        for item in items:
            values = [
                str(item.get("id", "")),
                str(item.get("log_key", "")),
                str(item.get("label", "")),
                str(item.get("status", "")),
                str(item.get("source", "")),
                str(item.get("line_number", "")),
                str(item.get("summary", "")).replace('"', '""'),
                str(item.get("raw", "")).replace('"', '""'),
            ]
            rows.append(",".join(f'"{value}"' for value in values))
        content = "\n".join(rows)
        media_type = "text/csv"
    else:
        raise ValueError("不支持的导出格式")
    return {
        "format": normalized_format,
        "content": content,
        "media_type": media_type,
        "filename": f"mail-logs.{normalized_format}",
    }


def _read_text_file_preview(path: Path, *, max_lines: int = CONFIG_PREVIEW_LINE_LIMIT) -> dict[str, object]:
    """读取配置文件预览文本。"""
    if not path.exists() or not path.is_file():
        return {
            "status": "unavailable",
            "detail": f"未找到配置文件 {path}",
            "path": str(path),
            "content": "",
        }
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {
            "status": "error",
            "detail": f"读取配置文件失败: {exc}",
            "path": str(path),
            "content": "",
        }
    preview = "\n".join(lines[:max_lines])
    return {
        "status": "ok",
        "detail": f"已读取 {path} 前 {min(len(lines), max_lines)} 行",
        "path": str(path),
        "content": preview,
        "line_count": len(lines),
    }


def get_mail_system_configs() -> dict[str, object]:
    """返回 Postfix 和 Dovecot 关键配置预览。"""
    settings = get_settings()
    postfix = _read_text_file_preview(Path(settings.postfix_main_cf_path))
    dovecot = _read_text_file_preview(Path(settings.dovecot_config_path))
    return {
        "status": "ok" if postfix["status"] == "ok" or dovecot["status"] == "ok" else "unavailable",
        "detail": "已读取邮件系统配置预览" if postfix["status"] == "ok" or dovecot["status"] == "ok" else "当前环境无法读取真实配置",
        "postfix": postfix,
        "dovecot": dovecot,
    }


def _ensure_backup_dir() -> Path:
    """确保后台配置备份目录存在。"""
    backup_dir = Path(get_settings().admin_config_backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def backup_config_file(path: Path) -> dict[str, object]:
    """备份配置文件到后台备份目录。"""
    if not path.exists() or not path.is_file():
        return {
            "status": "unavailable",
            "detail": f"未找到配置文件 {path}",
            "backup_path": None,
        }
    backup_dir = _ensure_backup_dir()
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup_path = backup_dir / f"{path.name}.{timestamp}.bak"
    try:
        shutil.copy2(path, backup_path)
    except OSError as exc:
        return {
            "status": "error",
            "detail": f"备份配置失败: {exc}",
            "backup_path": str(backup_path),
        }
    return {
        "status": "ok",
        "detail": f"已备份 {path.name}",
        "backup_path": str(backup_path),
    }


def restore_config_backup(backup_path: str, target_path: str) -> dict[str, object]:
    """从备份恢复配置文件。"""
    source = Path(backup_path)
    target = Path(target_path)
    if not source.exists() or not source.is_file():
        return {
            "status": "unavailable",
            "detail": f"未找到备份文件 {source}",
            "path": target_path,
        }
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        return {
            "status": "error",
            "detail": f"恢复配置失败: {exc}",
            "path": target_path,
        }
    return {
        "status": "ok",
        "detail": f"已从 {source.name} 恢复配置",
        "path": target_path,
    }


def rebuild_postfix_maps() -> dict[str, object]:
    """重建 Postfix virtual 映射表。"""
    settings = get_settings()
    result = run_allowed_command(["postmap", settings.postfix_virtual_aliases_path], timeout_seconds=15.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 postmap，无法重建 Postfix 映射表",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "重建 Postfix 映射表失败",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": "已重建 Postfix virtual 映射表",
        "command_result": _command_result_to_dict(result),
    }


def rebuild_system_aliases() -> dict[str, object]:
    """重建系统 aliases 数据库。"""
    settings = get_settings()
    result = run_allowed_command(["postalias", settings.postfix_system_aliases_path], timeout_seconds=15.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 postalias，无法重建别名表",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "重建别名表失败",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": "已重建系统别名表",
        "command_result": _command_result_to_dict(result),
    }


def reload_postfix_service() -> dict[str, object]:
    """重载 Postfix 配置。"""
    result = run_allowed_command(["postfix", "reload"], timeout_seconds=15.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 postfix 命令，无法重载 Postfix",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "Postfix 重载失败",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": "已触发 Postfix reload",
        "command_result": _command_result_to_dict(result),
    }


def reload_dovecot_service() -> dict[str, object]:
    """重载 Dovecot 配置。"""
    result = run_allowed_command(["doveadm", "reload"], timeout_seconds=15.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 doveadm，无法重载 Dovecot",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "Dovecot 重载失败",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": "已触发 Dovecot reload",
        "command_result": _command_result_to_dict(result),
    }


def control_mail_service(service_name: str, action: str) -> dict[str, object]:
    """通过 systemctl 控制邮件服务启停。"""
    normalized_service = service_name.strip().lower()
    normalized_action = action.strip().lower()
    if normalized_service not in SERVICE_UNITS:
        return {
            "status": "error",
            "detail": "不支持的服务名称",
            "command_result": None,
        }
    if normalized_action not in {"start", "stop", "restart"}:
        return {
            "status": "error",
            "detail": "不支持的服务动作",
            "command_result": None,
        }
    unit_name = SERVICE_UNITS[normalized_service][0]
    result = run_allowed_command(["systemctl", normalized_action, unit_name], timeout_seconds=20.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 systemctl，无法控制服务",
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or f"{unit_name} {normalized_action} 失败",
            "command_result": _command_result_to_dict(result),
        }
    return {
        "status": "ok",
        "detail": f"已执行 {unit_name} {normalized_action}",
        "command_result": _command_result_to_dict(result),
    }


def get_admin_ip_policy() -> dict[str, object]:
    """返回后台 IP 白名单/黑名单配置。"""
    settings = get_settings()
    raw_allowlist = getattr(settings, "admin_ip_allowlist_values", None)
    raw_blocklist = getattr(settings, "admin_ip_blocklist_values", None)
    if raw_allowlist is None:
        raw_allowlist = [item.strip() for item in str(getattr(settings, "admin_ip_allowlist", "") or "").split(",") if item.strip()]
    if raw_blocklist is None:
        raw_blocklist = [item.strip() for item in str(getattr(settings, "admin_ip_blocklist", "") or "").split(",") if item.strip()]
    return {
        "allowlist": list(raw_allowlist),
        "blocklist": list(raw_blocklist),
    }


def check_admin_ip_access(ip_address: str) -> dict[str, object]:
    """根据配置判断后台来源 IP 是否允许访问。"""
    normalized_ip = (ip_address or "").strip()
    policy = get_admin_ip_policy()
    if normalized_ip and normalized_ip in policy["blocklist"]:
        return {
            "status": "blocked",
            "detail": f"IP {normalized_ip} 在后台黑名单中",
            "ip": normalized_ip,
        }
    if policy["allowlist"] and normalized_ip not in policy["allowlist"]:
        return {
            "status": "blocked",
            "detail": f"IP {normalized_ip or 'unknown'} 不在后台白名单中",
            "ip": normalized_ip,
        }
    return {
        "status": "ok",
        "detail": "当前 IP 可访问后台",
        "ip": normalized_ip,
    }


def get_memory_usage_snapshot() -> dict[str, object]:
    """读取当前进程近似内存占用。"""
    try:
        usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        usage_mb = round(float(usage_kb) / 1024, 2)
    except Exception as exc:  # pragma: no cover - 平台差异兜底
        return {
            "status": "unavailable",
            "detail": f"无法读取内存使用量: {exc}",
            "used_mb": None,
        }
    return {
        "status": "ok",
        "detail": f"当前进程峰值常驻内存约 {usage_mb} MB",
        "used_mb": usage_mb,
    }


def get_cpu_usage_snapshot() -> dict[str, object]:
    """读取当前主机近似负载指标。"""
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError as exc:
        return {
            "status": "unavailable",
            "detail": f"无法读取 CPU 负载: {exc}",
            "load_1m": None,
            "load_5m": None,
            "load_15m": None,
        }
    return {
        "status": "ok",
        "detail": f"当前负载 1m={load1:.2f} / 5m={load5:.2f} / 15m={load15:.2f}",
        "load_1m": round(load1, 2),
        "load_5m": round(load5, 2),
        "load_15m": round(load15, 2),
    }


def get_online_dovecot_users() -> dict[str, object]:
    """读取 Dovecot 当前在线用户数。"""
    result = run_allowed_command(["doveadm", "who"], timeout_seconds=10.0)
    if result.exit_code == 127:
        return {
            "status": "unavailable",
            "detail": "当前环境未安装 doveadm，无法读取在线用户数",
            "online_user_count": 0,
            "command_result": _command_result_to_dict(result),
        }
    if not result.ok:
        return {
            "status": "error",
            "detail": result.stderr or "读取 Dovecot 在线用户失败",
            "online_user_count": 0,
            "command_result": _command_result_to_dict(result),
        }
    lines = _normalize_lines(result.stdout)
    return {
        "status": "ok",
        "detail": f"当前在线用户 {len(lines)} 个",
        "online_user_count": len(lines),
        "command_result": _command_result_to_dict(result),
    }
