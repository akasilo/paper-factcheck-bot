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
  --dm      'DM 답장' 칸을 채운다 — "어디서 사요?" 류 DM 에 붙여 보낼 답장 멘트.
              시트 행의 훅·답·추천 조건·쿠팡 링크로 만들므로, 사람이 링크를 붙여 넣으면
              다음 실행에서 멘트에 그 링크가 들어간다. 바뀐 행만 다시 쓴다.
              (시트에 'DM 답장' 열이 없으면 — Code.gs 를 새 버전으로 배포하기 전 — 조용히 건너뛴다)

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

LINK_COL = "쿠팡 링크"      # 사람이 채우는 칸 — 봇은 절대 쓰지 않는다
DM_COL = "DM 답장"          # 봇이 채우는 칸 — DM 이 오면 사람이 복사해 붙인다

# DM 멘트에 들어가는 고정 문구
BRAND = os.environ.get("DM_BRAND", "").strip() or "paper_factcheck"
LINKS_PAGE_URL = (os.environ.get("LINKS_PAGE_URL", "").strip()
                  or "https://akasilo.github.io/paper-factcheck-bot/")
DM_DISCLOSURE = ("※ 위 쿠팡 링크는 쿠팡 파트너스 활동의 일환으로, 구매 시 일정 수수료를 받습니다. "
                 "구매 가격은 달라지지 않아요.")
DM_MAX = 950                # 인스타 DM 한 통 1000자 제한보다 조금 아래


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


def answer_of(item: dict) -> str:
    """훅의 질문에 대한 답 한 문장. 이 칸을 보면 '답 없는 원고'가 바로 드러난다.

    answer 필드는 2026-09-09 에 생겼다. 그 전에 만든 초안에는 없으므로
    "빈 칸 = 답이 없다" 로 오해하지 않게 그 사실을 적어둔다.
    """
    a = (item.get("answer") or "").strip()
    if a:
        return a
    return "— (답 검사 도입 전 원고. 카드 미리보기로 직접 확인 필요)"


def evidence_of(item: dict) -> str:
    src = str(item.get("source_text", "") or "")
    if src.startswith("PMC"):
        return EVID_PMC
    if "papers/" in src:
        return EVID_MANUAL
    return EVID_ABSTRACT


def link_of_row(row: dict) -> str:
    """시트 행의 '쿠팡 링크' 칸 — http 로 시작하는 진짜 링크만. 'skip' 등은 빈 값."""
    cell = str(row.get(LINK_COL, "") or "").strip()
    return cell if cell.lower().startswith("http") else ""


def dm_ment(row: dict) -> str:
    """'DM 답장' 칸에 넣을 멘트. 시트 행(주제·훅·답·추천 제품 조건·쿠팡 링크)만 보고 만든다.

    사람이 DM 을 받으면 이 칸을 복사해 붙이면 끝나게 — 인사, 그 글의 핵심(훅+답),
    제품을 고를 때 볼 조건, 링크(있으면), 링크 모음 페이지, 파트너스 고지 순서.
    링크가 아직 없으면 '준비 중' 이라고 쓰고 링크 모음 페이지로 안내한다.
    """
    hook = plain(str(row.get("훅", "") or "")).strip()
    answer = plain(str(row.get("답", "") or "")).strip()
    if answer.startswith("—") or not answer:      # 답 검사 도입 전 원고의 자리표시 문구
        answer = ""
    cond = plain(str(row.get("추천 제품 조건", "") or "")).strip()
    link = link_of_row(row)

    skipped = str(row.get(LINK_COL, "") or "").strip().lower() in ("skip", "건너뜀", "패스", "x")
    lines = [f"안녕하세요, {BRAND} 입니다 🙂 관심 가져 주셔서 감사해요!", ""]
    if hook:
        lines.append(f"📌 {hook}")
    if answer:
        lines.append(f"👉 {answer}")
    if hook or answer:
        lines.append("")
    if link:
        if cond:
            lines.append(f"제품 고를 때 볼 조건: {cond}")
        lines.append(f"{'이 조건에 맞는 ' if cond else ''}제품 예시예요 → {link}")
        lines.append("")
        lines.append(f"다른 글의 제품 링크는 프로필 링크(링크 모음)에 최신순으로 정리돼 있어요 → {LINKS_PAGE_URL}")
        lines.append("")
        lines.append(DM_DISCLOSURE)
    elif cond and not skipped:
        lines.append(f"제품 고를 때 볼 조건: {cond}")
        lines.append(f"이 조건에 맞는 제품 링크는 아직 준비 중이에요. 준비되면 프로필 링크(링크 모음)에 올라가요 → {LINKS_PAGE_URL}")
    else:
        lines.append(f"이 글은 따로 추천하는 제품이 없어요. 다른 글의 제품 링크는 프로필 링크(링크 모음)에 최신순으로 정리돼 있어요 → {LINKS_PAGE_URL}")

    text = "\n".join(lines).strip()
    if len(text) > DM_MAX and answer:            # 너무 길면 답 줄부터 뺀다 (훅만으로도 무슨 글인지 안다)
        return dm_ment({**row, "답": ""})
    return text


def row_of(item: dict) -> dict:
    row = _row_of(item)
    row[DM_COL] = dm_ment(row)                   # 새 행은 링크가 없으니 '준비 중' 멘트로 시작
    return row


def _row_of(item: dict) -> dict:
    paper = item.get("paper", {}) or {}
    return {
        "id": item.get("id", ""),
        "상태": STATUS_WAITING,
        "주제": item.get("topic_ko", ""),
        "훅": hook_of(item),
        "답": answer_of(item),
        "추천 제품 조건": item.get("product_hint", ""),
        LINK_COL: "",
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
                  if k not in ("id", "상태", LINK_COL, DM_COL)}   # DM 답장은 --dm 이 링크까지 보고 맞춘다
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
        cell = str(row.get(LINK_COL, "") or "").strip()
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


def do_dm(url: str, token: str, ids: list[str] | None = None) -> int:
    """시트 모든 행의 'DM 답장' 칸을 최신 멘트로 맞춘다 (바뀐 행만 씀, 여러 번 돌려도 안전).

    큐 파일이 아니라 시트 행을 기준으로 만들므로 게시완료·건너뜀 행도 멘트가 있다
    (지난 글에 대한 DM 도 온다). 사람이 '쿠팡 링크' 를 채우면 다음 실행에서 멘트에 링크가 들어간다.
    """
    rows = sheet_get(url, token)
    if not rows:
        print("시트에 행이 없음")
        return 0
    if DM_COL not in rows[0]:
        print(f"시트에 '{DM_COL}' 열이 없어 건너뜀 — _sheet/Code.gs 의 HEADERS 에 '{DM_COL}' 을 넣고 "
              "새 버전으로 배포하면 열이 자동으로 생깁니다.")
        return 0
    want = set(ids or [])
    n = skipped = 0
    for r in rows:
        iid = str(r.get("id", "") or "").strip()
        if not iid or (want and iid not in want):
            continue
        text = dm_ment(r)
        if str(r.get(DM_COL, "") or "").strip() == text:
            skipped += 1
            continue
        sheet_post(url, token, {"action": "update", "id": iid, "fields": {DM_COL: text}})
        print(f"DM 답장 갱신: {iid} ({'링크 있음' if link_of_row(r) else '링크 없음'})")
        n += 1
    print(f"DM 답장: {n}건 갱신 / {skipped}건 그대로")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--push", action="store_true", help="새 초안을 시트에 추가")
    g.add_argument("--pull", action="store_true", help="시트의 링크를 큐에 반영")
    g.add_argument("--status", action="store_true", help="게시/건너뜀 결과를 시트에 반영")
    g.add_argument("--refresh", action="store_true",
                   help="이미 있는 행의 훅·근거·미리보기를 큐 기준으로 다시 씀 (쿠팡 링크는 건드리지 않음)")
    g.add_argument("--dm", action="store_true",
                   help="'DM 답장' 칸을 시트 행(훅·답·조건·쿠팡 링크) 기준으로 채움 (바뀐 행만)")
    ap.add_argument("--id", action="append", default=[],
                   help="--refresh / --dm 대상 id (여러 번 쓸 수 있음). 비우면 전부")
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
    if args.dm:
        return do_dm(url, token, args.id)
    return do_status(url, token)


if __name__ == "__main__":
    sys.exit(main())
