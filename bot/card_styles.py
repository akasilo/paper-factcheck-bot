"""카드 배경 스타일 모음.

큐 항목의 `style` / `accent` 값에 따라 배경 이미지와 테마 CSS 를 갈아끼운다.

    "style":  "geo" | "paper" | "soft"      (없으면 geo)
    "accent": "yellow" | "blue" | "red" | "green" | "orange" | "violet"  (없으면 yellow)

스타일 고르는 기준 (draft_post 프롬프트와 맞춰둘 것):
    geo   — 경고·위험·수치 폭로. 어두운 배경에 격자·원호·사선. 가장 강한 톤.
    paper — 검증·해명·안심. 밝은 종이 질감에 검은 글씨. 차분하고 신뢰감.
    soft  — 감성·생활·몸에 관한 이야기. 부드러운 색번짐. 톤 다운.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1350

# ---------------------------------------------------------------------------
# 액센트 색
# ---------------------------------------------------------------------------

ACCENTS: dict[str, dict[str, str]] = {
    # key: {hl = 형광펜/포인트, on_hl = 형광펜 위 글자색, red = 강조 글자}
    "yellow": {"hl": "#ffe500", "on_hl": "#111111", "red": "#ff3b3b", "dark_red": "#d92020"},
    "blue":   {"hl": "#5ce1ff", "on_hl": "#06202b", "red": "#ff5a5a", "dark_red": "#c81e1e"},
    "green":  {"hl": "#8dff9e", "on_hl": "#062012", "red": "#ff5a5a", "dark_red": "#c81e1e"},
    "orange": {"hl": "#ffb648", "on_hl": "#26140a", "red": "#ff4d4d", "dark_red": "#cc2222"},
    "violet": {"hl": "#c9a7ff", "on_hl": "#1a1030", "red": "#ff5c8a", "dark_red": "#c8235a"},
    "red":    {"hl": "#ff9d9d", "on_hl": "#2a0808", "red": "#ffe500", "dark_red": "#b31818"},
}

DEFAULT_ACCENT = "yellow"
DEFAULT_STYLE = "geo"


def _rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def accent_of(name: str | None) -> dict[str, str]:
    return ACCENTS.get((name or "").lower(), ACCENTS[DEFAULT_ACCENT])


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------

def _grain(img: Image.Image, amount: float, sigma: int = 20) -> Image.Image:
    noise = Image.effect_noise((W, H), sigma).convert("L")
    return Image.blend(img, Image.merge("RGB", (noise, noise, noise)), amount)


def _fade_bottom(img: Image.Image, to: tuple[int, int, int], start: float, power: float) -> Image.Image:
    """아래쪽을 to 색으로 서서히 덮는다 (글자 자리 확보)."""
    grad = Image.new("L", (1, H))
    span = max(1e-6, 1.0 - start)
    for y in range(H):
        t = max(0.0, (y / H - start) / span)
        grad.putpixel((0, y), int(255 * min(1.0, t) ** power))
    grad = grad.resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), to), img, grad)


# ---------------------------------------------------------------------------
# geo — 다크 + 기하 그래픽
# ---------------------------------------------------------------------------

def _bg_geo(path: Path, seed: int, acc: dict[str, str]) -> None:
    rnd = random.Random(seed)
    hl = _rgb(acc["hl"])
    cool = (58, 122, 255)

    img = Image.new("RGB", (W, H), (11, 14, 24))
    d = ImageDraw.Draw(img, "RGBA")

    step = rnd.choice([80, 90, 100])
    for x in range(0, W, step):
        d.line([(x, 0), (x, H)], fill=(255, 255, 255, 14), width=1)
    for y in range(0, H, step):
        d.line([(0, y), (W, y)], fill=(255, 255, 255, 14), width=1)

    # 원호 링 — 위치를 시드로 흔든다
    cx = rnd.randint(560, 900)
    cy = rnd.randint(220, 430)
    r = rnd.randint(320, 430)
    a0 = rnd.randint(160, 230)
    for i, (wd, alpha) in enumerate([(14, 210), (6, 120), (3, 70)]):
        rr = r - i * 62
        d.arc([cx - rr, cy - rr, cx + rr, cy + rr], a0, a0 + 180, fill=(*hl, alpha), width=wd)

    # 사선 블록
    y0 = rnd.randint(860, 1020)
    d.polygon([(-100, y0), (520, y0 - 340), (620, y0 - 220), (0, y0 + 120)], fill=(*cool, 60))

    for _ in range(70):
        x, y = rnd.randint(40, W - 40), rnd.randint(40, 900)
        rr = rnd.choice([3, 3, 4, 6])
        d.ellipse([x, y, x + rr, y + rr], fill=(255, 255, 255, rnd.randint(30, 110)))

    img = _fade_bottom(img, (6, 7, 12), 0.42, 1.4)
    img = _grain(img, 0.045, 22)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92)


CSS_GEO = """
  .card.hook .overlay {{ background: linear-gradient(180deg, rgba(0,0,0,.05) 0%, rgba(0,0,0,.18) 38%, rgba(0,0,0,.72) 74%, rgba(0,0,0,.92) 100%); }}
  .card.body .overlay {{ background: linear-gradient(180deg, rgba(0,0,0,.14) 0%, rgba(0,0,0,.50) 48%, rgba(0,0,0,.88) 100%); }}
  .kicker {{ color: {hl}; }} .kicker::before {{ background: {hl}; }}
  .hl {{ background: {hl}; color: {on_hl}; }}
  .red {{ color: {red}; }}
  .swipe {{ color: {hl}; }}
  .src-box {{ border-left-color: {hl}; }} .src-label {{ color: {hl}; }}
"""


# ---------------------------------------------------------------------------
# paper — 밝은 종이
# ---------------------------------------------------------------------------

def _bg_paper(path: Path, seed: int, acc: dict[str, str]) -> None:
    rnd = random.Random(seed)
    hl = _rgb(acc["hl"])

    img = Image.new("RGB", (W, H), (245, 243, 238))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.ellipse([rnd.randint(-340, -160), -300, rnd.randint(680, 860), 720], fill=(*hl, 78))
    ld.ellipse([rnd.randint(440, 620), 120, 1420, 1020], fill=(58, 122, 255, 34))
    layer = layer.filter(ImageFilter.GaussianBlur(150))
    img.paste(layer, (0, 0), layer)

    d = ImageDraw.Draw(img, "RGBA")
    for x in range(0, W, 60):
        d.line([(x, 0), (x, H)], fill=(20, 24, 34, 12), width=1)
    for y in range(0, H, 60):
        d.line([(0, y), (W, y)], fill=(20, 24, 34, 12), width=1)

    img = _grain(img, 0.05, 16)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=93)


CSS_PAPER = """
  html, body {{ background:#f5f3ee; }}
  body {{ color:#12161f; }}
  .card.hook .overlay {{ background: linear-gradient(180deg, rgba(245,243,238,0) 0%, rgba(245,243,238,.35) 55%, rgba(245,243,238,.80) 100%); }}
  .card.body .overlay {{ background: linear-gradient(180deg, rgba(245,243,238,.20) 0%, rgba(245,243,238,.62) 50%, rgba(245,243,238,.88) 100%); }}
  .card.source .overlay {{ background: linear-gradient(180deg, #f5f3ee 0%, #efece5 100%); }}
  .title, .text, .src-paper {{ text-shadow: none; }}
  .kicker {{ color:#0f62d6; }} .kicker::before {{ background:#0f62d6; }}
  .hl {{ background: {hl}; color: {on_hl}; }}
  .red {{ color: {dark_red}; }}
  .note {{ color: rgba(18,22,31,.62); }}
  .brand {{ color: rgba(18,22,31,.50); }}
  .page {{ color: rgba(18,22,31,.45); }}
  .swipe {{ color:#0f62d6; }}
  .src-box {{ border-left-color:#0f62d6; }}
  .src-label {{ color:#0f62d6; }}
  .src-meta {{ color: rgba(18,22,31,.72); }}
  .src-disclaimer {{ color: rgba(18,22,31,.48); }}
"""


# ---------------------------------------------------------------------------
# soft — 부드러운 색번짐 (기존 방식을 정돈한 것)
# ---------------------------------------------------------------------------

SOFT_PALETTES = [
    [(18, 34, 74), (36, 84, 160), (80, 150, 230)],
    [(60, 18, 52), (140, 40, 110), (230, 110, 150)],
    [(14, 52, 44), (34, 120, 96), (110, 200, 160)],
    [(70, 40, 10), (170, 105, 40), (240, 185, 90)],
    [(30, 30, 40), (80, 80, 110), (150, 150, 190)],
    [(50, 20, 20), (150, 50, 40), (230, 120, 90)],
]


def _bg_soft(path: Path, seed: int, acc: dict[str, str]) -> None:
    rnd = random.Random(seed)
    pal = SOFT_PALETTES[seed % len(SOFT_PALETTES)]
    img = Image.new("RGB", (W, H), pal[0])

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(10):
        r = rnd.randint(300, 760)
        x = rnd.randint(-r // 2, W - r // 2)
        y = rnd.randint(-r // 2, int(H * 0.7))
        d.ellipse([x, y, x + r, y + r], fill=(*rnd.choice(pal[1:]), rnd.randint(160, 240)))
    layer = layer.filter(ImageFilter.GaussianBlur(80))
    img.paste(layer, (0, 0), layer)

    # 형태가 아예 안 보이면 심심하니 얇은 선 몇 개만 얹는다
    d2 = ImageDraw.Draw(img, "RGBA")
    hl = _rgb(acc["hl"])
    for i in range(3):
        y = rnd.randint(120, 520) + i * 90
        d2.line([(rnd.randint(-100, 200), y), (W + 100, y - rnd.randint(40, 160))],
                fill=(*hl, 40), width=rnd.choice([2, 3]))

    img = _fade_bottom(img, (8, 9, 14), 0.34, 1.25)
    img = _grain(img, 0.04, 18)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=90)


CSS_SOFT = """
  .card.hook .overlay {{ background: linear-gradient(180deg, rgba(0,0,0,.08) 0%, rgba(0,0,0,.28) 40%, rgba(0,0,0,.78) 74%, rgba(0,0,0,.93) 100%); }}
  .card.body .overlay {{ background: linear-gradient(180deg, rgba(0,0,0,.24) 0%, rgba(0,0,0,.58) 46%, rgba(0,0,0,.90) 100%); }}
  .kicker {{ color: {hl}; }} .kicker::before {{ background: {hl}; }}
  .hl {{ background: {hl}; color: {on_hl}; }}
  .red {{ color: {red}; }}
  .swipe {{ color: {hl}; }}
  .src-box {{ border-left-color: {hl}; }} .src-label {{ color: {hl}; }}
"""


# ---------------------------------------------------------------------------
# 등록
# ---------------------------------------------------------------------------

STYLES = {
    "geo":   {"bg": _bg_geo,   "css": CSS_GEO,   "light": False},
    "paper": {"bg": _bg_paper, "css": CSS_PAPER, "light": True},
    "soft":  {"bg": _bg_soft,  "css": CSS_SOFT,  "light": False},
}

STYLE_NAMES = list(STYLES)


def resolve(style: str | None) -> str:
    s = (style or "").lower()
    return s if s in STYLES else DEFAULT_STYLE


def make_background(path: Path, seed: int, style: str | None = None, accent: str | None = None) -> None:
    STYLES[resolve(style)]["bg"](path, seed, accent_of(accent))


def theme_css(style: str | None = None, accent: str | None = None) -> str:
    return STYLES[resolve(style)]["css"].format(**accent_of(accent))


def is_light(style: str | None = None) -> bool:
    return bool(STYLES[resolve(style)]["light"])


# ---------------------------------------------------------------------------
# style / accent 가 없는 옛 초안용 자동 선택
# ---------------------------------------------------------------------------

# 주제 id·표시명에 이 낱말이 들어가면 해당 액센트
_ACCENT_HINTS: list[tuple[tuple[str, ...], str]] = [
    # 주의: 부분 문자열로 찾으므로 다른 낱말에 끼어드는 조각("screen" ⊂ "sunscreen")은 넣지 말 것
    (("water", "tumbler", "bottle", "sleep", "blue_light", "생수", "텀블러", "수면", "블루라이트", "화면"), "blue"),
    (("vitamin", "protein", "sweetener", "food", "supplement", "비타민", "단백질", "감미료", "식품", "보충제"), "green"),
    (("microwave", "airfryer", "fry", "cook", "heat", "전자레인지", "에어프라이어", "조리", "가열"), "orange"),
    (("collagen", "sunscreen", "skin", "cosmetic", "hormone", "콜라겐", "선크림", "피부", "화장", "호르몬"), "violet"),
]

# 몸·생활에 가까워 톤을 부드럽게 가져갈 주제
_SOFT_HINTS = ("sleep", "skin", "collagen", "blue_light", "toothbrush", "수면", "피부",
               "콜라겐", "블루라이트", "칫솔")


def auto_pick(item: dict) -> tuple[str, str]:
    """style/accent 가 없는 큐 항목에 어울리는 조합을 고른다."""
    hay = " ".join(str(item.get(k, "")) for k in ("topic", "topic_ko", "id")).lower()

    accent = DEFAULT_ACCENT
    for words, name in _ACCENT_HINTS:
        if any(w in hay for w in words):
            accent = name
            break

    verdict = str(item.get("verdict", "")).lower()
    if any(w in hay for w in _SOFT_HINTS):
        style = "soft"
    elif verdict in ("good", "mixed"):
        style = "paper"
    else:
        style = "geo"
    return style, accent
