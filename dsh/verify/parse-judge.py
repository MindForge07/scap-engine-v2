"""Parse analogy experiment judge outputs into group means.

Reads $out/judge-map.txt (Dn=id mapping) and $out/judge-t*-b*.txt (judge
tables) and prints per-group quality/depth means plus E1/E2/E3 transfer
counts. Usage:
  python dsh/verify/parse-judge.py <out-dir>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

FLAG = r"(?:\*\*)?((?:yes|no))(?:\*\*)?"
TABLE_LINE = re.compile(
    rf"^\|\s*D(\d+)(?:\s*\([^)]*\))?\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*{FLAG}\s*\|\s*{FLAG}\s*\|\s*{FLAG}\s*\|",
    re.IGNORECASE,
)


def parse_map(out: Path) -> dict[str, str]:
    """batch-file-stem -> {dn: run id}, from judge-map.txt lines."""
    batches: dict[str, dict[str, str]] = {}
    f = out / "judge-map.txt"
    if not f.exists():
        return batches
    for line in f.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^task(\d+) batch(\d+): (.+)$", line)
        if not m:
            continue
        stem = f"judge-t{m.group(1)}-b{m.group(2)}"
        batches[stem] = {}
        for pair in re.findall(r"D\d+=\S+", m.group(3)):
            dn, _, rid = pair.partition("=")
            batches[stem][dn] = rid
    return batches


def parse_judge_files(out: Path) -> dict[str, dict[str, tuple[int, int, str, str, str]]]:
    """batch-file-stem -> {dn: (quality, depth, e1, e2, e3)} from judge tables."""
    batches: dict[str, dict[str, tuple[int, int, str, str, str]]] = {}
    for f in sorted(out.glob("judge-t*-b*.txt")):
        batches[f.stem] = {}
        for line in f.read_text(encoding="utf-8").splitlines():
            m = TABLE_LINE.match(line.strip())
            if m:
                num, q, d, e1, e2, e3 = m.groups()
                batches[f.stem][f"D{num}"] = (int(q), int(d), e1.lower(), e2.lower(), e3.lower())
    return batches


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    out = Path(sys.argv[1])
    maps = parse_map(out)
    scores = parse_judge_files(out)
    print(f"judge files: {len(list(out.glob('judge-t*-b*.txt')))}  "
          f"mapped batches: {len(maps)}  scored batches: {len(scores)}")

    groups = {"A": [], "B": [], "C": []}
    e_counts = {"A": {"E1": 0, "E2": 0, "E3": 0}, "B": {"E1": 0, "E2": 0, "E3": 0},
                "C": {"E1": 0, "E2": 0, "E3": 0}}
    matched = 0
    for stem, dn_map in maps.items():
        batch_scores = scores.get(stem, {})
        for dn, rid in dn_map.items():
            if dn not in batch_scores:
                continue
            g = rid.split("-")[1][0]  # t1-A1 -> A
            if g not in groups:
                continue
            q, d, e1, e2, e3 = batch_scores[dn]
            groups[g].append((q, d))
            for tag, flag in (("E1", e1), ("E2", e2), ("E3", e3)):
                if flag == "yes":
                    e_counts[g][tag] += 1
            matched += 1

    print(f"matched designs: {matched}\n")
    print("=== group means ===")
    for g in ("A", "B", "C"):
        vals = groups[g]
        if not vals:
            print(f"  {g}: no scores")
            continue
        n = len(vals)
        q = sum(v[0] for v in vals) / n
        d = sum(v[1] for v in vals) / n
        print(f"  {g}: n={n} quality={q:.2f} depth={d:.2f} "
              f"E1={e_counts[g]['E1']}/{n} E2={e_counts[g]['E2']}/{n} E3={e_counts[g]['E3']}/{n}")

    a_q = sum(v[0] for v in groups["A"]) / len(groups["A"]) if groups["A"] else 0
    a_d = sum(v[1] for v in groups["A"]) / len(groups["A"]) if groups["A"] else 0
    b_q = sum(v[0] for v in groups["B"]) / len(groups["B"]) if groups["B"] else 0
    b_d = sum(v[1] for v in groups["B"]) / len(groups["B"]) if groups["B"] else 0
    c_q = sum(v[0] for v in groups["C"]) / len(groups["C"]) if groups["C"] else 0
    c_d = sum(v[1] for v in groups["C"]) / len(groups["C"]) if groups["C"] else 0
    print("\n=== verdict ===")
    print(f"  depth: B {b_d:.2f} vs A {a_d:.2f} ({'+' if b_d >= a_d else ''}{b_d - a_d:+.2f}) | "
          f"C {c_d:.2f} vs A ({'+' if c_d >= a_d else ''}{c_d - a_d:+.2f})")
    print(f"  quality: B {b_q:.2f} vs A {a_q:.2f} | C {c_q:.2f} vs A {a_q:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
