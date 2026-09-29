"""
한국투자증권 모의투자 자동매매 - 명령줄(CLI) 버전
화면으로 쓰려면 gui.py (또는 KIS_AutoTrader.exe) 를 실행하세요.

    python main.py --check      # 연결 테스트 (주문 없음)
    python main.py --dry-run    # 신호만 확인 (주문 없음)
    python main.py --once       # 1회 점검 + 모의주문
    python main.py --auto       # 장 시간 동안 반복 실행
"""
import argparse
import logging
import sys
import time

import trader

log = logging.getLogger("autotrader")


def main():
    ap = argparse.ArgumentParser(description="KIS 모의투자 자동매매 (CLI)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="연결 테스트")
    g.add_argument("--dry-run", action="store_true", help="주문 없이 신호만 확인")
    g.add_argument("--once", action="store_true", help="1회 점검 + 모의주문")
    g.add_argument("--auto", action="store_true", help="장 시간 동안 반복 실행")
    args = ap.parse_args()

    trader.setup_logging(console=True)
    try:
        cfg = trader.load_config()
    except trader.ConfigError as e:
        sys.exit(str(e))
    client = trader.make_client(cfg)

    if args.check:
        trader.check_connection(client, cfg)
    elif args.dry_run or args.once:
        trader.run_cycle(client, cfg, dry_run=args.dry_run)
    else:
        log.info("자동매매 시작 (모의투자) - Ctrl+C 로 종료")
        while True:
            try:
                if trader.in_trading_window(cfg):
                    trader.run_cycle(client, cfg)
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
    main()
