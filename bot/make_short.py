"""카드뉴스 → 세로 숏폼(1080x1920) 영상. 카드를 한 장씩 넘기며 TTS 가 읽어 준다.

  python bot/make_short.py --id 2046-energy_drink            # images/<id>/short.mp4
  python bot/make_short.py --id 2046-energy_drink --no-tts   # 음성 없이(무음, 카드당 4초) 조립만 — 로컬 시험용
  python bot/make_short.py --id 2046-energy_drink --script   # 읽을 대본만 출력
  python bot/make_short.py --auto                            # 큐 전체: 영상이 없거나 카드·대본이 바뀐 것만 (render 뒤 자동)

소리: Google Cloud Text-to-Speech (무료 등급: WaveNet/Neural2 월 100만 자).
  GOOGLE_TTS_API_KEY   필수 (없으면 --no-tts 처럼 무음으로 만든다)
  GOOGLE_TTS_VOICE     기본 ko-KR-Neural2-A  (여성 / -B 여성 / -C 남성, ko-KR-Wavenet-A~D 도 가능)
  GOOGLE_TTS_RATE      기본 1.15 (말 빠르기. 한국어는 1.2 까지 자연스럽다)

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


def _spoken_numbers(s: str) -> str:
    """TTS 가 잘못 읽기 쉬운 표기만 손본다."""
    s = s.replace("mg/dL", "밀리그램 퍼 데시리터").replace("mmHg", "밀리미터 수은주")
    s = s.replace("·", ", ")
    s = re.sub(r"(\d)~(\d)", r"\1에서 \2", s)
    s = s.replace("%", "퍼센트")
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
            lines.append(f"이 내용은 {src}을 근거로 정리했어요. 자세한 출처는 화면을 확인해 주세요.")
            continue
        text = _plain(card.get("text") or "")
        note = _soften(_plain(card.get("note") or "").lstrip("※ ").strip())
        if note:
            text = f"{text} 참고로, {note}"
        if text:
            lines.append(_spoken_numbers(text))
    return lines


# ---------- 소리 ----------

def tts(text: str, out: Path, key: str, voice: str, rate: str) -> None:
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
    want_voice = os.environ.get("GOOGLE_TTS_VOICE", "").strip() or DEFAULT_VOICE
    want_rate = os.environ.get("GOOGLE_TTS_RATE", "").strip() or DEFAULT_RATE
    return (m.get("cards") == _sha(imgs)
            and m.get("script") == _script_sha(script_for(item))
            and bool(m.get("voice"))
            # 목소리·속도를 바꾸면 다시 만든다 (옛 기록엔 rate 가 없으니 있을 때만 비교)
            and m.get("voice") == want_voice
            and str(m.get("rate", want_rate)) == str(want_rate))


def build(item_id: str, use_tts: bool = True, force: bool = False) -> Path | None:
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

    key = os.environ.get("GOOGLE_TTS_API_KEY", "").strip()
    voice = os.environ.get("GOOGLE_TTS_VOICE", "").strip() or DEFAULT_VOICE
    rate = os.environ.get("GOOGLE_TTS_RATE", "").strip() or DEFAULT_RATE
    if use_tts and not key:
        print("GOOGLE_TTS_API_KEY 없음 → 무음으로 만듭니다", file=sys.stderr)
        use_tts = False

    tts_dir = out_dir / "tts"
    if use_tts:
        tts_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        parts: list[Path] = []
        total = 0.0
        for i, (img, line) in enumerate(zip(imgs, lines), 1):
            audio = None
            if use_tts:
                audio = tts_dir / f"{i:02d}.mp3"
                stamp = tts_dir / f"{i:02d}.txt"        # 이 mp3 가 어떤 문장·목소리로 만든 것인지
                want = f"{voice}|{rate}|{line}"
                have = stamp.read_text(encoding="utf-8") if stamp.exists() else ""
                if not audio.exists() or have != want:
                    tts(line, audio, key, voice, rate)
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
        concat(parts, final)
    meta.write_text(json.dumps({"cards": _sha(imgs), "script": _script_sha(lines),
                                "voice": voice if use_tts else "", "rate": rate,
                                "seconds": round(total, 1)},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    a = ap.parse_args()
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
