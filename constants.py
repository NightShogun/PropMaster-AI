"""Shared configuration for the marine propeller advisor."""

import os


DEFAULT_VIDEO_WIDTH = 600
DEFAULT_VIDEO_HEIGHT = 338
VIDEO_ASPECT_RATIO = DEFAULT_VIDEO_HEIGHT / DEFAULT_VIDEO_WIDTH
MIN_VIDEO_WIDTH = 360
MAX_VIDEO_WIDTH = 760
VIDEO_FPS = 24

ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"
ARCHIVE_METADATA_URL = "https://archive.org/metadata"
ARCHIVE_VIDEO_SEARCH_LIMIT = 12
ARCHIVE_ITEMS_PER_QUERY = 4
ARCHIVE_VIDEO_EXTENSIONS = (".mp4", ".m4v", ".webm", ".ogv", ".mov")
ARCHIVE_VIDEO_FORMAT_TERMS = ("mpeg4", "h.264", "webm", "matroska", "ogv", "quicktime", "movie")

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

APP_BG = "#eef4f8"
SURFACE = "#ffffff"
SURFACE_SOFT = "#f8fafc"
VIDEO_SURFACE = "#0f172a"
BORDER = "#d8e0e8"
TEXT = "#111827"
MUTED_TEXT = "#5f6b7a"
ACCENT = "#0f766e"
ACCENT_DARK = "#115e59"
SECONDARY = "#2563eb"
SECONDARY_DARK = "#1d4ed8"
DANGER = "#b42318"

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
