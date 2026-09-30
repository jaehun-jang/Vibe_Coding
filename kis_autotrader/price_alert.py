"""
삼성전자(등 관심종목) 시세를 KIS Open API로 조회해 카카오톡 '나에게 보내기'로 전송

사용법
  python price_alert.py --kakao-login   # 최초 1회: 카카오 로그인 → 토큰 저장
  python price_alert.py --once          # 지금 1회 전송 (테스트)
  python price_alert.py --loop          # 평일 08:00~15:35 동안 15분마다 전송 후 자동 종료

config.yaml 에 아래 항목을 추가하세요 (기존 KIS 설정은 그대로 사용)
  kakao_rest_key: "카카오 REST API 키"
  kakao_redirect_uri: "https://localhost"      # 카카오 앱에 등록한 Redirect URI
  alert_codes: ["005930"]                       # 알림 종목 (생략 시 삼성전자)
  alert_interval_min: 15
"""
import argparse
import json
import logging
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
import yaml

from kis_api import KisClient, app_dir

log = logging.getLogger("price_alert")
CONFIG = app_dir() / "config.yaml"
KAKAO_TOKEN = app_dir() / ".kakao_token.json"
NAMES = {"005930": "삼성전자"}


# ---------------------------------------------------------------- 카카오
def kakao_login(cfg):
    q = urlencode({"client_id": cfg["kakao_rest_key"], "redirect_uri": cfg["kakao_redirect_uri"],
                   "response_type": "code", "scope": "talk_message"})
    print("\n1) 아래 주소를 브라우저에서 열고 카카오 로그인/동의하세요.")
    print(f"   https://kauth.kakao.com/oauth/authorize?{q}")
    print("2) 이동된 주소창의  ?code=XXXX  에서 XXXX 부분을 복사해 붙여넣으세요.")
    code = input("code: ").strip()
    r = requests.post("https://kauth.kakao.com/oauth/token", data={
        "grant_type": "authorization_code", "client_id": cfg["kakao_rest_key"],
        "redirect_uri": cfg["kakao_redirect_uri"], "code": code}, timeout=10)
    data = r.json()
    if "access_token" not in data:
        raise SystemExit(f"카카오 토큰 발급 실패: {data}")
    _save_kakao(data)
    print("카카오 토큰 저장 완료 →", KAKAO_TOKEN)


def _save_kakao(data, old=None):
    t = dict(old or {})
    t["access_token"] = data["access_token"]
    t["access_expire"] = (datetime.now() + timedelta(seconds=data.get("expires_in", 21599) - 300)).isoformat()
    if data.get("refresh_token"):  # 리프레시 토큰은 만료 임박 시에만 새로 옴
        t["refresh_token"] = data["refresh_token"]
    KAKAO_TOKEN.write_text(json.dumps(t), encoding="utf-8")
    return t


def kakao_access_token(cfg):
    if not KAKAO_TOKEN.exists():
        raise SystemExit("카카오 토큰이 없습니다. 먼저  python price_alert.py --kakao-login  을 실행하세요.")
    t = json.loads(KAKAO_TOKEN.read_text(encoding="utf-8"))
    if datetime.now() < datetime.fromisoformat(t["access_expire"]):
        return t["access_token"]
    r = requests.post("https://kauth.kakao.com/oauth/token", data={
        "grant_type": "refresh_token", "client_id": cfg["kakao_rest_key"],
        "refresh_token": t["refresh_token"]}, timeout=10)
    data = r.json()
    if "access_token" not in data:
        raise SystemExit(f"카카오 토큰 갱신 실패(다시 --kakao-login 필요): {data}")
    return _save_kakao(data, t)["access_token"]


def kakao_send(cfg, text):
    tpl = {"object_type": "text", "text": text[:200],
           "link": {"web_url": "https://finance.naver.com", "mobile_web_url": "https://m.stock.naver.com"}}
    r = requests.post("https://kapi.kakao.com/v2/api/talk/memo/default/send",
                      headers={"Authorization": f"Bearer {kakao_access_token(cfg)}"},
                      data={"template_object": json.dumps(tpl, ensure_ascii=False)}, timeout=10)
    if r.status_code != 200 or r.json().get("result_code") != 0:
        raise RuntimeError(f"카톡 전송 실패: {r.text}")


# ---------------------------------------------------------------- 시세
def build_message(kis, codes):
    now = datetime.now().strftime("%H:%M")
    lines = []
    for code in codes:
        p = kis.get_price(code)
        lines.append(f"{NAMES.get(code, code)} {p['price']:,}원 ({p['change_pct']:+.2f}%)")
    return f"[{now} 시세]\n" + "\n".join(lines)


def run_once(cfg, kis):
    msg = build_message(kis, cfg.get("alert_codes") or ["005930"])
    kakao_send(cfg, msg)
    log.info("전송: %s", msg.replace("\n", " | "))


def run_loop(cfg, kis):
    interval = int(cfg.get("alert_interval_min", 15))
    start, end = "08:00", "15:35"
    while True:
        now = datetime.now()
        hm = now.strftime("%H:%M")
        if now.weekday() >= 5 or hm > end:
            log.info("장 시간 종료 → 알림 종료")
            return
        if hm >= start:
            try:
                run_once(cfg, kis)
            except Exception as e:  # 한 번 실패해도 다음 주기에 계속
                log.warning("전송 실패: %s", e)
        # 다음 15분 정각(:00/:15/:30/:45)까지 대기
        nxt = (now.replace(second=0, microsecond=0)
               + timedelta(minutes=interval - now.minute % interval))
        time.sleep(max(1, (nxt - datetime.now()).total_seconds()))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--kakao-login", action="store_true")
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    if a.kakao_login:
        return kakao_login(cfg)
    # 시세 조회만 하므로 주문은 하지 않음. 모의투자 키로도 조회 가능
    kis = KisClient(cfg["app_key"], cfg["app_secret"], cfg["account"], cfg.get("mode", "demo"))
    run_once(cfg, kis) if a.once else run_loop(cfg, kis)


if __name__ == "__main__":
    main()
