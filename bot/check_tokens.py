"""
Secrets 가 제대로 들어갔는지 확인하는 스크립트 (게시는 하지 않음).
인스타 /me, Threads /me 를 호출해서 계정명이 paper_factcheck 로 나오는지 본다.
YT_* 시크릿이 있으면 유튜브 채널 이름도 확인한다 (없으면 '끔' 으로만 표시, 실패 아님).

실행: python bot/check_tokens.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meta_api  # noqa: E402
import youtube_api  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("check")


def main() -> int:
    ok = True
    ig_token = os.environ.get("IG_TOKEN", "").strip()
    th_token = os.environ.get("THREADS_TOKEN", "").strip()

    if ig_token:
        try:
            me = meta_api.ig_me(ig_token)
            log.info("Instagram OK: username=%s user_id=%s account_type=%s",
                     me.get("username"), me.get("user_id"), me.get("account_type"))
            if os.environ.get("IG_USER_ID", "").strip() not in (me.get("user_id"), me.get("id")):
                log.warning("IG_USER_ID 시크릿(%s)이 /me 결과(user_id=%s, id=%s)와 다릅니다.",
                            os.environ.get("IG_USER_ID"), me.get("user_id"), me.get("id"))
        except meta_api.MetaApiError as e:
            log.error("Instagram 토큰 확인 실패: %s", e)
            ok = False
    else:
        log.error("IG_TOKEN 이 비어 있습니다")
        ok = False

    if th_token:
        try:
            me = meta_api.th_me(th_token)
            log.info("Threads OK: username=%s id=%s", me.get("username"), me.get("id"))
            if os.environ.get("THREADS_USER_ID", "").strip() != str(me.get("id")):
                log.warning("THREADS_USER_ID 시크릿(%s)이 /me 결과(id=%s)와 다릅니다.",
                            os.environ.get("THREADS_USER_ID"), me.get("id"))
        except meta_api.MetaApiError as e:
            log.error("Threads 토큰 확인 실패: %s", e)
            ok = False
    else:
        log.error("THREADS_TOKEN 이 비어 있습니다")
        ok = False

    # 유튜브는 선택 사항 — 시크릿이 없으면 실패로 치지 않는다.
    if youtube_api.configured():
        try:
            tok = youtube_api.access_token()
            me = youtube_api.me(tok)
            log.info("YouTube OK: channel=%s id=%s privacy=%s", me.get("title"), me.get("id"),
                     os.environ.get("YT_PRIVACY", "").strip() or "private")
        except youtube_api.YouTubeApiError as e:
            log.error("YouTube 토큰 확인 실패: %s", e)
            ok = False
    else:
        log.info("YouTube: 끔 (YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN 없음)")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
