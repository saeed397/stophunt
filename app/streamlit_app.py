"""
app/streamlit_app.py
=====================
Run with:  streamlit run app/streamlit_app.py

Settings page includes exactly what the user asked for, plus the additional
fields an engineer would consider necessary for this to be usable and safe:

  - Asset dropdown          (live top-500 by market cap, CoinGecko)
  - Quote currency          (USD/USDT — needed because CryptoCompare needs a tsym)
  - Timeframe dropdown      (auto-populates the higher timeframe used for MTF context)
  - R:R dropdown
  - Direction buttons       (Buy / Sell / Both)
  - Execution mode          (Standard vs Stop-Hunt Trigger) — this directly
                              implements Rule #4's two mandatory output types
  - Risk % per trade + account equity (feeds Risk Management)
  - Lookback candle count
  - Confirm button -> runs the engine and displays results

No values here are pre-filled with invented market data — the app always
calls the real providers before showing anything.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from config import TIMEFRAME_ORDER, HIGHER_TIMEFRAME_MAP, RR_OPTIONS, DEFAULT_RR
from data.providers import CryptoCompareProvider, CoinGeckoProvider, ProviderError
from engines.stophunt_engine import run_signal_engine, SignalEngineError
from engines.calibrator import CalibrationError
from risk.risk_management import RiskConfig, RiskManager

st.set_page_config(page_title="Liquidity-Centric Order System", layout="wide")


@st.cache_data(ttl=3600, show_spinner="Loading top 500 assets from CoinGecko...")
def load_asset_universe():
    provider = CoinGeckoProvider()
    return provider.get_top_assets()


def main():
    st.title("سامانه هوشمند تحلیل نقدینگی و سفارش‌گذاری")
    st.caption("Liquidity-Centric Order System — Stop Hunt Detection Engine")

    with st.sidebar:
        st.header("⚙️ تنظیمات (Settings)")

        try:
            universe = load_asset_universe()
        except ProviderError as e:
            st.error(f"عدم دسترسی به فهرست رمزارزها: {e}")
            st.stop()

        asset_labels = [f"{a['symbol']} — {a['name']}" for a in universe]
        asset_choice = st.selectbox("انتخاب رمزارز (Asset)", asset_labels, index=0)
        asset_symbol = universe[asset_labels.index(asset_choice)]["symbol"]

        quote = st.selectbox("ارز پایه (Quote)", ["USDT", "USD"], index=0)

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

        st.subheader("داده (Data)")
        lookback = st.slider("تعداد کندل تاریخی (Lookback)", 300, 2000, 1000, step=100)

        confirm = st.button("✅ تایید و اجرا (Confirm & Run)", type="primary", use_container_width=True)

    if not confirm:
        st.info("تنظیمات را در نوار کناری انتخاب و روی «تایید و اجرا» کلیک کنید.")
        st.markdown(
            "**فلسفه این سیستم (Rule #1):** هر سیگنال فقط از بک‌تست همان رمزارز و همان "
            "تایم‌فریم استخراج می‌شود؛ هیچ آستانه‌ای بین دارایی‌های مختلف مشترک یا ثابت نیست."
        )
        return

    provider = CryptoCompareProvider()

    with st.spinner("در حال دریافت داده و اجرای موتور تحلیل..."):
        try:
            result = run_signal_engine(
                asset=asset_symbol, quote=quote, timeframe=timeframe,
                direction=direction, rr_target=rr_target,
                provider=provider, lookback=lookback,
            )
        except (SignalEngineError, CalibrationError, ProviderError) as e:
            st.error(f"⚠️ {e}")
            st.stop()

    st.success(f"تحلیل {asset_symbol}/{quote} در تایم‌فریم {timeframe} (HTF: {result.higher_timeframe}) کامل شد.")

    col1, col2, col3 = st.columns(3)
    col1.metric("تعداد کندل معتبر", result.data_quality.total_candles)
    col2.metric("سطوح نقدینگی شناسایی‌شده", len(result.liquidity_levels))
    col3.metric("رویدادهای Sweep", len(result.sweeps))

    if result.notes:
        for n in result.notes:
            st.warning(n)

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

    tab1, tab2, tab3 = st.tabs(["📍 سیگنال Standard", "🎯 سفارش Stop-Hunt Trigger", "📊 امتیاز Setup"])

    with tab1:
        if not show_standard:
            st.caption("این حالت غیرفعال است.")
        elif not result.standard_signals:
            st.info("هیچ سیگنال Standard تأییدشده‌ای در این لحظه یافت نشد.")
        else:
            for sig in result.standard_signals:
                size = risk_mgr.position_size(sig.entry_price, sig.stop_loss)
                with st.container(border=True):
                    st.markdown(f"### {sig.direction} — {sig.mode}")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Entry", f"{sig.entry_price:.6g}")
                    c2.metric("Stop Loss", f"{sig.stop_loss:.6g}")
                    c3.metric("Take Profit", f"{sig.take_profit:.6g}")
                    st.write(f"**R:R واقعی:** {sig.rr_actual:.2f}  |  **حجم پیشنهادی:** {size:.6g} واحد")
                    st.caption(f"Entry basis: {sig.entry_basis}")
                    st.caption(f"SL basis: {sig.sl_basis}")
                    st.caption(f"TP basis: {sig.tp_basis}")

    with tab2:
        if not show_trigger:
            st.caption("این حالت غیرفعال است.")
        elif not result.stophunt_signals:
            st.info("هیچ سطح نقدینگی فعالی برای سفارش معلق Stop-Hunt یافت نشد.")
        else:
            for sig in result.stophunt_signals:
                size = risk_mgr.position_size(sig.entry_price, sig.stop_loss)
                with st.container(border=True):
                    st.markdown(f"### {sig.direction} — {sig.mode}")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Trigger (Entry)", f"{sig.entry_price:.6g}")
                    c2.metric("Stop Loss", f"{sig.stop_loss:.6g}")
                    c3.metric("Take Profit", f"{sig.take_profit:.6g}")
                    st.write(f"**R:R محاسبه‌شده:** {sig.rr_actual:.2f}  |  **حجم پیشنهادی:** {size:.6g} واحد")
                    st.caption(f"Entry basis: {sig.entry_basis}")
                    st.caption(f"SL basis: {sig.sl_basis}")
                    st.caption(f"TP basis: {sig.tp_basis}")
                    if sig.valid_until_index is not None:
                        st.caption(f"این سفارش تا کندل شماره {sig.valid_until_index} معتبر است، سپس منقضی می‌شود.")

    with tab3:
        if not result.setup_scores:
            st.info("امتیازی برای نمایش وجود ندارد.")
        else:
            st.dataframe(result.setup_scores, use_container_width=True)
            st.caption(
                "⚠️ وزن‌های این امتیاز فعلاً پیش‌فرض اولیه هستند و باید با Walk-Forward "
                "Backtest برای این دارایی/تایم‌فریم کالیبره شوند (به README مراجعه کنید)."
            )

    with st.expander("📐 پروفایل آماری این دارایی/تایم‌فریم (Rule #1 — مبتنی بر بک‌تست همین رمزارز)"):
        p = result.profile
        st.json({
            "n_candles_used": p.n_candles,
            "equal_level_tolerance": p.equal_level_tolerance,
            "median_wick_body_ratio": p.median_wick_body_ratio,
            "penetration_depth_atr_p50": p.penetration_depth_atr_p50,
            "penetration_depth_atr_p75": p.penetration_depth_atr_p75,
        })


if __name__ == "__main__":
    main()
