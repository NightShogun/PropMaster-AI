import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import webbrowser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    from PIL import Image, ImageTk
except ImportError as exc:
    raise SystemExit("Install Pillow first: pip install pillow") from exc


DEFAULT_VIDEO_WIDTH = 450
DEFAULT_VIDEO_HEIGHT = 250
VIDEO_ASPECT_RATIO = DEFAULT_VIDEO_HEIGHT / DEFAULT_VIDEO_WIDTH
MIN_VIDEO_WIDTH = 260
MAX_VIDEO_WIDTH = 680
VIDEO_FPS = 24
COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
COMMONS_VIDEO_SEARCH_LIMIT = 32
MAX_EXACT_VIDEO_QUERY_ATTEMPTS = 6
MAX_BROAD_VIDEO_QUERY_ATTEMPTS = 12
MAX_LLAMA_VIDEO_QUERY_ATTEMPTS = 8
MAX_VIDEO_CANDIDATES = 18
LLAMA_VIDEO_SEARCH_ENABLED = os.getenv("LLAMA_VIDEO_SEARCH", "1").strip().lower() not in {"0", "false", "no"}
LLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
LLAMA_MODEL = os.getenv("OLLAMA_MODEL") or os.getenv("LLAMA_MODEL", "llama3.2:1b")
LLAMA_MODEL = LLAMA_MODEL.strip()
LLAMA_TIMEOUT_SECONDS = 8
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
video_request_id = 0
video_session_id = 0
current_video = None
video_process = None
video_playing = False
video_paused = False
video_frame_seen = False


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


def video_title(title):
    cleaned_title = title.replace("File:", "", 1).replace("_", " ").strip()
    return cleaned_title or t("Online propeller video", "فيديو رفاس من الإنترنت")


def commons_page_url(title):
    if not title:
        return ""

    return "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"), safe=":/")


def estimate_spec_text(estimate):
    return (
        f"{estimate['blades']}-blade, "
        f"{estimate['diameter']:.2f} m diameter, "
        f"{estimate['pitch']:.2f} m pitch, "
        f"{estimate['rpm']:.0f} RPM"
    )


def number_tokens(value):
    return {
        f"{value:.2f}",
        f"{value:.1f}",
        str(round(value)),
    }


def score_video_candidate(candidate, estimate, recommendation):
    title = candidate.get("title", "")
    video_url = candidate.get("url", "")
    raw_text = f"{title} {video_url}".lower()
    searchable_text = normalized_search_text(raw_text)
    score = 0
    blade_count = estimate["blades"]

    if has_blade_match(searchable_text, blade_count):
        score += 120

    if "propeller" in searchable_text or "screw" in searchable_text:
        score += 50

    if "ship" in searchable_text or "marine" in searchable_text or "vessel" in searchable_text:
        score += 35

    if "video" in searchable_text:
        score += 10

    if recommendation["kind"] == "controllable" and "controllable" in searchable_text:
        score += 35
    elif recommendation["kind"] == "ducted" and (
        "ducted" in searchable_text or "kort" in searchable_text or "nozzle" in searchable_text
    ):
        score += 35
    elif recommendation["kind"] == "fixed" and ("fixed" in searchable_text or "screw" in searchable_text):
        score += 20

    for token in number_tokens(estimate["rpm"]):
        if token in raw_text:
            score += 15
            break

    for dimension in ("diameter", "pitch"):
        for token in number_tokens(estimate[dimension]):
            if token in raw_text or f"{token}m" in raw_text:
                score += 15
                break

    if "aircraft" in searchable_text or "aeroplane" in searchable_text or "airplane" in searchable_text:
        score -= 80

    if "fan" in searchable_text:
        score -= 25

    if "fahrgeschaft" in searchable_text or "ride" in searchable_text or "amusement" in searchable_text:
        score -= 60

    return score


def parse_json_from_text(text):
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        pass

    if not text:
        return None

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def call_llama_json(prompt, timeout=LLAMA_TIMEOUT_SECONDS):
    if not LLAMA_VIDEO_SEARCH_ENABLED or not LLAMA_MODEL or not LLAMA_HOST:
        return None

    payload = {
        "model": LLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.15,
            "num_predict": 700,
        },
    }
    request = Request(
        f"{LLAMA_HOST}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            **HTTP_HEADERS,
            "Content-Type": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            response_data = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None

    return parse_json_from_text(response_data.get("response", ""))


def sanitize_search_query(query):
    query = re.sub(r"https?://\S+", "", str(query))
    query = re.sub(r"[^A-Za-z0-9 .,'()/-]+", " ", query)
    query = " ".join(query.split()).strip(" .,-/")
    if len(query) < 4 or len(query) > 120:
        return ""

    return query


def propeller_context(ship_type, speed_knots, estimate, recommendation):
    return (
        f"ship type: {SHIP_SEARCH_TERMS.get(ship_type, ship_type)}; "
        f"recommended propeller: {recommendation['en']}; "
        f"blades: {estimate['blades']}; "
        f"diameter: {estimate['diameter']:.2f} m; "
        f"pitch: {estimate['pitch']:.2f} m; "
        f"rpm: {estimate['rpm']:.0f}; "
        f"speed: {speed_knots:.1f} knots; "
        f"slip: {estimate['slip']:.3f}; "
        f"efficiency: {estimate['efficiency'] * 100:.1f}%"
    )


def build_llama_video_queries(ship_type, speed_knots, estimate, recommendation):
    prompt = f"""
You generate Wikimedia Commons media-search queries for marine propeller videos.
Use the estimated propeller specs, but avoid impossible over-specific queries.
Do not return URLs. Do not invent file names.
Return only JSON with this shape:
{{"queries":["query 1","query 2","query 3"]}}

Propeller context:
{propeller_context(ship_type, speed_knots, estimate, recommendation)}

Rules:
- Prefer marine, ship, vessel, boat, screw propeller, drydock, underwater, cavitation, nozzle, controllable pitch terms when relevant.
- Include blade count in some queries and omit it in some queries.
- Include the word video in most queries.
- Return 5 to 8 short English queries.
"""
    response = call_llama_json(prompt)
    if not isinstance(response, dict):
        return []

    queries = response.get("queries", [])
    if not isinstance(queries, list):
        return []

    return dedupe(
        query
        for query in (sanitize_search_query(item) for item in queries)
        if query
    )[:MAX_LLAMA_VIDEO_QUERY_ATTEMPTS]


def candidate_identity(candidate):
    return (
        candidate.get("title", "").strip().lower(),
        candidate.get("url", "").split("?", 1)[0].strip().lower(),
    )


def sort_video_candidates(candidates):
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.get("score", 0),
            video_title(candidate.get("title", "")).lower(),
            candidate.get("url", ""),
        ),
    )


def merge_video_candidates(existing_candidates, new_candidates):
    seen = {candidate_identity(candidate) for candidate in existing_candidates}
    for candidate in new_candidates:
        identity = candidate_identity(candidate)
        if identity in seen:
            continue

        seen.add(identity)
        existing_candidates.append(candidate)

    return sort_video_candidates(existing_candidates)[:MAX_VIDEO_CANDIDATES]


def choose_video_with_llama(candidates, ship_type, speed_knots, estimate, recommendation):
    if len(candidates) < 2:
        return None

    candidate_lines = []
    for index, candidate in enumerate(candidates[:MAX_VIDEO_CANDIDATES]):
        candidate_lines.append(
            f"{index}. title={video_title(candidate.get('title', ''))}; "
            f"mime={candidate.get('mime', '')}; "
            f"algorithm_score={candidate.get('score', 0)}"
        )

    prompt = f"""
Choose the most accurate marine propeller video candidate for the estimated propeller.
Only choose from the numbered candidates. Do not invent a URL.
Return only JSON with this shape:
{{"best_index":0,"reason":"short reason"}}

Propeller context:
{propeller_context(ship_type, speed_knots, estimate, recommendation)}

Candidates:
{chr(10).join(candidate_lines)}

Selection rules:
- Prefer actual marine/ship/boat propeller footage over aircraft, fans, rides, or generic machinery.
- Prefer matching propeller type and blade count when the title provides that information.
- If exact dimensions are unavailable, choose the closest marine propeller context.
"""
    response = call_llama_json(prompt)
    if not isinstance(response, dict):
        return None

    try:
        best_index = int(response.get("best_index"))
    except (TypeError, ValueError):
        return None

    if 0 <= best_index < min(len(candidates), MAX_VIDEO_CANDIDATES):
        return {
            "candidate": candidates[best_index],
            "reason": str(response.get("reason", "")).strip(),
        }

    return None


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


def build_video_queries(ship_type, speed_knots, estimate, recommendation):
    ship_term = SHIP_SEARCH_TERMS.get(ship_type, f"{ship_type} ship")
    blade_count = estimate["blades"]
    efficiency_percent = estimate["efficiency"] * 100
    slip = estimate["slip"]
    blade_word = BLADE_WORDS.get(blade_count, str(blade_count))
    diameter = estimate["diameter"]
    pitch = estimate["pitch"]
    rpm = estimate["rpm"]

    exact_spec_queries = [
        f"{blade_count} blade {recommendation['query']} {diameter:.2f} m diameter {pitch:.2f} m pitch {rpm:.0f} rpm video",
        f"{blade_word} blade {recommendation['query']} {ship_term} propeller video",
        f"{blade_count} blade marine propeller {ship_term} video",
        f"{blade_count} blade marine propeller {ship_term}",
        f"{blade_word}-bladed ship propeller video",
        f"{blade_count}-blade {recommendation['query']} video",
    ]

    broad_queries = [
        f"{recommendation['query']} {ship_term} video",
        f"{blade_word} blade propeller video",
        f"{blade_count} blade propeller video",
        f"{ship_term} propeller video",
        f"{ship_term} {recommendation['en']} video",
        f"{speed_knots:.0f} knot {ship_term} propeller video",
        f"{recommendation['query']} video",
        "marine propeller video",
        "ship propeller video",
        "boat propeller video",
        "propeller video",
        "video of propeller",
        f"{recommendation['query']} {ship_term}",
        f"{ship_term} propeller",
        recommendation["query"],
        "marine propeller",
        "ship propeller",
    ]

    if recommendation["kind"] == "controllable":
        exact_spec_queries.extend(
            [
                f"{blade_word} blade controllable pitch propeller video",
                f"{blade_count} blade controllable pitch propeller video",
            ]
        )
        broad_queries.extend(
            [
                f"high speed marine propeller {ship_term} video",
                "high speed marine propeller video",
                "controllable pitch propeller ship video",
            ]
        )
    elif recommendation["kind"] == "ducted":
        exact_spec_queries.extend(
            [
                f"{blade_word} blade ducted propeller video",
                f"{blade_count} blade ducted propeller video",
            ]
        )
        broad_queries.extend(
            [
                f"Kort nozzle propeller {ship_term} video",
                f"ducted propeller {ship_term} video",
                "Kort nozzle propeller video",
                "tugboat ducted propeller video",
            ]
        )
    else:
        exact_spec_queries.extend(
            [
                f"{blade_word} blade fixed pitch propeller video",
                f"{blade_count} blade fixed pitch propeller video",
            ]
        )
        broad_queries.extend(
            [
                f"fixed pitch propeller {ship_term} video",
                f"ship propeller drydock {ship_term} video",
                "ship propeller drydock video",
                "marine screw propeller ship video",
            ]
        )

    if slip < 0:
        broad_queries.append(f"high speed ship propeller {ship_term} video")
    elif slip > 0.6:
        broad_queries.append(f"low speed thrust propeller {ship_term} video")

    if efficiency_percent < 65:
        broad_queries.append(f"marine propeller cavitation {ship_term} video")

    return {
        "exact": dedupe(exact_spec_queries),
        "broad": dedupe(broad_queries),
    }


def search_commons_videos(query, estimate, recommendation, require_blade_match):
    params = urlencode(
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrnamespace": 6,
            "gsrsearch": query,
            "gsrlimit": COMMONS_VIDEO_SEARCH_LIMIT,
            "prop": "imageinfo",
            "iiprop": "url|mime|size",
        }
    )

    try:
        request = Request(f"{COMMONS_API_URL}?{params}", headers=HTTP_HEADERS)
        with urlopen(request, timeout=8) as response:
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return []

    candidates = []
    blade_count = estimate["blades"]
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        media_info = page.get("imageinfo", [])
        if not media_info:
            continue

        info = media_info[0]
        mime_type = info.get("mime", "")
        video_url = info.get("url", "")
        if not video_url:
            continue

        if not (
            mime_type.startswith("video/")
            or video_url.lower().endswith((".mp4", ".webm", ".ogv", ".mov"))
        ):
            continue

        candidate = {
            "title": page.get("title", ""),
            "url": video_url,
            "page_url": commons_page_url(page.get("title", "")),
            "mime": mime_type,
            "query": query,
        }
        if require_blade_match and not has_blade_match(f"{candidate['title']} {candidate['url']}", blade_count):
            continue

        candidate["score"] = score_video_candidate(candidate, estimate, recommendation)
        candidates.append(candidate)

    return sort_video_candidates(candidates)


def fetch_commons_search_video(search_groups, ship_type, speed_knots, estimate, recommendation, status_callback=None):
    candidates = []
    exact_queries = list(search_groups["exact"])[:MAX_EXACT_VIDEO_QUERY_ATTEMPTS]
    broad_queries = list(search_groups["broad"])[:MAX_BROAD_VIDEO_QUERY_ATTEMPTS]

    if status_callback:
        status_callback("Searching exact online video matches...", "جاري البحث عن فيديوهات مطابقة بدقة...")

    for query in exact_queries:
        candidates = merge_video_candidates(
            candidates,
            search_commons_videos(query, estimate, recommendation, True),
        )

    if status_callback:
        status_callback("Asking local Llama for extra search queries...", "جاري طلب عبارات بحث إضافية من Llama المحلي...")

    llama_queries = build_llama_video_queries(ship_type, speed_knots, estimate, recommendation)
    if status_callback:
        if llama_queries:
            status_callback("Searching Llama-generated online video queries...", "جاري البحث بعبارات Llama عبر الإنترنت...")
        else:
            status_callback("Llama unavailable; continuing with algorithmic search.", "Llama غير متاح؛ جار المتابعة بالبحث الخوارزمي.")

    for query in llama_queries:
        candidates = merge_video_candidates(
            candidates,
            search_commons_videos(query, estimate, recommendation, False),
        )

    if status_callback:
        status_callback("Searching broader online video candidates...", "جاري البحث عن فيديوهات أوسع صلة...")

    for query in broad_queries:
        candidates = merge_video_candidates(
            candidates,
            search_commons_videos(query, estimate, recommendation, False),
        )

    if not candidates:
        return None

    if status_callback:
        status_callback("Asking local Llama to rank video candidates...", "جاري طلب ترتيب نتائج الفيديو من Llama المحلي...")

    llama_choice = choose_video_with_llama(candidates, ship_type, speed_knots, estimate, recommendation)
    if llama_choice is not None:
        return {
            "video": llama_choice["candidate"],
            "source": "Llama-ranked online match",
            "source_ar": "نتيجة من الإنترنت رتبها Llama",
            "reason": llama_choice["reason"],
        }

    return {
        "video": candidates[0],
        "source": "Algorithm-ranked online match",
        "source_ar": "نتيجة من الإنترنت رتبتها الخوارزمية",
        "reason": "",
    }


def current_video_size():
    width = round(float(video_width_var.get()))
    height = max(150, round(width * VIDEO_ASPECT_RATIO))
    return width, height


def sync_video_panel_size():
    if "video_panel" not in globals():
        return

    width, height = current_video_size()
    video_panel.config(width=width, height=height)
    video_label.config(wraplength=max(160, width - 36))
    if "video_size_value" in globals():
        video_size_value.config(text=f"{width} x {height} px")


def set_video_status(message):
    if "video_status_label" in globals():
        video_status_label.config(text=message)


def set_video_buttons_state():
    if "video_play_button" not in globals():
        return

    has_video = current_video is not None
    video_play_button.config(state=tk.NORMAL if has_video and (not video_playing or video_paused) else tk.DISABLED)
    video_pause_button.config(state=tk.NORMAL if has_video and video_playing and not video_paused else tk.DISABLED)
    video_restart_button.config(state=tk.NORMAL if has_video else tk.DISABLED)
    video_open_button.config(state=tk.NORMAL if has_video else tk.DISABLED)


def show_video_placeholder(message):
    sync_video_panel_size()
    video_label.config(image="", text=message)
    video_label.image = None


def display_video_frame(session_id, frame):
    global video_frame_seen

    if session_id != video_session_id:
        return

    video_frame_seen = True
    photo = ImageTk.PhotoImage(frame)
    video_label.config(image=photo, text="")
    video_label.image = photo


def read_video_frame(stdout, frame_size):
    chunks = []
    remaining = frame_size

    while remaining > 0:
        chunk = stdout.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)

    return b"".join(chunks)


def build_ffmpeg_command(video_url, width, height):
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return None

    scale_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0xf8fafc,"
        f"fps={VIDEO_FPS}"
    )
    return [
        ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-user_agent",
        HTTP_HEADERS["User-Agent"],
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "4",
        "-i",
        video_url,
        "-an",
        "-vf",
        scale_filter,
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def video_reader(session_id, process, width, height):
    frame_size = width * height * 3
    frame_delay = 1 / VIDEO_FPS
    next_frame_time = time.monotonic()

    try:
        while session_id == video_session_id:
            while video_paused and session_id == video_session_id:
                time.sleep(0.05)

            frame_data = read_video_frame(process.stdout, frame_size)
            if frame_data is None:
                break

            frame = Image.frombytes("RGB", (width, height), frame_data)
            try:
                root.after(0, lambda sid=session_id, image=frame: display_video_frame(sid, image))
            except tk.TclError:
                break

            next_frame_time += frame_delay
            delay = next_frame_time - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_frame_time = time.monotonic()
    finally:
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass

        try:
            root.after(0, lambda sid=session_id: finish_video_playback(sid))
        except tk.TclError:
            pass


def terminate_video_process(process):
    if process is None or process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=0.6)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=0.6)
        except subprocess.TimeoutExpired:
            pass


def stop_video_playback(clear_frame=False):
    global video_session_id, video_process, video_playing, video_paused, video_frame_seen

    video_session_id += 1
    process = video_process
    video_process = None
    video_playing = False
    video_paused = False
    video_frame_seen = False
    terminate_video_process(process)

    if clear_frame and "video_label" in globals():
        show_video_placeholder(t("Video preview", "معاينة الفيديو"))

    set_video_buttons_state()


def start_video_playback(video):
    global video_session_id, video_process, video_playing, video_paused, video_frame_seen

    if video is None:
        return

    stop_video_playback(clear_frame=False)
    width, height = current_video_size()
    command = build_ffmpeg_command(video["url"], width, height)
    if command is None:
        show_video_placeholder(
            t(
                "Video found, but ffmpeg is required for in-app playback. Use Open Video.",
                "تم العثور على فيديو، لكن ffmpeg مطلوب للتشغيل داخل التطبيق. استخدم فتح الفيديو.",
            )
        )
        set_video_status(t("Playback unavailable: ffmpeg is not installed.", "التشغيل غير متاح: ffmpeg غير مثبت."))
        set_video_buttons_state()
        return

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=width * height * 3,
        )
    except OSError as exc:
        show_video_placeholder(t("Could not start video playback.", "تعذر بدء تشغيل الفيديو."))
        set_video_status(str(exc))
        set_video_buttons_state()
        return

    video_session_id += 1
    session_id = video_session_id
    video_process = process
    video_playing = True
    video_paused = False
    video_frame_seen = False
    show_video_placeholder(t("Starting video...", "جاري بدء الفيديو..."))
    set_video_status(t(f"Playing: {video_title(video['title'])}", f"تشغيل: {video_title(video['title'])}"))
    set_video_buttons_state()
    threading.Thread(target=video_reader, args=(session_id, process, width, height), daemon=True).start()


def finish_video_playback(session_id):
    global video_process, video_playing, video_paused

    if session_id != video_session_id:
        return

    video_process = None
    video_playing = False
    video_paused = False
    if not video_frame_seen:
        show_video_placeholder(
            t(
                "Video found, but playback could not start. Use Open Video.",
                "تم العثور على فيديو، لكن تعذر تشغيله. استخدم فتح الفيديو.",
            )
        )
        set_video_status(t("Playback failed for this online video.", "فشل تشغيل هذا الفيديو من الإنترنت."))
    else:
        set_video_status(t("Video ended. Use Restart to replay.", "انتهى الفيديو. استخدم إعادة التشغيل للمشاهدة مرة أخرى."))

    set_video_buttons_state()


def play_current_video():
    global video_paused

    if current_video is None:
        return

    if video_playing and video_paused:
        video_paused = False
        set_video_status(t(f"Playing: {video_title(current_video['title'])}", f"تشغيل: {video_title(current_video['title'])}"))
        set_video_buttons_state()
        return

    start_video_playback(current_video)


def pause_current_video():
    global video_paused

    if not video_playing:
        return

    video_paused = True
    set_video_status(t("Paused.", "متوقف مؤقتًا."))
    set_video_buttons_state()


def restart_current_video():
    if current_video is not None:
        start_video_playback(current_video)


def open_current_video():
    if current_video is None:
        return

    webbrowser.open(current_video.get("page_url") or current_video["url"])


def resize_video_display(value=None):
    sync_video_panel_size()


def finish_video_load(request_id, video_result):
    global current_video

    if request_id != video_request_id:
        return

    if video_result is None:
        current_video = None
        show_video_placeholder(
            t(
                "No matching online video found. Check your internet connection and try again.",
                "لم يتم العثور على فيديو مطابق من الإنترنت. تحقق من الاتصال ثم حاول مرة أخرى.",
            )
        )
        set_video_status("")
        set_video_buttons_state()
        return

    video = video_result["video"]
    current_video = video
    source = video_result.get("source", "Online match") if lang == "en" else video_result.get("source_ar", "نتيجة من الإنترنت")
    reason = video_result.get("reason", "")
    reason_text = f" ({reason})" if reason else ""
    set_video_status(
        t(
            f"{source}: {video_title(video['title'])}{reason_text}",
            f"{source}: {video_title(video['title'])}{reason_text}",
        )
    )
    set_video_buttons_state()
    start_video_playback(video)


def load_video(search_groups, ship_type, speed_knots, estimate, recommendation):
    global video_request_id, current_video

    video_request_id += 1
    request_id = video_request_id
    current_video = None
    stop_video_playback(clear_frame=False)
    show_video_placeholder(
        t(
            f"Searching online for a {estimate['blades']}-blade propeller video matching {estimate_spec_text(estimate)}...",
            f"جاري البحث عبر الإنترنت عن فيديو رفاس بعدد {estimate['blades']} شفرات مطابق للمواصفات...",
        )
    )
    set_video_status(t("Searching Wikimedia Commons videos with Llama assistance...", "جاري البحث في فيديوهات ويكيميديا كومنز بمساعدة Llama..."))
    set_video_buttons_state()

    def publish_status(en_message, ar_message):
        try:
            root.after(0, lambda: set_video_status(t(en_message, ar_message)))
        except tk.TclError:
            pass

    def worker():
        video_result = fetch_commons_search_video(
            search_groups,
            ship_type,
            speed_knots,
            estimate,
            recommendation,
            publish_status,
        )
        try:
            root.after(0, lambda: finish_video_load(request_id, video_result))
        except tk.TclError:
            pass

    threading.Thread(target=worker, daemon=True).start()


def clear_outputs():
    global video_request_id, current_video

    video_request_id += 1
    current_video = None
    stop_video_playback(clear_frame=False)
    result_label.config(text="", style="Result.TLabel")
    recommendation_label.config(text="")
    show_video_placeholder(t("Video preview", "معاينة الفيديو"))
    set_video_status("")
    set_video_buttons_state()


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
    search_groups = build_video_queries(ship_type, speed_knots, estimate, recommendation)
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
    load_video(search_groups, ship_type, speed_knots, estimate, recommendation)


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
        video_size_label,
        video_size_value,
        video_status_label,
    ):
        label.config(anchor=sticky_side, justify=justify)

    result_label.grid_configure(sticky="ew")
    recommendation_label.grid_configure(sticky="ew")
    entry_power.config(justify="right" if is_arabic else "left")
    entry_speed.config(justify="right" if is_arabic else "left")
    video_label.config(font=app_font(10), justify="center")


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
    video_frame.config(text=t("Video", "الفيديو"))
    video_size_label.config(text=t("Video width", "عرض الفيديو"))
    video_play_button.config(text=t("Play", "تشغيل"))
    video_pause_button.config(text=t("Pause", "إيقاف مؤقت"))
    video_restart_button.config(text=t("Restart", "إعادة تشغيل"))
    video_open_button.config(text=t("Open Video", "فتح الفيديو"))
    video_size_reset_button.config(text=t("Reset size", "إعادة الحجم"))
    lang_combo.config(font=app_font(10))
    ship_combo.config(values=ship_display_values(), font=app_font(10))
    set_ship_combo(selected_ship_type)
    set_video_buttons_state()

    if not result_label.cget("text") and not recommendation_label.cget("text"):
        show_video_placeholder(t("Video preview", "معاينة الفيديو"))


def update_scroll_region(event=None):
    scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))


def resize_scroll_frame(event):
    requested_width = main_frame.winfo_reqwidth()
    scroll_canvas.itemconfigure(main_window, width=max(event.width, requested_width))

    if "result_label" in globals():
        wraplength = max(260, event.width - 96)
        result_label.config(wraplength=wraplength)
        recommendation_label.config(wraplength=wraplength)
        if "video_status_label" in globals():
            video_status_label.config(wraplength=wraplength)


def on_mousewheel(event):
    if event.num == 4:
        scroll_canvas.yview_scroll(-1, "units")
    elif event.num == 5:
        scroll_canvas.yview_scroll(1, "units")
    else:
        scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


def reset_video_size():
    video_width_var.set(DEFAULT_VIDEO_WIDTH)
    resize_video_display()


def close_app():
    stop_video_playback(clear_frame=False)
    root.destroy()


root = tk.Tk()
root.geometry("640x780")
root.minsize(420, 480)
root.configure(bg="#edf2f7")
root.rowconfigure(0, weight=1)
root.columnconfigure(0, weight=1)

LATIN_FONT = choose_font(["Segoe UI", "Noto Sans", "DejaVu Sans", "Arial"])
ARABIC_FONT = choose_font(["Noto Sans Arabic", "Amiri", "DejaVu Sans", "Arial"])
video_width_var = tk.DoubleVar(value=DEFAULT_VIDEO_WIDTH)

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

video_frame = ttk.LabelFrame(main_frame, padding=16, style="Panel.TLabelframe")
video_frame.grid(row=4, column=0, sticky="ew")

video_button_frame = ttk.Frame(video_frame, style="Panel.TFrame")
video_button_frame.pack(fill="x", pady=(0, 10))
for column in range(4):
    video_button_frame.columnconfigure(column, weight=1)

video_play_button = ttk.Button(
    video_button_frame,
    command=play_current_video,
    style="Accent.TButton",
)
video_play_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

video_pause_button = ttk.Button(
    video_button_frame,
    command=pause_current_video,
    style="Plain.TButton",
)
video_pause_button.grid(row=0, column=1, sticky="ew", padx=(0, 6))

video_restart_button = ttk.Button(
    video_button_frame,
    command=restart_current_video,
    style="Plain.TButton",
)
video_restart_button.grid(row=0, column=2, sticky="ew", padx=(0, 6))

video_open_button = ttk.Button(
    video_button_frame,
    command=open_current_video,
    style="Plain.TButton",
)
video_open_button.grid(row=0, column=3, sticky="ew")

video_size_frame = ttk.Frame(video_frame, style="Panel.TFrame")
video_size_frame.pack(fill="x", pady=(0, 10))
video_size_frame.columnconfigure(1, weight=1)

video_size_label = ttk.Label(video_size_frame, style="Field.TLabel")
video_size_label.grid(row=0, column=0, sticky="w", padx=(0, 10))

video_width_slider = ttk.Scale(
    video_size_frame,
    from_=MIN_VIDEO_WIDTH,
    to=MAX_VIDEO_WIDTH,
    variable=video_width_var,
    command=resize_video_display,
)
video_width_slider.grid(row=0, column=1, sticky="ew", padx=(0, 10))

video_size_value = ttk.Label(video_size_frame, width=14, style="Field.TLabel")
video_size_value.grid(row=0, column=2, sticky="e", padx=(0, 10))

video_size_reset_button = ttk.Button(
    video_size_frame,
    command=reset_video_size,
    style="Plain.TButton",
)
video_size_reset_button.grid(row=0, column=3, sticky="e")

video_status_label = ttk.Label(video_frame, text="", wraplength=540, style="Field.TLabel")
video_status_label.pack(fill="x", pady=(0, 10))

initial_video_width, initial_video_height = current_video_size()
video_panel = tk.Frame(
    video_frame,
    width=initial_video_width,
    height=initial_video_height,
    bg="#f8fafc",
    highlightbackground="#d0d7de",
    highlightthickness=1,
)
video_panel.pack(anchor="center")
video_panel.pack_propagate(False)

video_label = tk.Label(
    video_panel,
    bg="#f8fafc",
    fg="#6b7280",
    font=app_font(10),
    justify="center",
    wraplength=400,
)
video_label.pack(fill="both", expand=True)

refresh_language()
root.protocol("WM_DELETE_WINDOW", close_app)
root.mainloop()
