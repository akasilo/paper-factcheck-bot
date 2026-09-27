"""
유튜브 업로드용 OAuth 토큰을 **한 번만** 받는 도우미. PC 에서 직접 돌린다 (표준 라이브러리만 씀).

    python bot/yt_auth.py --client-secret 경로/client_secret_xxx.json
    python bot/yt_auth.py --client-secret ... --save _secrets_do_not_upload/secrets.env

하는 일
  1. 브라우저를 열어 구글 계정 → 유튜브 채널 선택 → "허용" 을 누르게 한다.
  2. 돌아온 코드를 refresh token 으로 바꾼다.
  3. YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN 세 값을 화면에 보여준다
     (--save 를 주면 그 파일 끝에도 덧붙인다). 이 값들을 GitHub Secrets 에 넣으면 끝.
  4. 어느 채널이 연결됐는지 이름을 보여준다 (엉뚱한 채널을 고르지 않았는지 확인용).

미리 해 둘 것 (Google Cloud Console, 한 번만)
  - Google TTS 쓰는 그 프로젝트에서 "YouTube Data API v3" 사용 설정
  - OAuth 동의 화면: 외부(External), 게시 상태를 **프로덕션** 으로 (테스트 상태면 토큰이 7일마다 죽는다).
    검증(verification)은 안 받아도 된다 — 동의할 때 "확인되지 않은 앱" 경고만 뜨고 넘어갈 수 있다.
  - 사용자 인증 정보 → OAuth 클라이언트 ID 만들기 → 유형 **데스크톱 앱** → JSON 다운로드
  - 공개 업로드를 하려면 별도로 "YouTube API Services 컴플라이언스 심사" 양식을 제출해야 한다.
    심사 전에는 API 로 올린 영상이 강제 비공개가 된다 (YT_PRIVACY=private 이 기본인 이유).
"""
from __future__ import annotations

import argparse
import json
import secrets
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def load_client(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    node = data.get("installed") or data.get("web") or data
    cid, sec = node.get("client_id", ""), node.get("client_secret", "")
    if not cid or not sec:
        raise SystemExit("client_secret JSON 에 client_id / client_secret 이 없습니다. "
                         "OAuth 클라이언트 유형이 '데스크톱 앱' 인지 확인하세요.")
    return cid, sec


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Catch(BaseHTTPRequestHandler):
    code: str = ""
    state: str = ""
    error: str = ""
    expect_state: str = ""

    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("state", [""])[0] != _Catch.expect_state:
            _Catch.error = "state 가 다릅니다 (다른 창에서 온 응답?)"
        elif q.get("error"):
            _Catch.error = q["error"][0]
        else:
            _Catch.code = q.get("code", [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "허용됐습니다. 이 창을 닫고 터미널로 돌아가세요." if _Catch.code else f"실패: {_Catch.error}"
        self.wfile.write(f"<html><body style='font-family:sans-serif'><h3>{msg}</h3></body></html>"
                         .encode("utf-8"))

    def log_message(self, *a):  # 조용히
        pass


def post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"토큰 교환 실패 {e.code}: {e.read().decode()[:400]}")


def channel_name(access: str) -> str:
    req = urllib.request.Request(CHANNELS_URL + "?part=snippet&mine=true",
                                 headers={"Authorization": f"Bearer {access}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return f"(채널 조회 실패 {e.code} — YouTube Data API 가 켜져 있는지 확인)"
    items = data.get("items") or []
    if not items:
        return "(이 계정에 채널이 없습니다 — 허용 화면에서 채널을 골랐는지 확인)"
    return items[0].get("snippet", {}).get("title", "?")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-secret", required=True, help="Cloud Console 에서 받은 client_secret_*.json")
    ap.add_argument("--save", default="", help="이 파일 끝에 YT_* 세 줄을 덧붙인다 (예: _secrets_do_not_upload/secrets.env)")
    ap.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않고 URL 만 보여준다")
    args = ap.parse_args()

    cid, sec = load_client(Path(args.client_secret))
    port = free_port()
    redirect = f"http://127.0.0.1:{port}/"
    state = secrets.token_urlsafe(16)
    _Catch.expect_state = state

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",     # refresh token 을 받으려면 필수
        "prompt": "consent",          # 이미 허용한 적 있어도 refresh token 을 다시 내주게
        "state": state,
        "include_granted_scopes": "true",
    })

    srv = HTTPServer(("127.0.0.1", port), _Catch)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()

    print("\n브라우저에서 구글 계정으로 로그인하고, **올릴 유튜브 채널** 을 고른 뒤 '허용' 을 누르세요.")
    print("'확인되지 않은 앱' 경고가 뜨면 '고급 → 이동' 으로 넘어가면 됩니다.\n")
    if args.no_browser or not webbrowser.open(url):
        print("이 주소를 브라우저에 붙여 넣으세요:\n" + url + "\n")
    t.join(timeout=300)
    srv.server_close()

    if _Catch.error or not _Catch.code:
        raise SystemExit(f"허용을 받지 못했습니다: {_Catch.error or '응답 없음 (5분 초과)'}")

    tok = post_form(TOKEN_URL, {
        "code": _Catch.code, "client_id": cid, "client_secret": sec,
        "redirect_uri": redirect, "grant_type": "authorization_code",
    })
    refresh = tok.get("refresh_token", "")
    if not refresh:
        raise SystemExit("refresh_token 이 안 왔습니다. Cloud Console 에서 이 앱의 권한을 한 번 삭제한 뒤 "
                         "(myaccount.google.com/permissions) 다시 실행하세요.")

    name = channel_name(tok.get("access_token", ""))

    lines = [f"YT_CLIENT_ID={cid}", f"YT_CLIENT_SECRET={sec}", f"YT_REFRESH_TOKEN={refresh}"]
    print("=" * 70)
    print(f"연결된 채널: {name}")
    print("아래 세 값을 GitHub 저장소 → Settings → Secrets and variables → Actions 에 넣으세요.")
    print("(YT_PRIVACY 는 Variables 에 private 로 두고, 심사 통과 뒤 public 으로 바꾸면 됩니다)")
    print("=" * 70)
    for ln in lines:
        print(ln)
    print("=" * 70)

    if args.save:
        p = Path(args.save)
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        with p.open("a", encoding="utf-8") as f:
            if old and not old.endswith("\n"):
                f.write("\n")
            f.write("\n# YouTube (bot/yt_auth.py, 채널: " + name + ")\n")
            f.write("\n".join(lines) + "\n")
        print(f"→ {p} 끝에 덧붙였습니다. 이 파일은 절대 업로드하지 마세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
