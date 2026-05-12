"""Internet Archive and local Llama video discovery for propeller examples."""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from constants import (
    ARCHIVE_ITEMS_PER_QUERY,
    ARCHIVE_METADATA_URL,
    ARCHIVE_SEARCH_URL,
    ARCHIVE_VIDEO_EXTENSIONS,
    ARCHIVE_VIDEO_FORMAT_TERMS,
    ARCHIVE_VIDEO_SEARCH_LIMIT,
    BLADE_WORDS,
    HTTP_HEADERS,
    LLAMA_HOST,
    LLAMA_MODEL,
    LLAMA_TIMEOUT_SECONDS,
    LLAMA_VIDEO_SEARCH_ENABLED,
    MAX_BROAD_VIDEO_QUERY_ATTEMPTS,
    MAX_EXACT_VIDEO_QUERY_ATTEMPTS,
    MAX_LLAMA_VIDEO_QUERY_ATTEMPTS,
    MAX_VIDEO_CANDIDATES,
    SHIP_SEARCH_TERMS,
)
from i18n import translate


def estimate_spec_text(estimate):
    return (
        f"{estimate['blades']}-blade, "
        f"{estimate['diameter']:.2f} m diameter, "
        f"{estimate['pitch']:.2f} m pitch, "
        f"{estimate['rpm']:.0f} RPM"
    )


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


def video_title(title, language="en"):
    cleaned_title = title.replace("File:", "", 1).replace("_", " ").strip()
    return cleaned_title or translate(language, "Online propeller video", "فيديو رفاس من الإنترنت")


def archive_details_url(identifier):
    if not identifier:
        return ""

    return "https://archive.org/details/" + quote(identifier, safe="")


def archive_download_url(identifier, file_name):
    if not identifier or not file_name:
        return ""

    return f"https://archive.org/download/{quote(identifier, safe='')}/{quote(file_name, safe='/')}"


def number_tokens(value):
    return {
        f"{value:.2f}",
        f"{value:.1f}",
        str(round(value)),
    }


def score_video_candidate(candidate, estimate, recommendation):
    searchable_fields = [
        candidate.get("title", ""),
        candidate.get("description", ""),
        candidate.get("subject", ""),
        candidate.get("identifier", ""),
        candidate.get("file_name", ""),
        candidate.get("query", ""),
        candidate.get("url", ""),
    ]
    raw_text = " ".join(str(field) for field in searchable_fields).lower()
    searchable_text = normalized_search_text(raw_text)
    score = 0
    blade_count = estimate["blades"]

    if has_blade_match(searchable_text, blade_count):
        score += 120

    if "propeller" in searchable_text or "screw" in searchable_text:
        score += 50

    if "ship" in searchable_text or "marine" in searchable_text or "vessel" in searchable_text:
        score += 35

    if "boat" in searchable_text or "drydock" in searchable_text or "underwater" in searchable_text:
        score += 18

    if "rudder" in searchable_text or "propulsion" in searchable_text or "cavitation" in searchable_text:
        score += 15

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

    if "toy" in searchable_text or "lego" in searchable_text or "game" in searchable_text:
        score -= 45

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
You generate public video archive search queries for marine propeller videos.
Use the estimated propeller specs, but avoid impossible over-specific queries.
Do not return URLs. Do not invent file names.
Return only JSON with this shape:
{{"queries":["query 1","query 2","query 3"]}}

Propeller context:
{propeller_context(ship_type, speed_knots, estimate, recommendation)}

Rules:
- Prefer marine, ship, vessel, boat, screw propeller, drydock, underwater, cavitation, nozzle, controllable pitch terms when relevant.
- Write queries that can find Internet Archive video records.
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

    return dedupe(query for query in (sanitize_search_query(item) for item in queries) if query)[
        :MAX_LLAMA_VIDEO_QUERY_ATTEMPTS
    ]


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


def clean_metadata_text(value):
    if isinstance(value, (list, tuple, set)):
        return " ".join(clean_metadata_text(item) for item in value)

    if isinstance(value, dict):
        return " ".join(clean_metadata_text(item) for item in value.values())

    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(text.split())


def archive_file_is_video(file_info):
    name = file_info.get("name", "").lower()
    file_format = file_info.get("format", "").lower()
    return name.endswith(ARCHIVE_VIDEO_EXTENSIONS) or any(term in file_format for term in ARCHIVE_VIDEO_FORMAT_TERMS)


def archive_file_mime(file_name):
    name = file_name.lower()
    if name.endswith((".mp4", ".m4v", ".mov")):
        return "video/mp4"
    if name.endswith(".webm"):
        return "video/webm"
    if name.endswith(".ogv"):
        return "video/ogg"

    return "video/*"


def archive_video_file_score(file_info):
    name = file_info.get("name", "").lower()
    file_format = file_info.get("format", "").lower()
    score = 0

    if name.endswith((".mp4", ".m4v")):
        score += 50
    elif name.endswith(".webm"):
        score += 42
    elif name.endswith(".ogv"):
        score += 30
    elif name.endswith(".mov"):
        score += 20

    if "h.264" in file_format or "mpeg4" in file_format:
        score += 20
    if "derivative" in file_info.get("source", "").lower():
        score += 6

    try:
        size = int(file_info.get("size", 0))
    except (TypeError, ValueError):
        size = 0
    if size > 2_000_000:
        score += 8

    return score


def best_archive_video_file(files):
    video_files = [file_info for file_info in files if archive_file_is_video(file_info)]
    if not video_files:
        return None

    video_files.sort(key=archive_video_file_score, reverse=True)
    return video_files[0]


def fetch_archive_metadata(identifier):
    if not identifier:
        return None

    try:
        request = Request(f"{ARCHIVE_METADATA_URL}/{quote(identifier, safe='')}", headers=HTTP_HEADERS)
        with urlopen(request, timeout=8) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None


def search_archive_items(query):
    clean_query = sanitize_search_query(query)
    if not clean_query:
        return []

    params = urlencode(
        [
            ("q", f"({clean_query}) AND mediatype:(movies)"),
            ("fl[]", "identifier"),
            ("fl[]", "title"),
            ("fl[]", "description"),
            ("fl[]", "subject"),
            ("rows", str(ARCHIVE_VIDEO_SEARCH_LIMIT)),
            ("page", "1"),
            ("output", "json"),
        ]
    )

    try:
        request = Request(f"{ARCHIVE_SEARCH_URL}?{params}", headers=HTTP_HEADERS)
        with urlopen(request, timeout=8) as response:
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return []

    return data.get("response", {}).get("docs", [])


def search_archive_videos(query, estimate, recommendation, require_blade_match):
    candidates = []
    blade_count = estimate["blades"]

    for item in search_archive_items(query)[:ARCHIVE_ITEMS_PER_QUERY]:
        identifier = item.get("identifier", "")
        metadata = fetch_archive_metadata(identifier)
        if not metadata:
            continue

        file_info = best_archive_video_file(metadata.get("files", []))
        if not file_info:
            continue

        item_metadata = metadata.get("metadata", {})
        title = clean_metadata_text(item_metadata.get("title") or item.get("title") or identifier)
        description = clean_metadata_text(item_metadata.get("description") or item.get("description", ""))
        subject = clean_metadata_text(item_metadata.get("subject") or item.get("subject", ""))
        file_name = file_info.get("name", "")
        video_url = archive_download_url(identifier, file_name)
        if not video_url:
            continue

        candidate = {
            "title": title,
            "description": description,
            "subject": subject,
            "identifier": identifier,
            "file_name": file_name,
            "url": video_url,
            "page_url": archive_details_url(identifier),
            "mime": archive_file_mime(file_name),
            "query": query,
            "provider": "Internet Archive",
        }
        if require_blade_match and not has_blade_match(
            f"{candidate['title']} {candidate['description']} {candidate['subject']} {candidate['file_name']}",
            blade_count,
        ):
            continue

        candidate["score"] = score_video_candidate(candidate, estimate, recommendation)
        candidates.append(candidate)

    return sort_video_candidates(candidates)


def fetch_archive_search_video(search_groups, ship_type, speed_knots, estimate, recommendation, status_callback=None):
    candidates = []
    exact_queries = list(search_groups["exact"])[:MAX_EXACT_VIDEO_QUERY_ATTEMPTS]
    broad_queries = list(search_groups["broad"])[:MAX_BROAD_VIDEO_QUERY_ATTEMPTS]

    if status_callback:
        status_callback("Searching exact public video archive matches...", "جاري البحث عن فيديوهات أرشيفية مطابقة بدقة...")

    for query in exact_queries:
        candidates = merge_video_candidates(
            candidates,
            search_archive_videos(query, estimate, recommendation, True),
        )

    if status_callback:
        status_callback("Asking local Llama for extra search queries...", "جاري طلب عبارات بحث إضافية من Llama المحلي...")

    llama_queries = build_llama_video_queries(ship_type, speed_knots, estimate, recommendation)
    if status_callback:
        if llama_queries:
            status_callback("Searching Llama-generated online video queries...", "جاري البحث بعبارات Llama عبر الإنترنت...")
        else:
            status_callback(
                "Llama unavailable; continuing with algorithmic search.",
                "Llama غير متاح؛ جار المتابعة بالبحث الخوارزمي.",
            )

    for query in llama_queries:
        candidates = merge_video_candidates(
            candidates,
            search_archive_videos(query, estimate, recommendation, False),
        )

    if status_callback:
        status_callback("Searching broader public video archive candidates...", "جاري البحث عن فيديوهات أرشيفية أوسع صلة...")

    for query in broad_queries:
        candidates = merge_video_candidates(
            candidates,
            search_archive_videos(query, estimate, recommendation, False),
        )

    if not candidates:
        return None

    if status_callback:
        status_callback("Asking local Llama to rank video candidates...", "جاري طلب ترتيب نتائج الفيديو من Llama المحلي...")

    llama_choice = choose_video_with_llama(candidates, ship_type, speed_knots, estimate, recommendation)
    if llama_choice is not None:
        return {
            "video": llama_choice["candidate"],
            "source": "Llama-ranked archive video",
            "source_ar": "فيديو أرشيفي رتبه Llama",
            "reason": llama_choice["reason"],
        }

    return {
        "video": candidates[0],
        "source": "Algorithm-ranked archive video",
        "source_ar": "فيديو أرشيفي رتبته الخوارزمية",
        "reason": "",
    }
