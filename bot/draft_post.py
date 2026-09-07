"""
원고 초안 작성 — 고른 논문(data/candidate.json)을 Gemini 에 넘겨 카드뉴스 원고·캡션·쓰레드 본문을 만들고
queue/<date>.json 을 생성한다 (approved: false).

실행
  python bot/draft_post.py --date 2026-09-08                # data/candidate.json 사용
  python bot/draft_post.py --date 2026-09-08 --candidate data/candidate.json
환경변수
  LLM_PROVIDER : claude_code (기본, Claude 구독 OAuth 토큰) | groq | openrouter | cerebras | openai | openai_compat | gemini | github
  CLAUDE_CODE_OAUTH_TOKEN : claude_code 일 때 (claude setup-token 으로 발급). 없으면 groq 로 자동 대체
  LLM_API_KEY  : OpenAI 호환 제공자의 API 키 (groq 등)
  LLM_MODEL    : 모델명, 쉼표로 여러 개 적으면 앞에서부터 시도 (비우면 제공자별 기본 후보)
  LLM_BASE_URL : openai_compat 일 때 필수
  GEMINI_API_KEY, GEMINI_MODEL : gemini 일 때 / GH_TOKEN : github 일 때
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
11. 논문이 제품을 직접 다루지 않고 성분·물질만 다루면, hook 은 물질 중심으로 잡고 제품은 "이 물질이 쓰이는 제품 예"로만 연결하세요. 제품 자체가 위험/안전하다고 단정 금지.
12. 영어 학술 용어는 한국어로 풀어쓰세요 (umbrella review → 여러 메타분석을 종합한 리뷰, RCT → 무작위 대조 시험). 해시태그는 주제와 직접 관련된 것만.
13. instagram_caption 은 첫 줄 훅 → 빈 줄로 나눈 짧은 문단 3~4개 → 오늘의 행동 → 출처 줄 → 해시태그 순서. threads_text 는 말하듯 가볍게, 마지막은 독자에게 묻는 한 문장.

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
  "evidence_level": "meta-analysis|systematic review|review|rct|cohort|cross-sectional|experimental|animal|in-vitro|other",
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


def github_models_generate(prompt: str, system: str, model: str, token: str) -> dict:
    """GitHub Models (OpenAI 호환 chat/completions). 무료 사용량 안에서 동작."""
    url = "https://models.github.ai/inference/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(url, headers=headers, json=body, timeout=180)
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub Models {r.status_code}: {r.text[:500]}")
    data = r.json()
    try:
        choice = data["choices"][0]
        text = choice["message"]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(f"GitHub Models 응답 형식 이상: {json.dumps(data)[:500]}")
    if choice.get("finish_reason") == "length":
        raise RuntimeError("출력이 잘렸습니다(finish_reason=length) — 더 짧게 다시 시도")
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


def openai_compat_generate(prompt: str, system: str, model: str, api_key: str, base_url: str) -> dict:
    """OpenAI 호환 chat/completions (Groq, OpenRouter, Cerebras, Together, OpenAI 등 공용)."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(url, headers=headers, json=body, timeout=180)
    if r.status_code >= 400:
        raise RuntimeError(f"LLM({base_url}) {r.status_code}: {r.text[:500]}")
    data = r.json()
    try:
        choice = data["choices"][0]
        text = choice["message"]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(f"LLM 응답 형식 이상: {json.dumps(data)[:500]}")
    if choice.get("finish_reason") == "length":
        raise RuntimeError("출력이 잘렸습니다(finish_reason=length)")
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


def claude_code_generate(prompt: str, system: str, model: str | None = None) -> dict:
    """Claude Code CLI 를 구독(OAuth 토큰)으로 호출. 환경변수 CLAUDE_CODE_OAUTH_TOKEN 필요."""
    import shutil
    import subprocess
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI 가 설치되어 있지 않습니다 (npm i -g @anthropic-ai/claude-code)")
    full = system + "\n\n---\n\n" + prompt + "\n\n반드시 JSON 객체 하나만 출력하고 다른 말은 하지 마세요."
    cmd = [exe, "-p", full, "--output-format", "json", "--max-turns", "1"]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 실패({proc.returncode}): {(proc.stderr or proc.stdout)[:500]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI 출력 파싱 실패: {proc.stdout[:500]}")
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI 오류: {json.dumps(data)[:500]}")
    text = (data.get("result") or "").strip()
    m = re.search(r"\{.*\}", text, flags=re.S)   # 앞뒤 설명이 붙어도 JSON 만 뽑는다
    if not m:
        raise RuntimeError(f"claude CLI 결과에 JSON 없음: {text[:300]}")
    return json.loads(m.group(0))


# provider 별 기본 base_url 과 무료로 쓸 만한 모델 후보 (앞에서부터 시도, 404/모델없음이면 다음)
PROVIDER_DEFAULTS = {
    "groq": ("https://api.groq.com/openai/v1",
             ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "meta-llama/llama-4-maverick-17b-128e-instruct"]),
    "openrouter": ("https://openrouter.ai/api/v1",
                   ["openai/gpt-oss-120b:free", "deepseek/deepseek-chat-v3-0324:free", "qwen/qwen3-235b-a22b:free"]),
    "cerebras": ("https://api.cerebras.ai/v1", ["gpt-oss-120b", "llama-3.3-70b"]),
    "openai": ("https://api.openai.com/v1", ["gpt-4.1-mini"]),
    "openai_compat": ("", []),
}


def generate(prompt: str, system: str) -> tuple[dict, str]:
    """환경변수에 따라 provider 선택. (결과, 사용한 모델명) 반환

    LLM_PROVIDER = groq | openrouter | cerebras | openai | openai_compat | gemini | github
    LLM_API_KEY  = 위 OpenAI 호환 제공자의 키
    LLM_MODEL    = 모델명 (쉼표로 여러 개 적으면 앞에서부터 시도)
    LLM_BASE_URL = openai_compat 일 때 필수
    """
    provider = os.environ.get("LLM_PROVIDER", "claude_code").strip().lower()

    if provider == "claude_code":
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip():
            model = os.environ.get("LLM_MODEL", "").strip() or None
            return claude_code_generate(prompt, system, model), f"claude_code:{model or 'default'}"
        if os.environ.get("LLM_API_KEY", "").strip():
            print("CLAUDE_CODE_OAUTH_TOKEN 이 없어 groq 로 대체합니다.", file=sys.stderr)
            provider = "groq"
        else:
            raise SystemExit("CLAUDE_CODE_OAUTH_TOKEN 이 없습니다 (claude setup-token 으로 발급).")

    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise SystemExit("GEMINI_API_KEY 가 없습니다.")
        model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
        return gemini_generate(prompt, system, model, key), f"gemini:{model}"

    if provider == "github":
        token = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
        if not token:
            raise SystemExit("GH_TOKEN 이 없습니다 (GitHub Models 호출용).")
        model = os.environ.get("LLM_MODEL", "openai/gpt-4.1-mini")
        return github_models_generate(prompt, system, model, token), f"github:{model}"

    if provider not in PROVIDER_DEFAULTS:
        raise SystemExit(f"알 수 없는 LLM_PROVIDER: {provider}")
    base_url = os.environ.get("LLM_BASE_URL", "").strip() or PROVIDER_DEFAULTS[provider][0]
    if not base_url:
        raise SystemExit("LLM_BASE_URL 이 필요합니다 (openai_compat).")
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        raise SystemExit("LLM_API_KEY 가 없습니다. GitHub Secrets 에 넣어주세요.")
    models = [m.strip() for m in os.environ.get("LLM_MODEL", "").split(",") if m.strip()] \
        or PROVIDER_DEFAULTS[provider][1]
    last_err: Exception | None = None
    for model in models:
        try:
            return openai_compat_generate(prompt, system, model, key, base_url), f"{provider}:{model}"
        except RuntimeError as e:
            msg = str(e)
            last_err = e
            # 모델이 없거나 폐기된 경우만 다음 후보로, 나머지 오류는 그대로 올림
            if any(k in msg for k in (" 404", "model_not_found", "does not exist", "decommissioned", "not found")):
                print(f"  모델 {model} 사용 불가 → 다음 후보", file=sys.stderr)
                continue
            raise
    raise RuntimeError(f"사용 가능한 모델이 없습니다: {last_err}")


def build_prompt(topic: dict, paper: dict, angle: str = "") -> str:
    angle_line = f"\n[편집장이 정한 각도] {angle}\n" if angle else ""
    return f"""[주제] {topic['ko']}  (대안 제품 힌트: {topic.get('hint', '')}){angle_line}

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


def next_seq() -> int:
    """큐/게시완료/건너뜀 을 통틀어 가장 큰 순번 + 1. 큐는 날짜가 아니라 '순서'로 관리한다."""
    seen = [0]
    for d in (QUEUE, ROOT / "posted", ROOT / "skipped"):
        for p in d.glob("*.json"):
            m = re.match(r"(\d{4})-", p.name)
            if m:
                seen.append(int(m.group(1)))
            else:                       # posted/ 는 날짜 이름이라 안쪽 id 를 본다
                try:
                    d2 = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                sid = str((d2.get("queue") or d2).get("id", ""))
                m2 = re.match(r"(\d{4})-", sid)
                if m2:
                    seen.append(int(m2.group(1)))
    return max(seen) + 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", default=str(DATA / "candidate.json"))
    args = ap.parse_args()

    cand = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    topic, paper = cand["topic"], cand["paper"]

    QUEUE.mkdir(exist_ok=True)
    item_id = f"{next_seq():04d}-{topic['id']}"
    qpath = QUEUE / f"{item_id}.json"

    print(f"원고 작성 ({os.environ.get('LLM_PROVIDER', 'claude_code')}): [{topic['ko']}] {paper['title'][:70]}")
    draft = None
    used_model = ""
    problems: list[str] = []
    for attempt in range(1, 4):
        try:
            draft, used_model = generate(build_prompt(topic, paper, cand.get("angle", "")), SYSTEM_PROMPT)
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

    today = datetime.now(KST).strftime("%Y-%m-%d")
    item = {
        "id": item_id,
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
        "model": used_model,
    }
    qpath.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"큐 생성: {qpath.relative_to(ROOT)}  (카드 {len(cards)}장, verdict={draft['verdict']})")

    # ---- 상태 갱신: 주제 사용일, 사용한 논문 ----------------------------
    state_p = DATA / "topic_state.json"
    state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
    state[topic["id"]] = {"last_used": today, "pmid": paper["pmid"]}
    state_p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    used_p = DATA / "used_papers.json"
    used = json.loads(used_p.read_text(encoding="utf-8")) if used_p.exists() else []
    used.append({"pmid": paper["pmid"], "doi": paper["doi"], "date": today,
                 "topic": topic["id"], "title": paper["title"]})
    used_p.write_text(json.dumps(used, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
