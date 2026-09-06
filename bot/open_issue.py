"""
승인 대기 이슈 열기 — queue/<date>.json 초안을 GitHub 이슈로 올려 휴대폰 알림을 받게 한다.
이슈 댓글에 링크를 달면 approve.yml 이 큐를 승인 처리한다.

실행 (GitHub Actions 안에서, GH_TOKEN 필요)
  python bot/open_issue.py --date 2026-09-08
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = "approval"


def sh(*args: str, input_text: str | None = None) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, input=input_text)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} 실패: {proc.stderr.strip()}")
    return proc.stdout.strip()


def ensure_label(repo: str) -> None:
    try:
        sh("gh", "label", "create", LABEL, "--repo", repo, "--color", "FBCA04",
           "--description", "게시 승인 대기", "--force")
    except RuntimeError as e:
        print(f"라벨 생성 건너뜀: {e}", file=sys.stderr)


def strip_markup(s: str) -> str:
    return s.replace("[[", "**").replace("]]", "**").replace("{{", "**").replace("}}", "**")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = ap.parse_args()
    if not args.repo:
        print("--repo 또는 GITHUB_REPOSITORY 필요", file=sys.stderr)
        return 2

    qpath = ROOT / "queue" / f"{args.date}.json"
    if not qpath.exists():
        print(f"큐 파일 없음: {qpath}")
        return 1
    item = json.loads(qpath.read_text(encoding="utf-8"))
    paper = item.get("paper", {})
    raw_base = f"https://raw.githubusercontent.com/{args.repo}/main/"

    # 이미 열린 승인 이슈가 있으면 중복 생성 안 함
    existing = sh("gh", "issue", "list", "--repo", args.repo, "--label", LABEL, "--state", "open",
                  "--search", f"\"{args.date}\" in:title", "--json", "number", "--jq", ".[].number")
    if existing:
        print(f"이미 열린 승인 이슈 #{existing.splitlines()[0]} — 건너뜀")
        return 0

    verdict_ko = {"bad": "⚠️ 문제 있음", "good": "✅ 의외로 좋음", "mixed": "🤔 엇갈림"}.get(item.get("verdict"), "")
    title = f"[승인 대기] {args.date} · {item.get('topic_ko', '')} — {paper.get('title', '')[:60]}"

    lines = [
        f"<!-- queue:{args.date} -->",
        f"## {args.date} 게시 초안 · {item.get('topic_ko', '')} {verdict_ko}",
        "",
        "### 승인 방법",
        "- 이 이슈에 **쿠팡파트너스 링크 하나**를 댓글로 달면 → 승인 + 링크 저장 → 다음 날 아침 8시 게시",
        "- 건너뛰려면 댓글에 `skip`",
        "- 글을 고치려면 `queue/" + args.date + ".json` 을 편집한 뒤 댓글로 링크",
        "",
        f"**추천 제품 조건:** {item.get('product_hint') or '(없음)'}",
        f"**근거 수준:** {item.get('evidence_level', '')}",
        "",
        "### 논문",
        f"- {paper.get('title', '')}",
        f"- {paper.get('authors', '')} · {paper.get('journal', '')} ({paper.get('year', '')})",
        f"- DOI {paper.get('doi', '')} · {paper.get('url', '')}",
        "",
        "### 카드 원고",
    ]
    for i, c in enumerate(item.get("cards", []), 1):
        if c.get("type") == "source":
            lines.append(f"{i}. (출처 카드)")
        else:
            t = strip_markup(c.get("text", "")).replace("\n", " / ")
            lines.append(f"{i}. {t}")
            if c.get("note"):
                lines.append(f"   - {c['note']}")
    if item.get("caveats"):
        lines += ["", "### 봇이 남긴 주의점"] + [f"- {c}" for c in item["caveats"]]
    lines += ["", "### 인스타 캡션", "```", item.get("instagram_caption", ""), "```",
              "", "### Threads 본문", "```", item.get("threads_text", ""), "```"]
    imgs = item.get("images") or []
    if imgs:
        lines += ["", "### 카드 미리보기"]
        for p in imgs:
            lines.append(f'<img src="{raw_base}{p}" width="260">')
    body = "\n".join(lines)

    ensure_label(args.repo)
    url = sh("gh", "issue", "create", "--repo", args.repo, "--title", title,
             "--label", LABEL, "--body-file", "-", input_text=body)
    print(f"이슈 생성: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
