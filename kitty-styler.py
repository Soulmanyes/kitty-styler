#!/usr/bin/env python3
"""Kitty Styler: pick kitty's look from dropdowns and see it live before applying."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import warnings
import zipfile

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
warnings.filterwarnings("ignore", ".*cairo_set_source_pixbuf", DeprecationWarning)
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

KITTY_DIR = os.environ.get("KITTY_STYLER_DIR", os.path.expanduser("~/.config/kitty"))
CONF = os.path.join(KITTY_DIR, "kitty.conf")
NOCTALIA_THEME = os.path.join(KITTY_DIR, "themes", "noctalia.conf")
STYLER_THEME = os.path.join(KITTY_DIR, "themes", "kitty-styler.conf")
STYLER_INCLUDE = "include themes/kitty-styler.conf"
THEMES_ZIP = os.environ.get("KITTY_STYLER_THEMES", os.path.expanduser("~/.cache/kitty/kitty-themes.zip"))
# Keep this many kitty.conf.bak-<time> backups; older ones are deleted on Apply.
KEEP_BACKUPS = 5
NOCTALIA_SETTINGS = os.environ.get("KITTY_STYLER_NOCTALIA",
                                   os.path.expanduser("~/.local/state/noctalia/settings.toml"))
STARSHIP_CONF = os.path.expanduser(os.environ.get("KITTY_STYLER_STARSHIP",
                                                  os.environ.get("STARSHIP_CONFIG", "~/.config/starship.toml")))
STARSHIP_BEGIN = "# >>> KITTY STYLER STARSHIP PALETTE >>>"
STARSHIP_END = "# <<< KITTY STYLER STARSHIP PALETTE <<<"
STARSHIP_PALETTE_LINE = 'palette = "kitty-styler"'
# Comments inside our starship block that remember what to restore when going back to Noctalia.
STARSHIP_FLAG = "# kitty-styler turned off Noctalia's starship template"
STARSHIP_PREV = "# kitty-styler previous palette line: "
# Used when starship.toml has no Noctalia palette to copy names from.
BASIC_PALETTE = {"black": "color0", "red": "color1", "green": "color2", "yellow": "color3", "blue": "color4",
                 "magenta": "color5", "purple": "color5", "cyan": "color6", "white": "color7"}
STARSHIP_AVAILABLE = os.path.exists(STARSHIP_CONF) or shutil.which("starship") is not None
BLOCK_BEGIN = "# BEGIN_KITTY_STYLER (managed by kitty-styler; keep this block last so it wins)"
BLOCK_END = "# END_KITTY_STYLER"
# Noctalia's kitty template keeps a wallpaper-coloured theme here; without it, the first
# theme entry keeps whatever colours kitty.conf already has.
NOCTALIA = os.path.exists(NOCTALIA_THEME)
BASE_LABEL = "Noctalia (follows wallpaper)" if NOCTALIA else "Keep current theme"
APP_ID = "io.github.soulmanyes.KittyStyler"

KITTY_COLORS = {
    "foreground": "#dddddd", "background": "#000000",
    "cursor": "#cccccc", "cursor_text_color": None,
    "selection_foreground": "#000000", "selection_background": "#fffacd",
    "active_tab_foreground": "#000000", "active_tab_background": "#eeeeee",
    "inactive_tab_foreground": "#444444", "inactive_tab_background": "#999999",
    "tab_bar_background": None,
    "color0": "#000000", "color1": "#cc0403", "color2": "#19cb00", "color3": "#cecb00",
    "color4": "#0d73cc", "color5": "#cb1ed1", "color6": "#0dcdcd", "color7": "#dddddd",
    "color8": "#767676", "color9": "#f2201f", "color10": "#23fd00", "color11": "#fffd00",
    "color12": "#1a8fff", "color13": "#fd28ff", "color14": "#14ffff", "color15": "#ffffff",
}

TAB_STYLES = {
    "fade": ("fade", None),
    "separator": ("separator", None),
    "slant": ("slant", None),
    "powerline angled": ("powerline", "angled"),
    "powerline slanted": ("powerline", "slanted"),
    "powerline round": ("powerline", "round"),
    "hidden": ("hidden", None),
}

# (label, kitty key, choices, kitty default). Choices are the strings written to kitty.conf.
OPTIONS = [
    ("Font size", "font_size", [f"{s:.1f}" for s in [8, 9, 10, 10.5, 11, 11.5, 12, 12.5, 13, 14, 15, 16, 18, 20, 22, 24]], "11.0"),
    ("Background opacity", "background_opacity", [f"{i / 100:.2f}" for i in range(30, 101, 5)], "1.00"),
    ("Window padding", "window_padding_width", ["0", "2", "4", "6", "8", "10", "12", "15", "20", "25", "30", "40"], "0"),
    ("Cursor shape", "cursor_shape", ["block", "beam", "underline"], "block"),
    ("Cursor blink", "cursor_blink_interval", ["-1", "0", "0.3", "0.5", "0.8", "1.2"], "-1"),
    ("Cursor trail", "cursor_trail", ["0", "1", "3", "10", "30"], "0"),
    ("Line height", "modify_font cell_height", ["90%", "100%", "105%", "110%", "115%", "120%", "130%", "140%"], "100%"),
    ("Letter spacing", "modify_font cell_width", ["90%", "95%", "100%", "105%", "110%", "120%"], "100%"),
    ("Tab bar position", "tab_bar_edge", ["bottom", "top"], "bottom"),
    ("Tab bar alignment", "tab_bar_align", ["left", "center", "right"], "left"),
]
# Starship prompt. Colours are ANSI names, so they follow the kitty theme (and the Noctalia palette).
PROMPT_BEGIN = "# >>> KITTY STYLER PROMPT >>>"
PROMPT_END = "# <<< KITTY STYLER PROMPT <<<"
PROMPT_STATE = "# kitty-styler settings: "
PROMPT_COLORS = {"": "Default text", "red": "Red", "green": "Green", "yellow": "Yellow", "blue": "Blue",
                 "purple": "Magenta", "cyan": "Cyan", "white": "White", "bright-black": "Grey",
                 "bright-red": "Bright red", "bright-green": "Bright green", "bright-yellow": "Bright yellow",
                 "bright-blue": "Bright blue", "bright-purple": "Bright magenta", "bright-cyan": "Bright cyan",
                 "bright-white": "Bright white"}
COLOR_SLOTS = {"": "foreground", "black": "color0", "red": "color1", "green": "color2", "yellow": "color3",
               "blue": "color4", "purple": "color5", "cyan": "color6", "white": "color7",
               "bright-black": "color8", "bright-red": "color9", "bright-green": "color10",
               "bright-yellow": "color11", "bright-blue": "color12", "bright-purple": "color13",
               "bright-cyan": "color14", "bright-white": "color15"}
# Nerd Font icons for starship's os module; keys are starship's own OS names.
OS_SYMBOLS = {"Linux": "\uf17c", "CachyOS": "\uf17c", "Alpine": "\uf300", "AlmaLinux": "\uf31d",
              "Arch": "\uf303", "Artix": "\uf31f", "CentOS": "\uf304", "Debian": "\uf306",
              "Elementary": "\uf309", "EndeavourOS": "\uf322", "Fedora": "\uf30a", "FreeBSD": "\uf30c",
              "Garuda": "\uf337", "Gentoo": "\uf30d", "Kali": "\uf327", "Macos": "\uf179",
              "Manjaro": "\uf312", "Mint": "\uf30e", "NixOS": "\uf313", "Nobara": "\uf380",
              "OpenBSD": "\uf328", "openSUSE": "\uf314", "Pop": "\uf32a", "Raspbian": "\uf315",
              "Redhat": "\uf316", "RedHatEnterprise": "\uf316", "RockyLinux": "\uf32b", "Solus": "\uf32d",
              "Ubuntu": "\uf31b", "Void": "\uf32e", "Windows": "\uf17a", "Zorin": "\uf32f"}
# /etc/os-release ID to starship's OS name, for the preview.
OS_IDS = {"almalinux": "AlmaLinux", "alpine": "Alpine", "arch": "Arch", "artix": "Artix", "cachyos": "CachyOS",
          "centos": "CentOS", "debian": "Debian", "elementary": "Elementary", "endeavouros": "EndeavourOS",
          "fedora": "Fedora", "garuda": "Garuda", "gentoo": "Gentoo", "kali": "Kali", "linuxmint": "Mint",
          "manjaro": "Manjaro", "nixos": "NixOS", "nobara": "Nobara", "opensuse-leap": "openSUSE",
          "opensuse-tumbleweed": "openSUSE", "pop": "Pop", "raspbian": "Raspbian", "rhel": "RedHatEnterprise",
          "rocky": "RockyLinux", "solus": "Solus", "ubuntu": "Ubuntu", "void": "Void", "zorin": "Zorin"}
DIR_ICONS = {"home": "\uf015", "folder": "\uf115", "none": ""}
CLOCK_ICON = "\uf017"
TIME_FORMATS = {"%H:%M:%S": "21:52:28", "%H:%M": "21:52", "%I:%M %p": "09:52 PM", "%I:%M:%S %p": "09:52:28 PM"}
SHOW_HIDE = ["show", "hide"]
# Nerd Font end caps for each segment style; None is plain coloured text.
SEGMENT_STYLES = {"plain": None, "bubbles": ("\ue0b6", "\ue0b4"), "arrows": ("\ue0b2", "\ue0b0"),
                  "slanted": ("\ue0ba", "\ue0bc"), "blocks": ("", "")}
# Cell-relative outlines for the caps kitty draws itself.
CAP_SHAPES = {"\ue0b4": "arc", "\ue0b6": "arc",
              "\ue0b0": [(0, 0), (1, 0.5), (0, 1)], "\ue0b2": [(1, 0), (0, 0.5), (1, 1)],
              "\ue0ba": [(1, 0), (1, 1), (0, 1)], "\ue0bc": [(0, 0), (1, 0), (0, 1)]}
SEGMENT_TEXT = {"black": "Dark", "white": "Light"}
DIR_LENGTHS = {"1": "Last folder", "2": "Last 2 folders", "3": "Last 3 folders", "5": "Last 5 folders",
               "0": "Full path"}
DURATIONS = {"1000": "Over 1 s", "2000": "Over 2 s", "3000": "Over 3 s", "5000": "Over 5 s", "10000": "Over 10 s"}
USER_HOST = {"never": "Never", "ssh": "Only over SSH", "always": "Always"}
LANG_SYMBOLS = {"python": ("\U0001f40d ", "yellow"), "nodejs": ("\ue718 ", "green"),
                "rust": ("\U0001f980 ", "red"), "golang": ("\ue627 ", "cyan")}
COLORS = (list(PROMPT_COLORS), PROMPT_COLORS)
# (label, settings key, choices, default, display names). A key of None is a sub-heading.
PROMPT_OPTIONS = [
    ("Layout", None, None, None, None),
    ("Segment style", "p_style", list(SEGMENT_STYLES), "plain", None),
    ("Text inside segments", "p_seg_text", list(SEGMENT_TEXT), "black", SEGMENT_TEXT),
    ("Lines", "p_lines", ["1", "2"], "1", {"1": "One line", "2": "Symbol on its own line"}),
    ("Blank line between prompts", "p_newline", ["no", "yes"], "no", None),
    ("Left side", None, None, None, None),
    ("OS icon", "p_os", SHOW_HIDE, "show", None),
    ("OS icon colour", "p_os_color", *COLORS[:1], "", PROMPT_COLORS),
    ("User and host", "p_userhost", list(USER_HOST), "ssh", USER_HOST),
    ("User and host colour", "p_userhost_color", *COLORS[:1], "yellow", PROMPT_COLORS),
    ("Folder icon", "p_dir_icon", list(DIR_ICONS), "home", None),
    ("Folder path", "p_dir_len", list(DIR_LENGTHS), "3", DIR_LENGTHS),
    ("Start path at git repo", "p_dir_repo", ["yes", "no"], "yes", None),
    ("Folder colour", "p_dir_color", *COLORS[:1], "blue", PROMPT_COLORS),
    ("Bold folder", "p_dir_bold", ["yes", "no"], "yes", None),
    ("Git branch", "p_git", SHOW_HIDE, "show", None),
    ("Git branch icon", "p_git_icon", SHOW_HIDE, "show", None),
    ("Git branch colour", "p_git_color", *COLORS[:1], "green", PROMPT_COLORS),
    ("Git changes", "p_gitst", SHOW_HIDE, "show", None),
    ("Git changes colour", "p_gitst_color", *COLORS[:1], "yellow", PROMPT_COLORS),
    ("Prompt symbol", "p_symbol", ["❯", "➜", "›", "❱", "▶", "λ", "$", "%", "→", "#", "»"], "❯", None),
    ("Symbol colour", "p_ok_color", *COLORS[:1], "green", PROMPT_COLORS),
    ("Symbol after an error", "p_err_color", *COLORS[:1], "red", PROMPT_COLORS),
    ("Right side", None, None, None, None),
    ("Error code", "p_status", SHOW_HIDE, "show", None),
    ("Error code colour", "p_status_color", *COLORS[:1], "red", PROMPT_COLORS),
    ("Command duration", "p_dur", SHOW_HIDE, "show", None),
    ("Show duration when", "p_dur_min", list(DURATIONS), "3000", DURATIONS),
    ("Duration colour", "p_dur_color", *COLORS[:1], "yellow", PROMPT_COLORS),
    ("Language versions", "p_langs", SHOW_HIDE, "show", None),
    ("Clock", "p_time", SHOW_HIDE, "show", None),
    ("Time format", "p_time_format", list(TIME_FORMATS), "%H:%M:%S", TIME_FORMATS),
    ('"at" before the time', "p_time_at", SHOW_HIDE, "show", None),
    ("Clock icon", "p_time_icon", SHOW_HIDE, "show", None),
    ("Time colour", "p_time_color", *COLORS[:1], "cyan", PROMPT_COLORS),
]
PROMPT_KEYS = [o for o in PROMPT_OPTIONS if o[1]]
CHOICE_NOTES = {"cursor_blink_interval": {"-1": "-1 (system default)", "0": "0 (no blink)"},
                "cursor_trail": {"0": "0 (off)"}}
CHOICE_NOTES.update({key: names for _l, key, _c, _d, names in PROMPT_KEYS if names})


def parse_conf(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = (line.split(None, 1) + ["", ""])[:2]
        if key == "modify_font":
            # Several modify_font lines can exist; key them by what they modify.
            what, value = (value.split(None, 1) + ["", ""])[:2]
            key = f"{key} {what}"
        out[key] = value.strip()
    return out


def parse_conf_file(path, skip=(), seen=None):
    """parse_conf for a kitty config file, following include and globinclude in place.

    geninclude is skipped since it runs a program. Files in skip are left out.
    """
    seen = set() if seen is None else seen
    real = os.path.realpath(path)
    if real in seen or real in skip:
        return {}
    seen.add(real)
    out = {}
    for line in read(path).splitlines():
        key, value = (line.strip().split(None, 1) + ["", ""])[:2]
        if key in ("include", "globinclude") and value:
            # Relative paths are relative to the kitty config folder, like kitty does it.
            target = os.path.join(KITTY_DIR, os.path.expandvars(os.path.expanduser(value.strip())))
            for inc in sorted(glob.glob(target)) if key == "globinclude" else [target]:
                out.update(parse_conf_file(inc, skip, seen))
        else:
            out.update(parse_conf(line))
    return out


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def load_themes():
    """Return {name: conf text}; the Noctalia theme always comes first."""
    themes = {BASE_LABEL: None}
    try:
        with zipfile.ZipFile(THEMES_ZIP) as z:
            index = next(n for n in z.namelist() if n.endswith("/themes.json"))
            root = index.rsplit("/", 1)[0]
            entries = []
            for t in json.loads(z.read(index)):
                text = z.read(f"{root}/{t['file']}").decode("utf-8", "replace")
                entries.append((t["name"] if t.get("is_dark", True) else f"{t['name']} (light)", text))
            for name, text in sorted(entries, key=lambda e: e[0].lower()):
                themes.setdefault(name, text)
    except (OSError, KeyError, StopIteration, ValueError, zipfile.BadZipFile):
        pass
    return themes


def mono_fonts():
    try:
        out = subprocess.run(["fc-list", ":spacing=mono", "family"], capture_output=True, text=True).stdout
    except OSError:
        out = ""
    fams = {line.split(",")[0].strip() for line in out.splitlines() if line.strip()}
    return ["monospace"] + sorted(fams, key=str.lower)


def wallpaper_path():
    try:
        with open(NOCTALIA_SETTINGS, "rb") as f:
            path = tomllib.load(f).get("wallpaper", {}).get("default", {}).get("path")
        return path if path and os.path.exists(path) else None
    except (OSError, tomllib.TOMLDecodeError):
        return None


def detect_os():
    """starship's name for this OS, from /etc/os-release; Linux when not in OS_IDS."""
    for line in read("/etc/os-release").splitlines():
        if line.startswith("ID="):
            return OS_IDS.get(line[3:].strip().strip('"'), "Linux")
    return "Linux"


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


class Cursor(Gtk.DrawingArea):
    """The preview's cursor as its own widget, so blinking doesn't repaint the whole preview.

    Redrawing the preview stalled the window for ~60 ms, which made open dropdowns skip frames
    every blink.
    """

    def __init__(self):
        super().__init__(halign=Gtk.Align.START, valign=Gtk.Align.START, can_target=False)
        self.color = "#cccccc"
        self.rect = None
        self.set_draw_func(lambda _a, cr, _w, _h: (cr.set_source_rgb(*hex_rgb(self.color)), cr.paint()))

    def place(self, rect, color):
        # Called while the preview is being drawn; resizing a widget then is not allowed, so defer it.
        rect = tuple(round(v) for v in rect)
        if (rect, color) != (self.rect, self.color):
            self.rect, self.color = rect, color
            GLib.idle_add(self.apply_place)

    def apply_place(self):
        x, y, w, h = self.rect
        self.set_margin_start(x)
        self.set_margin_top(y)
        self.set_size_request(max(1, w), max(1, h))
        self.queue_draw()
        return False


class Preview(Gtk.DrawingArea):
    def __init__(self, app):
        super().__init__(hexpand=True, vexpand=True)
        self.app = app
        wp = wallpaper_path()
        try:
            self.wallpaper = GdkPixbuf.Pixbuf.new_from_file(wp) if wp else None
        except GLib.Error:
            # Not an image GdkPixbuf can read (a video wallpaper, say); use the checkerboard.
            self.wallpaper = None
        # The wallpaper scaled to the current preview size. Scaling a 4K image on every
        # redraw took ~50 ms and made the dropdowns lag, so it is done once per size.
        self.bg_cache = None
        self.bg_rebuild_id = None
        self.set_draw_func(self.draw)

    def build_bg(self, w, h):
        pw, ph = self.wallpaper.get_width(), self.wallpaper.get_height()
        scale = max(w / pw, h / ph)
        sw, sh = max(1, round(pw * scale)), max(1, round(ph * scale))
        scaled = self.wallpaper.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(surf)
        Gdk.cairo_set_source_pixbuf(cr, scaled, (w - sw) / 2, (h - sh) / 2)  # deprecated, still simplest
        cr.paint()
        self.bg_cache = (w, h, surf)

    def rebuild_bg(self):
        self.bg_rebuild_id = None
        self.build_bg(self.get_width(), self.get_height())
        self.queue_draw()
        return False

    def draw(self, _area, cr, w, h):
        s = self.app.settings
        c = self.app.colors
        # Desktop behind the window, so opacity is visible.
        if self.wallpaper:
            if self.bg_cache is None:
                self.build_bg(w, h)
            cw0, ch0, surf = self.bg_cache
            if (cw0, ch0) != (w, h):
                # Mid-resize: stretch the old one and rebuild once the size settles.
                if self.bg_rebuild_id:
                    GLib.source_remove(self.bg_rebuild_id)
                self.bg_rebuild_id = GLib.timeout_add(150, self.rebuild_bg)
            cr.save()
            cr.scale(w / cw0, h / ch0)
            cr.set_source_surface(surf, 0, 0)
            cr.paint()
            cr.restore()
        else:
            for y in range(0, h, 24):
                for x in range(0, w, 24):
                    cr.set_source_rgb(*((0.35,) * 3 if (x + y) // 24 % 2 else (0.6,) * 3))
                    cr.rectangle(x, y, 24, 24)
                    cr.fill()

        mx = 30
        win = (mx, mx, w - 2 * mx, h - 2 * mx)
        cr.rectangle(*win)
        cr.set_source_rgba(*hex_rgb(c["background"]), float(s["background_opacity"]))
        cr.fill()

        font = Pango.FontDescription.from_string(f"{s['font_family']} {s['font_size']}")
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(font)
        layout.set_text("M", -1)
        _ink, logical = layout.get_pixel_extents()
        # modify_font cell_width/cell_height widen the cells; glyphs stay their size, centred vertically.
        cw = logical.width * float(s["modify_font cell_width"].rstrip("%")) / 100
        lh = logical.height * float(s["modify_font cell_height"].rstrip("%")) / 100
        yoff = (lh - logical.height) / 2
        per_cell = cw != logical.width
        pad = float(s["window_padding_width"]) * 96 / 72

        cr.save()
        cr.rectangle(*win)
        cr.clip()
        style, pl = TAB_STYLES[s["tab_bar"]]
        tab_h = lh if style != "hidden" else 0
        top = s["tab_bar_edge"] == "top"
        tab_y = win[1] if top else win[1] + win[3] - tab_h
        text_y0 = win[1] + (tab_h if top else 0) + pad

        x0 = win[0] + pad
        cursor_xy = None
        for i, line in enumerate(self.app.sample_lines()):
            y = text_y0 + i * lh
            x = x0
            for j, (text, fg, bg, attrs) in enumerate(line):
                if text == "\0cursor":
                    cursor_xy = (x, y)
                    continue
                if text == "\0right":
                    # Right prompt: the rest of the line ends at the right padding. Like zsh,
                    # drop it when it would run into the command.
                    rest = sum(len(t) for t, *_ in line[j + 1:])
                    right_x = win[0] + win[2] - pad - rest * cw
                    if right_x < x + cw:
                        break
                    x = right_x
                    continue
                n = len(text)
                if bg:
                    cr.set_source_rgb(*hex_rgb(bg))
                    cr.rectangle(x, y, n * cw, lh)
                    cr.fill()
                markup = GLib.markup_escape_text(text)
                if "b" in attrs:
                    markup = f"<b>{markup}</b>"
                if "i" in attrs:
                    markup = f"<i>{markup}</i>"
                if "u" in attrs:
                    markup = f"<u>{markup}</u>"
                cr.set_source_rgb(*hex_rgb(fg))
                if text.isascii() and not per_cell:
                    layout.set_markup(markup, -1)
                    cr.move_to(x, y + yoff)
                    PangoCairo.show_layout(cr, layout)
                else:
                    self.draw_cells(cr, layout, text, attrs, x, y, cw, lh, yoff)
                x += n * cw
        if tab_h:
            # After the text, so a preview taller than the window tucks under the tab bar.
            self.draw_tabs(cr, layout, win[0], tab_y, win[2], tab_h, style, pl, cw)
        if cursor_xy:
            x, y = cursor_xy
            shape = s["cursor_shape"]
            if shape == "block":
                rect = (x, y, cw, lh)
            elif shape == "beam":
                rect = (x, y, max(1.5, cw * 0.12), lh)
            else:
                rect = (x, y + lh - 2, cw, 2)
            self.cursor.place(rect, c["cursor"])
        cr.restore()

    @staticmethod
    def draw_cells(cr, layout, text, attrs, x, y, cw, lh, yoff):
        """One character per cell, squeezing wide glyphs (Nerd Font icons) into one cell like kitty."""
        for ch in text:
            shape = CAP_SHAPES.get(ch)
            if shape:
                # Kitty draws powerline caps itself so they fill the whole cell; do the same.
                cr.new_path()
                if shape == "arc":
                    cr.save()
                    right = ch == "\ue0b4"
                    cr.translate(x if right else x + cw, y + lh / 2)
                    cr.scale(cw, lh / 2)
                    cr.arc(0, 0, 1, -1.5708, 1.5708) if right else cr.arc(0, 0, 1, 1.5708, 4.7124)
                    cr.restore()
                else:
                    pts = [(x + px * cw, y + py * lh) for px, py in shape]
                    cr.move_to(*pts[0])
                    for pt in pts[1:]:
                        cr.line_to(*pt)
                cr.close_path()
                cr.fill()
                x += cw
                continue
            markup = GLib.markup_escape_text(ch)
            if "b" in attrs:
                markup = f"<b>{markup}</b>"
            if "i" in attrs:
                markup = f"<i>{markup}</i>"
            if "u" in attrs:
                # Underline the whole cell, so letter-spaced text keeps a continuous line.
                cr.rectangle(x, y + yoff + layout.get_pixel_extents()[1].height - 2, cw, 1)
                cr.fill()
            layout.set_markup(markup, -1)
            ink, logical = layout.get_pixel_extents()
            # Nerd Font icons often draw past their advance width, so go by the ink too.
            left = min(0, ink.x)
            gw = max(logical.width, ink.x + ink.width) - left
            cr.save()
            cr.move_to(x, y + yoff)
            if gw > cw * 1.05:
                cr.translate(x, y + yoff)
                cr.scale(cw / gw, 1)
                cr.move_to(-left, 0)
            PangoCairo.show_layout(cr, layout)
            cr.restore()
            x += cw

    def draw_tabs(self, cr, layout, x, y, w, h, style, pl, cw):
        c = self.app.colors
        bar_bg = c["tab_bar_background"] or c["background"]
        cr.set_source_rgb(*hex_rgb(bar_bg))
        cr.rectangle(x, y, w, h)
        cr.fill()
        tabs = [("1: zsh", True), ("2: nvim", False), ("3: btop", False)]
        extra = {"slant": h * 0.9, "powerline": h * 0.5, "separator": cw * 2}.get(style, 0)
        total = sum(len(f" {t} ") * cw + extra for t, _a in tabs)
        align = self.app.settings["tab_bar_align"]
        x += {"left": 0, "center": (w - total) / 2, "right": w - total}[align]
        for text, active in tabs:
            fg = c["active_tab_foreground"] if active else c["inactive_tab_foreground"]
            bg = c["active_tab_background"] if active else c["inactive_tab_background"]
            label = f" {text} "
            tw = len(label) * cw
            if style == "separator":
                bg = bar_bg if not active else bg
            if style == "fade" and not active:
                bg = bar_bg
            if style == "slant":
                cr.move_to(x, y + h)
                cr.line_to(x + h * 0.6, y)
                cr.line_to(x + tw + h * 1.2, y)
                cr.line_to(x + tw + h * 0.6, y + h)
                cr.close_path()
                cr.set_source_rgb(*hex_rgb(bg))
                cr.fill()
                tx = x + h * 0.6
                adv = tw + h * 0.9
            else:
                cr.set_source_rgb(*hex_rgb(bg))
                cr.rectangle(x, y, tw, h)
                cr.fill()
                tx = x
                adv = tw
                if style == "powerline":
                    cr.set_source_rgb(*hex_rgb(bg))
                    ex = x + tw
                    if pl == "round":
                        cr.save()
                        cr.translate(ex, y + h / 2)
                        cr.scale(h * 0.5, h / 2)
                        cr.arc(0, 0, 1, -1.5708, 1.5708)
                        cr.restore()
                    elif pl == "slanted":
                        cr.move_to(ex, y)
                        cr.line_to(ex + h * 0.5, y)
                        cr.line_to(ex, y + h)
                    else:
                        cr.move_to(ex, y)
                        cr.line_to(ex + h * 0.5, y + h / 2)
                        cr.line_to(ex, y + h)
                    cr.close_path()
                    cr.fill()
                    adv = tw + h * 0.5
            layout.set_markup(f"<i>{GLib.markup_escape_text(label)}</i>" if not active
                              else GLib.markup_escape_text(label), -1)
            cr.set_source_rgb(*hex_rgb(fg))
            cr.move_to(tx, y)
            PangoCairo.show_layout(cr, layout)
            x += adv
            if style == "separator":
                layout.set_text("┇", -1)
                cr.set_source_rgb(*hex_rgb(c["inactive_tab_foreground"]))
                cr.move_to(x, y)
                PangoCairo.show_layout(cr, layout)
                x += cw * 2


class StylerWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Kitty Styler", default_width=1350, default_height=780)
        self.themes = load_themes()
        self.fonts = mono_fonts()
        self.load_current()

        header = Gtk.HeaderBar()
        apply_btn = Gtk.Button(label="Apply to kitty")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self.on_apply)
        reset_btn = Gtk.Button(label="Reset to current")
        reset_btn.connect("clicked", self.on_reset)
        header.pack_end(apply_btn)
        header.pack_start(reset_btn)
        self.set_titlebar(header)

        grid = Gtk.Grid(row_spacing=10, column_spacing=12, margin_top=16, margin_bottom=16,
                        margin_start=16, margin_end=16)
        self.dropdowns = {}
        row = 0
        rows = [("Theme", "theme", list(self.themes)), ("Font", "font_family", self.fonts)]
        rows += [(label, key, choices) for label, key, choices, _d in OPTIONS]
        rows.append(("Tab bar style", "tab_bar", list(TAB_STYLES)))
        if STARSHIP_AVAILABLE:
            rows.append(("Prompt (starship)", None, None))
            rows.append(("Style the prompt", "prompt_on", None))
            rows += [(label, key, list(choices) if key else None) for label, key, choices, _d, _n in PROMPT_OPTIONS]
        # Everything below the prompt switch; hidden while it is off.
        self.prompt_widgets = []
        in_prompt = False
        for label, key, choices in rows:
            if key is None:
                head = Gtk.Label(label=label, xalign=0, margin_top=12)
                head.add_css_class("heading" if label.startswith("Prompt") else "dim-label")
                grid.attach(head, 0, row, 2, 1)
                if in_prompt:
                    self.prompt_widgets.append(head)
                in_prompt = in_prompt or label.startswith("Prompt")
                row += 1
                continue
            if key == "prompt_on":
                grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
                self.prompt_switch = Gtk.Switch(halign=Gtk.Align.START)
                self.prompt_switch.connect("notify::active", self.on_prompt_toggled)
                grid.attach(self.prompt_switch, 1, row, 1, 1)
                row += 1
                note = Gtk.Label(label="On: Apply writes a prompt block into starship.toml (backed up first). "
                                       "Off: Apply removes that block, so your own starship config is used.",
                                 xalign=0, wrap=True, max_width_chars=34)
                note.add_css_class("dim-label")
                grid.attach(note, 0, row, 2, 1)
                row += 1
                continue
            name_label = Gtk.Label(label=label, xalign=0)
            grid.attach(name_label, 0, row, 1, 1)
            notes = CHOICE_NOTES.get(key, {})
            dd = Gtk.DropDown.new_from_strings([notes.get(ch, ch) for ch in choices])
            dd.choices = choices
            dd.set_hexpand(True)
            if len(choices) > 15:
                dd.set_enable_search(True)
                dd.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
            dd.connect("notify::selected", self.on_changed, key)
            self.dropdowns[key] = dd
            if len(choices) > 6:
                # Step through long lists without reopening the dropdown each time.
                step = Gtk.Box(css_classes=["linked"])
                step.append(dd)
                for icon, delta, tip in (("go-previous-symbolic", -1, "Previous"), ("go-next-symbolic", 1, "Next")):
                    btn = Gtk.Button(icon_name=icon, tooltip_text=tip)
                    btn.connect("clicked", self.on_step, dd, delta)
                    step.append(btn)
                grid.attach(step, 1, row, 1, 1)
            else:
                grid.attach(dd, 1, row, 1, 1)
            if in_prompt:
                self.prompt_widgets += [name_label, step if len(choices) > 6 else dd]
            row += 1
            if key == "theme":
                self.theme_note = Gtk.Label(xalign=0, wrap=True, max_width_chars=34, selectable=True)
                self.theme_note.add_css_class("dim-label")
                grid.attach(self.theme_note, 0, row, 2, 1)
                row += 1
                self.reload_btn = Gtk.Button(label="Reload themes", halign=Gtk.Align.START)
                self.reload_btn.connect("clicked", self.on_reload_themes)
                grid.attach(self.reload_btn, 0, row, 2, 1)
                row += 1
            if key == "cursor_trail":
                note = Gtk.Label(label="Cursor trail only shows when the cursor moves; not drawn in the preview.",
                                 xalign=0, wrap=True, max_width_chars=34)
                note.add_css_class("dim-label")
                grid.attach(note, 0, row, 2, 1)
                row += 1
        self.status = Gtk.Label(xalign=0, wrap=True, max_width_chars=34, selectable=True)
        grid.attach(self.status, 0, row, 2, 1)

        side = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=440, hexpand=False)
        side.set_child(grid)
        self.preview = Preview(self)
        self.preview.cursor = Cursor()
        overlay = Gtk.Overlay(child=self.preview)
        overlay.add_overlay(self.preview.cursor)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(side)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        box.append(overlay)
        self.set_child(box)

        self.blink_id = None
        self.sync_dropdowns()

    # ---- state ----
    def load_current(self):
        text = read(CONF)
        conf = parse_conf_file(CONF)
        # The colours kitty has without our theme file, for "Keep current theme".
        self.base_colors = parse_conf_file(CONF, skip={os.path.realpath(STYLER_THEME)})
        s = {"font_family": conf.get("font_family", "monospace")}
        for _label, key, _choices, default in OPTIONS:
            s[key] = conf.get(key, default)
        style = conf.get("tab_bar_style", "fade")
        pl = conf.get("tab_powerline_style", "angled")
        s["tab_bar"] = next((k for k, v in TAB_STYLES.items() if v == (style, pl) or (v[0] == style and v[1] is None)),
                            "fade")
        s["theme"] = BASE_LABEL
        if STYLER_INCLUDE in text.splitlines():
            m = re.match(r"# Theme: (.+)", read(STYLER_THEME))
            if m and m.group(1).strip() in self.themes:
                s["theme"] = m.group(1).strip()
        s.update(load_prompt_settings())
        s["prompt_on"] = PROMPT_BEGIN in read(STARSHIP_CONF).splitlines()
        self.file_conf = conf
        self.settings = s

    def sync_dropdowns(self):
        self.syncing = True
        for key, dd in self.dropdowns.items():
            value = self.settings[key]
            if value not in dd.choices:
                # Keep values from kitty.conf that are not in our list.
                norm = next((ch for ch in dd.choices if same(ch, value)), None)
                if norm is None:
                    dd.choices.append(value)
                    dd.get_model().append(value)
                    norm = value
                value = norm
                self.settings[key] = value
            dd.set_selected(dd.choices.index(value))
        if STARSHIP_AVAILABLE:
            self.prompt_switch.set_active(self.settings["prompt_on"])
            for widget in self.prompt_widgets:
                widget.set_visible(self.settings["prompt_on"])
        self.syncing = False
        self.refresh()

    def on_step(self, _btn, dd, delta):
        dd.set_selected((dd.get_selected() + delta) % len(dd.choices))

    def on_changed(self, dd, _pspec, key):
        if self.syncing:
            return
        self.settings[key] = dd.choices[dd.get_selected()]
        self.refresh()

    def on_prompt_toggled(self, switch, _pspec):
        if self.syncing:
            return
        self.settings["prompt_on"] = switch.get_active()
        for widget in self.prompt_widgets:
            widget.set_visible(switch.get_active())
        self.refresh()

    def on_reload_themes(self, _btn):
        self.themes = load_themes()
        dd = self.dropdowns["theme"]
        self.syncing = True
        dd.choices[:] = list(self.themes)
        dd.get_model().splice(0, dd.get_model().get_n_items(), dd.choices)
        self.syncing = False
        self.sync_dropdowns()

    def refresh(self):
        name = self.settings["theme"]
        if name != BASE_LABEL:
            conf = parse_conf(self.themes[name])
        else:
            conf = parse_conf(read(NOCTALIA_THEME)) if NOCTALIA else self.base_colors
        colors = dict(KITTY_COLORS)
        colors.update({k: v for k, v in conf.items() if k in colors and v.startswith("#")})
        self.colors = colors
        if len(self.themes) == 1:
            note = ("No kitty themes found. Run  kitty +kitten themes  once (Esc quits it) so kitty downloads "
                    "its theme collection, then press Reload themes.")
        elif name == BASE_LABEL:
            note = ""
        elif NOCTALIA:
            note = ("Kitty and the starship prompt will stop following Noctalia's wallpaper colours. "
                    "Pick Noctalia again to go back.")
        else:
            note = (f"Apply saves this theme as themes/kitty-styler.conf and includes it at the end of kitty.conf. "
                    f'Pick "{BASE_LABEL}" to go back to your own colours.')
        self.theme_note.set_label(note)
        self.theme_note.set_visible(bool(note))
        self.reload_btn.set_visible(len(self.themes) == 1)
        if self.blink_id:
            GLib.source_remove(self.blink_id)
            self.blink_id = None
        self.preview.cursor.set_opacity(1)
        interval = float(self.settings["cursor_blink_interval"])
        if interval != 0:
            ms = 500 if interval < 0 else int(interval * 1000)
            self.blink_id = GLib.timeout_add(ms, self.blink)
        self.preview.queue_draw()

    def blink(self):
        cursor = self.preview.cursor
        cursor.set_opacity(0 if cursor.get_opacity() else 1)
        return True

    def sample_lines(self):
        c = self.colors
        fg = c["foreground"]
        sel = (c["selection_foreground"], c["selection_background"])
        k = [c[f"color{i}"] for i in range(16)]

        p = self.settings
        if not p["prompt_on"]:
            # Their own prompt is unknown, so show ours with its defaults.
            p = dict(p, **{key: default for _l, key, _c, default, _n in PROMPT_KEYS})

        def col(name):
            return c[COLOR_SLOTS[name]]

        caps = SEGMENT_STYLES[p["p_style"]]
        seg_text = col(p["p_seg_text"])

        def seg(text, name, attrs="", word=""):
            """One prompt segment, either coloured text or a filled segment in that colour, then a space."""
            if not caps:
                return ([(word, fg, None, "")] if word else []) + [(text + " ", col(name), None, attrs)]
            color = col(name or "white")
            out = [(caps[0], color, None, "")] if caps[0] else []
            out.append((f" {text} ", seg_text, color, attrs))
            if caps[1]:
                out.append((caps[1], color, None, ""))
            return out + [(" ", fg, None, "")]

        def dir_path():
            parts = ["~", "code", "my-app", "src"]
            if p["p_dir_repo"] == "yes":
                parts = parts[2:]
            n = int(p["p_dir_len"])
            return "/".join(parts[-n:] if n else parts)

        def prompt(cmd, cursor=False, error=False, slow=False):
            """The prompt as preview lines: optional blank line, then one or two lines."""
            left = []
            if p["p_os"] == "show":
                left += seg(OS_SYMBOLS[detect_os()], p["p_os_color"])
            if p["p_userhost"] == "always":
                left += seg("you", p["p_userhost_color"]) + seg("@cachyos", p["p_userhost_color"]) if caps \
                    else [("you@cachyos ", col(p["p_userhost_color"]), None, "b")]
            left += seg(DIR_ICONS[p["p_dir_icon"]] + dir_path(), p["p_dir_color"],
                        "b" if p["p_dir_bold"] == "yes" else "")
            if p["p_git"] == "show":
                icon = "\ue0a0 " if p["p_git_icon"] == "show" else ""
                left += seg(icon + "main", p["p_git_color"], word="on ")
            if p["p_gitst"] == "show":
                left += seg("!?" if caps else "[!?]", p["p_gitst_color"])
            symbol = [(p["p_symbol"], col(p["p_err_color"] if error else p["p_ok_color"]), None, ""),
                      (" " + cmd, fg, None, "")]
            if cursor:
                symbol.append(("\0cursor", fg, None, ""))
            right = [("\0right", fg, None, "")]
            if error and p["p_status"] == "show":
                right += seg("✘ 1", p["p_status_color"])
            if slow and p["p_dur"] == "show":
                right += seg("5s", p["p_dur_color"], word="took ")
            if p["p_langs"] == "show":
                right += seg(LANG_SYMBOLS["python"][0] + "v3.14.8", LANG_SYMBOLS["python"][1], "b", word="via ")
            if p["p_time"] == "show":
                if p["p_time_at"] == "show":
                    right.append(("at ", fg, None, ""))
                icon = CLOCK_ICON + " " if p["p_time_icon"] == "show" else ""
                right += seg(icon + time.strftime(p["p_time_format"]), p["p_time_color"])
            if right[-1][0] == " ":
                right.pop()
            lines = [[]] if p["p_newline"] == "yes" else []
            if p["p_lines"] == "2":
                return lines + [left, symbol + right]
            return lines + [left + symbol + right]

        def t(text, color=fg, attrs=""):
            return (text, color, None, attrs)

        return [
            *prompt("ls -la"),
            [t("drwxr-xr-x  "), t("themes/", k[12], "b"), t("   "), t("run.sh", k[10], "b"), t("   "),
             t("kitty-styler.py", k[2]), t("   README.md   "), t("backup.tar.gz", k[1], "b"),
             t("   "), t("link", k[14], "b"), t(" -> "), t("target", k[6])],
            *prompt("git diff --stat"),
            [t("@@ -12,3 +12,4 @@ ", k[6]), t("def apply(self):", fg)],
            [t("-    size = 11", k[1])],
            [t("+    size = 12.5", k[2])],
            [t("+    opacity = 0.85", k[2])],
            *prompt('python -c "import missing"'),
            [t("Traceback (most recent call last):", k[1])],
            [t("ModuleNotFoundError", k[9], "b"), t(": No module named 'missing'")],
            [t("warning:", k[11], "b"), t(" unused variable "), t("`x`", k[13]), t(" at "),
             t("src/main.rs:42", k[4], "u")],
            [t("Normal, "), t("bold", fg, "b"), t(", "), t("italic", fg, "i"), t(", "),
             t("underlined", fg, "u"), t(" and "), (" selected text ", sel[0], sel[1], ""),
             t(" in "), t("dim", k[8]), t(" grey.")],
            [t("normal  ")] + [("   ", fg, k[i], "") for i in range(8)],
            [t("bright  ")] + [("   ", fg, k[i], "") for i in range(8, 16)],
            [t("0123456789 {}[]() <= >= != -> => === ~ | & ** // %")],
            *prompt("", cursor=True, error=True, slow=True),
        ]

    # ---- actions ----
    def on_reset(self, _btn):
        self.load_current()
        self.sync_dropdowns()
        self.status.set_label("Reset to what kitty.conf has now.")

    def on_apply(self, _btn):
        try:
            msg = apply_all(self.settings, self.themes, self.colors, self.file_conf)
        except OSError as e:
            msg = f"Could not save: {e}"
        self.file_conf = parse_conf_file(CONF)
        self.status.set_label(msg)

def same(a, b):
    try:
        return float(a) == float(b)
    except ValueError:
        return a == b


def prune_backups():
    """Delete all but the newest KEEP_BACKUPS kitty.conf.bak-<time> files; other files are left alone."""
    folder, name = os.path.split(CONF)
    pattern = re.compile(re.escape(name) + r"\.bak-\d{8}-\d{6}$")
    for old in sorted(f for f in os.listdir(folder) if pattern.match(f))[:-KEEP_BACKUPS]:
        os.remove(os.path.join(folder, old))


def apply_all(s, themes, colors, file_conf):
    """Write kitty.conf, the theme file and starship.toml from the settings; returns a status message."""
    style, pl = TAB_STYLES[s["tab_bar"]]
    wanted = {"font_family": s["font_family"], "tab_bar_style": style}
    if pl:
        wanted["tab_powerline_style"] = pl
    defaults = {"font_family": "monospace", "tab_bar_style": "fade", "tab_powerline_style": "angled"}
    for _label, key, _choices, default in OPTIONS:
        wanted[key] = s[key]
        defaults[key] = default
    # Only write keys already in kitty.conf, or ones that differ from kitty's default.
    wanted = {k: v for k, v in wanted.items()
              if k in file_conf or not same(v, defaults[k])}

    os.makedirs(KITTY_DIR, exist_ok=True)
    text = read(CONF)
    backup = None
    if text:
        backup = f"{CONF}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(CONF, backup)
        prune_backups()

    out, in_block, done = [], False, set()
    for line in text.splitlines():
        if line.strip().startswith("# BEGIN_KITTY_STYLER"):
            in_block = True
            continue
        if in_block:
            if line.strip() == BLOCK_END:
                in_block = False
            continue
        m = re.match(r"^(\s*)(modify_font\s+\w+|\w+)(\s+)", line)
        key = m and re.sub(r"\s+", " ", m.group(2))
        if key in wanted:
            line = f"{m.group(1)}{key}{m.group(3)}{wanted[key]}"
            done.add(key)
        out.append(line)
    while out and not out[-1].strip():
        out.pop()

    extra = [f"{k} {v}" for k, v in wanted.items() if k not in done]
    if s["theme"] != BASE_LABEL:
        os.makedirs(os.path.dirname(STYLER_THEME), exist_ok=True)
        with open(STYLER_THEME, "w", encoding="utf-8") as f:
            f.write(f"# Theme: {s['theme']}\n# Written by kitty-styler from kitty's theme collection.\n")
            f.write(themes[s["theme"]])
        extra.append(STYLER_INCLUDE)
    if extra:
        out += ([""] if out else []) + [BLOCK_BEGIN, *extra, BLOCK_END]
    write_atomic(CONF, "\n".join(out) + "\n")

    subprocess.run(["pkill", "-USR1", "-x", "kitty"], check=False)
    msg = ["Applied and reloaded open kitty windows."]
    if backup:
        msg.append(f"Backup: {backup}")
    if STARSHIP_AVAILABLE:
        msg.append(apply_prompt(s if s["prompt_on"] else None))
    if NOCTALIA or STARSHIP_BEGIN in read(STARSHIP_CONF).splitlines():
        # Without Noctalia the prompt's colour names already follow kitty's colours; this
        # branch then only cleans up a palette left from before.
        msg.append(apply_starship(s["theme"] if NOCTALIA else BASE_LABEL, colors))
    return "\n".join(m for m in msg if m)


def load_prompt_settings():
    out = {key: default for _l, key, _c, default, _n in PROMPT_KEYS}
    for line in read(STARSHIP_CONF).splitlines():
        if line.startswith(PROMPT_STATE):
            try:
                saved = json.loads(line[len(PROMPT_STATE):])
            except ValueError:
                break
            out.update({k: v for k, v in saved.items() if k in out})
            break
    return out


def toml_str(value):
    return json.dumps(value, ensure_ascii=False)


def prompt_block(p):
    """starship.toml lines for the prompt, laid out like the old Powerlevel10k lean prompt."""
    state = {key: p[key] for _l, key, _c, _d, _n in PROMPT_KEYS}
    caps = SEGMENT_STYLES[p["p_style"]]
    shown = {key: p[key] == "show" for key in ("p_os", "p_git", "p_gitst", "p_status", "p_dur", "p_langs", "p_time")}

    def seg(inner, color, extra="", word=""):
        """Format string for one segment: coloured text, or a segment filled with that colour."""
        if not caps:
            return f"{word}[{inner}]({(extra + ' ' + color).strip() or 'none'}) "
        color = color or "white"
        style = f"bg:{color} fg:{p['p_seg_text']} {extra}".strip()
        left = f"[{caps[0]}](fg:{color})" if caps[0] else ""
        right = f"[{caps[1]}](fg:{color})" if caps[1] else ""
        return f"{left}[ {inner} ]({style}){right} "

    def disabled(key):
        return f"disabled = {str(not shown[key]).lower()}"

    bold = "bold" if p["p_dir_bold"] == "yes" else ""
    user_on = p["p_userhost"] != "never"
    clock = CLOCK_ICON + " " if p["p_time_icon"] == "show" else ""
    at = "at " if p["p_time_at"] == "show" else ""
    langs = "".join(f"${name}" for name in LANG_SYMBOLS) if shown["p_langs"] else ""
    lines = [
        PROMPT_BEGIN,
        "# Managed by Kitty Styler; changes here are overwritten when you press Apply.",
        PROMPT_STATE + json.dumps(state, ensure_ascii=False),
        toml_kv("format", "$os$username$hostname$directory$git_branch$git_status"
                + ("$line_break" if p["p_lines"] == "2" else "") + "$character"),
        toml_kv("right_format", f"$status$cmd_duration$jobs{langs}$time"),
        f"add_newline = {str(p['p_newline'] == 'yes').lower()}",
        "",
        "[os]",
        disabled("p_os"),
        toml_kv("format", seg("$symbol", p["p_os_color"])),
        "[os.symbols]",
        *(toml_kv(name, icon) for name, icon in OS_SYMBOLS.items()),
        "",
        "[username]",
        f"disabled = {str(not user_on).lower()}",
        f"show_always = {str(p['p_userhost'] == 'always').lower()}",
        toml_kv("format", seg("$user", p["p_userhost_color"], "bold")[:-1] if not caps
                else seg("$user", p["p_userhost_color"], "bold")),
        "",
        "[hostname]",
        f"disabled = {str(not user_on).lower()}",
        f"ssh_only = {str(p['p_userhost'] != 'always').lower()}",
        toml_kv("format", seg("@$hostname", p["p_userhost_color"], "bold")),
        "",
        "[directory]",
        toml_kv("format", seg(DIR_ICONS[p["p_dir_icon"]] + "$path$read_only", p["p_dir_color"], bold)),
        f"truncation_length = {int(p['p_dir_len'])}",
        f"truncate_to_repo = {str(p['p_dir_repo'] == 'yes').lower()}",
        'read_only = " \uf023"',
        "",
        "[git_branch]",
        disabled("p_git"),
        toml_kv("format", seg("$symbol$branch", p["p_git_color"], word="on ")),
        toml_kv("symbol", "\ue0a0 " if p["p_git_icon"] == "show" else ""),
        "",
        "[git_status]",
        disabled("p_gitst"),
        # The ( ) group hides the segment, filled ends included, when the tree is clean.
        toml_kv("format", "(" + seg("$all_status$ahead_behind" if caps else "\\[$all_status$ahead_behind\\]",
                                    p["p_gitst_color"]) + ")"),
        "",
        "[character]",
        toml_kv("success_symbol", f"[{p['p_symbol']}]({p['p_ok_color'] or 'none'})"),
        toml_kv("error_symbol", f"[{p['p_symbol']}]({p['p_err_color'] or 'none'})"),
        "",
        "[status]",
        disabled("p_status"),
        toml_kv("format", seg("\u2718 $status", p["p_status_color"])),
        "",
        "[cmd_duration]",
        disabled("p_dur"),
        f"min_time = {int(p['p_dur_min'])}",
        toml_kv("format", seg("$duration", p["p_dur_color"], word="took ")),
        "",
        "[time]",
        disabled("p_time"),
        toml_kv("time_format", p["p_time_format"]),
        toml_kv("format", at + seg(clock + "$time", p["p_time_color"])[:-1]),
    ]
    for name, (symbol, color) in LANG_SYMBOLS.items():
        lines += ["", f"[{name}]", toml_kv("symbol", symbol),
                  toml_kv("format", seg("${symbol}${version}", color, "bold", word="via "))]
    return lines + [PROMPT_END]


def toml_kv(key, value):
    return f"{key} = {toml_str(value)}"


def apply_prompt(p):
    """Write the prompt block near the top of starship.toml, or remove it when p is None.

    Returns a status line. Nothing is written when the result would not be valid TOML,
    for example when the user's own config already sets format or [directory].
    """
    text = read(STARSHIP_CONF)
    kept, state, in_block = [], None, False
    for line in text.splitlines():
        if line == PROMPT_BEGIN:
            in_block = True
            # Drop the blank line that was added in front of the block.
            if kept and not kept[-1].strip():
                kept.pop()
        elif in_block:
            in_block = line != PROMPT_END
            if line.startswith(PROMPT_STATE):
                state = line
        elif line.startswith(PROMPT_STATE):
            # Left behind when styling was turned off; the new block carries its own.
            pass
        else:
            kept.append(line)
    # Top-level keys like format must come before the first [table], so go right after
    # the palette line (Noctalia keeps that one at the very top).
    at = next((i + 1 for i, ln in enumerate(kept) if re.match(r"^\s*palette\s*=", ln)), 0)
    if p is None:
        if PROMPT_BEGIN not in text.splitlines():
            return ""
        # Keep the settings as a comment, so turning styling back on restores them.
        new = kept[:at] + ([state] if state else []) + kept[at:]
        done = "Prompt block removed; starship uses your own config again."
    else:
        new = kept[:at] + [""] + prompt_block(p) + kept[at:]
        done = "Prompt saved; it changes from the next prompt in each shell."
    new = "\n".join(new) + "\n"
    try:
        tomllib.loads(new)
    except tomllib.TOMLDecodeError as e:
        return (f"Prompt not saved: starship.toml would not be valid ({e}). Your own config probably sets "
                "the same thing; remove it there, or turn prompt styling off.")
    if text and not os.path.exists(f"{STARSHIP_CONF}.bak-kitty-styler"):
        shutil.copy2(STARSHIP_CONF, f"{STARSHIP_CONF}.bak-kitty-styler")
    write_atomic(STARSHIP_CONF, new)
    return done


def write_atomic(path, text):
    # Write through symlinks (dotfile managers), so the link itself stays.
    path = os.path.realpath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.kitty-styler-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    if os.path.exists(path):
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def set_noctalia_starship(enable, dry_run=False):
    """Add or remove "starship" in Noctalia's [theme.templates] builtin_ids. Returns True if it (would have) changed."""
    text = read(NOCTALIA_SETTINGS)
    sec = re.search(r"^\s*\[theme\.templates\]\s*$", text, re.M)
    if not sec:
        return False
    arr = re.compile(r"^(\s*)builtin_ids\s*=\s*\[(.*?)\]", re.M | re.S).search(text, sec.end())
    if not arr:
        return False
    body = arr.group(2)
    present = re.search(r'"starship"', body) is not None
    if present == enable:
        return False
    if enable:
        # Keep Noctalia's alphabetical order: insert before the first id that sorts after "starship".
        after = next((m for m in re.finditer(r'\n[ \t]*"([^"]+)"', body) if m.group(1) > "starship"), None)
        item = f'\n{arr.group(1)}    "starship"'
        if after:
            body = body[:after.start()] + item + "," + body[after.start():]
        else:
            last = body.rstrip()
            sep = "," if last.strip() and not last.endswith(",") else ""
            body = last + sep + item + body[len(last):]
    else:
        body = re.sub(r'\n?[ \t]*"starship"\s*,?', "", body, count=1)
    new = text[:arr.start(2)] + body + text[arr.end(2):]
    try:
        tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        return False
    if dry_run:
        return True
    backup = f"{NOCTALIA_SETTINGS}.bak-kitty-styler"
    if not os.path.exists(backup):
        shutil.copy2(NOCTALIA_SETTINGS, backup)
    write_atomic(NOCTALIA_SETTINGS, new)
    return True


def starship_palette(theme_colors, starship_text):
    """Same colour names as the Noctalia palette, each filled from the matching slot of the kitty theme."""
    noctalia = parse_conf(read(NOCTALIA_THEME))
    slots = [f"color{i}" for i in range(16)] + ["foreground", "background"]
    m = re.search(r"^\[palettes\.noctalia\]\s*$(.*?)(?=^\[|^# <<<|\Z)", starship_text, re.M | re.S)
    names = re.findall(r'^\s*(\w+)\s*=\s*"(#[0-9a-fA-F]{6})"', m.group(1), re.M) if m else []
    out = {}
    for name, value in names:
        slot = next((s for s in slots if noctalia.get(s, "").lower() == value.lower()), "foreground")
        out[name] = theme_colors[slot]
    for name, slot in BASIC_PALETTE.items():
        out.setdefault(name, theme_colors[slot])
    return out


def apply_starship(theme, colors):
    """Point starship at a palette built from the kitty theme, or hand it back to Noctalia."""
    text = read(STARSHIP_CONF)
    if not text:
        return ""
    if not os.path.exists(f"{STARSHIP_CONF}.bak-kitty-styler"):
        shutil.copy2(STARSHIP_CONF, f"{STARSHIP_CONF}.bak-kitty-styler")
    kept, ours, in_block = [], [], False
    for line in text.splitlines():
        if line == STARSHIP_BEGIN:
            in_block = True
        elif in_block:
            if line == STARSHIP_END:
                in_block = False
            else:
                ours.append(line)
        else:
            kept.append(line)
    turned_off = STARSHIP_FLAG in ours
    prev = next((ln[len(STARSHIP_PREV):] for ln in ours if ln.startswith(STARSHIP_PREV)), None)
    while kept and not kept[-1].strip():
        kept.pop()

    if theme == BASE_LABEL:
        if not ours:
            return ""
        kept = [ln for ln in kept if ln.strip() != STARSHIP_PALETTE_LINE]
        if prev and not turned_off:
            kept.insert(0, prev)
        new = "\n".join(kept) + "\n"
        if not valid_toml(new):
            return "Starship palette not changed: starship.toml would not be valid TOML."
        write_atomic(STARSHIP_CONF, new)
        if turned_off:
            # Noctalia's apply hook puts its own palette line and colours back.
            set_noctalia_starship(True)
        return "Starship is back on Noctalia's colours." if NOCTALIA else "Starship palette put back."

    palette = starship_palette(colors, text)
    palette_re = re.compile(r"^\s*palette\s*=")
    if prev is None:
        prev = next((ln for ln in kept if palette_re.match(ln)), "")
    kept = [ln for ln in kept if not palette_re.match(ln)]
    at = next((i + 1 for i, ln in enumerate(kept) if ln.startswith('"$schema"')), 0)
    kept.insert(at, STARSHIP_PALETTE_LINE)
    # Without this, Noctalia's hook would switch starship back on the next wallpaper change.
    turn_off = set_noctalia_starship(False, dry_run=True)
    turned_off = turned_off or turn_off
    block = [STARSHIP_BEGIN, "# Written by kitty-styler; pick Noctalia in Kitty Styler to undo."]
    if turned_off:
        block.append(STARSHIP_FLAG)
    if prev:
        block.append(STARSHIP_PREV + prev)
    block.append("[palettes.kitty-styler]")
    block += [f'{name} = "{value}"' for name, value in palette.items()]
    block.append(STARSHIP_END)
    new = "\n".join(kept + [""] + block) + "\n"
    if not valid_toml(new):
        return "Starship palette not changed: starship.toml would not be valid TOML."
    write_atomic(STARSHIP_CONF, new)
    if turn_off:
        # Its undo hook only drops Noctalia's own palette line and block, so ours stays.
        set_noctalia_starship(False)
    return "Starship prompt now uses the theme's colours."


def valid_toml(text):
    try:
        tomllib.loads(text)
        return True
    except tomllib.TOMLDecodeError:
        return False


class StylerApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        (self.props.active_window or StylerWindow(self)).present()


if __name__ == "__main__":
    sys.exit(StylerApp().run(sys.argv))
