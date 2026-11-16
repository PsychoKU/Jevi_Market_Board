# 제비리서치센터 증시 보드

미국·한국·일본 증시 보드. GitHub Actions가 약 15분마다 데이터를 모아 GitHub Pages에 올립니다.

## 갱신 방식
| 구분 | 시점 | 내용 |
|---|---|---|
| 장중 갱신 | 각 시장 개장 20분 전 ~ 마감 25분 후, 15분마다 | 전 종목 시세, 지수 차트, 투자자별 매매, 공시, 뉴스, 미국 옵션, 한국 선물·옵션(한투 API) |
| 마감 갱신 | 마감 30분 뒤 하루 한 번 | 종목 목록·업종, 2년 일봉, 52주 신고가 추이, 밸류에이션, 애널리스트 추정치 |
| 해설 | 하루 한 번 (Claude 예약 작업) | `digest/`를 읽고 `content/{us,kr,jp}.json`의 요약·투자 아이디어·뉴스 번역·추정치/파생 메모를 갱신 |

## 저장소 비밀 값 (Settings → Secrets and variables → Actions)
- `SEC_UA` — SEC EDGAR 접속용 연락처 이메일 (SEC 규정상 필요)
- `KIS_APP_KEY`, `KIS_APP_SECRET` — 한국투자증권 Open API (시세 조회 전용, 주문 기능 없음)

## 구조
- `pipeline/` 수집·가공 (Python). `run.py`가 진입점
- `site/index.html` 보드 화면, `site/data/` 생성 데이터 (커밋하지 않음, Actions 캐시에 보관)
- `content/` Claude가 쓰는 해설, `digest/` 해설용 요약 데이터

수동 실행: Actions → board → Run workflow (`force`: `us,kr,jp` 또는 `all`, `full`: 전체 갱신).

© 2026 제비리서치센터 by JEVI. 데이터 출처는 화면 하단에 적었습니다. 투자 권유가 아닙니다.
