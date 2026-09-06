"""
논문 후보 찾기 — PubMed E-utilities 로 "일상 제품 × 최근 논문" 을 검색해 가장 좋은 논문 하나를 고른다.

- data/topics.json : 주제 목록 (id, ko, query, hint)
- data/topic_state.json : 주제별 마지막 사용일 (오래 안 쓴 주제부터 돌린다)
- data/used_papers.json : 이미 쓴 논문 (PMID/DOI) — 다시 안 고른다

실행
  python bot/find_papers.py                 # 후보 3개 주제 검색 → 최고 점수 논문 1개를 JSON 으로 출력
  python bot/find_papers.py --topic sunscreen
  python bot/find_papers.py --out data/candidate.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TOPICS = DATA / "topics.json"
TOPIC_STATE = DATA / "topic_state.json"
USED = DATA / "used_papers.json"

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = {"tool": "paper_factcheck_bot", "email": "bot@paper-factcheck.local"}
YEARS_BACK = 3

PUBTYPE_SCORE = {
    "meta-analysis": 4.0,
    "systematic review": 3.5,
    "review": 2.5,
    "randomized controlled trial": 3.0,
    "clinical trial": 2.0,
    "multicenter study": 1.5,
    "observational study": 1.5,
    "comparative study": 1.0,
    "journal article": 0.5,
}
EXCLUDE_PUBTYPES = {"letter", "comment", "editorial", "erratum", "retracted publication",
                    "retraction of publication", "case reports", "news"}


def load_json(p: Path, default):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _get(url: str, params: dict, retries: int = 3):
    for i in range(retries):
        try:
            r = requests.get(url, params={**params, **TOOL}, timeout=40)
            if r.status_code == 429:
                time.sleep(2 + i)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if i == retries - 1:
                raise
            time.sleep(1 + i)
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------

def pubmed_search(query: str, retmax: int = 8) -> list[str]:
    today = date.today()
    mindate = f"{today.year - YEARS_BACK}/01/01"
    term = (f"({query}) AND hasabstract[filter] AND english[lang] "
            f"AND (humans[mh] OR review[pt] OR meta-analysis[pt])")
    r = _get(f"{EUTILS}/esearch.fcgi", {
        "db": "pubmed", "term": term, "retmax": retmax, "retmode": "json",
        "sort": "relevance", "datetype": "pdat", "mindate": mindate,
        "maxdate": f"{today.year}/12/31",
    })
    return r.json().get("esearchresult", {}).get("idlist", [])


def pubmed_fetch(pmids: list[str]) -> list[dict]:
    if not pmids:
        return []
    r = _get(f"{EUTILS}/efetch.fcgi", {
        "db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml",
    })
    root = ET.fromstring(r.content)
    papers = []
    for art in root.findall(".//PubmedArticle"):
        try:
            papers.append(_parse_article(art))
        except Exception as e:  # 파싱 실패한 논문은 건너뜀
            print(f"  파싱 실패: {e}", file=sys.stderr)
    return papers


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _parse_article(art) -> dict:
    med = art.find("MedlineCitation")
    article = med.find("Article")
    pmid = _text(med.find("PMID"))
    title = _text(article.find("ArticleTitle"))
    # 초록 (라벨 붙은 구조화 초록 포함)
    abs_parts = []
    for a in article.findall(".//Abstract/AbstractText"):
        label = a.get("Label")
        t = _text(a)
        abs_parts.append(f"{label}: {t}" if label else t)
    abstract = "\n".join(abs_parts)
    journal = _text(article.find("Journal/Title"))
    iso = _text(article.find("Journal/ISOAbbreviation"))
    year = _text(article.find("Journal/JournalIssue/PubDate/Year")) or \
        _text(article.find("Journal/JournalIssue/PubDate/MedlineDate"))[:4]
    pubtypes = [_text(p).lower() for p in article.findall("PublicationTypeList/PublicationType")]
    mesh = [_text(m.find("DescriptorName")) for m in med.findall("MeshHeadingList/MeshHeading")]
    doi = ""
    for aid in art.findall("PubmedData/ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            doi = _text(aid)
    authors = []
    for au in article.findall("AuthorList/Author")[:3]:
        last = _text(au.find("LastName"))
        init = _text(au.find("Initials"))
        if last:
            authors.append(f"{last} {init}".strip())
    n_auth = len(article.findall("AuthorList/Author"))
    author_str = ", ".join(authors) + (" et al." if n_auth > 3 else "")
    return {
        "pmid": pmid, "doi": doi, "title": title, "abstract": abstract,
        "journal": journal, "journal_abbr": iso, "year": year,
        "pubtypes": pubtypes, "mesh": mesh, "authors": author_str,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


# ---------------------------------------------------------------------------
# 점수
# ---------------------------------------------------------------------------

def score_paper(p: dict, used: set[str]) -> float:
    if p["pmid"] in used or (p["doi"] and p["doi"] in used):
        return -1
    if any(pt in EXCLUDE_PUBTYPES for pt in p["pubtypes"]):
        return -1
    if len(p["abstract"]) < 400:
        return -1
    s = max((PUBTYPE_SCORE.get(pt, 0) for pt in p["pubtypes"]), default=0.5)
    try:
        s += max(0, int(p["year"]) - (date.today().year - YEARS_BACK)) * 0.6
    except ValueError:
        pass
    if "Humans" in p["mesh"]:
        s += 1.0
    if "Animals" in p["mesh"] and "Humans" not in p["mesh"]:
        s -= 1.0
    title_l = p["title"].lower()
    if any(k in title_l for k in ("systematic review", "meta-analysis", "umbrella review")):
        s += 1.0
    if not p["doi"]:
        s -= 0.5
    return s


# ---------------------------------------------------------------------------
# 주제 선택
# ---------------------------------------------------------------------------

def pick_topics(n: int = 3, only: str | None = None) -> list[dict]:
    topics = load_json(TOPICS, [])
    if only:
        return [t for t in topics if t["id"] == only]
    state = load_json(TOPIC_STATE, {})
    def last_used(t):
        return state.get(t["id"], {}).get("last_used", "0000-00-00")
    topics.sort(key=last_used)
    pool = topics[: max(n * 3, 6)]       # 오래 안 쓴 주제 풀에서
    random.shuffle(pool)
    return pool[:n]


def find_best(n_topics: int = 3, only: str | None = None) -> dict | None:
    used = set()
    for u in load_json(USED, []):
        used.add(u.get("pmid", ""))
        if u.get("doi"):
            used.add(u["doi"])

    best = None
    for t in pick_topics(n_topics, only):
        print(f"검색: {t['ko']} — {t['query']}")
        try:
            ids = pubmed_search(t["query"])
            papers = pubmed_fetch(ids)
        except Exception as e:
            print(f"  검색 실패: {e}", file=sys.stderr)
            continue
        for p in papers:
            sc = score_paper(p, used)
            print(f"  [{sc:4.1f}] {p['year']} {p['title'][:80]}")
            if sc > 0 and (best is None or sc > best["score"]):
                best = {"score": sc, "topic": t, "paper": p}
        time.sleep(0.4)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", help="특정 주제 id 만 검색")
    ap.add_argument("--n", type=int, default=3, help="검색할 주제 수")
    ap.add_argument("--out", default=str(DATA / "candidate.json"))
    args = ap.parse_args()

    best = find_best(args.n, args.topic)
    if not best:
        print("적합한 논문을 찾지 못했습니다.")
        return 1
    print(f"\n선택: [{best['topic']['ko']}] {best['paper']['title']}")
    print(f"  {best['paper']['journal']} ({best['paper']['year']}) DOI {best['paper']['doi']}")
    save_json(Path(args.out), best)
    return 0


if __name__ == "__main__":
    sys.exit(main())
