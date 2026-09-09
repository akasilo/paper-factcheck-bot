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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import card_styles
import fulltext

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
QUEUE = ROOT / "queue"
KST = timezone(timedelta(hours=9))

DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."

SYSTEM_PROMPT = """당신은 인스타그램 계정 @paper_factcheck 의 편집자입니다.
이 계정은 "우리가 몰랐던 일상 속 제품의 문제(또는 의외의 장점)를 최신 논문으로 팩트체크" 합니다.
아래 논문 초록만을 근거로 카드뉴스 원고를 한국어로 씁니다.

반드시 지킬 규칙
1. 근거는 오직 주어진 [논문] 자료(초록, 그리고 있으면 [본문 발췌]). 거기에 없는 수치·기전·주장을 지어내지 마세요.
   초록이 애매해도 본문 발췌에 답이 있으면 본문을 근거로 쓰세요. 둘 다 애매하면 애매하다고 쓰세요.
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
15. **훅에서 던진 질문에는 반드시 body 카드에서 답하세요.** 이게 이 계정의 존재 이유입니다.
    "신장 망가진다, 진짜일까?" 로 시작했으면 어딘가에서 "이 논문에 따르면 ~ 였다" 로 답해야 합니다.
    "이 논문이 그 주제를 다뤘다 / 검토했다 / 질문 목록에 있었다" 는 답이 아닙니다. 그건 목차입니다.
    body 카드 중 최소 2장은 자료에 적힌 *구체적 결과*(방향·수치·비교)를 담아야 합니다.
    그리고 `answer` 필드에 그 답을 한 문장으로 옮겨 적고, `answer_card` 에 그 답이 들어있는 카드 번호를 쓰세요.
    `answer` 를 쓸 수 없다면 그건 답이 없다는 뜻이니 규칙 16 대로 abort 하세요.
16. **결론이 없으면 억지로 쓰지 마세요.** 초록에도 본문 발췌에도 결과의 방향·수치가 하나도
    없다면, 다른 필드 없이 `{"abort": true, "abort_reason": "<한 줄 이유>"}` 만 출력하세요.
    편집자는 다른 논문으로 넘어갑니다. 빈 껍데기 카드뉴스를 내보내는 것보다 그게 낫습니다.

14. 카드 디자인을 이 글의 성격에 맞게 고릅니다.
    style — "geo": 위험·오염·수치 폭로처럼 경고 톤이 강한 글 (어두운 배경 + 격자·원호 그래픽).
            "paper": 괴담 반박·안전성 해명·"의외로 괜찮다"처럼 차분히 정리하는 글 (밝은 종이 배경 + 검은 글씨).
            "soft": 수면·피부·기분처럼 몸에 관한 부드러운 생활 이야기 (은은한 색번짐).
    accent — 글의 소재에 어울리는 포인트 색 하나:
            "yellow"(기본·경고), "blue"(물·수면·화면·차분함), "green"(식품·자연·건강),
            "orange"(열·조리·에너지), "violet"(밤·뷰티·호르몬), "red"(가장 센 경고. 아껴 쓸 것).
    verdict 가 good 이면 대체로 paper, bad 면 geo 가 어울리지만 글 내용을 우선해서 고르세요.

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
  "answer": "훅에서 던진 질문에 대한 답 한 문장. 논문에 적힌 결과를 그대로. '다뤘다/검토했다' 는 답이 아님",
  "answer_card": <그 답이 들어있는 body_cards 번호 (1부터)>,
  "instagram_caption": "...",
  "threads_text": "...",
  "product_hint": "...",
  "evidence_level": "meta-analysis|systematic review|review|rct|cohort|cross-sectional|experimental|animal|in-vitro|other",
  "style": "geo|paper|soft",
  "accent": "yellow|blue|green|orange|violet|red",
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


def _cli_error(proc) -> str:
    """claude CLI 가 0 아닌 코드로 끝났을 때 사람이 읽을 수 있는 이유를 뽑는다.

    CLI 는 실패해도 stdout 에 긴 JSON 을 뱉는데 진짜 사유는 result/error 필드에 있다.
    통째로 자르면 usage 통계만 남고 사유가 잘려서 로그만 보고는 원인을 알 수 없다.
    """
    raw = (proc.stdout or "").strip()
    try:
        d = json.loads(raw)
    except Exception:
        return ((proc.stderr or raw).strip() or "(출력 없음)")[:400]
    bits = []
    for k in ("subtype", "result", "error", "message"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            bits.append(f"{k}={v.strip()[:300]}")
    if (proc.stderr or "").strip():
        bits.append(f"stderr={proc.stderr.strip()[:200]}")
    return " | ".join(bits) or raw[:400]


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
        raise RuntimeError(f"claude CLI 실패({proc.returncode}): {_cli_error(proc)}")
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


def _fallback_provider() -> str:
    """Claude 구독을 못 쓸 때 대신 쓸 제공자. 키가 있는 것 중 하나."""
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "gemini"
    if os.environ.get("LLM_API_KEY", "").strip():
        return "groq"
    return ""


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
            try:
                return claude_code_generate(prompt, system, model), f"claude_code:{model or 'default'}"
            except Exception as e:
                # 토큰 만료·사용량 한도 등으로 구독 호출이 죽어도 파이프라인은 굴러가게 한다.
                fb = _fallback_provider()
                if not fb:
                    raise
                print(f"!! Claude 구독 호출 실패 → {fb} 로 대체합니다: {e}", file=sys.stderr)
                provider = fb
        elif _fallback_provider():
            provider = _fallback_provider()
            print(f"CLAUDE_CODE_OAUTH_TOKEN 이 없어 {provider} 로 대체합니다.", file=sys.stderr)
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


def build_item(item_id: str, topic: dict, paper: dict, draft: dict,
               used_model: str, body_src: str = "") -> dict:
    """LLM 원고(draft) → 큐 항목 JSON. draft_post 와 redraft 가 같이 쓴다."""
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
    caption += "\n*본 카드뉴스는 AI의 도움을 받아 제작되었습니다."

    return {
        "id": item_id,
        "approved": False,
        "link": "",
        "topic": topic["id"],
        "topic_ko": topic["ko"],
        "verdict": draft["verdict"],
        "evidence_level": draft.get("evidence_level", ""),
        "source_text": body_src or "초록만",
        "answer": draft.get("answer", ""),
        "style": card_styles.resolve(draft.get("style")),
        "accent": draft.get("accent") if draft.get("accent") in card_styles.ACCENTS
                  else card_styles.DEFAULT_ACCENT,
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


def build_prompt(topic: dict, paper: dict, angle: str = "", body: str = "") -> str:
    angle_line = f"\n[편집장이 정한 각도] {angle}\n" if angle else ""
    body_block = f"\n\n[본문 발췌] (초록에 없는 내용도 여기 있으면 근거로 쓰세요)\n{body}" if body else ""
    return f"""[주제] {topic['ko']}  (대안 제품 힌트: {topic.get('hint', '')}){angle_line}

[논문]
제목: {paper['title']}
저자: {paper['authors']}
저널: {paper['journal']} ({paper['year']})
DOI: {paper['doi'] or '없음'}
논문 유형: {', '.join(paper['pubtypes'])}
MeSH: {', '.join(paper['mesh'][:15])}

[초록]
{paper['abstract']}{body_block}

위 자료만 근거로 스키마에 맞는 JSON 을 작성하세요."""


# 결과의 방향·수치가 담긴 문장에 나오는 말들
_FINDING_WORDS = (
    "높", "낮", "증가", "감소", "개선", "줄었", "늘었", "차이", "연관", "관련성",
    "유의", "효과", "위험", "없었", "없다", "없는", "않았", "않는", "이었", "였다",
    "나타났", "보고됐", "밝혀", "밝히", "확인됐", "원인", "증거", "결론은",
    "배", "%", "오즈비", "위약", "대조", "비해", "반면",
)
# 결과 없이 '다뤘다'만 말하는 목차형 문장
_TOC_WORDS = ("다뤘", "다룬", "검토했", "살펴봤", "포함돼", "포함됐", "정리했", "질문 목록", "언급됐", "소개돼")


def last_clause(text: str, min_len: int = 8) -> str:
    """문장에서 '무엇을 주장하는지' 가 드러나는 마지막 절.

    한국어는 서술어가 끝에 오고, 절은 '~고,' '~며,' 로 이어진다.
    쉼표·마침표로 끊어 마지막 조각을 본다 (너무 짧으면 앞 조각까지 붙인다).
    """
    parts = [p for p in re.split(r"[,.]\s*", (text or "").strip()) if p.strip()]
    if not parts:
        return ""
    out = parts[-1]
    if len(out) < min_len and len(parts) >= 2:
        out = parts[-2] + " " + out
    return out


def is_toc_like(text: str) -> bool:
    """'이런 주제를 다뤘다' 식 목차 문장인가 — 마지막 절의 서술어로 판단한다.

      "…통념 11가지를 정리했어요"                  → 목차 (주장이 '정리했다')
      "…정리했고, 심정지 9건이 확인됐다고 보고했어요"  → 목차 아님 (주장이 '확인됐다')
    문장 전체에서 찾으면, 앞에 '정리했고' 가 붙었을 뿐 뒤에 진짜 결과가 있는
    멀쩡한 카드까지 떨어뜨린다.
    """
    return any(w in last_clause(text) for w in _TOC_WORDS)


def answer_problems(d: dict) -> list[str]:
    """훅의 질문에 대한 답이 실제로 있는지 본다.

    2026-09-09 단백질 보충제 건: 초록이 주제 목록뿐인 논문으로 원고가 나가 훅에서 질문만
    던지고 7장 내내 '다뤘다·검토했다'만 반복한 게시물이 올라갔다. 그걸 막는 검사.
    """
    out = []
    ans = (d.get("answer") or "").strip()
    cards = d.get("body_cards") or []
    if len(ans) < 15:
        out.append("answer 가 비었거나 너무 짧음 (훅의 질문에 대한 답이 없음)")
        return out
    if is_toc_like(ans):
        out.append(f"answer 가 목차형이라 답이 아님: {ans[:40]}")
    if not any(w in ans for w in _FINDING_WORDS):
        out.append(f"answer 에 결과의 방향·수치가 없음: {ans[:40]}")
    try:
        idx = int(d.get("answer_card", 0))
    except (TypeError, ValueError):
        idx = 0
    if not 1 <= idx <= len(cards):
        out.append(f"answer_card 번호 이상 ({d.get('answer_card')})")
    else:
        card = cards[idx - 1].get("text", "") or ""
        if is_toc_like(card):
            out.append(f"answer_card 로 지목한 카드가 목차형: {card[:40]}")
        # 답이 정말 그 카드에 들어 있는지 — 두 글자 이상 어절이 2개 이상 겹치는지
        wa = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", ans)}
        wc = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", card)}
        if len(wa & wc) < 2:
            out.append("answer 가 answer_card 의 내용과 맞지 않음")
    return out


def has_findings(cards: list[dict], need: int = 1) -> bool:
    """body 카드 중 '구체적 결과'를 담은 게 need 장 이상인가.

    정밀 판정은 answer_problems() 가 한다. 이건 카드가 전부 목차 문장인 원고만 막는
    거친 그물이라 기준을 느슨하게 둔다 (멀쩡한 원고를 헛되이 떨어뜨리면 그만큼 생성이 낭비된다).
    """
    hits = 0
    for c in cards:
        t = c.get("text", "") or ""
        if is_toc_like(t):
            continue                      # 목차 문장은 숫자가 있어도 결과가 아니다
        if any(ch.isdigit() for ch in t) or any(w in t for w in _FINDING_WORDS):
            hits += 1
    return hits >= need


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
    problems += answer_problems(d)
    if not has_findings(cards):
        problems.append("body_cards 가 전부 목차 문장 (구체적 결과가 한 장도 없음)")
    if d.get("style") and d["style"] not in card_styles.STYLE_NAMES:
        problems.append(f"style 값 이상 ({d['style']})")
    if d.get("accent") and d["accent"] not in card_styles.ACCENTS:
        problems.append(f"accent 값 이상 ({d['accent']})")
    return problems


def next_seq() -> int:
    """큐/게시완료/건너뜀 을 통틀어 가장 큰 순번 + 1. 큐는 날짜가 아니라 '순서'로 관리한다."""
    seen = [0]
    for d in (QUEUE, ROOT / "posted", ROOT / "skipped"):
        for p in d.glob("*.json"):
            m = re.match(r"(\d{4})-(?=.*[A-Za-z])", p.name)   # 0001-airfryer O, 2026-09-08 X
            if m:
                seen.append(int(m.group(1)))
            else:                       # posted/ 는 날짜 이름이라 안쪽 id 를 본다
                try:
                    d2 = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                sid = str((d2.get("queue") or d2).get("id", ""))
                m2 = re.match(r"(\d{4})-(?=.*[A-Za-z])", sid)
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
    # 초록에 결론이 없는 논문이 있다 → 볼 수 있으면 본문을 같이 준다
    body_text, body_src = fulltext.get(paper.get("pmid", ""), doi=paper.get("doi", ""))
    if body_text:
        print(f"  본문 확보: {body_src} ({len(body_text)}자)")
    else:
        print("  본문 없음 — 초록만으로 작성")

    for attempt in range(1, 4):
        try:
            draft, used_model = generate(
                build_prompt(topic, paper, cand.get("angle", ""), body_text), SYSTEM_PROMPT)
        except Exception as e:
            print(f"  시도 {attempt} 실패: {e}", file=sys.stderr)
            continue
        if draft.get("abort"):
            print(f"  이 논문으로는 쓸 수 없음: {draft.get('abort_reason', '(이유 없음)')}", file=sys.stderr)
            print("원고 생성 중단 — 다른 논문으로", file=sys.stderr)
            return 1
        problems = validate(draft)
        if not problems:
            break
        print(f"  시도 {attempt} 검증 실패: {problems}", file=sys.stderr)
    if draft is None or problems:
        print("원고 생성 실패", file=sys.stderr)
        return 1

    # ---- 큐 JSON 조립 ---------------------------------------------------
    item = build_item(item_id, topic, paper, draft, used_model, body_src)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    qpath.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"큐 생성: {qpath.relative_to(ROOT)}  (카드 {len(item['cards'])}장, "
          f"verdict={item['verdict']}, 근거={item['source_text']})")

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
