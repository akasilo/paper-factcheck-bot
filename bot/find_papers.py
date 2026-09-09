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

def matches_topic(p: dict, topic: dict) -> bool:
    """주제의 핵심어(must)가 제목·초록에 하나라도 있어야 한다 — 엉뚱한 논문 방지."""
    must = [m.lower() for m in topic.get("must", []) if m]
    if not must:
        return True
    title = p["title"].lower()
    if any(m in title for m in must):
        return True
    abstract = p["abstract"].lower()
    return sum(abstract.count(m) for m in must) >= 2   # 초록에 두 번 이상은 나와야 '그 제품' 논문으로 본다


def score_paper(p: dict, used: set[str], topic: dict | None = None) -> float:
    if p["pmid"] in used or (p["doi"] and p["doi"] in used):
        return -1
    if topic is not None and not matches_topic(p, topic):
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
    if topic is not None:
        must = [m.lower() for m in topic.get("must", []) if m]
        if any(m in p["title"].lower() for m in must):
            s += 1.5   # 제목에 핵심어가 있으면 주제 적합도가 높다
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


JUDGE_SYSTEM = """당신은 인스타그램 계정 @paper_factcheck 의 편집장입니다.
이 계정은 "일상 속 제품(선크림, 프라이팬, 텀블러, 영양제 …)이 의외로 안 좋다/의외로 좋다"를 최신 논문으로 팩트체크합니다.
아래 후보 논문들 중에서 오늘 게시물로 만들 논문 하나를 고르세요.

판단 기준 (중요한 순서)
1. 제품 직접성: 논문이 그 제품(또는 그 제품의 핵심 성분·사용 상황)을 직접 다루는가. 환경·하수·작물 같은 간접 경로만 다루면 탈락.
2. **결론 유무 (가장 자주 놓치는 항목)**: 초록에 결과의 *방향이나 수치가 실제로 적혀 있는가*.
   "이런 질문들을 다뤘다 / 이런 주제를 검토했다" 처럼 목차만 나열하고 결론이 없는 초록이 있다.
   그런 논문으로 카드뉴스를 만들면 훅에서 질문만 던지고 답을 못 하는 빈 껍데기가 된다.
   단, 후보에 "전문:있음" 이라고 적힌 논문은 초록에 결론이 없어도 본문에서 결론을 가져올 수 있으니
   이 항목 때문에 탈락시키지 말 것 ("전문:없음" 인데 초록에 결론도 없으면 반드시 탈락).
3. 독자 유용성: 일반 소비자가 "그래서 뭘 바꾸면 되는데?"에 답할 수 있는가.
4. 근거 수준: 메타분석·체계적 문헌고찰·RCT·대규모 코호트 > 소규모·동물·세포 실험.
5. 훅 가능성: 첫 장 한 줄로 궁금증을 만들 수 있는가.

출력은 JSON 하나만:
{"pick": <후보 번호(1부터) 또는 0(적합한 게 없음)>,
 "fit": <0~10 종합 적합도>,
 "conclusion": <0~10 결론을 확보할 수 있는가. 초록에 결과가 분명하면 높게. 초록엔 없지만 "전문:있음" 이면 7.
               초록에도 없고 "전문:없음" 이면 0~2>,
 "finding": "<초록에 적힌 핵심 결과를 한 줄로 옮겨 적기. 못 옮기겠으면 빈 문자열>",
 "angle": "<한 줄: 어떤 각도로 쓰면 좋은지>",
 "reason": "<한 줄 이유>"}
fit 이 6 미만이거나 conclusion 이 5 미만이면 pick 은 0 으로 하세요.
finding 을 한 줄로 옮겨 적을 수 없다면 그 논문은 conclusion 이 낮은 것입니다."""


def llm_judge(cands: list[dict]) -> dict | None:
    """후보 논문들을 LLM 에게 보여주고 제품 직접성 기준으로 하나를 고르게 한다. 실패하면 None."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from draft_post import generate  # noqa
    except Exception as e:
        print(f"  judge 불가(import): {e}", file=sys.stderr)
        return None
    try:
        import fulltext
    except Exception:
        fulltext = None
    lines = []
    for i, c in enumerate(cands, 1):
        p = c["paper"]
        abstract = re.sub(r"\s+", " ", p["abstract"])[:900]
        # 초록에 결론이 없어도 전문을 볼 수 있으면 쓸 수 있는 논문이다
        ft = "있음" if (fulltext and fulltext.has_fulltext(p.get("pmid", ""))) else "없음"
        c["fulltext"] = ft == "있음"
        lines.append(f"[{i}] 주제: {c['topic']['ko']} / 유형: {', '.join(p['pubtypes'][:3])} / {p['year']} / 전문:{ft}\n"
                     f"제목: {p['title']}\n초록: {abstract}\n")
    prompt = "후보 논문 목록:\n\n" + "\n".join(lines) + "\n위 기준으로 JSON 을 작성하세요."
    try:
        res, model = generate(prompt, JUDGE_SYSTEM)
    except Exception as e:
        print(f"  judge 실패: {e}", file=sys.stderr)
        return None
    try:
        pick = int(res.get("pick", 0))
        fit = float(res.get("fit", 0))
        concl = float(res.get("conclusion", 10))   # 옛 응답 호환: 없으면 통과
    except (TypeError, ValueError):
        return None
    finding = (res.get("finding") or "").strip()
    print(f"  judge({model}): pick={pick} fit={fit} conclusion={concl} angle={res.get('angle', '')} — {res.get('reason', '')}")
    if finding:
        print(f"    초록의 결론: {finding}")
    if pick < 1 or pick > len(cands):
        return {"pick": None, "fit": fit, "reason": res.get("reason", "")}
    if fit < 6:
        return {"pick": None, "fit": fit, "reason": f"적합도 부족({fit})"}
    has_ft = bool(cands[pick - 1].get("fulltext"))
    if concl < 5 and not has_ft:
        # 초록에 결론이 없고 전문도 못 보는 논문 → 훅만 있고 답이 없는 카드뉴스가 된다
        why = f"초록에 결론 없고 전문도 못 봄 (conclusion={concl})"
        print(f"  탈락: {why}", file=sys.stderr)
        return {"pick": None, "fit": fit, "reason": why}
    chosen = dict(cands[pick - 1])
    chosen["angle"] = res.get("angle", "")
    chosen["fit"] = fit
    chosen["finding"] = finding
    return {"pick": chosen}


def find_best(n_topics: int = 3, only: str | None = None, rounds: int = 3) -> dict | None:
    used = set()
    for u in load_json(USED, []):
        used.add(u.get("pmid", ""))
        if u.get("doi"):
            used.add(u["doi"])

    tried: set[str] = set()
    fallback = None
    for rnd in range(1, rounds + 1):
        cands: list[dict] = []
        topics = [t for t in pick_topics(n_topics * rnd, only) if t["id"] not in tried][:n_topics]
        if not topics:
            break
        for t in topics:
            tried.add(t["id"])
            print(f"검색: {t['ko']} — {t['query']}")
            try:
                ids = pubmed_search(t["query"])
                papers = pubmed_fetch(ids)
            except Exception as e:
                print(f"  검색 실패: {e}", file=sys.stderr)
                continue
            for p in papers:
                sc = score_paper(p, used, t)
                print(f"  [{sc:4.1f}] {p['year']} {p['title'][:80]}")
                if sc > 0:
                    cands.append({"score": sc, "topic": t, "paper": p})
                    if fallback is None or sc > fallback["score"]:
                        fallback = {"score": sc, "topic": t, "paper": p}
            time.sleep(0.4)
        if not cands:
            continue
        cands.sort(key=lambda c: -c["score"])
        cands = cands[:8]
        verdict = llm_judge(cands)
        if verdict is None:            # LLM 을 못 쓰면 점수 1등
            return cands[0]
        if verdict.get("pick"):
            return verdict["pick"]
        print(f"  {rnd}라운드: 적합한 논문 없음 ({verdict.get('reason', '')}) → 다른 주제로 재시도")
        if only:
            break
    return fallback


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
    if best.get("angle"):
        print(f"  각도: {best['angle']} (fit {best.get('fit')})")
    save_json(Path(args.out), best)
    return 0


if __name__ == "__main__":
    sys.exit(main())
