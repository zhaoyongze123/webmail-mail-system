#!/usr/bin/env python3
"""更新和校验实施计划中的任务状态表。"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PLAN_PATH = Path("docs/IMPLEMENTATION_PLAN.md")
START_MARKER = "<!-- PLAN_STATUS_START -->"
END_MARKER = "<!-- PLAN_STATUS_END -->"
STATUSES = {"未开始", "进行中", "阻塞", "待验收", "已完成"}
HEADERS = ["任务ID", "状态", "负责人", "分支", "PR/提交", "测试命令", "测试结果", "完成时间", "风险备注"]


class PlanError(Exception):
    """计划文档格式或数据错误。"""


@dataclass
class TaskRow:
    task_id: str
    status: str
    owner: str
    branch: str
    reference: str
    tests: str
    result: str
    completed_at: str
    risk: str

    @classmethod
    def from_cells(cls, cells: list[str]) -> "TaskRow":
        if len(cells) != len(HEADERS):
            raise PlanError(f"任务行列数错误：期望 {len(HEADERS)} 列，实际 {len(cells)} 列：{cells}")
        return cls(*cells)

    def to_cells(self) -> list[str]:
        return [
            self.task_id,
            self.status,
            self.owner,
            self.branch,
            self.reference,
            self.tests,
            self.result,
            self.completed_at,
            self.risk,
        ]


def now_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def escape_cell(value: str | None) -> str:
    value = (value or "").strip()
    return value.replace("|", "\\|").replace("\n", "<br>")


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise PlanError(f"不是合法 Markdown 表格行：{line}")

    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in stripped[1:-1]:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    cells.append("".join(current).strip())
    return cells


def is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|", line.strip()))


def render_row(cells: list[str]) -> str:
    return "| " + " | ".join(escape_cell(cell) for cell in cells) + " |"


def load_plan(path: Path = PLAN_PATH) -> tuple[str, list[TaskRow], str]:
    if not path.exists():
        raise PlanError(f"计划文档不存在：{path}")

    content = path.read_text(encoding="utf-8")
    if START_MARKER not in content or END_MARKER not in content:
        raise PlanError("计划文档缺少 PLAN_STATUS 标记")

    before, rest = content.split(START_MARKER, 1)
    table_text, after = rest.split(END_MARKER, 1)
    table_lines = [line for line in table_text.strip().splitlines() if line.strip()]
    if len(table_lines) < 2:
        raise PlanError("计划状态表缺少表头或分隔行")

    headers = split_markdown_row(table_lines[0])
    if headers != HEADERS:
        raise PlanError(f"计划状态表表头不匹配：{headers}")
    if not is_separator(table_lines[1]):
        raise PlanError("计划状态表分隔行格式错误")

    rows = [TaskRow.from_cells(split_markdown_row(line)) for line in table_lines[2:]]
    return before, rows, after


def save_plan(before: str, rows: list[TaskRow], after: str, dry_run: bool) -> str:
    table_lines = [
        render_row(HEADERS),
        "| " + " | ".join("---" for _ in HEADERS) + " |",
        *(render_row(row.to_cells()) for row in rows),
    ]
    content = before + START_MARKER + "\n" + "\n".join(table_lines) + "\n" + END_MARKER + after
    if not dry_run:
        PLAN_PATH.write_text(content, encoding="utf-8")
    return content


def validate_rows(rows: list[TaskRow]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not re.fullmatch(r"T\d{2}", row.task_id):
            errors.append(f"{row.task_id}: 任务ID格式必须为 TNN")
        if row.task_id in seen:
            errors.append(f"{row.task_id}: 任务ID重复")
        seen.add(row.task_id)
        if row.status not in STATUSES:
            errors.append(f"{row.task_id}: 非法状态 {row.status}")
        if row.status == "已完成":
            if not row.reference:
                errors.append(f"{row.task_id}: 已完成任务必须填写 PR/提交")
            if not row.tests:
                errors.append(f"{row.task_id}: 已完成任务必须填写测试命令")
            if not row.result:
                errors.append(f"{row.task_id}: 已完成任务必须填写测试结果")
            if not row.completed_at:
                errors.append(f"{row.task_id}: 已完成任务必须填写完成时间")
        if row.status == "阻塞" and not row.risk:
            errors.append(f"{row.task_id}: 阻塞任务必须填写风险备注")
    return errors


def find_task(rows: list[TaskRow], task_id: str) -> TaskRow:
    for row in rows:
        if row.task_id == task_id:
            return row
    raise PlanError(f"任务不存在：{task_id}")


def update_task(args: argparse.Namespace) -> None:
    before, rows, after = load_plan()
    row = find_task(rows, args.task_id)

    if args.command == "start":
        row.status = "进行中"
        row.owner = args.owner or row.owner or "Codex"
        row.branch = args.branch or row.branch
    elif args.command == "block":
        row.status = "阻塞"
        row.risk = args.reason or row.risk
    elif args.command == "ready":
        row.status = "待验收"
        row.tests = args.tests or row.tests
        row.result = args.result or row.result or "待人工验收"
    elif args.command == "done":
        row.status = "已完成"
        row.reference = args.commit or row.reference
        row.tests = args.tests or row.tests
        row.result = args.result or row.result or "通过"
        row.completed_at = args.completed_at or now_text()
    elif args.command == "evidence":
        row.tests = args.tests or row.tests
        row.result = args.result or row.result
        if args.commit:
            row.reference = args.commit
    else:
        raise PlanError(f"不支持的命令：{args.command}")

    errors = validate_rows(rows)
    if errors:
        raise PlanError("\n".join(errors))

    save_plan(before, rows, after, args.dry_run)
    if args.dry_run:
        print(f"dry-run: {args.task_id} 可更新为 {row.status}")
    else:
        print(f"{args.task_id} 已更新为 {row.status}")


def validate_plan(_: argparse.Namespace) -> None:
    _, rows, _ = load_plan()
    errors = validate_rows(rows)
    expected_ids = [f"T{number:02d}" for number in range(1, len(rows) + 1)]
    actual_ids = [row.task_id for row in rows]
    if actual_ids != expected_ids:
        errors.append(f"任务ID必须连续为 T01 到 T{len(rows):02d}，实际为：{', '.join(actual_ids)}")

    if errors:
        raise PlanError("\n".join(errors))
    print(f"计划校验通过：{len(rows)} 个任务")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="更新和校验实施计划任务状态")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="校验计划状态表")
    validate.set_defaults(func=validate_plan)

    for command in ("start", "block", "ready", "done", "evidence"):
        sub = subparsers.add_parser(command, help=f"执行 {command} 状态更新")
        sub.add_argument("task_id")
        sub.add_argument("--dry-run", action="store_true", help="只校验不写入文件")
        sub.set_defaults(func=update_task)

    subparsers.choices["start"].add_argument("--owner", default="Codex")
    subparsers.choices["start"].add_argument("--branch")
    subparsers.choices["block"].add_argument("--reason", required=True)
    subparsers.choices["ready"].add_argument("--tests")
    subparsers.choices["ready"].add_argument("--result")
    subparsers.choices["done"].add_argument("--commit", required=True)
    subparsers.choices["done"].add_argument("--tests", required=True)
    subparsers.choices["done"].add_argument("--result", default="通过")
    subparsers.choices["done"].add_argument("--completed-at")
    subparsers.choices["evidence"].add_argument("--commit")
    subparsers.choices["evidence"].add_argument("--tests")
    subparsers.choices["evidence"].add_argument("--result")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except PlanError as exc:
        print(f"计划状态错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
