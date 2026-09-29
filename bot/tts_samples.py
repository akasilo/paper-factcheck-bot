"""Gemini TTS 목소리 비교용 샘플 만들기 — 같은 문장을 여러 목소리로 읽혀 assets/voice_samples/<voice>.mp3 로 저장.

  python bot/tts_samples.py --voices Kore,Leda,Zephyr --text "안녕하세요 ..."      # 지정한 목소리만
  python bot/tts_samples.py --female                                                # 여성 14개
  python bot/tts_samples.py --male                                                  # 남성 16개
  python bot/tts_samples.py --all                                                   # 30개 전부

환경변수는 make_short.py 와 같다 (GEMINI_API_KEY 필수, GEMINI_TTS_MODEL / GEMINI_TTS_STYLE 선택).
결과 폴더의 samples.json 에 문장·목소리·길이를 적는다. 이미 같은 문장·말투로 만든 파일은 건너뛴다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_short as ms  # noqa: E402

OUT = ms.ROOT / "assets" / "voice_samples"

# 구글 문서(Gemini-TTS voice options)의 성별 분류
FEMALE = ["Achernar", "Aoede", "Autonoe", "Callirrhoe", "Despina", "Erinome", "Gacrux", "Kore",
          "Laomedeia", "Leda", "Pulcherrima", "Sulafat", "Vindemiatrix", "Zephyr"]
MALE = ["Achird", "Algenib", "Algieba", "Alnilam", "Charon", "Enceladus", "Fenrir", "Iapetus", "Orus",
        "Puck", "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar", "Umbriel", "Zubenelgenubi"]

DEFAULT_TEXT = ("안녕하세요, 논문 팩트체크입니다. 혈압이 정상인데 저염 소금으로 바꾸면 뭐가 달라질까요? "
                "한 논문에 따르면, 효과는 생각보다 작았습니다.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", default="", help="쉼표로 구분한 목소리 이름")
    ap.add_argument("--female", action="store_true")
    ap.add_argument("--male", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--text", default=DEFAULT_TEXT)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    voices: list[str] = [v.strip() for v in a.voices.split(",") if v.strip()]
    if a.female or a.all:
        voices += FEMALE
    if a.male or a.all:
        voices += MALE
    voices = list(dict.fromkeys(voices))
    if not voices:
        print("목소리를 지정하세요 (--voices / --female / --male / --all)", file=sys.stderr)
        return 1

    base = ms.tts_config("gemini")
    if base["provider"] != "gemini":
        print("GEMINI_API_KEY 가 없습니다", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    meta_path = OUT / "samples.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    samples = meta.get("samples") or {}
    if meta.get("text") != a.text or meta.get("style") != base["style"] or meta.get("model") != base["model"]:
        samples = {}                                    # 문장·말투·모델이 바뀌면 전부 새로
    rc = 0
    for v in voices:
        out = OUT / f"{v}.mp3"
        if not a.force and out.exists() and v in samples:
            print(f"  {v}: 있음")
            continue
        cfg = {**base, "voice": v, "tag": f"gemini:{base['model']}:{v}"}
        try:
            ms.tts_gemini(a.text, out, cfg)
            sec = round(ms.duration_of(out), 1)
            samples[v] = {"file": out.name, "seconds": sec,
                          "gender": "female" if v in FEMALE else "male" if v in MALE else ""}
            print(f"  {v}: {sec}초")
        except Exception as e:                           # noqa: BLE001 — 한 목소리가 죽어도 나머지는 만든다
            print(f"  {v}: 실패 — {str(e)[:200]}", file=sys.stderr)
            rc = 1
        meta_path.write_text(json.dumps({"text": a.text, "style": base["style"], "model": base["model"],
                                         "samples": samples}, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"완료: {len(samples)}개 → {OUT.relative_to(ms.ROOT)}/")
    return rc


if __name__ == "__main__":
    sys.exit(main())
