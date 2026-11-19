import json
import shutil
import sys
from pathlib import Path


def read_events(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def find_reviewer(review, sessions):
    parent = next(event["thread_id"] for event in read_events(review / "events.jsonl")
                  if event["type"] == "thread.started")
    boundary = (review / "started-at.txt").stat().st_mtime
    matches = []
    for path in sessions.rglob("rollout-*.jsonl"):
        try:
            if path.stat().st_mtime < boundary:
                continue
            with path.open("rb") as stream:
                first = json.loads(stream.readline())
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            # Other sessions may be creating or rotating rollouts during discovery.
            continue
        if not isinstance(first, dict) or first.get("type") != "session_meta":
            continue
        payload = first.get("payload", {})
        if payload.get("source") == {"subagent": "review"} and payload.get("parent_thread_id") == parent:
            matches.append(path)
    if len(matches) != 1:
        raise ValueError(f"Expected one reviewer rollout for {parent}, found {len(matches)}")
    return matches[0]


def save_usage(review, sessions):
    rollout = find_reviewer(review, sessions)
    shutil.copyfile(rollout, review / "reviewer-rollout.jsonl")
    events = list(read_events(rollout))
    context = next(event["payload"] for event in reversed(events) if event["type"] == "turn_context")
    counts = [event["payload"]["info"] for event in events
              if event["type"] == "event_msg" and event["payload"]["type"] == "token_count"
              and event["payload"].get("info")]
    usage = counts[-1]["total_token_usage"]
    max_input = max(count["last_token_usage"]["input_tokens"] for count in counts if count["last_token_usage"])
    estimated_usd = None
    if context["model"] == "gpt-6.2-sol" and max_input <= 272_000 and not usage.get("cache_write_input_tokens"):
        cached = usage["cached_input_tokens"]
        estimated_usd = ((usage["input_tokens"] - cached) * 2 + cached * 0.1 + usage["output_tokens"] * 10) / 1_000_000
    result = {"model": context["model"], "effort": context["effort"], "max_request_input": max_input,
              "usage": usage, "estimated_usd": estimated_usd}
    (review / "usage.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    save_usage(Path(sys.argv[1]), Path(sys.argv[2]))
