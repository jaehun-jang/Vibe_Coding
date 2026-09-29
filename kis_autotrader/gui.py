"""
한국투자증권 모의투자 자동매매 - 데스크톱 앱 (tkinter)

    python gui.py        (또는 run_app.bat 더블클릭 / KIS_AutoTrader.exe)
"""
import logging
import queue
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

import trader

log = logging.getLogger("autotrader")

UP = "#d62d2d"      # 한국식: 상승/매수 = 빨강
DOWN = "#1f5fd6"    # 하락/매도 = 파랑


class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            self.q.put(("log", self.format(record), record.levelno))
        except Exception:
            pass


# ====================================================================== 설정 창
class SettingsDialog(tk.Toplevel):
    def __init__(self, master, cfg, on_saved):
        super().__init__(master)
        self.title("설정")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.cfg = cfg
        self.on_saved = on_saved

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)

        # --- 인증
        box = ttk.LabelFrame(body, text=" 인증 정보 (모의투자용) ", padding=10)
        box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.v_key = tk.StringVar(value=cfg.get("app_key", ""))
        self.v_secret = tk.StringVar(value=cfg.get("app_secret", ""))
        self.v_account = tk.StringVar(value=cfg.get("account", ""))
        self.e_key = self._entry(box, 0, "APP KEY", self.v_key, show="•", width=38)
        self.e_secret = self._entry(box, 1, "APP SECRET", self.v_secret, show="•", width=38)
        self._entry(box, 2, "모의계좌번호", self.v_account, width=20, hint="예: 50123456-01")
        self.v_show = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="키 보기", variable=self.v_show,
                        command=self._toggle_show).grid(row=3, column=1, sticky="w", pady=(4, 0))

        ttk.Label(box, text="관심종목 (한 줄에 하나: 종목코드 종목명)").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(12, 2))
        self.t_watch = tk.Text(box, width=40, height=8, font=("맑은 고딕", 10))
        self.t_watch.grid(row=5, column=0, columnspan=3, sticky="we")
        self.t_watch.insert("1.0", "\n".join(
            f"{c} {n}".strip() for c, n in trader.parse_watchlist(cfg.get("watchlist"))))

        # --- 전략/리스크
        box2 = ttk.LabelFrame(body, text=" 전략 · 리스크 ", padding=10)
        box2.grid(row=0, column=1, sticky="nsew")
        s, r = cfg["strategy"], cfg["risk"]
        self.v_short = tk.StringVar(value=str(s["short_ma"]))
        self.v_long = tk.StringVar(value=str(s["long_ma"]))
        self.v_entry = tk.StringVar(value=s.get("entry", "cross"))
        self.v_budget = tk.StringVar(value=str(r["budget_per_stock"]))
        self.v_maxpos = tk.StringVar(value=str(r["max_positions"]))
        self.v_sl = tk.StringVar(value=str(r["stop_loss_pct"]))
        self.v_tp = tk.StringVar(value=str(r["take_profit_pct"]))
        self.v_interval = tk.StringVar(value=str(int(cfg["loop_interval_sec"]) // 60))
        self.v_open = tk.StringVar(value=cfg["market_open"])
        self.v_close = tk.StringVar(value=cfg["market_close"])

        self._entry(box2, 0, "단기 이동평균(일)", self.v_short, width=8)
        self._entry(box2, 1, "장기 이동평균(일)", self.v_long, width=8)
        ttk.Label(box2, text="매수 방식").grid(row=2, column=0, sticky="w", pady=3)
        cb = ttk.Combobox(box2, textvariable=self.v_entry, values=["cross", "trend"],
                          state="readonly", width=8)
        cb.grid(row=2, column=1, sticky="w")
        ttk.Label(box2, text="cross: 골든크로스 당일만\ntrend: 단기선>장기선이면",
                  foreground="gray").grid(row=2, column=2, sticky="w", padx=6)
        self._entry(box2, 3, "종목당 예산(원)", self.v_budget, width=12)
        self._entry(box2, 4, "최대 보유 종목 수", self.v_maxpos, width=8)
        self._entry(box2, 5, "손절 기준(%)", self.v_sl, width=8, hint="예: -5")
        self._entry(box2, 6, "익절 기준(%)", self.v_tp, width=8, hint="예: 10")
        self._entry(box2, 7, "점검 주기(분)", self.v_interval, width=8)
        self._entry(box2, 8, "매매 시작 시각", self.v_open, width=8, hint="HH:MM")
        self._entry(box2, 9, "매매 종료 시각", self.v_close, width=8, hint="HH:MM")

        btns = ttk.Frame(body)
        btns.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="저장", command=self._save).pack(side="right", padx=6)

        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + 40
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _entry(self, parent, row, label, var, show=None, width=20, hint=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        e = ttk.Entry(parent, textvariable=var, width=width, show=show or "")
        e.grid(row=row, column=1, sticky="w", padx=(6, 0))
        if hint:
            ttk.Label(parent, text=hint, foreground="gray").grid(row=row, column=2, sticky="w", padx=6)
        return e

    def _toggle_show(self):
        ch = "" if self.v_show.get() else "•"
        self.e_key.configure(show=ch)
        self.e_secret.configure(show=ch)

    def _save(self):
        try:
            acc = self.v_account.get().strip()
            digits = acc.replace("-", "")
            if len(digits) != 10 or not digits.isdigit():
                raise ValueError("계좌번호는 '12345678-01' 형식(숫자 10자리)이어야 합니다.")
            watch = [l.strip() for l in self.t_watch.get("1.0", "end").splitlines() if l.strip()]
            if not watch:
                raise ValueError("관심종목을 하나 이상 입력하세요.")
            for c, _ in trader.parse_watchlist(watch):
                if not c.isdigit() or len(c) != 6:
                    raise ValueError(f"종목코드가 올바르지 않습니다: {c}")
            short_ma, long_ma = int(self.v_short.get()), int(self.v_long.get())
            if not (1 <= short_ma < long_ma <= 120):
                raise ValueError("이동평균은 1 ≤ 단기 < 장기 ≤ 120 이어야 합니다.")
            for t in (self.v_open.get(), self.v_close.get()):
                datetime.strptime(t, "%H:%M")
            interval = int(self.v_interval.get())
            if interval < 1:
                raise ValueError("점검 주기는 1분 이상이어야 합니다.")

            cfg = dict(self.cfg)
            cfg.update({
                "app_key": self.v_key.get().strip(),
                "app_secret": self.v_secret.get().strip(),
                "account": f"{digits[:8]}-{digits[8:]}",
                "mode": "demo",
                "watchlist": watch,
                "strategy": {"short_ma": short_ma, "long_ma": long_ma, "entry": self.v_entry.get()},
                "risk": {
                    "budget_per_stock": int(float(self.v_budget.get().replace(",", ""))),
                    "max_positions": int(self.v_maxpos.get()),
                    "stop_loss_pct": -abs(float(self.v_sl.get())),
                    "take_profit_pct": abs(float(self.v_tp.get())),
                },
                "loop_interval_sec": interval * 60,
                "market_open": self.v_open.get(),
                "market_close": self.v_close.get(),
            })
            if not cfg["app_key"] or not cfg["app_secret"]:
                raise ValueError("APP KEY 와 APP SECRET 을 입력하세요.")
        except ValueError as e:
            messagebox.showerror("입력 오류", str(e), parent=self)
            return
        trader.save_config(cfg)
        self.destroy()
        self.on_saved(cfg)


# ====================================================================== 메인 창
class App(tk.Tk):
    COLS = [("code", "종목코드", 80), ("name", "종목명", 110), ("price", "현재가", 100),
            ("short_ma", "단기MA", 100), ("long_ma", "장기MA", 100), ("action", "신호", 70),
            ("qty", "보유", 60), ("profit", "수익률", 80), ("reason", "사유", 300)]

    def __init__(self):
        super().__init__()
        self.title("KIS 모의투자 자동매매")
        self.geometry("1120x720")
        self.minsize(900, 560)

        self.q = queue.Queue()
        self.cfg = None
        self.client = None
        self.busy = False
        self.auto_thread = None
        self.stop_event = threading.Event()

        fmt = trader.setup_logging(console=False)
        qh = QueueHandler(self.q)
        qh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(qh)

        self._build_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        self.after(200, self._load_config)

    # ------------------------------------------------------------ UI 구성
    def _build_style(self):
        st = ttk.Style(self)
        if "vista" in st.theme_names():
            st.theme_use("vista")
        base = ("맑은 고딕", 10)
        st.configure(".", font=base)
        st.configure("Treeview", rowheight=26, font=base)
        st.configure("Treeview.Heading", font=("맑은 고딕", 10, "bold"))
        st.configure("Big.TLabel", font=("맑은 고딕", 15, "bold"))
        st.configure("Cap.TLabel", foreground="gray")
        st.configure("Accent.TButton", font=("맑은 고딕", 10, "bold"))

    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        # --- 요약 카드
        cards = ttk.Frame(root)
        cards.pack(fill="x")
        self.v_cash = tk.StringVar(value="-")
        self.v_eval = tk.StringVar(value="-")
        self.v_hold = tk.StringVar(value="-")
        self.v_state = tk.StringVar(value="설정 확인 중")
        for i, (cap, var) in enumerate([("예수금", self.v_cash), ("총평가", self.v_eval),
                                        ("보유 종목", self.v_hold), ("상태", self.v_state)]):
            f = ttk.Frame(cards, padding=(14, 8), relief="groove", borderwidth=1)
            f.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            cards.columnconfigure(i, weight=1 if i < 3 else 2)
            ttk.Label(f, text=cap, style="Cap.TLabel").pack(anchor="w")
            lbl = ttk.Label(f, textvariable=var, style="Big.TLabel")
            lbl.pack(anchor="w")
            if i == 3:
                self.lbl_state = lbl

        # --- 버튼 바
        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(12, 8))
        self.btn_check = ttk.Button(bar, text="🔌 연결 테스트", command=self.do_check)
        self.btn_signal = ttk.Button(bar, text="🔍 신호 확인", command=self.do_signal)
        self.btn_once = ttk.Button(bar, text="🛒 지금 1회 실행", command=self.do_once)
        self.btn_auto = ttk.Button(bar, text="▶ 자동매매 시작", style="Accent.TButton",
                                   command=self.toggle_auto)
        self.btn_settings = ttk.Button(bar, text="⚙ 설정", command=self.open_settings)
        self.btn_logs = ttk.Button(bar, text="📂 기록 폴더", command=self.open_logs)
        for b in (self.btn_check, self.btn_signal, self.btn_once):
            b.pack(side="left", padx=(0, 6))
        self.btn_auto.pack(side="left", padx=(12, 6), ipadx=8)
        self.btn_logs.pack(side="right")
        self.btn_settings.pack(side="right", padx=6)

        # --- 표 + 로그 (위아래로 크기 조절 가능)
        pane = ttk.PanedWindow(root, orient="vertical")
        pane.pack(fill="both", expand=True)

        tf = ttk.Frame(pane)
        self.tree = ttk.Treeview(tf, columns=[c for c, _, _ in self.COLS], show="headings")
        for key, text, w in self.COLS:
            anchor = "w" if key in ("name", "reason") else ("center" if key in ("code", "action") else "e")
            self.tree.heading(key, text=text)
            self.tree.column(key, width=w, anchor=anchor, stretch=(key == "reason"))
        self.tree.tag_configure("BUY", foreground=UP)
        self.tree.tag_configure("SELL", foreground=DOWN)
        self.tree.tag_configure("ERROR", foreground="#b36b00")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        pane.add(tf, weight=3)

        lf = ttk.LabelFrame(pane, text=" 실행 로그 ", padding=4)
        self.logbox = ScrolledText(lf, height=10, font=("Consolas", 10), state="disabled", wrap="none")
        self.logbox.tag_configure("err", foreground="#c62828")
        self.logbox.tag_configure("warn", foreground="#b36b00")
        self.logbox.pack(fill="both", expand=True)
        pane.add(lf, weight=2)

        # --- 상태바
        self.v_status = tk.StringVar(value="모의투자 전용 · 실전 주문은 차단되어 있습니다")
        ttk.Label(root, textvariable=self.v_status, style="Cap.TLabel").pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------ 설정
    def _load_config(self):
        try:
            self.cfg = trader.load_config()
            self.client = trader.make_client(self.cfg)
            self._set_state("대기 중")
            self._fill_watchlist()
            log.info("설정을 불러왔습니다. [연결 테스트]로 시작해 보세요.")
        except trader.ConfigError as e:
            self.cfg = self.cfg or (trader.load_config(check_keys=False)
                                    if trader.config_exists() else dict(trader.DEFAULT_CONFIG))
            self.client = None
            self._set_state("설정 필요", DOWN)
            log.warning(str(e))
            self.open_settings()
        self._update_buttons()

    def open_settings(self):
        if self.auto_running:
            messagebox.showinfo("설정", "자동매매를 중지한 뒤 설정을 변경하세요.")
            return
        cfg = self.cfg or dict(trader.DEFAULT_CONFIG)
        SettingsDialog(self, cfg, self._on_settings_saved)

    def _on_settings_saved(self, cfg):
        log.info("설정을 저장했습니다.")
        self._load_config()

    def _fill_watchlist(self):
        self.tree.delete(*self.tree.get_children())
        for code, name in trader.parse_watchlist(self.cfg["watchlist"]):
            self.tree.insert("", "end", iid=code,
                             values=(code, name, "", "", "", "", "", "", ""))

    def open_logs(self):
        import os
        import subprocess
        import sys
        trader.LOG_DIR.mkdir(exist_ok=True)
        path = str(trader.LOG_DIR)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showinfo("기록 폴더", f"{path}\n\n{e}")

    # ------------------------------------------------------------ 동작
    @property
    def auto_running(self):
        return self.auto_thread is not None and self.auto_thread.is_alive()

    def _run_bg(self, fn, busy_text):
        if self.busy or self.client is None:
            return
        self.busy = True
        self._set_state(busy_text, UP)
        self._update_buttons()

        def work():
            try:
                fn()
            except Exception as e:
                log.error("오류: %s", e)
                self.q.put(("error", str(e)))
            finally:
                self.q.put(("done", None))

        threading.Thread(target=work, daemon=True).start()

    def do_check(self):
        self._run_bg(lambda: self.q.put(("result", trader.check_connection(self.client, self.cfg))),
                     "연결 테스트 중…")

    def do_signal(self):
        self._run_bg(lambda: self.q.put(("result", trader.run_cycle(self.client, self.cfg, dry_run=True))),
                     "신호 계산 중…")

    def do_once(self):
        if not messagebox.askyesno(
                "1회 실행", "신호가 있으면 모의투자 계좌로 시장가 주문이 들어갑니다.\n진행할까요?"):
            return
        self._run_bg(lambda: self.q.put(("result", trader.run_cycle(self.client, self.cfg))),
                     "점검 · 주문 중…")

    def toggle_auto(self):
        if self.auto_running:
            self.stop_event.set()
            self._set_state("중지하는 중…", DOWN)
            self.btn_auto.configure(state="disabled")
            return
        if self.client is None or self.busy:
            return
        mins = int(self.cfg["loop_interval_sec"]) // 60
        if not messagebox.askyesno(
                "자동매매 시작",
                f"평일 {self.cfg['market_open']}~{self.cfg['market_close']} 동안 "
                f"{mins}분마다 점검하고,\n신호가 나오면 모의투자 계좌로 자동 주문합니다.\n\n"
                "이 창을 닫거나 PC가 절전 모드에 들어가면 멈춥니다.\n시작할까요?"):
            return
        self.stop_event.clear()
        self.auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
        self.auto_thread.start()
        log.info("자동매매를 시작했습니다.")
        self._update_buttons()

    def _auto_loop(self):
        interval = int(self.cfg["loop_interval_sec"])
        waiting_logged = False
        while not self.stop_event.is_set():
            try:
                if trader.in_trading_window(self.cfg):
                    waiting_logged = False
                    self.q.put(("auto", "점검 중…"))
                    res = trader.run_cycle(self.client, self.cfg, stop_event=self.stop_event)
                    self.q.put(("result", res))
                    nxt = datetime.now() + timedelta(seconds=interval)
                    self.q.put(("auto", f"자동매매 중 · 다음 {nxt:%H:%M}"))
                else:
                    if not waiting_logged:
                        log.info("장 운영 시간(%s~%s, 평일)이 아니라 대기합니다.",
                                 self.cfg["market_open"], self.cfg["market_close"])
                        waiting_logged = True
                    self.q.put(("auto", "자동매매 중 · 장 시간 대기"))
            except Exception as e:
                log.exception("자동매매 오류: %s (다음 주기에 재시도)", e)
            self.stop_event.wait(interval if trader.in_trading_window(self.cfg) else 30)
        log.info("자동매매를 중지했습니다.")
        self.q.put(("auto_stopped", None))

    # ------------------------------------------------------------ 화면 갱신
    def _poll(self):
        try:
            while True:
                kind, data, *rest = self.q.get_nowait() + (None,)
                if kind == "log":
                    self._append_log(data, rest[0])
                elif kind == "result":
                    self._show_result(data)
                elif kind == "done":
                    self.busy = False
                    if not self.auto_running:
                        self._set_state("대기 중")
                    self._update_buttons()
                elif kind == "error":
                    messagebox.showerror("오류", data)
                elif kind == "auto":
                    self._set_state(data, UP)
                elif kind == "auto_stopped":
                    self._set_state("대기 중")
                    self._update_buttons()
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _append_log(self, text, level):
        self.logbox.configure(state="normal")
        tag = "err" if level and level >= logging.ERROR else ("warn" if level == logging.WARNING else "")
        self.logbox.insert("end", text + "\n", tag)
        lines = int(self.logbox.index("end-1c").split(".")[0])
        if lines > 2000:
            self.logbox.delete("1.0", f"{lines - 2000}.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _show_result(self, res):
        self.v_cash.set(f"{res['cash']:,}원")
        self.v_eval.set(f"{res['total_eval']:,}원")
        self.v_hold.set(f"{res['n_holdings']}종목")
        seen = set()
        for r in res["rows"]:
            code = r["code"]
            seen.add(code)
            num = lambda v: f"{v:,.0f}" if isinstance(v, (int, float)) else ""
            profit = f"{r['profit_pct']:+.2f}%" if r.get("profit_pct") is not None and r.get("qty") else ""
            action = {"BUY": "매수", "SELL": "매도", "HOLD": "관망", "ERROR": "오류"}.get(r.get("action"), "")
            vals = (code, r.get("name", ""), num(r.get("price")), num(r.get("short_ma")),
                    num(r.get("long_ma")), action, r.get("qty") or "", profit, r.get("reason", ""))
            tag = r.get("action") if r.get("action") in ("BUY", "SELL", "ERROR") else ""
            if self.tree.exists(code):
                self.tree.item(code, values=vals, tags=(tag,))
            else:
                self.tree.insert("", "end", iid=code, values=vals, tags=(tag,))
        self.v_status.set(f"마지막 점검: {datetime.now():%Y-%m-%d %H:%M:%S}"
                          + (f" · 주문 {res['orders']}건" if res.get("orders") else ""))

    def _set_state(self, text, color=None):
        self.v_state.set(text)
        self.lbl_state.configure(foreground=color or "")

    def _update_buttons(self):
        auto = self.auto_running
        ready = self.client is not None and not self.busy and not auto
        for b in (self.btn_check, self.btn_signal, self.btn_once):
            b.configure(state="normal" if ready else "disabled")
        self.btn_settings.configure(state="disabled" if (auto or self.busy) else "normal")
        self.btn_auto.configure(
            text="■ 자동매매 중지" if auto else "▶ 자동매매 시작",
            state="normal" if (auto or ready) else "disabled")

    def _on_close(self):
        if self.auto_running:
            if not messagebox.askyesno("종료", "자동매매가 실행 중입니다. 중지하고 종료할까요?"):
                return
            self.stop_event.set()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
