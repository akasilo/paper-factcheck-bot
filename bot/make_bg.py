"""카드 배경용 AI 이미지 — 프롬프트 만들기 + 받은 이미지를 카드 크기로 맞추기.

지금까지 배경은 card_styles.py 가 코드로 그린 격자·색번짐 그래픽이었다. 이 모듈은 글마다
**주제에 맞는 사진풍 배경 한 장**을 images/<id>/ai_bg.jpg 로 두고, 렌더러가 훅·본문 카드
전부에 그 한 장을 깐다 (본문은 card.html 의 어두운 오버레이가 글자를 살린다).

기본 흐름 (Gemini 앱 구독 — API 비용 없음):
  1. `python bot/make_bg.py --id <큐id> --dry` 로 프롬프트를 뽑는다.
     원고를 쓴 LLM 이 `image_prompt`(영어, 피사체만) 를 같이 써 두고, 여기서 스타일
     (geo/paper/soft)에 맞는 분위기·구도·금지사항을 덧붙인다. 옛 원고(image_prompt 없음)는
     주제 id(energy_drink → energy drink) 로 피사체를 만든다.
  2. Gemini 앱(gemini.google.com, Flash-Lite)에 그 프롬프트를 넣어 이미지를 만들고 "원본 크기
     다운로드" 한다.
  3. 받은 PNG 를 images/<id>/ 에 ai_bg.png (또는 ai_bg.jpg) 로 올린다. 렌더러(ensure)가
     1080x1350 JPEG 로 잘라 ai_bg.jpg 를 만들고 그 위에 카드를 그린다.
     로컬에서 미리 맞추려면: `python bot/make_bg.py --id <큐id> --fit 받은파일.png`

선택 (유료): GEMINI_API_KEY 가 있으면 파일이 없을 때 Gemini 이미지 API 로 직접 만든다.
  GEMINI_IMAGE_MODEL 기본 gemini-2.5-flash-image. 키가 없으면 조용히 건너뛴다.

어느 쪽이든 배경을 못 구하면 None 을 돌려주고 렌더러는 예전 그래픽 배경으로 그린다.
게시가 배경 때문에 막히는 일은 없다.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "queue"
IMAGES = ROOT / "images"
W, H = 1080, 1350
DEFAULT_MODEL = "gemini-2.5-flash-image"
FILENAME = "ai_bg.jpg"

# 스타일별 분위기 — card_styles 의 세 가지와 짝을 맞춘다
MOOD = {
    "geo":   "dark moody studio scene on a near-black background, dramatic rim lighting with a hint of {accent}, "
             "cinematic, high contrast, subtle haze",
    "paper": "bright and clean, soft daylight on a pale paper or linen surface, minimal, calm, "
             "plenty of negative space, a faint touch of {accent}",
    "soft":  "soft pastel tones with a hint of {accent}, gentle diffused light, dreamy shallow depth of field, "
             "warm and reassuring",
}
ACCENT_WORD = {"yellow": "warm amber", "blue": "cool blue", "green": "fresh green",
               "orange": "warm orange", "violet": "soft violet", "red": "deep red"}

TEMPLATE = (
    "{subject}. Editorial product photograph for a magazine cover. {mood}. "
    "Composition: the subject sits in the upper half of the frame; the lower half is plain, "
    "uncluttered and darker, left empty for a text overlay. Vertical 4:5 portrait. "
    "Strictly no text, no letters, no numbers, no logos, no labels, no brand marks, "
    "no watermark, no people, no hands. Unbranded generic packaging only."
)


def subject_of(item: dict) -> str:
    s = (item.get("image_prompt") or "").strip()
    if s:
        return s.rstrip(".")
    topic = re.sub(r"[_\-]+", " ", str(item.get("topic", "")).strip()) or "an everyday household product"
    return f"a single generic unbranded {topic}, centered"


def prompt_for(item: dict) -> str:
    style = str(item.get("style") or "geo")
    accent = ACCENT_WORD.get(str(item.get("accent") or ""), "warm amber")
    mood = MOOD.get(style, MOOD["geo"]).format(accent=accent)
    return TEMPLATE.format(subject=subject_of(item), mood=mood)


def _call_gemini(prompt: str, key: str, model: str) -> bytes:
    """이미지 바이트를 돌려준다. 실패하면 RuntimeError."""
    import requests
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "3:4"}},
    }
    r = requests.post(url, params={"key": key}, json=body, timeout=120)
    if r.status_code == 400 and "imageConfig" in r.text:
        # 이 모델은 비율 지정을 모른다 → 빼고 다시 (아래서 잘라 맞춘다)
        del body["generationConfig"]["imageConfig"]
        r = requests.post(url, params={"key": key}, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    for cand in data.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                return base64.b64decode(blob["data"])
    raise RuntimeError(f"응답에 이미지가 없음: {json.dumps(data)[:300]}")


TRIM_BOTTOM = 0.13     # Gemini 앱이 찍는 워터마크(✦, 높이 87~92% 지점·오른쪽)를 잘라내는 비율


def _fit(raw: bytes) -> bytes:
    """어떤 비율로 오든 1080x1350 으로 덮어 자른다. JPEG 로 저장."""
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    if TRIM_BOTTOM:
        im = im.crop((0, 0, im.width, round(im.height * (1 - TRIM_BOTTOM))))
    sw, sh = im.size
    scale = max(W / sw, H / sh)
    im = im.resize((round(sw * scale), round(sh * scale)), Image.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    im = im.crop((x, y, x + W, y + H))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


# 손으로 올린 원본 후보 — 이 이름으로 PNG/JPG 를 두면 ensure 가 ai_bg.jpg 로 잘라 준다
HAND_NAMES = ("ai_bg.png", "ai_bg.jpeg", "ai_bg_raw.png", "ai_bg_raw.jpg", "image.png")


def _from_hand(out_dir: Path, out: Path) -> Path | None:
    """images/<id>/ 에 사람이 넣어 둔 원본이 있으면 카드 크기로 맞춰 ai_bg.jpg 를 만든다."""
    for name in HAND_NAMES:
        src = out_dir / name
        if src.exists():
            try:
                out.write_bytes(_fit(src.read_bytes()))
                print(f"  AI 배경: {name} → {FILENAME} (1080x1350)")
                return out
            except Exception as e:               # noqa: BLE001
                print(f"  AI 배경: {name} 변환 실패 → {str(e)[:200]}", file=sys.stderr)
    return None


def ensure(item: dict, out_dir: Path, force: bool = False) -> Path | None:
    """images/<id>/ai_bg.jpg 를 보장한다. 못 만들면 None (렌더러는 그래픽 배경으로 간다)."""
    out = out_dir / FILENAME
    if out.exists() and not force:
        return out
    hand = _from_hand(out_dir, out)
    if hand:
        return hand
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("  AI 배경: 올려둔 이미지도 GEMINI_API_KEY 도 없음 → 그래픽 배경 사용")
        return None
    model = os.environ.get("GEMINI_IMAGE_MODEL", "").strip() or DEFAULT_MODEL
    prompt = prompt_for(item)
    try:
        raw = _call_gemini(prompt, key, model)
        out_dir.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_fit(raw))
        try:
            shown = out.relative_to(ROOT)
        except ValueError:
            shown = out
        print(f"  AI 배경 생성: {shown} ({model})")
        return out
    except Exception as e:                       # noqa: BLE001 — 배경 실패가 게시를 막으면 안 된다
        print(f"  AI 배경 실패 ({model}) → 그래픽 배경 사용: {str(e)[:200]}", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="큐 id (예: 2046-energy_drink)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry", action="store_true", help="프롬프트만 보여주고 만들지 않음")
    ap.add_argument("--fit", metavar="FILE", help="받아 둔 이미지를 images/<id>/ai_bg.jpg 로 잘라 저장")
    a = ap.parse_args()
    if a.fit:
        out = IMAGES / a.id / FILENAME
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_fit(Path(a.fit).read_bytes()))
        print(f"저장: {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")
        return 0
    item = json.loads((QUEUE / f"{a.id}.json").read_text(encoding="utf-8"))
    print("프롬프트:", prompt_for(item))
    if a.dry:
        return 0
    return 0 if ensure(item, IMAGES / a.id, force=a.force) else 1


if __name__ == "__main__":
    sys.exit(main())
