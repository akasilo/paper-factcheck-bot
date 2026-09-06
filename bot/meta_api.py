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

def ig_wait_container(container_id: str, token: str) -> None:
    """캐러셀/이미지 컨테이너가 FINISHED 될 때까지 대기."""
    deadline = time.time() + CONTAINER_POLL_TIMEOUT
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

def th_wait_container(container_id: str, token: str) -> None:
    deadline = time.time() + CONTAINER_POLL_TIMEOUT
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
                        image_urls: Iterable[str], text: str) -> str:
    """Threads 캐러셀 게시 → 게시물 ID."""
    image_urls = list(image_urls)
    if not 2 <= len(image_urls) <= 20:
        raise MetaApiError(f"Threads 캐러셀은 2~20장이어야 합니다 (현재 {len(image_urls)}장)")
    if len(text) > 500:
        raise MetaApiError(f"Threads 본문은 500자 이하여야 합니다 (현재 {len(text)}자)")

    children = []
    for i, url in enumerate(image_urls, 1):
        log.info("Threads 아이템 컨테이너 생성 %d/%d: %s", i, len(image_urls), url)
        res = _post(f"{TH_GRAPH}/{th_user_id}/threads", {
            "media_type": "IMAGE",
            "image_url": url,
            "is_carousel_item": "true",
            "access_token": token,
        })
        children.append(res["id"])

    for cid in children:
        th_wait_container(cid, token)

    log.info("Threads 캐러셀 컨테이너 생성 (children=%d)", len(children))
    carousel = _post(f"{TH_GRAPH}/{th_user_id}/threads", {
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "text": text,
        "access_token": token,
    })
    th_wait_container(carousel["id"], token)

    log.info("Threads 게시 요청")
    published = _post(f"{TH_GRAPH}/{th_user_id}/threads_publish", {
        "creation_id": carousel["id"],
        "access_token": token,
    })
    return published["id"]


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
    published = _post(f"{TH_GRAPH}/{th_user_id}/threads_publish", {
        "creation_id": res["id"],
        "access_token": token,
    })
    return published["id"]


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
