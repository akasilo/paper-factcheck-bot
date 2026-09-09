"""
구글시트 ↔ 큐 동기화.

시트는 Apps Script 웹 앱(_sheet/Code.gs)으로 열려 있고, 환경변수 두 개로 접근한다.
  SHEET_URL   = 웹 앱 URL
  SHEET_TOKEN = Code.gs 의 TOKEN 과 같은 문자열

하는 일
  --push    큐에 있는데 시트에 아직 없는 초안을 시트에 추가한다 (봇 → 시트)
  --pull    시트의 '쿠팡 링크' 칸을 큐에 반영한다 (시트 → 봇)
              링크가 있으면      → approved: true + link 저장
              'skip' 이라고 쓰면 → skipped/ 로 옮김
  --status  게시/건너뜀 결과를 시트의 '상태' 칸에 반영한다
  --refresh 이미 있는 행의 훅·근거·미리보기를 큐 기준으로 다시 쓴다 ('쿠팡 링크' 는 안 건드림)

SHEET_URL 이 없으면 아무것도 하지 않고 정상 종료한다(= 시트를 안 쓰는 설정).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
QUEUE_DIR = ROOT / "queue"
POSTED_DIR = ROOT / "posted"
SKIPPED_DIR = ROOT / "skipped"
KST = timezone(timedelta(hours=9))

STATUS_WAITING = "대기"
STATUS_POSTED = "게시완료"
STATUS_SKIPPED = "건너뜀"


# ---------------------------------------------------------------------------
# 시트 호출
# ---------------------------------------------------------------------------

def cfg() -> tuple[str, str] | None:
    url = os.environ.get("SHEET_URL", "").strip()
    token = os.environ.get("SHEET_TOKEN", "").strip()
    if not url:
        return None
    if not token:
        raise SystemExit("SHEET_URL 은 있는데 SHEET_TOKEN 이 비어 있습니다.")
    return url, token


def sheet_get(url: str, token: str) -> list[dict]:
    r = requests.get(url, params={"token": token}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise SystemExit(f"시트 오류: {data['error']} (SHEET_TOKEN 이 Code.gs 의 TOKEN 과 같은지 확인)")
    return data.get("rows", [])


def sheet_post(url: str, token: str, body: dict) -> dict:
    # Apps Script 는 302 로 googleusercontent 로 넘기므로 리다이렉트를 따라가야 한다
    r = requests.post(url, data=json.dumps({**body, "token": token}),
                      headers={"Content-Type": "application/json"},
                      timeout=60, allow_redirects=True)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise SystemExit(f"시트 오류: {data['error']}")
    return data


# ---------------------------------------------------------------------------
# 큐 읽기
# ---------------------------------------------------------------------------

def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def save(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def queue_items() -> list[tuple[Path, dict]]:
    out = []
    for p in sorted(QUEUE_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            out.append((p, load(p)))
        except Exception as e:
            print(f"건너뜀 {p.name}: {e}", file=sys.stderr)
    return out


def plain(s: str) -> str:
    """카드 마크업을 사람이 읽는 글로."""
    return (s or "").replace("[[", "").replace("]]", "") \
                    .replace("{{", "").replace("}}", "").replace("\n", " ")


def hook_of(item: dict) -> str:
    for c in item.get("cards", []):
        if c.get("type") == "hook":
            return plain(c.get("text", ""))
    return plain(item.get("threads_text", ""))[:120]


def title_cell(item: dict) -> str:
    """논문 제목만. 링크는 따로 '링크' 열에 둔다.

    (HYPERLINK 수식은 시트 지역 설정에 따라 인자 구분자가 , 인지 ; 인지 달라져 깨질 수 있다.
     그냥 URL 을 칸에 넣으면 구글시트가 알아서 누를 수 있게 만들어 준다.)
    """
    return ((item.get("paper") or {}).get("title") or "").strip()


def journal_cell(item: dict) -> str:
    p = item.get("paper", {}) or {}
    j, y = (p.get("journal") or "").strip(), str(p.get("year") or "").strip()
    return f"{j} ({y})" if j and y else (j or y)


def preview_of(item: dict) -> str:
    imgs = item.get("images") or []
    if not imgs:
        return ""
    base = os.environ.get("IMAGE_BASE_URL", "").rstrip("/")
    return f"{base}/{imgs[0].lstrip('/')}" if base else imgs[0]


# '근거' 칸에 적을 말 — 사람이 보고 "본문을 넣어줄까?" 를 판단하는 칸
EVID_PMC = "PMC 전문"
EVID_MANUAL = "직접 넣음"
EVID_ABSTRACT = "초록만"


def evidence_of(item: dict) -> str:
    src = str(item.get("source_text", "") or "")
    if src.startswith("PMC"):
        return EVID_PMC
    if "papers/" in src:
        return EVID_MANUAL
    return EVID_ABSTRACT


def row_of(item: dict) -> dict:
    paper = item.get("paper", {}) or {}
    return {
        "id": item.get("id", ""),
        "상태": STATUS_WAITING,
        "주제": item.get("topic_ko", ""),
        "훅": hook_of(item),
        "추천 제품 조건": item.get("product_hint", ""),
        "쿠팡 링크": "",
        "논문 제목": title_cell(item),
        "저널·연도": journal_cell(item),
        "PMID": paper.get("pmid", "") or "",
        "DOI": paper.get("doi", "") or "",
        "링크": paper.get("url", "") or "",
        "근거": evidence_of(item),
        "카드 미리보기": preview_of(item),
        "만든날짜": (item.get("drafted_at") or "")[:10],
    }


# ---------------------------------------------------------------------------
# 동작
# ---------------------------------------------------------------------------

def do_push(url: str, token: str) -> int:
    have = {str(r.get("id", "")) for r in sheet_get(url, token)}
    rows = [row_of(it) for _, it in queue_items()
            if it.get("id") and str(it["id"]) not in have]
    if not rows:
        print("시트에 추가할 새 초안 없음")
        return 0
    res = sheet_post(url, token, {"action": "add", "rows": rows})
    print(f"시트에 {res.get('added', 0)}건 추가: {', '.join(r['id'] for r in rows)}")
    return 0


def do_refresh(url: str, token: str, ids: list[str] | None = None) -> int:
    """이미 시트에 있는 행의 내용을 큐 기준으로 다시 써 넣는다 ('쿠팡 링크' 는 건드리지 않음).

    본문을 받아 원고를 다시 쓴 뒤, 시트의 훅·근거·미리보기를 맞추는 데 쓴다.
    """
    have = {str(r.get("id", "")): r for r in sheet_get(url, token)}
    want = set(ids or [])
    n = 0
    for _, item in queue_items():
        iid = str(item.get("id", ""))
        if not iid or iid not in have or (want and iid not in want):
            continue
        row = row_of(item)
        fields = {k: v for k, v in row.items()
                  if k not in ("id", "상태", "쿠팡 링크")}
        cur = have[iid]
        if all(str(cur.get(k, "")) == str(v) for k, v in fields.items()):
            continue                      # 바뀐 게 없으면 건드리지 않는다
        sheet_post(url, token, {"action": "update", "id": iid, "fields": fields})
        print(f"시트 갱신: {iid} (근거={fields.get('근거')})")
        n += 1
    if not n:
        print("시트에서 고칠 행 없음")
    return 0


def do_pull(url: str, token: str) -> int:
    by_id = {str(r.get("id", "")): r for r in sheet_get(url, token)}
    approved = skipped = 0
    for p, item in queue_items():
        row = by_id.get(str(item.get("id", "")))
        if not row:
            continue
        cell = str(row.get("쿠팡 링크", "") or "").strip()
        if not cell:
            continue
        if cell.lower() in ("skip", "건너뜀", "패스", "x"):
            SKIPPED_DIR.mkdir(exist_ok=True)
            item["skipped"] = True
            item["approved"] = False
            save(SKIPPED_DIR / p.name, item)
            p.unlink()
            print(f"건너뜀: {item['id']} ({item.get('topic_ko', '')})")
            skipped += 1
            continue
        if not cell.lower().startswith("http"):
            print(f"링크 형식이 아님, 무시: {item['id']} → {cell[:40]}", file=sys.stderr)
            continue
        if item.get("approved") is True and item.get("link") == cell:
            continue
        item["approved"] = True
        item["link"] = cell
        item["approved_at"] = datetime.now(KST).isoformat()
        save(p, item)
        print(f"승인: {item['id']} ({item.get('topic_ko', '')}) → {cell[:50]}")
        approved += 1
    print(f"승인 {approved}건 / 건너뜀 {skipped}건")
    return 0


def do_status(url: str, token: str) -> int:
    """posted/, skipped/ 를 훑어 시트의 '상태' 칸을 맞춘다 (여러 번 돌려도 안전)."""
    rows = {str(r.get("id", "")): r for r in sheet_get(url, token)}
    want: dict[str, str] = {}
    for p in sorted(POSTED_DIR.glob("*.json")):
        try:
            q = load(p).get("queue", {})
        except Exception:
            continue
        if q.get("id"):
            want[str(q["id"])] = STATUS_POSTED
    for p in sorted(SKIPPED_DIR.glob("*.json")):
        try:
            q = load(p)
        except Exception:
            continue
        if q.get("id"):
            want.setdefault(str(q["id"]), STATUS_SKIPPED)

    n = 0
    for sid, status in want.items():
        row = rows.get(sid)
        if not row or str(row.get("상태", "")).strip() == status:
            continue
        sheet_post(url, token, {"action": "status", "id": sid, "status": status})
        print(f"상태 갱신: {sid} → {status}")
        n += 1
    if not n:
        print("상태 변경 없음")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--push", action="store_true", help="새 초안을 시트에 추가")
    g.add_argument("--pull", action="store_true", help="시트의 링크를 큐에 반영")
    g.add_argument("--status", action="store_true", help="게시/건너뜀 결과를 시트에 반영")
    g.add_argument("--refresh", action="store_true",
                   help="이미 있는 행의 훅·근거·미리보기를 큐 기준으로 다시 씀 (쿠팡 링크는 건드리지 않음)")
    ap.add_argument("--id", action="append", default=[],
                   help="--refresh 대상 id (여러 번 쓸 수 있음). 비우면 전부")
    args = ap.parse_args()

    conf = cfg()
    if not conf:
        print("SHEET_URL 이 없어 시트 동기화를 건너뜁니다.")
        return 0
    url, token = conf

    if args.push:
        return do_push(url, token)
    if args.pull:
        return do_pull(url, token)
    if args.refresh:
        return do_refresh(url, token, args.id)
    return do_status(url, token)


if __name__ == "__main__":
    sys.exit(main())
