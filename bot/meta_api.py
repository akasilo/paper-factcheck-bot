"""
Instagram(Instagram Login 방식) + Threads 게시용 얇은 API 래퍼.

- Instagram: https://graph.instagram.com/{version}/{IG_USER_ID}/media → media_publish
- Threads  : https://graph.threads.net/{version}/{THREADS_USER_ID}/threads → threads_publish
             답글은 같은 /threads 엔드포인트에 reply_to_id 를 붙여서 만든다.

모든 함수는 실패 시 MetaApiError 를 던진다. 호출 측(publish.py)에서 잡아서 로그로 남긴다.
"""
from __future__ import annotations

import time
import logging
from typing import Iterable

import requests

log = logging.getLogger("meta_api")

IG_GRAPH = "https://graph.instagram.com/v23.0"
TH_GRAPH = "https://graph.threads.net/v1.0"

# 컨테이너가 처리(이미지 다운로드·검증)되길 기다리는 시간
CONTAINER_POLL_INTERVAL = 5     # 초
CONTAINER_POLL_TIMEOUT = 180    # 초
VIDEO_POLL_TIMEOUT = 600        # 초 — 동영상(릴스·Threads 비디오)은 서버 쪽 변환이 있어 더 오래 걸린다


class MetaApiError(RuntimeError):
    pass


def _post(url: str, data: dict, *, timeout: int = 60) -> dict:
    r = requests.post(url, data=data, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text}
    if r.status_code >= 400 or "error" in body:
        raise MetaApiError(f"POST {url} -> {r.status_code}: {body}")
    return body


def _get(url: str, params: dict, *, timeout: int = 60) -> dict:
    r = requests.get(url, params=params, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text}
    if r.status_code >= 400 or "error" in body:
        raise MetaApiError(f"GET {url} -> {r.status_code}: {body}")
    return body


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------

def ig_wait_container(container_id: str, token: str, timeout: int = CONTAINER_POLL_TIMEOUT) -> None:
    """캐러셀/이미지/릴스 컨테이너가 FINISHED 될 때까지 대기."""
    deadline = time.time() + timeout
    while True:
        info = _get(f"{IG_GRAPH}/{container_id}",
                    {"fields": "status_code,status", "access_token": token})
        code = info.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise MetaApiError(f"IG container {container_id} failed: {info}")
        if time.time() > deadline:
            raise MetaApiError(f"IG container {container_id} timeout: {info}")
        time.sleep(CONTAINER_POLL_INTERVAL)


def ig_publish_carousel(ig_user_id: str, token: str,
                        image_urls: Iterable[str], caption: str) -> str:
    """이미지 여러 장을 캐러셀로 게시하고 게시물 ID 를 돌려준다."""
    image_urls = list(image_urls)
    if not 2 <= len(image_urls) <= 10:
        raise MetaApiError(f"인스타 캐러셀은 2~10장이어야 합니다 (현재 {len(image_urls)}장)")

    children = []
    for i, url in enumerate(image_urls, 1):
        log.info("IG 아이템 컨테이너 생성 %d/%d: %s", i, len(image_urls), url)
        res = _post(f"{IG_GRAPH}/{ig_user_id}/media", {
            "image_url": url,
            "is_carousel_item": "true",
            "access_token": token,
        })
        children.append(res["id"])

    for cid in children:
        ig_wait_container(cid, token)

    log.info("IG 캐러셀 컨테이너 생성 (children=%d)", len(children))
    carousel = _post(f"{IG_GRAPH}/{ig_user_id}/media", {
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "caption": caption,
        "access_token": token,
    })
    ig_wait_container(carousel["id"], token)

    log.info("IG 게시 요청")
    published = _post(f"{IG_GRAPH}/{ig_user_id}/media_publish", {
        "creation_id": carousel["id"],
        "access_token": token,
    })
    return published["id"]


def ig_publish_reel(ig_user_id: str, token: str, video_url: str, caption: str,
                    share_to_feed: bool = False) -> str:
    """세로 동영상을 릴스로 게시 → 게시물 ID.

    share_to_feed=False 면 프로필 그리드·홈 피드에는 안 뜨고 릴스 탭에만 뜬다
    (피드에는 같은 글의 카드 캐러셀이 따로 올라가므로 기본은 False).
    동영상은 공개 URL 의 MP4(H.264/AAC, 9:16) 여야 하고 서버 변환을 기다려야 한다.
    """
    log.info("IG 릴스 컨테이너 생성: %s", video_url)
    res = _post(f"{IG_GRAPH}/{ig_user_id}/media", {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true" if share_to_feed else "false",
        "access_token": token,
    })
    ig_wait_container(res["id"], token, timeout=VIDEO_POLL_TIMEOUT)
    log.info("IG 릴스 게시 요청")
    published = _post(f"{IG_GRAPH}/{ig_user_id}/media_publish", {
        "creation_id": res["id"],
        "access_token": token,
    })
    return published["id"]


def ig_publish_single_image(ig_user_id: str, token: str,
                            image_url: str, caption: str) -> str:
    """이미지 한 장 게시 (테스트용)."""
    res = _post(f"{IG_GRAPH}/{ig_user_id}/media", {
        "image_url": image_url,
        "caption": caption,
        "access_token": token,
    })
    ig_wait_container(res["id"], token)
    published = _post(f"{IG_GRAPH}/{ig_user_id}/media_publish", {
        "creation_id": res["id"],
        "access_token": token,
    })
    return published["id"]


# ---------------------------------------------------------------------------
# "정말 올라갔나?" 확인 — 오류 응답을 그대로 믿지 않기 위한 장치
#
# 2026-09-13~14: media_publish 가 403 "action is blocked" 를 돌려주면서 실제로는
# 게시를 해버렸다. 봇은 실패로 알고 재시도했고 같은 글이 인스타에 두 번 올라갔다.
# 그래서 올리기 전에 '이미 있는지' 보고, 오류가 나도 '그래도 올라갔는지' 다시 본다.
# ---------------------------------------------------------------------------

def caption_key(text: str, n: int = 60) -> str:
    """캡션 비교용 열쇠 — 공백을 접고 앞부분만 본다 (플랫폼이 뒤를 자를 수 있다)."""
    return " ".join((text or "").split())[:n]


def ig_recent_media(ig_user_id: str, token: str, limit: int = 12) -> list[dict]:
    res = _get(f"{IG_GRAPH}/{ig_user_id}/media",
               {"fields": "id,caption,timestamp,media_type,media_product_type",
                "limit": limit, "access_token": token})
    return res.get("data", []) or []


def ig_find_posted(ig_user_id: str, token: str, caption: str,
                   limit: int = 12, kind: str | None = None) -> str | None:
    """같은 캡션의 글이 이미 계정에 있으면 그 id. 없으면 None.

    kind: "feed" → 캐러셀/사진(media_product_type FEED), "reel" → 릴스(REELS), None → 아무거나.
    같은 글이 캐러셀과 릴스로 둘 다 올라가므로(캡션 동일) 어느 쪽을 찾는지 구분해야 한다.
    """
    key = caption_key(caption)
    if not key:
        return None
    for m in ig_recent_media(ig_user_id, token, limit):
        if caption_key(m.get("caption", "")) != key:
            continue
        product = str(m.get("media_product_type") or "").upper()
        if kind == "reel" and product != "REELS":
            continue
        if kind == "feed" and product == "REELS":
            continue
        return str(m.get("id"))
    return None


def th_recent_posts(th_user_id: str, token: str, limit: int = 12) -> list[dict]:
    res = _get(f"{TH_GRAPH}/{th_user_id}/threads",
               {"fields": "id,text,permalink,timestamp", "limit": limit, "access_token": token})
    return res.get("data", []) or []


def th_find_posted(th_user_id: str, token: str, text: str,
                   limit: int = 12) -> tuple[str, str | None] | None:
    """같은 본문의 글이 이미 있으면 (id, permalink). 없으면 None."""
    key = caption_key(text)
    if not key:
        return None
    for m in th_recent_posts(th_user_id, token, limit):
        if caption_key(m.get("text", "")) == key:
            return str(m.get("id")), m.get("permalink")
    return None


def ig_refresh_token(token: str) -> dict:
    """장기 토큰 갱신 (60일 연장). 반환: {access_token, token_type, expires_in}"""
    return _get(f"{IG_GRAPH.rsplit('/', 1)[0]}/refresh_access_token", {
        "grant_type": "ig_refresh_token",
        "access_token": token,
    })


def ig_me(token: str) -> dict:
    return _get(f"{IG_GRAPH}/me", {
        "fields": "id,user_id,username,account_type",
        "access_token": token,
    })


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

def th_wait_container(container_id: str, token: str, timeout: int = CONTAINER_POLL_TIMEOUT) -> None:
    deadline = time.time() + timeout
    while True:
        info = _get(f"{TH_GRAPH}/{container_id}",
                    {"fields": "status,error_message", "access_token": token})
        status = info.get("status")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise MetaApiError(f"Threads container {container_id} failed: {info}")
        if time.time() > deadline:
            raise MetaApiError(f"Threads container {container_id} timeout: {info}")
        time.sleep(CONTAINER_POLL_INTERVAL)


def th_publish_carousel(th_user_id: str, token: str,
                        image_urls: Iterable[str], text: str,
                        lead_video_url: str | None = None) -> str:
    """Threads 캐러셀 게시 → 게시물 ID.

    lead_video_url 이 있으면 첫 장은 그 동영상, 이어서 사진들. 동영상 컨테이너가 변환에
    실패하면(ERROR/시간 초과) 그 장만 빼고 사진 캐러셀로 올린다 — 게시 자체는 막지 않는다.
    """
    image_urls = list(image_urls)
    total = len(image_urls) + (1 if lead_video_url else 0)
    if not 2 <= total <= 20:
        raise MetaApiError(f"Threads 캐러셀은 2~20장이어야 합니다 (현재 {total}장)")
    if len(text) > 500:
        raise MetaApiError(f"Threads 본문은 500자 이하여야 합니다 (현재 {len(text)}자)")

    children = []
    video_cid = None
    if lead_video_url:
        log.info("Threads 동영상 아이템 컨테이너 생성 1/%d: %s", total, lead_video_url)
        try:
            res = _post(f"{TH_GRAPH}/{th_user_id}/threads", {
                "media_type": "VIDEO",
                "video_url": lead_video_url,
                "is_carousel_item": "true",
                "access_token": token,
            })
            video_cid = res["id"]
        except MetaApiError as e:
            log.warning("Threads 동영상 컨테이너 생성 실패 — 사진만 올립니다: %s", e)

    for i, url in enumerate(image_urls, 1):
        log.info("Threads 아이템 컨테이너 생성 %d/%d: %s", i + (1 if video_cid else 0), total, url)
        res = _post(f"{TH_GRAPH}/{th_user_id}/threads", {
            "media_type": "IMAGE",
            "image_url": url,
            "is_carousel_item": "true",
            "access_token": token,
        })
        children.append(res["id"])

    for cid in children:
        th_wait_container(cid, token)
    if video_cid:
        try:
            th_wait_container(video_cid, token, timeout=VIDEO_POLL_TIMEOUT)
            children.insert(0, video_cid)
        except MetaApiError as e:
            log.warning("Threads 동영상 변환 실패 — 사진만 올립니다: %s", e)

    log.info("Threads 캐러셀 컨테이너 생성 (children=%d)", len(children))
    carousel = _post(f"{TH_GRAPH}/{th_user_id}/threads", {
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "text": text,
        "access_token": token,
    })
    th_wait_container(carousel["id"], token)

    log.info("Threads 게시 요청")
    return th_publish_container(th_user_id, token, carousel["id"])


# 컨테이너가 FINISHED 로 뜬 직후 threads_publish 가 "Media Not Found"(code 24, subcode 4279009)
# 를 돌려주는 일이 있다 (2026-09-13 점심 2034 콜라겐: 인스타는 올라가고 Threads 만 실패).
# Threads 쪽 전파 지연으로 보이며, 잠시 뒤 다시 부르면 된다. 그래서 이 오류만 골라 몇 번 더 시도한다.
PUBLISH_RETRIES = 5
PUBLISH_RETRY_WAIT = 15         # 초


def _is_media_not_found(err: Exception) -> bool:
    s = str(err)
    return "4279009" in s or "Media Not Found" in s or "cannot be found" in s


def th_publish_container(th_user_id: str, token: str, creation_id: str) -> str:
    """컨테이너를 게시한다. 'Media Not Found' 면 잠시 기다렸다 다시 시도."""
    last: Exception | None = None
    for attempt in range(1, PUBLISH_RETRIES + 1):
        try:
            published = _post(f"{TH_GRAPH}/{th_user_id}/threads_publish", {
                "creation_id": creation_id,
                "access_token": token,
            })
            return published["id"]
        except MetaApiError as e:
            if not _is_media_not_found(e) or attempt == PUBLISH_RETRIES:
                raise
            last = e
            log.warning("Threads 게시 %d/%d 실패 (Media Not Found) — %d초 뒤 재시도",
                        attempt, PUBLISH_RETRIES, PUBLISH_RETRY_WAIT)
            time.sleep(PUBLISH_RETRY_WAIT)
    raise MetaApiError(f"Threads 게시 실패: {last}")


def th_publish_text(th_user_id: str, token: str, text: str,
                    reply_to_id: str | None = None,
                    link_attachment: str | None = None) -> str:
    """텍스트 게시물 또는 (reply_to_id 가 있으면) 답글 게시 → ID."""
    if len(text) > 500:
        raise MetaApiError(f"Threads 텍스트는 500자 이하여야 합니다 (현재 {len(text)}자)")
    data = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to_id:
        data["reply_to_id"] = reply_to_id
    if link_attachment:
        data["link_attachment"] = link_attachment
    res = _post(f"{TH_GRAPH}/{th_user_id}/threads", data)
    th_wait_container(res["id"], token)
    return th_publish_container(th_user_id, token, res["id"])


def th_refresh_token(token: str) -> dict:
    """장기 토큰 갱신 (60일 연장)."""
    return _get("https://graph.threads.net/refresh_access_token", {
        "grant_type": "th_refresh_token",
        "access_token": token,
    })


def th_me(token: str) -> dict:
    return _get(f"{TH_GRAPH}/me", {
        "fields": "id,username,name",
        "access_token": token,
    })


def th_permalink(post_id: str, token: str) -> str | None:
    try:
        info = _get(f"{TH_GRAPH}/{post_id}", {"fields": "permalink", "access_token": token})
        return info.get("permalink")
    except MetaApiError:
        return None
