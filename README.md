# paper-factcheck-bot

인스타그램 `@paper_factcheck` + Threads 에 카드뉴스를 매일 자동으로 올리는 봇.

## 흐름

큐는 **날짜가 아니라 순서**로 관리한다. 초안이 `queue/0007-airfryer.json` 처럼 쌓여 있고,
매일 아침 그중 링크가 채워진 가장 오래된 하나가 나간다. 그래서 하루를 건너뛰어도 빈 날이 생기지 않는다.

```
매일 19:43 KST  draft-post.yml   ── 대기 초안을 목표 개수(기본 10개)까지 채운다
  bot/fill_pool.py  → 부족한 만큼 반복:
      bot/find_papers.py  ── PubMed 검색 + LLM 판정기가 제품 직접성 기준으로 논문 선택
      bot/draft_post.py   ── Claude(구독 토큰)로 카드 원고·캡션·쓰레드 본문 → queue/NNNN-<주제>.json
  bot/render_cards.py ── 카드 이미지 렌더 → images/<주제>/
  bot/sheet_sync.py --push ── 구글시트에 행 추가 (주제·훅·추천 제품 조건·논문·미리보기)
        │
        ▼  사용자가 시트의 '쿠팡 링크' 칸을 틈틈이 채운다 (건너뛰려면 skip)
        │
        ▼  매일 07:37 KST  daily-post.yml
  bot/sheet_sync.py --pull ── 링크 → approved:true / skip → skipped/ 로 이동
  bot/publish.py           ── 가장 오래된 승인 초안 하나를
                                인스타 캐러셀 → Threads 캐러셀 → Threads 링크 답글
  bot/sheet_sync.py --status ── 시트 '상태' 칸을 게시완료/건너뜀 으로
        │
        ▼
posted/YYYY-MM-DD.json  (게시물 ID·시간 기록, 큐 파일은 삭제)
```

- 링크가 채워진 초안이 하나도 없으면 **아무것도 올리지 않고** 조용히 끝납니다.
- 단계마다 결과를 바로 기록하므로 중간에 실패해도 재실행하면 이미 올린 것은 건너뜁니다.
- 이미지는 인스타/Threads 서버가 직접 가져가야 하므로 **공개 URL** 이어야 합니다.
  기본값은 이 저장소의 raw URL(`IMAGE_BASE_URL`)이며, 저장소가 public 일 때만 동작합니다.
- `SHEET_URL` 을 비워두면 시트 대신 예전 방식(GitHub 이슈 댓글 승인, `approve.yml`)으로 돌아갑니다.

## 구글시트 연결

`_sheet/Code.gs` 를 구글시트의 **확장 프로그램 → Apps Script** 에 붙여넣고, 맨 위 `TOKEN` 을
아무 긴 문자열로 바꾼 뒤 **배포 → 새 배포 → 웹 앱(실행: 나 / 액세스: 모든 사용자)** 으로 배포한다.
나온 URL 과 TOKEN 을 GitHub Secrets 의 `SHEET_URL`, `SHEET_TOKEN` 에 넣으면 끝.

시트 열: `id · 상태 · 주제 · 훅 · 추천 제품 조건 · 쿠팡 링크 · 논문 · 카드 미리보기 · 만든날짜`
— 봇이 나머지를 채우고, 사람은 **'쿠팡 링크' 한 칸만** 채운다. 건너뛰려면 그 칸에 `skip`.

Code.gs 를 고쳤으면 **배포 → 배포 관리 → 편집 → 버전: 새 버전 → 배포** 를 해야 반영된다.

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

## 데이터

| 파일 | 설명 |
|---|---|
| `data/topics.json` | 주제 목록. `id`, `ko`(표시명), `query`(PubMed 검색식), `hint`(대안 제품 조건). 자유롭게 추가·삭제 |
| `data/topic_state.json` | 주제별 마지막 사용일 (봇이 갱신) |
| `data/used_papers.json` | 이미 쓴 논문 (봇이 갱신) — 같은 논문을 다시 고르지 않음 |
| `skipped/` | 시트에서 `skip` 처리한 초안 |

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
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude 구독 토큰 (`claude setup-token`) — 원고 작성 기본 |
| `SHEET_URL`, `SHEET_TOKEN` | 구글시트 Apps Script 웹 앱 URL / 토큰 (없으면 이슈 방식) |
| `LLM_API_KEY` | (대체) Groq 등 OpenAI 호환 LLM 키 |
| `GEMINI_API_KEY` | (선택) Google AI Studio API 키 — `LLM_PROVIDER=gemini` 일 때, 나중에 배경 이미지 |
| `REPO_PAT` (선택) | Actions secrets 쓰기 권한 PAT. 있으면 토큰 갱신 시 Secrets 자동 업데이트 |

원고 작성 LLM 은 기본 **Claude 구독(Claude Code OAuth 토큰)**. `claude setup-token` 으로 만든 토큰을 `CLAUDE_CODE_OAUTH_TOKEN` 에 넣으면 API 과금 없이 구독 사용량으로 돌아간다. 토큰이 없으면 Groq(`LLM_API_KEY`)로 자동 대체.
저장소 변수(Variables): `LLM_PROVIDER`(`groq`|`openrouter`|`cerebras`|`openai`|`openai_compat`|`gemini`|`github`),
`LLM_MODEL`(쉼표로 여러 개 → 앞에서부터 시도), `LLM_BASE_URL`(openai_compat), `GEMINI_MODEL`.

## 워크플로

| 이름 | 언제 | 하는 일 |
|---|---|---|
| `check-tokens` | 수동 | Secrets 가 맞는지 `/me` 호출로 확인 (게시 안 함) |
| `draft-post` | 매일 19:43 KST / 수동 | 대기 초안을 목표 개수까지 보충 → 렌더 → 시트에 행 추가 |
| `approve` | 승인 이슈 댓글 | (시트를 안 쓸 때만) 링크 댓글 → 승인, `skip` → 건너뜀 |
| `render-cards` | `queue/*.json` push 시 / 수동 | `cards` → 카드뉴스 이미지 렌더 후 커밋 |
| `daily-post` | 매일 07:37 KST / 수동 | 시트에서 링크 받아 가장 오래된 승인 초안 하나 게시 |
| `refresh-tokens` | 매주 월요일 / 수동 | 60일 토큰 연장 |

저장소에 커밋하는 워크플로는 모두 `bot/commit_push.sh` 를 거친다. 두 워크플로가 동시에 push 해서 거부되면 원격을 다시 받아 자기 변경만 다시 얹어 재시도한다.

## 로컬 실행

```bash
pip install -r requirements.txt
export IG_USER_ID=... IG_TOKEN=... THREADS_USER_ID=... THREADS_TOKEN=...
export IMAGE_BASE_URL=https://raw.githubusercontent.com/akasilo/paper-factcheck-bot/main/
python bot/check_tokens.py
python bot/publish.py --dry-run
```
