import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import ttk

from constants import (
    ACCENT,
    ACCENT_DARK,
    APP_BG,
    BLADE_WORDS,
    BORDER,
    DANGER,
    DEFAULT_VIDEO_WIDTH,
    HTTP_HEADERS,
    MAX_VIDEO_WIDTH,
    MIN_VIDEO_WIDTH,
    MUTED_TEXT,
    SECONDARY,
    SECONDARY_DARK,
    SHIP_RPM_FACTORS,
    SHIP_SEARCH_TERMS,
    SHIP_TYPES,
    SURFACE,
    SURFACE_SOFT,
    TEXT,
    VIDEO_ASPECT_RATIO,
    VIDEO_FPS,
    VIDEO_SURFACE,
)
from i18n import translate
from propeller import estimate_propeller, recommend_propeller, slip_note
from ships import ship_display_name, ship_display_values, ship_type_from_display
from video_search import estimate_spec_text, fetch_archive_search_video, video_title

try:
    from PIL import Image, ImageTk
except ImportError as exc:
    raise SystemExit("Install Pillow first: pip install pillow") from exc


LOCAL_PHOTO_DIR = Path(__file__).resolve().parent / "photos"
LOCAL_VIDEO_DIR = Path(__file__).resolve().parent / "videos"
LOCAL_PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".gif")
LOCAL_VIDEO_EXTENSIONS = (".mp4", ".m4v", ".webm", ".ogv", ".mov")
MEDIA_MODES = ("videos", "photos")
RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS


def normalized_media_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def compact_media_text(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def blade_match_score(text, compact_text, blade_count):
    word = BLADE_WORDS.get(blade_count, str(blade_count))
    number = str(blade_count)
    spaced_phrases = (
        f"{number} blade",
        f"{number} blades",
        f"{number} bladed",
        f"{word} blade",
        f"{word} blades",
        f"{word} bladed",
    )
    compact_phrases = (
        f"{number}blade",
        f"{number}blades",
        f"{number}bladed",
        f"{word}blade",
        f"{word}blades",
        f"{word}bladed",
    )

    if any(phrase in text for phrase in spaced_phrases) or any(phrase in compact_text for phrase in compact_phrases):
        return 100

    return 0


def ship_match_score(text, compact_text, ship_type):
    aliases = {
        ship_type,
        SHIP_SEARCH_TERMS.get(ship_type, ""),
        ship_display_name(ship_type, "en"),
    }
    score = 0

    for alias in aliases:
        normalized_alias = normalized_media_text(alias)
        compact_alias = compact_media_text(alias)
        if normalized_alias and normalized_alias in text:
            score += 60
        elif compact_alias and compact_alias in compact_text:
            score += 60

    return score


def recommendation_match_score(text, compact_text, recommendation):
    score = 0
    terms = (
        recommendation.get("kind", ""),
        recommendation.get("query", ""),
        recommendation.get("en", ""),
    )

    for term in terms:
        normalized_term = normalized_media_text(term)
        compact_term = compact_media_text(term)
        if normalized_term and normalized_term in text:
            score += 30
        elif compact_term and compact_term in compact_text:
            score += 30

    return score


def score_local_media_file(path, root_dir, ship_type, estimate, recommendation):
    relative_path = path.relative_to(root_dir).as_posix()
    text = normalized_media_text(relative_path)
    compact_text = compact_media_text(relative_path)
    score = 0

    score += blade_match_score(text, compact_text, estimate["blades"])
    score += ship_match_score(text, compact_text, ship_type)
    score += recommendation_match_score(text, compact_text, recommendation)

    if "propeller" in text:
        score += 25

    return score


def find_relevant_local_media(root_dir, extensions, ship_type, estimate, recommendation):
    if not root_dir.exists():
        return None, 0

    candidates = sorted(path for path in root_dir.rglob("*") if path.is_file() and path.suffix.lower() in extensions)
    if not candidates:
        return None, 0

    scored_candidates = [
        (score_local_media_file(path, root_dir, ship_type, estimate, recommendation), path) for path in candidates
    ]
    best_score, best_path = max(scored_candidates, key=lambda item: item[0])
    if best_score <= 0:
        return candidates[0], 0

    return best_path, best_score


class MarinePropellerAdvisorApp:
    """Tkinter shell for the propeller advisor workflow."""

    def __init__(self):
        self.lang = "en"
        self.video_request_id = 0
        self.video_session_id = 0
        self.current_video = None
        self.video_process = None
        self.video_playing = False
        self.video_paused = False
        self.video_frame_seen = False
        self.last_media_context = None

        self.root = tk.Tk()
        self.root.geometry("1120x760")
        self.root.minsize(920, 620)
        self.root.configure(bg=APP_BG)
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.latin_font = self.choose_font(["Segoe UI", "Noto Sans", "DejaVu Sans", "Arial"])
        self.arabic_font = self.choose_font(["Noto Sans Arabic", "Amiri", "DejaVu Sans", "Arial"])
        self.video_width_var = tk.DoubleVar(self.root, value=DEFAULT_VIDEO_WIDTH)
        self.media_mode_var = tk.StringVar(self.root, value=MEDIA_MODES[0])

        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.configure_styles()
        self.build_layout()
        self.refresh_language()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)

    def tr(self, en_text, ar_text):
        return translate(self.lang, en_text, ar_text)

    def choose_font(self, preferred_names, fallback="TkDefaultFont"):
        available_fonts = {font.lower(): font for font in tkfont.families(self.root)}
        for name in preferred_names:
            if name.lower() in available_fonts:
                return available_fonts[name.lower()]

        return fallback

    def app_font(self, size, weight=None):
        family = self.arabic_font if self.lang == "ar" else self.latin_font
        return (family, size, weight) if weight else (family, size)

    def get_selected_ship_type(self):
        return ship_type_from_display(self.ship_combo.get())

    def set_ship_combo(self, ship_type):
        self.ship_combo.set(ship_display_name(ship_type, self.lang))

    def metric_line(self, en_label, ar_label, value):
        return f"{self.tr(en_label, ar_label)}: {value}"

    def media_display_name(self, media_mode):
        if media_mode == "photos":
            return self.tr("Photos", "صور")

        return self.tr("Videos", "فيديوهات")

    def media_display_values(self):
        return [self.media_display_name(media_mode) for media_mode in MEDIA_MODES]

    def get_selected_media_mode(self):
        selected_value = self.media_combo.get().strip() if hasattr(self, "media_combo") else ""
        normalized_value = selected_value.lower()

        for media_mode in MEDIA_MODES:
            if normalized_value == media_mode or selected_value == self.media_display_name(media_mode):
                return media_mode

        return self.media_mode_var.get() if self.media_mode_var.get() in MEDIA_MODES else MEDIA_MODES[0]

    def set_media_combo(self, media_mode):
        if media_mode not in MEDIA_MODES:
            media_mode = MEDIA_MODES[0]

        self.media_mode_var.set(media_mode)
        self.media_combo.set(self.media_display_name(media_mode))

    def read_positive_number(self, entry, en_name, ar_name):
        raw_value = entry.get().strip()

        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ValueError(self.tr(f"{en_name} must be a number.", f"{ar_name} يجب أن يكون رقمًا.")) from exc

        if value <= 0:
            raise ValueError(
                self.tr(
                    f"{en_name} must be greater than zero.",
                    f"{ar_name} يجب أن يكون أكبر من صفر.",
                )
            )

        return value

    def current_video_size(self):
        width = round(float(self.video_width_var.get()))
        height = max(150, round(width * VIDEO_ASPECT_RATIO))
        return width, height

    def sync_video_panel_size(self):
        if not hasattr(self, "video_panel"):
            return

        width, height = self.current_video_size()
        self.video_panel.config(width=width, height=height)
        self.video_label.config(wraplength=max(160, width - 36))
        self.video_size_value.config(text=f"{width} x {height} px")

    def set_video_status(self, message):
        if hasattr(self, "video_status_label"):
            self.video_status_label.config(text=message)

    def set_video_buttons_state(self):
        if not hasattr(self, "video_play_button"):
            return

        has_media = self.current_video is not None
        has_video = has_media and self.current_video.get("type", "video") == "video"
        self.video_play_button.config(
            state=tk.NORMAL if has_video and (not self.video_playing or self.video_paused) else tk.DISABLED
        )
        self.video_pause_button.config(
            state=tk.NORMAL if has_video and self.video_playing and not self.video_paused else tk.DISABLED
        )
        self.video_restart_button.config(state=tk.NORMAL if has_video else tk.DISABLED)
        self.video_open_button.config(state=tk.NORMAL if has_media else tk.DISABLED)

    def show_video_placeholder(self, message):
        self.sync_video_panel_size()
        self.video_label.config(image="", text=message)
        self.video_label.image = None

    def display_video_frame(self, session_id, frame):
        if session_id != self.video_session_id:
            return

        self.video_frame_seen = True
        photo = ImageTk.PhotoImage(frame)
        self.video_label.config(image=photo, text="")
        self.video_label.image = photo

    def display_photo_file(self, photo_path):
        self.stop_video_playback(clear_frame=False)
        self.sync_video_panel_size()
        width, height = self.current_video_size()

        try:
            with Image.open(photo_path) as image:
                display_image = image.convert("RGB")
        except OSError as exc:
            self.show_video_placeholder(self.tr("Could not load this photo.", "تعذر تحميل هذه الصورة."))
            self.set_video_status(str(exc))
            self.set_video_buttons_state()
            return

        display_image.thumbnail((width, height), RESAMPLE)
        canvas = Image.new("RGB", (width, height), VIDEO_SURFACE)
        x = (width - display_image.width) // 2
        y = (height - display_image.height) // 2
        canvas.paste(display_image, (x, y))

        photo = ImageTk.PhotoImage(canvas)
        self.video_label.config(image=photo, text="")
        self.video_label.image = photo
        self.set_video_buttons_state()

    @staticmethod
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

    @staticmethod
    def build_ffmpeg_command(video_url, width, height):
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            return None

        video_source = str(video_url)
        is_remote_source = "://" in video_source
        scale_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x0f172a,"
            f"fps={VIDEO_FPS}"
        )

        command = [
            ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
        ]

        if is_remote_source:
            command.extend(
                [
                    "-user_agent",
                    HTTP_HEADERS["User-Agent"],
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_delay_max",
                    "4",
                ]
            )

        command.extend(
            [
                "-i",
                video_source,
                "-an",
                "-vf",
                scale_filter,
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
        )
        return command

    def video_reader(self, session_id, process, width, height):
        frame_size = width * height * 3
        frame_delay = 1 / VIDEO_FPS
        next_frame_time = time.monotonic()

        try:
            while session_id == self.video_session_id:
                while self.video_paused and session_id == self.video_session_id:
                    time.sleep(0.05)

                frame_data = self.read_video_frame(process.stdout, frame_size)
                if frame_data is None:
                    break

                frame = Image.frombytes("RGB", (width, height), frame_data)
                try:
                    self.root.after(0, lambda sid=session_id, image=frame: self.display_video_frame(sid, image))
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
                self.root.after(0, lambda sid=session_id: self.finish_video_playback(sid))
            except tk.TclError:
                pass

    @staticmethod
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

    def stop_video_playback(self, clear_frame=False):
        self.video_session_id += 1
        process = self.video_process
        self.video_process = None
        self.video_playing = False
        self.video_paused = False
        self.video_frame_seen = False
        self.terminate_video_process(process)

        if clear_frame and hasattr(self, "video_label"):
            self.show_video_placeholder(self.tr("Media preview", "معاينة الوسائط"))

        self.set_video_buttons_state()

    def start_video_playback(self, video):
        if video is None:
            return

        self.stop_video_playback(clear_frame=False)
        width, height = self.current_video_size()
        command = self.build_ffmpeg_command(video["url"], width, height)
        if command is None:
            self.show_video_placeholder(
                self.tr(
                    "Video found, but ffmpeg is required for in-app playback. Use Open Media.",
                    "تم العثور على فيديو، لكن ffmpeg مطلوب للتشغيل داخل التطبيق. استخدم فتح الوسائط.",
                )
            )
            self.set_video_status(
                self.tr("Playback unavailable: ffmpeg is not installed.", "التشغيل غير متاح: ffmpeg غير مثبت.")
            )
            self.set_video_buttons_state()
            return

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=width * height * 3,
            )
        except OSError as exc:
            self.show_video_placeholder(self.tr("Could not start video playback.", "تعذر بدء تشغيل الفيديو."))
            self.set_video_status(str(exc))
            self.set_video_buttons_state()
            return

        self.video_session_id += 1
        session_id = self.video_session_id
        self.video_process = process
        self.video_playing = True
        self.video_paused = False
        self.video_frame_seen = False
        self.show_video_placeholder(self.tr("Starting video...", "جاري بدء الفيديو..."))
        title = video_title(video["title"], self.lang)
        self.set_video_status(self.tr(f"Playing: {title}", f"تشغيل: {title}"))
        self.set_video_buttons_state()
        threading.Thread(target=self.video_reader, args=(session_id, process, width, height), daemon=True).start()

    def finish_video_playback(self, session_id):
        if session_id != self.video_session_id:
            return

        self.video_process = None
        self.video_playing = False
        self.video_paused = False
        if not self.video_frame_seen:
            self.show_video_placeholder(
                self.tr(
                    "Video found, but playback could not start. Use Open Media.",
                    "تم العثور على فيديو، لكن تعذر تشغيله. استخدم فتح الوسائط.",
                )
            )
            self.set_video_status(self.tr("Playback failed for this video.", "فشل تشغيل هذا الفيديو."))
        else:
            self.set_video_status(
                self.tr("Video ended. Use Restart to replay.", "انتهى الفيديو. استخدم إعادة التشغيل للمشاهدة مرة أخرى.")
            )

        self.set_video_buttons_state()

    def play_current_video(self):
        if self.current_video is None:
            return
        if self.current_video.get("type", "video") != "video":
            return

        if self.video_playing and self.video_paused:
            self.video_paused = False
            title = video_title(self.current_video["title"], self.lang)
            self.set_video_status(self.tr(f"Playing: {title}", f"تشغيل: {title}"))
            self.set_video_buttons_state()
            return

        self.start_video_playback(self.current_video)

    def pause_current_video(self):
        if not self.video_playing:
            return

        self.video_paused = True
        self.set_video_status(self.tr("Paused.", "متوقف مؤقتًا."))
        self.set_video_buttons_state()

    def restart_current_video(self):
        if self.current_video is not None and self.current_video.get("type", "video") == "video":
            self.start_video_playback(self.current_video)

    def open_current_video(self):
        if self.current_video is None:
            return

        webbrowser.open(self.current_video.get("page_url") or self.current_video["url"])

    def resize_video_display(self, value=None):
        self.sync_video_panel_size()
        if self.current_video is not None and self.current_video.get("type") == "photo":
            self.display_photo_file(Path(self.current_video["url"]))

    def finish_video_load(self, request_id, video_result):
        if request_id != self.video_request_id:
            return

        if video_result is None:
            self.current_video = None
            self.show_video_placeholder(
                self.tr(
                    "No matching online video found. Check your internet connection and try again.",
                    "لم يتم العثور على فيديو مطابق من الإنترنت. تحقق من الاتصال ثم حاول مرة أخرى.",
                )
            )
            self.set_video_status("")
            self.set_video_buttons_state()
            return

        video = video_result["video"]
        self.current_video = video
        source = (
            video_result.get("source", "Online match")
            if self.lang == "en"
            else video_result.get("source_ar", "نتيجة من الإنترنت")
        )
        reason = video_result.get("reason", "")
        reason_text = f" ({reason})" if reason else ""
        self.set_video_status(f"{source}: {video_title(video['title'], self.lang)}{reason_text}")
        self.set_video_buttons_state()
        self.start_video_playback(video)

    def load_video(self, search_groups, ship_type, speed_knots, estimate, recommendation):
        self.video_request_id += 1
        request_id = self.video_request_id
        self.current_video = None
        self.stop_video_playback(clear_frame=False)
        self.show_video_placeholder(
            self.tr(
                f"Searching online for a {estimate['blades']}-blade propeller video matching {estimate_spec_text(estimate)}...",
                f"جاري البحث عبر الإنترنت عن فيديو رفاس بعدد {estimate['blades']} شفرات مطابق للمواصفات...",
            )
        )
        self.set_video_status(
            self.tr(
                "Searching public archive videos with Llama assistance...",
                "جاري البحث في فيديوهات الأرشيف العام بمساعدة Llama...",
            )
        )
        self.set_video_buttons_state()

        def publish_status(en_message, ar_message):
            try:
                self.root.after(0, lambda: self.set_video_status(self.tr(en_message, ar_message)))
            except tk.TclError:
                pass

        def worker():
            video_result = fetch_archive_search_video(
                search_groups,
                ship_type,
                speed_knots,
                estimate,
                recommendation,
                publish_status,
            )
            try:
                self.root.after(0, lambda: self.finish_video_load(request_id, video_result))
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def clear_outputs(self):
        self.video_request_id += 1
        self.current_video = None
        self.last_media_context = None
        self.stop_video_playback(clear_frame=False)
        self.result_label.config(text="", style="Result.TLabel")
        self.recommendation_label.config(text="")
        self.show_video_placeholder(self.tr("Media preview", "معاينة الوسائط"))
        self.set_video_status("")
        self.set_video_buttons_state()

    def reset(self):
        self.entry_power.delete(0, tk.END)
        self.entry_speed.delete(0, tk.END)
        self.set_ship_combo(SHIP_TYPES[0])
        self.clear_outputs()

    def show_selected_local_media(self):
        if self.last_media_context is None:
            return

        self.video_request_id += 1
        media_mode = self.get_selected_media_mode()
        ship_type = self.last_media_context["ship_type"]
        estimate = self.last_media_context["estimate"]
        recommendation = self.last_media_context["recommendation"]

        if media_mode == "photos":
            photo_path, score = find_relevant_local_media(
                LOCAL_PHOTO_DIR,
                LOCAL_PHOTO_EXTENSIONS,
                ship_type,
                estimate,
                recommendation,
            )
            if photo_path is None:
                self.current_video = None
                self.stop_video_playback(clear_frame=False)
                self.show_video_placeholder(
                    self.tr(
                        "No photo file found in the photos folder.",
                        "لم يتم العثور على ملف صورة في مجلد photos.",
                    )
                )
                self.set_video_status("")
                self.set_video_buttons_state()
                return

            self.current_video = {
                "type": "photo",
                "title": photo_path.name,
                "url": str(photo_path),
                "page_url": photo_path.as_uri(),
            }
            status = "Matched local photo" if score > 0 else "Showing local photo"
            status_ar = "صورة محلية مطابقة" if score > 0 else "عرض صورة محلية"
            self.display_photo_file(photo_path)
            self.set_video_status(self.tr(f"{status}: {photo_path.name}", f"{status_ar}: {photo_path.name}"))
            self.set_video_buttons_state()
            return

        video_path, score = find_relevant_local_media(
            LOCAL_VIDEO_DIR,
            LOCAL_VIDEO_EXTENSIONS,
            ship_type,
            estimate,
            recommendation,
        )
        if video_path is None:
            self.current_video = None
            self.stop_video_playback(clear_frame=False)
            self.show_video_placeholder(
                self.tr(
                    "No video file found in the videos folder.",
                    "لم يتم العثور على ملف فيديو في مجلد videos.",
                )
            )
            self.set_video_status("")
            self.set_video_buttons_state()
            return

        self.current_video = {
            "type": "video",
            "title": video_path.name,
            "url": str(video_path),
            "page_url": video_path.as_uri(),
        }
        status = "Matched local video" if score > 0 else "Showing local video"
        status_ar = "فيديو محلي مطابق" if score > 0 else "عرض فيديو محلي"
        self.set_video_status(self.tr(f"{status}: {video_path.name}", f"{status_ar}: {video_path.name}"))
        self.set_video_buttons_state()
        self.start_video_playback(self.current_video)

    def calculate(self):
        try:
            power = self.read_positive_number(self.entry_power, "Power", "القدرة")
            speed_knots = self.read_positive_number(self.entry_speed, "Speed", "السرعة")
            ship_type = self.get_selected_ship_type()

            if ship_type not in SHIP_RPM_FACTORS:
                raise ValueError(self.tr("Choose a valid ship type.", "اختر نوع سفينة صحيحًا."))

            estimate = estimate_propeller(power, speed_knots, ship_type, self.lang)
        except ValueError as exc:
            self.result_label.config(text=str(exc), style="Error.TLabel")
            self.recommendation_label.config(text="")
            return

        recommendation = recommend_propeller(ship_type, speed_knots, estimate["slip"])
        efficiency_percent = estimate["efficiency"] * 100
        note = slip_note(estimate["slip"], self.lang)

        lines = [
            self.metric_line("Ship", "السفينة", ship_display_name(ship_type, self.lang)),
            self.metric_line("RPM", "عدد الدورات في الدقيقة", f"{estimate['rpm']:.2f}"),
            self.metric_line("Blades", "الشفرات", f"{estimate['blades']}"),
            self.metric_line("Diameter", "القطر", f"{estimate['diameter']:.2f} {self.tr('m', 'متر')}"),
            self.metric_line("Pitch", "الميل", f"{estimate['pitch']:.2f} {self.tr('m', 'متر')}"),
            self.metric_line("Slip", "الانزلاق", f"{estimate['slip']:.3f}"),
            self.metric_line("Efficiency", "الكفاءة", f"{efficiency_percent:.2f}%"),
        ]

        if note:
            lines.extend(("", note))

        self.result_label.config(text="\n".join(lines), style="Result.TLabel")
        recommendation_lines = [
            self.tr("Recommendation", "التوصية"),
            recommendation["en"] if self.lang == "en" else recommendation["ar"],
        ]
        self.recommendation_label.config(text="\n".join(recommendation_lines))
        self.last_media_context = {
            "ship_type": ship_type,
            "estimate": estimate,
            "recommendation": recommendation,
        }
        self.show_selected_local_media()

    def change_media_mode(self, event=None):
        self.media_mode_var.set(self.get_selected_media_mode())
        self.show_selected_local_media()

    def change_lang(self, event=None):
        self.lang = self.lang_combo.get()
        self.refresh_language()

    def configure_styles(self):
        self.style.configure("App.TFrame", background=APP_BG)
        self.style.configure("Header.TFrame", background=APP_BG)
        self.style.configure("Panel.TLabelframe", background=SURFACE, bordercolor=BORDER, relief="solid")
        self.style.configure(
            "Panel.TLabelframe.Label",
            background=APP_BG,
            foreground=TEXT,
            font=self.app_font(11, "bold"),
        )
        self.style.configure("Panel.TFrame", background=SURFACE)
        self.style.configure("Title.TLabel", background=APP_BG, foreground=TEXT, font=self.app_font(24, "bold"))
        self.style.configure("HeaderField.TLabel", background=APP_BG, foreground=MUTED_TEXT, font=self.app_font(10))
        self.style.configure("Field.TLabel", background=SURFACE, foreground=MUTED_TEXT, font=self.app_font(10))
        self.style.configure(
            "TEntry",
            bordercolor=BORDER,
            fieldbackground=SURFACE_SOFT,
            foreground=TEXT,
            lightcolor=BORDER,
            padding=7,
        )
        self.style.configure(
            "TCombobox",
            arrowsize=14,
            bordercolor=BORDER,
            fieldbackground=SURFACE_SOFT,
            foreground=TEXT,
            lightcolor=BORDER,
            padding=7,
        )
        self.style.configure(
            "Result.TLabel",
            background=SURFACE,
            foreground=TEXT,
            font=self.app_font(10),
            justify="right" if self.lang == "ar" else "left",
        )
        self.style.configure(
            "Error.TLabel",
            background=SURFACE,
            foreground=DANGER,
            font=self.app_font(10, "bold"),
            justify="right" if self.lang == "ar" else "left",
        )
        self.style.configure(
            "Accent.TButton",
            background=ACCENT,
            bordercolor=ACCENT,
            darkcolor=ACCENT,
            foreground="#ffffff",
            focuscolor=ACCENT,
            font=self.app_font(10, "bold"),
            lightcolor=ACCENT,
            padding=(14, 9),
            relief="flat",
        )
        self.style.map(
            "Accent.TButton",
            background=[("active", ACCENT_DARK), ("disabled", "#9fb8b5")],
            foreground=[("disabled", "#ecfdf5")],
        )
        self.style.configure(
            "Plain.TButton",
            background="#e8eef5",
            bordercolor="#d6e0ea",
            foreground=TEXT,
            focuscolor="#e8eef5",
            font=self.app_font(10),
            padding=(14, 9),
            relief="flat",
        )
        self.style.map(
            "Plain.TButton",
            background=[("active", "#dbe7f3"), ("disabled", "#edf2f7")],
            foreground=[("disabled", "#94a3b8")],
        )
        self.style.configure(
            "Secondary.TButton",
            background=SECONDARY,
            bordercolor=SECONDARY,
            foreground="#ffffff",
            focuscolor=SECONDARY,
            font=self.app_font(10, "bold"),
            padding=(14, 9),
            relief="flat",
        )
        self.style.map("Secondary.TButton", background=[("active", SECONDARY_DARK), ("disabled", "#9bb7ea")])

    def apply_language_layout(self):
        is_arabic = self.lang == "ar"
        sticky_side = "e" if is_arabic else "w"
        opposite_side = "w" if is_arabic else "e"
        justify = "right" if is_arabic else "left"

        self.title_label.grid_configure(sticky=sticky_side)
        self.language_frame.grid_configure(sticky=opposite_side)
        self.language_label.config(anchor=sticky_side, justify=justify)

        for label in (
            self.power_label,
            self.speed_label,
            self.ship_label,
            self.media_label,
            self.result_label,
            self.recommendation_label,
            self.video_size_label,
            self.video_size_value,
            self.video_status_label,
        ):
            label.config(anchor=sticky_side, justify=justify)

        self.result_label.grid_configure(sticky="ew")
        self.recommendation_label.grid_configure(sticky="ew")
        self.entry_power.config(justify="right" if is_arabic else "left")
        self.entry_speed.config(justify="right" if is_arabic else "left")
        self.video_label.config(font=self.app_font(10), justify="center")

    def refresh_language(self):
        selected_ship_type = self.get_selected_ship_type() or SHIP_TYPES[0]
        selected_media_mode = self.get_selected_media_mode()

        self.configure_styles()
        self.apply_language_layout()

        self.root.title(self.tr("Marine Propeller Advisor", "مستشار الرفاس البحري"))
        self.title_label.config(text=self.tr("Marine Propeller Advisor", "مستشار الرفاس البحري"))
        self.language_label.config(text=self.tr("Language", "اللغة"))
        self.input_frame.config(text=self.tr("Inputs", "المدخلات"))
        self.power_label.config(text=self.tr("Power (kW)", "القدرة (kW)"))
        self.speed_label.config(text=self.tr("Speed (knots)", "السرعة (عقدة)"))
        self.ship_label.config(text=self.tr("Ship Type", "نوع السفينة"))
        self.media_label.config(text=self.tr("Media Type", "نوع الوسائط"))
        self.entry_power.config(font=self.app_font(11))
        self.entry_speed.config(font=self.app_font(11))
        self.calculate_button.config(text=self.tr("Calculate", "احسب"))
        self.reset_button.config(text=self.tr("Reset", "إعادة ضبط"))
        self.result_frame.config(text=self.tr("Estimate", "التقدير"))
        self.recommendation_frame.config(text=self.tr("Recommendation", "التوصية"))
        self.video_frame.config(text=self.tr("Visualizer", "العارض"))
        self.video_size_label.config(text=self.tr("Media width", "عرض الوسائط"))
        self.video_play_button.config(text=self.tr("Play", "تشغيل"))
        self.video_pause_button.config(text=self.tr("Pause", "إيقاف مؤقت"))
        self.video_restart_button.config(text=self.tr("Restart", "إعادة تشغيل"))
        self.video_open_button.config(text=self.tr("Open Media", "فتح الوسائط"))
        self.video_size_reset_button.config(text=self.tr("Reset size", "إعادة الحجم"))
        self.lang_combo.config(font=self.app_font(10))
        self.ship_combo.config(values=ship_display_values(self.lang), font=self.app_font(10))
        self.set_ship_combo(selected_ship_type)
        self.media_combo.config(values=self.media_display_values(), font=self.app_font(10))
        self.set_media_combo(selected_media_mode)
        self.set_video_buttons_state()

        if not self.result_label.cget("text") and not self.recommendation_label.cget("text"):
            self.show_video_placeholder(self.tr("Media preview", "معاينة الوسائط"))

    def update_scroll_region(self, event=None):
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def resize_scroll_frame(self, event):
        requested_width = self.main_frame.winfo_reqwidth()
        requested_height = self.main_frame.winfo_reqheight()
        self.scroll_canvas.itemconfigure(
            self.main_window,
            width=max(event.width, requested_width),
            height=max(event.height, requested_height),
        )

        if hasattr(self, "result_label"):
            side_width = self.side_column.winfo_width() if hasattr(self, "side_column") else 0
            visualizer_width = self.visualizer_column.winfo_width() if hasattr(self, "visualizer_column") else 0
            info_wrap = max(260, side_width - 52) if side_width > 1 else max(280, event.width // 3)
            video_wrap = max(280, visualizer_width - 52) if visualizer_width > 1 else max(320, event.width // 2)
            self.result_label.config(wraplength=info_wrap)
            self.recommendation_label.config(wraplength=info_wrap)
            self.video_status_label.config(wraplength=video_wrap)

    def on_mousewheel(self, event):
        if event.num == 4:
            self.scroll_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.scroll_canvas.yview_scroll(1, "units")
        else:
            self.scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def reset_video_size(self):
        self.video_width_var.set(DEFAULT_VIDEO_WIDTH)
        self.resize_video_display()

    def close_app(self):
        self.stop_video_playback(clear_frame=False)
        self.root.destroy()

    def build_layout(self):
        self.scroll_canvas = tk.Canvas(self.root, bg=APP_BG, highlightthickness=0)
        self.vertical_scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=self.scroll_canvas.yview)
        self.horizontal_scrollbar = ttk.Scrollbar(self.root, orient="horizontal", command=self.scroll_canvas.xview)
        self.scroll_canvas.configure(
            yscrollcommand=self.vertical_scrollbar.set,
            xscrollcommand=self.horizontal_scrollbar.set,
        )
        self.scroll_canvas.grid(row=0, column=0, sticky="nsew")
        self.vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        self.horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

        self.main_frame = ttk.Frame(self.scroll_canvas, padding=(28, 24), style="App.TFrame")
        self.main_window = self.scroll_canvas.create_window((0, 0), window=self.main_frame, anchor="nw")
        self.main_frame.bind("<Configure>", self.update_scroll_region)
        self.scroll_canvas.bind("<Configure>", self.resize_scroll_frame)
        self.root.bind_all("<MouseWheel>", self.on_mousewheel)
        self.root.bind_all("<Button-4>", self.on_mousewheel)
        self.root.bind_all("<Button-5>", self.on_mousewheel)
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.rowconfigure(1, weight=1)

        self.header_frame = ttk.Frame(self.main_frame, style="Header.TFrame")
        self.header_frame.grid(row=0, column=0, sticky="ew")
        self.header_frame.columnconfigure(0, weight=1)

        self.title_label = ttk.Label(self.header_frame, style="Title.TLabel")
        self.title_label.grid(row=0, column=0, sticky="w")

        self.language_frame = ttk.Frame(self.header_frame, style="Header.TFrame")
        self.language_frame.grid(row=0, column=1, sticky="e")

        self.language_label = ttk.Label(self.language_frame, style="HeaderField.TLabel")
        self.language_label.grid(row=0, column=0, padx=(0, 8))

        self.lang_combo = ttk.Combobox(self.language_frame, values=["en", "ar"], width=6, state="readonly")
        self.lang_combo.grid(row=0, column=1)
        self.lang_combo.set(self.lang)
        self.lang_combo.bind("<<ComboboxSelected>>", self.change_lang)

        self.accent_bar = tk.Frame(self.header_frame, height=4, bg=ACCENT)
        self.accent_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        self.body_frame = ttk.Frame(self.main_frame, style="App.TFrame")
        self.body_frame.grid(row=1, column=0, sticky="nsew", pady=(20, 0))
        self.body_frame.columnconfigure(0, weight=3, minsize=520)
        self.body_frame.columnconfigure(1, weight=2, minsize=340)
        self.body_frame.rowconfigure(0, weight=1)

        self.visualizer_column = ttk.Frame(self.body_frame, style="App.TFrame")
        self.visualizer_column.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        self.visualizer_column.columnconfigure(0, weight=1)
        self.visualizer_column.rowconfigure(0, weight=1)

        self.side_column = ttk.Frame(self.body_frame, style="App.TFrame")
        self.side_column.grid(row=0, column=1, sticky="nsew", padx=(18, 0))
        self.side_column.columnconfigure(0, weight=1)
        self.side_column.rowconfigure(1, weight=1)
        self.side_column.rowconfigure(2, weight=1)

        self.input_frame = ttk.LabelFrame(self.side_column, padding=(18, 16), style="Panel.TLabelframe")
        self.input_frame.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.input_frame.columnconfigure(1, weight=1)

        self.power_label = ttk.Label(self.input_frame, style="Field.TLabel")
        self.power_label.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=6)
        self.entry_power = ttk.Entry(self.input_frame)
        self.entry_power.grid(row=0, column=1, sticky="ew", pady=6)

        self.speed_label = ttk.Label(self.input_frame, style="Field.TLabel")
        self.speed_label.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=6)
        self.entry_speed = ttk.Entry(self.input_frame)
        self.entry_speed.grid(row=1, column=1, sticky="ew", pady=6)

        self.ship_label = ttk.Label(self.input_frame, style="Field.TLabel")
        self.ship_label.grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)
        self.ship_combo = ttk.Combobox(self.input_frame, values=SHIP_TYPES, state="readonly")
        self.ship_combo.grid(row=2, column=1, sticky="ew", pady=6)
        self.set_ship_combo(SHIP_TYPES[0])

        self.media_label = ttk.Label(self.input_frame, style="Field.TLabel")
        self.media_label.grid(row=3, column=0, sticky="w", padx=(0, 12), pady=6)
        self.media_combo = ttk.Combobox(self.input_frame, values=self.media_display_values(), state="readonly")
        self.media_combo.grid(row=3, column=1, sticky="ew", pady=6)
        self.set_media_combo(MEDIA_MODES[0])
        self.media_combo.bind("<<ComboboxSelected>>", self.change_media_mode)

        self.button_frame = ttk.Frame(self.input_frame, style="Panel.TFrame")
        self.button_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.button_frame.columnconfigure(0, weight=1)
        self.button_frame.columnconfigure(1, weight=1)

        self.calculate_button = ttk.Button(self.button_frame, command=self.calculate, style="Accent.TButton")
        self.calculate_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.reset_button = ttk.Button(self.button_frame, command=self.reset, style="Plain.TButton")
        self.reset_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.result_frame = ttk.LabelFrame(self.side_column, padding=(18, 16), style="Panel.TLabelframe")
        self.result_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 14))
        self.result_frame.columnconfigure(0, weight=1)
        self.result_frame.rowconfigure(0, weight=1)

        self.result_label = ttk.Label(self.result_frame, text="", wraplength=540, style="Result.TLabel")
        self.result_label.grid(row=0, column=0, sticky="new")

        self.recommendation_frame = ttk.LabelFrame(self.side_column, padding=(18, 16), style="Panel.TLabelframe")
        self.recommendation_frame.grid(row=2, column=0, sticky="nsew")
        self.recommendation_frame.columnconfigure(0, weight=1)
        self.recommendation_frame.rowconfigure(0, weight=1)

        self.recommendation_label = ttk.Label(
            self.recommendation_frame,
            text="",
            wraplength=540,
            style="Result.TLabel",
        )
        self.recommendation_label.grid(row=0, column=0, sticky="new")

        self.video_frame = ttk.LabelFrame(self.visualizer_column, padding=(18, 16), style="Panel.TLabelframe")
        self.video_frame.grid(row=0, column=0, sticky="nsew")

        self.video_button_frame = ttk.Frame(self.video_frame, style="Panel.TFrame")
        self.video_button_frame.pack(side="bottom", fill="x", pady=(12, 0))
        for column in range(4):
            self.video_button_frame.columnconfigure(column, weight=1)

        self.video_play_button = ttk.Button(
            self.video_button_frame,
            command=self.play_current_video,
            style="Accent.TButton",
        )
        self.video_play_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.video_pause_button = ttk.Button(
            self.video_button_frame,
            command=self.pause_current_video,
            style="Plain.TButton",
        )
        self.video_pause_button.grid(row=0, column=1, sticky="ew", padx=(0, 6))

        self.video_restart_button = ttk.Button(
            self.video_button_frame,
            command=self.restart_current_video,
            style="Plain.TButton",
        )
        self.video_restart_button.grid(row=0, column=2, sticky="ew", padx=(0, 6))

        self.video_open_button = ttk.Button(
            self.video_button_frame,
            command=self.open_current_video,
            style="Secondary.TButton",
        )
        self.video_open_button.grid(row=0, column=3, sticky="ew")

        self.video_size_frame = ttk.Frame(self.video_frame, style="Panel.TFrame")
        self.video_size_frame.pack(side="bottom", fill="x", pady=(10, 0))
        self.video_size_frame.columnconfigure(1, weight=1)

        self.video_size_label = ttk.Label(self.video_size_frame, style="Field.TLabel")
        self.video_size_label.grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.video_width_slider = ttk.Scale(
            self.video_size_frame,
            from_=MIN_VIDEO_WIDTH,
            to=MAX_VIDEO_WIDTH,
            variable=self.video_width_var,
            command=self.resize_video_display,
        )
        self.video_width_slider.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        self.video_size_value = ttk.Label(self.video_size_frame, width=14, style="Field.TLabel")
        self.video_size_value.grid(row=0, column=2, sticky="e", padx=(0, 10))

        self.video_size_reset_button = ttk.Button(
            self.video_size_frame,
            command=self.reset_video_size,
            style="Plain.TButton",
        )
        self.video_size_reset_button.grid(row=0, column=3, sticky="e")

        self.video_status_label = ttk.Label(self.video_frame, text="", wraplength=540, style="Field.TLabel")
        self.video_status_label.pack(side="bottom", fill="x", pady=(10, 0))

        initial_video_width, initial_video_height = self.current_video_size()
        self.video_panel = tk.Frame(
            self.video_frame,
            width=initial_video_width,
            height=initial_video_height,
            bg=VIDEO_SURFACE,
            highlightbackground=ACCENT,
            highlightthickness=1,
        )
        self.video_panel.pack(fill="both", expand=True, pady=(6, 0))
        self.video_panel.pack_propagate(False)

        self.video_label = tk.Label(
            self.video_panel,
            bg=VIDEO_SURFACE,
            fg="#dbeafe",
            font=self.app_font(10),
            justify="center",
            wraplength=400,
        )
        self.video_label.pack(fill="both", expand=True)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    MarinePropellerAdvisorApp().run()
