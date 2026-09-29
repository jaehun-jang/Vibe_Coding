"""
자동매매 핵심 로직 (CLI/GUI 공용)
- 설정 로드/저장, 상태 관리, 1회 점검(run_cycle), 연결 테스트
"""
import copy
import csv
import json
import logging
from datetime import datetime, date

import yaml

from kis_api import KisClient, KisApiError, app_dir
from strategy import build_series, decide

log = logging.getLogger("autotrader")

BASE = app_dir()
LOG_DIR = BASE / "logs"
CONFIG_PATH = BASE / "config.yaml"
TRADES_CSV = LOG_DIR / "trades.csv"
STATE_FILE = BASE / ".state.json"

DEFAULT_CONFIG = {
    "app_key": "",
    "app_secret": "",
    "account": "",
    "mode": "demo",
    "watchlist": ["005930 삼성전자", "000660 SK하이닉스", "035420 NAVER", "005380 현대차"],
    "strategy": {"short_ma": 5, "long_ma": 20, "entry": "cross"},
    "risk": {
        "budget_per_stock": 3000000,
        "max_positions": 3,
        "stop_loss_pct": -5.0,
        "take_profit_pct": 10.0,
    },
    "loop_interval_sec": 300,
    "market_open": "09:05",
    "market_close": "15:15",
}


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- 로깅
def setup_logging(console=True):
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_DIR / f"trade_{date.today():%Y%m%d}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if console:
        import sys
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return fmt


# ---------------------------------------------------------------- 설정
def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def parse_watchlist(items):
    """["005930 삼성전자", "000660", 5930] → [("005930","삼성전자"), ("000660",""), ("005930","")]"""
    out, seen = [], set()
    for it in items or []:
        s = str(it).replace(":", " ").strip()
        if not s:
            continue
        parts = s.split(None, 1)
        code = parts[0].zfill(6)
        if code in seen:
            continue
        seen.add(code)
        out.append((code, parts[1].strip() if len(parts) > 1 else ""))
    return out


def config_exists():
    return CONFIG_PATH.exists()


def load_config(check_keys=True):
    if not CONFIG_PATH.exists():
        raise ConfigError("config.yaml 이 없습니다. 설정에서 키와 계좌번호를 입력하세요.")
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    cfg = _merge(DEFAULT_CONFIG, raw)
    if cfg.get("mode") != "demo":
        raise ConfigError("안전장치: 이 프로그램은 모의투자(mode: demo) 전용입니다.")
    if check_keys:
        key = str(cfg.get("app_key") or "")
        if not key or "여기에" in key or not cfg.get("app_secret") or not cfg.get("account"):
            raise ConfigError("APP KEY / APP SECRET / 계좌번호를 설정에서 입력하세요.")
    return cfg


def save_config(cfg):
    cfg = copy.deepcopy(cfg)
    cfg["mode"] = "demo"
    header = (
        "# KIS 모의투자 자동매매 설정 (앱의 [설정]에서 저장됨)\n"
        "# ⚠ 이 파일에는 키와 계좌번호가 들어 있습니다. 공유하거나 GitHub에 올리지 마세요.\n\n"
    )
    CONFIG_PATH.write_text(
        header + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def make_client(cfg):
    return KisClient(cfg["app_key"], cfg["app_secret"], cfg["account"], mode="demo")


# ---------------------------------------------------------------- 상태/기록
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


def record_trade(side, code, name, qty, price, reason, order_no=""):
    LOG_DIR.mkdir(exist_ok=True)
    new = not TRADES_CSV.exists()
    with TRADES_CSV.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["시각", "구분", "종목코드", "종목명", "수량", "기준가", "사유", "주문번호"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), side, code, name, qty, price, reason, order_no])


def in_trading_window(cfg, now=None):
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.strftime("%H:%M")
    return cfg["market_open"] <= t <= cfg["market_close"]


# ---------------------------------------------------------------- 연결 테스트
def check_connection(client, cfg):
    log.info("연결 테스트 시작")
    client.get_token()
    log.info("  토큰 OK")
    bal = client.get_balance()
    log.info("  예수금 %s원, 보유종목 %d개", f"{bal['cash']:,}", len(bal["holdings"]))
    names = dict(parse_watchlist(cfg["watchlist"]))
    rows = []
    for code, name in parse_watchlist(cfg["watchlist"]):
        p = client.get_price(code)
        h = bal["holdings"].get(code)
        rows.append({
            "code": code, "name": name or (h or {}).get("name", ""),
            "price": p["price"], "change_pct": p["change_pct"],
            "qty": h["qty"] if h else 0, "profit_pct": h["profit_pct"] if h else None,
            "action": "", "reason": f"전일대비 {p['change_pct']:+.2f}%",
        })
        log.info("  %s %s: %s원 (%+.2f%%)", code, names.get(code, ""), f"{p['price']:,}", p["change_pct"])
    log.info("연결 테스트 완료 ✅")
    return {"cash": bal["cash"], "total_eval": bal["total_eval"],
            "n_holdings": len(bal["holdings"]), "rows": rows}


# ---------------------------------------------------------------- 1회 점검
def run_cycle(client, cfg, dry_run=False, stop_event=None):
    """
    관심종목 + 보유종목을 점검하고 신호에 따라 모의주문한다.
    반환: {"cash", "total_eval", "n_holdings", "rows": [...], "orders": n}
    """
    state = load_state()
    bal = client.get_balance()
    holdings = bal["holdings"]
    cash = bal["cash"]
    log.info("── 점검 시작%s | 예수금 %s원 | 총평가 %s원 | 보유 %d종목",
             " (주문 없음)" if dry_run else "", f"{cash:,}", f"{bal['total_eval']:,}", len(holdings))

    today_str = date.today().strftime("%Y%m%d")
    s_cfg, r_cfg = cfg["strategy"], cfg["risk"]
    watch = parse_watchlist(cfg["watchlist"])
    names = dict(watch)
    codes = list(dict.fromkeys([c for c, _ in watch] + list(holdings.keys())))

    rows, n_orders = [], 0
    for code in codes:
        if stop_event is not None and stop_event.is_set():
            log.info("중지 요청으로 점검을 멈춥니다.")
            break
        holding = holdings.get(code)
        name = names.get(code) or (holding or {}).get("name", "")
        row = {"code": code, "name": name, "price": None, "short_ma": None, "long_ma": None,
               "action": "ERROR", "reason": "", "qty": holding["qty"] if holding else 0,
               "profit_pct": holding.get("profit_pct") if holding else None}
        try:
            price = client.get_price(code)["price"]
            closes = build_series(client.get_daily_closes(code, s_cfg["long_ma"] + 30), today_str, price)
            sig = decide(closes, holding, s_cfg, r_cfg)
            row.update(price=price, short_ma=sig.short_ma, long_ma=sig.long_ma,
                       action=sig.action, reason=sig.reason)
            log.info("%s %s | 현재가 %s | MA%d %.0f / MA%d %.0f | %s (%s)",
                     code, name, f"{price:,}", s_cfg["short_ma"], sig.short_ma,
                     s_cfg["long_ma"], sig.long_ma, sig.action, sig.reason)

            if sig.action == "BUY":
                if code in state["bought_today"] or code in state["sold_today"]:
                    row["reason"] += " → 오늘 이미 거래"
                elif len(holdings) >= r_cfg["max_positions"]:
                    row["reason"] += " → 최대 보유 종목 수 도달"
                else:
                    qty = int(min(r_cfg["budget_per_stock"], cash) // price)
                    if qty < 1:
                        row["reason"] += " → 예산 부족"
                    elif dry_run:
                        row["reason"] += f" → [확인만] 매수 {qty}주"
                    else:
                        out = client.order("buy", code, qty)
                        order_no = out.get("ODNO", "")
                        log.info("  ✅ 시장가 매수 주문 %d주 (주문번호 %s)", qty, order_no)
                        record_trade("매수", code, name, qty, price, sig.reason, order_no)
                        state["bought_today"].append(code)
                        holdings[code] = {"qty": qty, "name": name}
                        cash -= qty * price
                        row["qty"] = qty
                        row["reason"] += f" → 매수 {qty}주 주문"
                        n_orders += 1

            elif sig.action == "SELL" and holding:
                qty = holding["qty"]
                if code in state["sold_today"]:
                    row["reason"] += " → 오늘 이미 매도 주문함 (체결 대기)"
                elif dry_run:
                    row["reason"] += f" → [확인만] 매도 {qty}주"
                else:
                    out = client.order("sell", code, qty)
                    order_no = out.get("ODNO", "")
                    log.info("  ✅ 시장가 매도 주문 %d주 (주문번호 %s)", qty, order_no)
                    record_trade("매도", code, name, qty, price, sig.reason, order_no)
                    state["sold_today"].append(code)
                    holdings.pop(code, None)
                    row["qty"] = 0
                    row["reason"] += f" → 매도 {qty}주 주문"
                    n_orders += 1

        except KisApiError as e:
            row["reason"] = str(e)
            log.error("%s 처리 중 API 오류: %s", code, e)
        except Exception as e:
            row["reason"] = str(e)
            log.exception("%s 처리 중 예외: %s", code, e)
        rows.append(row)

    if not dry_run:
        save_state(state)
    log.info("── 점검 종료 (주문 %d건)", n_orders)
    return {"cash": cash, "total_eval": bal["total_eval"], "n_holdings": len(holdings),
            "rows": rows, "orders": n_orders}
