#!/usr/bin/env python3
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from review_gate import MAX_REPAIRS, TITLE_PRIORITY, finding_priority, git, independent_review, repair_prompt, snapshot


TAG = "[심층]"
MAX_NOTICE_FINDINGS = 6


def notice(message):
    return {"systemMessage": f"[심층] {message}"}


def state_directory(event, codex_home):
    identifiers = event["session_id"], event["turn_id"]
    if any(not isinstance(value, str) or not value for value in identifiers):
        raise ValueError("Invalid hook turn identifiers")
    key = hashlib.sha256(json.dumps(identifiers).encode()).hexdigest()
    return codex_home / "deep-review-runs" / key


def save_state(path, state):
    temporary = path.with_name("pending.json")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def lock_state(directory):
    with (directory / "status.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def start_review(event, directory, task):
    status = directory / "status.json"
    if status.exists():
        return {}
    if event.get("permission_mode") == "plan":
        return notice("계획 모드에서는 자동 검수를 건너뛰었어요.")
    cwd = Path(event["cwd"])
    try:
        root = Path(git(cwd, "rev-parse", "--show-toplevel"))
    except subprocess.CalledProcessError:
        return notice("Git 작업 트리를 찾지 못해 자동 검수를 건너뛰었어요.")
    base = snapshot(root)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_state(directory):
        if not status.exists():
            save_state(status, {"root": str(root), "task": task, "base": base, "reviews": 0, "result": "pending"})
    return {}


def finish_review(state, result, message):
    state["result"] = result
    state.pop("task", None)
    return notice(message)


def excerpt(text, limit):
    line = " ".join(text.split())
    return line if len(line) <= limit else line[:limit - 1] + "…"


def findings_report(findings, report_path):
    lines = [f"검수 지적 {len(findings)}건이에요."]
    for finding in sorted(findings, key=finding_priority)[:MAX_NOTICE_FINDINGS]:
        label = f"P{finding_priority(finding)}"
        location = finding["code_location"]
        title = excerpt(TITLE_PRIORITY.sub("", finding["title"], count=1), 110)
        body = excerpt(finding["body"], 170)
        path = excerpt(location["path"], 140)
        lines.append(f"- {label} · {path}:{location['line_range']['start']} · {title}\n  {body}")
    remaining = len(findings) - MAX_NOTICE_FINDINGS
    if remaining > 0:
        lines.append(f"나머지 {remaining}건은 전체 검수 결과에서 확인할 수 있어요.")
    lines.append(f"전체 검수 결과 {report_path}")
    return "\n".join(lines)


def stop_review(directory, state, codex_home, executable):
    root = Path(state["root"])
    current = snapshot(root)
    if current == state["base"]:
        message = "자동 수정 후 작업 전 상태와 같아졌어요." if state["reviews"] else "이 작업의 코드 변경이 없어 검수를 건너뛰었어요."
        return finish_review(state, "no-changes", message)
    if current == state.get("last_reviewed"):
        report_path = directory / f"review-{state['reviews']}" / "findings.json"
        findings = json.loads(report_path.read_text(encoding="utf-8"))["findings"]
        return finish_review(state, "no-progress",
                             "수정 후 변경분이 같아 재검수를 종료했어요.\n" +
                             findings_report(findings, report_path))

    number = state["reviews"] + 1
    review_dir = directory / f"review-{number}"
    findings = independent_review(root, review_dir, state["base"], current,
                                  state["task"], codex_home / "sessions", executable=executable)
    if snapshot(root) != current:
        return finish_review(state, "worktree-changed",
                             f"검수 중 작업 트리가 바뀌어 결과를 적용하지 않았어요. 기록 {directory}")
    state.update(reviews=number, last_reviewed=current)
    if not findings:
        return finish_review(state, "review-clear", f"독립 검수 {number}회 완료. 남은 지적이 없어요.")
    urgent = [finding for finding in findings if finding["priority"] <= 1]
    report_path = review_dir / "findings.json"
    if not urgent:
        return finish_review(state, "report-only", f"독립 검수 {number}회 완료.\n" + findings_report(findings, report_path))
    if number > MAX_REPAIRS:
        return finish_review(state, "repair-limit", "자동 수정 한도에 도달했어요.\n" + findings_report(findings, report_path))
    state["result"] = "pending"
    reason = "Restrict the automatic repair to the P0/P1 findings listed below.\n\n" + repair_prompt(urgent, "")
    report_only = [finding for finding in findings if finding["priority"] >= 2]
    response = {"decision": "block", "reason": reason}
    if report_only:
        response.update(notice(findings_report(report_only, report_path)))
    return response


def run(event, codex_home, executable):
    kind = event.get("hook_event_name")
    if kind == "UserPromptSubmit":
        prompt = event.get("prompt", "").lstrip(" \t")
        if event.get("agent_id") or not prompt.startswith(TAG):
            return {}
        return start_review(event, state_directory(event, codex_home), prompt[len(TAG):].strip())
    if kind not in ("Stop", "Interrupt"):
        return {}
    directory = state_directory(event, codex_home)
    state_path = directory / "status.json"
    if not state_path.exists():
        return {}
    with lock_state(directory):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if kind == "Interrupt" and state["result"] in ("pending", "reviewing"):
            result = finish_review(state, "interrupted", "중단 요청에 따라 자동 검수를 종료했어요.")
            save_state(state_path, state)
            return result
        if state["result"] != "pending":
            return {}
        state["result"] = "reviewing"
        save_state(state_path, state)
    try:
        result = stop_review(directory, state, codex_home, executable)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError) as exc:
        (directory / "error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        result = finish_review(state, "error", f"자동 검수를 완료하지 못했어요. 기록 {directory}")
    with lock_state(directory):
        if json.loads(state_path.read_text(encoding="utf-8"))["result"] != "reviewing":
            return {}
        save_state(state_path, state)
    return result


def main():
    os.umask(0o077)
    default_home = os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    codex_home = Path(sys.argv[2] if len(sys.argv) > 2 else default_home).resolve()
    try:
        result = run(json.load(sys.stdin), codex_home, sys.argv[1])
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result = notice(f"자동 검수를 준비하지 못했어요. {exc}")
    if result:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
