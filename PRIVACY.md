# 개인정보처리방침 / Privacy Policy — paper_factcheck bot

최종 갱신: 2026-09-27

## 이 앱은 무엇인가

**paper_factcheck bot** 은 최신 의학·과학 논문을 근거로 일상 제품을 팩트체크하는 카드뉴스와 짧은 영상을 만들어,
운영자 본인의 소셜 계정(Instagram `@paper_factcheck`, Threads, YouTube)에 자동으로 게시하는 **개인 운영 자동화 도구**입니다.
일반 사용자를 위한 서비스가 아니며, 운영자 한 사람만 사용합니다.

## 수집하는 정보

- **일반 사용자의 개인정보는 전혀 수집하지 않습니다.** 이 앱에는 가입·로그인·방문자 추적 기능이 없습니다.
- 앱이 다루는 유일한 계정 정보는 **운영자 본인의** YouTube 채널에 대한 OAuth 접근 토큰입니다.

## Google 사용자 데이터 사용 (YouTube Data API)

- 요청 범위: `youtube.upload`(운영자 채널에 영상 업로드), `youtube.readonly`(같은 영상을 중복 업로드하지 않기 위한 최근 업로드 목록 조회)
- 용도: 봇이 만든 영상을 **운영자 본인의 채널에만** 업로드합니다. 다른 채널·다른 사용자의 데이터는 읽거나 쓰지 않습니다.
- 저장: OAuth 토큰은 GitHub Actions Secrets 에 암호화되어 저장되며, 코드·로그·공개 저장소에 기록되지 않습니다.
- 공유: 어떤 제3자에게도 데이터를 전달·판매하지 않습니다.
- 삭제: 운영자는 https://myaccount.google.com/permissions 에서 언제든 이 앱의 접근 권한을 철회할 수 있으며, 철회 즉시 토큰은 무효가 됩니다.

이 앱의 Google API 사용은 [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)
(Limited Use 요건 포함)를 따릅니다.

## 게시물에 포함되는 것

게시물에는 논문 출처(저자·저널·DOI·PubMed 링크)와 쿠팡 파트너스 제휴 링크가 포함될 수 있으며, 제휴 링크는 게시물 안에 고지합니다.
게시물은 AI 의 도움을 받아 제작되며 그 사실을 게시물에 표시합니다.

## 문의

운영자 이메일: cbgsub01@gmail.com
저장소: https://github.com/akasilo/paper-factcheck-bot

---

# Privacy Policy (English)

**paper_factcheck bot** is a personal automation tool operated by a single person. It generates fact-check card news and short
videos based on recent peer-reviewed papers and posts them to the operator's own social accounts (Instagram, Threads, YouTube).

- It does **not** collect, store, or process any personal data from the general public. There is no sign-up, login, or visitor tracking.
- The only account data it handles is the operator's own YouTube OAuth token, used solely to upload videos to the operator's own channel
  (`youtube.upload`) and to list the channel's recent uploads to avoid duplicates (`youtube.readonly`).
- Tokens are stored encrypted in GitHub Actions Secrets and never written to code, logs, or the public repository.
- No data is shared with or sold to third parties.
- The operator can revoke access at any time at https://myaccount.google.com/permissions.
- Use of Google APIs complies with the Google API Services User Data Policy, including the Limited Use requirements.

Contact: cbgsub01@gmail.com
