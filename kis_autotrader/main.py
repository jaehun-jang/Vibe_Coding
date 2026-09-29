"""
한국투자증권 모의투자 자동매매 실행 파일

사용법:
    python main.py              # 메뉴 화면 (exe 더블클릭 시에도 이 화면)
    python main.py --check      # 연결 테스트: 토큰/잔고/현재가만 확인 (주문 없음)
    python main.py --dry-run    # 신호만 계산하고 주문은 넣지 않음 (로그로 확인)
    python main.py --once       # 한 번만 점검하고 실제(모의) 주문 실행 후 종료
    python main.py --auto       # 장 시간 동안 반복 실행
"""
import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime, date
from pathlib import Path

import yaml

from kis_api import KisClient, KisApiError, app_dir
from strategy import build_series, decide

BASE = app_dir()
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
TRADES_CSV = LOG_DIR / "trades.csv"
STATE_FILE = BASE / ".state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / f"trade_{date.today():%Y%m%d}.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("autotrader")


# ---------------------------------------------------------------- 설정/상태
def load_config():
    path = BASE / "config.yaml"
    if not path.exists():
        sys.exit("config.yaml 이 없습니다. config.example.yaml 을 복사해 config.yaml 로 만든 뒤 키를 입력하세요.")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if cfg.get("mode") != "demo":
        sys.exit("안전장치: 이 프로그램은 모의투자(mode: demo) 전용으로 설정되어 있습니다.")
    if "여기에" in str(cfg.get("app_key")):
        sys.exit("config.yaml 에 APP KEY / APP SECRET 을 입력하세요.")
    return cfg


def load_state():
    today = date.today().isoformat()
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if s.get("date") == today:
            return s
    except Exception:
        pass
    return {"date": today, "bought_today": [], "sold_today": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_trade(side, code, qty, price, reason, order_no=""):
    new = not TRADES_CSV.exists()
    with TRADES_CSV.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["시각", "구분", "종목코드", "수량", "기준가", "사유", "주문번호"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), side, code, qty, price, reason, order_no])


# ---------------------------------------------------------------- 시간
def in_trading_window(cfg):
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.strftime("%H:%M")
    return cfg["market_open"] <= t <= cfg["market_close"]


# ---------------------------------------------------------------- 1회 점검
def run_cycle(client: KisClient, cfg, dry_run=False):
    state = load_state()
    bal = client.get_balance()
    holdings = bal["holdings"]
    cash = bal["cash"]
    log.info("── 점검 시작 | 예수금 %s원 | 총평가 %s원 | 보유 %d종목",
             f"{cash:,}", f"{bal['total_eval']:,}", len(holdings))

    today_str = date.today().strftime("%Y%m%d")
    s_cfg, r_cfg = cfg["strategy"], cfg["risk"]

    # 보유 중이지만 관심종목에 없는 종목도 손절/익절 관리 대상에 포함
    codes = list(dict.fromkeys(list(cfg["watchlist"]) + list(holdings.keys())))

    for code in codes:
        try:
            price = client.get_price(code)["price"]
            closes = build_series(client.get_daily_closes(code, s_cfg["long_ma"] + 30), today_str, price)
            holding = holdings.get(code)
            sig = decide(closes, holding, s_cfg, r_cfg)

            log.info("%s | 현재가 %s | MA%d %.0f / MA%d %.0f | %s (%s)",
                     code, f"{price:,}", s_cfg["short_ma"], sig.short_ma,
                     s_cfg["long_ma"], sig.long_ma, sig.action, sig.reason)

            if sig.action == "BUY":
                if code in state["bought_today"] or code in state["sold_today"]:
                    log.info("  → 오늘 이미 거래한 종목이라 건너뜀")
                    continue
                if len(holdings) >= r_cfg["max_positions"]:
                    log.info("  → 최대 보유 종목 수(%d) 도달, 매수 보류", r_cfg["max_positions"])
                    continue
                budget = min(r_cfg["budget_per_stock"], cash)
                qty = int(budget // price)
                if qty < 1:
                    log.info("  → 예산 부족으로 매수 불가")
                    continue
                if dry_run:
                    log.info("  → [DRY-RUN] 매수 %d주 (주문 안 함)", qty)
                    continue
                out = client.order("buy", code, qty)
                order_no = out.get("ODNO", "")
                log.info("  ✅ 시장가 매수 주문 %d주 (주문번호 %s)", qty, order_no)
                record_trade("매수", code, qty, price, sig.reason, order_no)
                state["bought_today"].append(code)
                holdings[code] = {"qty": qty}
                cash -= qty * price

            elif sig.action == "SELL" and holding:
                qty = holding["qty"]
                if dry_run:
                    log.info("  → [DRY-RUN] 매도 %d주 (주문 안 함)", qty)
                    continue
                out = client.order("sell", code, qty)
                order_no = out.get("ODNO", "")
                log.info("  ✅ 시장가 매도 주문 %d주 (주문번호 %s)", qty, order_no)
                record_trade("매도", code, qty, price, sig.reason, order_no)
                state["sold_today"].append(code)
                holdings.pop(code, None)

        except KisApiError as e:
            log.error("%s 처리 중 API 오류: %s", code, e)
        except Exception as e:
            log.exception("%s 처리 중 예외: %s", code, e)

    save_state(state)
    log.info("── 점검 종료")


def check_connection(client: KisClient, cfg):
    log.info("1) 토큰 발급 테스트")
    client.get_token()
    log.info("   OK")
    log.info("2) 잔고 조회 테스트")
    bal = client.get_balance()
    log.info("   예수금 %s원, 보유종목 %d개", f"{bal['cash']:,}", len(bal["holdings"]))
    for code, h in bal["holdings"].items():
        log.info("   - %s %s %d주 (%.2f%%)", code, h["name"], h["qty"], h["profit_pct"])
    log.info("3) 현재가 조회 테스트")
    for code in cfg["watchlist"]:
        p = client.get_price(code)
        log.info("   - %s: %s원 (%+.2f%%)", code, f"{p['price']:,}", p["change_pct"])
    log.info("연결 테스트 완료 ✅")


# ---------------------------------------------------------------- 메인
def choose_from_menu():
    print()
    print("=" * 44)
    print("   KIS 모의투자 자동매매")
    print("=" * 44)
    print("  1. 연결 테스트 (주문 없음)")
    print("  2. 신호만 확인 (dry-run, 주문 없음)")
    print("  3. 1회 점검 + 모의주문")
    print("  4. 자동매매 시작 (장중 반복)")
    print("  0. 종료")
    print("-" * 44)
    choice = input("번호를 입력하세요: ").strip()
    return {"1": "check", "2": "dry-run", "3": "once", "4": "auto"}.get(choice)


def main():
    ap = argparse.ArgumentParser(description="KIS 모의투자 자동매매")
    ap.add_argument("--check", action="store_true", help="연결 테스트만 수행")
    ap.add_argument("--dry-run", action="store_true", help="주문 없이 신호만 확인")
    ap.add_argument("--once", action="store_true", help="1회만 실행하고 종료")
    ap.add_argument("--auto", action="store_true", help="장 시간 동안 반복 실행")
    args = ap.parse_args()

    if args.check:
        action = "check"
    elif args.dry_run:
        action = "dry-run"
    elif args.once:
        action = "once"
    elif args.auto:
        action = "auto"
    else:
        action = choose_from_menu()
        if action is None:
            return

    cfg = load_config()
    client = KisClient(cfg["app_key"], cfg["app_secret"], cfg["account"], mode="demo")

    if action == "check":
        check_connection(client, cfg)
        return
    if action in ("once", "dry-run"):
        run_cycle(client, cfg, dry_run=(action == "dry-run"))
        return

    log.info("자동매매 시작 (모의투자) - Ctrl+C 로 종료")
    while True:
        try:
            if in_trading_window(cfg):
                run_cycle(client, cfg)
            else:
                log.info("장 운영 시간이 아닙니다. 대기 중...")
            time.sleep(cfg["loop_interval_sec"])
        except KeyboardInterrupt:
            log.info("사용자에 의해 종료되었습니다.")
            break
        except Exception as e:
            log.exception("루프 오류: %s (60초 후 재시도)", e)
            time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if e.code not in (None, 0):
            print(e.code)
    except Exception as e:
        log.exception("오류로 종료되었습니다: %s", e)
    # exe로 더블클릭 실행한 경우 창이 바로 닫히지 않도록 대기
    if getattr(sys, "frozen", False):
        try:
            input("\n엔터를 누르면 창이 닫힙니다...")
        except EOFError:
            pass
