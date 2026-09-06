"""
승인 이슈의 댓글을 읽어 큐를 승인/건너뛰기 처리한다 (approve.yml 에서 호출).

환경변수
  ISSUE_NUMBER, ISSUE_BODY, COMMENT_BODY, GITHUB_REPOSITORY, GH_TOKEN
동작
  - 댓글에 http(s) 링크가 있으면 → queue/<date>.json 의 link 에 저장, approved=true, 이슈 닫기
  - 댓글이 'skip' 이면 → approved=false, skipped=true, 이슈 닫기
  - 그 외 → 아무것도 안 함 (일반 대화)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def gh(*args: str) -> None:
    subprocess.run(["gh", *args], check=False)


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    issue = os.environ.get("ISSUE_NUMBER", "")
    body = os.environ.get("ISSUE_BODY", "")
    comment = (os.environ.get("COMMENT_BODY", "") or "").strip()

    m = re.search(r"<!--\s*queue:(\d{4}-\d{2}-\d{2})\s*-->", body)
    if not m:
        print("이슈 본문에 queue 날짜 표식이 없음 — 무시")
        return 0
    date = m.group(1)
    qpath = ROOT / "queue" / f"{date}.json"
    if not qpath.exists():
        gh("issue", "comment", issue, "--repo", repo, "--body",
           f"⚠️ `queue/{date}.json` 이 없습니다. 이미 게시됐거나 삭제된 항목이에요.")
        return 0
    item = json.loads(qpath.read_text(encoding="utf-8"))

    if re.fullmatch(r"(?i)\s*/?skip\s*[.!]?\s*", comment):
        item["approved"] = False
        item["skipped"] = True
        qpath.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        gh("issue", "comment", issue, "--repo", repo, "--body", f"⏭️ {date} 건너뜁니다. 게시하지 않아요.")
        gh("issue", "close", issue, "--repo", repo, "--reason", "not planned")
        print(f"skip: {date}")
        return 0

    urls = re.findall(r"https?://[^\s<>()\"']+", comment)
    if not urls:
        print("링크도 skip 도 아님 — 무시")
        return 0
    link = urls[0].rstrip(".,)")
    item["link"] = link
    item["approved"] = True
    item.pop("skipped", None)
    qpath.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gh("issue", "comment", issue, "--repo", repo, "--body",
       f"✅ 승인됐습니다. 링크: {link}\n{date} 아침 8시(KST)에 인스타 + Threads 에 올라갑니다. "
       f"취소하려면 `queue/{date}.json` 의 `approved` 를 `false` 로 바꾸세요.")
    gh("issue", "close", issue, "--repo", repo, "--reason", "completed")
    print(f"approved: {date} link={link}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
