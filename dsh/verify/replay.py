"""Production-traffic replay: feed REAL DSH user messages through SCAP's
retrieval / injection / write pipeline offline (no LLM, deterministic).

This is stronger evidence than a synthetic smoke: the inputs are real
production traffic (mixed CJK/Latin, code, URLs, emoji, long texts) and the
memory background is a real project's exported memory (openclaw).

Usage:
  python dsh/verify/replay.py <session.jsonl.zstd> [--max-messages 20]
                                [--memory-md C:/path/.scap/openclaw.md]

Checks per message: tokenization, recall scoring (_format_recall), four-tier
search — none may throw; every 5th message also simulates a write (remember +
export JSON). Prints a summary report; exits non-zero on any failure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time

import zstandard

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from scap.mcp_server import _format_recall  # noqa: E402
from scap.models import Decision  # noqa: E402
from scap.store import MemoryStore  # noqa: E402


def load_user_messages(session_path: str, max_messages: int) -> list[str]:
    """Stream-decode a DSH session log and extract real user messages."""
    msgs: list[str] = []
    dctx = zstandard.ZstdDecompressor()
    with open(session_path, "rb") as f:
        reader = dctx.stream_reader(f)
        buf = b""
        while True:
            chunk = reader.read(1 << 20)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    evt = json.loads(line.decode("utf-8", errors="replace"))
                except Exception:
                    continue
                if evt.get("type") != "user/message":
                    continue
                src = evt.get("data", {}).get("source", {})
                if src.get("kind") != "user":
                    continue
                content = evt.get("data", {}).get("content", [])
                text = " ".join(
                    b.get("text", "") for b in content if b.get("type") == "text"
                ).strip()
                if text:
                    msgs.append(text)
                    if max_messages and len(msgs) >= max_messages:
                        return msgs
    return msgs


def import_memory_md(store: MemoryStore, project: str, md_path: str) -> int:
    """Parse a real .scap/{project}.md export into decisions (best effort)."""
    if not md_path or not os.path.exists(md_path):
        return 0
    count = 0
    title = None
    decision = ""
    rationale = ""
    with open(md_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            m = re.match(r"^### (.+?) \(\d{4}-\d{2}-\d{2}\)$", line)
            if m:
                if title:
                    store.save_decision(Decision(
                        project=project, title=title, decision=decision,
                        rationale=rationale, importance=3,
                    ))
                    count += 1
                title, decision, rationale = m.group(1), "", ""
            elif line.startswith("**Chosen:**"):
                decision = line[len("**Chosen:**"):].strip()
            elif line.startswith("**Why:**"):
                rationale = line[len("**Why:**"):].strip()
    if title:
        store.save_decision(Decision(
            project=project, title=title, decision=decision,
            rationale=rationale, importance=3,
        ))
        count += 1
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", help="path to a DSH session.jsonl.zstd")
    ap.add_argument("--max-messages", type=int, default=20)
    ap.add_argument("--project", default="replay")
    ap.add_argument("--memory-md", default="",
                    help="real .scap/{project}.md to seed the memory background")
    args = ap.parse_args()

    msgs = load_user_messages(args.session, args.max_messages)
    print(f"[replay] loaded {len(msgs)} real user messages from {os.path.basename(args.session)}")

    tmp = tempfile.mkdtemp(prefix="scap-replay-")
    store = MemoryStore(os.path.join(tmp, "replay.db"))
    store.initialize()
    seeded = import_memory_md(store, args.project, args.memory_md)
    print(f"[replay] seeded {seeded} decisions from real project memory")

    failures = 0
    recall_times: list[float] = []
    search_times: list[float] = []
    written = 0
    samples: list[str] = []

    for i, msg in enumerate(msgs, 1):
        tag = f"[{i}/{len(msgs)}] {msg[:60].replace(chr(10), ' ')}"
        try:
            t0 = time.perf_counter()
            terms = _format_recall.__globals__["_task_match_terms"](msg)
            recall = _format_recall(store, args.project, msg)
            recall_times.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            hits = store.search(args.project, msg, limit=5)
            search_times.append(time.perf_counter() - t0)
            if i == 1:
                samples.append(f"terms={terms[:8]} hits={len(hits)}")
                samples.append(recall[:200].replace("\n", " | "))
            # every 5th message simulates a write (decision + export)
            if i % 5 == 0:
                d = Decision(
                    project=args.project, title=f"回放决策 {i}",
                    decision=msg[:60] or "empty", rationale=msg[60:180],
                    importance=3,
                )
                store.save_decision(d)
                out = os.path.join(tmp, f"{args.project}.md")
                store.export_context(args.project, out)
                written += 1
            print(f"  ok  {tag}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  FAIL {tag}\n       {type(e).__name__}: {e}")

    if recall_times:
        avg_r = sum(recall_times) / len(recall_times) * 1000
        avg_s = sum(search_times) / len(search_times) * 1000
        print(f"[replay] avg recall={avg_r:.1f}ms  avg search={avg_s:.1f}ms  "
              f"writes={written}  failures={failures}")
        print(f"[replay] sample terms/hits: {samples[0] if samples else 'n/a'}")
        if len(samples) > 1:
            print(f"[replay] sample recall: {samples[1]}")
    else:
        print("[replay] no user messages found — nothing replayed")
        return 1

    if failures:
        print(f"[replay] RESULT: FAIL ({failures} failures)")
        return 1
    print("[replay] RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
