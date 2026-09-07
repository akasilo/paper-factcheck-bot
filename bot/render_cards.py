"""
카드뉴스 렌더러 — 큐 JSON 의 `cards` 를 1080x1350 JPEG 로 만든다.

카드 형식 (queue/YYYY-MM-DD.json 안의 "cards" 배열)
  {"type": "hook",   "text": "매일 바르는\\n[[선크림]]\\n혹시 {{'이 성분'}}\\n확인하셨나요?", "bg": "images/2026-09-07/bg1.jpg", "kicker": "논문 팩트체크"}
  {"type": "body",   "text": "본문 ... [[노란 강조]] ... {{빨간 강조}}", "bg": "...", "note": "작은 주석(선택)"}
  {"type": "source", "paper": {"title": "...", "journal": "...", "year": "2025", "doi": "10.xxxx/..."}, "cta": "...(선택)"}

  - [[텍스트]] → 노란 형광 박스,  {{텍스트}} → 빨간 글자,  \\n → 줄바꿈
  - bg 가 없거나 파일이 없으면 자동으로 은은한 배경을 만들어 images/<date>/bg<n>.jpg 로 저장한다.

실행
  python bot/render_cards.py --date 2026-09-07        # queue/2026-09-07.json 의 cards 렌더 → images/2026-09-07/NN.jpg, queue 의 images 갱신
  python bot/render_cards.py --demo                   # 샘플 카드 7장 → images/demo/
"""
from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
RENDER_DIR = ROOT / "render"
TEMPLATE = RENDER_DIR / "card.html"
FONT = RENDER_DIR / "fonts" / "PretendardVariable.woff2"
W, H = 1080, 1350

DEFAULT_CTA = "매일 아침 8시,\n논문으로 팩트체크 → 팔로우"
DEFAULT_DISCLAIMER = "이 콘텐츠는 논문 내용을 요약한 것으로 개인의 진단·치료를 대체하지 않습니다."
BRAND = "@paper_factcheck"


# ---------------------------------------------------------------------------
# 텍스트 마크업 → HTML
# ---------------------------------------------------------------------------

def markup(text: str) -> str:
    """[[..]] {{..}} \\n 을 HTML 로 변환 (그 외는 전부 escape)."""
    out = html.escape(text or "")
    out = re.sub(r"\[\[(.+?)\]\]", r'<span class="hl">\1</span>', out, flags=re.S)
    out = re.sub(r"\{\{(.+?)\}\}", r'<span class="red">\1</span>', out, flags=re.S)
    return out.replace("\\n", "<br>").replace("\n", "<br>")


# ---------------------------------------------------------------------------
# 배경 자동 생성 (사진이 없을 때)
# ---------------------------------------------------------------------------

PALETTES = [
    [(18, 34, 74), (36, 84, 160), (80, 150, 230)],      # 네이비-블루
    [(60, 18, 52), (140, 40, 110), (230, 110, 150)],    # 와인-핑크
    [(14, 52, 44), (34, 120, 96), (110, 200, 160)],     # 딥그린
    [(70, 40, 10), (170, 105, 40), (240, 185, 90)],     # 앰버
    [(30, 30, 40), (80, 80, 110), (150, 150, 190)],     # 그레이-라벤더
    [(50, 20, 20), (150, 50, 40), (230, 120, 90)],      # 테라코타
]


def make_background(path: Path, seed: int) -> None:
    rnd = random.Random(seed)
    pal = PALETTES[seed % len(PALETTES)]
    img = Image.new("RGB", (W, H), pal[0])
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(12):
        r = rnd.randint(260, 720)
        x = rnd.randint(-r // 2, W - r // 2)
        y = rnd.randint(-r // 2, H - r // 2)
        c = rnd.choice(pal[1:])
        d.ellipse([x, y, x + r, y + r], fill=(*c, rnd.randint(150, 235)))
    layer = layer.filter(ImageFilter.GaussianBlur(95))
    img.paste(layer, (0, 0), layer)
    # 살짝 어둡게 + 비네팅
    vign = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vign)
    vd.ellipse([-W * 0.3, -H * 0.2, W * 1.3, H * 1.2], fill=255)
    vign = vign.filter(ImageFilter.GaussianBlur(200))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(img, dark, vign.point(lambda v: 150 + v * 105 // 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=88)


# ---------------------------------------------------------------------------
# 카드 HTML 조립
# ---------------------------------------------------------------------------

def build_card_html(card: dict, index: int, total: int, bg_path: Path | None) -> str:
    ctype = card.get("type", "body")
    tpl = TEMPLATE.read_text(encoding="utf-8")

    if bg_path and bg_path.exists():
        bg_css = f'url("{bg_path.resolve().as_uri()}")'
        bg_class = "zoom"
    else:
        bg_css = "linear-gradient(160deg, #1a2238 0%, #0b0c10 100%)"
        bg_class = ""

    brand = f'<div class="brand">{html.escape(BRAND)}</div>'
    page = f'<div class="page">{index} / {total}</div>'

    if ctype == "hook":
        kicker = card.get("kicker")
        body = '<div class="content">'
        if kicker:
            body += f'<div class="kicker">{html.escape(kicker)}</div>'
        body += f'<div class="title">{markup(card.get("text", ""))}</div>'
        body += "</div>"
        body += brand + '<div class="swipe">넘겨보기</div>'
    elif ctype == "source":
        paper = card.get("paper", {}) or {}
        heading = card.get("heading", "이 이야기의 출처")
        meta_bits = [b for b in [paper.get("journal"), paper.get("year")] if b]
        meta = " · ".join(str(b) for b in meta_bits)
        doi = paper.get("doi")
        body = '<div class="content">'
        body += '<div class="src-head">'
        body += f'<div class="src-title">{markup(heading)}</div>'
        body += '<div class="src-box"><div class="src-label">논문</div>'
        if paper.get("title"):
            body += f'<div class="src-paper">{html.escape(paper["title"])}</div>'
        if meta:
            body += f'<div class="src-meta">{html.escape(meta)}</div>'
        if doi:
            body += f'<div class="src-meta">DOI {html.escape(str(doi))}</div>'
        if paper.get("authors"):
            body += f'<div class="src-meta">{html.escape(paper["authors"])}</div>'
        body += "</div>"
        body += f'<div class="src-cta">{markup(card.get("cta", DEFAULT_CTA))}</div>'
        body += "</div>"
        body += f'<div class="src-disclaimer">{html.escape(card.get("disclaimer", DEFAULT_DISCLAIMER))}</div>'
        body += "</div>"
        body += brand + page
    else:  # body
        body = '<div class="content">'
        body += f'<div class="text">{markup(card.get("text", ""))}</div>'
        if card.get("note"):
            body += f'<div class="note">{markup(card["note"])}</div>'
        body += "</div>"
        body += brand + page

    return (tpl.replace("%%FONT%%", FONT.resolve().as_uri())
               .replace("%%BG%%", bg_css)
               .replace("%%BGCLASS%%", bg_class)
               .replace("%%CLASS%%", ctype)
               .replace("%%BODY%%", body))


# ---------------------------------------------------------------------------
# 렌더
# ---------------------------------------------------------------------------

def render_cards(cards: list[dict], out_dir: Path, seed: int = 0) -> list[Path]:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(cards)
    outputs: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for i, card in enumerate(cards, 1):
            # 배경 결정
            bg_path: Path | None = None
            if card.get("bg"):
                cand = ROOT / card["bg"]
                if cand.exists():
                    bg_path = cand
            if bg_path is None and card.get("type", "body") != "source":
                bg_path = out_dir / f"bg{i:02d}.jpg"
                if not bg_path.exists():
                    make_background(bg_path, seed * 100 + i)

            html_str = build_card_html(card, i, total, bg_path)
            with tempfile.NamedTemporaryFile("w", suffix=".html", dir=RENDER_DIR,
                                             delete=False, encoding="utf-8") as f:
                f.write(html_str)
                tmp = Path(f.name)
            try:
                page.goto(tmp.as_uri(), wait_until="load")
                page.wait_for_selector('body[data-ready="1"]', timeout=15000)
                page.wait_for_timeout(150)
                out = out_dir / f"{i:02d}.jpg"
                page.screenshot(path=str(out), type="jpeg", quality=92)
                outputs.append(out)
                print(f"  렌더 {i}/{total} → {out.relative_to(ROOT)}")
            finally:
                tmp.unlink(missing_ok=True)
        browser.close()
    return outputs


DEMO_CARDS = [
    {"type": "hook", "kicker": "논문 팩트체크",
     "text": "매일 바르는\n[[선크림]]\n혹시 {{'이 성분'}}\n확인하셨나요?"},
    {"type": "body",
     "text": "자외선을 막기 위해 매일 바르는 선크림.\n하지만 자외선 차단제에 흔히 쓰이는 특정 화학 성분들은 피부를 통해 흡수되어 [[혈액까지 도달]]할 수 있습니다."},
    {"type": "body",
     "text": "2025년 신경내분비학 저널 리뷰 논문에 따르면, 특히 주의해야 할 성분은 [[벤조페논-3(옥시벤존)]]과 [[벤조페논-2]]. 체내에서 {{내분비계 교란 물질}}로 작용해 정상적인 호르몬 조절을 방해합니다."},
    {"type": "body",
     "text": "여성호르몬과 유사하게 작용해 [[남성호르몬 수치를 낮추고]] 정자의 질을 떨어뜨릴 수 있으며, 여성의 난포 발달을 지연시키고 [[갑상선 호르몬]]에도 영향을 준다는 연구들이 보고되고 있습니다."},
    {"type": "body",
     "text": "이미 해외 일부 지역에서는 생태계 보호를 위해 이 성분들의 사용을 [[법으로 금지]]하고 있고, 지속 노출에 대한 안전성 우려도 계속 커지고 있습니다.",
     "note": "※ 리뷰 논문 1편의 종합 결과이며, 개인별 위험은 노출량·제품에 따라 다릅니다."},
    {"type": "body",
     "text": "선크림을 고를 때\n반드시 뒷면의\n[['전성분표']]를 확인하세요!\n\n벤조페논 계열 대신 {{징크옥사이드·티타늄디옥사이드}} 같은 무기자차 성분을 찾아보세요."},
    {"type": "source",
     "paper": {"title": "Benzophenones: How ultraviolet filters can interfere with reproduction",
               "journal": "Journal of Neuroendocrinology", "year": "2025",
               "doi": "10.1111/jne.70088", "authors": "Gomez et al."}},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="queue/YYYY-MM-DD.json 의 cards 를 렌더")
    ap.add_argument("--demo", action="store_true", help="샘플 카드 렌더 → images/demo/")
    ap.add_argument("--no-write", action="store_true", help="queue JSON 의 images 필드를 갱신하지 않음")
    ap.add_argument("--auto", action="store_true",
                    help="queue/*.json 중 cards 는 있는데 이미지가 없거나 빠진 항목을 전부 렌더")
    ap.add_argument("--force", action="store_true", help="이미지가 있어도 다시 렌더")
    args = ap.parse_args()

    if args.demo:
        outs = render_cards(DEMO_CARDS, ROOT / "images" / "demo", seed=7)
        print(f"완료: {len(outs)}장")
        return 0

    if args.auto:
        targets = []
        for qpath in sorted(p for p in (ROOT / "queue").glob("*.json")
                            if not p.name.startswith("_")):
            try:
                item = json.loads(qpath.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"건너뜀 {qpath.name}: JSON 오류 {e}")
                continue
            if not item.get("cards"):
                continue
            imgs = item.get("images") or []
            missing = (not imgs) or any(not (ROOT / p).exists() for p in imgs)
            if missing or args.force:
                targets.append(qpath.stem)
        if not targets:
            print("렌더할 항목 없음")
            return 0
        rc = 0
        for d in targets:
            print(f"== {d}")
            rc |= render_one(d, write=not args.no_write)
        return rc

    if not args.date:
        ap.error("--date, --auto, --demo 중 하나가 필요합니다")
    return render_one(args.date, write=not args.no_write)


def render_one(date: str, write: bool = True) -> int:
    qpath = ROOT / "queue" / f"{date}.json"
    if not qpath.exists():
        print(f"큐 파일 없음: {qpath}")
        return 1
    item = json.loads(qpath.read_text(encoding="utf-8"))
    cards = item.get("cards") or []
    if not 2 <= len(cards) <= 10:
        print(f"cards 는 2~10장이어야 합니다 (현재 {len(cards)}장)")
        return 1

    # 큐 이름이 날짜가 아니라 순번(0001-airfryer)이라 문자열에서 안정적인 시드를 만든다
    seed = sum((i + 1) * ord(c) for i, c in enumerate(date)) % 100000
    outs = render_cards(cards, ROOT / "images" / date, seed=seed)

    if write:
        item["images"] = [str(p.relative_to(ROOT)).replace("\\", "/") for p in outs]
        item.pop("image_urls", None)
        qpath.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"queue/{date}.json 의 images 갱신 ({len(outs)}장)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
