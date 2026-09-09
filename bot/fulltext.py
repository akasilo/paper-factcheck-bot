"""논문 본문 가져오기.

초록에 "이런 주제들을 다뤘다"만 있고 결론이 없는 논문이 있다. 그런 초록으로 카드뉴스를 만들면
훅에서 질문만 던지고 답을 못 하는 빈 껍데기가 된다(2026-09-09 단백질 보충제 건).

그래서 본문을 찾아본다. 순서:
  1. papers/<pmid>.txt  — 사람이 직접 넣어둔 본문 (유료 저널용 수동 경로)
  2. PMC 전문           — PubMed Central 에 공개돼 있으면 자동으로 받는다 (무료, 키 불필요)
둘 다 없으면 초록만으로 가고, 판정기가 결론 없는 논문을 걸러낸다.

단독 실행:
  python bot/fulltext.py 38626029          # 본문 있는지 확인하고 발췌 출력
"""
from __future__ import annotations

import html as _html
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
MANUAL_DIR = ROOT / "papers"          # 사람이 직접 넣는 본문 (.txt)
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TIMEOUT = 30

# 본문에서 우선적으로 담을 섹션 (결론이 들어 있을 만한 곳부터)
_PRIORITY = [
    ("conclusion", 3), ("summary", 3), ("discussion", 2),
    ("result", 2), ("finding", 2), ("introduction", 1),
]


def _get(url: str, params: dict) -> requests.Response:
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT,
                             headers={"User-Agent": "paper-factcheck-bot"})
            if r.status_code == 200:
                return r
        except requests.RequestException:
            pass
        time.sleep(1 + attempt)
    raise RuntimeError(f"요청 실패: {url}")


def pmc_id_for(pmid: str) -> str | None:
    """이 PubMed 논문의 PMC(전문) 아이디. 없으면 None."""
    if not pmid:
        return None
    try:
        j = _get(f"{EUTILS}/elink.fcgi",
                 {"dbfrom": "pubmed", "db": "pmc", "id": pmid, "retmode": "json"}).json()
    except Exception:
        return None
    for ls in j.get("linksets", []):
        for db in ls.get("linksetdbs", []):
            # pubmed_pmc = 그 논문 자체. pubmed_pmc_refs 는 '이 논문을 인용한 글들'이라 쓰면 안 된다.
            if db.get("linkname") == "pubmed_pmc" and db.get("links"):
                return str(db["links"][0])
    return None


def _xml_to_sections(xml: str) -> list[tuple[str, str]]:
    """PMC XML → [(섹션 제목, 본문), ...]. 참고문헌·표·수식은 버린다."""
    body = re.search(r"<body[^>]*>(.*?)</body>", xml, flags=re.S)
    xml = body.group(1) if body else xml
    for tag in ("ref-list", "table-wrap", "fig", "disp-formula", "inline-formula", "xref", "table"):
        xml = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", xml, flags=re.S)
        xml = re.sub(rf"<{tag}[^>]*/>", " ", xml)

    out: list[tuple[str, str]] = []
    for m in re.finditer(r"<sec\b[^>]*>(.*?)</sec>", xml, flags=re.S):
        chunk = m.group(1)
        t = re.search(r"<title[^>]*>(.*?)</title>", chunk, flags=re.S)
        title = _strip(t.group(1)) if t else ""
        text = _strip(re.sub(r"<title[^>]*>.*?</title>", " ", chunk, flags=re.S))
        if text:
            out.append((title, text))
    if not out:
        txt = _strip(xml)
        if txt:
            out.append(("", txt))
    return out


def _strip(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)          # &amp; 뿐 아니라 &#x201C; 같은 숫자 엔티티까지
    return re.sub(r"\s+", " ", s).strip()


def _rank(title: str) -> int:
    low = title.lower()
    for key, w in _PRIORITY:
        if key in low:
            return w
    return 0


def fetch_pmc_text(pmcid: str, max_chars: int = 16000) -> str:
    """PMC 전문에서 결론이 있을 만한 섹션부터 골라 max_chars 까지 담는다."""
    xml = _get(f"{EUTILS}/efetch.fcgi", {"db": "pmc", "id": pmcid, "rettype": "xml"}).text
    secs = _xml_to_sections(xml)
    if not secs:
        return ""
    # 우선순위 높은 섹션부터, 같은 순위면 원문 순서대로
    order = sorted(range(len(secs)), key=lambda i: (-_rank(secs[i][0]), i))
    parts, total = [], 0
    for i in order:
        title, text = secs[i]
        head = f"[{title}] " if title else ""
        piece = head + text
        if total + len(piece) > max_chars:
            room = max_chars - total
            if room < 240:          # 남은 자리가 너무 작으면 조각을 남기지 말고 건너뛴다
                continue
            cut = piece[:room]
            end = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("? "))
            piece = (cut[: end + 1] if end > 200 else cut.rsplit(" ", 1)[0]) + " …"
        if not piece:
            continue
        parts.append((i, piece))
        total += len(piece)
        if total >= max_chars:
            break
    parts.sort(key=lambda x: x[0])          # 읽기 좋게 원문 순서로 되돌린다
    return "\n\n".join(p for _, p in parts)


def get(pmid: str, max_chars: int = 16000) -> tuple[str, str]:
    """(본문 발췌, 출처 설명). 없으면 ("", "")."""
    manual = MANUAL_DIR / f"{pmid}.txt"
    if pmid and manual.exists():
        return manual.read_text(encoding="utf-8")[:max_chars], f"papers/{pmid}.txt (직접 넣은 본문)"
    pmcid = pmc_id_for(pmid)
    if not pmcid:
        return "", ""
    try:
        text = fetch_pmc_text(pmcid, max_chars)
    except Exception as e:
        print(f"  PMC 본문 실패: {e}", file=sys.stderr)
        return "", ""
    return (text, f"PMC{pmcid} 전문") if text else ("", "")


def has_fulltext(pmid: str) -> bool:
    """전문을 볼 수 있는 논문인지 (판정기에 알려주는 용도)."""
    return bool(pmid) and ((MANUAL_DIR / f"{pmid}.txt").exists() or pmc_id_for(pmid) is not None)


if __name__ == "__main__":
    pmid = sys.argv[1] if len(sys.argv) > 1 else ""
    text, src = get(pmid)
    print(f"pmid={pmid}  전문={'있음 — ' + src if text else '없음'}  길이={len(text)}")
    if text:
        print("-" * 70)
        print(text[:1500])
