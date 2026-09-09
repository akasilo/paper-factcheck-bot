/**
 * paper_factcheck 봇 ↔ 구글시트 연결기 (Google Apps Script)
 *
 * 쓰는 법
 *  1. 구글시트를 하나 새로 만든다.
 *  2. 확장 프로그램 → Apps Script → 기존 코드 다 지우고 이 파일 전체를 붙여넣는다.
 *  3. 아래 TOKEN 을 아무 긴 문자열로 바꾼다 (비밀번호 같은 것. 남이 못 맞출 것으로).
 *  4. 저장 → 배포 → 새 배포 → 유형 "웹 앱"
 *       - 실행 계정: 나
 *       - 액세스 권한: 모든 사용자
 *     → 배포 → 권한 승인 → 나오는 웹 앱 URL 을 복사한다.
 *  5. GitHub 저장소 Settings → Secrets → Actions 에 두 개 등록:
 *       SHEET_URL   = 방금 복사한 웹 앱 URL
 *       SHEET_TOKEN = 3번에서 정한 문자열
 *
 * 코드를 고치면 반드시 "배포 → 배포 관리 → 편집(연필) → 버전: 새 버전 → 배포" 를 해야
 * 반영된다 (그냥 저장만 하면 예전 버전이 계속 돈다).
 */

const TOKEN = 'CHANGE_ME_아무_긴_문자열로_바꾸세요';
const SHEET_NAME = '게시목록';

const HEADERS = [
  'id', '상태', '주제', '훅', '추천 제품 조건', '쿠팡 링크',
  '논문 제목', '저널·연도', 'PMID', 'DOI', '링크', '근거', '카드 미리보기', '만든날짜'
];

// 예전 열 이름 → 새 이름 (이미 쓰던 시트를 자동으로 맞춰준다)
const RENAMES = { '논문': '논문 제목' };

// '근거' 칸 뜻
//   PMC 전문   — 논문 전문을 자동으로 받아 원고를 썼다 (가장 든든)
//   직접 넣음  — papers/<PMID>.txt 로 넣어준 본문을 썼다
//   초록만     — 초록만 보고 썼다. 내용이 얕아 보이면 본문을 넣어주면 다시 쓴다

/** 시트를 가져오고, 없으면 머리글까지 만들어 둔다. */
function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) sh = ss.insertSheet(SHEET_NAME);

  if (sh.getLastRow() === 0) {
    sh.appendRow(HEADERS);
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, HEADERS.length)
      .setFontWeight('bold')
      .setBackground('#f0f0f0');
    // 보기 좋으라고 열 너비만 잡아둔다
    const widths = [110, 80, 110, 320, 260, 300, 340, 150, 95, 190, 210, 110, 130, 100];
    widths.forEach((w, i) => sh.setColumnWidth(i + 1, w));
    sh.getRange(1, 1, sh.getMaxRows(), HEADERS.length).setVerticalAlignment('top');
    sh.getRange(2, 4, sh.getMaxRows() - 1, 2).setWrap(true);  // 훅 / 추천 조건 줄바꿈
  } else {
    ensureHeaders_(sh);
  }
  return sh;
}

/**
 * 이미 쓰고 있던 시트에 새 열(DOI·PMID·근거 등)을 끼워 넣는다.
 * 기존 열은 건드리지 않고, HEADERS 에 있는데 시트에 없는 것만 제자리에 삽입한다.
 * (열을 지우거나 옮기지 않으므로 이미 채워둔 쿠팡 링크는 그대로 남는다)
 */
function ensureHeaders_(sh) {
  const lastCol = Math.max(sh.getLastColumn(), 1);
  let cur = sh.getRange(1, 1, 1, lastCol).getValues()[0].map(function (v) { return String(v).trim(); });

  // 이름만 바뀐 열은 새로 만들지 말고 머리글만 고쳐 쓴다 (그 아래 값은 그대로 둔다)
  Object.keys(RENAMES).forEach(function (oldName) {
    const at = cur.indexOf(oldName);
    if (at !== -1 && cur.indexOf(RENAMES[oldName]) === -1) {
      sh.getRange(1, at + 1).setValue(RENAMES[oldName]);
      cur[at] = RENAMES[oldName];
    }
  });

  let inserted = 0;
  for (let i = 0; i < HEADERS.length; i++) {
    if (cur[i] === HEADERS[i]) continue;
    if (cur.indexOf(HEADERS[i]) !== -1) continue;   // 어딘가 있으면 순서만 다른 것 — 건드리지 않는다
    sh.insertColumnBefore(i + 1);
    sh.getRange(1, i + 1).setValue(HEADERS[i]).setFontWeight('bold').setBackground('#f0f0f0');
    cur.splice(i, 0, HEADERS[i]);
    inserted++;
  }
  return inserted;
}

/** 시트 1행에 실제로 적힌 머리글 (열 순서가 달라도 안전하게 읽고 쓰기 위해) */
function headerRow_(sh) {
  const lastCol = Math.max(sh.getLastColumn(), 1);
  return sh.getRange(1, 1, 1, lastCol).getValues()[0].map(function (v) { return String(v).trim(); });
}

/** 데이터 행을 객체 배열로 (내부용, _row 포함) */
function rows_() {
  const sh = sheet_();
  const last = sh.getLastRow();
  if (last < 2) return [];
  const head = headerRow_(sh);
  const values = sh.getRange(2, 1, last - 1, head.length).getValues();
  return values.map(function (r, i) {
    const o = { _row: i + 2 };
    head.forEach(function (h, c) { if (h) o[h] = r[c]; });
    return o;
  });
}

function out_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/** 봇이 목록을 읽어간다: GET ?token=...  */
function doGet(e) {
  const p = (e && e.parameter) || {};
  if (String(p.token || '') !== TOKEN) return out_({ error: 'bad token' });
  const rows = rows_().map(function (r) {
    const o = {};
    HEADERS.forEach(function (h) { o[h] = r[h]; });
    return o;
  });
  return out_({ rows: rows });
}

/** 봇이 새 초안을 붙이거나 상태를 바꾼다: POST {token, action, ...} */
function doPost(e) {
  let body = {};
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    return out_({ error: 'bad json' });
  }
  if (String(body.token || '') !== TOKEN) return out_({ error: 'bad token' });

  const sh = sheet_();

  // 새 초안 여러 개 추가 (이미 있는 id 는 건너뜀 → 여러 번 불러도 안전)
  if (body.action === 'add') {
    const have = {};
    rows_().forEach(function (r) { have[String(r.id)] = true; });
    let added = 0;
    (body.rows || []).forEach(function (r) {
      if (have[String(r.id)]) return;
      sh.appendRow(headerRow_(sh).map(function (h) {
        return (!h || r[h] === undefined || r[h] === null) ? '' : r[h];
      }));
      have[String(r.id)] = true;
      added++;
    });
    return out_({ added: added });
  }

  // 상태 바꾸기 (게시완료 / 건너뜀)
  if (body.action === 'status') {
    const t = rows_().filter(function (r) { return String(r.id) === String(body.id); })[0];
    if (!t) return out_({ error: 'no such id: ' + body.id });
    const col = headerRow_(sh).indexOf('상태') + 1;
    sh.getRange(t._row, col || 2).setValue(String(body.status || ''));
    return out_({ ok: true });
  }

  // 이미 있는 행의 몇 칸만 고쳐 쓴다 (본문 받아 원고를 다시 썼을 때)
  //   {action:'update', id:'2031-sunscreen', fields:{'훅':'...', '근거':'직접 넣음'}}
  // '쿠팡 링크' 는 사람이 채우는 칸이라 절대 덮어쓰지 않는다.
  if (body.action === 'update') {
    const t = rows_().filter(function (r) { return String(r.id) === String(body.id); })[0];
    if (!t) return out_({ error: 'no such id: ' + body.id });
    const head = headerRow_(sh);
    let n = 0;
    Object.keys(body.fields || {}).forEach(function (k) {
      if (k === '쿠팡 링크' || k === 'id') return;
      const c = head.indexOf(k);
      if (c === -1) return;
      sh.getRange(t._row, c + 1).setValue(body.fields[k]);
      n++;
    });
    return out_({ ok: true, updated: n });
  }

  return out_({ error: 'unknown action: ' + body.action });
}
