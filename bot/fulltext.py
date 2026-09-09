"""논문 본문 가져오기.

초록에 "이런 주제들을 다뤘다"만 있고 결론이 없는 논문이 있다. 그런 초록으로 카드뉴스를 만들면
훅에서 질문만 던지고 답을 못 하는 빈 껍데기가 된다(2026-09-09 단백질 보충제 건).

그래서 본문을 찾아본다. 순서:
  1. papers/ 에 사람이 넣어둔 파일 (유료 저널용 수동 경로)
       - papers/<pmid>.pdf 또는 <pmid>.txt   ← 이름을 PMID 로 지으면 바로 찾는다
       - 이름이 달라도 된다. papers/ 안의 PDF·TXT 를 열어 그 안에 이 논문의
         DOI 가 적혀 있으면 그 파일로 인식한다 (출판사가 준 이름 그대로 넣어도 됨).
         출판사 PDF 에는 보통 PMID 가 안 찍혀 있으므로, 내용 대조는 사실상 DOI 로 된다.
         PMID 로 찾고 싶으면 파일 이름을 <pmid>.pdf 로 바꿔 넣으면 된다.
  2. PMC 전문 — PubMed Central 에 공개돼 있으면 자동으로 받는다 (무료, 키 불필요)
둘 다 없으면 초록만으로 가고, 판정기가 결론 없는 논문을 걸러낸다.

단독 실행:
  python bot/fulltext.py 38626029                              # PMID 로
  python bot/fulltext.py 38626029 10.1080/15502783.2024.2341903  # DOI 까지 주면 파일 탐색이 정확해진다
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


# ---------------------------------------------------------------------------
# 사람이 papers/ 에 넣어둔 파일 (PDF / TXT)
# ---------------------------------------------------------------------------

# PDF 본문에서 걷어낼 것들
_JUNK = [
    re.compile(r"\[\s*[\d,\s–-]+\s*\]"),                  # [12,15] 인용 번호
    re.compile(r"Downloaded from .*?(?=\s[A-Z])", re.S),     # 배포 안내 줄
    re.compile(r"https?://\S*creativecommons\S*"),
]


def pdf_text(path: Path, max_pages: int = 60) -> str:
    """PDF 에서 글자를 뽑아 읽을 만하게 다듬는다."""
    try:
        from pypdf import PdfReader
    except ImportError:
        print("  pypdf 가 없어 PDF 를 못 읽습니다 (pip install pypdf)", file=sys.stderr)
        return ""
    try:
        reader = PdfReader(str(path))
        raw = "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages])
    except Exception as e:
        print(f"  PDF 읽기 실패 {path.name}: {e}", file=sys.stderr)
        return ""
    t = re.sub(r"-\s*\n\s*", "", raw)        # 줄 끝에서 잘린 단어 이어붙이기
    t = re.sub(r"\s+", " ", t)
    for pat in _JUNK:
        t = pat.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def read_any(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return pdf_text(path)
    return path.read_text(encoding="utf-8", errors="replace")


def _mentions(text: str, pmid: str, doi: str) -> bool:
    head = text[:6000].lower()               # 앞부분에 서지정보가 있다
    if doi and doi.lower() in head:
        return True
    if pmid and re.search(rf"\b{re.escape(pmid)}\b", head):
        return True
    return False


def manual_file(pmid: str, doi: str = "") -> Path | None:
    """papers/ 에서 이 논문의 본문 파일을 찾는다.

    1) <pmid>.pdf / <pmid>.txt — 이름이 곧 답
    2) 이름이 달라도, 파일 안에 이 논문의 DOI 나 PMID 가 있으면 그 파일
       (출판사가 준 `RSSN_21_2341903.pdf` 같은 이름을 그대로 넣어도 되게)
    """
    if not MANUAL_DIR.exists():
        return None
    for ext in (".txt", ".pdf"):
        p = MANUAL_DIR / f"{pmid}{ext}"
        if pmid and p.exists():
            return p
    if not (pmid or doi):
        return None
    for p in sorted(MANUAL_DIR.iterdir()):
        if p.suffix.lower() not in (".pdf", ".txt") or p.name.startswith("."):
            continue
        try:
            if _mentions(read_any(p), pmid, doi):
                return p
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------

# 번호가 붙은 소제목: "13. Conclusions", "2.Is protein harmful to your kidneys?"
# 이 논문처럼 소제목이 질문인 경우가 많아서, IMRaD 낱말만 찾으면 본문을 통째로 놓친다.
_HEADING = re.compile(
    r"(?:^|(?<=[.?\s]))(\d{1,2}\s*[.)]\s*)"          # 앞의 번호 (필수)
    r"([A-Z][^.?]{3,90}[.?]?)(?=\s)"                   # 대문자로 시작하는 짧은 구절
)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """[(소제목, 본문), ...]. 번호 붙은 소제목을 경계로 자른다."""
    marks = [(m.start(), m.group(2).strip()) for m in _HEADING.finditer(text)]
    # 같은 소제목이 목차에도 나오므로, 뒤쪽(본문 쪽) 것만 남긴다
    seen, keep = {}, []
    for pos, title in marks:
        seen[title] = pos
    for title, pos in seen.items():
        keep.append((pos, title))
    keep.sort()
    if not keep:
        return [("", text)]
    if keep[0][0] > 0:
        keep.insert(0, (0, ""))
    keep.append((len(text), ""))
    return [(keep[i][1], text[keep[i][0]:keep[i + 1][0]].strip())
            for i in range(len(keep) - 1)]


def _pick_sections(text: str, max_chars: int) -> str:
    """긴 본문 줄이기 — 결론을 먼저 확보하고, 나머지는 원문 순서대로 채운다.

    결론만 순위로 뽑고 나머지를 버리면, 이 글이 실제로 다루는 대목(예: 신장 섹션)이
    통째로 빠질 수 있다. 그래서 결론·요약만 우선 담고 그 다음은 앞에서부터 순서대로 담는다.
    """
    if len(text) <= max_chars:
        return text
    secs = _split_sections(text)
    order = ([i for i, (t, _) in enumerate(secs) if _rank(t) >= 3]      # 결론·요약 먼저
             + [i for i, (t, _) in enumerate(secs) if _rank(t) < 3])    # 나머지는 원문 순서
    picked, total, seen = [], 0, set()
    for i in order:
        if i in seen:
            continue
        seen.add(i)
        title, body = secs[i]
        piece = (f"[{title}] " if title else "") + body
        if total + len(piece) > max_chars:
            room = max_chars - total
            if room < 400:
                continue
            cut = piece[:room]
            end = cut.rfind(". ")
            piece = (cut[: end + 1] if end > 300 else cut.rsplit(" ", 1)[0]) + " …"
        picked.append((i, piece))
        total += len(piece)
        if total >= max_chars:
            break
    picked.sort(key=lambda x: x[0])
    return "\n\n".join(p for _, p in picked)


def get(pmid: str, max_chars: int = 16000, doi: str = "") -> tuple[str, str]:
    """(본문 발췌, 출처 설명). 없으면 ("", "")."""
    manual = manual_file(pmid, doi)
    if manual:
        text = read_any(manual)
        if text.strip():
            return _pick_sections(text, max_chars), f"papers/{manual.name} (직접 넣은 본문)"
        print(f"  {manual.name} 에서 글자를 못 뽑았습니다 (스캔본 PDF?)", file=sys.stderr)
    pmcid = pmc_id_for(pmid)
    if not pmcid:
        return "", ""
    try:
        text = fetch_pmc_text(pmcid, max_chars)
    except Exception as e:
        print(f"  PMC 본문 실패: {e}", file=sys.stderr)
        return "", ""
    return (text, f"PMC{pmcid} 전문") if text else ("", "")


def has_fulltext(pmid: str, doi: str = "") -> bool:
    """전문을 볼 수 있는 논문인지 (판정기에 알려주는 용도)."""
    if manual_file(pmid, doi) is not None:
        return True
    return bool(pmid) and pmc_id_for(pmid) is not None


if __name__ == "__main__":
    pmid = sys.argv[1] if len(sys.argv) > 1 else ""
    doi = sys.argv[2] if len(sys.argv) > 2 else ""
    text, src = get(pmid, doi=doi)
    print(f"pmid={pmid}  전문={'있음 — ' + src if text else '없음'}  길이={len(text)}")
    if text:
        print("-" * 70)
        print(text[:1500])
