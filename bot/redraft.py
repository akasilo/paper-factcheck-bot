"""본문을 받은 뒤 원고를 다시 쓴다.

시트의 '근거' 칸이 `초록만` 인 초안은 논문 초록만 보고 쓴 것이다. 내용이 얕아 보이면
그 논문 본문을 `papers/<PMID>.txt` 로 넣어두면 되고, 이 스크립트가 그 본문을 근거로
같은 초안을 다시 쓴다. id·승인 여부·쿠팡 링크는 그대로 두고 원고만 갈아끼운다.

  python bot/redraft.py --auto              # papers/ 에 본문이 새로 생긴 초안 전부
  python bot/redraft.py --id 2031-sunscreen # 하나만
  python bot/redraft.py --auto --dry-run    # 뭘 다시 쓸지 확인만

다시 쓴 뒤에는 카드도 다시 그려야 한다:
  python bot/render_cards.py --date <id> --force
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import draft_post
import fulltext

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "queue"


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def items() -> list[tuple[Path, dict]]:
    out = []
    for p in sorted(QUEUE.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            out.append((p, load(p)))
        except Exception as e:
            print(f"건너뜀 {p.name}: {e}", file=sys.stderr)
    return out


def needs_redraft(item: dict) -> str:
    """다시 써야 할 이유. 없으면 빈 문자열."""
    paper = item.get("paper") or {}
    pmid, doi = str(paper.get("pmid", "") or ""), str(paper.get("doi", "") or "")
    f = fulltext.manual_file(pmid, doi)
    if f is None:
        return ""
    src = str(item.get("source_text", "") or "")
    if f"papers/{f.name}" in src:
        return ""                      # 이미 그 파일로 쓴 원고
    return f"papers/{f.name} 가 새로 들어옴 (지금 근거: {src or '초록만'})"


def redraft_one(path: Path, item: dict, dry: bool = False) -> bool:
    paper = dict(item.get("paper") or {})
    pmid = str(paper.get("pmid", "") or "")
    topic = {"id": item.get("topic", ""), "ko": item.get("topic_ko", ""),
             "hint": item.get("product_hint", "")}

    body, src = fulltext.get(pmid, doi=paper.get("doi", ""))
    if not body:
        print(f"  {item['id']}: 본문을 못 읽었습니다 — 그대로 둡니다")
        return False
    print(f"  본문: {src} ({len(body)}자)")
    if dry:
        print(f"  (dry-run) {item['id']} 를 이 본문으로 다시 쓸 예정")
        return False

    # 초록은 큐에 없으므로 제목·저널만으로 [논문] 블록을 채운다.
    paper.setdefault("abstract", item.get("answer", "") or "(초록은 아래 본문 발췌로 대신합니다)")
    paper.setdefault("pubtypes", [item.get("evidence_level", "")] if item.get("evidence_level") else [])
    paper.setdefault("mesh", [])

    draft = None
    problems: list[str] = ["시도 없음"]
    for attempt in range(1, 4):
        try:
            draft, used_model = draft_post.generate(
                draft_post.build_prompt(topic, paper, "", body), draft_post.SYSTEM_PROMPT)
        except Exception as e:
            print(f"  시도 {attempt} 실패: {e}", file=sys.stderr)
            continue
        if draft.get("abort"):
            print(f"  본문을 봐도 답이 없다고 판단: {draft.get('abort_reason', '')}", file=sys.stderr)
            return False
        problems = draft_post.validate(draft)
        if not problems:
            break
        print(f"  시도 {attempt} 검증 실패: {problems}", file=sys.stderr)
    if draft is None or problems:
        print(f"  {item['id']}: 다시 쓰기 실패 — 원래 원고를 그대로 둡니다", file=sys.stderr)
        return False

    new = draft_post.build_item(item["id"], topic, paper, draft, used_model, src)
    # 사람이 정한 것과 게시 상태는 그대로 이어간다
    for k in ("approved", "link", "approved_at", "skipped"):
        if k in item:
            new[k] = item[k]
    new["images"] = []                    # 카드가 바뀌었으니 다시 렌더해야 한다
    new["redrafted_from"] = item.get("source_text", "초록만")
    path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  다시 씀: {item['id']}  답='{new.get('answer', '')[:60]}'")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", action="append", default=[], help="다시 쓸 큐 id (여러 번 가능)")
    ap.add_argument("--auto", action="store_true", help="papers/ 에 본문이 생긴 초안 전부")
    ap.add_argument("--dry-run", action="store_true", help="뭘 다시 쓸지 확인만")
    args = ap.parse_args()
    if not args.id and not args.auto:
        ap.error("--id 또는 --auto 가 필요합니다")

    targets = []
    for p, it in items():
        iid = str(it.get("id", ""))
        if args.id:
            if iid in args.id:
                targets.append((p, it, "지정됨"))
        else:
            why = needs_redraft(it)
            if why:
                targets.append((p, it, why))

    if not targets:
        print("다시 쓸 초안 없음")
        return 0

    done = 0
    for p, it, why in targets:
        print(f"== {it.get('id')} ({it.get('topic_ko', '')}) — {why}")
        if redraft_one(p, it, args.dry_run):
            done += 1
    print(f"완료: {done}개 다시 씀 / 대상 {len(targets)}개")
    # 다시 쓴 게 있으면 워크플로가 이어서 렌더·시트갱신을 하도록 id 를 남긴다
    if done and (out := __import__("os").environ.get("GITHUB_OUTPUT")):
        Path(out).open("a", encoding="utf-8").write(
            "redrafted=" + ",".join(str(it.get("id")) for _, it, _ in targets) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
