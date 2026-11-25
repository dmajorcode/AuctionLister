# 서울 아파트 경매 모니터

**공개 페이지: https://dmajorcode.github.io/AuctionLister/** (GitHub 설정을 마친 뒤 열립니다. 휴대폰 데이터망에서도 접속 가능)

## 실행 명령어

모두 프로젝트 폴더에서 실행합니다.

```bash
cd ~/Documents/Github/auction_app
```

지금 한 번 수집 (`.env`에 텔레그램 설정이 있으면 알림도 **실제로 보냅니다**):

```bash
python3 collector.py
```

알림 받을 사람(채팅 ID) 확인 / 테스트 메시지:

```bash
python3 telegram_setup.py
```

```bash
python3 telegram_setup.py --test
```

매각결과만 갱신 (알림 없음):

```bash
python3 collector.py --results-only
```

이 컴퓨터에서만 화면 보기 (http://localhost:8765):

```bash
python3 server.py
```

맥에서 자동 실행 등록/다시 등록. GitHub 로 운영하면 필요 없습니다. 이미 등록된 상태에서 `bootstrap`만 하면 `Bootstrap failed: 5` 오류가 나므로 먼저 해제합니다.

화면 서버:

```bash
launchctl bootout gui/$(id -u)/com.auctionapp.server 2>/dev/null; sleep 1; cp com.auctionapp.server.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.auctionapp.server.plist
```

매일 9시 수집:

```bash
mkdir -p ~/Library/Logs/auction_app; launchctl bootout gui/$(id -u)/com.auctionapp.collector 2>/dev/null; sleep 1; cp com.auctionapp.collector.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.auctionapp.collector.plist
```

자동 실행 해제:

```bash
launchctl bootout gui/$(id -u)/com.auctionapp.collector; launchctl bootout gui/$(id -u)/com.auctionapp.server
```

## 수집 조건

경매마당(madangs.com/search)에서 아래 조건의 물건을 매일 오전 9시에 수집해 보여줍니다. 매각액은 경매마당 매각결과 페이지에서 13시와 18시에도 가져옵니다.

- 지역: 서울 전체 · 용도: 주거용건물 > 아파트
- 지분경매 제외 (사이트 지분필터 `지분 제외` + 특수권리에 '지분' 포함 물건 추가 제외)
- 감정가 10억 원 이상 · **최저가 20억 원 이하**

수집 URL: `https://madangs.com/search?use_type=2000&addr=11&share=1&eval_p_min=1000000000`
(최저가 20억 초과 물건도 '가격 대기'로 함께 추적합니다. 유찰로 최저가가 20억 이하로 내려오면 그날 신규로 들어옵니다.)

## 화면 탭

| 탭 | 내용 |
|---|---|
| 신규 (미확인) | **첫 수집 이후** 목록에 새로 등록됐거나 유찰 등으로 최저가가 20억 이하가 된 물건 중, **진행 중(매각기일 전)이고 아직 눌러보지 않은 것**. 링크(주소·경매마당·네이버부동산)나 `확인함으로 표시`를 누르면 빠집니다. 첫 수집 때 이미 있던 물건은 신규가 아닙니다. `NEW`=새로 등록, `유찰로 진입`=가격이 내려와 들어온 물건 |
| ★ 집중 모니터링 | 카드 오른쪽 위 ☆를 눌러 표시한 물건 (임차인 필터와 상관없이 항상 표시) |
| 모니터링 중 | 지금 조건을 만족하는 진행 물건 전체 |
| 가격 대기 | 감정가 10억↑이지만 최저가가 20억을 넘는 물건. 조건까지 얼마 남았는지 표시 |
| 매각 결과 | 매각기일 당일부터, 또는 목록에서 빠진 물건과 매각액. **계속 누적**되며 `숨기기`로만 안 보이게 함 (`숨긴 물건 보기`에서 복구) |

기본 정렬은 **감정가 대비 최저가가 낮은 순**입니다.

- **검색**: 아파트 이름·구·동·주소·사건번호로 검색. 띄어쓰기로 여러 단어를 넣으면 모두 포함하는 물건만 (예: `강남구 대치동`). 모든 탭에 적용되고 탭 건수도 검색 결과 기준으로 바뀜
- 선순위임차인 / 임차권등기 물건은 기본으로 숨김. 화면 위 체크박스로 함께 보기 (브라우저에 기억)
- 모든 카드에 매각기일 D-day, 최저가가 바뀌었으면 이전 최저가(취소선) 표시
- 확인함·집중 모니터링 표시는 `data/auction.db`에 저장됩니다.

## 파일

| 파일 | 역할 |
|---|---|
| `collector.py` | 수집 · 조건 진입 판별 · 매각결과/시세 조회 (표준 라이브러리만 사용) |
| `telegram_setup.py` | 알림 받을 사람(채팅 ID) 확인 · 테스트 |
| `.github/workflows/daily.yml` | GitHub 에서 매일 9시 수집 · 페이지 게시 |
| `data/store.json`, `data/state.json` | GitHub 용 수집 데이터 / 확인함·★ 표시 |
| `server.py` | 화면 서버 (127.0.0.1:8765, 이 컴퓨터에서만 접속) |
| `report_template.html` | 화면 템플릿 |
| `data/auction.db` | 물건 · 확인 여부 · 집중 표시 (SQLite) |
| `run.sh` | 매일 수집 스크립트 (수동 실행용. 자동 실행 로그: `~/Library/Logs/auction_app/collector.log`) |
| `com.auctionapp.collector.plist` | 매일 오전 9시 수집 LaunchAgent |
| `com.auctionapp.server.plist` | 화면 서버 상시 실행 LaunchAgent |

## GitHub에서 무료로 돌리기 (맥이 꺼져 있어도 매일 9시 수집)

공개 저장소 + GitHub Actions + GitHub Pages 로 돌아갑니다. 비용 없음, Claude 없이 동작.

- 매일 09:00(전체 수집·알림), 13:00·18:00(매각결과만) Actions 가 실행 → `data/store.json` 커밋 → 페이지를 `https://dmajorcode.github.io/AuctionLister/` 에 게시
- 확인함/★ 표시는 페이지에서 저장소의 `data/state.json` 에 직접 저장 (기기마다 GitHub 토큰을 한 번 입력)
- 토큰이 없는 기기·사람은 **읽기 전용**으로만 봄. 페이지 주소를 아는 사람은 목록을 볼 수 있음 (공개 저장소)
- 비밀값(텔레그램 토큰, 채팅 ID, 국토부 키)은 저장소에 올라가지 않고 GitHub Secrets 에만 저장

### 여러 사람이 함께 쓰기

- **보기**: 페이지 주소만 알면 누구나, 로그인·토큰 없이 볼 수 있습니다.
- **확인함/★ 저장**: 모두가 같은 표시를 공유합니다. 저장할 사람의 기기에는 이 저장소의 쓰기 토큰이 필요합니다.
  fine-grained 토큰은 저장소 소유자만 자기 저장소용으로 만들 수 있으므로, 소유자가 만든 토큰을 믿는 사람에게만 따로 전달합니다.
  이 토큰으로는 저장소의 코드도 바꿀 수 있고, 코드는 Secrets(텔레그램 토큰 등)를 읽을 수 있으니 주의하세요. 만료일을 설정하고, 새면 바로 삭제하세요.

### 처음 한 번 설정

1. 저장소: `dmajorcode/AuctionLister` (Public)
2. 이 폴더를 올리기: GitHub Desktop 에서 `Push origin`
3. 저장소 **Settings › Pages › Source** 를 `GitHub Actions` 로
4. **Settings › Secrets and variables › Actions › New repository secret** 에 등록
   - `TELEGRAM_TOKEN` : @AuctionCrawlerBot 토큰
   - `TELEGRAM_CHAT_IDS` : 알림 받을 사람 채팅 ID (쉼표로 여러 명)
   - `MOLIT_API_KEY` : 국토부 실거래가 키
5. **Actions › daily-collect › Run workflow** 로 첫 실행 → 성공하면 페이지 주소가 표시됨
6. 페이지에서 확인함/★ 를 저장하려면 [fine-grained 토큰](https://github.com/settings/personal-access-tokens/new) 생성
   - Repository access: **Only select repositories** → 이 저장소만
   - Permissions › Repository › **Contents: Read and write** (다른 권한 없음)
   - 페이지 위쪽 `저장 설정` 을 눌러 붙여넣기 (그 기기 브라우저에만 저장됨)

GitHub 로 옮긴 뒤에는 로컬 자동 실행을 끄세요. 그대로 두면 텔레그램 알림이 두 번 옵니다.

```bash
launchctl bootout gui/$(id -u)/com.auctionapp.collector; launchctl bootout gui/$(id -u)/com.auctionapp.server
```

참고: 저장소에 60일간 아무 커밋이 없으면 GitHub 가 예약 실행을 멈추는데, 매일 수집 커밋이 생기므로 보통 해당 없습니다. 멈추면 Actions 탭에서 다시 켜면 됩니다.

## 텔레그램 봇을 특정 사람만 쓰게 하기

텔레그램 봇은 이름을 알면 누구나 대화창을 열 수 있지만, 이 봇은 **아무 메시지에도 답하지 않고 `TELEGRAM_CHAT_IDS` 허용 목록에 있는 사람에게만** 알림을 보냅니다.

1. 받을 사람이 @AuctionCrawlerBot 에서 `시작` 을 누름
2. `python3 telegram_setup.py` → 봇에 말을 건 사람들의 채팅 ID 확인 (`.env` 에 TELEGRAM_TOKEN 필요)
3. 받을 사람 ID만 `TELEGRAM_CHAT_IDS` 에 (로컬 `.env`, GitHub Secrets 둘 다)
4. `python3 telegram_setup.py --test` 로 테스트 메시지
5. (권장) @BotFather 에서 `/setjoingroups` → Disable : 다른 사람이 봇을 단체방에 초대하지 못하게

## 같은 평형 시세 (국토부 실거래가 API 키 필요)

1. [공공데이터포털](https://www.data.go.kr)에서 **국토교통부_아파트 매매 실거래가 자료** 활용신청 (무료, 보통 바로 승인)
2. 마이페이지에서 일반 인증키(Decoding 또는 Encoding 아무거나)를 복사해 `.env`에 추가

```
MOLIT_API_KEY=발급받은키
```

키가 없으면 경매마당에서 제공하는 단지 평균 실거래가(평형 무관)만 표시됩니다.

## 매각액과 갱신 빈도

경매마당 **매각결과** 페이지(`/search/result?...&date=시작일&date_end=오늘`)에서 낙찰가를 읽습니다. 서울·아파트·감정가 10억↑·지분 제외이면서 최저가 20억 이하였던(또는 추적하던) 물건을 저장하고, 계속 누적합니다.

| 실행 | 내용 | 경매마당 요청 |
|---|---|---|
| 매일 09:00 | 진행 목록 + 최근 90일 매각결과 + 알림 | 목록 2회 + 결과 약 5회 + 새 물건 상세 |
| 매일 13:00, 18:00 | 매각결과만 (알림 없음) | 약 5회 + 새 물건 상세 |

요청 사이에는 1.5초를 둡니다. GitHub 예약 실행은 몇 분~수십 분 늦을 수 있습니다. 늦게 올라온 결과는 90일 범위를 매번 다시 훑으므로 다음 실행에서 들어옵니다.

## 텔레그램 알림 (선택)

`.env`(로컬) 또는 GitHub Secrets 에 `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_IDS` 가 있으면 매일 보냅니다 (`.env.example` 참고).

알림은 그날 조건에 새로 들어온 물건(신건·유찰로 진입)을 보내며, 화면 기본값처럼 선순위임차인·임차권등기 물건은 빼고 보냅니다. 포함하려면 `.env`에 추가:

```
ALERT_SHOW_TAGS=선순위임차인,임차권등기
```
