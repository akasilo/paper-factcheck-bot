"""
YouTube Shorts 업로드용 얇은 API 래퍼 (YouTube Data API v3).

인스타 릴스·Threads 첫 장에 쓰는 images/<id>/short.mp4 (세로 1080x1920, 60~70초)를
그대로 유튜브에 올린다. 세로 3분 이하면 유튜브가 알아서 Shorts 로 분류한다.

인스타·Threads 와 다른 점
  - API 키가 아니라 OAuth. 채널 주인이 한 번 허용해서 받은 refresh token 으로
    매번 access token 을 새로 받는다 (bot/yt_auth.py 가 그 한 번을 도와준다).
  - 심사(YouTube API 컴플라이언스 audit) 전 프로젝트가 올린 영상은 유튜브가 강제로
    비공개(private) 로 둔다. 그래서 YT_PRIVACY 기본값이 private 이다.
  - 할당량: 업로드 한 번 = 1,600 단위, 하루 기본 10,000 단위 → 하루 3편이면 충분.

환경변수
  YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN   (GitHub Secrets)
  YT_PRIVACY      private(기본) | unlisted | public      (GitHub Variables — 심사 뒤 public 으로)
  YT_CATEGORY_ID  기본 28 (Science & Technology)

모든 함수는 실패 시 YouTubeApiError 를 던진다. 호출 측(publish.py)에서 잡아서 로그로 남긴다.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import requests

log = logging.getLogger("youtube_api")

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",     # 올리기
    "https://www.googleapis.com/auth/youtube.readonly",   # 이미 올라갔는지 확인(중복 방지)
]

TITLE_MAX = 100          # 유튜브 제목 한도
DESC_MAX = 5000          # 유튜브 설명 한도
DEFAULT_CATEGORY = "28"  # Science & Technology
DISCLOSURE = "이 영상은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
AI_NOTE = "* 본 영상은 AI의 도움을 받아 제작되었습니다."
ROOT = Path(__file__).resolve().parent.parent


class YouTubeApiError(RuntimeError):
    pass


def _req(method: str, url: str, **kw) -> requests.Response:
    """requests 호출. 네트워크 오류(타임아웃·연결 끊김)도 YouTubeApiError 로 바꿔서
    publish.py 가 '유튜브만 실패'로 기록하고 다음 실행에 재시도하게 한다."""
    try:
        return requests.request(method, url, **kw)
    except requests.RequestException as e:
        raise YouTubeApiError(f"{method} {url.split('?')[0]} 네트워크 오류: {e.__class__.__name__}: {e}") from e


# ---------------------------------------------------------------------------
# 토큰
# ---------------------------------------------------------------------------

def configured() -> bool:
    """세 가지 시크릿이 다 있어야 유튜브 단계를 켠다. 없으면 publish.py 가 조용히 건너뛴다."""
    return all(os.environ.get(k, "").strip()
               for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def access_token(client_id: str = "", client_secret: str = "", refresh_token: str = "",
                 *, timeout: int = 30) -> str:
    """refresh token 으로 1시간짜리 access token 을 받는다. 인자를 비우면 환경변수를 쓴다."""
    client_id = client_id or os.environ.get("YT_CLIENT_ID", "").strip()
    client_secret = client_secret or os.environ.get("YT_CLIENT_SECRET", "").strip()
    refresh_token = refresh_token or os.environ.get("YT_REFRESH_TOKEN", "").strip()
    if not (client_id and client_secret and refresh_token):
        raise YouTubeApiError("YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN 이 비어 있습니다")
    r = _req("POST", TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text}
    if r.status_code >= 400 or "access_token" not in body:
        # invalid_grant = refresh token 이 만료·철회됨. OAuth 앱이 '테스트' 상태면 7일마다 만료된다 →
        # Cloud Console 에서 앱을 '프로덕션'으로 게시하고 yt_auth.py 로 토큰을 다시 받을 것.
        raise YouTubeApiError(f"토큰 갱신 실패 {r.status_code}: {body}")
    return str(body["access_token"])


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _check(r: requests.Response, what: str) -> dict:
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text}
    if r.status_code >= 400 or "error" in body:
        raise YouTubeApiError(f"{what} -> {r.status_code}: {body}")
    return body


# ---------------------------------------------------------------------------
# 제목·설명 만들기 (큐 항목 → 유튜브 메타데이터)
# ---------------------------------------------------------------------------

def _strip_markup(text: str) -> str:
    """카드 글의 [[강조]] {{강조}} 마크업과 줄바꿈을 걷어낸다."""
    t = re.sub(r"\[\[(.*?)\]\]", r"\1", text)
    t = re.sub(r"\{\{(.*?)\}\}", r"\1", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _hook(item: dict) -> str:
    for c in item.get("cards") or []:
        if c.get("type") == "hook" and c.get("text"):
            return _strip_markup(str(c["text"]))
    # 훅 카드가 없으면 인스타 캡션 첫 줄
    first = (item.get("instagram_caption") or "").strip().split("\n")[0]
    return _strip_markup(first)


def _yt_safe(text: str) -> str:
    """유튜브는 제목·설명에 '<' '>' 가 있으면 invalidTitle/invalidDescription 으로 거부한다
    (09-29 아침 2081 의 "(p<.05)" 로 확인). 전각 부등호로 바꿔 뜻은 살린다."""
    return text.replace("<", "＜").replace(">", "＞")


def build_title(item: dict) -> str:
    """훅 한 줄 + #Shorts. 100자 안으로. (#Shorts 는 필수는 아니지만 분류를 확실히 해 준다)"""
    hook = _yt_safe(_hook(item) or str(item.get("topic_ko") or item.get("id") or "논문 팩트체크"))
    tag = " #Shorts"
    room = TITLE_MAX - len(tag)
    if len(hook) > room:
        hook = hook[: room - 1].rstrip() + "…"
    return hook + tag


def _source_line(item: dict) -> str:
    p = item.get("paper") or {}
    parts = []
    if p.get("authors"):
        parts.append(str(p["authors"]))
    if p.get("journal"):
        j = str(p["journal"])
        if p.get("year"):
            j += f" ({p['year']})"
        parts.append(j)
    line = ", ".join(parts)
    if p.get("doi"):
        line += f". DOI {p['doi']}"
    if p.get("url"):
        line += f"\n{p['url']}"
    return ("📄 출처: " + line) if line else ""


def build_description(item: dict, *, ai_label: bool = True) -> str:
    """답 한 줄 → 본문 요약 → 출처 → 쿠팡 링크 + 고지 → 해시태그. 5000자 안으로."""
    blocks: list[str] = []

    answer = _strip_markup(str(item.get("answer") or ""))
    if answer:
        blocks.append(answer)

    # 인스타 캡션에서 출처·해시태그 줄을 뺀 본문을 그대로 쓴다 (이미 사람이 읽기 좋은 형태)
    cap = str(item.get("instagram_caption") or "").strip()
    body_lines = [ln for ln in cap.split("\n")
                  if not ln.startswith("📄") and not ln.startswith("#") and "쿠팡 파트너스" not in ln]
    body = "\n".join(body_lines).strip()
    if body and body != answer:
        blocks.append(body)

    src = _source_line(item)
    if src:
        blocks.append(src)

    link = (item.get("link") or "").strip()
    if link:
        blocks.append("📎 논문에서 말한 조건에 맞는 제품 예시\n" + link + "\n\n" + DISCLOSURE)

    if ai_label:
        blocks.append(AI_NOTE)

    credit = music_credit(item)          # 저작자 표시가 필요한 배경음일 때만 한 줄 (make_short.py 가 short.json 에 적어 둔다)
    if credit:
        blocks.append(credit)

    tags = ["#논문팩트체크", "#paper_factcheck", "#Shorts"]
    topic = str(item.get("topic_ko") or "").strip()
    if topic:
        tags.insert(1, "#" + re.sub(r"\s+", "", topic))
    blocks.append(" ".join(tags))

    desc = _yt_safe("\n\n".join(b for b in blocks if b))
    if len(desc) > DESC_MAX:
        desc = desc[: DESC_MAX - 1].rstrip() + "…"
    return desc


def music_credit(item: dict) -> str:
    """images/<id>/short.json 의 music_credit (배경음이 저작자 표시를 요구할 때만 값이 있다)."""
    iid = str(item.get("id") or "").strip()
    if not iid:
        return ""
    p = ROOT / "images" / iid / "short.json"
    try:
        import json  # noqa: PLC0415
        return str(json.loads(p.read_text(encoding="utf-8")).get("music_credit") or "").strip()
    except Exception:
        return ""


def build_tags(item: dict) -> list[str]:
    tags = ["논문팩트체크", "paper_factcheck", "Shorts", "건강", "팩트체크"]
    topic = str(item.get("topic_ko") or "").strip()
    if topic:
        tags.insert(0, topic)
    return tags


def title_key(text: str, n: int = 40) -> str:
    """중복 확인용 키: 앞 n자, 공백·마크업 정리."""
    return _strip_markup(text)[:n].strip()


# ---------------------------------------------------------------------------
# 업로드
# ---------------------------------------------------------------------------

def upload_short(token: str, video_path: str | Path, title: str, description: str,
                 *, tags: list[str] | None = None, privacy: str = "",
                 category_id: str = "", ai_label: bool = True,
                 timeout: int = 300) -> str:
    """resumable 방식으로 올리고 video id 를 돌려준다.

    파일이 2~3MB 라 한 번의 PUT 으로 끝난다. 중간에 끊기면 그냥 실패로 두고, publish.py 의
    다음 실행이 find_posted 로 이미 올라갔는지 본 뒤 다시 올린다.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise YouTubeApiError(f"영상 파일이 없습니다: {video_path}")
    privacy = (privacy or os.environ.get("YT_PRIVACY", "").strip().lower() or "private")
    if privacy not in ("private", "unlisted", "public"):
        raise YouTubeApiError(f"YT_PRIVACY 값이 이상합니다: {privacy!r} (private/unlisted/public)")
    category_id = category_id or os.environ.get("YT_CATEGORY_ID", "").strip() or DEFAULT_CATEGORY

    meta = {
        "snippet": {
            "title": title[:TITLE_MAX],
            "description": description[:DESC_MAX],
            "tags": (tags or [])[:30],
            "categoryId": str(category_id),
            "defaultLanguage": "ko",
            "defaultAudioLanguage": "ko",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            # 인스타 'AI 정보' 라벨과 같은 자기 공개. 유튜브의 '변경·합성된 콘텐츠' 표시.
            "containsSyntheticMedia": bool(ai_label),
        },
    }
    size = video_path.stat().st_size

    # 1) 세션 열기 — Location 헤더가 업로드 URL
    r = _req(
        "POST", UPLOAD,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={**_hdr(token),
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4",
                 "X-Upload-Content-Length": str(size)},
        json=meta, timeout=60,
    )
    if r.status_code >= 400 or not r.headers.get("Location"):
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        raise YouTubeApiError(f"업로드 세션 열기 실패 {r.status_code}: {body}")
    upload_url = r.headers["Location"]
    log.info("유튜브 업로드 세션 열림 (%s, %.1f MB, %s)", privacy, size / 1e6, video_path.name)

    # 2) 파일 본문 올리기
    with video_path.open("rb") as f:
        r2 = _req("PUT", upload_url,
                          headers={**_hdr(token),
                                   "Content-Type": "video/mp4",
                                   "Content-Length": str(size)},
                          data=f, timeout=timeout)
    body = _check(r2, "업로드 PUT")
    vid = body.get("id")
    if not vid:
        raise YouTubeApiError(f"업로드 응답에 id 가 없습니다: {body}")
    st = (body.get("status") or {}).get("uploadStatus", "")
    log.info("유튜브 업로드 완료: %s (uploadStatus=%s, privacy=%s)", vid, st,
             (body.get("status") or {}).get("privacyStatus", privacy))
    return str(vid)


def short_url(video_id: str) -> str:
    return f"https://youtube.com/shorts/{video_id}"


# ---------------------------------------------------------------------------
# 중복 방지 — 내 채널 최근 업로드에서 같은 제목 찾기 (readonly 스코프, 약 3 단위)
# ---------------------------------------------------------------------------

def recent_uploads(token: str, limit: int = 15) -> list[dict]:
    """[{id, title, publishedAt}, ...] 최신순."""
    ch = _check(_req("GET", f"{API}/channels",
                             params={"part": "contentDetails", "mine": "true"},
                             headers=_hdr(token), timeout=30), "channels.list")
    items = ch.get("items") or []
    if not items:
        raise YouTubeApiError("이 계정에 유튜브 채널이 없습니다 (토큰을 받을 때 채널을 골랐는지 확인)")
    uploads = (((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {})
               .get("uploads"))
    if not uploads:
        return []
    pl = _check(_req("GET", f"{API}/playlistItems",
                             params={"part": "snippet", "playlistId": uploads,
                                     "maxResults": str(min(max(limit, 1), 50))},
                             headers=_hdr(token), timeout=30), "playlistItems.list")
    out = []
    for it in pl.get("items") or []:
        sn = it.get("snippet") or {}
        vid = ((sn.get("resourceId") or {}).get("videoId")) or ""
        out.append({"id": vid, "title": sn.get("title", ""), "publishedAt": sn.get("publishedAt", "")})
    return out


def find_posted(token: str, title: str, limit: int = 15) -> str | None:
    """같은 제목(앞 40자)의 영상이 최근 업로드에 있으면 그 id. 없으면 None."""
    key = title_key(title)
    if not key:
        return None
    for v in recent_uploads(token, limit):
        if title_key(v.get("title", "")) == key:
            return v["id"] or None
    return None


def me(token: str) -> dict:
    """채널 이름·id — check_tokens 용."""
    ch = _check(_req("GET", f"{API}/channels",
                             params={"part": "snippet", "mine": "true"},
                             headers=_hdr(token), timeout=30), "channels.list")
    items = ch.get("items") or []
    if not items:
        return {}
    return {"id": items[0].get("id"), "title": (items[0].get("snippet") or {}).get("title")}
