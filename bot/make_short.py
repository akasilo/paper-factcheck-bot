"""카드뉴스 → 세로 숏폼(1080x1920) 영상. 카드를 한 장씩 넘기며 TTS 가 읽어 준다.

  python bot/make_short.py --id 2046-energy_drink            # images/<id>/short.mp4
  python bot/make_short.py --id 2046-energy_drink --no-tts   # 음성 없이(무음, 카드당 4초) 조립만 — 로컬 시험용
  python bot/make_short.py --id 2046-energy_drink --script   # 읽을 대본만 출력
  python bot/make_short.py --auto                            # 큐 전체: 영상이 없거나 카드·대본이 바뀐 것만 (render 뒤 자동)

소리 (2026-09-29 부터 기본은 Gemini TTS, 그 전엔 Google Cloud TTS):
  SHORT_TTS            gemini(기본) | google.  gemini 인데 GEMINI_API_KEY 가 없으면 google 로, 그것도 없으면 무음.
  [gemini]  Gemini 3.8 Flash TTS — 무료 등급 있음, 유료여도 오디오 100만 토큰(≈11시간)에 $9 (25토큰/초).
  GEMINI_API_KEY       필수
  GEMINI_TTS_MODEL     기본 gemini-3.8-flash-tts  (싸고 빠른 gemini-3.8-flash-lite-tts 도 가능)
  GEMINI_TTS_VOICE     기본 Aoede (30개 프리셋: Kore·Leda·Zephyr·Aoede·Puck·Charon·Orus·… 전부 한국어 됨)
  GEMINI_TTS_STYLE     말투 지시문 (기본: 차분하고 또렷한 설명 톤, 약간 빠르게). 영어·한국어 아무거나.
  GEMINI_TTS_RATE      기본 1.0 — 1보다 크면 ffmpeg atempo 로 빠르게 (Gemini 는 speakingRate 가 없어서 후처리)
  [google]  Google Cloud Text-to-Speech (무료 등급: WaveNet/Neural2 월 100만 자).
  GOOGLE_TTS_API_KEY   필수 (없으면 --no-tts 처럼 무음으로 만든다)
  GOOGLE_TTS_VOICE     기본 ko-KR-Neural2-A  (여성 / -B 여성 / -C 남성, ko-KR-Wavenet-A~D 도 가능)
  GOOGLE_TTS_RATE      기본 1.15 (말 빠르기. 한국어는 1.2 까지 자연스럽다)
  목소리·모델·말투를 바꾸면 short.json 의 voice 도장이 달라져 다음 render-cards 때 TTS 부터 다시 만든다.
  ※ Tier 1 은 gemini-3.8-flash-tts 가 **하루 100 요청** (글 하나 = 카드 수만큼, 보통 7~8회 → 하루 12편쯤).
    하루 한도는 **태평양 시간 자정(한국 16:00, 겨울엔 17:00)** 에 리셋된다 — 429 본문의 "retry in" 은 UTC 자정 기준이라 믿지 말 것.
    한도에 걸리면 그 글부터 Google Cloud TTS 로 만들고(short.json 에 tts_fallback 메모), 다음 날 다시 Gemini 로 만든다.

배경음 (2026-09-28): assets/music/*.mp3 + music.json (YouTube 오디오 보관함, 저작자 표시 불필요 곡만).
  글의 스타일(geo/paper/soft)에 맞는 곡을 id 해시로 하나 골라 목소리 밑에 깐다. 목소리가 나올 땐
  사이드체인 컴프레서로 음악을 더 낮추고(더킹), 시작·끝은 페이드. 유튜브 API 로는 오디오 보관함 곡을
  붙일 수 없어서 영상에 미리 섞는다 — 인스타 릴스·Threads 에도 같은 영상이 나간다.
  SHORT_MUSIC=off       배경음 끄기 (--no-music 도 같음)
  SHORT_MUSIC_VOLUME    배경음 음량 0~1, 기본 0.35 (목소리 대비 약 -15dB; 말할 땐 더킹으로 5dB 더 내려감)

화면: 카드(1080x1350)를 가운데 두고, 위아래 남는 띠는 같은 카드를 흐리게 키워서 깐다.
  각 카드는 그 카드 음성 길이 + PAD 초 만큼 머문다. 마지막 출처 카드는 짧게 한 줄만 읽는다.
  ffmpeg 가 PATH 에 있어야 한다 (GitHub 러너에는 기본 설치).

만든 mp3 는 images/<id>/tts/NN.mp3 (+ NN.txt: 문장·목소리 도장) 로 남겨 두고, 같은 문장이면 다시 만들지 않는다
(글자 수 아끼기). images/<id>/short.json 에 카드·대본 해시를 적어 두어, 배경을 바꿔 다시 렌더하면 영상만 다시
조립하고 TTS 는 그대로 쓴다. 게시(publish.py)는 이 short.mp4 를 인스타 릴스 + Threads 첫 장으로 올린다.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "queue"
IMAGES = ROOT / "images"

W, H = 1080, 1920
FPS = 30
PAD = 0.7            # 음성 끝나고 카드가 더 머무는 시간(초)
SILENT_SEC = 4.0     # --no-tts 일 때 카드당 시간
DEFAULT_VOICE = "ko-KR-Neural2-A"
DEFAULT_RATE = "1.15"
TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

GEMINI_TTS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_DEFAULT_MODEL = "gemini-3.8-flash-tts"
GEMINI_DEFAULT_VOICE = "Aoede"        # 2026-09-30 사용자 선택 (여성 14개 비교 샘플 중 4번)
GEMINI_DEFAULT_RATE = "1.0"
GEMINI_DEFAULT_STYLE = ("calm, clear and trustworthy Korean narration for a short explainer video, "
                        "like a friendly science reporter; natural but slightly brisk pace")
GEMINI_TTS_RETRIES = 5
GEMINI_TTS_GAP = 1.0          # 요청 사이 쉬는 시간(초) — 분당 요청 한도 보호

MUSIC_DIR = ROOT / "assets" / "music"
MUSIC_META = MUSIC_DIR / "music.json"
DEFAULT_MUSIC_VOLUME = "0.35"   # 배경음 음량 (0~1). 목소리(-18dB)보다 12~17dB 낮게 깔린다
MUSIC_FADE_IN, MUSIC_FADE_OUT = 1.5, 2.5


# ---------- 대본 ----------

def _plain(s: str) -> str:
    """카드 마크업([[강조]] {{색}} 줄바꿈)을 읽기 좋은 평문으로."""
    s = re.sub(r"\[\[(.+?)\]\]", r"\1", s)
    s = re.sub(r"\{\{(.+?)\}\}", r"\1", s)
    s = s.replace("\\n", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


_ENDINGS = (("아님", "아니에요"), ("있음", "있어요"), ("없음", "없어요"), ("않음", "않아요"),
            ("됨", "돼요"), ("임", "이에요"), ("함", "해요"), ("큼", "커요"), ("작음", "작아요"))


def _soften(s: str) -> str:
    """'…확률은 아님' 처럼 명사형으로 끝나는 메모를 말로 읽기 좋게."""
    s = s.rstrip(".")
    for a, b in _ENDINGS:
        if s.endswith(a):
            return s[: -len(a)] + b + "."
    return s + "." if s and not s.endswith((".", "!", "?")) else s


# ---------- 숫자·단위 읽기 (2026-09-30 사용자 요청: "611 → 육백십일, vs → 대, mg → 밀리그램") ----------
# Gemini TTS 는 아라비아 숫자를 "6백열하나" 처럼 한자어·고유어를 섞어 읽을 때가 있어서, 대본에서 미리 한글로 바꿔 준다.
# 규칙: 고유어 수사(한·두·세…)를 붙이는 단위(명·개·잔·시간·개월·배·번…)는 1~99 까지 고유어, 그 밖엔 전부 한자어(일이삼…).

_SINO = "영일이삼사오육칠팔구"
_NATIVE_ONES = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯", 6: "여섯", 7: "일곱", 8: "여덟", 9: "아홉"}
_NATIVE_TENS = {1: "열", 2: "스물", 3: "서른", 4: "마흔", 5: "쉰", 6: "예순", 7: "일흔", 8: "여든", 9: "아흔"}
# 고유어 수사와 어울리는 단위 (긴 것부터 — '시간' 이 '시' 보다 먼저 맞아야 한다)
_NATIVE_COUNTERS = ("개월", "시간", "사람", "가지", "군데", "봉지", "숟가락", "스푼", "그릇", "조각", "방울", "켤레",
                    "마리", "명", "개", "잔", "살", "시", "번", "배", "병", "알", "캔", "컵", "장", "곳", "달", "줄",
                    "권", "벌", "쌍", "통", "송이", "척", "채")
# 숫자 뒤에 붙는 단위 → 읽는 말 (긴 것부터). 앞에 숫자가 있을 때만 바꾼다.
_UNITS = (("mg/dL", "밀리그램 퍼 데시리터"), ("mg/dl", "밀리그램 퍼 데시리터"), ("mmHg", "밀리미터 수은주"),
          ("mmol/L", "밀리몰 퍼 리터"), ("ng/mL", "나노그램 퍼 밀리리터"), ("ng/ml", "나노그램 퍼 밀리리터"),
          ("μg/dL", "마이크로그램 퍼 데시리터"), ("µg/dL", "마이크로그램 퍼 데시리터"),
          ("kg/m²", "킬로그램 퍼 제곱미터"), ("kg/m2", "킬로그램 퍼 제곱미터"),
          ("kcal", "킬로칼로리"), ("mcg", "마이크로그램"), ("μg", "마이크로그램"), ("µg", "마이크로그램"), ("㎍", "마이크로그램"),
          ("mg", "밀리그램"), ("kg", "킬로그램"), ("mL", "밀리리터"), ("ml", "밀리리터"), ("㎖", "밀리리터"),
          ("cm", "센티미터"), ("mm", "밀리미터"), ("km", "킬로미터"), ("nm", "나노미터"),
          ("ppm", "피피엠"), ("ppb", "피피비"), ("dB", "데시벨"), ("Hz", "헤르츠"), ("IU", "아이유"),
          ("°C", "도"), ("℃", "도"), ("g", "그램"), ("L", "리터"), ("m", "미터"))


def _sino(n: int) -> str:
    """611 → 육백십일, 10000 → 만, 2023 → 이천이십삼."""
    if n == 0:
        return "영"
    small, big = ("", "십", "백", "천"), ("", "만", "억", "조")
    chunks: list[str] = []
    grp = 0
    while n > 0:
        part, n = n % 10000, n // 10000
        s = ""
        for i in range(4):
            d, part = part % 10, part // 10
            if d:
                s = ("" if d == 1 and i > 0 else _SINO[d]) + small[i] + s
        if s:
            if grp == 1 and s == "일":
                s = ""                       # 일만 → 만
            chunks.insert(0, s + big[grp])
        grp += 1
    return "".join(chunks)


def _native(n: int) -> str:
    """1~99 를 단위 앞에 오는 고유어로: 1 → 한, 20 → 스무, 24 → 스물네."""
    tens, ones = divmod(n, 10)
    if tens == 0:
        return _NATIVE_ONES[ones]
    if n == 20:
        return "스무"
    return _NATIVE_TENS[tens] + (_NATIVE_ONES[ones] if ones else "")


def _num_words(text: str, counter: str = "") -> str:
    """숫자 문자열(정수·소수) → 한글. counter 가 고유어 단위이고 1~99 정수면 고유어."""
    if "." in text:
        a, b = text.split(".", 1)
        return (_sino(int(a)) if a else "") + "점" + "".join(_SINO[int(c)] for c in b if c.isdigit())
    n = int(text)
    if counter and 1 <= n <= 99:
        return _native(n)
    return _sino(n)


def _spoken_numbers(s: str) -> str:
    """TTS 가 잘못 읽기 쉬운 표기를 읽는 말로 바꾼다."""
    s = s.replace("·", ", ")
    s = re.sub(r"(?i)\b(?:vs\.?|v\.)\s*(?=\S)", "대 ", s)           # 젤 vs 스프레이 → 젤 대 스프레이
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)                             # 1,200 → 1200
    for u, spoken in _UNITS:                                              # 150mg → 150 밀리그램
        s = re.sub(rf"(?<=\d)\s*{re.escape(u)}(?![A-Za-z°℃])", f" {spoken}", s)
    s = s.replace("%", " 퍼센트")
    counters = "|".join(_NATIVE_COUNTERS)
    # 단위 뒤에 조사·'째'·'간' 등이 오거나 끝나야 단위로 본다 ('3살균', '2개국' 같은 건 안 건드린다)
    after = r"(?=$|[^가-힣]|(?:들|이|은|을|에|의|으로|로|도|만|이나|이상|이하|씩|당|마다|쯤|정도|간|째|가|과|와|까지|부터|보다|밖에|인|짜리))"
    s = re.sub(r"\bp\s*<\s*(\.?\d+(?:\.\d+)?)", lambda m: f"p값 {_num_words('0' + m[1] if m[1].startswith('.') else m[1])} 미만", s)
    s = re.sub(r"\bp\s*=\s*(\.?\d+(?:\.\d+)?)", lambda m: f"p값 {_num_words('0' + m[1] if m[1].startswith('.') else m[1])}", s)
    s = re.sub(r"(?<![\w가-힣])[-−–](?=\.?\d)", "마이너스 ", s)                # -8.0 → 마이너스 8.0
    s = re.sub(r"(?<=[\d%가-힣)])\s*\+\s*(?=[\d가-힣(])", " 플러스 ", s)      # 62.5%+칼륨 → 62.5% 플러스 칼륨
    s = re.sub(r"(?<![\d.])\.(\d+)", lambda m: "영점" + "".join(_SINO[int(c)] for c in m[1]), s)   # .05 → 영점영오
    # 1~2잔 → 한 잔에서 두 잔 / 3~5회 → 삼 회에서 오 회
    s = re.sub(rf"(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)\s*({counters}){after}",
               lambda m: f"{_num_words(m[1], m[3])} {m[3]}에서 {_num_words(m[2], m[3])} {m[3]}", s)
    sino_units = "|".join(sorted(("회", "번째", "일", "주", "년", "분", "초", "퍼센트", "도", "위", "등급", "단계")
                                 + tuple(u for _, u in _UNITS), key=len, reverse=True))
    s = re.sub(rf"(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)\s*({sino_units})(?![가-힣])",
               lambda m: f"{_num_words(m[1])} {m[3]}에서 {_num_words(m[2])} {m[3]}", s)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*[~\-–]\s*(\d+(?:\.\d+)?)", lambda m: f"{_num_words(m[1])}에서 {_num_words(m[2])}", s)
    # 611명 → 육백십일 명, 2잔 → 두 잔, 3개월 → 세 개월
    s = re.sub(rf"(\d+(?:\.\d+)?)\s*({counters}){after}", lambda m: f"{_num_words(m[1], m[2])} {m[2]}", s)
    # 남은 숫자는 전부 한자어 (2023년 → 이천이십삼년, 30 퍼센트 → 삼십 퍼센트, 1.5배 는 위에서 처리됨)
    s = re.sub(r"(\d+(?:\.\d+)?)(?=[가-힣])", lambda m: _num_words(m[1]) + " ", s)   # 2023년 → 이천이십삼 년
    s = re.sub(r"\d+(?:\.\d+)?", lambda m: _num_words(m[0]), s)
    s = re.sub(r"[ ]{2,}", " ", s)
    return s


def script_for(item: dict) -> list[str]:
    """카드 순서대로 읽을 문장. 출처 카드는 한 줄 요약."""
    lines: list[str] = []
    for card in item.get("cards") or []:
        t = card.get("type")
        if t == "source":
            p = card.get("paper") or item.get("paper") or {}
            year = str(p.get("year") or "").strip()
            journal = str(p.get("journal") or "").strip()
            src = f"{year}년 {journal}에 실린 논문" if year and journal else "화면에 적힌 논문"
            lines.append(_spoken_numbers(f"이 내용은 {src}을 근거로 정리했어요. 자세한 출처는 화면을 확인해 주세요."))
            continue
        text = _plain(card.get("text") or "")
        note = _soften(_plain(card.get("note") or "").lstrip("※ ").strip())
        if note:
            text = f"{text} 참고로, {note}"
        if text:
            lines.append(_spoken_numbers(text))
    return lines


# ---------- 소리 ----------

def tts_config(provider: str | None = None) -> dict:
    """환경변수에서 TTS 설정을 읽는다. provider 는 gemini / google / none(무음).
    provider 를 주면 그걸로 강제 (gemini 가 막혔을 때 google 로 넘어갈 때 씀)."""
    want = provider or (os.environ.get("SHORT_TTS", "") or "gemini").strip().lower()
    gkey = os.environ.get("GEMINI_API_KEY", "").strip()
    ckey = os.environ.get("GOOGLE_TTS_API_KEY", "").strip()
    provider = want
    if provider == "gemini" and not gkey:
        provider = "google"
    if provider == "google" and not ckey:
        provider = "none"
    if provider == "gemini":
        model = os.environ.get("GEMINI_TTS_MODEL", "").strip() or GEMINI_DEFAULT_MODEL
        voice = os.environ.get("GEMINI_TTS_VOICE", "").strip() or GEMINI_DEFAULT_VOICE
        return {"provider": "gemini", "key": gkey, "model": model, "voice": voice,
                "rate": os.environ.get("GEMINI_TTS_RATE", "").strip() or GEMINI_DEFAULT_RATE,
                "style": os.environ.get("GEMINI_TTS_STYLE", "").strip() or GEMINI_DEFAULT_STYLE,
                # short.json / NN.txt 도장 — 이 값이 바뀌면 다시 만든다
                "tag": f"gemini:{model}:{voice}"}
    if provider == "google":
        voice = os.environ.get("GOOGLE_TTS_VOICE", "").strip() or DEFAULT_VOICE
        return {"provider": "google", "key": ckey, "model": "", "voice": voice,
                "rate": os.environ.get("GOOGLE_TTS_RATE", "").strip() or DEFAULT_RATE,
                "style": "", "tag": voice}
    return {"provider": "none", "key": "", "model": "", "voice": "", "rate": "", "style": "", "tag": ""}


def tts_stamp(cfg: dict, line: str) -> str:
    """NN.txt 에 적는 도장. google 은 옛 형식 그대로(기존 mp3 를 다시 만들지 않게)."""
    if cfg["provider"] == "gemini":
        return f"{cfg['tag']}|{cfg['rate']}|{cfg['style']}|{line}"
    return f"{cfg['tag']}|{cfg['rate']}|{line}"


def tts(text: str, out: Path, cfg: dict) -> None:
    if cfg["provider"] == "gemini":
        tts_gemini(text, out, cfg)
    elif cfg["provider"] == "google":
        tts_google(text, out, cfg["key"], cfg["voice"], cfg["rate"])
    else:
        raise RuntimeError("TTS 설정 없음")


def tts_google(text: str, out: Path, key: str, voice: str, rate: str) -> None:
    import requests
    body = {
        "input": {"text": text},
        "voice": {"languageCode": "ko-KR", "name": voice},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": float(rate), "sampleRateHertz": 24000},
    }
    r = requests.post(TTS_URL, params={"key": key}, json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"TTS HTTP {r.status_code}: {r.text[:300]}")
    out.write_bytes(base64.b64decode(r.json()["audioContent"]))


def _find_audio(obj) -> str | None:
    """interactions 응답에서 audio 조각(base64)을 찾는다. 문서상 경로:
    steps[].content[] 중 type=="audio" 의 data (마지막 것). 구조가 조금 달라도 찾도록 재귀로 훑는다."""
    found: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "audio" and isinstance(o.get("data"), str):
                found.append(o["data"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    if found:
        return found[-1]
    oa = obj.get("output_audio") if isinstance(obj, dict) else None
    if isinstance(oa, dict) and isinstance(oa.get("data"), str):
        return oa["data"]
    return None


class TtsUnavailable(RuntimeError):
    """이 설정으로는 지금 TTS 를 못 쓴다 (선불 크레딧 소진 402, 키 문제 401/403, 하루 요청 한도 등). 다른 제공자로 넘어갈 신호."""


_GEMINI_DOWN: str = ""      # 이 프로세스 안에서 Gemini 가 막힌 이유 (하루 한도·크레딧). 남은 글은 두드리지 않고 바로 대체.


def _daily_limit(text: str) -> bool:
    """429 본문이 '하루 한도'(per day / PerDay / RPD) 초과인지. 분당 한도면 기다리면 되지만 하루 한도는 기다려도 소용없다."""
    t = text.lower()
    return "per day" in t or "perday" in t or "requests per day" in t or "daily" in t


def tts_gemini(text: str, out: Path, cfg: dict) -> None:
    """Gemini TTS (interactions API) → WAV(24kHz mono) → mp3. 429/5xx 는 잠깐 쉬고 다시,
    402(선불 크레딧 소진)·401·403 은 TtsUnavailable 로 바로 올린다."""
    import time

    import requests
    content = {"type": "text", "text": text}
    if cfg.get("style"):
        content["annotations"] = [{"type": "speech_metadata", "style": cfg["style"]}]
    body = {
        "model": cfg["model"],
        "input": [{"type": "user_input", "content": [content]}],
        "response_format": {"type": "audio", "mime_type": "audio/wav", "sample_rate": 24000},
        "generation_config": {"speech_config": [{"voice": cfg["voice"]}]},
    }
    headers = {"x-goog-api-key": cfg["key"], "Content-Type": "application/json"}
    global _GEMINI_DOWN
    if _GEMINI_DOWN:
        raise TtsUnavailable(f"Gemini TTS 막힘 (이번 실행에서 이미 확인): {_GEMINI_DOWN}")
    last = ""
    data = None
    for attempt in range(GEMINI_TTS_RETRIES):
        try:
            r = requests.post(GEMINI_TTS_URL, headers=headers, json=body, timeout=120)
        except requests.RequestException as e:
            last = f"네트워크: {str(e)[:200]}"
            r = None
        if r is not None:
            if r.status_code == 200:
                try:
                    data = _find_audio(r.json())
                except ValueError:
                    data = None
                if data:
                    break
                last = f"응답에 audio 없음: {r.text[:300]}"
            elif r.status_code == 429 and _daily_limit(r.text):
                # Tier 1 은 gemini-3.8-flash-tts 하루 100회 (2026-09-30 확인). 기다려도 안 풀리니 바로 대체.
                _GEMINI_DOWN = f"하루 요청 한도: {r.text[:160]}"
                raise TtsUnavailable(f"Gemini TTS HTTP 429 (하루 한도): {r.text[:300]}")
            elif r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}: {r.text[:200]}"
            elif r.status_code in (401, 402, 403):
                _GEMINI_DOWN = f"HTTP {r.status_code}: {r.text[:160]}"
                raise TtsUnavailable(f"Gemini TTS HTTP {r.status_code}: {r.text[:300]}")
            else:
                raise RuntimeError(f"Gemini TTS HTTP {r.status_code}: {r.text[:300]}")
        if attempt < GEMINI_TTS_RETRIES - 1:
            wait = (15, 30, 60, 90)[min(attempt, 3)]
            print(f"  Gemini TTS 재시도 {attempt + 1}/{GEMINI_TTS_RETRIES} ({last}) → {wait}초", file=sys.stderr)
            time.sleep(wait)
    if not data:
        raise TtsUnavailable(f"Gemini TTS 실패: {last}")
    wav = out.with_suffix(".wav")
    wav.write_bytes(base64.b64decode(data))
    rate = float(cfg.get("rate") or 1.0)
    af = ["-af", f"atempo={rate:.3f}"] if abs(rate - 1.0) > 0.005 else []
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), *af,
                    "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", str(out)], check=True)
    wav.unlink(missing_ok=True)
    time.sleep(GEMINI_TTS_GAP)


def duration_of(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip())


# ---------- 화면 ----------

def segment(card: Path, audio: Path | None, seconds: float, out: Path) -> None:
    """카드 한 장 + 음성 → 같은 인코딩의 mp4 조각."""
    vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"gblur=sigma=28,eq=brightness=-0.22:saturation=0.9[bg];"
          f"[0:v]scale={W}:-2[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-i", str(card)]
    if audio:
        cmd += ["-i", str(audio)]
        af = f"[1:a]apad=pad_dur={PAD + 0.2}[a]"
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono"]
        af = "[1:a]anull[a]"
    cmd += ["-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]",
            "-t", f"{seconds:.2f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)


def concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    lst.unlink(missing_ok=True)


# ---------- 배경음 ----------

def music_enabled() -> bool:
    return os.environ.get("SHORT_MUSIC", "").strip().lower() not in ("off", "0", "false", "no")


def music_volume() -> str:
    v = os.environ.get("SHORT_MUSIC_VOLUME", "").strip() or DEFAULT_MUSIC_VOLUME
    try:
        return f"{max(0.0, min(1.0, float(v))):.3f}"
    except ValueError:
        return DEFAULT_MUSIC_VOLUME


def music_tracks() -> list[dict]:
    """assets/music/music.json 의 곡 목록 (파일이 실제로 있는 것만)."""
    if not MUSIC_META.exists():
        return []
    try:
        data = json.loads(MUSIC_META.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for t in data.get("tracks") or []:
        f = str(t.get("file") or "")
        if f and (MUSIC_DIR / f).exists():
            out.append(t)
    return out


def style_of(item: dict) -> str:
    """카드 스타일(geo/paper/soft). render_cards 와 같은 규칙 — 없으면 card_styles.auto_pick."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import card_styles  # noqa: PLC0415
        if item.get("style"):
            return card_styles.resolve(item.get("style"))
        return card_styles.auto_pick(item)[0]
    except Exception:
        return str(item.get("style") or "geo")


def pick_music(item: dict) -> dict | None:
    """스타일에 맞는 곡 중 id 해시로 하나. 없으면 None (= 음악 없이)."""
    if not music_enabled():
        return None
    tracks = music_tracks()
    if not tracks:
        return None
    style = style_of(item)
    cands = [t for t in tracks if style in (t.get("styles") or [])] or tracks
    key = str(item.get("id") or "")
    idx = int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % len(cands)
    return cands[idx]


def music_credit(track: dict | None) -> str:
    """저작자 표시가 필요한 곡이면 설명에 붙일 한 줄. (지금 넣어 둔 곡은 전부 불필요)"""
    if not track or not track.get("attribution"):
        return ""
    return f"Music: {track.get('title', '')} — {track.get('artist', '')} ({track.get('license') or 'YouTube Audio Library'})"


def mix_music(video: Path, music: Path, out: Path, seconds: float, volume: str) -> None:
    """목소리 영상 + 배경음 → 배경음을 낮게 깔고(더킹) 페이드 넣어 다시 muxing (영상은 그대로 copy)."""
    t = max(seconds, 1.0)
    fade_out_at = max(t - MUSIC_FADE_OUT, 0.0)
    fc = (
        f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,atrim=0:{t:.2f},asetpts=N/SR/TB,"
        f"volume={volume},afade=t=in:st=0:d={MUSIC_FADE_IN},afade=t=out:st={fade_out_at:.2f}:d={MUSIC_FADE_OUT}[m];"
        # 목소리(모노)는 양쪽 채널에 그대로 복사한다 (기본 업믹스는 -3dB 깎인다)
        f"[0:a]aformat=sample_rates=44100:channel_layouts=mono,pan=stereo|c0=c0|c1=c0,asplit=2[v1][v2];"
        f"[m][v2]sidechaincompress=threshold=0.05:ratio=3:attack=40:release=400:level_sc=1[md];"
        f"[v1][md]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    )
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-stream_loop", "-1", "-i", str(music),
           "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-t", f"{t:.2f}",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)


# ---------- 조립 ----------

def load_item(item_id: str) -> dict | None:
    """queue/<id>.json → 없으면(이미 게시돼 지워짐) posted/*.json 의 queue 항목에서 찾는다."""
    qpath = QUEUE / f"{item_id}.json"
    if qpath.exists():
        return json.loads(qpath.read_text(encoding="utf-8"))
    for p in sorted((ROOT / "posted").glob("*.json"), reverse=True):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        q = rec.get("queue") or {}
        if q.get("id") == item_id:
            return q
    return None


def _sha(paths: list[Path]) -> str:
    h = hashlib.sha1()
    for p in paths:
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _script_sha(lines: list[str]) -> str:
    return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def is_current(item_id: str) -> bool:
    """short.mp4 가 있고, 만들 때의 카드·대본과 지금 것이 같으면 True."""
    item = load_item(item_id)
    if not item:
        return False
    imgs = [ROOT / p for p in (item.get("images") or [])]
    out_dir = IMAGES / item_id
    final, meta = out_dir / "short.mp4", out_dir / "short.json"
    if not final.exists() or not meta.exists() or not imgs or any(not p.exists() for p in imgs):
        return False
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return False
    cfg = tts_config()
    if _meta_matches(m, item, imgs, cfg):
        return True
    # gemini 가 막혀(크레딧 소진 등) google 로 만든 기록: 같은 날엔 다시 두드리지 않는다.
    # 날이 바뀌면 하루 한 번 gemini 를 다시 시도하고, 여전히 안 되면 영상은 그대로 두고 메모 날짜만 갱신한다.
    fb = m.get("tts_fallback") or {}
    if (cfg["provider"] == "gemini" and fb.get("wanted") == cfg["tag"] and fb.get("date") == _today()
            and _meta_matches(m, item, imgs, tts_config("google"))):
        return True
    return False


def _today() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def _meta_matches(m: dict, item: dict, imgs: list[Path], cfg: dict) -> bool:
    """short.json 기록이 지금 카드·대본·TTS 설정·배경음과 같은가."""
    want_voice, want_rate = cfg["tag"], cfg["rate"]
    track = pick_music(item)
    want_music = f"{track['file']}@{music_volume()}" if track else ""
    return (m.get("cards") == _sha(imgs)
            and m.get("script") == _script_sha(script_for(item))
            and bool(m.get("voice"))
            # 목소리·속도를 바꾸면 다시 만든다 (옛 기록엔 rate 가 없으니 있을 때만 비교)
            and m.get("voice") == want_voice
            and str(m.get("rate", want_rate)) == str(want_rate)
            # 말투 지시문(gemini)이 바뀌어도 다시 만든다 (옛 기록엔 style 이 없으니 있을 때만 비교)
            and str(m.get("style", cfg["style"])) == str(cfg["style"])
            # 배경음(곡·음량)이 바뀌어도 다시 조립한다 (옛 기록엔 music 이 없음 → "" 와 비교)
            and str(m.get("music", "")) == want_music)


def build(item_id: str, use_tts: bool = True, force: bool = False, use_music: bool = True) -> Path | None:
    item = load_item(item_id)
    if not item:
        print(f"큐/게시 기록에 {item_id} 가 없음", file=sys.stderr)
        return None
    imgs = [ROOT / p for p in (item.get("images") or [])]
    if not imgs or any(not p.exists() for p in imgs):
        print("카드 이미지가 아직 없음 → 먼저 render_cards.py", file=sys.stderr)
        return None
    lines = script_for(item)
    if len(lines) != len(imgs):
        print(f"카드 {len(imgs)}장 / 대본 {len(lines)}줄 — 수가 안 맞음", file=sys.stderr)
        return None

    out_dir = IMAGES / item_id
    final, meta = out_dir / "short.mp4", out_dir / "short.json"
    if not force and is_current(item_id):
        print(f"이미 최신: {final.relative_to(ROOT)}")
        return final

    cfg = tts_config()
    if use_tts and cfg["provider"] == "none":
        print("TTS 키 없음 (GEMINI_API_KEY / GOOGLE_TTS_API_KEY) → 무음으로 만듭니다", file=sys.stderr)
        use_tts = False
    if use_tts:
        print(f"  TTS: {cfg['provider']} {cfg['model']} {cfg['voice']} (속도 {cfg['rate']})")
    wanted_tag = cfg["tag"]          # 원래 쓰려던 설정 (gemini 가 막혀 google 로 넘어가면 메모에 남긴다)
    fallback: dict | None = None

    tts_dir = out_dir / "tts"
    if use_tts:
        tts_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        parts: list[Path] = []
        total = 0.0
        made = 0                     # 이번 실행에서 새로 만든 TTS 수 (제공자를 바꿔도 되는지 판단)
        i = 0
        while i < len(imgs):
            i += 1
            img, line = imgs[i - 1], lines[i - 1]
            audio = None
            if use_tts:
                audio = tts_dir / f"{i:02d}.mp3"
                stamp = tts_dir / f"{i:02d}.txt"        # 이 mp3 가 어떤 문장·목소리로 만든 것인지
                want = tts_stamp(cfg, line)
                have = stamp.read_text(encoding="utf-8") if stamp.exists() else ""
                if force or not audio.exists() or have != want:
                    try:
                        tts(line, audio, cfg)
                    except TtsUnavailable as e:
                        alt = tts_config("google") if cfg["provider"] == "gemini" else None
                        if not alt or alt["provider"] != "google":
                            raise
                        # 통째로 google 로 바꿔 처음부터 다시 만든다 — 이미 만든 gemini 카드가 있어도 도장이 달라
                        # 전부 다시 만드니 목소리가 섞이지 않는다 (09-30: 2장 만들고 하루 한도에 걸려 실패했던 것)
                        if made:
                            print(f"  {str(e)[:160]}\n  → 카드 {made}장 만든 뒤 막힘. 통째로 Google Cloud TTS 로 다시 만듭니다 ({alt['voice']})", file=sys.stderr)
                        else:
                            print(f"  {str(e)[:160]}\n  → Google Cloud TTS 로 대신 만듭니다 ({alt['voice']})", file=sys.stderr)
                        fallback = {"wanted": wanted_tag, "date": _today()}
                        cfg = alt
                        if not force and meta.exists():
                            try:
                                old = json.loads(meta.read_text(encoding="utf-8"))
                            except Exception:
                                old = {}
                            if _meta_matches(old, item, imgs, cfg) and final.exists():
                                old["tts_fallback"] = fallback      # 영상은 그대로, 메모 날짜만 갱신
                                meta.write_text(json.dumps(old, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                                print(f"  google 로 만든 영상이 이미 최신 → 그대로 둠: {final.relative_to(ROOT)}")
                                return final
                        parts, total, i, made = [], 0.0, 0, 0
                        continue
                    made += 1
                    stamp.write_text(want, encoding="utf-8")
                    print(f"  TTS {i}/{len(lines)}: {len(line)}자")
                seconds = duration_of(audio) + PAD
            else:
                seconds = SILENT_SEC
            part = Path(td) / f"{i:02d}.mp4"
            segment(img, audio, seconds, part)
            parts.append(part)
            total += seconds
            print(f"  카드 {i}/{len(imgs)} → {seconds:.1f}초")
        track = pick_music(item) if use_music else None
        if track:
            voice_only = Path(td) / "voice_only.mp4"
            concat(parts, voice_only)
            try:
                mix_music(voice_only, MUSIC_DIR / track["file"], final, total, music_volume())
                print(f"  배경음: {track.get('title')} — {track.get('artist')} (음량 {music_volume()})")
            except subprocess.CalledProcessError as e:      # 배경음이 실패해도 영상은 나가야 한다
                print(f"  배경음 섞기 실패 → 목소리만: {str(e)[:200]}", file=sys.stderr)
                shutil.copyfile(voice_only, final)
                track = None
        else:
            concat(parts, final)
    music_tag = f"{track['file']}@{music_volume()}" if track else ""
    record = {"cards": _sha(imgs), "script": _script_sha(lines),
              "voice": cfg["tag"] if use_tts else "", "rate": cfg["rate"],
              "style": cfg["style"] if use_tts else "",
              "music": music_tag, "music_title": (track or {}).get("title", ""),
              "music_credit": music_credit(track),
              "seconds": round(total, 1)}
    if fallback:
        record["tts_fallback"] = fallback
    meta.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"완성: {final.relative_to(ROOT)} ({total:.0f}초, {final.stat().st_size // 1024} KB)")
    return final


def build_auto(use_tts: bool = True) -> int:
    """큐의 모든 항목 중 카드는 있는데 영상이 없거나 낡은 것을 만든다 (render 뒤에 돌린다)."""
    rc = 0
    ids = sorted(p.stem for p in QUEUE.glob("*.json") if not p.name.startswith("_"))
    todo = [i for i in ids if not is_current(i)]
    if not todo:
        print("만들 영상 없음 (전부 최신)")
        return 0
    for item_id in todo:
        item = load_item(item_id) or {}
        if not item.get("images"):
            continue
        print(f"== {item_id}")
        try:
            if not build(item_id, use_tts=use_tts):
                rc = 1
        except Exception as e:                    # noqa: BLE001 — 한 건이 죽어도 다음 건은 만든다
            print(f"  실패: {str(e)[:200]}", file=sys.stderr)
            rc = 1
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="큐 id (예: 2046-energy_drink)")
    ap.add_argument("--auto", action="store_true", help="큐 전체에서 영상이 없거나 낡은 것만 만들기")
    ap.add_argument("--no-tts", action="store_true", help="음성 없이 조립만")
    ap.add_argument("--script", action="store_true", help="대본만 출력")
    ap.add_argument("--force", action="store_true", help="있어도 다시 만들기 (TTS 도 다시)")
    ap.add_argument("--no-music", action="store_true", help="배경음 없이 (SHORT_MUSIC=off 와 같음)")
    a = ap.parse_args()
    if a.no_music:
        os.environ["SHORT_MUSIC"] = "off"
    if a.script:
        item = load_item(a.id)
        if not item:
            print(f"큐/게시 기록에 {a.id} 가 없음", file=sys.stderr)
            return 1
        for i, line in enumerate(script_for(item), 1):
            print(f"{i}. {line}")
        return 0
    if not shutil.which("ffmpeg"):
        print("ffmpeg 가 없습니다", file=sys.stderr)
        return 1
    if a.auto:
        return build_auto(use_tts=not a.no_tts)
    if not a.id:
        ap.error("--id 또는 --auto 가 필요합니다")
    return 0 if build(a.id, use_tts=not a.no_tts, force=a.force) else 1


if __name__ == "__main__":
    sys.exit(main())
