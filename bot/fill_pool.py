"""
대기 중인 초안을 목표 개수까지 채운다.

큐는 날짜가 아니라 '순서'로 관리한다. 매일 아침 가장 오래된 승인된 초안 하나가 나가고,
이 스크립트가 밤에 빠진 만큼 다시 채운다. 그래서 하루를 건너뛰어도 빈 날이 생기지 않는다.

  python bot/fill_pool.py --target 10
  python bot/fill_pool.py --target 10 --max-new 3   # 한 번에 3개까지만
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "bot"
QUEUE = ROOT / "queue"


def pending() -> int:
    QUEUE.mkdir(exist_ok=True)
    return len([p for p in QUEUE.glob("*.json") if not p.name.startswith("_")])


def run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd).returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=10, help="대기 초안을 몇 개까지 유지할지")
    ap.add_argument("--max-new", type=int, default=10, help="한 번 실행에서 새로 만들 최대 개수")
    ap.add_argument("--topic", default="", help="특정 주제 id 로만 (테스트용)")
    args = ap.parse_args()

    have = pending()
    need = min(max(args.target - have, 0), args.max_new)
    print(f"대기 초안 {have}개 / 목표 {args.target}개 → {need}개 만듭니다")
    if need == 0:
        return 0

    made = failed = 0
    for i in range(1, need + 1):
        print(f"\n=== {i}/{need} ===", flush=True)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            cand = tf.name
        find = [sys.executable, str(BOT / "find_papers.py"), "--out", cand]
        if args.topic:
            find += ["--topic", args.topic]
        if run(find) != 0:
            print("  논문 찾기 실패 — 다음으로", file=sys.stderr)
            failed += 1
            continue
        if run([sys.executable, str(BOT / "draft_post.py"), "--candidate", cand]) != 0:
            print("  원고 작성 실패 — 다음으로", file=sys.stderr)
            failed += 1
            continue
        made += 1

    print(f"\n완료: {made}개 생성, {failed}개 실패 (대기 {pending()}개)")
    # 하나도 못 만들었고 시도는 했으면 실패로 본다
    return 1 if (made == 0 and need > 0) else 0


if __name__ == "__main__":
    sys.exit(main())
