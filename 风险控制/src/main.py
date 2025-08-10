import tkinter as tk
from tkinter import ttk, messagebox
import requests
import math
from datetime import datetime

OKX_CANDLE_URL = "https://www.okx.com/api/v5/market/candles"

# 新增：Dexscreener 配置
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"
DEX_SUPPORTED_CHAINS = [
    ("以太坊", "ethereum"),
    ("BSC", "bsc"),
    ("Arbitrum", "arbitrum"),
    ("Base", "base"),
    ("Polygon", "polygon"),
    ("Optimism", "optimism"),
    ("Avalanche", "avalanche"),
    ("Fantom", "fantom"),
    ("Solana", "solana"),
]


def fetch_okx_candles(inst_id: str, bar: str = "1H", limit: int = 200):
    params = {"instId": inst_id, "bar": bar, "limit": str(limit)}
    resp = requests.get(OKX_CANDLE_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != '0':
        raise RuntimeError(f"OKX API 错误: {data}")
    # OKX candles: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    candles = [
        {
            "ts": int(item[0]),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
        }
        for item in data.get("data", [])
    ]
    # OKX 返回按时间倒序，翻转为正序
    candles.reverse()
    return candles


def fetch_okx_ticker_last(inst_id: str):
    params = {"instId": inst_id}
    resp = requests.get("https://www.okx.com/api/v5/market/ticker", params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != '0':
        raise RuntimeError(f"OKX API 错误: {data}")
    arr = data.get("data", [])
    if not arr:
        raise ValueError("未获取到报价")
    last = float(arr[0]["last"])  # 最新成交价
    ts = int(arr[0]["ts"])       # 毫秒时间戳
    return last, ts

# 新增：基于 Dexscreener 的代币价格查询（通过合约地址）
def fetch_dex_price_by_token(chain_id: str, token_address: str, prefer_quotes: list[str] | None = None):
    if prefer_quotes is None:
        prefer_quotes = ["USDT", "USDC"]
    if not token_address:
        raise ValueError("请先输入代币合约地址")
    url = f"{DEXSCREENER_TOKEN_URL}{token_address}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    pairs = data.get("pairs") or data.get("data", {}).get("pairs") or []
    if not pairs:
        raise ValueError("未在 DexScreener 找到该代币的交易对")
    # 过滤链
    if chain_id:
        pairs_filtered = [p for p in pairs if (p.get("chainId") or "").lower() == chain_id.lower()]
        if pairs_filtered:
            pairs = pairs_filtered
    # 评分：稳定币优先、流动性优先
    def score(p):
        quote_sym = (p.get("quoteToken", {}).get("symbol") or "").upper()
        liq = float((p.get("liquidity", {}) or {}).get("usd") or 0.0)
        stable_bonus = 2 if quote_sym in prefer_quotes else (1 if quote_sym in ["WETH", "WBNB", "WMATIC", "WBTC", "WAVAX", "ETH", "BNB"] else 0)
        return (stable_bonus, liq)
    pairs.sort(key=score, reverse=True)
    best = pairs[0]
    price_usd = best.get("priceUsd")
    if price_usd is None:
        raise ValueError("该代币未提供美元报价(priceUsd)，请更换交易对或链")
    base_sym = best.get("baseToken", {}).get("symbol") or "Token"
    quote_sym = best.get("quoteToken", {}).get("symbol") or "USD"
    return float(price_usd), best.get("dexId"), best.get("pairAddress"), base_sym, quote_sym


def compute_true_range(prev_close: float, high: float, low: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def compute_atr(candles, period: int = 14):
    if len(candles) < period + 1:
        raise ValueError("K线数量不足以计算 ATR")
    trs = []
    for i in range(1, len(candles)):
        tr = compute_true_range(candles[i - 1]["close"], candles[i]["high"], candles[i]["low"])
        trs.append(tr)
    # 简单移动平均 ATR
    atr_values = []
    for i in range(period - 1, len(trs)):
        window = trs[i - (period - 1) : i + 1]
        atr_values.append(sum(window) / period)
    return atr_values[-1]



def median_hitting_time_bm(sigma: float, barrier_abs: float, dt_seconds: float) -> float:
    """
    以布朗运动近似：
    - sigma: 每根 K 的波动标准差(以价格绝对值表示)的代理，使用 ATR 近似
    - barrier_abs: 价格到止损的绝对距离
    - dt_seconds: 每根 K 的时间间隔秒数
    中位首次到达时间 ~ (barrier_abs / sigma)^2 * dt_seconds * c
    常量 c 近似为 ln(2) ≈ 0.693 的比例调节；这里给出经验中位数近似：0.5*(barrier/sigma)^2*dt
    """
    if sigma <= 0:
        return float("inf")
    steps = 0.5 * (barrier_abs / sigma) ** 2
    return steps * dt_seconds


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("风险等价 - 虚拟货币交易")
        self.geometry("900x600")
        self.resizable(False, False)
        self.create_widgets()

    def create_widgets(self):
        pad = {"padx": 8, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        row = 0
        # 模式与链选择
        ttk.Label(frm, text="模式").grid(row=row, column=0, sticky=tk.W, **pad)
        self.mode_var = tk.StringVar(value="CEX")
        self.mode_combo = ttk.Combobox(frm, textvariable=self.mode_var, values=["CEX", "DEX"], width=8, state="readonly")
        self.mode_combo.grid(row=row, column=1, **pad)

        self.chain_label = ttk.Label(frm, text="链/网络")
        self.chain_var = tk.StringVar(value="ethereum")
        self.chain_combo = ttk.Combobox(frm, textvariable=self.chain_var, values=[c[1] for c in DEX_SUPPORTED_CHAINS], width=12, state="readonly")
        # 初始在 CEX 隐藏，DEX 模式显示
        self.chain_label.grid(row=row, column=2, sticky=tk.W, **pad)
        self.chain_combo.grid(row=row, column=3, **pad)
        self.mode_row_index = row

        self.mode_combo.bind("<<ComboboxSelected>>", lambda e: self.on_mode_change())

        row += 1
        # 行情输入
        self.inst_label = ttk.Label(frm, text="交易对(instId，如 BTC-USDT)")
        self.inst_label.grid(row=row, column=0, sticky=tk.W, **pad)
        self.inst_var = tk.StringVar(value="BTC-USDT")
        ttk.Entry(frm, textvariable=self.inst_var, width=22).grid(row=row, column=1, **pad)

        ttk.Label(frm, text="K线周期").grid(row=row, column=2, sticky=tk.W, **pad)
        self.bar_var = tk.StringVar(value="1H")
        ttk.Combobox(frm, textvariable=self.bar_var, values=["1m", "5m", "15m", "1H", "4H", "1D"], width=8).grid(row=row, column=3, **pad)

        ttk.Label(frm, text="ATR周期").grid(row=row, column=4, sticky=tk.W, **pad)
        self.atr_n_var = tk.StringVar(value="14")
        ttk.Entry(frm, textvariable=self.atr_n_var, width=6).grid(row=row, column=5, **pad)

        row += 1
        ttk.Label(frm, text="自定义 ATR(可选，价格绝对值)").grid(row=row, column=0, sticky=tk.W, **pad)
        self.custom_atr_var = tk.StringVar(value="")
        ttk.Entry(frm, textvariable=self.custom_atr_var, width=22).grid(row=row, column=1, **pad)

        ttk.Label(frm, text="开仓金额(报价货币)").grid(row=row, column=2, sticky=tk.W, **pad)
        self.open_notional_var = tk.StringVar(value="100")
        ttk.Entry(frm, textvariable=self.open_notional_var, width=12).grid(row=row, column=3, **pad)

        ttk.Label(frm, text="止损百分比(%)").grid(row=row, column=4, sticky=tk.W, **pad)
        self.stop_pct_var = tk.StringVar(value="")
        ttk.Entry(frm, textvariable=self.stop_pct_var, width=10).grid(row=row, column=5, **pad)

        row += 1
        ttk.Label(frm, text="交易方向").grid(row=row, column=0, sticky=tk.W, **pad)
        self.side_var = tk.StringVar(value="多头")
        ttk.Combobox(frm, textvariable=self.side_var, values=["多头", "空头"], width=8, state="readonly").grid(row=row, column=1, **pad)

        ttk.Label(frm, text="止损位(价格，可选)").grid(row=row, column=2, sticky=tk.W, **pad)
        self.stop_level_var = tk.StringVar(value="")
        ttk.Entry(frm, textvariable=self.stop_level_var, width=12).grid(row=row, column=3, **pad)

        self.fetch_btn = ttk.Button(frm, text="计算", command=self.on_calculate)
        self.fetch_btn.grid(row=row, column=4, columnspan=2, **pad)

        # 当前报价显示与按钮
        row += 1
        ttk.Label(frm, text="当前报价").grid(row=row, column=0, sticky=tk.W, **pad)
        self.price_var = tk.StringVar(value="--")
        ttk.Label(frm, textvariable=self.price_var).grid(row=row, column=1, sticky=tk.W, **pad)

        self.quote_btn = ttk.Button(frm, text="获取当前报价", command=self.on_fetch_price)
        self.quote_btn.grid(row=row, column=2, columnspan=2, **pad)

        row += 1
        self.status_var = tk.StringVar()
        ttk.Label(frm, textvariable=self.status_var, foreground="#666").grid(row=row, column=0, columnspan=6, sticky=tk.W, **pad)

        row += 1
        sep = ttk.Separator(frm)
        sep.grid(row=row, column=0, columnspan=6, sticky="ew", pady=8)

        row += 1
        ttk.Label(frm, text="结果").grid(row=row, column=0, sticky=tk.W, **pad)

        # 突出展示：最大仓位 与 中位触发时间
        row += 1
        self.pos_highlight_var = tk.StringVar(value="--")
        self.time_highlight_var = tk.StringVar(value="--")
        tk.Label(frm, text="止损亏损金额", font=("Segoe UI", 12, "bold")).grid(row=row, column=0, sticky=tk.W, **pad)
        tk.Label(frm, textvariable=self.pos_highlight_var, font=("Segoe UI", 14, "bold"), fg="#0B6").grid(row=row, column=1, sticky=tk.W, **pad)
        tk.Label(frm, text="止损中位触发时间", font=("Segoe UI", 12, "bold")).grid(row=row, column=2, sticky=tk.W, **pad)
        tk.Label(frm, textvariable=self.time_highlight_var, font=("Segoe UI", 14, "bold"), fg="#06C").grid(row=row, column=3, sticky=tk.W, **pad)

        row += 1
        self.txt = tk.Text(frm, height=18, width=110)
        self.txt.grid(row=row, column=0, columnspan=6, **pad)
        self.txt.configure(state=tk.DISABLED)

        # 初始化模式（隐藏链选择等）
        self.on_mode_change()

    def append_result(self, lines):
        self.txt.configure(state=tk.NORMAL)
        self.txt.delete("1.0", tk.END)
        self.txt.insert(tk.END, "\n".join(lines))
        self.txt.configure(state=tk.DISABLED)

    def bar_to_seconds(self, bar: str) -> int:
        mapping = {
            "1m": 60,
            "5m": 5 * 60,
            "15m": 15 * 60,
            "1H": 60 * 60,
            "4H": 4 * 60 * 60,
            "1D": 24 * 60 * 60,
        }
        return mapping.get(bar, 60 * 60)

    def compute_stop_distance(self, last_close: float) -> tuple[float, float]:
        # 优先使用止损百分比，其次根据止损位和方向计算价格距离
        stop_pct_str = self.stop_pct_var.get().strip()
        if stop_pct_str:
            pct = float(stop_pct_str)
            if pct <= 0:
                raise ValueError("止损百分比必须大于 0")
            dist = last_close * (pct / 100.0)
            return dist, pct
        stop_level_str = self.stop_level_var.get().strip()
        if not stop_level_str:
            raise ValueError("未提供止损百分比或止损位")
        stop_level = float(stop_level_str)
        side = self.side_var.get()
        if side == "多头":
            dist = max(0.0, last_close - stop_level)
        else:
            dist = max(0.0, stop_level - last_close)
        if dist <= 0:
            raise ValueError("根据方向与止损位计算得到的止损距离无效，请检查输入")
        pct = (dist / last_close) * 100.0
        return dist, pct

    def parse_inst_symbols(self, inst: str):
        if "-" in inst:
            base, quote = inst.split("-", 1)
        else:
            base, quote = inst, "USDT"
        return base, quote

    # 新增：模式切换
    def on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "CEX":
            self.inst_label.configure(text="交易对(instId，如 BTC-USDT)")
            # 隐藏链选择
            self.chain_label.grid_remove()
            self.chain_combo.grid_remove()
            if not self.inst_var.get():
                self.inst_var.set("BTC-USDT")
        else:
            self.inst_label.configure(text="代币合约地址(0x...)")
            # 显示链选择
            self.chain_label.grid(row=self.mode_row_index, column=2, sticky=tk.W, padx=8, pady=6)
            self.chain_combo.grid(row=self.mode_row_index, column=3, padx=8, pady=6)
            if self.inst_var.get().upper() == "BTC-USDT":
                self.inst_var.set("")
        # 清空报价显示
        self.price_var.set("--")
        self.status_var.set("")

    def on_fetch_price(self):
        try:
            mode = self.mode_var.get()
            if mode == "CEX":
                inst = self.inst_var.get().strip().upper()
                if not inst:
                    raise ValueError("请先输入交易对，例如 BTC-USDT")
                last, ts = fetch_okx_ticker_last(inst)
                self.price_var.set(f"{last:.6f}")
                ts_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
                self.status_var.set(f"{inst} 报价时间: {ts_str}")
            else:
                token_addr = self.inst_var.get().strip()
                if not token_addr:
                    raise ValueError("请先输入代币合约地址")
                chain_id = self.chain_var.get().strip()
                price_usd, dex_id, pair_addr, base_sym, quote_sym = fetch_dex_price_by_token(chain_id, token_addr)
                self.price_var.set(f"{price_usd:.6f}")
                self.status_var.set(f"{chain_id} | {dex_id} | pair: {pair_addr} | {base_sym}/{quote_sym}")
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def on_calculate(self):
        try:
            mode = self.mode_var.get()
            bar = self.bar_var.get().strip()
            atr_n = int(self.atr_n_var.get())
            open_notional = float(self.open_notional_var.get())
            custom_atr = self.custom_atr_var.get().strip()
            if open_notional <= 0:
                raise ValueError("开仓金额必须大于 0")

            self.status_var.set("拉取数据/计算中...")
            self.update_idletasks()

            if mode == "CEX":
                inst = self.inst_var.get().strip().upper()
                base_sym, quote_sym = self.parse_inst_symbols(inst)
                if custom_atr:
                    atr_value = float(custom_atr)
                    candles = fetch_okx_candles(inst, bar, limit=max(atr_n + 2, 50))
                    if not candles:
                        raise ValueError("未获取到K线")
                    last_close = candles[-1]["close"]
                else:
                    candles = fetch_okx_candles(inst, bar, limit=max(atr_n + 50, 100))
                    if len(candles) < atr_n + 1:
                        raise ValueError("K线不足以计算ATR")
                    atr_value = compute_atr(candles, atr_n)
                    last_close = candles[-1]["close"]
            else:
                # DEX 模式
                token_addr = self.inst_var.get().strip()
                chain_id = self.chain_var.get().strip()
                price_usd, dex_id, pair_addr, base_token_sym, quote_token_sym = fetch_dex_price_by_token(chain_id, token_addr)
                last_close = price_usd  # 使用美元报价
                base_sym = base_token_sym or "Token"
                quote_sym = "USD"  # 以 USD 显示
                if custom_atr:
                    atr_value = float(custom_atr)
                else:
                    raise ValueError("DEX 模式暂未提供历史K线以计算 ATR，请先填写‘自定义 ATR’")

            stop_abs, stop_pct = self.compute_stop_distance(last_close)

            # 根据开仓金额推导仓位与止损损失
            position_base = open_notional / last_close
            loss_quote = position_base * stop_abs
            notional_quote = open_notional

            dt_seconds = self.bar_to_seconds(bar)
            median_seconds = median_hitting_time_bm(atr_value, stop_abs, dt_seconds)
            median_hours = median_seconds / 3600.0

            # 更新突出显示区域
            self.pos_highlight_var.set(f"{loss_quote:.4f} {quote_sym}")
            self.time_highlight_var.set(f"{median_hours:.2f} 小时")

            if mode == "CEX":
                inst_display = self.inst_var.get().strip().upper()
            else:
                inst_display = f"{self.chain_var.get()} | {self.inst_var.get().strip()}"

            lines = [
                f"标的: {inst_display} (基础: {base_sym}, 报价: {quote_sym})",
                f"方向: {self.side_var.get()}  周期: {bar}  ATR周期: {atr_n}",
                f"最新价格: {last_close:.6f}",
                f"ATR(价格绝对值): {atr_value:.6f}",
                f"止损百分比: {stop_pct:.2f}%  (价格距离: {stop_abs:.6f})",
                f"开仓金额({quote_sym}): {open_notional:.4f}",
                f"止损触发亏损金额({quote_sym}): {loss_quote:.4f}",
                f"按最新价的名义规模({quote_sym}): {notional_quote:.6f}",
                f"中位触发时间(近似): {median_hours:.2f} 小时",
                "",
                "说明:",
                "- CEX: 使用 OKX 公共K线/报价; DEX: 使用 DexScreener 的美元报价。",
                "- 亏损金额 ≈ (开仓金额/最新价) × 止损距离; 中位触发时间基于布朗运动近似。",
            ]

            self.append_result(lines)
            self.status_var.set("完成")
        except Exception as e:
            self.status_var.set("")
            messagebox.showerror("错误", str(e))


if __name__ == "__main__":
    App().mainloop() 