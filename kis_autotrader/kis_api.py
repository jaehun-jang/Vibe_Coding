"""
한국투자증권 Open API (KIS Developers) 간단 클라이언트 - 국내주식 모의투자용
"""
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BASE_URLS = {
    "demo": "https://openapivts.koreainvestment.com:29443",  # 모의투자
    "real": "https://openapi.koreainvestment.com:9443",      # 실전투자
}

# 모의/실전 TR ID
TR_IDS = {
    "demo": {
        "buy": "VTTC0012U",
        "sell": "VTTC0011U",
        "balance": "VTTC8434R",
    },
    "real": {
        "buy": "TTTC0012U",
        "sell": "TTTC0011U",
        "balance": "TTTC8434R",
    },
}

def app_dir() -> Path:
    """실행 위치 폴더 (exe로 실행 시 exe가 있는 폴더)"""
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


TOKEN_CACHE = app_dir() / ".token_cache.json"


class KisApiError(Exception):
    pass


class KisClient:
    # 모의투자는 초당 호출 제한이 실전보다 빡빡하므로 호출 간격을 둡니다.
    MIN_CALL_INTERVAL = 0.55

    def __init__(self, app_key: str, app_secret: str, account: str, mode: str = "demo"):
        if mode not in BASE_URLS:
            raise ValueError("mode 는 'demo' 또는 'real' 이어야 합니다.")
        self.app_key = app_key
        self.app_secret = app_secret
        self.cano, self.acnt_prdt_cd = self._split_account(account)
        self.mode = mode
        self.base_url = BASE_URLS[mode]
        self.tr = TR_IDS[mode]
        self._token = None
        self._token_expire = None
        self._last_call = 0.0
        self.session = requests.Session()

    # ------------------------------------------------------------------ 공통
    @staticmethod
    def _split_account(account: str):
        acc = account.replace("-", "").strip()
        if len(acc) != 10 or not acc.isdigit():
            raise ValueError("계좌번호는 '12345678-01' 형식(숫자 10자리)이어야 합니다.")
        return acc[:8], acc[8:]

    def _throttle(self):
        wait = self.MIN_CALL_INTERVAL - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _request(self, method: str, path: str, tr_id: str, params=None, body=None, retries=3):
        url = self.base_url + path
        for attempt in range(1, retries + 1):
            self._throttle()
            try:
                if method == "GET":
                    r = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=10)
                else:
                    r = self.session.post(url, headers=self._headers(tr_id), data=json.dumps(body), timeout=10)
                data = r.json()
            except (requests.RequestException, ValueError) as e:
                log.warning("요청 실패(%s/%s) %s: %s", attempt, retries, path, e)
                time.sleep(1.5 * attempt)
                continue

            if r.status_code == 200 and data.get("rt_cd") == "0":
                return data

            msg = f"[{data.get('msg_cd')}] {data.get('msg1')}"
            # 초당 호출 초과(EGW00201)는 잠깐 쉬고 재시도
            if data.get("msg_cd") == "EGW00201" and attempt < retries:
                time.sleep(1.0)
                continue
            # 토큰 만료 시 재발급 후 재시도
            if data.get("msg_cd") in ("EGW00123", "EGW00121") and attempt < retries:
                self._issue_token()
                continue
            raise KisApiError(f"{path} 실패: {msg}")
        raise KisApiError(f"{path} 요청이 {retries}회 모두 실패했습니다.")

    # ------------------------------------------------------------------ 토큰
    def get_token(self) -> str:
        if self._token and self._token_expire and datetime.now() < self._token_expire:
            return self._token
        if self._load_cached_token():
            return self._token
        return self._issue_token()

    def _load_cached_token(self) -> bool:
        # 토큰 발급은 1분에 1회로 제한되므로 파일에 저장해 재사용합니다.
        if not TOKEN_CACHE.exists():
            return False
        try:
            c = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            if c.get("app_key") != self.app_key or c.get("mode") != self.mode:
                return False
            exp = datetime.strptime(c["expire"], "%Y-%m-%d %H:%M:%S")
            if datetime.now() < exp - timedelta(minutes=10):
                self._token, self._token_expire = c["token"], exp - timedelta(minutes=10)
                return True
        except Exception:
            pass
        return False

    def _issue_token(self) -> str:
        url = self.base_url + "/oauth2/tokenP"
        body = {"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret}
        r = self.session.post(url, data=json.dumps(body), headers={"content-type": "application/json"}, timeout=10)
        data = r.json()
        if "access_token" not in data:
            raise KisApiError(f"토큰 발급 실패: {data}")
        exp_str = data.get("access_token_token_expired") or (
            datetime.now() + timedelta(hours=23)).strftime("%Y-%m-%d %H:%M:%S")
        exp = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
        self._token, self._token_expire = data["access_token"], exp - timedelta(minutes=10)
        TOKEN_CACHE.write_text(json.dumps({
            "app_key": self.app_key, "mode": self.mode,
            "token": self._token, "expire": exp_str,
        }), encoding="utf-8")
        log.info("접근 토큰 발급 완료 (만료: %s)", exp_str)
        return self._token

    # ------------------------------------------------------------------ 시세
    def get_price(self, code: str) -> dict:
        """현재가 조회"""
        data = self._request(
            "GET", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        o = data["output"]
        return {
            "code": code,
            "price": int(o["stck_prpr"]),
            "change_pct": float(o["prdy_ctrt"]),
            "volume": int(o["acml_vol"]),
        }

    def get_daily_closes(self, code: str, days: int = 60) -> list:
        """일봉 종가 리스트 (오래된 날짜 → 최근 날짜 순)"""
        end = datetime.now()
        start = end - timedelta(days=int(days * 1.6) + 10)  # 휴장일 감안
        data = self._request(
            "GET", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        rows = [x for x in data.get("output2", []) if x.get("stck_clpr")]
        rows.sort(key=lambda x: x["stck_bsop_date"])
        return [(x["stck_bsop_date"], int(x["stck_clpr"])) for x in rows]

    # ------------------------------------------------------------------ 계좌
    def get_balance(self) -> dict:
        """보유종목 + 예수금 조회"""
        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        data = self._request("GET", "/uapi/domestic-stock/v1/trading/inquire-balance",
                             self.tr["balance"], params=params)
        holdings = {}
        for h in data.get("output1", []):
            qty = int(h.get("hldg_qty", 0))
            if qty <= 0:
                continue
            holdings[h["pdno"]] = {
                "name": h.get("prdt_name", ""),
                "qty": qty,
                "avg_price": float(h.get("pchs_avg_pric", 0)),
                "cur_price": int(h.get("prpr", 0)),
                "profit_pct": float(h.get("evlu_pfls_rt", 0)),
            }
        summary = (data.get("output2") or [{}])[0]
        cash = int(summary.get("prvs_rcdl_excc_amt") or summary.get("dnca_tot_amt") or 0)
        return {
            "holdings": holdings,
            "cash": cash,
            "total_eval": int(summary.get("tot_evlu_amt") or 0),
        }

    # ------------------------------------------------------------------ 주문
    def order(self, side: str, code: str, qty: int, price: int = 0) -> dict:
        """
        side: 'buy' | 'sell'
        price=0 이면 시장가, 그 외 지정가
        """
        if side not in ("buy", "sell"):
            raise ValueError("side 는 buy 또는 sell")
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": code,
            "ORD_DVSN": "01" if price == 0 else "00",   # 01 시장가, 00 지정가
            "ORD_QTY": str(int(qty)),
            "ORD_UNPR": str(int(price)),
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01" if side == "sell" else "",
            "CNDT_PRIC": "",
        }
        data = self._request("POST", "/uapi/domestic-stock/v1/trading/order-cash",
                             self.tr[side], body=body)
        return data.get("output", {})
