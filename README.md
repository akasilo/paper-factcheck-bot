# paper-factcheck-bot

인스타그램 `@paper_factcheck` + Threads 에 카드뉴스를 매일 자동으로 올리는 봇.

## 흐름

```
queue/YYYY-MM-DD.json  (approved: true + link 입력)
        │
        ▼  매일 08:00 KST (.github/workflows/daily-post.yml)
bot/publish.py ── 인스타 캐러셀 게시
               ├─ Threads 캐러셀 게시
               └─ Threads 게시물에 추천 링크 답글
        │
        ▼
posted/YYYY-MM-DD.json  (게시물 ID·시간 기록, 큐 파일은 삭제)
```

- 큐 항목이 `approved: true` 가 아니거나 `link` 가 비어 있으면 **아무것도 올리지 않고** 조용히 끝납니다.
- 단계마다 결과를 바로 기록하므로 중간에 실패해도 재실행하면 이미 올린 것은 건너뜁니다.
- 이미지는 인스타/Threads 서버가 직접 가져가야 하므로 **공개 URL** 이어야 합니다.
  기본값은 이 저장소의 raw URL(`IMAGE_BASE_URL`)이며, 저장소가 public 일 때만 동작합니다.

## 큐 파일 형식

`queue/_template.json` 참고. 필수 필드:

| 필드 | 설명 |
|---|---|
| `approved` | `true` 로 바꿔야 게시됨 |
| `link` | 쿠팡파트너스 등 추천 링크 (Threads 답글에 들어감) |
| `cards` | 카드뉴스 원고 (아래 참고). 있으면 `render-cards` 워크플로가 이미지를 만들어 `images` 를 채움 |
| `images` | 저장소 내 이미지 경로 2~10장 (또는 `image_urls` 로 절대 URL). `cards` 가 있으면 자동 생성 |
| `instagram_caption` | 인스타 캡션 (2200자 이내) |
| `threads_text` | Threads 본문 (500자 이내) |
| `threads_reply` | 답글 템플릿. `{link}` 자리에 링크가 들어감. 쿠팡 파트너스 고지문이 없으면 자동으로 붙음 |

## 카드뉴스 (`cards`)

`render/card.html` 템플릿 + `bot/render_cards.py` 가 1080×1350 JPEG 를 만든다. 글꼴은 Pretendard(OFL).

| type | 필드 | 설명 |
|---|---|---|
| `hook` | `text`, `kicker`(선택), `bg`(선택) | 첫 장. 큰 제목 + "넘겨보기" |
| `body` | `text`, `note`(선택), `bg`(선택) | 본문. 최대 6~7줄 |
| `source` | `paper{title,journal,year,doi,authors}`, `cta`(선택), `disclaimer`(선택) | 마지막 장. 출처 + 팔로우 CTA |

텍스트 마크업: `[[노란 형광]]`, `{{빨간 글자}}`, 줄바꿈 `\n`. 글이 길면 자동으로 글자 크기를 줄인다.
`bg` 가 없거나 파일이 없으면 은은한 배경을 자동 생성해 `images/<date>/bgNN.jpg` 로 저장한다 (나중에 Gemini 생성 이미지로 대체 예정).

```bash
python bot/render_cards.py --demo                # images/demo/ 에 샘플 7장
python bot/render_cards.py --date 2026-09-07     # queue/2026-09-07.json 렌더 → images/2026-09-07/, queue 의 images 갱신
python bot/render_cards.py --auto                # 이미지 없는 큐 항목 전부
```

## GitHub Secrets

| 이름 | 값 |
|---|---|
| `IG_APP_ID`, `IG_APP_SECRET` | Meta 앱 → Instagram 앱 ID / 시크릿 |
| `IG_USER_ID` | `graph.instagram.com/me` 의 `user_id` |
| `IG_TOKEN` | 인스타 장기 토큰 (60일) |
| `THREADS_APP_ID`, `THREADS_APP_SECRET` | Meta 앱 → Threads 앱 ID / 시크릿 |
| `THREADS_USER_ID` | Threads 토큰 교환 시 나온 `user_id` |
| `THREADS_TOKEN` | Threads 장기 토큰 (60일) |
| `REPO_PAT` (선택) | Actions secrets 쓰기 권한 PAT. 있으면 토큰 갱신 시 Secrets 자동 업데이트 |

## 워크플로

| 이름 | 언제 | 하는 일 |
|---|---|---|
| `check-tokens` | 수동 | Secrets 가 맞는지 `/me` 호출로 확인 (게시 안 함) |
| `render-cards` | `queue/*.json` push 시 / 수동 | `cards` → 카드뉴스 이미지 렌더 후 커밋 |
| `daily-post` | 매일 08:00 KST / 수동 | 오늘 큐 항목 게시. 수동 실행 시 날짜·대상·dry-run 선택 가능 |
| `refresh-tokens` | 매주 월요일 / 수동 | 60일 토큰 연장 |

## 로컬 실행

```bash
pip install -r requirements.txt
export IG_USER_ID=... IG_TOKEN=... THREADS_USER_ID=... THREADS_TOKEN=...
export IMAGE_BASE_URL=https://raw.githubusercontent.com/akasilo/paper-factcheck-bot/main/
python bot/check_tokens.py
python bot/publish.py --dry-run
```
