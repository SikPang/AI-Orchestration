import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from review_gate import git, snapshot


SOURCE = Path(__file__).resolve().parent

FAKE_CODEX = """\
import json
import os
import sys
from pathlib import Path

if "--help" in sys.argv:
    sys.exit(0)

assert "-s" in sys.argv and "read-only" in sys.argv
assert "--disable" in sys.argv and "hooks" in sys.argv
assert "-m" not in sys.argv
assert "Original task" in sys.stdin.read()
root = Path.cwd()
mode = (root / "result.txt").read_text()
priorities = {"first": [1, 2], "second": [0, 3], "third": [2, 3],
              "persistent": [1], "bad priority": [4]}[mode.split("-")[0]]
path = Path(os.environ["CODEX_HOME"]) / "sessions" / f"rollout-{mode}.jsonl"
path.parent.mkdir(exist_ok=True)
events = [
    {"type": "session_meta", "payload": {"source": {"subagent": "review"}, "parent_thread_id": mode}},
    {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": json.dumps({
        "overall_correctness": "patch is correct" if mode == "third" else "patch is incorrect",
        "findings": [
            {"title": f"[P{priority}] Finding {priority}", "body": f"Body {priority}",
             "priority": priority, "code_location": {
                 "absolute_file_path": str(root / "result.txt"), "line_range": {"start": 1, "end": 1}}}
            for priority in priorities
        ]})}},
]
path.write_text("".join(json.dumps(event) + "\\n" for event in events))
print(json.dumps({"type": "thread.started", "thread_id": mode}))
print(json.dumps({"type": "turn.completed"}))
"""


class InstalledHooksTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="deep-review-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.home = root / "Codex's home"
        self.home.mkdir()
        self.config = self.home / "hooks.json"
        self.repository = root / "Repository with spaces"
        self.repository.mkdir()
        git(self.repository, "init", "--quiet", "--template=")
        self.result = self.repository / "result.txt"
        self.result.write_text("before")
        codex = root / "codex"
        codex.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) +
                         " -B -c " + shlex.quote(FAKE_CODEX) + ' "$@"\n')
        codex.chmod(0o700)
        self.env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"], CODEX_HOME=str(self.home))
        self.env.pop("CODEX_DEEP_REVIEW_MODEL", None)
        self.event = dict(session_id="session", turn_id="turn", cwd=str(self.repository), permission_mode="default")

    def install(self, *, succeeds=True):
        done = subprocess.run([SOURCE / "install.sh"], env=self.env, text=True, capture_output=True)
        self.assertEqual(done.returncode == 0, succeeds, done.stderr)
        if succeeds:
            self.hooks = json.loads(self.config.read_text())["hooks"]

    def hook(self, kind, **extra):
        handler = self.hooks[kind][-1]["hooks"][0]
        done = subprocess.run(shlex.split(handler["command"]), input=json.dumps(
            dict(self.event, hook_event_name=kind, **extra)), capture_output=True, text=True, check=True,
            env={k: v for k, v in self.env.items() if k != "CODEX_HOME"})
        return json.loads(done.stdout or "{}")

    def test_p0_p1_continue_and_p2_p3_report(self):
        self.install()
        self.assertEqual(self.hook("UserPromptSubmit", prompt="[심층] Check this task"), {})
        sessions = self.home / "sessions"
        sessions.mkdir()
        (sessions / "rollout-other.jsonl").touch()
        for phase, urgent, report in (("first", 1, 2), ("second", 0, 3)):
            self.result.write_text(phase)
            answer = self.hook("Stop", stop_hook_active=phase != "first")
            self.assertEqual(answer["decision"], "block")
            self.assertIn(f"Finding {urgent}", answer["reason"])
            self.assertNotIn(f"Finding {report}", answer["reason"])
            self.assertIn(f"Finding {report}", answer["systemMessage"])
        self.result.write_text("third")
        answer = self.hook("Stop", stop_hook_active=True)
        self.assertNotIn("decision", answer)
        self.assertIn("Finding 2", answer["systemMessage"])
        self.assertIn("Finding 3", answer["systemMessage"])
        self.assertEqual(self.hook("Stop", stop_hook_active=True), {})

    def test_no_progress_and_interrupt(self):
        self.install()
        self.hook("UserPromptSubmit", prompt="[심층] Check")
        self.result.write_text("persistent")
        self.assertEqual(self.hook("Stop")["decision"], "block")
        self.assertIn("수정 후 변경분이 같아", self.hook("Stop")["systemMessage"])

        self.event["turn_id"] = "different-turn"
        self.hook("UserPromptSubmit", prompt="[심층] Check")
        self.result.write_text("first")
        self.assertIn("중단", self.hook("Interrupt")["systemMessage"])
        self.assertEqual(self.hook("Stop"), {})

    def test_repair_limit_reports_remaining_urgent_finding(self):
        self.install()
        self.hook("UserPromptSubmit", prompt="[심층] Check")
        for round_number in range(4):
            self.result.write_text(f"persistent-{round_number}")
            answer = self.hook("Stop")
            if round_number < 3:
                self.assertEqual(answer["decision"], "block")
        self.assertNotIn("decision", answer)
        self.assertIn("Finding 1", answer["systemMessage"])
        self.assertIn("자동 수정 한도", answer["systemMessage"])

    def test_bad_priority_reports_review_failure(self):
        self.install()
        self.hook("UserPromptSubmit", prompt="[심층] Check")
        self.result.write_text("bad priority")
        answer = self.hook("Stop")
        self.assertIn("자동 검수를 완료하지 못했어요", answer["systemMessage"])

    def test_install_preserves_other_hooks_and_rejects_invalid_config_before_copying(self):
        unrelated = {"type": "command", "command": "echo /old/hooks/deep-review/deep_review_hook.py"}
        previous = {"type": "command", "command": "/old/python3 -B /old/hooks/deep-review/deep_review_hook.py /old/codex"}
        original = json.dumps({"description": "preserve", "hooks": {"Stop": [
            {"matcher": "keep", "hooks": [unrelated, previous]}, {"hooks": []}]}})
        self.config.write_text(original)
        self.install()
        self.assertEqual(self.hooks["Stop"][:-1], [{"matcher": "keep", "hooks": [unrelated]}, {"hooks": []}])
        config = self.config.read_bytes()
        self.install()
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual(self.config.with_suffix(".json.bak").read_text(), original)

        script = self.home / "hooks" / "deep-review" / "review_gate.py"
        script.write_text("installed before invalid config")
        self.config.write_text('{"hooks": {"Stop": {}}}')
        self.install(succeeds=False)
        self.assertEqual(script.read_text(), "installed before invalid config")

    def test_snapshot_keeps_staged_ignored_file_in_an_unborn_repository(self):
        git(self.repository, "add", "--", "result.txt")
        (self.repository / ".gitignore").write_text("result.txt\n")
        self.result.write_text("after")
        index = self.repository / ".git" / "index"
        original_index = index.read_bytes()
        tree = snapshot(self.repository)
        self.assertEqual(git(self.repository, "show", f"{tree}:result.txt"), "after")
        self.assertEqual(index.read_bytes(), original_index)


if __name__ == "__main__":
    unittest.main()
