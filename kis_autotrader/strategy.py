"""
매매 전략: 이동평균선 크로스 + 손절/익절
- 브로커(API)와 분리되어 있어 전략만 바꿔 끼우기 쉽습니다.
"""
from dataclasses import dataclass


@dataclass
class Signal:
    action: str        # "BUY" | "SELL" | "HOLD"
    reason: str
    short_ma: float = 0.0
    long_ma: float = 0.0


def moving_average(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def build_series(daily_closes, today_str, current_price):
    """과거 일봉 종가에 '오늘 현재가'를 반영한 종가 시계열을 만든다."""
    closes = [c for d, c in daily_closes if d != today_str]
    closes.append(current_price)
    return closes


def ma_cross_signal(closes, short_n, long_n, entry="cross"):
    """
    closes: 오래된 → 최근 종가 (마지막 값 = 오늘 현재가)
    반환: 매수 조건 충족 여부 판단용 Signal (보유 여부는 고려하지 않음)
    """
    if len(closes) < long_n + 1:
        return Signal("HOLD", f"데이터 부족({len(closes)}일)")

    s_now, l_now = moving_average(closes, short_n), moving_average(closes, long_n)
    s_prev, l_prev = moving_average(closes[:-1], short_n), moving_average(closes[:-1], long_n)

    golden = s_prev <= l_prev and s_now > l_now
    dead = s_prev >= l_prev and s_now < l_now

    if golden:
        return Signal("BUY", "골든크로스 발생", s_now, l_now)
    if dead:
        return Signal("SELL", "데드크로스 발생", s_now, l_now)
    if entry == "trend" and s_now > l_now:
        return Signal("BUY", "상승추세(단기>장기)", s_now, l_now)
    if s_now < l_now:
        return Signal("SELL", "하락추세(단기<장기)", s_now, l_now)
    return Signal("HOLD", "신호 없음", s_now, l_now)


def decide(closes, holding, cfg_strategy, cfg_risk):
    """
    holding: None 또는 {"qty", "avg_price", "profit_pct", ...}
    최종 매매 결정을 반환한다.
    """
    sig = ma_cross_signal(closes, cfg_strategy["short_ma"], cfg_strategy["long_ma"],
                          cfg_strategy.get("entry", "cross"))

    if holding:
        pnl = holding["profit_pct"]
        if pnl <= cfg_risk["stop_loss_pct"]:
            return Signal("SELL", f"손절 ({pnl:.2f}%)", sig.short_ma, sig.long_ma)
        if pnl >= cfg_risk["take_profit_pct"]:
            return Signal("SELL", f"익절 ({pnl:.2f}%)", sig.short_ma, sig.long_ma)
        if sig.action == "SELL" and sig.reason.startswith("데드크로스"):
            return sig
        return Signal("HOLD", f"보유 유지 ({pnl:.2f}%)", sig.short_ma, sig.long_ma)

    # 미보유
    if sig.action == "BUY":
        return sig
    return Signal("HOLD", sig.reason, sig.short_ma, sig.long_ma)
