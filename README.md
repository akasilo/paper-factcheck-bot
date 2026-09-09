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
        ▼  매일 아침 05:08~09:38 KST 사이 30분마다 깨어남  daily-post.yml
  bot/gate.py              ── POST_AFTER(기본 08:00) 이후 + 오늘 아직 안 올렸을 때만 통과
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

시트 열:
`id · 상태 · 주제 · 훅 · 추천 제품 조건 · 쿠팡 링크 · 논문 제목 · 저널·연도 · PMID · DOI · 링크 · 근거 · 카드 미리보기 · 만든날짜`

봇이 나머지를 채우고, 사람은 **'쿠팡 링크' 한 칸만** 채운다. 건너뛰려면 그 칸에 `skip`.

**'근거' 칸**이 이 원고가 뭘 보고 쓰였는지 알려준다.

| 값 | 뜻 | 할 일 |
|---|---|---|
| `PMC 전문` | 논문 전문을 자동으로 받아 썼다 | 없음 |
| `직접 넣음` | `papers/<PMID>.txt` 로 넣어준 본문을 썼다 | 없음 |
| `초록만` | 초록만 보고 썼다 | 훅 대비 내용이 얕으면 본문을 넣어주면 다시 쓴다 |

`초록만` 인 줄이 부실해 보이면 — 그 줄의 **DOI/PMID** 로 논문을 찾아 본문을 텍스트로 저장하고
`papers/<PMID>.txt` 로 저장소에 올리면 된다. push 되는 순간 `redraft` 워크플로가
그 초안을 본문 근거로 다시 쓰고, 카드를 다시 그리고, 시트의 훅·근거까지 갱신한다.
**id·승인 여부·쿠팡 링크는 그대로 유지된다.**

기존 시트에 열을 새로 추가해도 Code.gs 가 알아서 끼워 넣는다. 예전 `논문` 열은 `논문 제목` 으로
이름만 바뀌고, 채워둔 쿠팡 링크·미리보기·만든날짜는 제자리에 남는다. 여러 번 배포해도 안전하다.
값 자체는 다음 `sheet_sync.py --refresh` 때 큐 기준으로 다시 채워진다.

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
| `papers/<PMID>.txt` | (선택) 직접 넣는 논문 본문. 유료 저널이라 PMC 전문이 없을 때 |
| `skipped/` | 시트에서 `skip` 처리한 초안 |

## 근거 — 초록만으로 부족할 때

초록이 "이런 주제들을 다뤘다"만 적혀 있고 결론이 없는 논문이 있다. 그런 초록으로 원고를 쓰면
훅에서 질문만 던지고 답을 못 하는 게시물이 나온다 (2026-09-09 단백질 보충제 건).

막는 장치가 세 겹이다.

1. **논문 고를 때** (`find_papers.py`) — 판정기가 `conclusion` 점수를 따로 매긴다.
   초록에 결론이 없고 **전문도 못 보는** 논문은 탈락.
2. **본문 확보** (`bot/fulltext.py`) — 초록에 없어도 본문에서 답을 가져온다.
   `papers/<PMID>.txt` 가 있으면 그걸 먼저 쓰고, 없으면 **PMC 전문**을 자동으로 받는다 (무료, 키 불필요).
   결론·논의 섹션부터 골라 16,000자까지 담아 원고 프롬프트에 붙인다.
3. **원고 검증** (`draft_post.py`) — 원고에 `answer`(훅의 질문에 대한 답 한 문장)와
   `answer_card`(그 답이 든 카드 번호)를 반드시 쓰게 하고, 검증에서 확인한다.
   답이 "다뤘다·검토했다" 류 목차 문장이거나 지목한 카드에 없으면 탈락.
   답을 못 쓰겠으면 모델이 `{"abort": true}` 로 스스로 물러나고, 편집자는 다른 논문으로 넘어간다.

```bash
python bot/fulltext.py 38626029     # 이 논문 전문을 볼 수 있는지, 발췌가 어떻게 나오는지 확인
```

## 카드뉴스 (`cards`)

`render/card.html` 템플릿 + `bot/render_cards.py` 가 1080×1350 JPEG 를 만든다. 글꼴은 Pretendard(OFL).

| type | 필드 | 설명 |
|---|---|---|
| `hook` | `text`, `kicker`(선택), `bg`(선택) | 첫 장. 큰 제목 + "넘겨보기" |
| `body` | `text`, `note`(선택), `bg`(선택) | 본문. 최대 6~7줄 |
| `source` | `paper{title,journal,year,doi,authors}`, `cta`(선택), `disclaimer`(선택) | 마지막 장. 출처 + 팔로우 CTA |

텍스트 마크업: `[[노란 형광]]`, `{{빨간 글자}}`, 줄바꿈 `\n`. 글이 길면 자동으로 글자 크기를 줄인다.
`bg` 가 없거나 파일이 없으면 배경을 자동 생성해 `images/<주제>/bgNN.jpg` 로 저장한다.

### 카드 스타일 (`style` / `accent`)

큐 항목의 `style` 과 `accent` 로 글의 성격에 맞게 디자인을 바꾼다 (`bot/card_styles.py`).
원고를 쓸 때 Claude 가 함께 골라 넣고, 값이 없는 옛 초안은 주제·verdict 로 자동 선택한다.

| style | 언제 | 모습 |
|---|---|---|
| `geo` | 위험·오염·수치 폭로 (경고 톤) | 어두운 배경 + 격자·원호·사선 그래픽 |
| `paper` | 괴담 반박·안전성 해명 (차분한 톤) | 밝은 종이 질감 + 검은 글씨 |
| `soft` | 수면·피부·기분 등 몸 이야기 | 은은한 색번짐 |

`accent`: `yellow`(기본) `blue` `green` `orange` `violet` `red` — 형광펜·포인트 색이 바뀐다.

```bash
python bot/render_cards.py --demo --style paper --accent green   # images/demo-paper/ 에 샘플 7장
python bot/render_cards.py --date 2030-plastic_container         # 그 큐 항목 렌더 → images/<id>/, queue 의 images 갱신
python bot/render_cards.py --auto                               # 이미지 없는 큐 항목 전부
python bot/render_cards.py --auto --force                       # 배경까지 전부 다시 (스타일 바꾼 뒤)
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
| `redraft` | `papers/*.txt` push 시 / 수동 | 받은 본문으로 해당 초안을 다시 쓰고 렌더·시트 갱신 |
| `daily-post` | 아침에 30분마다 깨어남 / 수동 | 문지기 통과 시 시트에서 링크 받아 가장 오래된 승인 초안 하나 게시 |
| `refresh-tokens` | 매주 월요일 / 수동 | 60일 토큰 연장 |

### 게시 시각

GitHub 예약 실행은 뜨는 시각이 들쭉날쭉하다 — 07:37 로 걸어도 09:30 에 뜬 날이 이틀 연속 있었다.
그래서 아침 시간대에 **30분마다 여러 번 깨우고**, 실제 게시는 `bot/gate.py` 가 하루 한 번으로 묶는다.

- `POST_AFTER`(daily-post.yml 상단, 기본 `08:00` KST) 이후 처음 깨어난 실행이 그날의 게시를 담당
- 오늘 이미 올린 기록(`posted/<날짜>*.json`)이 있으면 그냥 종료
- 문지기는 표준 라이브러리만 쓰므로 `pip install` 전에 돈다 → 대부분의 깨우기는 15초 만에 끝난다
- **게시 시각을 바꾸려면 `POST_AFTER` 만 고치면 된다.** cron 은 깨우는 창일 뿐
- 수동 실행은 문지기를 그냥 통과한다. 같은 날 두 번 올리려면 `again` 을 켠다

저장소에 커밋하는 워크플로는 모두 `bot/commit_push.sh` 를 거친다. 두 워크플로가 동시에 push 해서 거부되면 원격을 다시 받아 자기 변경만 다시 얹어 재시도한다.

## 로컬 실행

```bash
pip install -r requirements.txt
export IG_USER_ID=... IG_TOKEN=... THREADS_USER_ID=... THREADS_TOKEN=...
export IMAGE_BASE_URL=https://raw.githubusercontent.com/akasilo/paper-factcheck-bot/main/
python bot/check_tokens.py
python bot/publish.py --dry-run
```
