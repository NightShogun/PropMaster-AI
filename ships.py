"""Ship type labels and selection helpers."""

from constants import SHIP_LABELS, SHIP_TYPES


def ship_display_name(ship_type, language="en"):
    labels = SHIP_LABELS.get(ship_type, {"en": ship_type.title(), "ar": ship_type})
    return labels["en"] if language == "en" else labels["ar"]


def ship_display_values(language="en"):
    return [ship_display_name(ship_type, language) for ship_type in SHIP_TYPES]


def ship_type_from_display(selected_value):
    selected_value = selected_value.strip()
    normalized_value = selected_value.lower()

    for ship_type in SHIP_TYPES:
        labels = SHIP_LABELS[ship_type]
        if normalized_value == ship_type or selected_value in labels.values():
            return ship_type

    return ""
