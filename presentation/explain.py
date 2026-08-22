"""
presentation/explain.py
=========================
Converts a TradeSignal's technical basis strings into a short, plain,
colloquial Persian explanation — no jargon, a few lines, meant to sit behind
a hidden toggle under the signal box, per the user's explicit UI request:

    "توضیحات ... به زبان کاملا ساده و عامیانه و کاربردی و پرهیز از کلمات
    تخصصی و سنگین، در چند خط مختصر"

Also encodes, in plain words, the priority rule the user set:
    سابقه نوسانات و Stop Hunt آن رمزارز  >  نسبت R:R انتخابی در تنظیمات
"""

from __future__ import annotations
from models.entry_sl_tp import TradeSignal
from engines.calibrator import AssetTimeframeProfile


def explain_signal(signal: TradeSignal, profile: AssetTimeframeProfile,
                    asset: str, rr_target: float) -> str:
    direction_fa = "خرید" if signal.direction == "BUY" else "فروش"
    used_liquidity_target = "fell back" not in signal.tp_basis

    lines = []

    if signal.mode == "STANDARD":
        lines.append(
            f"این سیگنال {direction_fa} همین الان، بر اساس رفتار قبلی خود {asset} فعال شده — "
            f"یعنی قیمت واقعاً یک سطح نقدینگی رو شکار کرده، دوباره برگشته، و یک حرکت قوی در جهت "
            f"{direction_fa} نشون داده."
        )
    else:
        lines.append(
            f"این یک سفارش معلق {direction_fa} هست، یعنی هنوز فعال نشده. وقتی قیمت به این سطح برسه و "
            f"نقدینگی اونجا رو جمع کنه، سفارش خودکار باز میشه."
        )

    lines.append(
        f"حد ضرر عمداً کمی فاصله از نقطه شکار قیمت گذاشته شده — این فاصله از روی نوسان‌های واقعی و "
        f"عمق شکارهای قبلی خود {asset} در همین تایم‌فریم حساب شده، نه یک عدد ثابت. هدفش اینه که با یک "
        f"نوسان کوچیک و بی‌اهمیت، حد ضرر زده نشه."
    )

    if used_liquidity_target:
        lines.append(
            "حد سود هم روی نزدیک‌ترین نقطه‌ای گذاشته شده که قبلاً نقدینگی واقعی دیگه‌ای اونجا جمع شده "
            "بود — یعنی جایی که احتمال واکنش قیمت بیشتره، نه فقط یک ضرب‌در ساده از عدد ریسک به پاداش."
        )
    else:
        lines.append(
            f"نقطه نقدینگی مناسبی برای حد سود پیدا نشد، برای همین از نسبت ریسک به پاداش انتخابی شما "
            f"(۱ به {rr_target:g}) استفاده شده."
        )

    if profile.regime_shift_flag:
        direction_word = "بیشتر" if profile.regime_shift_ratio > 1 else "کمتر"
        lines.append(
            f"⚠️ نوسان این رمزارز اخیراً نسبت به قبل به‌طور محسوسی {direction_word} شده. حد ضرر و سود "
            f"با داده تازه محاسبه شدن، ولی بهتره با احتیاط بیشتری تصمیم بگیرید."
        )

    lines.append(
        f"نکته مهم: در تعیین این قیمت‌ها، رفتار قبلی خود {asset} همیشه اولویت اول بود، و نسبت R:R "
        f"انتخابی شما فقط وقتی به کار رفت که نقطه نقدینگی واقعی دیگه‌ای در دسترس نبود."
    )

    return "\n\n".join(lines)
