import json
import math
import os
import random
import re
import threading
import tkinter as tk
import tkinter.font as tkfont
from io import BytesIO
from pathlib import Path
from tkinter import ttk
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError
except ImportError as exc:
    raise SystemExit("Install Pillow first: pip install pillow") from exc


DEFAULT_IMAGE_WIDTH = 450
DEFAULT_IMAGE_HEIGHT = 250
IMAGE_ASPECT_RATIO = DEFAULT_IMAGE_HEIGHT / DEFAULT_IMAGE_WIDTH
MIN_IMAGE_WIDTH = 260
MAX_IMAGE_WIDTH = 680
LOCAL_IMAGE_DIR = Path(__file__).resolve().parent / "images"
RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
COMMONS_SEARCH_LIMIT = 24
HTTP_HEADERS = {
    "User-Agent": "MarinePropellerAdvisor/1.0 (educational tkinter app)",
}

SHIP_TYPES = (
    "cargo",
    "tanker",
    "container",
    "passenger",
    "yacht",
    "tug",
    "fishing",
    "naval",
)

SHIP_LABELS = {
    "cargo": {"en": "Cargo", "ar": "سفينة بضائع"},
    "tanker": {"en": "Tanker", "ar": "ناقلة"},
    "container": {"en": "Container Ship", "ar": "سفينة حاويات"},
    "passenger": {"en": "Passenger Ship", "ar": "سفينة ركاب"},
    "yacht": {"en": "Yacht", "ar": "يخت"},
    "tug": {"en": "Tugboat", "ar": "قاطرة بحرية"},
    "fishing": {"en": "Fishing Vessel", "ar": "سفينة صيد"},
    "naval": {"en": "Naval Ship", "ar": "سفينة بحرية"},
}

SHIP_SEARCH_TERMS = {
    "cargo": "cargo ship",
    "tanker": "oil tanker",
    "container": "container ship",
    "passenger": "passenger ship",
    "yacht": "yacht",
    "tug": "tugboat",
    "fishing": "fishing vessel",
    "naval": "naval ship",
}

SHIP_RPM_FACTORS = {
    "cargo": 25,
    "tanker": 25,
    "container": 35,
    "passenger": 35,
    "yacht": 50,
    "tug": 30,
    "fishing": 28,
    "naval": 32,
}

HIGH_BLADE_TYPES = {"cargo", "tanker", "container"}
BLADE_WORDS = {
    4: "four",
    5: "five",
}

lang = "en"
image_request_id = 0
current_display_image = None


def t(en, ar):
    return en if lang == "en" else ar


def choose_font(preferred_names, fallback="TkDefaultFont"):
    available_fonts = {font.lower(): font for font in tkfont.families(root)}
    for name in preferred_names:
        if name.lower() in available_fonts:
            return available_fonts[name.lower()]

    return fallback


def app_font(size, weight=None):
    family = ARABIC_FONT if lang == "ar" else LATIN_FONT
    return (family, size, weight) if weight else (family, size)


def ship_display_name(ship_type):
    labels = SHIP_LABELS.get(ship_type, {"en": ship_type.title(), "ar": ship_type})
    return labels["en"] if lang == "en" else labels["ar"]


def ship_display_values():
    return [ship_display_name(ship_type) for ship_type in SHIP_TYPES]


def get_selected_ship_type():
    selected_value = ship_combo.get().strip()
    normalized_value = selected_value.lower()

    for ship_type in SHIP_TYPES:
        labels = SHIP_LABELS[ship_type]
        if normalized_value == ship_type or selected_value in labels.values():
            return ship_type

    return ""


def set_ship_combo(ship_type):
    ship_combo.set(ship_display_name(ship_type))


def metric_line(en_label, ar_label, value):
    return f"{t(en_label, ar_label)}: {value}"


def dedupe(items):
    seen = set()
    unique_items = []

    for item in items:
        normalized_item = " ".join(item.split()).lower()
        if normalized_item in seen:
            continue

        seen.add(normalized_item)
        unique_items.append(item)

    return unique_items


def normalized_search_text(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower())


def blade_phrases(blade_count):
    word = BLADE_WORDS.get(blade_count, str(blade_count))
    number = str(blade_count)
    return [
        f"{number} blade",
        f"{number} bladed",
        f"{word} blade",
        f"{word} bladed",
    ]


def has_blade_match(text, blade_count):
    normalized_text = normalized_search_text(text)
    return any(phrase in normalized_text for phrase in blade_phrases(blade_count))


def score_image_candidate(candidate, blade_count, recommendation):
    title = candidate.get("title", "")
    image_url = candidate.get("url", "")
    searchable_text = normalized_search_text(f"{title} {image_url}")
    score = 0

    if has_blade_match(searchable_text, blade_count):
        score += 100

    if "propeller" in searchable_text:
        score += 20

    if "ship" in searchable_text or "marine" in searchable_text:
        score += 12

    if recommendation["kind"] == "controllable" and "controllable" in searchable_text:
        score += 20
    elif recommendation["kind"] == "ducted" and (
        "ducted" in searchable_text or "kort" in searchable_text or "nozzle" in searchable_text
    ):
        score += 20
    elif recommendation["kind"] == "fixed" and ("fixed" in searchable_text or "screw" in searchable_text):
        score += 10

    if "aircraft" in searchable_text or "aeroplane" in searchable_text or "airplane" in searchable_text:
        score -= 25

    return score


def read_positive_number(entry, en_name, ar_name):
    raw_value = entry.get().strip()

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(t(f"{en_name} must be a number.", f"{ar_name} يجب أن يكون رقمًا.")) from exc

    if value <= 0:
        raise ValueError(
            t(
                f"{en_name} must be greater than zero.",
                f"{ar_name} يجب أن يكون أكبر من صفر.",
            )
        )

    return value


def estimate_propeller(power_kw, speed_knots, ship_type):
    rpm = SHIP_RPM_FACTORS[ship_type] * math.sqrt(power_kw)
    speed_ms = speed_knots * 0.5144

    diameter = (power_kw / (rpm * 3)) * 2
    pitch_ratio = 0.6 if speed_knots < 12 else 0.8 if speed_knots < 20 else 1.0
    pitch = diameter * pitch_ratio

    revolutions_per_second = rpm / 60
    theoretical_advance = revolutions_per_second * pitch
    if theoretical_advance <= 0:
        raise ValueError(t("Unable to calculate slip from these inputs.", "لا يمكن حساب الانزلاق بهذه القيم."))

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


def slip_note(slip):
    if slip < 0:
        return t(
            "Note: negative slip usually means the speed is too high for this rough estimate.",
            "ملاحظة: الانزلاق السالب يعني غالبًا أن السرعة عالية جدًا لهذا التقدير التقريبي.",
        )

    if slip > 0.6:
        return t(
            "Note: high slip suggests a low-speed thrust setup may be more suitable.",
            "ملاحظة: الانزلاق العالي يشير إلى أن إعداد دفع للسرعات المنخفضة قد يكون أنسب.",
        )

    return ""


def build_image_queries(ship_type, speed_knots, estimate, recommendation):
    ship_term = SHIP_SEARCH_TERMS.get(ship_type, f"{ship_type} ship")
    blade_count = estimate["blades"]
    efficiency_percent = estimate["efficiency"] * 100
    slip = estimate["slip"]
    blade_word = BLADE_WORDS.get(blade_count, str(blade_count))

    exact_blade_queries = [
        f"{blade_word} blade ship propeller",
        f"{blade_word}-bladed ship propeller",
        f"{blade_count} blade ship propeller",
        f"{blade_count}-blade marine propeller",
        f"{blade_word} blade {recommendation['query']}",
        f"{blade_count} blade {recommendation['query']}",
    ]

    broad_queries = [
        f"{recommendation['query']} {ship_term}",
        f"{blade_word} blade propeller",
        f"{blade_count} blade propeller",
        f"{ship_term} propeller",
        f"{ship_term} {recommendation['en']}",
        recommendation["query"],
        "marine propeller",
    ]

    if recommendation["kind"] == "controllable":
        exact_blade_queries.extend(
            [
                f"{blade_word} blade controllable pitch propeller",
                f"{blade_count} blade controllable pitch propeller",
            ]
        )
        broad_queries.extend(
            [
                f"high speed marine propeller {ship_term}",
                "high speed marine propeller",
                "controllable pitch propeller ship",
            ]
        )
    elif recommendation["kind"] == "ducted":
        exact_blade_queries.extend(
            [
                f"{blade_word} blade ducted propeller",
                f"{blade_count} blade ducted propeller",
            ]
        )
        broad_queries.extend(
            [
                f"Kort nozzle propeller {ship_term}",
                f"ducted propeller {ship_term}",
                "Kort nozzle",
                "tugboat ducted propeller drydock",
            ]
        )
    else:
        exact_blade_queries.extend(
            [
                f"{blade_word} blade fixed pitch propeller",
                f"{blade_count} blade fixed pitch propeller",
            ]
        )
        broad_queries.extend(
            [
                f"fixed pitch propeller {ship_term}",
                f"ship propeller drydock {ship_term}",
                "ship propeller drydock",
                "marine screw propeller ship",
            ]
        )

    if slip < 0:
        broad_queries.append(f"high speed ship propeller {ship_term}")
    elif slip > 0.6:
        broad_queries.append(f"low speed thrust propeller {ship_term}")

    if efficiency_percent < 65:
        broad_queries.append(f"marine propeller cavitation {ship_term}")

    return {
        "exact": dedupe(exact_blade_queries),
        "broad": dedupe(broad_queries),
    }


def fetch_unsplash_image(search_groups):
    access_key = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()
    if not access_key:
        return None

    try:
        query = random.choice(search_groups["exact"] or search_groups["broad"])
        api_url = "https://api.unsplash.com/photos/random?" + urlencode(
            {"query": query, "orientation": "landscape"}
        )
        request = Request(
            api_url,
            headers={
                **HTTP_HEADERS,
                "Authorization": f"Client-ID {access_key}",
                "Accept-Version": "v1",
            },
        )
        with urlopen(request, timeout=8) as api_response:
            data = json.load(api_response)

        image_url = data.get("urls", {}).get("regular")
        if not image_url:
            return None

        return fetch_url_image(image_url)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, UnidentifiedImageError):
        return None


def search_commons_images(query, blade_count, recommendation, require_blade_match):
    params = urlencode(
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrnamespace": 6,
            "gsrsearch": query,
            "gsrlimit": COMMONS_SEARCH_LIMIT,
            "prop": "imageinfo",
            "iiprop": "url|mime|size",
            "iiurlwidth": 900,
        }
    )

    try:
        request = Request(f"{COMMONS_API_URL}?{params}", headers=HTTP_HEADERS)
        with urlopen(request, timeout=8) as response:
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return []

    candidates = []
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        image_info = page.get("imageinfo", [])
        if not image_info:
            continue

        info = image_info[0]
        mime_type = info.get("mime", "")
        if not mime_type.startswith("image/"):
            continue

        image_url = info.get("thumburl") or info.get("url")
        if not image_url:
            continue

        width = info.get("thumbwidth") or info.get("width") or 0
        height = info.get("thumbheight") or info.get("height") or 0
        if width < 250 or height < 150:
            continue

        candidate = {
            "title": page.get("title", ""),
            "url": image_url,
        }
        if require_blade_match and not has_blade_match(f"{candidate['title']} {candidate['url']}", blade_count):
            continue

        candidate["score"] = score_image_candidate(candidate, blade_count, recommendation)
        candidates.append(candidate)

    random.shuffle(candidates)
    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    return candidates


def fetch_url_image(image_url):
    try:
        request = Request(image_url, headers=HTTP_HEADERS)
        with urlopen(request, timeout=8) as response:
            content_type = response.headers.get("Content-Type", "")
            if "image" not in content_type:
                return None

            image = Image.open(BytesIO(response.read()))

        image.load()
        return image
    except (HTTPError, URLError, TimeoutError, OSError, UnidentifiedImageError):
        return None


def fetch_commons_search_image(search_groups, blade_count, recommendation):
    exact_queries = list(search_groups["exact"])
    broad_queries = list(search_groups["broad"])
    random.shuffle(exact_queries)
    random.shuffle(broad_queries)

    for query in exact_queries:
        for candidate in search_commons_images(query, blade_count, recommendation, True):
            image = fetch_url_image(candidate["url"])
            if image is not None:
                return image

    for query in broad_queries:
        for candidate in search_commons_images(query, blade_count, recommendation, False):
            image = fetch_url_image(candidate["url"])
            if image is not None:
                return image

    return None


def load_local_image(fallback_file):
    path = LOCAL_IMAGE_DIR / fallback_file

    try:
        image = Image.open(path)
        image.load()
        return image
    except (FileNotFoundError, OSError, UnidentifiedImageError):
        return None


def current_image_size():
    width = round(float(image_width_var.get()))
    height = max(150, round(width * IMAGE_ASPECT_RATIO))
    return width, height


def sync_image_panel_size():
    if "image_panel" not in globals():
        return

    width, height = current_image_size()
    image_panel.config(width=width, height=height)
    image_label.config(wraplength=max(160, width - 36))
    if "image_size_value" in globals():
        image_size_value.config(text=f"{width} x {height} px")


def prepare_image(image):
    image_size = current_image_size()
    fitted_image = ImageOps.contain(image.convert("RGB"), image_size, RESAMPLE)
    canvas = Image.new("RGB", image_size, "#f8fafc")
    offset = (
        (image_size[0] - fitted_image.width) // 2,
        (image_size[1] - fitted_image.height) // 2,
    )
    canvas.paste(fitted_image, offset)
    return canvas


def show_image_placeholder(message):
    global current_display_image

    current_display_image = None
    sync_image_panel_size()
    image_label.config(image="", text=message)
    image_label.image = None


def display_image(image):
    global current_display_image

    current_display_image = image
    sync_image_panel_size()
    render_displayed_image()


def render_displayed_image():
    if current_display_image is None:
        return

    photo = ImageTk.PhotoImage(prepare_image(current_display_image))
    image_label.config(image=photo, text="")
    image_label.image = photo


def resize_displayed_image(value=None):
    sync_image_panel_size()
    render_displayed_image()


def finish_image_load(request_id, image):
    if request_id != image_request_id:
        return

    if image is None:
        show_image_placeholder(
            t(
                "No image available. Check your internet connection or add a matching file in images/.",
                "لا توجد صورة. تحقق من اتصال الإنترنت أو أضف ملفًا مناسبًا داخل images/.",
            )
        )
        return

    display_image(image)


def load_image(search_groups, blade_count, recommendation, fallback_file):
    global image_request_id

    image_request_id += 1
    request_id = image_request_id
    show_image_placeholder(
        t(
            f"Searching for a {blade_count}-blade propeller image...",
            f"جاري البحث عن صورة رفاس بعدد {blade_count} شفرات...",
        )
    )

    def worker():
        image = (
            fetch_commons_search_image(search_groups, blade_count, recommendation)
            or fetch_unsplash_image(search_groups)
            or load_local_image(fallback_file)
        )
        try:
            root.after(0, lambda: finish_image_load(request_id, image))
        except tk.TclError:
            pass

    threading.Thread(target=worker, daemon=True).start()


def clear_outputs():
    global image_request_id

    image_request_id += 1
    result_label.config(text="", style="Result.TLabel")
    recommendation_label.config(text="")
    show_image_placeholder(t("Image preview", "معاينة الصورة"))


def reset():
    entry_power.delete(0, tk.END)
    entry_speed.delete(0, tk.END)
    set_ship_combo(SHIP_TYPES[0])
    clear_outputs()


def calculate():
    try:
        power = read_positive_number(entry_power, "Power", "القدرة")
        speed_knots = read_positive_number(entry_speed, "Speed", "السرعة")
        ship_type = get_selected_ship_type()

        if ship_type not in SHIP_RPM_FACTORS:
            raise ValueError(t("Choose a valid ship type.", "اختر نوع سفينة صحيحًا."))

        estimate = estimate_propeller(power, speed_knots, ship_type)
    except ValueError as exc:
        result_label.config(text=str(exc), style="Error.TLabel")
        recommendation_label.config(text="")
        return

    recommendation = recommend_propeller(ship_type, speed_knots, estimate["slip"])
    search_groups = build_image_queries(ship_type, speed_knots, estimate, recommendation)
    efficiency_percent = estimate["efficiency"] * 100
    note = slip_note(estimate["slip"])

    lines = [
        metric_line("Ship", "السفينة", ship_display_name(ship_type)),
        metric_line("RPM", "عدد الدورات في الدقيقة", f"{estimate['rpm']:.2f}"),
        metric_line("Blades", "الشفرات", f"{estimate['blades']}"),
        metric_line("Diameter", "القطر", f"{estimate['diameter']:.2f} {t('m', 'متر')}"),
        metric_line("Pitch", "الميل", f"{estimate['pitch']:.2f} {t('m', 'متر')}"),
        metric_line("Slip", "الانزلاق", f"{estimate['slip']:.3f}"),
        metric_line("Efficiency", "الكفاءة", f"{efficiency_percent:.2f}%"),
    ]

    if note:
        lines.extend(("", note))

    result_label.config(text="\n".join(lines), style="Result.TLabel")
    recommendation_lines = [
        t("Recommendation", "التوصية"),
        recommendation["en"] if lang == "en" else recommendation["ar"],
    ]
    recommendation_label.config(text="\n".join(recommendation_lines))
    load_image(search_groups, estimate["blades"], recommendation, recommendation["fallback"])


def change_lang(event=None):
    global lang

    lang = lang_combo.get()
    refresh_language()


def configure_styles():
    style.configure("App.TFrame", background="#edf2f7")
    style.configure("Panel.TLabelframe", background="#ffffff", bordercolor="#d0d7de", relief="solid")
    style.configure(
        "Panel.TLabelframe.Label",
        background="#edf2f7",
        foreground="#1f2937",
        font=app_font(11, "bold"),
    )
    style.configure("Panel.TFrame", background="#ffffff")
    style.configure("Title.TLabel", background="#edf2f7", foreground="#111827", font=app_font(20, "bold"))
    style.configure("HeaderField.TLabel", background="#edf2f7", foreground="#374151", font=app_font(10))
    style.configure("Field.TLabel", background="#ffffff", foreground="#374151", font=app_font(10))
    style.configure("Result.TLabel", background="#ffffff", foreground="#111827", font=app_font(10), justify="right" if lang == "ar" else "left")
    style.configure("Error.TLabel", background="#ffffff", foreground="#b42318", font=app_font(10, "bold"), justify="right" if lang == "ar" else "left")
    style.configure("Accent.TButton", font=app_font(10, "bold"), padding=(12, 8))
    style.configure("Plain.TButton", font=app_font(10), padding=(12, 8))


def apply_language_layout():
    is_arabic = lang == "ar"
    sticky_side = "e" if is_arabic else "w"
    opposite_side = "w" if is_arabic else "e"
    justify = "right" if is_arabic else "left"

    title_label.grid_configure(sticky=sticky_side)
    language_frame.grid_configure(sticky=opposite_side)
    language_label.config(anchor=sticky_side, justify=justify)

    for label in (
        power_label,
        speed_label,
        ship_label,
        result_label,
        recommendation_label,
        image_size_label,
        image_size_value,
    ):
        label.config(anchor=sticky_side, justify=justify)

    result_label.grid_configure(sticky="ew")
    recommendation_label.grid_configure(sticky="ew")
    entry_power.config(justify="right" if is_arabic else "left")
    entry_speed.config(justify="right" if is_arabic else "left")
    image_label.config(font=app_font(10), justify="center")


def refresh_language():
    selected_ship_type = get_selected_ship_type() or SHIP_TYPES[0]

    configure_styles()
    apply_language_layout()

    root.title(t("Marine Propeller Advisor", "مستشار الرفاس البحري"))
    title_label.config(text=t("Marine Propeller Advisor", "مستشار الرفاس البحري"))
    language_label.config(text=t("Language", "اللغة"))
    input_frame.config(text=t("Inputs", "المدخلات"))
    power_label.config(text=t("Power (kW)", "القدرة (kW)"))
    speed_label.config(text=t("Speed (knots)", "السرعة (عقدة)"))
    ship_label.config(text=t("Ship Type", "نوع السفينة"))
    calculate_button.config(text=t("Calculate", "احسب"))
    reset_button.config(text=t("Reset", "إعادة ضبط"))
    result_frame.config(text=t("Estimate", "التقدير"))
    recommendation_frame.config(text=t("Recommendation", "التوصية"))
    image_frame.config(text=t("Image", "الصورة"))
    image_size_label.config(text=t("Image width", "عرض الصورة"))
    image_size_reset_button.config(text=t("Reset size", "إعادة الحجم"))
    lang_combo.config(font=app_font(10))
    ship_combo.config(values=ship_display_values(), font=app_font(10))
    set_ship_combo(selected_ship_type)

    if not result_label.cget("text") and not recommendation_label.cget("text"):
        show_image_placeholder(t("Image preview", "معاينة الصورة"))


def update_scroll_region(event=None):
    scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))


def resize_scroll_frame(event):
    requested_width = main_frame.winfo_reqwidth()
    scroll_canvas.itemconfigure(main_window, width=max(event.width, requested_width))

    if "result_label" in globals():
        wraplength = max(260, event.width - 96)
        result_label.config(wraplength=wraplength)
        recommendation_label.config(wraplength=wraplength)


def on_mousewheel(event):
    if event.num == 4:
        scroll_canvas.yview_scroll(-1, "units")
    elif event.num == 5:
        scroll_canvas.yview_scroll(1, "units")
    else:
        scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


def reset_image_size():
    image_width_var.set(DEFAULT_IMAGE_WIDTH)
    resize_displayed_image()


root = tk.Tk()
root.geometry("640x780")
root.minsize(420, 480)
root.configure(bg="#edf2f7")
root.rowconfigure(0, weight=1)
root.columnconfigure(0, weight=1)

LATIN_FONT = choose_font(["Segoe UI", "Noto Sans", "DejaVu Sans", "Arial"])
ARABIC_FONT = choose_font(["Noto Sans Arabic", "Amiri", "DejaVu Sans", "Arial"])
image_width_var = tk.DoubleVar(value=DEFAULT_IMAGE_WIDTH)

style = ttk.Style(root)
try:
    style.theme_use("clam")
except tk.TclError:
    pass
configure_styles()

scroll_canvas = tk.Canvas(root, bg="#edf2f7", highlightthickness=0)
vertical_scrollbar = ttk.Scrollbar(root, orient="vertical", command=scroll_canvas.yview)
horizontal_scrollbar = ttk.Scrollbar(root, orient="horizontal", command=scroll_canvas.xview)
scroll_canvas.configure(
    yscrollcommand=vertical_scrollbar.set,
    xscrollcommand=horizontal_scrollbar.set,
)
scroll_canvas.grid(row=0, column=0, sticky="nsew")
vertical_scrollbar.grid(row=0, column=1, sticky="ns")
horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

main_frame = ttk.Frame(scroll_canvas, padding=24, style="App.TFrame")
main_window = scroll_canvas.create_window((0, 0), window=main_frame, anchor="nw")
main_frame.bind("<Configure>", update_scroll_region)
scroll_canvas.bind("<Configure>", resize_scroll_frame)
root.bind_all("<MouseWheel>", on_mousewheel)
root.bind_all("<Button-4>", on_mousewheel)
root.bind_all("<Button-5>", on_mousewheel)
main_frame.columnconfigure(0, weight=1)

header_frame = ttk.Frame(main_frame, style="App.TFrame")
header_frame.grid(row=0, column=0, sticky="ew")
header_frame.columnconfigure(0, weight=1)

title_label = ttk.Label(header_frame, style="Title.TLabel")
title_label.grid(row=0, column=0, sticky="w")

language_frame = ttk.Frame(header_frame, style="App.TFrame")
language_frame.grid(row=0, column=1, sticky="e")

language_label = ttk.Label(language_frame, style="HeaderField.TLabel")
language_label.grid(row=0, column=0, padx=(0, 8))

lang_combo = ttk.Combobox(language_frame, values=["en", "ar"], width=6, state="readonly")
lang_combo.grid(row=0, column=1)
lang_combo.set(lang)
lang_combo.bind("<<ComboboxSelected>>", change_lang)

input_frame = ttk.LabelFrame(main_frame, padding=16, style="Panel.TLabelframe")
input_frame.grid(row=1, column=0, sticky="ew", pady=(18, 12))
input_frame.columnconfigure(1, weight=1)

power_label = ttk.Label(input_frame, style="Field.TLabel")
power_label.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=6)
entry_power = ttk.Entry(input_frame)
entry_power.grid(row=0, column=1, sticky="ew", pady=6)

speed_label = ttk.Label(input_frame, style="Field.TLabel")
speed_label.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=6)
entry_speed = ttk.Entry(input_frame)
entry_speed.grid(row=1, column=1, sticky="ew", pady=6)

ship_label = ttk.Label(input_frame, style="Field.TLabel")
ship_label.grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)
ship_combo = ttk.Combobox(input_frame, values=SHIP_TYPES, state="readonly")
ship_combo.grid(row=2, column=1, sticky="ew", pady=6)
set_ship_combo(SHIP_TYPES[0])

button_frame = ttk.Frame(input_frame, style="Panel.TFrame")
button_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
button_frame.columnconfigure(0, weight=1)
button_frame.columnconfigure(1, weight=1)

calculate_button = ttk.Button(button_frame, command=calculate, style="Accent.TButton")
calculate_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
reset_button = ttk.Button(button_frame, command=reset, style="Plain.TButton")
reset_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

result_frame = ttk.LabelFrame(main_frame, padding=16, style="Panel.TLabelframe")
result_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))
result_frame.columnconfigure(0, weight=1)

result_label = ttk.Label(result_frame, text="", wraplength=540, style="Result.TLabel")
result_label.grid(row=0, column=0, sticky="w")

recommendation_frame = ttk.LabelFrame(main_frame, padding=16, style="Panel.TLabelframe")
recommendation_frame.grid(row=3, column=0, sticky="ew", pady=(0, 12))
recommendation_frame.columnconfigure(0, weight=1)

recommendation_label = ttk.Label(recommendation_frame, text="", wraplength=540, style="Result.TLabel")
recommendation_label.grid(row=0, column=0, sticky="w")

image_frame = ttk.LabelFrame(main_frame, padding=16, style="Panel.TLabelframe")
image_frame.grid(row=4, column=0, sticky="ew")

image_control_frame = ttk.Frame(image_frame, style="Panel.TFrame")
image_control_frame.pack(fill="x", pady=(0, 12))
image_control_frame.columnconfigure(1, weight=1)

image_size_label = ttk.Label(image_control_frame, style="Field.TLabel")
image_size_label.grid(row=0, column=0, sticky="w", padx=(0, 10))

image_width_slider = ttk.Scale(
    image_control_frame,
    from_=MIN_IMAGE_WIDTH,
    to=MAX_IMAGE_WIDTH,
    variable=image_width_var,
    command=resize_displayed_image,
)
image_width_slider.grid(row=0, column=1, sticky="ew", padx=(0, 10))

image_size_value = ttk.Label(image_control_frame, width=14, style="Field.TLabel")
image_size_value.grid(row=0, column=2, sticky="e", padx=(0, 10))

image_size_reset_button = ttk.Button(
    image_control_frame,
    command=reset_image_size,
    style="Plain.TButton",
)
image_size_reset_button.grid(row=0, column=3, sticky="e")

initial_image_width, initial_image_height = current_image_size()
image_panel = tk.Frame(
    image_frame,
    width=initial_image_width,
    height=initial_image_height,
    bg="#f8fafc",
    highlightbackground="#d0d7de",
    highlightthickness=1,
)
image_panel.pack(anchor="center")
image_panel.pack_propagate(False)

image_label = tk.Label(
    image_panel,
    bg="#f8fafc",
    fg="#6b7280",
    font=app_font(10),
    justify="center",
    wraplength=400,
)
image_label.pack(fill="both", expand=True)

refresh_language()
root.mainloop()
