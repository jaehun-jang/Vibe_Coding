# KIS 모의투자 자동매매

한국투자증권 Open API(KIS Developers)로 국내주식 **모의투자** 자동매매를 하는 Python 프로그램입니다.
전략은 이동평균선 크로스(기본 5일/20일) + 손절(-5%)/익절(+10%)입니다.

## 파일 구성
| 파일 | 역할 |
|---|---|
| `config.example.yaml` | 설정 예시 (복사해서 `config.yaml`로 사용) |
| `kis_api.py` | 토큰·시세·잔고·주문 API 호출 |
| `strategy.py` | 매매 신호 계산 (여기만 고치면 전략 변경) |
| `main.py` | 실행 파일 (반복 점검, 로그, 거래기록) |

## 설치
```bash
pip install -r requirements.txt
```
Python 3.9 이상 권장.

## 설정
1. `config.example.yaml` → `config.yaml` 로 복사
2. **모의투자용** APP KEY, APP SECRET, 모의계좌번호(예: `50123456-01`) 입력
3. `watchlist` 에 매매할 종목코드 입력

## 실행 순서 (꼭 이 순서대로)
```bash
python main.py             # 메뉴 화면에서 번호 선택
python main.py --check     # ① 연결 테스트 (토큰/잔고/현재가) - 주문 없음
python main.py --dry-run   # ② 신호만 계산, 주문 안 넣음
python main.py --once      # ③ 1회 실제 모의주문
python main.py --auto      # ④ 장 시간 동안 5분마다 반복
```

## 실행파일(.exe)로 만들기 (Windows)
1. Python 설치 (설치 시 "Add python.exe to PATH" 체크)
2. `build_exe.bat` 더블클릭 → 1~2분 후 `dist` 폴더가 열림
3. `dist` 폴더의 `KIS_AutoTrader.exe` 와 `config.yaml` 을 원하는 곳에 함께 두고 사용
   - exe를 더블클릭하면 메뉴가 나옵니다
   - `config.yaml`, 로그(`logs`) 는 exe와 같은 폴더에 생깁니다
   - 한 번 만든 exe는 Python 없는 PC에서도 실행됩니다
   - 백신이 PyInstaller exe를 오탐지하는 경우가 있습니다. 그때는 예외 등록하세요.

## 결과 확인
- `logs/trade_YYYYMMDD.log` : 실행 로그
- `logs/trades.csv` : 매수/매도 기록 (엑셀로 열림)
- 실제 체결 여부는 한국투자 앱/홈페이지 모의투자 화면에서 확인

## 안전장치
- `mode: demo` 가 아니면 실행되지 않습니다 (실전 주문 방지)
- 같은 종목은 하루에 한 번만 매수/매도
- 최대 보유 종목 수, 종목당 예산 제한

## 자주 나오는 오류
| 메시지 | 원인 / 해결 |
|---|---|
| 토큰 발급 실패 | 모의투자 키가 맞는지, 실전 키를 넣지 않았는지 확인 |
| EGW00133 / 1분당 1회 | 토큰 재발급은 1분에 1회 제한. 잠시 후 재실행 (토큰은 `.token_cache.json`에 저장되어 재사용됨) |
| EGW00201 | 초당 호출 초과. 자동 재시도됨 |
| 계좌 관련 오류 | 계좌번호 뒤 2자리(보통 `01`) 확인 |

## 전략 바꾸기
`strategy.py` 의 `decide()` 함수만 수정하면 됩니다.
신호가 너무 안 나오면 `config.yaml` 의 `entry` 를 `trend` 로 바꿔 테스트해 보세요.

> ⚠ `config.yaml`, `.token_cache.json` 은 키 정보가 들어있으니 공유하거나 GitHub에 올리지 마세요.
