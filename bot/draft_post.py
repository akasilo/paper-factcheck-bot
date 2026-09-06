"""
원고 초안 작성 — 고른 논문(data/candidate.json)을 Gemini 에 넘겨 카드뉴스 원고·캡션·쓰레드 본문을 만들고
queue/<date>.json 을 생성한다 (approved: false).

실행
  python bot/draft_post.py --date 2026-09-08                # data/candidate.json 사용
  python bot/draft_post.py --date 2026-09-08 --candidate data/candidate.json
환경변수
  GEMINI_API_KEY (필수), GEMINI_MODEL (기본 gemini-3.6-flash)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
QUEUE = ROOT / "queue"
KST = timezone(timedelta(hours=9))

DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."

SYSTEM_PROMPT = """당신은 인스타그램 계정 @paper_factcheck 의 편집자입니다.
이 계정은 "우리가 몰랐던 일상 속 제품의 문제(또는 의외의 장점)를 최신 논문으로 팩트체크" 합니다.
아래 논문 초록만을 근거로 카드뉴스 원고를 한국어로 씁니다.

반드시 지킬 규칙
1. 근거는 오직 주어진 초록. 초록에 없는 수치·기전·주장을 지어내지 마세요. 초록이 애매하면 애매하다고 쓰세요.
2. 표현 수위: 논문 1편이므로 "한 리뷰 논문에 따르면", "~와 관련이 있다는 결과", "~할 수 있다" 로 씁니다.
   "증명됐다", "반드시", "무조건" 같은 단정은 금지. 동물·세포 실험이면 카드에 그 사실을 분명히 씁니다.
3. 의학적 진단·치료·예방 효능 문구 금지 (예: "암을 예방한다" X → "위험과 관련이 있다는 보고" O).
4. 특정 브랜드·제품명 금지. 공포 조장 금지. 마지막은 독자가 오늘 할 수 있는 실용적 행동 하나로 마무리.
5. 카드 마크업: 강조할 핵심 단어는 [[이렇게]] (노란 형광, 카드당 1~2개), 경고성 핵심은 {{이렇게}} (빨간 글자, 카드당 최대 1개). 줄바꿈은 \\n.
6. 글자 수 제한을 지키세요. hook 은 4줄 이내·줄당 12자 이내, body 카드는 각 110자 이내, threads_text 는 450자 이내, instagram_caption 은 1500자 이내.
7. 해시태그는 instagram_caption 끝에 10~14개, 한국어 위주, 반드시 #논문팩트체크 #paper_factcheck 포함.
8. instagram_caption 본문 끝에 출처 줄을 넣습니다: "📄 출처: 저자, 저널명 (연도). DOI xxx"
9. verdict 는 논문 결론에 따라 "bad"(일상 제품의 문제), "good"(의외로 좋음), "mixed"(엇갈림) 중 하나.
10. product_hint 는 독자가 대안으로 찾아볼 제품 조건을 한 줄로 (브랜드 없이). 논문이 제품 선택과 무관하면 빈 문자열.

출력은 JSON 하나만. 스키마:
{
  "verdict": "bad|good|mixed",
  "hook": "매일 쓰는\\n[[텀블러]]\\n{{미세플라스틱}}\\n괜찮을까요?",
  "kicker": "논문 팩트체크",
  "body_cards": [
    {"text": "...", "note": ""},
    {"text": "...", "note": ""},
    {"text": "...", "note": ""},
    {"text": "...", "note": "※ 한계나 주의점 한 줄(선택)"}
  ],
  "action_card": "오늘 할 수 있는 실용적 행동 (110자 이내, [[강조]] 1개)",
  "instagram_caption": "...",
  "threads_text": "...",
  "product_hint": "...",
  "evidence_level": "meta-analysis|systematic review|review|rct|cohort|animal|in-vitro|other",
  "caveats": ["원고에 반영하지 못한 한계 1~3개"]
}
body_cards 는 3~5개."""


def gemini_generate(prompt: str, system: str, model: str, api_key: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json",
            "maxOutputTokens": 4096,
        },
    }
    r = requests.post(url, params={"key": api_key}, json=body, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:500]}")
    data = r.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Gemini 응답 형식 이상: {json.dumps(data)[:500]}")
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


def build_prompt(topic: dict, paper: dict) -> str:
    return f"""[주제] {topic['ko']}  (대안 제품 힌트: {topic.get('hint', '')})

[논문]
제목: {paper['title']}
저자: {paper['authors']}
저널: {paper['journal']} ({paper['year']})
DOI: {paper['doi'] or '없음'}
논문 유형: {', '.join(paper['pubtypes'])}
MeSH: {', '.join(paper['mesh'][:15])}

[초록]
{paper['abstract']}

위 초록만 근거로 스키마에 맞는 JSON 을 작성하세요."""


def validate(d: dict) -> list[str]:
    problems = []
    if d.get("verdict") not in ("bad", "good", "mixed"):
        problems.append("verdict 값 이상")
    hook = d.get("hook", "")
    if not hook or len(hook.split("\n")) > 5:
        problems.append("hook 이 비었거나 5줄 초과")
    cards = d.get("body_cards") or []
    if not 3 <= len(cards) <= 5:
        problems.append(f"body_cards 수 이상 ({len(cards)})")
    for i, c in enumerate(cards, 1):
        if len(c.get("text", "")) > 160:
            problems.append(f"body_cards[{i}] 160자 초과")
    if not d.get("action_card"):
        problems.append("action_card 비었음")
    if len(d.get("instagram_caption", "")) > 2100 or not d.get("instagram_caption"):
        problems.append("instagram_caption 길이 이상")
    if len(d.get("threads_text", "")) > 500 or not d.get("threads_text"):
        problems.append("threads_text 길이 이상")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(datetime.now(KST) + timedelta(days=1)).strftime("%Y-%m-%d"),
                    help="큐 날짜 (기본: 내일 KST)")
    ap.add_argument("--candidate", default=str(DATA / "candidate.json"))
    ap.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"))
    ap.add_argument("--force", action="store_true", help="같은 날짜 큐가 있어도 덮어씀")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("GEMINI_API_KEY 가 없습니다.", file=sys.stderr)
        return 2

    cand = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    topic, paper = cand["topic"], cand["paper"]

    qpath = QUEUE / f"{args.date}.json"
    if qpath.exists() and not args.force:
        print(f"이미 큐가 있습니다: {qpath.name} (--force 로 덮어쓰기)")
        return 0

    print(f"Gemini({args.model}) 원고 작성: [{topic['ko']}] {paper['title'][:70]}")
    draft = None
    problems: list[str] = []
    for attempt in range(1, 4):
        try:
            draft = gemini_generate(build_prompt(topic, paper), SYSTEM_PROMPT, args.model, api_key)
        except Exception as e:
            print(f"  시도 {attempt} 실패: {e}", file=sys.stderr)
            continue
        problems = validate(draft)
        if not problems:
            break
        print(f"  시도 {attempt} 검증 실패: {problems}", file=sys.stderr)
    if draft is None or problems:
        print("원고 생성 실패", file=sys.stderr)
        return 1

    # ---- 큐 JSON 조립 ---------------------------------------------------
    cards = [{"type": "hook", "kicker": draft.get("kicker") or "논문 팩트체크", "text": draft["hook"]}]
    for c in draft["body_cards"]:
        card = {"type": "body", "text": c["text"]}
        if c.get("note"):
            card["note"] = c["note"]
        cards.append(card)
    cards.append({"type": "body", "text": draft["action_card"]})
    cards.append({"type": "source", "paper": {
        "title": paper["title"], "journal": paper["journal"], "year": paper["year"],
        "doi": paper["doi"], "authors": paper["authors"],
    }})

    caption = draft["instagram_caption"].rstrip()
    if "#paper_factcheck" not in caption:
        caption += " #paper_factcheck"
    caption += "\n*본 카드뉴스의 이미지는 AI를 이용하여 제작되었습니다."

    item = {
        "date": args.date,
        "approved": False,
        "link": "",
        "topic": topic["id"],
        "topic_ko": topic["ko"],
        "verdict": draft["verdict"],
        "evidence_level": draft.get("evidence_level", ""),
        "product_hint": draft.get("product_hint") or topic.get("hint", ""),
        "caveats": draft.get("caveats", []),
        "cards": cards,
        "images": [],
        "instagram_caption": caption,
        "threads_text": draft["threads_text"],
        "threads_reply": "📎 논문에서 말한 조건에 맞는 제품 예시예요\n{link}\n\n" + DISCLOSURE,
        "paper": {
            "title": paper["title"], "journal": paper["journal"], "year": paper["year"],
            "doi": paper["doi"], "pmid": paper["pmid"], "authors": paper["authors"],
            "url": paper["url"],
        },
        "drafted_at": datetime.now(KST).isoformat(),
        "model": args.model,
    }
    QUEUE.mkdir(exist_ok=True)
    qpath.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"큐 생성: {qpath.relative_to(ROOT)}  (카드 {len(cards)}장, verdict={draft['verdict']})")

    # ---- 상태 갱신: 주제 사용일, 사용한 논문 ----------------------------
    state_p = DATA / "topic_state.json"
    state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
    state[topic["id"]] = {"last_used": args.date, "pmid": paper["pmid"]}
    state_p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    used_p = DATA / "used_papers.json"
    used = json.loads(used_p.read_text(encoding="utf-8")) if used_p.exists() else []
    used.append({"pmid": paper["pmid"], "doi": paper["doi"], "date": args.date,
                 "topic": topic["id"], "title": paper["title"]})
    used_p.write_text(json.dumps(used, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
