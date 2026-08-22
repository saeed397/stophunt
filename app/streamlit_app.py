"""
app/streamlit_app.py
=====================
Run with:  streamlit run app/streamlit_app.py

Settings sidebar is unchanged in spirit from before (asset, timeframe with
auto-HTF, R:R, direction, execution mode, risk inputs, confirm button).

OUTPUT is now deliberately minimal, per explicit instruction: exactly the two
required groups (Standard / Stop-Hunt Trigger), each just three colored
numbers — Entry (yellow), Take Profit (green), Stop Loss (red) — nothing
else visible by default. A small "ℹ️" toggle under each box reveals a short,
plain-language explanation (see presentation/explain.py); everything else
that used to be shown directly (candle counts, liquidity-level counts, setup
score tables, raw calibration JSON) has been removed from the default view.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from config import TIMEFRAME_ORDER, HIGHER_TIMEFRAME_MAP, RR_OPTIONS, DEFAULT_RR
from data.providers import MultiProviderOHLC, CoinGeckoProvider, ProviderError
from engines.stophunt_engine import run_signal_engine, SignalEngineError
from engines.calibrator import CalibrationError
from risk.risk_management import RiskConfig, RiskManager
from presentation.explain import explain_signal

st.set_page_config(page_title="Liquidity-Centric Order System", layout="wide")

BOX_CSS = """
<style>
.signal-box {
    border: 1px solid rgba(128,128,128,0.35);
    border-radius: 12px;
    padding: 18px 20px;
    margin-bottom: 10px;
}
.signal-title { font-size: 1.05rem; font-weight: 600; margin-bottom: 10px; }
.price-row { display: flex; justify-content: space-between; font-size: 1.15rem; margin: 4px 0; }
.price-label { opacity: 0.75; }
.price-entry { color: #d4a017; font-weight: 700; }
.price-tp { color: #1f9d55; font-weight: 700; }
.price-sl { color: #e0303d; font-weight: 700; }
</style>
"""


@st.cache_data(ttl=3600, show_spinner="Loading top 500 assets from CoinGecko...")
def load_asset_universe():
    provider = CoinGeckoProvider()
    return provider.get_top_assets()


def render_signal_box(title: str, sig, explanation: str):
    st.markdown(
        f"""<div class="signal-box">
        <div class="signal-title">{title}</div>
        <div class="price-row"><span class="price-label">ورود</span><span class="price-entry">{sig.entry_price:.6g}</span></div>
        <div class="price-row"><span class="price-label">حد سود</span><span class="price-tp">{sig.take_profit:.6g}</span></div>
        <div class="price-row"><span class="price-label">حد ضرر</span><span class="price-sl">{sig.stop_loss:.6g}</span></div>
        </div>""",
        unsafe_allow_html=True,
    )
    with st.expander("ℹ️"):
        st.write(explanation)


def main():
    st.title("سامانه هوشمند تحلیل نقدینگی و سفارش‌گذاری")
    st.markdown(BOX_CSS, unsafe_allow_html=True)

    with st.sidebar:
        st.header("⚙️ تنظیمات (Settings)")

        try:
            universe = load_asset_universe()
        except ProviderError as e:
            st.error(f"عدم دسترسی به فهرست رمزارزها: {e}")
            st.stop()

        asset_labels = [f"{a['symbol']} — {a['name']}" for a in universe]
        asset_choice = st.selectbox("انتخاب رمزارز (Asset)", asset_labels, index=0)
        chosen_asset = universe[asset_labels.index(asset_choice)]
        asset_symbol = chosen_asset["symbol"]
        coingecko_id = chosen_asset["id"]

        quote = st.selectbox("ارز پایه (Quote)", ["USD", "USDT"], index=0)

        timeframe = st.selectbox("تایم‌فریم اصلی (Timeframe)", TIMEFRAME_ORDER,
                                  index=TIMEFRAME_ORDER.index("4h"))
        higher_tf = HIGHER_TIMEFRAME_MAP[timeframe]
        st.text_input("تایم‌فریم بالاتر (auto)", value=higher_tf, disabled=True)

        rr_label = st.selectbox("نسبت ریسک/پاداش (R:R)", RR_OPTIONS,
                                 index=RR_OPTIONS.index(DEFAULT_RR))
        rr_target = float(rr_label.split(":")[1])

        st.subheader("جهت سیگنال (Direction)")
        direction = st.radio("", ["Buy", "Sell", "Both"], horizontal=True, label_visibility="collapsed")

        st.subheader("حالت اجرا (Execution Mode)")
        exec_mode = st.radio(
            "",
            ["هر دو (Standard + Stop-Hunt Trigger)", "فقط Standard (سیگنال لحظه‌ای)",
             "فقط Stop-Hunt Trigger (سفارش معلق)"],
            label_visibility="collapsed",
        )

        st.subheader("مدیریت ریسک (Risk Management)")
        account_equity = st.number_input("سرمایه حساب (Account Equity, USD)", min_value=1.0, value=1000.0)
        risk_pct = st.slider("ریسک هر معامله (%)", 0.1, 5.0, 1.0, step=0.1)
        max_daily_loss = st.slider("حداکثر ضرر روزانه (%)", 1.0, 20.0, 3.0, step=0.5)
        max_consec_losses = st.number_input("حداکثر باخت متوالی مجاز", min_value=1, value=3, step=1)

        with st.expander("داده (Data) — پیشرفته"):
            lookback = st.slider("تعداد کندل تاریخی (Lookback)", 300, 2000, 1000, step=100)
            calib_window = st.slider(
                "پنجره کالیبراسیون آماری (Rolling Calibration Window)", 100, 1000, 300, step=50,
            )

        confirm = st.button("✅ تایید و اجرا (Confirm & Run)", type="primary", use_container_width=True)

    if not confirm:
        st.info("تنظیمات را در نوار کناری انتخاب و روی «تایید و اجرا» کلیک کنید.")
        return

    provider = MultiProviderOHLC()

    with st.spinner("در حال دریافت داده و اجرای موتور تحلیل..."):
        try:
            result = run_signal_engine(
                asset=asset_symbol, coingecko_id=coingecko_id, quote=quote, timeframe=timeframe,
                direction=direction, rr_target=rr_target,
                provider=provider, lookback=lookback,
                calibration_window=calib_window,
            )
        except (SignalEngineError, CalibrationError, ProviderError) as e:
            st.error(f"⚠️ {e}")
            st.stop()

    show_standard = "Stop-Hunt Trigger" not in exec_mode or "هر دو" in exec_mode
    show_trigger = "Standard" not in exec_mode.split("(")[0] or "هر دو" in exec_mode

    risk_cfg = RiskConfig(
        risk_percent_per_trade=risk_pct,
        max_daily_loss_percent=max_daily_loss,
        max_consecutive_losses=int(max_consec_losses),
        min_rr=1.0,
        account_equity=account_equity,
    )
    risk_mgr = RiskManager(risk_cfg)

    col1, col2 = st.columns(2)

    with col1:
        if show_standard:
            if result.standard_signals:
                for sig in result.standard_signals:
                    label = "خرید" if sig.direction == "BUY" else "فروش"
                    explanation = explain_signal(sig, result.profile, asset_symbol, rr_target)
                    render_signal_box(f"📍 سیگنال {label} — لحظه‌ای", sig, explanation)
            else:
                st.caption("در حال حاضر سیگنال لحظه‌ای تأییدشده‌ای وجود ندارد.")

    with col2:
        if show_trigger:
            if result.stophunt_signals:
                for sig in result.stophunt_signals:
                    label = "خرید" if sig.direction == "BUY" else "فروش"
                    explanation = explain_signal(sig, result.profile, asset_symbol, rr_target)
                    render_signal_box(f"🎯 سفارش {label} — Stop-Hunt Trigger", sig, explanation)
            else:
                st.caption("در حال حاضر سطح فعالی برای سفارش معلق Stop-Hunt وجود ندارد.")


if __name__ == "__main__":
    main()
