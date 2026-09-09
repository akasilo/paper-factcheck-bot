"""하루 한 번 문지기 — 표준 라이브러리만 쓴다 (워크플로에서 pip install 전에 부르므로).

GitHub 의 예약 실행은 언제 뜰지 모른다. 07:37 로 걸어도 실제로는 09:30 에 뜬 날이 있었다.
그래서 아침 시간대에 30분마다 여러 번 깨우되, 실제 게시는 이 문지기가 하루 한 번으로 묶는다.

    - 목표 시각(--after, KST) 전이면  → 아직 아님
    - 오늘 이미 올린 기록이 있으면    → 이미 함

워크플로에서:
    python bot/gate.py --after 08:00 >> "$GITHUB_OUTPUT"     # go=yes / go=no 를 출력
publish.py 도 같은 함수를 써서 판단이 두 군데로 갈라지지 않게 한다.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTED_DIR = ROOT / "posted"
KST = timezone(timedelta(hours=9))


def today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def posted_today(date: str, posted_dir: Path | None = None) -> Path | None:
    """오늘 올린 기록 파일이 있으면 그 경로. 같은 날 두 번째는 `<날짜>-2.json` 로 저장된다."""
    d = posted_dir or POSTED_DIR
    if not d.exists():
        return None
    for pth in sorted(d.glob(f"{date}*.json")):
        if pth.stem == date or pth.stem.startswith(f"{date}-"):
            return pth
    return None


def before_target(after: str, now: datetime | None = None) -> bool:
    """`after`(KST, HH:MM) 보다 이른 시각이면 True. 형식이 이상하면 문지기를 통과시킨다."""
    if not after:
        return False
    try:
        hh, mm = (int(x) for x in after.split(":", 1))
    except Exception:
        return False
    n = now or datetime.now(KST)
    return (n.hour, n.minute) < (hh, mm)


def should_post(after: str = "", date: str | None = None) -> tuple[bool, str]:
    d = date or today_kst()
    if before_target(after):
        return False, f"아직 {after} (KST) 전"
    done = posted_today(d)
    if done:
        return False, f"오늘({d})은 이미 올림 — {done.name}"
    return True, "올릴 차례"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--after", default="", help="이 시각(KST, HH:MM) 이후에만 통과")
    ap.add_argument("--date", default="", help="기준 날짜 (기본: 오늘 KST)")
    args = ap.parse_args()
    go, why = should_post(args.after, args.date or None)
    print(f"go={'yes' if go else 'no'}")
    print(f"reason={why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
