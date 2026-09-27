"""
"링크 모음" 페이지 만들기 — posted/*.json → docs/index.html (GitHub Pages).

인스타 캡션에는 링크가 안 걸리므로, 프로필 링크 한 곳에 "최근 글 + 쿠팡 링크" 를 최신순으로 모아 둔다.
누가 DM 으로 "어디서 사요?" 라고 물으면 인스타 FAQ 자동답장이 이 주소를 알려준다.
daily-post 워크플로가 Publish 뒤에 한 번 돌려서 docs/ 를 함께 커밋한다 (표준 라이브러리만 씀).

    python bot/build_links_page.py               # docs/index.html 갱신
    python bot/build_links_page.py --limit 40    # 최근 40개만

환경변수
  IMAGE_BASE_URL   썸네일 기준 URL (워크플로가 넣어 준다). 없으면 raw.githubusercontent.com 기본값.
  LINKS_SITE_NAME  페이지 제목 (기본 "paper_factcheck")
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTED = ROOT / "posted"
DOCS = ROOT / "docs"

DEFAULT_BASE = "https://raw.githubusercontent.com/akasilo/paper-factcheck-bot/main/"
DISCLOSURE = "이 페이지의 쿠팡 링크는 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
AI_NOTE = "카드뉴스·영상은 AI의 도움을 받아 제작되었습니다."
INSTAGRAM = "https://www.instagram.com/paper_factcheck/"
THREADS = "https://www.threads.com/@paper_factcheck"
PRIVACY = "https://github.com/akasilo/paper-factcheck-bot/blob/main/PRIVACY.md"

VERDICT_LABEL = {
    "good": ("근거 있음", "good"),
    "bad": ("근거 부족", "bad"),
    "mixed": ("조건부", "mixed"),
    "caution": ("주의", "bad"),
}


def strip_markup(text: str) -> str:
    t = re.sub(r"\[\[(.*?)\]\]", r"\1", text or "")
    t = re.sub(r"\{\{(.*?)\}\}", r"\1", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def hook_of(item: dict) -> str:
    for c in item.get("cards") or []:
        if c.get("type") == "hook" and c.get("text"):
            return strip_markup(str(c["text"]))
    return strip_markup((item.get("instagram_caption") or "").split("\n")[0])


def load_posts() -> list[dict]:
    """올라간 글만, 최신순. 같은 id 가 여러 기록에 걸쳐 있으면 하나로 합친다."""
    by_id: dict[str, dict] = {}
    for p in sorted(POSTED.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        q = rec.get("queue") or {}
        iid = str(q.get("id") or "")
        if not iid or not (rec.get("instagram_post_id") or rec.get("threads_post_id")):
            continue
        cur = by_id.setdefault(iid, {"queue": q})
        for k in ("instagram_posted_at", "threads_posted_at", "threads_permalink",
                  "youtube_url", "completed_at", "date"):
            if rec.get(k) and not cur.get(k):
                cur[k] = rec[k]
        if q.get("link") and not (cur["queue"].get("link")):
            cur["queue"]["link"] = q["link"]
    posts = list(by_id.values())

    def when(r: dict) -> str:
        return str(r.get("instagram_posted_at") or r.get("threads_posted_at")
                   or r.get("completed_at") or r.get("date") or "")
    posts.sort(key=when, reverse=True)
    return posts


def fmt_date(s: str) -> str:
    try:
        return datetime.fromisoformat(s[:19]).strftime("%Y.%m.%d")
    except Exception:
        return (s or "")[:10].replace("-", ".")


def card_html(rec: dict, base: str) -> str:
    q = rec["queue"]
    iid = str(q.get("id", ""))
    imgs = q.get("images") or []
    thumb = (base.rstrip("/") + "/" + str(imgs[0]).lstrip("/")) if imgs else ""
    hook = html.escape(hook_of(q) or q.get("topic_ko") or iid)
    answer = html.escape(strip_markup(str(q.get("answer") or "")))
    link = (q.get("link") or "").strip()
    th = (rec.get("threads_permalink") or "").strip()
    yt = (rec.get("youtube_url") or "").strip()
    date = fmt_date(str(rec.get("instagram_posted_at") or rec.get("threads_posted_at")
                        or rec.get("completed_at") or rec.get("date") or ""))
    label, cls = VERDICT_LABEL.get(str(q.get("verdict") or ""), ("", ""))
    topic = html.escape(str(q.get("topic_ko") or ""))

    btns = []
    if link:
        btns.append(f'<a class="btn primary" href="{html.escape(link)}" target="_blank" rel="noopener sponsored">쿠팡에서 보기</a>')
    if th:
        btns.append(f'<a class="btn" href="{html.escape(th)}" target="_blank" rel="noopener">Threads</a>')
    if yt:
        btns.append(f'<a class="btn" href="{html.escape(yt)}" target="_blank" rel="noopener">YouTube</a>')

    badge = f'<span class="badge {cls}">{html.escape(label)}</span>' if label else ""
    img = f'<img src="{html.escape(thumb)}" alt="" loading="lazy">' if thumb else '<div class="noimg"></div>'
    return f"""<article class="card" id="{html.escape(iid)}">
  <div class="thumb">{img}</div>
  <div class="body">
    <div class="meta"><span class="topic">{topic}</span>{badge}<span class="date">{date}</span></div>
    <h2>{hook}</h2>
    {f'<p class="answer">{answer}</p>' if answer else ''}
    <div class="btns">{''.join(btns) or '<span class="nolink">제품 링크 없음</span>'}</div>
  </div>
</article>"""


PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{site} — 제품 링크 모음</title>
<meta name="description" content="논문으로 팩트체크한 일상 제품들. 각 글에서 말한 조건에 맞는 제품 링크를 최신순으로 모았어요.">
<meta name="robots" content="index,follow">
<style>
  :root {{
    --bg:#f6f5f2; --card:#ffffff; --ink:#1c1b18; --muted:#6b6862; --line:#e6e3dc;
    --accent:#1f5eff; --accent-ink:#ffffff; --good:#1b7f4b; --bad:#b3261e; --mixed:#8a5a00;
    --good-bg:#e4f5ea; --bad-bg:#fbe7e5; --mixed-bg:#fff1d6;
  }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
    --bg:#121210; --card:#1c1b18; --ink:#f2efe8; --muted:#a29e95; --line:#2c2a25;
    --accent:#6f95ff; --accent-ink:#0b1430; --good-bg:#12331f; --bad-bg:#3a1512; --mixed-bg:#3a2a08;
    --good:#7fd6a2; --bad:#ff9d94; --mixed:#ffc96b;
  }} }}
  * {{ box-sizing:border-box }}
  html,body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard","Noto Sans KR","Malgun Gothic",sans-serif;
    -webkit-font-smoothing:antialiased; }}
  main {{ max-width:640px; margin:0 auto; padding:24px 16px 48px; }}
  header {{ margin-bottom:20px }}
  header h1 {{ font-size:22px; margin:0 0 6px; letter-spacing:-.01em }}
  header p {{ margin:0; color:var(--muted); font-size:14px; line-height:1.5 }}
  header .links {{ margin-top:10px; display:flex; gap:8px; flex-wrap:wrap }}
  header .links a {{ font-size:13px; color:var(--accent); text-decoration:none; border:1px solid var(--line); border-radius:999px; padding:5px 11px; background:var(--card) }}
  .card {{ display:grid; grid-template-columns:96px 1fr; gap:14px; background:var(--card); border:1px solid var(--line);
    border-radius:14px; padding:12px; margin-bottom:12px; }}
  .thumb img, .noimg {{ width:96px; height:120px; object-fit:cover; border-radius:8px; background:var(--line); display:block }}
  .body {{ min-width:0 }}
  .meta {{ display:flex; gap:8px; align-items:center; font-size:12px; color:var(--muted); margin-bottom:6px; flex-wrap:wrap }}
  .topic {{ font-weight:600; color:var(--ink) }}
  .date {{ margin-left:auto }}
  .badge {{ font-size:11px; padding:2px 7px; border-radius:999px; font-weight:600 }}
  .badge.good {{ background:var(--good-bg); color:var(--good) }}
  .badge.bad {{ background:var(--bad-bg); color:var(--bad) }}
  .badge.mixed {{ background:var(--mixed-bg); color:var(--mixed) }}
  h2 {{ font-size:15px; line-height:1.4; margin:0 0 6px; letter-spacing:-.01em; word-break:keep-all }}
  .answer {{ font-size:13px; line-height:1.5; color:var(--muted); margin:0 0 10px;
    display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; word-break:keep-all }}
  .btns {{ display:flex; gap:6px; flex-wrap:wrap }}
  .btn {{ font-size:13px; text-decoration:none; padding:7px 12px; border-radius:9px; border:1px solid var(--line); color:var(--ink); background:transparent }}
  .btn.primary {{ background:var(--accent); color:var(--accent-ink); border-color:var(--accent); font-weight:600 }}
  .nolink {{ font-size:12px; color:var(--muted) }}
  footer {{ margin-top:28px; font-size:12px; color:var(--muted); line-height:1.6 }}
  footer a {{ color:var(--muted) }}
  @media (max-width:380px) {{ .card {{ grid-template-columns:80px 1fr }} .thumb img,.noimg {{ width:80px; height:100px }} }}
</style>
</head>
<body>
<main>
  <header>
    <h1>📄 {site} 제품 링크 모음</h1>
    <p>논문으로 팩트체크한 일상 제품들. 각 글에서 말한 조건에 맞는 제품 예시를 최신순으로 모았어요.</p>
    <div class="links"><a href="{ig}" target="_blank" rel="noopener">Instagram</a><a href="{th}" target="_blank" rel="noopener">Threads</a></div>
  </header>
  {cards}
  <footer>
    <p>{disclosure}</p>
    <p>{ai_note} · 마지막 갱신 {updated} · <a href="{privacy}" target="_blank" rel="noopener">개인정보처리방침</a></p>
  </footer>
</main>
</body>
</html>
"""


def build(limit: int) -> Path:
    base = os.environ.get("IMAGE_BASE_URL", "").strip() or DEFAULT_BASE
    site = os.environ.get("LINKS_SITE_NAME", "").strip() or "paper_factcheck"
    posts = load_posts()[:limit]
    cards = "\n".join(card_html(r, base) for r in posts) or '<p class="nolink">아직 올라간 글이 없어요.</p>'
    out = PAGE.format(site=html.escape(site), cards=cards, disclosure=html.escape(DISCLOSURE),
                      ai_note=html.escape(AI_NOTE), updated=datetime.now().strftime("%Y.%m.%d"),
                      ig=INSTAGRAM, th=THREADS, privacy=PRIVACY)
    DOCS.mkdir(exist_ok=True)
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    dst = DOCS / "index.html"
    dst.write_text(out, encoding="utf-8")
    print(f"docs/index.html: {len(posts)}개 글, {len(out)} bytes")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60, help="최근 몇 개까지 (기본 60)")
    args = ap.parse_args()
    build(args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
