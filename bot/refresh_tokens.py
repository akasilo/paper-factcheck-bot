"""
인스타·Threads 장기 토큰(60일) 갱신.

- 갱신 결과 토큰 문자열이 바뀌지 않았으면 만료만 연장된 것이므로 끝.
- 토큰이 새 값으로 바뀌었으면 GitHub Secrets 도 갱신해야 한다.
  REPO_PAT(Actions secrets 쓰기 권한이 있는 fine-grained PAT) 이 있으면 gh CLI 로 자동 갱신,
  없으면 실패 코드로 종료해서 워크플로가 빨간불이 나게 한다 (수동 교체 알림용).

실행: python bot/refresh_tokens.py
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meta_api  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refresh")


def set_secret(name: str, value: str) -> bool:
    pat = os.environ.get("REPO_PAT", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not pat or not repo:
        return False
    proc = subprocess.run(
        ["gh", "secret", "set", name, "--repo", repo, "--body", value],
        env={**os.environ, "GH_TOKEN": pat},
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        log.error("gh secret set %s 실패: %s", name, proc.stderr.strip())
        return False
    log.info("GitHub Secret %s 갱신 완료", name)
    return True


def refresh_one(label: str, secret_name: str, old: str, fn) -> bool:
    if not old:
        log.warning("%s 토큰이 비어 있어 건너뜀", label)
        return True
    try:
        res = fn(old)
    except meta_api.MetaApiError as e:
        log.error("%s 토큰 갱신 실패: %s", label, e)
        return False
    new = res.get("access_token", "")
    days = int(res.get("expires_in", 0)) // 86400
    log.info("%s 토큰 갱신됨 — 남은 유효기간 약 %d일", label, days)
    if new and new != old:
        log.warning("%s 토큰 값이 바뀌었습니다 → Secrets 갱신 필요", label)
        if not set_secret(secret_name, new):
            log.error("REPO_PAT 이 없어 %s 를 자동 갱신하지 못했습니다. "
                      "새 토큰을 GitHub Secrets 에 직접 넣어주세요.", secret_name)
            return False
    return True


def main() -> int:
    ok = True
    ok &= refresh_one("Instagram", "IG_TOKEN",
                      os.environ.get("IG_TOKEN", "").strip(), meta_api.ig_refresh_token)
    ok &= refresh_one("Threads", "THREADS_TOKEN",
                      os.environ.get("THREADS_TOKEN", "").strip(), meta_api.th_refresh_token)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
