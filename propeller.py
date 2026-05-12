"""Propeller estimation and recommendation rules."""

import math

from constants import HIGH_BLADE_TYPES, SHIP_RPM_FACTORS
from i18n import translate


def estimate_propeller(power_kw, speed_knots, ship_type, language="en"):
    rpm = SHIP_RPM_FACTORS[ship_type] * math.sqrt(power_kw)
    speed_ms = speed_knots * 0.5144

    diameter = (power_kw / (rpm * 3)) * 2
    pitch_ratio = 0.6 if speed_knots < 12 else 0.8 if speed_knots < 20 else 1.0
    pitch = diameter * pitch_ratio

    revolutions_per_second = rpm / 60
    theoretical_advance = revolutions_per_second * pitch
    if theoretical_advance <= 0:
        raise ValueError(
            translate(
                language,
                "Unable to calculate slip from these inputs.",
                "لا يمكن حساب الانزلاق بهذه القيم.",
            )
        )

    slip = (theoretical_advance - speed_ms) / theoretical_advance
    blades = 5 if ship_type in HIGH_BLADE_TYPES else 4
    efficiency = max(0.0, min(1.0 - abs(slip) * 0.7, 1.0))

    return {
        "rpm": rpm,
        "diameter": diameter,
        "pitch": pitch,
        "slip": slip,
        "blades": blades,
        "efficiency": efficiency,
    }


def recommend_propeller(ship_type, speed_knots, slip):
    if speed_knots > 25:
        return {
            "kind": "controllable",
            "en": "Controllable Pitch Propeller",
            "ar": "رفاس متغير الزاوية",
            "query": "controllable pitch propeller",
            "fallback": "propeller.jpg",
        }

    if slip > 0.5:
        return {
            "kind": "ducted",
            "en": "Ducted Propeller",
            "ar": "رفاس داخل قناة",
            "query": "ducted propeller",
            "fallback": "ducted.jpg",
        }

    return {
        "kind": "fixed",
        "en": "Fixed Pitch Propeller",
        "ar": "رفاس ثابت",
        "query": "fixed pitch propeller",
        "fallback": "cargo.jpg",
    }


def slip_note(slip, language="en"):
    if slip < 0:
        return translate(
            language,
            "Note: negative slip usually means the speed is too high for this rough estimate.",
            "ملاحظة: الانزلاق السالب يعني غالبًا أن السرعة عالية جدًا لهذا التقدير التقريبي.",
        )

    if slip > 0.6:
        return translate(
            language,
            "Note: high slip suggests a low-speed thrust setup may be more suitable.",
            "ملاحظة: الانزلاق العالي يشير إلى أن إعداد دفع للسرعات المنخفضة قد يكون أنسب.",
        )

    return ""
