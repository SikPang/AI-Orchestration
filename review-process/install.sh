#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="$(command -v python3 || true)"
CODEX_BIN="$(command -v codex || true)"

if [[ -z "$PYTHON_BIN" || -z "$CODEX_BIN" ]] || ! command -v git >/dev/null; then
  echo "오류: git, python3, codex CLI가 모두 필요해요." >&2
  exit 1
fi

if ! "$CODEX_BIN" exec -s read-only review --disable hooks --json --help >/dev/null; then
  echo "오류: JSON 리뷰를 지원하는 Codex CLI로 업데이트해 주세요." >&2
  exit 1
fi

"$PYTHON_BIN" - "$SOURCE_DIR" "$PYTHON_BIN" "$CODEX_BIN" <<'PY'
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path


def write_atomic(path, content):
    with tempfile.TemporaryDirectory(prefix=".deep-review-", dir=path.parent) as stage:
        temporary = Path(stage) / path.name
        temporary.write_bytes(content)
        temporary.replace(path)


def is_previous_deep_review(handler):
    if handler.get("type") != "command":
        return False
    try:
        args = shlex.split(handler["command"])
    except (KeyError, ValueError, TypeError):
        return False
    if len(args) not in (4, 5) or args[1] != "-B":
        return False
    return (Path(args[0]).name == "python3" and Path(args[3]).name == "codex"
            and Path(args[2]).parts[-3:] == ("hooks", "deep-review", "deep_review_hook.py"))


def install(source, python_bin, codex_bin, codex_home):
    hook_dir = codex_home / "hooks" / "deep-review"
    config_path = codex_home / "hooks.json"
    command = " ".join(shlex.quote(str(arg)) for arg in (
        python_bin, "-B", hook_dir / "deep_review_hook.py", codex_bin, codex_home,
    ))
    events = {"UserPromptSubmit": 25, "Stop": 575, "Interrupt": 3}
    scripts = {name: (source / name).read_bytes() for name in (
        "deep_review_hook.py", "review_gate.py", "review_usage.py",
    )}
    original = config_path.read_bytes() if config_path.exists() else None
    config = json.loads(original) if original is not None else {}
    if not isinstance(config, dict) or not isinstance(config.get("hooks", {}), dict):
        raise ValueError(f"훅 설정은 JSON 객체여야 해요. {config_path}")

    hooks = config.setdefault("hooks", {})
    for event, timeout in events.items():
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"{event} 훅 설정은 목록이어야 해요. {config_path}")

        preserved = []
        for group in groups:
            handlers = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(handlers, list) or not all(isinstance(handler, dict) for handler in handlers):
                raise ValueError(f"{event} 훅 그룹 형식이 잘못됐어요. {config_path}")
            remaining = [handler for handler in handlers if not is_previous_deep_review(handler)]
            if len(remaining) == len(handlers):
                preserved.append(group)
            elif remaining:
                preserved.append(dict(group, hooks=remaining))

        preserved.append({"hooks": [{"type": "command", "command": command, "timeout": timeout}]})
        hooks[event] = preserved

    new_config = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if original is not None and new_config != original:
        write_atomic(config_path.with_suffix(".json.bak"), original)
    hook_dir.mkdir(parents=True, exist_ok=True)
    for name, content in scripts.items():
        write_atomic(hook_dir / name, content)
    if new_config != original:
        write_atomic(config_path.resolve(), new_config)

    print(f"훅 설치 완료 · {hook_dir}")
    print(f"설정 파일 · {config_path}")


os.umask(0o077)
try:
    install(Path(sys.argv[1]), Path(sys.argv[2]).absolute(), Path(sys.argv[3]).absolute(),
            Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve())
except (OSError, ValueError) as exc:
    sys.exit(f"오류: 설치를 완료하지 못했어요. {exc}")
PY

echo "Codex에서 /hooks를 열고 새 훅을 확인한 뒤 승인해 주세요."
