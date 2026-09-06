#!/usr/bin/env bash
# 사용: bash bot/commit_push.sh "커밋 메시지" 경로1 [경로2 ...]
#
# 지정한 폴더의 변경을 커밋하고 push 한다.
# 다른 워크플로(approve, render-cards 등)가 먼저 push 해서 거부되면
# 원격 main 을 다시 받은 뒤 이 워크플로의 변경 파일을 그 위에 다시 얹어 재시도한다.
# → 같은 파일을 양쪽이 고쳤을 때는 "지금 실행 중인 워크플로" 쪽이 항상 이긴다.
set -euo pipefail

msg="$1"; shift
paths=("$@")
branch="${GITHUB_REF_NAME:-main}"

git config user.name "paper-factcheck-bot"
git config user.email "bot@users.noreply.github.com"

stage() {
  mkdir -p "${paths[@]}"
  git add -A -- "${paths[@]}"
}

stage
if git diff --cached --quiet; then
  echo "변경 없음"
  exit 0
fi

# 이 워크플로가 만든 변경을 따로 보관 (재시도 때 다시 얹기 위해)
tmp="$(mktemp -d)"
git diff --cached --name-only --diff-filter=D > "$tmp/deleted.txt"
git diff --cached --name-only --diff-filter=d > "$tmp/changed.txt"
if [ -s "$tmp/changed.txt" ]; then
  tar -cf "$tmp/changed.tar" -T "$tmp/changed.txt"
fi

git commit -q -m "$msg"

for i in 1 2 3 4 5; do
  if git push -q origin "HEAD:$branch"; then
    echo "push 완료"
    exit 0
  fi
  echo "push 거부됨 — 원격 변경을 받아 다시 얹는 중 ($i/5)"
  sleep $((i * 3))
  git fetch -q origin "$branch"
  git reset -q --hard "origin/$branch"
  if [ -s "$tmp/changed.txt" ]; then
    tar -xf "$tmp/changed.tar"
  fi
  if [ -s "$tmp/deleted.txt" ]; then
    xargs -a "$tmp/deleted.txt" -d '\n' git rm -q --ignore-unmatch --
  fi
  stage
  if git diff --cached --quiet; then
    echo "원격에 이미 같은 내용이 반영되어 있음"
    exit 0
  fi
  git commit -q -m "$msg"
done

echo "push 실패 (5회 재시도)"
exit 1
