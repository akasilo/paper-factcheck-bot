"""게시 문지기 — 표준 라이브러리만 쓴다 (워크플로에서 pip install 전에 부르므로).

GitHub 예약 실행은 언제 뜰지 모르고 상당수는 아예 안 뜬다. 그래서 하루 종일 30분마다
여러 번 깨우되, 실제 게시는 이 문지기가 **슬롯당 한 번**으로 묶는다.

슬롯은 "이름=시각" 목록이다 (기본: 아침 07:00 / 점심 12:00 / 퇴근길 18:00).
깨어난 시각을 보고 *지금이 어느 슬롯인지* 스스로 정한다.

    06:50 에 깨어남 → 아직 첫 슬롯 전         → 안 올림
    07:27 에 깨어남 → morning 슬롯, 아직 안 함 → 올림
    09:30 에 깨어남 → morning 슬롯, 이미 함    → 안 올림
    12:40 에 깨어남 → lunch 슬롯, 아직 안 함   → 올림

시각으로 슬롯을 정하므로, 아침 예약이 크게 밀려 점심때 떠도 점심 몫으로 알아서 쓰인다.

워크플로에서:
    python bot/gate.py --slots "morning=07:00,lunch=12:00,evening=18:00" >> "$GITHUB_OUTPUT"
    # go=yes|no / slot=<이름> / reason=<사유> 를 출력
publish.py 도 같은 함수를 써서 판단이 두 군데로 갈라지지 않게 한다.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTED_DIR = ROOT / "posted"
KST = timezone(timedelta(hours=9))

DEFAULT_SLOTS = "morning=07:00,lunch=12:00,evening=18:00"
# 슬롯이 없던 시절의 기록(posted/2026-09-12.json)은 첫 슬롯을 쓴 것으로 본다
LEGACY_SLOT = "morning"


def today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def parse_slots(spec: str) -> list[tuple[str, tuple[int, int]]]:
    """"morning=07:00,lunch=12:00" → [("morning",(7,0)), ("lunch",(12,0))] (시각순)."""
    out: list[tuple[str, tuple[int, int]]] = []
    for part in (spec or DEFAULT_SLOTS).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            name, hhmm = part.split("=", 1)
            hh, mm = (int(x) for x in hhmm.split(":", 1))
        except Exception:
            continue                      # 형식이 이상한 건 조용히 건너뛴다
        out.append((name.strip(), (hh, mm)))
    out.sort(key=lambda x: x[1])
    return out


def current_slot(spec: str = "", now: datetime | None = None) -> str:
    """지금이 어느 슬롯인지. 첫 슬롯 시각 전이면 빈 문자열."""
    n = now or datetime.now(KST)
    cur = ""
    for name, t in parse_slots(spec):
        if (n.hour, n.minute) >= t:
            cur = name
    return cur


def posted_slots(date: str, posted_dir: Path | None = None) -> dict[str, str]:
    """오늘 이미 채운 슬롯 → 기록 파일 이름."""
    d = posted_dir or POSTED_DIR
    done: dict[str, str] = {}
    if not d.exists():
        return done
    for pth in sorted(d.glob(f"{date}*.json")):
        if not (pth.stem == date or pth.stem.startswith(f"{date}-")):
            continue
        slot = LEGACY_SLOT
        try:
            slot = (json.loads(pth.read_text(encoding="utf-8")).get("slot") or LEGACY_SLOT)
        except Exception:
            pass
        done.setdefault(str(slot), pth.name)
    return done


def should_post(spec: str = "", date: str | None = None,
                now: datetime | None = None) -> tuple[bool, str, str]:
    """(올릴까, 슬롯이름, 사유)."""
    d = date or today_kst()
    slot = current_slot(spec, now)
    if not slot:
        first = parse_slots(spec)[0] if parse_slots(spec) else ("", (0, 0))
        return False, "", f"아직 첫 슬롯({first[0]} {first[1][0]:02d}:{first[1][1]:02d}) 전"
    done = posted_slots(d)
    if slot in done:
        return False, slot, f"{d} {slot} 몫은 이미 올림 — {done[slot]}"
    return True, slot, f"{slot} 몫 올릴 차례"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", default=DEFAULT_SLOTS,
                    help='슬롯 정의 "이름=HH:MM,이름=HH:MM" (KST)')
    ap.add_argument("--date", default="", help="기준 날짜 (기본: 오늘 KST)")
    args = ap.parse_args()
    go, slot, why = should_post(args.slots, args.date or None)
    print(f"go={'yes' if go else 'no'}")
    print(f"slot={slot}")
    print(f"reason={why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
