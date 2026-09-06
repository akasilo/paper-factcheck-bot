"""
큐(queue/YYYY-MM-DD.json)에 있는 승인된 카드뉴스를 인스타그램 + Threads 에 게시한다.

동작 순서
  1. 오늘 날짜(KST) 큐 파일을 찾는다. 없으면 오늘 이전 날짜 중 가장 오래된 승인 항목을 쓴다.
  2. approved == true 이고 추천 링크가 있어야만 게시한다. (아니면 아무것도 안 하고 종료)
  3. 인스타 캐러셀 게시 → Threads 캐러셀 게시 → Threads 게시물에 추천 링크 답글.
  4. 결과를 posted/YYYY-MM-DD.json 에 기록하고 큐 파일은 삭제한다.
     (단계마다 바로 기록하므로 중간에 실패해도 재실행 시 이미 올린 건 건너뛴다)

실행 예
  python bot/publish.py                 # 오늘 큐 게시
  python bot/publish.py --date 2026-09-07
  python bot/publish.py --dry-run       # API 호출 없이 검증만
  python bot/publish.py --target threads
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meta_api  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QUEUE_DIR = ROOT / "queue"
POSTED_DIR = ROOT / "posted"
KST = timezone(timedelta(hours=9))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("publish")

DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
DEFAULT_REPLY = (
    "📎 논문에서 말한 조건에 맞는 제품 예시예요\n"
    "{link}\n\n" + DISCLOSURE
)


def today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def env(name: str, required: bool = True) -> str:
    v = os.environ.get(name, "").strip()
    if required and not v:
        raise SystemExit(f"환경변수 {name} 가 비어 있습니다. GitHub Secrets 를 확인하세요.")
    return v


def load_json(p: Path) -> dict:
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def pick_queue_item(date: str) -> Path | None:
    exact = QUEUE_DIR / f"{date}.json"
    if exact.exists():
        return exact
    # 놓친 날짜가 있으면 가장 오래된 승인 항목을 집는다
    for p in sorted(QUEUE_DIR.glob("????-??-??.json")):
        if p.stem <= date:
            try:
                if load_json(p).get("approved") is True:
                    return p
            except Exception:
                continue
    return None


def resolve_image_urls(item: dict) -> list[str]:
    if item.get("image_urls"):
        return list(item["image_urls"])
    base = env("IMAGE_BASE_URL", required=False) or item.get("image_base_url", "")
    if not base:
        raise SystemExit("image_urls 가 없고 IMAGE_BASE_URL 도 설정되지 않았습니다.")
    base = base.rstrip("/") + "/"
    return [base + rel.lstrip("/") for rel in item.get("images", [])]


def validate(item: dict, image_urls: list[str], *, allow_no_link: bool) -> list[str]:
    problems = []
    if item.get("approved") is not True:
        problems.append("approved 가 true 가 아닙니다 (아직 승인 전)")
    link = (item.get("link") or "").strip()
    if not link and not allow_no_link and item.get("link_required", True):
        problems.append("추천 링크(link)가 비어 있습니다")
    if not 2 <= len(image_urls) <= 10:
        problems.append(f"이미지는 2~10장이어야 합니다 (현재 {len(image_urls)}장)")
    cap = item.get("instagram_caption", "")
    if not cap:
        problems.append("instagram_caption 이 비어 있습니다")
    if len(cap) > 2200:
        problems.append(f"instagram_caption 이 2200자를 넘습니다 ({len(cap)}자)")
    tt = item.get("threads_text", "")
    if not tt:
        problems.append("threads_text 가 비어 있습니다")
    if len(tt) > 500:
        problems.append(f"threads_text 가 500자를 넘습니다 ({len(tt)}자)")
    return problems


def build_reply(item: dict) -> str | None:
    link = (item.get("link") or "").strip()
    if not link:
        return None
    template = item.get("threads_reply") or DEFAULT_REPLY
    text = template.replace("{link}", link)
    if DISCLOSURE not in text and "쿠팡 파트너스" not in text:
        text = text.rstrip() + "\n\n" + DISCLOSURE
    if len(text) > 500:
        raise SystemExit(f"Threads 답글이 500자를 넘습니다 ({len(text)}자)")
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=today_kst(), help="큐 날짜 YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 검증만")
    ap.add_argument("--target", choices=["both", "instagram", "threads"], default="both")
    ap.add_argument("--allow-no-link", action="store_true", help="링크 없이도 게시 허용")
    args = ap.parse_args()

    qpath = pick_queue_item(args.date)
    if not qpath:
        log.info("게시할 큐 항목이 없습니다 (date=%s). 종료.", args.date)
        return 0
    item = load_json(qpath)
    date = qpath.stem
    log.info("큐 항목: %s", qpath.name)

    image_urls = resolve_image_urls(item)
    problems = validate(item, image_urls, allow_no_link=args.allow_no_link)
    if problems:
        for p in problems:
            log.warning("건너뜀: %s", p)
        # 승인 전 항목은 오류가 아니라 '아직 아님'이므로 정상 종료
        return 0 if item.get("approved") is not True else 1

    reply_text = build_reply(item)

    log.info("이미지 %d장: %s", len(image_urls), image_urls)
    log.info("인스타 캡션 %d자 / Threads 본문 %d자 / 답글 %s",
             len(item["instagram_caption"]), len(item["threads_text"]),
             f"{len(reply_text)}자" if reply_text else "없음")

    if args.dry_run:
        log.info("dry-run: 여기까지. 실제 게시는 하지 않습니다.")
        return 0

    result_path = POSTED_DIR / f"{date}.json"
    result = load_json(result_path) if result_path.exists() else {"date": date, "queue": item}
    result.setdefault("queue", item)

    do_ig = args.target in ("both", "instagram")
    do_th = args.target in ("both", "threads")

    # ---- Instagram -------------------------------------------------------
    if do_ig:
        if result.get("instagram_post_id"):
            log.info("인스타는 이미 게시됨 (%s) — 건너뜀", result["instagram_post_id"])
        else:
            ig_user = env("IG_USER_ID")
            ig_token = env("IG_TOKEN")
            try:
                post_id = meta_api.ig_publish_carousel(
                    ig_user, ig_token, image_urls, item["instagram_caption"])
                result["instagram_post_id"] = post_id
                result["instagram_posted_at"] = datetime.now(KST).isoformat()
                save_json(result_path, result)
                log.info("인스타 게시 완료: %s", post_id)
            except meta_api.MetaApiError as e:
                log.error("인스타 게시 실패: %s", e)
                result.setdefault("errors", []).append({"instagram": str(e)})
                save_json(result_path, result)
                return 1

    # ---- Threads ---------------------------------------------------------
    if do_th:
        th_user = env("THREADS_USER_ID")
        th_token = env("THREADS_TOKEN")
        if result.get("threads_post_id"):
            log.info("Threads 는 이미 게시됨 (%s) — 건너뜀", result["threads_post_id"])
        else:
            try:
                post_id = meta_api.th_publish_carousel(
                    th_user, th_token, image_urls, item["threads_text"])
                result["threads_post_id"] = post_id
                result["threads_posted_at"] = datetime.now(KST).isoformat()
                result["threads_permalink"] = meta_api.th_permalink(post_id, th_token)
                save_json(result_path, result)
                log.info("Threads 게시 완료: %s %s", post_id, result.get("threads_permalink"))
            except meta_api.MetaApiError as e:
                log.error("Threads 게시 실패: %s", e)
                result.setdefault("errors", []).append({"threads": str(e)})
                save_json(result_path, result)
                return 1

        if reply_text and not result.get("threads_reply_id"):
            try:
                reply_id = meta_api.th_publish_text(
                    th_user, th_token, reply_text, reply_to_id=result["threads_post_id"])
                result["threads_reply_id"] = reply_id
                save_json(result_path, result)
                log.info("Threads 링크 답글 완료: %s", reply_id)
            except meta_api.MetaApiError as e:
                log.error("Threads 답글 실패: %s", e)
                result.setdefault("errors", []).append({"threads_reply": str(e)})
                save_json(result_path, result)
                return 1

    # ---- 마무리: 큐에서 제거 ---------------------------------------------
    done_ig = (not do_ig) or bool(result.get("instagram_post_id"))
    done_th = (not do_th) or bool(result.get("threads_post_id"))
    if done_ig and done_th and args.target == "both":
        qpath.unlink(missing_ok=True)
        log.info("큐 파일 제거: %s", qpath.name)
    result["completed_at"] = datetime.now(KST).isoformat()
    save_json(result_path, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
