#!/usr/bin/env python3
"""Shellac: style your terminal (kitty, Alacritty, foot, Ghostty) and shell prompt with a live preview."""
import base64
import glob
import json
import math
import os
import platform
import pwd
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import urllib.request
import warnings
import zipfile
import zlib

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
warnings.filterwarnings("ignore", ".*cairo_set_source_pixbuf", DeprecationWarning)
from gi.repository import Gdk, GdkPixbuf, GLib, Graphene, Gsk, Gtk, Pango, PangoCairo

# updater.py sits next to this file (the command in ~/.local/bin is a symlink). A copy of
# shellac.py on its own still runs, just without the Updates section.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
try:
    import updater
except ImportError:
    updater = None

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
BLOCK_BEGIN = "# BEGIN_KITTY_STYLER (managed by kitty-styler; keep this block last so it wins)"
BLOCK_END = "# END_KITTY_STYLER"
# Noctalia's kitty template keeps a wallpaper-coloured theme here; without it, the first
# theme entry keeps whatever colours kitty.conf already has.
NOCTALIA = os.path.exists(NOCTALIA_THEME)
BASE_LABEL = "Noctalia (follows wallpaper)" if NOCTALIA else "Keep current theme"
APP_ID = "io.github.soulmanyes.Shellac"
VERSION = "1.4.0"
REPO = "Soulmanyes/shellac"
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
UPDATE_STATE = os.path.join(CONFIG_HOME, "shellac", "updates.json")

KITTY_COLORS = {
    "foreground": "#dddddd", "background": "#000000",
    "cursor": "#cccccc", "cursor_text_color": None,
    "selection_foreground": "#000000", "selection_background": "#fffacd",
    "active_tab_foreground": "#000000", "active_tab_background": "#eeeeee",
    "inactive_tab_foreground": "#444444", "inactive_tab_background": "#999999",
    "tab_bar_background": None,
    "active_border_color": "#00ff00", "inactive_border_color": "#cccccc",
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


def kitty_shaders():
    """Names of the shader pipelines this kitty ships (kitty 0.49+), plus the user's own."""
    dirs = [os.path.join(KITTY_DIR, "shaders")]
    exe = shutil.which("kitty")
    if exe:
        # Distro packages and kitty.app both keep the Python package in ../lib/kitty/kitty.
        dirs.append(os.path.join(os.path.dirname(os.path.realpath(exe)), "..", "lib", "kitty", "kitty",
                                 "shaders", "custom"))
    return {os.path.basename(p)[:-len(".pipeline")] for d in dirs for p in glob.glob(os.path.join(d, "*.pipeline"))}


# Trail looks and effects are names in kitty's custom_shaders list. Apply swaps only the names
# picked here and keeps any others. The trail looks still need cursor_trail above 0.
TRAIL_STYLES = {"plain": None, "blaze": "cursor-trail-blaze", "lightning": "cursor-trail-lightning",
                "motion blur": "cursor-trail-motion-blur"}
SHADERS = kitty_shaders()
TRAIL_STYLES = {k: v for k, v in TRAIL_STYLES.items() if v is None or v in SHADERS}
TRAIL_SHADERS = set(TRAIL_STYLES.values()) | {"cursor-trail-default"}
EFFECTS = {"none": None, **{n: n for n in sorted(SHADERS - TRAIL_SHADERS)}}
# Extra programs some options need. Installed with the distro's package manager where it has them
# (Arch-based: pacman, behind a password prompt), else downloaded from the project's GitHub release
# into ~/.local/share/kitty-styler/extras and linked into ~/.local/bin, which needs no password.
HOME_BIN = os.path.expanduser("~/.local/bin")
EXTRAS_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
                          "kitty-styler", "extras")
EXTRAS = {
    "slangc": {"what": "kitty's shader compiler, for trail styles and effects", "pacman": "shader-slang",
               "repo": "shader-slang/slang", "asset": "slang-{version}-linux-{arch}-glibc-2.28.tar.gz",
               "bin": "bin/slangc"},
    "starship": {"what": "the prompt engine, for prompt styling", "pacman": "starship",
                 "repo": "starship/starship", "asset": "starship-{arch}-unknown-linux-musl.tar.gz",
                 "bin": "starship"},
}


def find_extra(name):
    """Path of an installed extra, or None."""
    if name == "slangc":
        # kitty's own binary bundle ships slangc next to kitty.
        exe = shutil.which("kitty")
        bundled = exe and os.path.join(os.path.dirname(os.path.realpath(exe)), "slangc")
        if bundled and os.access(bundled, os.X_OK):
            return bundled
    return shutil.which(name)


def install_extra(name, gui=True):
    """Install an extra; returns (ok, message). gui asks for the password with pkexec, else sudo."""
    info = EXTRAS[name]
    pkg = info["pacman"]
    asker = shutil.which("pkexec") if gui else shutil.which("sudo")
    if asker and shutil.which("pacman") and subprocess.run(["pacman", "-Si", pkg], capture_output=True,
                                                            check=False).returncode == 0:
        r = subprocess.run([asker, "pacman", "-S", "--needed", "--noconfirm", pkg], check=False,
                           capture_output=gui, text=True)
        if r.returncode == 0:
            return True, f"Installed {name} (package {pkg})."
        if gui and r.returncode == 126:
            return False, f"{name} not installed: the password prompt was closed."
        # Only fall back to the download when no password prompt could be shown (no polkit agent);
        # after other pacman errors a second, untracked copy in ~/.local/bin would only confuse.
        if not (gui and r.returncode == 127):
            return False, f"Could not install {name}: pacman failed. Try: sudo pacman -S {pkg}"
    try:
        return True, download_extra(name)
    except Exception as e:  # noqa: BLE001 - network, archive and file errors all end up as a message
        return False, f"Could not install {name}: {e}"


def download_extra(name):
    info = EXTRAS[name]
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(platform.machine())
    if not arch:
        raise ValueError(f"no download for this CPU ({platform.machine()})")
    api = f"https://api.github.com/repos/{info['repo']}/releases/latest"
    with urllib.request.urlopen(api, timeout=30) as r:
        release = json.load(r)
    version = release["tag_name"].lstrip("v")
    asset = info["asset"].format(version=version, arch=arch)
    url = next((a["browser_download_url"] for a in release["assets"] if a["name"] == asset), None)
    if not url:
        raise ValueError(f"the {release['tag_name']} release has no {asset}")
    os.makedirs(EXTRAS_DIR, exist_ok=True)
    dest = os.path.join(EXTRAS_DIR, name)
    with tempfile.TemporaryDirectory(dir=EXTRAS_DIR) as tmp:
        archive = os.path.join(tmp, asset)
        urllib.request.urlretrieve(url, archive)
        unpacked = os.path.join(tmp, "unpacked")
        with tarfile.open(archive) as tar:
            try:
                tar.extractall(unpacked, filter="data")
            except TypeError:
                # Python before 3.11.4 has no extraction filters: refuse paths leaving the folder.
                for m in tar.getmembers():
                    for target in (m.name, os.path.join(os.path.dirname(m.name), m.linkname) if m.issym() else ""):
                        if target and (os.path.isabs(target) or ".." in os.path.normpath(target).split(os.sep)):
                            raise ValueError(f"unsafe path in {asset}: {m.name}") from None
                tar.extractall(unpacked)
        if not os.access(os.path.join(unpacked, info["bin"]), os.X_OK):
            raise ValueError(f"{info['bin']} missing from {asset}")
        shutil.rmtree(dest, ignore_errors=True)
        os.rename(unpacked, dest)
    os.makedirs(HOME_BIN, exist_ok=True)
    link = os.path.join(HOME_BIN, name)
    if os.path.lexists(link) and not os.path.islink(link):
        return f"Downloaded {name} {version} to {dest}, but left the existing {link} alone."
    if os.path.islink(link):
        os.remove(link)
    os.symlink(os.path.join(dest, info["bin"]), link)
    msg = f"Installed {name} {version} into {HOME_BIN}."
    if HOME_BIN not in os.environ.get("PATH", "").split(os.pathsep):
        msg += f" {HOME_BIN} is not on your PATH; add it so kitty and your shell find {name}."
    return msg


SLANGC = find_extra("slangc")
STARSHIP_AVAILABLE = os.path.exists(STARSHIP_CONF) or find_extra("starship") is not None
SLANG_NOTE = "Trail styles and effects need kitty's shader compiler, slangc."


def shader_pick(names, table):
    """Index and setting of the first name in a custom_shaders list that table offers, else (None, None)."""
    rev = {v: k for k, v in table.items() if v}
    return next(((i, rev[n]) for i, n in enumerate(names) if n in rev), (None, None))


FONT_CHOICES = None  # filled in with the installed monospace fonts
OFF_SCROLLBAR = "never"

# Headings, then (label, kitty key, choices, kitty default). Choices are the strings written to kitty.conf.
OPTION_GROUPS = [
    ("Text", [
        ("Font size", "font_size", [f"{s:.1f}" for s in [8, 9, 10, 10.5, 11, 11.5, 12, 12.5, 13, 14, 15, 16, 18, 20, 22, 24]], "11.0"),
        ("Bold font", "bold_font", FONT_CHOICES, "auto"),
        ("Italic font", "italic_font", FONT_CHOICES, "auto"),
        ("Bold italic font", "bold_italic_font", FONT_CHOICES, "auto"),
        ("Ligatures", "disable_ligatures", ["never", "cursor", "always"], "never"),
        ("Line height", "modify_font cell_height", ["90%", "100%", "105%", "110%", "115%", "120%", "130%", "140%"], "100%"),
        ("Letter spacing", "modify_font cell_width", ["90%", "95%", "100%", "105%", "110%", "120%"], "100%"),
    ]),
    ("Window", [
        ("Background opacity", "background_opacity", [f"{i / 100:.2f}" for i in range(30, 101, 5)], "1.00"),
        ("Window padding", "window_padding_width", ["0", "2", "4", "6", "8", "10", "12", "15", "20", "25", "30", "40"], "0"),
        ("Window margin", "window_margin_width", ["0", "2", "4", "6", "8", "10", "15", "20"], "0"),
        ("Border width", "window_border_width", ["0.5pt", "1pt", "1.5pt", "2pt", "3pt", "4pt"], "0.5pt"),
        ("Borders", "draw_minimal_borders", ["yes", "no"], "yes"),
        ("Border corners", "window_border_radius", ["0", "4", "8", "12", "16"], "0"),
        ("Inactive window text", "inactive_text_alpha", ["1.0", "0.9", "0.8", "0.7", "0.6", "0.5", "0.4"], "1.0"),
        ("Title bar", "hide_window_decorations", ["no", "titlebar-only", "yes"], "no"),
    ]),
    ("Cursor", [
        ("Cursor shape", "cursor_shape", ["block", "beam", "underline"], "block"),
        ("When unfocused", "cursor_shape_unfocused", ["hollow", "block", "beam", "underline", "unchanged"], "hollow"),
        ("Beam thickness", "cursor_beam_thickness", ["1.0", "1.5", "2.0", "2.5", "3.0", "4.0"], "1.5"),
        ("Underline thickness", "cursor_underline_thickness", ["1.0", "2.0", "3.0", "4.0"], "2.0"),
        ("Cursor blink", "cursor_blink_interval", ["-1", "0", "0.3", "0.5", "0.8", "1.2"], "-1"),
        ("Stop blinking after", "cursor_stop_blinking_after", ["0", "5", "15", "30", "60"], "15.0"),
        ("Cursor trail", "cursor_trail", ["0", "1", "3", "10", "30"], "0"),
        ("Trail speed", "cursor_trail_decay", ["0.05 0.2", "0.1 0.4", "0.2 0.6", "0.3 1.0"], "0.1 0.4"),
        ("Trail start distance", "cursor_trail_start_threshold", ["0", "1", "2", "3", "5", "10"], "2"),
        ("Trail colour", "cursor_trail_color", ["none", "white", "red", "orange", "gold", "limegreen", "cyan",
                                                "dodgerblue", "violet", "hotpink"], "none"),
    ]),
    ("Tab bar", [
        ("Tab bar position", "tab_bar_edge", ["bottom", "top"], "bottom"),
        ("Tab bar alignment", "tab_bar_align", ["left", "center", "right"], "left"),
        ("Show tab bar from", "tab_bar_min_tabs", ["1", "2", "3"], "2"),
        ("Side margins", "tab_bar_margin_width", ["0.0", "2.0", "4.0", "6.0", "8.0", "10.0"], "0.0"),
        ("Top/bottom margins", "tab_bar_margin_height", ["0.0 0.0", "2.0 2.0", "4.0 4.0", "6.0 6.0",
                                                         "0.0 4.0", "4.0 0.0"], "0.0 0.0"),
        ("Active tab text", "active_tab_font_style", ["normal", "bold", "italic", "bold-italic"], "bold-italic"),
        ("Inactive tab text", "inactive_tab_font_style", ["normal", "bold", "italic", "bold-italic"], "normal"),
    ]),
    ("Scrollbar", [
        ("Show scrollbar", "scrollbar", ["scrolled", "always", "never", "hovered", "scrolled-or-hovered",
                                         "scrolled-and-hovered"], "scrolled"),
        ("Scrollbar width", "scrollbar_width", ["0.25", "0.5", "0.75", "1.0"], "0.5"),
        ("Handle opacity", "scrollbar_handle_opacity", ["0.2", "0.3", "0.5", "0.7", "0.9", "1.0"], "0.5"),
        ("Handle corners", "scrollbar_radius", ["0.0", "0.1", "0.2", "0.3", "0.5"], "0.3"),
        ("Handle colour", "scrollbar_handle_color", ["foreground", "selection_background", "white", "cyan",
                                                     "dodgerblue", "violet", "hotpink", "orange"], "foreground"),
        ("Track opacity", "scrollbar_track_opacity", ["0", "0.1", "0.2", "0.3", "0.5"], "0"),
    ]),
]
OPTIONS = [o for _heading, opts in OPTION_GROUPS for o in opts]
# Rows that only matter while another setting is on: key -> (value that turns it off, rows to hide).
DEPENDS = {"cursor_trail": ("0", ("trail_style", "cursor_trail_decay", "cursor_trail_start_threshold",
                                  "cursor_trail_color")),
           "scrollbar": (OFF_SCROLLBAR, ("scrollbar_width", "scrollbar_handle_opacity", "scrollbar_radius",
                                         "scrollbar_handle_color", "scrollbar_track_opacity"))}
DEPENDENT = {k: parent for parent, (_off, keys) in DEPENDS.items() for k in keys}
# Tiling compositors draw no title bar for kitty, so the preview doesn't either.
TILING = next((name for env, name in (("HYPRLAND_INSTANCE_SIGNATURE", "Hyprland"), ("SWAYSOCK", "Sway"),
                                      ("NIRI_SOCKET", "niri")) if os.environ.get(env)), None)
# Notes shown under a row.
ROW_NOTES = {
    "trail_style": "The preview draws look-alikes of these styles; Try in kitty shows the real ones.",
    "scrollbar": "The preview acts as if it is scrolled back. For the mouse choices, hover over the preview.",
    "hide_window_decorations": (f"{TILING} draws no title bar for kitty, so this only matters on other desktops."
                                if TILING else "Title bar changes show in new kitty windows."),
    "effect": ("Effects are not drawn in the preview; Try in kitty shows them. Animated effects (matrix, fireworks, northern lights,"
               " spotlight) redraw all the time and use more power."),
}
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
# Shell (zsh). Shellac owns two blocks in .zshrc: the plugin list just before the line that
# loads oh-my-zsh, and everything else at the end, after zsh-syntax-highlighting has loaded.
ZSHRC = os.path.expanduser(os.environ.get("KITTY_STYLER_ZSHRC",
                                          os.path.join(os.environ.get("ZDOTDIR", "~"), ".zshrc")))
ZSH_AVAILABLE = shutil.which("zsh") is not None and os.path.exists(ZSHRC)
ZSH_PLUGINS_BEGIN = "# >>> KITTY STYLER ZSH PLUGINS >>>"
ZSH_PLUGINS_END = "# <<< KITTY STYLER ZSH PLUGINS <<<"
ZSH_BEGIN = "# >>> KITTY STYLER ZSH >>>"
ZSH_END = "# <<< KITTY STYLER ZSH <<<"
ZSH_STATE = "# kitty-styler shell settings: "
# Choice that writes nothing, so the shell keeps what its own config sets.
KEEP = "keep"
STYLE_ATTRS = {"precommand": "u"}
ZSH_COLORS = {KEEP: "Leave as is", **PROMPT_COLORS, "black": "Black"}
ON_OFF = {KEEP: "Leave as is", "on": "On", "off": "Off"}
# Colours while typing: (label, settings key, zsh-syntax-highlighting styles, the plugin's own
# default as (colour, attrs)). None styles is the suggestion. Bold/underline stay when a colour is
# picked: the row's attrs, plus STYLE_ATTRS for styles that differ from the rest of their row.
ZSH_HL = [
    ("Commands", "z_cmd", ["arg0", "precommand"], ("green", "")),
    ("Unknown commands", "z_unknown", ["unknown-token"], ("red", "b")),
    ("Quoted text", "z_string", ["single-quoted-argument", "double-quoted-argument",
                                 "dollar-quoted-argument"], ("yellow", "")),
    ("Paths", "z_path", ["path"], ("", "u")),
    ("Options (-x, --long)", "z_option", ["single-hyphen-option", "double-hyphen-option"], ("", "")),
    ("Suggestion (ghost text)", "z_suggest", None, ("bright-black", "")),
]
# (label, settings key, choices, display names, what it needs: hl, suggest, colours or None).
ZSH_OPTIONS = [("Colours while typing", None, None, None, "colours")]
ZSH_OPTIONS += [(label, key, list(ZSH_COLORS), ZSH_COLORS, "suggest" if styles is None else "hl")
                for label, key, styles, _d in ZSH_HL]
ZSH_OPTIONS += [
    ("Behaviour", None, None, None, None),
    ("Completion matching", "z_case", [KEEP, "insensitive", "sensitive"],
     {KEEP: "Leave as is", "insensitive": "Ignore upper/lower case", "sensitive": "Exact case"}, None),
    ("Typo correction", "z_correct", [KEEP, "off", "commands", "all"],
     {KEEP: "Leave as is", "off": "Off", "commands": "Command names only", "all": "Commands and arguments"}, None),
    ("Type a folder name to cd", "z_autocd", list(ON_OFF), ON_OFF, None),
    ("History size", "z_history", [KEEP, "10000", "50000", "100000", "1000000"],
     {KEEP: "Leave as is", "10000": "10 000 commands", "50000": "50 000 commands",
      "100000": "100 000 commands", "1000000": "1 000 000 commands"}, None),
    ("Share history between shells", "z_share", list(ON_OFF), ON_OFF, None),
    ("Suggestions come from", "z_suggest_src", [KEEP, "history", "history completion"],
     {KEEP: "Leave as is", "history": "History", "history completion": "History, then completion"}, "suggest"),
    ("Terminal beep", "z_beep", list(ON_OFF), ON_OFF, None),
]
ZSH_KEYS = [o for o in ZSH_OPTIONS if o[1]]
TOP_HEADINGS = ("Prompt", "Shell (zsh)", *(heading for heading, _opts in OPTION_GROUPS))
# Commands the preview shows as not found.
PREVIEW_UNKNOWN = {"sl", "gti"}
# Text rows of the inactive window split off below the preview's main one.
INACTIVE_ROWS = 5
CHOICE_NOTES = {"cursor_blink_interval": {"-1": "-1 (system default)", "0": "0 (no blink)"},
                "cursor_trail": {"0": "0 (off)"},
                "cursor_trail_decay": {"0.05 0.2": "Fast", "0.1 0.4": "Normal", "0.2 0.6": "Slow",
                                       "0.3 1.0": "Slower"},
                "cursor_trail_start_threshold": {"0": "Any move", "1": "1 cell", "2": "2 cells",
                                                 "3": "3 cells", "5": "5 cells", "10": "10 cells"},
                "cursor_trail_color": {"none": "Cursor colour"},
                "trail_style": {"plain": "Plain (kitty's own)", "blaze": "Blaze", "lightning": "Lightning",
                                "motion blur": "Motion blur"},
                "effect": {k: "None" if v is None else k.replace("-", " ").capitalize() for k, v in EFFECTS.items()},
                "bold_font": {"auto": "Auto (from the font)"},
                "italic_font": {"auto": "Auto (from the font)"},
                "bold_italic_font": {"auto": "Auto (from the font)"},
                "disable_ligatures": {"never": "On", "cursor": "Off under the cursor", "always": "Off"},
                "window_margin_width": {"0": "0 (none)"},
                "draw_minimal_borders": {"yes": "Only between windows", "no": "Around every window"},
                "window_border_radius": {"0": "Square", "4": "4 pt", "8": "8 pt", "12": "12 pt", "16": "16 pt"},
                "inactive_text_alpha": {"1.0": "1.0 (not faded)"},
                "hide_window_decorations": {"no": "Show", "titlebar-only": "Hide title bar",
                                            "yes": "Hide title bar and borders"},
                "cursor_shape_unfocused": {"hollow": "Hollow block", "unchanged": "Same as focused"},
                "cursor_stop_blinking_after": {"0": "Never", "5": "5 s", "15": "15 s", "30": "30 s", "60": "60 s"},
                "tab_bar_min_tabs": {"1": "1 tab (always)", "2": "2 tabs", "3": "3 tabs"},
                "tab_bar_margin_height": {"0.0 0.0": "None", "2.0 2.0": "2 pt both", "4.0 4.0": "4 pt both",
                                          "6.0 6.0": "6 pt both", "0.0 4.0": "4 pt next to the text",
                                          "4.0 0.0": "4 pt at the window edge"},
                "scrollbar": {"scrolled": "While scrolled back", "always": "Always", "never": "Never",
                              "hovered": "When the mouse is near", "scrolled-or-hovered": "Scrolled back or mouse near",
                              "scrolled-and-hovered": "Scrolled back and mouse near"},
                "scrollbar_width": {"0.25": "Quarter cell", "0.5": "Half cell", "0.75": "Three quarters",
                                    "1.0": "One cell"},
                "scrollbar_handle_color": {"foreground": "Text colour", "selection_background": "Selection colour"},
                "scrollbar_track_opacity": {"0": "0 (hidden)"}}
CHOICE_NOTES.update({key: names for _l, key, _c, _d, names in PROMPT_KEYS if names})
CHOICE_NOTES.update({key: names for _l, key, _c, names, _n in ZSH_KEYS})
CHOICE_NOTES["prompt_mode"] = {}
CSS = """
.plugin-card { background: alpha(@theme_fg_color, 0.07); border: 1px solid alpha(@theme_fg_color, 0.3);
               border-radius: 8px; padding: 8px 10px; margin: 4px 8px; }
.plugin-card:disabled { opacity: 0.6; }
.update-banner { background: alpha(@accent_bg_color, 0.25); border-bottom: 1px solid alpha(@theme_fg_color, 0.3); }
"""


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


def label_factory(ellipsize):
    """List item factory for a string DropDown. ellipsize: fixed narrow natural width, so the
    widget stays the same size whatever text it shows; hexpand still lets it fill the row."""
    factory = Gtk.SignalListItemFactory()

    def setup(_f, item):
        label = Gtk.Label(xalign=0)
        if ellipsize:
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_width_chars(8)
            label.set_max_width_chars(8)
            label.set_hexpand(True)
        item.set_child(label)

    def bind(_f, item):
        text = item.get_item().get_string()
        item.get_child().set_label(text)
        if ellipsize:
            item.get_child().set_tooltip_text(text)

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    return factory


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
        out = subprocess.run(["fc-list", ":spacing=mono", "family"], capture_output=True, text=True,
                             check=False).stdout
    except OSError:
        out = ""
    fams = {line.split(",")[0].strip() for line in out.splitlines() if line.strip()}
    return ["monospace"] + sorted(fams, key=str.lower)


def font_names():
    """Every name kitty can find an installed font by, lowercased: family, family + style,
    full name and PostScript name (e.g. "JetBrainsMono Nerd Font ExtraBold")."""
    try:
        out = subprocess.run(["fc-list", ":", "family", "style", "fullname", "postscriptname"],
                             capture_output=True, text=True, check=False).stdout
    except OSError:
        out = ""
    names = set()
    for line in out.splitlines():
        fields = dict(part.split("=", 1) for part in line.split(":")[1:] if "=" in part)
        families = [f.strip() for f in line.split(":")[0].split(",") if f.strip()]
        styles = [st.strip() for st in fields.get("style", "").split(",") if st.strip()]
        names.update(families)
        names.update(f"{f} {st}" for f in families for st in styles)
        names.update(v.strip() for k, v in fields.items() if k in ("fullname", "postscriptname"))
    return {n.lower() for n in names}


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


def pts(value, i=0):
    """Number i of a kitty size like "4", "2.0 6" or "3px" in pixels (pt at 96 dpi); 0 when unreadable."""
    try:
        parts = value.split()
        part = parts[i] if i < len(parts) else parts[-1]
        if part.endswith("px"):
            return float(part[:-2])
        return float(part.removesuffix("pt")) * 96 / 72
    except (ValueError, IndexError):
        return 0.0


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def color_rgb(value, colors):
    """A kitty colour setting (hex, a colour name, foreground or selection_background) as RGB floats."""
    value = colors.get(value) if value in ("foreground", "selection_background") else value
    if value.startswith("#"):
        return hex_rgb(value)
    rgba = Gdk.RGBA()
    return (rgba.red, rgba.green, rgba.blue) if rgba.parse(value) else hex_rgb(colors["foreground"])


def rounded_rect(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


# ---- The shell kitty runs, its prompt, and switching it to starship ----
# Each shell's config file; the env variables point tests at scratch copies.
SHELL_RC = {
    "zsh": ZSHRC,
    "fish": os.path.expanduser(os.environ.get("KITTY_STYLER_FISHRC", os.path.join(
        os.environ.get("XDG_CONFIG_HOME", "~/.config"), "fish", "config.fish"))),
    "bash": os.path.expanduser(os.environ.get("KITTY_STYLER_BASHRC", "~/.bashrc")),
}
SHELL_RC_NAMES = {"zsh": ".zshrc", "fish": "config.fish", "bash": ".bashrc"}
NOT_FOUND = {"zsh": "zsh: command not found: sl", "fish": "fish: Unknown command: sl",
             "bash": "bash: sl: command not found"}
STARSHIP_BIN = find_extra("starship")
# Lines Shellac adds to the shell config so starship takes over the prompt. They are skipped
# when KITTY_STYLER_BASE_PROMPT is set, which is how the preview draws the shell's own prompt.
SWITCH_TOP_BEGIN = "# >>> KITTY STYLER PROMPT SETUP >>>"
SWITCH_TOP_END = "# <<< KITTY STYLER PROMPT SETUP <<<"
SWITCH_BEGIN = "# >>> KITTY STYLER PROMPT SWITCH >>>"
SWITCH_END = "# <<< KITTY STYLER PROMPT SWITCH <<<"
SWITCH_NOTE = "# Managed by Shellac: the prompt comes from starship. Pick your own prompt there to undo."
SWITCH_LINES = {
    # Before anything else, so Powerlevel10k's instant prompt (CachyOS loads it first thing) stays off.
    "zsh_top": ["if [[ -z $KITTY_STYLER_BASE_PROMPT ]]; then",
                "  POWERLEVEL9K_INSTANT_PROMPT=off",
                "  POWERLEVEL9K_DISABLE_CONFIGURATION_WIZARD=true",
                "fi"],
    "zsh": ["if [[ -z $KITTY_STYLER_BASE_PROMPT ]]; then",
            "  (( $+functions[prompt_powerlevel9k_teardown] )) && prompt_powerlevel9k_teardown",
            '  eval "$(starship init zsh)"',
            "fi"],
    "fish": ["if status is-interactive; and not set -q KITTY_STYLER_BASE_PROMPT",
             "    starship init fish | source",
             "end"],
    "bash": ['if [[ -z $KITTY_STYLER_BASE_PROMPT && $- == *i* ]]; then',
             '  eval "$(starship init bash)"',
             "fi"],
}
SYNTAX_CHECK = {"zsh": ["zsh", "-n"], "fish": ["fish", "--no-execute"], "bash": ["bash", "-n"]}
# Prompt choices: the shell's own prompt, starship with the user's starship.toml, or styled here.
PROMPT_MODES = {"base": "Your own prompt", "starship": "Starship, your starship.toml",
                "styled": "Starship, styled here"}
# Renders the prompt for each exit code the preview's sample commands end with. \x1e separates
# the parts: N engine name, then P left prompt, R right prompt per code, E end of each.
PREVIEW_STATUSES = (0, 1, 127)
RENDER_SCRIPTS = {
    "zsh": r"""unset HISTFILE
_ks_render() {
  local f
  for f in $precmd_functions; do (exit $1); $f; done
  print -rn -- $'\x1eP'; print -rnP -- "$PROMPT"; print -rn -- $'\x1eR'; print -rnP -- "$RPROMPT"
  print -rn -- $'\x1eE'
}
if [[ $PROMPT == *starship* ]]; then n=Starship
elif (( ${precmd_functions[(I)_p9k_precmd]} )); then n=Powerlevel10k
elif [[ -n $ZSH_THEME ]]; then n="oh-my-zsh theme $ZSH_THEME"
else n="zsh prompt"; fi
print -rn -- $'\x1eN'"$n"$'\x1eE'
for s in 0 1 127; do _ks_render $s; done""",
    "fish": r"""function _ks_status; return $argv[1]; end
if functions fish_prompt | string match -q '*starship*'; set n Starship
else if set -q tide_left_prompt_items; set n Tide
else; set n "fish prompt"; end
printf '\x1eN%s\x1eE' $n
for s in 0 1 127
    printf '\x1eP'; _ks_status $s; fish_prompt
    printf '\x1eR'; _ks_status $s; functions -q fish_right_prompt; and fish_right_prompt
    printf '\x1eE'
end""",
    "bash": r"""unset HISTFILE
case "$PROMPT_COMMAND" in *starship*) n=Starship ;; *) n="bash prompt" ;; esac
printf '\x1eN%s\x1eE' "$n"
for s in 0 1 127; do
  (exit $s); eval "$PROMPT_COMMAND"
  printf '\x1eP%s\x1eR\x1eE' "${PS1@P}"
done""",
}
RENDER_WIDTH = 200
SHELL_FLAGS = {"zsh": ["-i", "-c"], "fish": ["-i", "-c"], "bash": ["-i", "-c"]}


def kitty_shell(conf):
    """zsh, fish or bash: the shell kitty starts (its shell option, else the login shell); None otherwise."""
    if os.environ.get("KITTY_STYLER_SHELL"):
        return os.environ["KITTY_STYLER_SHELL"]
    value = conf.get("shell", ".").split()
    name = os.path.basename(value[0]) if value and value[0] != "." else ""
    if name not in SHELL_RC:
        try:
            name = os.path.basename(pwd.getpwuid(os.getuid()).pw_shell)
        except KeyError:
            name = os.path.basename(os.environ.get("SHELL", ""))
    return name if name in SHELL_RC and shutil.which(name) else None


def xterm_color(n):
    """Hex for xterm 256-colour index n >= 16."""
    if n >= 232:
        v = 8 + (n - 232) * 10
        return f"#{v:02x}{v:02x}{v:02x}"
    n -= 16
    steps = (0, 95, 135, 175, 215, 255)
    return f"#{steps[n // 36]:02x}{steps[n // 6 % 6]:02x}{steps[n % 6]:02x}"


ANSI_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b\[([0-9;:<=>?]*)([@-~])|\x1b.?|([^\x1b]+)")


def ansi_rows(text):
    """Rows of (char, fg, bg, attrs) cells, from text a terminal would draw.

    Colours are kept as specs: None (default), 0-15 (the theme's colourN) or "#rrggbb", so the
    preview can still follow the theme. Handles the cursor moves prompts use for multi-line layouts.
    """
    rows, r, col = [[]], 0, 0
    fg = bg = None
    attrs = set()

    def put(ch):
        nonlocal col
        row = rows[r]
        while len(row) < col:
            row.append((" ", None, None, ""))
        cell = (ch, fg, bg, "".join(sorted(attrs)))
        if col < len(row):
            row[col] = cell
        else:
            row.append(cell)
        col += 1

    for m in ANSI_RE.finditer(text):
        if m.group(3) is not None:
            for ch in m.group(3):
                if ch == "\n":
                    r, col = r + 1, 0
                    while len(rows) <= r:
                        rows.append([])
                elif ch == "\r":
                    col = 0
                elif ch == "\t":
                    col = (col // 8 + 1) * 8
                elif ch >= " ":
                    put(ch)
            continue
        params, final = m.group(1), m.group(2)
        if final is None:
            continue
        nums = [int(p) if p.isdigit() else 0 for p in re.split("[;:]", params)] if params else [0]
        n = nums[0] or 1
        if final == "A":
            r = max(0, r - n)
        elif final == "B":
            r += n
            while len(rows) <= r:
                rows.append([])
        elif final == "C":
            col += n
        elif final == "D":
            col = max(0, col - n)
        elif final == "G":
            col = n - 1
        elif final == "K" and nums[0] == 0:
            del rows[r][col:]
        elif final == "m" and not params.startswith(("<", ">", "?", "=")):
            i = 0
            while i < len(nums):
                p = nums[i]
                if p == 0:
                    fg = bg = None
                    attrs.clear()
                elif p in (1, 3, 4):
                    attrs.add({1: "b", 3: "i", 4: "u"}[p])
                elif p in (22, 23, 24):
                    attrs.discard({22: "b", 23: "i", 24: "u"}[p])
                elif 30 <= p <= 37 or 90 <= p <= 97:
                    fg = p - 30 if p < 90 else p - 82
                elif 40 <= p <= 47 or 100 <= p <= 107:
                    bg = p - 40 if p < 100 else p - 92
                elif p == 39:
                    fg = None
                elif p == 49:
                    bg = None
                elif p in (38, 48) and i + 1 < len(nums):
                    if nums[i + 1] == 5 and i + 2 < len(nums):
                        c = nums[i + 2]
                        c = c if c < 16 else xterm_color(c)
                        i += 2
                    elif nums[i + 1] == 2 and i + 4 < len(nums):
                        red, green, blue = nums[i + 2:i + 5]
                        c = f"#{red:02x}{green:02x}{blue:02x}"
                        i += 4
                    else:
                        c = None
                    if p == 38:
                        fg = c
                    else:
                        bg = c
                i += 1
    return rows


def cells_runs(cells, strip=False):
    """Cells to (text, fg, bg, attrs) runs; a long run of one plain character (the gap before a
    right-hand part, filled with spaces or a line) becomes a "\\0right" marker carrying that character.
    strip drops trailing blank cells; the left prompt keeps them, as the command follows them."""
    while strip and cells and cells[-1][0] == " " and cells[-1][2] is None:
        cells = cells[:-1]
    runs, i = [], 0
    while i < len(cells):
        j = i
        while j < len(cells) and cells[j] == cells[i]:
            j += 1
        ch, fg, bg, attrs = cells[i]
        if j - i >= 20 and bg is None and i and j < len(cells):
            runs.append(("\0right" + ("" if ch == " " else ch), fg, None, ""))
        elif runs and runs[-1][1:] == (fg, bg, attrs) and not runs[-1][0].startswith("\0"):
            runs[-1] = (runs[-1][0] + ch * (j - i), fg, bg, attrs)
        else:
            runs.append((ch * (j - i), fg, bg, attrs))
        i = j
    return runs


def run_quiet(cmd, env, script=None, timeout=15):
    """Run a command in its own process group, cwd home; stdout, or None on failure.

    The group is killed afterwards, so helpers the shell left running (gitstatusd) go too.
    """
    try:
        proc = subprocess.Popen(cmd + ([script] if script else []), cwd=os.path.expanduser("~"), env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        return None
    try:
        out, _err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        out = None
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
    return out.decode("utf-8", "replace") if out is not None else None


def render_env(**extra):
    env = dict(os.environ)
    for key in ("STARSHIP_SESSION_KEY", "STARSHIP_SHELL", "KITTY_STYLER_BASE_PROMPT"):
        env.pop(key, None)
    env.update(TERM="xterm-256color", COLUMNS=str(RENDER_WIDTH), LINES="50", PWD=os.path.expanduser("~"),
               **extra)
    return env


def render_shell_prompt(shell):
    """The shell's own prompt (without Shellac's starship switch): (engine name,
    {status: (left rows, right runs)}), or None when it could not be read."""
    out = run_quiet([shell, *SHELL_FLAGS[shell]], render_env(KITTY_STYLER_BASE_PROMPT="1"),
                    RENDER_SCRIPTS[shell])
    if not out:
        return None
    m = re.search("\x1eN(.*?)\x1eE", out)
    parts = re.findall("\x1eP(.*?)\x1eR(.*?)\x1eE", out, re.DOTALL)
    if not m or len(parts) != len(PREVIEW_STATUSES):
        return None
    return m.group(1), {s: prompt_parts(left, right) for s, (left, right) in zip(PREVIEW_STATUSES, parts)}


def render_starship(config_text):
    """starship's prompt for this config: ("Starship", {status: (left rows, right runs)}), or None."""
    if not STARSHIP_BIN:
        return None
    fd, path = tempfile.mkstemp(prefix="shellac-", suffix=".toml", dir=os.environ.get("XDG_RUNTIME_DIR"))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(config_text)
        env = render_env(STARSHIP_CONFIG=path)
        out = {}
        for status in PREVIEW_STATUSES:
            args = [STARSHIP_BIN, "prompt", f"--status={status}", f"--terminal-width={RENDER_WIDTH}"]
            if status == 1:
                # The preview's slow command.
                args.append("--cmd-duration=5000")
            left = run_quiet(args, env)
            right = run_quiet(args + ["--right"], env)
            if left is None:
                return None
            out[status] = prompt_parts(left, right or "")
        return "Starship", out
    finally:
        os.remove(path)


def prompt_parts(left, right):
    """Left prompt as rows of runs (the last row is where the command is typed), right prompt as runs."""
    cells = ansi_rows(left)
    rows = [cells_runs(r, strip=i < len(cells) - 1) for i, r in enumerate(cells)]
    right_rows = ansi_rows(right.rstrip("\n"))
    return rows, cells_runs(right_rows[-1], strip=True) if right_rows else []


def strip_switch(lines):
    """Shell config lines without our prompt switch blocks; also says whether they were there."""
    kept, block, found = [], False, False
    for line in lines:
        if line in (SWITCH_TOP_BEGIN, SWITCH_BEGIN):
            block = found = True
            if line == SWITCH_BEGIN and kept and not kept[-1].strip():
                kept.pop()
        elif block:
            block = line not in (SWITCH_TOP_END, SWITCH_END)
        else:
            kept.append(line)
    return kept, found


def own_starship(shell):
    """True when the shell config starts starship itself, without our switch (fish: conf.d too)."""
    kept, _found = strip_switch(read(SHELL_RC[shell]).splitlines())
    if shell == "fish":
        for path in sorted(glob.glob(os.path.join(os.path.dirname(SHELL_RC[shell]), "conf.d", "*.fish"))):
            kept += read(path).splitlines()
    return any(re.search(r"starship\s+init", ln) and not ln.lstrip().startswith("#") for ln in kept)


def prompt_choices():
    """The prompt modes this machine can offer, in dropdown order."""
    if not KITTY_SHELL:
        return ["starship", "styled"] if STARSHIP_AVAILABLE else []
    if own_starship(KITTY_SHELL):
        return ["starship", "styled"]
    return ["base", "starship", "styled"] if STARSHIP_BIN else ["base"]


def strip_prompt_block(text):
    """starship.toml text without our prompt block."""
    out, block = [], False
    for line in text.splitlines():
        if line == PROMPT_BEGIN:
            block = True
        elif block:
            block = line != PROMPT_END
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def apply_switch(shell, on):
    """Add or remove the lines that make the shell use starship; returns a status line."""
    path = SHELL_RC[shell]
    text = read(path)
    kept, found = strip_switch(text.splitlines())
    if on == found:
        # Already as wanted; leave the blocks where they are.
        return ""
    out = list(kept)
    if on:
        if shell == "zsh":
            out = [SWITCH_TOP_BEGIN, SWITCH_NOTE, *SWITCH_LINES["zsh_top"], SWITCH_TOP_END, *out]
        while out and not out[-1].strip():
            out.pop()
        out += ["", SWITCH_BEGIN, SWITCH_NOTE, *SWITCH_LINES[shell], SWITCH_END]
    new = "\n".join(out) + "\n"
    if new == text:
        return ""
    if shell_syntax_ok(shell, text) and not shell_syntax_ok(shell, new):
        return f"Prompt not switched: {SHELL_RC_NAMES[shell]} would not be valid {shell}."
    if text and not os.path.exists(f"{path}.bak-kitty-styler"):
        shutil.copy2(path, f"{path}.bak-kitty-styler")
    write_atomic(path, new)
    if on:
        return f"{shell} now uses starship for its prompt, from the next new shell."
    return f"{shell} uses your own prompt again, from the next new shell."


def shell_syntax_ok(shell, text):
    try:
        return subprocess.run(SYNTAX_CHECK[shell], input=text, capture_output=True, text=True,
                              timeout=10, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class KittyTerminal:
    """The terminal whose config the app edits. Settings use kitty's option names; each terminal
    maps them to its own config."""

    name = "kitty"
    label = "kitty"
    live_reload = True
    conf = CONF
    noctalia = NOCTALIA
    # Splits, tabs, trails and shaders: the preview draws them, Try in kitty can open it.
    kitty_only = True
    neutral = None

    def supports(self, _key):
        return True

    def choices(self, _key, choices):
        return choices

    def theme_note(self):
        return ("Apply saves this theme as themes/kitty-styler.conf and includes it at the end of kitty.conf. "
                f'Pick "{BASE_LABEL}" to go back to your own colours.')

    def read_conf(self, without_theme=False):
        """What the terminal uses now, includes followed; without_theme leaves out our theme file."""
        return parse_conf_file(CONF, skip={os.path.realpath(STYLER_THEME)} if without_theme else ())

    def theme(self):
        """(name, text) of the theme the app wrote, or (None, "") when it wrote none."""
        if STYLER_INCLUDE not in read(CONF).splitlines():
            return None, ""
        text = read(STYLER_THEME)
        m = re.match(r"# Theme: (.+)", text)
        return (m and m.group(1).strip()), (text.split("\n", 2)[2] if text.count("\n") >= 2 else "")

    def noctalia_colors(self):
        return parse_conf(read(NOCTALIA_THEME))

    def shell(self):
        return kitty_shell(parse_conf_file(CONF))

    def write(self, s, themes, file_conf):
        """Write kitty.conf and the theme file; returns the backup path or None."""
        style, pl = TAB_STYLES[s["tab_bar"]]
        wanted = {"font_family": s["font_family"], "tab_bar_style": style}
        if pl:
            wanted["tab_powerline_style"] = pl
        defaults = {"font_family": "monospace", "tab_bar_style": "fade", "tab_powerline_style": "angled"}
        for _label, key, _choices, default in OPTIONS:
            wanted[key] = s[key]
            defaults[key] = default
        # Swap only the effect and trail names the app loaded (the first of each), in place; any other
        # custom_shaders names, a second effect included, stay as they are.
        shaders = file_conf.get("custom_shaders", "").split()
        for table, key in ((EFFECTS, "effect"), (TRAIL_STYLES, "trail_style")):
            new = table.get(s[key])
            i = shader_pick(shaders, table)[0]
            if i is None:
                shaders += [new] if new else []
            elif new:
                shaders[i] = new
            else:
                del shaders[i]
        wanted["custom_shaders"] = " ".join(shaders)
        defaults["custom_shaders"] = ""
        # Only write keys already in kitty.conf itself, or ones changed from what kitty uses now. A key that
        # only an included theme sets (Noctalia's cursor_trail_color) stays the theme's, so it keeps following it.
        own = parse_conf(read(CONF))
        wanted = {k: v for k, v in wanted.items()
                  if k in own or not same(v, file_conf.get(k, defaults[k]))}

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
                done.add(key)
                if not wanted[key]:
                    # kitty rejects a key with no value (an emptied custom_shaders list); drop the line.
                    continue
                line = f"{m.group(1)}{key}{m.group(3)}{wanted[key]}"
            out.append(line)
        while out and not out[-1].strip():
            out.pop()

        extra = [f"{k} {v}" for k, v in wanted.items() if k not in done and v]
        if s["theme"] != BASE_LABEL:
            os.makedirs(os.path.dirname(STYLER_THEME), exist_ok=True)
            with open(STYLER_THEME, "w", encoding="utf-8") as f:
                f.write(f"# Theme: {s['theme']}\n# Written by Shellac from kitty's theme collection.\n")
                f.write(themes[s["theme"]])
            extra.append(STYLER_INCLUDE)
        if extra:
            out += ([""] if out else []) + [BLOCK_BEGIN, *extra, BLOCK_END]
        write_atomic(CONF, "\n".join(out) + "\n")
        return backup

    def reload(self):
        subprocess.run(["pkill", "-USR1", "-x", "kitty"], check=False)


TOML_HEADER = re.compile(r"^\s*(\[\[?)\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")
TOML_ASSIGN = re.compile(r"^(\s*)([A-Za-z0-9_.\-]+(?:\s*\.\s*[A-Za-z0-9_\-]+)*)(\s*=\s*)(.*)$")


def toml_sections(lines):
    """[(table path, first line, end)]: the top level, then one per [table] header. Arrays of tables
    ([[x]]) get None, so nothing in them is edited."""
    out, path, start = [], (), 0
    for i, line in enumerate(lines):
        m = TOML_HEADER.match(line)
        if m:
            out.append((path, start, i))
            path = None if m.group(1) == "[[" else tuple(p.strip().strip('"') for p in m.group(2).split("."))
            start = i + 1
    out.append((path, start, len(lines)))
    return out


def toml_find(lines, path):
    """(line, rest) where a value in path is set: rest is what is left of path inside an inline
    table on that line. None if the file doesn't set it in a form this can edit."""
    for table, start, end in toml_sections(lines):
        if table is None or tuple(path[:len(table)]) != table or len(table) >= len(path):
            continue
        rest = tuple(path[len(table):])
        for i in range(start, end):
            m = TOML_ASSIGN.match(lines[i])
            if m:
                key = tuple(p.strip() for p in m.group(2).split("."))
                if rest[:len(key)] == key:
                    return i, rest[len(key):]
    return None


def toml_inline(value):
    """A value as TOML on one line, inline tables written { like = "this" }."""
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{toml_key(k)} = {toml_inline(v)}" for k, v in value.items()) + " }"
    return toml_value(value)


def toml_set(lines, path, value):
    """Set path in place on the line that already sets it (also inside a one-line inline table).
    Returns False, changing nothing, if the file sets it in a form this can't edit."""
    found = toml_find(lines, path)
    if not found:
        return False
    i, rest = found
    m = TOML_ASSIGN.match(lines[i])
    if rest:
        try:
            old = tomllib.loads("v = " + m.group(4))["v"]
        except tomllib.TOMLDecodeError:
            return False
        node = old
        for part in rest[:-1]:
            if not isinstance(node, dict):
                return False
            node = node.setdefault(part, {})
        if not isinstance(node, dict):
            return False
        node[rest[-1]] = value
        value = old
    else:
        try:
            tomllib.loads("v = " + m.group(4))
        except tomllib.TOMLDecodeError:
            return False  # a value over several lines
    lines[i] = m.group(1) + m.group(2) + m.group(3) + toml_inline(value)
    return True


def deep_get(table, path, default=None):
    for part in path:
        if not isinstance(table, dict) or part not in table:
            return default
        table = table[part]
    return table


def deep_set(table, path, value):
    for part in path[:-1]:
        table = table.setdefault(part, {})
    table[path[-1]] = value


def deep_merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def toml_file(path):
    try:
        return tomllib.loads(read(path))
    except tomllib.TOMLDecodeError:
        return {}


def hidden_system_config(user_conf, names):
    """A system-wide config a new user config would hide (the terminal loads only the first it finds),
    if it sets anything; None when the user config exists or there is none to hide."""
    if os.path.exists(user_conf):
        return None
    dirs = [d for d in os.environ.get("XDG_CONFIG_DIRS", "/etc/xdg").split(":") if d]
    for path in [os.path.join(d, n) for d in dirs for n in names] + ["/etc/" + names[0]]:
        # Section headers alone ([main], [colors]) set nothing; foot's example file is only those and comments.
        if any(line.strip() and not line.lstrip().startswith(("#", "[")) for line in read(path).splitlines()):
            return path
    return None


def hex_color(value):
    """#rrggbb for "#RRGGBB" or "0xRRGGBB"; None for anything else."""
    m = re.fullmatch(r"(?:#|0x)([0-9a-fA-F]{6})", str(value).strip())
    return "#" + m.group(1).lower() if m else None


ANSI_NAMES = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
ALACRITTY_COLORS = {"foreground": ("colors", "primary", "foreground"),
                    "background": ("colors", "primary", "background"),
                    "cursor": ("colors", "cursor", "cursor"), "cursor_text_color": ("colors", "cursor", "text"),
                    "selection_foreground": ("colors", "selection", "text"),
                    "selection_background": ("colors", "selection", "background"),
                    **{f"color{i}": ("colors", "normal" if i < 8 else "bright", ANSI_NAMES[i % 8])
                       for i in range(16)}}


def seconds(value):
    """A number of seconds as kitty writes it: 0.5, 1, 1.2."""
    return f"{value:.3f}".rstrip("0").rstrip(".")

# kitty key -> (choices this terminal has, None for the app's own list; its default).
ALACRITTY_OPTIONS = {
    "font_size": (None, "11.25"), "bold_font": (None, "auto"), "italic_font": (None, "auto"),
    "bold_italic_font": (None, "auto"), "background_opacity": (None, "1.00"),
    "window_padding_width": (None, "0"), "hide_window_decorations": (["no", "yes"], "no"),
    "cursor_shape": (None, "block"), "cursor_shape_unfocused": (["hollow", "unchanged"], "hollow"),
    "cursor_blink_interval": (["0", "0.3", "0.5", "0.75", "1.2"], "0"),
    "cursor_stop_blinking_after": (["0", "5", "15", "30", "60"], "5"),
}
# Values that turn off what other terminals don't have, so the preview doesn't draw it.
NO_KITTY_EXTRAS = {"tab_bar": "hidden", "trail_style": "plain", "effect": "none", "cursor_trail": "0",
                   "scrollbar": OFF_SCROLLBAR, "window_margin_width": "0", "draw_minimal_borders": "yes",
                   "window_border_radius": "0", "inactive_text_alpha": "1.0", "disable_ligatures": "always",
                   "modify_font cell_height": "100%", "modify_font cell_width": "100%"}
ALACRITTY_FONTS = {"font_family": "normal", "bold_font": "bold", "italic_font": "italic",
                   "bold_italic_font": "bold_italic"}


class AlacrittyTerminal:
    """Alacritty. Keys alacritty.toml sets itself are edited in place (the main file wins over its
    imports); the rest go in shellac.toml, imported last. A picked theme's colours go in a block at
    the end of alacritty.toml, so no import (Noctalia's included) beats them. While a theme is picked
    the file's own [colors] tables wait in alacritty-colors.shellac.toml, imported, so their other keys
    still apply. Picking the base theme puts them back."""

    name = "alacritty"
    label = "Alacritty"
    live_reload = True
    kitty_only = False
    neutral = NO_KITTY_EXTRAS

    def supports(self, key):
        return key in ALACRITTY_OPTIONS or key in ALACRITTY_FONTS or key not in TERMINAL_KEYS

    def choices(self, key, choices):
        return ALACRITTY_OPTIONS.get(key, (None,))[0] or choices

    def theme_note(self):
        return (f"Apply saves this theme in {self.MANAGED}, which alacritty.toml imports. While a theme is "
                f"picked, the [colors] tables of alacritty.toml wait in {self.SIDE}; picking "
                f'"{BASE_LABEL}" puts them back.')
    MANAGED = "shellac.toml"
    SIDE = "alacritty-colors.shellac.toml"
    NOCTALIA_IMPORT = "noctalia.toml"
    THEME_LINE = "# Theme: "
    IMPORT_LINE = "# Shellac original import: "
    THEME_BEGIN = "# >>> SHELLAC THEME >>>"
    THEME_END = "# <<< SHELLAC THEME <<<"
    ANCHOR_LINE = "# Shellac puts this back before: "

    def __init__(self, conf=None):
        self.conf = conf or self.find_conf()
        self.dir = os.path.dirname(self.conf)
        self.managed = os.path.join(self.dir, self.MANAGED)
        self.side = os.path.join(self.dir, self.SIDE)
        self.noctalia_theme = os.path.join(self.dir, "themes", self.NOCTALIA_IMPORT)

    @staticmethod
    def find_conf():
        """The file alacritty loads: the first that exists, in alacritty's order (else the first)."""
        if os.environ.get("KITTY_STYLER_ALACRITTY"):
            return os.environ["KITTY_STYLER_ALACRITTY"]
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        candidates = [os.path.join(xdg, "alacritty", "alacritty.toml"), os.path.join(xdg, "alacritty.toml"),
                      os.path.expanduser("~/.config/alacritty/alacritty.toml"),
                      os.path.expanduser("~/.alacritty.toml")]
        return next((c for c in candidates if os.path.isfile(c)), candidates[0])

    def import_path(self, path):
        """The path as it stands in an import list: relative to the config folder where it can be."""
        rel = os.path.relpath(path, self.dir)
        return rel if not rel.startswith("..") else path

    def resolve(self, entry, base=None):
        """An import path as alacritty reads it: relative paths are relative to the importing file."""
        entry = os.path.expanduser(entry)
        return os.path.normpath(entry if os.path.isabs(entry) else os.path.join(base or self.dir, entry))

    def strip_theme(self, lines):
        """Take the theme block out of alacritty.toml's lines (in place); returns its lines."""
        begin = next((i for i, line in enumerate(lines) if line == self.THEME_BEGIN), None)
        if begin is None:
            return []
        end = next((i for i in range(begin, len(lines)) if lines[i] == self.THEME_END), len(lines) - 1)
        block = lines[begin:end + 1]
        start = begin - 1 if begin > 0 and not lines[begin - 1].strip() else begin
        del lines[start:end + 1]
        return block

    def own_conf(self):
        """alacritty.toml without the theme block: what the user set there."""
        lines = read(self.conf).splitlines()
        self.strip_theme(lines)
        try:
            return tomllib.loads("\n".join(lines))
        except tomllib.TOMLDecodeError:
            return {}

    def imports(self, data):
        value = deep_get(data, ("general", "import"), data.get("import", []))
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    def merged(self, skip=(), path=None, depth=0, without_theme=False):
        """The config as alacritty sees it: imports in order, then the file itself."""
        path = path or self.conf
        data = self.own_conf() if without_theme and path == self.conf else toml_file(path)
        out = {}
        for entry in self.imports(data):
            target = self.resolve(entry, os.path.dirname(path))
            if target not in skip and os.path.isfile(target) and depth < 5:
                deep_merge(out, self.merged(skip, target, depth + 1))
        return deep_merge(out, data)

    @property
    def noctalia(self):
        imports = self.imports(toml_file(self.conf))
        on = any(entry.endswith(self.NOCTALIA_IMPORT) for entry in imports) and os.path.exists(self.noctalia_theme)
        # The main file's own colours win over Noctalia's (also while they wait in the side file).
        own = deep_get(self.own_conf(), ("colors",)) or os.path.exists(self.side)
        return bool(on and not own and '"alacritty"' in read(NOCTALIA_SETTINGS))

    def to_kitty(self, data):
        """kitty option names for what data sets, with alacritty's defaults for the rest."""
        out = {key: default for key, (_choices, default) in ALACRITTY_OPTIONS.items()}
        fonts = {k: deep_get(data, ("font", sub, "family")) for k, sub in ALACRITTY_FONTS.items()}
        out["font_family"] = fonts.pop("font_family") or "monospace"
        for key, family in fonts.items():
            out[key] = family if family and family != out["font_family"] else "auto"
        if isinstance(deep_get(data, ("font", "size")), (int, float)):
            size = deep_get(data, ("font", "size"))
            out["font_size"] = f"{size:.1f}" if round(size, 1) == size else seconds(size)
        if isinstance(deep_get(data, ("window", "opacity")), (int, float)):
            out["background_opacity"] = f"{deep_get(data, ('window', 'opacity')):.2f}"
        if isinstance(deep_get(data, ("window", "padding", "x")), int):
            out["window_padding_width"] = str(deep_get(data, ("window", "padding", "x")))
        out["hide_window_decorations"] = "yes" if deep_get(data, ("window", "decorations")) == "None" else "no"
        style = deep_get(data, ("cursor", "style"))
        shape = style if isinstance(style, str) else deep_get(style, ("shape",), "Block")
        blinking = deep_get(style, ("blinking",), "Off") if isinstance(style, dict) else "Off"
        out["cursor_shape"] = str(shape).lower()
        out["cursor_shape_unfocused"] = "hollow" if deep_get(data, ("cursor", "unfocused_hollow"), True) else "unchanged"
        if blinking in ("On", "Always"):
            out["cursor_blink_interval"] = seconds(deep_get(data, ("cursor", "blink_interval"), 750) / 1000)
        if isinstance(deep_get(data, ("cursor", "blink_timeout")), int):
            out["cursor_stop_blinking_after"] = str(deep_get(data, ("cursor", "blink_timeout")))
        shell = deep_get(data, ("terminal", "shell"))
        shell = deep_get(shell, ("program",)) if isinstance(shell, dict) else shell
        if isinstance(shell, str):
            out["shell"] = shell
        for key, path in ALACRITTY_COLORS.items():
            color = hex_color(deep_get(data, path, ""))
            if color:
                out[key] = color
        return out

    def to_alacritty(self, s):
        """{kitty key: {path: value}}: the alacritty settings each of the app's settings stands for."""
        out = {key: {("font", sub, "family"): s["font_family"] if s[key] == "auto" else s[key]}
               for key, sub in ALACRITTY_FONTS.items()}
        out["font_size"] = {("font", "size"): float(s["font_size"])}
        out["background_opacity"] = {("window", "opacity"): float(s["background_opacity"])}
        pad = int(float(s["window_padding_width"]))
        out["window_padding_width"] = {("window", "padding"): {"x": pad, "y": pad}}
        out["hide_window_decorations"] = {
            ("window", "decorations"): "None" if s["hide_window_decorations"] == "yes" else "Full"}
        out["cursor_shape"] = {("cursor", "style", "shape"): s["cursor_shape"].capitalize()}
        blink = float(s["cursor_blink_interval"])
        out["cursor_blink_interval"] = {("cursor", "style", "blinking"): "On" if blink > 0 else "Off"}
        if blink > 0:
            out["cursor_blink_interval"][("cursor", "blink_interval")] = round(blink * 1000)
        out["cursor_shape_unfocused"] = {("cursor", "unfocused_hollow"): s["cursor_shape_unfocused"] == "hollow"}
        out["cursor_stop_blinking_after"] = {
            ("cursor", "blink_timeout"): int(float(s["cursor_stop_blinking_after"]))}
        return out

    def read_conf(self, without_theme=False):
        return {**TERMINAL_DEFAULT_COLORS["alacritty"],
                **self.to_kitty(self.merged(skip={self.managed} if without_theme else (),
                                            without_theme=without_theme))}

    def theme(self):
        block = self.strip_theme(read(self.conf).splitlines())
        if len(block) > 1 and block[1].startswith(self.THEME_LINE):
            name, body = block[1], "\n".join(line for line in block[2:] if line != self.THEME_END)
        else:
            # Shellac 1.4.0 kept the theme in shellac.toml.
            name, body = read(self.managed).split("\n", 1)[0], read(self.managed)
            if not name.startswith(self.THEME_LINE):
                return None, ""
        try:
            colors = self.to_kitty(tomllib.loads(body))
        except tomllib.TOMLDecodeError:
            colors = {}
        return name[len(self.THEME_LINE):].strip(), "".join(
            f"{k} {colors[k]}\n" for k in ALACRITTY_COLORS if k in colors)

    def noctalia_colors(self):
        return {k: v for k, v in self.to_kitty(toml_file(self.noctalia_theme)).items() if k in ALACRITTY_COLORS}

    def shell(self):
        return kitty_shell(self.read_conf())

    def move_colors_out(self, lines):
        """Cut the [colors] tables out of lines; returns the side file text, or None if there are none."""
        sections = toml_sections(lines)
        chunks, keep = [], []
        for n, (table, start, end) in enumerate(sections):
            first = start - 1 if table != () else start  # the header line
            if table and table[0] == "colors":
                anchor = next((lines[s - 1] for t, s, _e in sections[n + 1:]
                               if not (t and t[0] == "colors")), "")
                if chunks and chunks[-1][0] == anchor:
                    chunks[-1][1].extend(lines[first:end])
                else:
                    chunks.append((anchor, lines[first:end]))
            else:
                keep.extend(lines[first:end])
        if not chunks:
            return None
        lines[:] = keep
        out = ["# Shellac moved these from alacritty.toml while a theme is picked, so the theme",
               "# can win over them. Picking the base theme puts them back."]
        for anchor, chunk in chunks:
            out += [self.ANCHOR_LINE + anchor, *chunk]
        return "\n".join(out) + "\n"

    def move_colors_back(self, lines):
        chunks, cur = [], None
        for line in read(self.side).splitlines():
            if line.startswith(self.ANCHOR_LINE):
                cur = (line[len(self.ANCHOR_LINE):], [])
                chunks.append(cur)
            elif cur:
                cur[1].append(line)
        for anchor, chunk in chunks:
            at = next((i for i, line in enumerate(lines) if anchor and line == anchor), len(lines))
            lines[at:at] = chunk

    def set_imports(self, lines, wanted_ours):
        """Rewrite [general] import so our files come last (side file, then ours). The original import
        text is kept in shellac.toml and put back once neither file is imported any more."""
        ours = {self.import_path(self.side), self.import_path(self.managed)}
        current = self.imports(tomllib.loads("\n".join(lines)))
        others = [e for e in current if e not in ours]
        if current == others + wanted_ours:
            return None
        found = toml_find(lines, ("general", "import")) or toml_find(lines, ("import",))
        start = found[0] if found else None
        end = start
        if start is not None:
            depth = 0
            for end in range(start, len(lines)):
                depth += lines[end].count("[") - lines[end].count("]")
                if depth <= 0:
                    break
        original = lines[start:end + 1] if start is not None else []
        saved = None
        for line in read(self.managed).splitlines():
            if line.startswith(self.IMPORT_LINE):
                saved = json.loads(line[len(self.IMPORT_LINE):])
        if not wanted_ours and saved is not None:
            new = saved["lines"] if saved["imports"] == others else (
                ["import = ["] + [f"    {toml_value(e)}," for e in others] + ["]"] if others else [])
            if start is None:
                return saved
            lines[start:end + 1] = new
            if saved["added_general"] and not new:
                i = next((i for i, line in enumerate(lines) if line.strip() == "[general]"), None)
                if i is not None and (i + 1 == len(lines) or TOML_HEADER.match(lines[i + 1]) or not lines[i + 1].strip()):
                    del lines[i:i + 2 if i + 1 < len(lines) and not lines[i + 1].strip() else i + 1]
            return saved
        # One path per line: Noctalia's undo deletes every line naming noctalia.toml.
        new = ["import = ["] + [f"    {toml_value(e)}," for e in others + wanted_ours] + ["]"]
        saved = saved or {"lines": original, "imports": others, "added_general": False}
        if start is not None:
            lines[start:end + 1] = new
        elif any(line.strip() == "[general]" for line in lines):
            i = next(i for i, line in enumerate(lines) if line.strip() == "[general]")
            lines[i + 1:i + 1] = new
        else:
            lines[0:0] = ["[general]", *new, ""]
            saved["added_general"] = True
        return saved

    def write(self, s, themes, file_conf):
        hidden = hidden_system_config(self.conf, ["alacritty/alacritty.toml", "alacritty.toml"])
        if hidden:
            raise OSError(f"no {self.conf} yet, and creating one would hide {hidden}: "
                          "copy that file there first")
        text = read(self.conf)
        lines = text.splitlines()
        before = tomllib.loads(text) if text else {}
        self.strip_theme(lines)
        old_managed = toml_file(self.managed)
        # Decide per setting, not per alacritty value: the mapping loses detail (padding x and y,
        # blinking "Never" or "Always"), so a setting left alone must not touch the file.
        changed = {key for key in ALACRITTY_FONTS | ALACRITTY_OPTIONS
                   if not same(str(s[key]), str(file_conf.get(key, "")))}
        if "font_family" in changed:
            changed |= {key for key in ALACRITTY_FONTS if s[key] == "auto"}
        wanted, managed, notes = {}, {}, []
        for key, paths in self.to_alacritty(s).items():
            for path, value in paths.items():
                if deep_get(before, path) is not None:
                    if key in changed and deep_get(before, path) != value:
                        if toml_set(lines, path, value):
                            wanted[path] = value
                        else:
                            notes.append(f"Could not change {'.'.join(path)} in {os.path.basename(self.conf)}; "
                                         "it is set there in a form Shellac can't edit.")
                elif key in changed or deep_get(old_managed, path) is not None:
                    deep_set(managed, path, value)
        # A picked theme's colours go in a block at the end of alacritty.toml itself: the file wins
        # over every import, so Noctalia's import can't beat them, wherever its hook puts it.
        theme = s["theme"] if s["theme"] != BASE_LABEL else None
        side_text, theme_block = None, []
        if theme:
            colors, table = parse_conf(themes[theme]), {}
            for key, path in ALACRITTY_COLORS.items():
                if hex_color(colors.get(key, "")):
                    deep_set(table, path, hex_color(colors[key]))
            body = toml_dump(table)
            while body and not body[0]:
                body.pop(0)
            theme_block = [self.THEME_BEGIN, self.THEME_LINE + theme,
                           "# Written by Shellac; pick the first theme entry to take it out again.", *body,
                           self.THEME_END]
            if not os.path.exists(self.side):
                side_text = self.move_colors_out(lines)
        elif os.path.exists(self.side):
            self.move_colors_back(lines)
        side_on = bool(side_text) or (theme and os.path.exists(self.side))
        wanted_ours = ([self.import_path(self.side)] if side_on else []) + \
                      ([self.import_path(self.managed)] if managed or side_on else [])
        saved = self.set_imports(lines, wanted_ours)
        if theme_block:
            lines += ([""] if lines else []) + theme_block  # strip_theme takes the blank line out again
        new_text = "\n".join(lines) + ("\n" if lines else "")
        try:
            after = tomllib.loads(new_text)
        except tomllib.TOMLDecodeError as e:
            raise OSError(f"left {self.conf} alone: the edit would break it ({e})") from e
        # The edited values read back, and everything else in the file reads back as before.
        def rest(data):
            data = json.loads(json.dumps(data, default=str))
            for path in (*wanted, ("colors",), ("general", "import"), ("import",)):
                node = deep_get(data, path[:-1])
                if isinstance(node, dict):
                    node.pop(path[-1], None)
            if data.get("general") == {}:
                del data["general"]  # added to hold the import
            return data
        if any(deep_get(after, path) != value for path, value in wanted.items()) or rest(before) != rest(after):
            raise OSError(f"left {self.conf} alone: the edit did not come out as planned")

        backup = None
        if text and new_text != text:
            backup = f"{self.conf}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(self.conf, backup)
            prune_backups(self.conf)
        if side_text:
            write_atomic(self.side, side_text)
        if wanted_ours:
            head = ["# Written by Shellac, imported last from alacritty.toml. Changes here are overwritten."]
            if saved:
                head.append(self.IMPORT_LINE + json.dumps(saved))
            write_atomic(self.managed, "\n".join(head + toml_dump(managed)) + "\n")
        if new_text != text:
            write_atomic(self.conf, new_text)
        if not side_on and os.path.exists(self.side):
            os.remove(self.side)
        if not wanted_ours and os.path.exists(self.managed):
            os.remove(self.managed)
        self.notes = notes
        return backup

    def reload(self):
        # live_config_reload (on unless turned off) picks the change up.
        pass


FOOT_OPTIONS = {
    "font_size": (None, "8.0"), "bold_font": (None, "auto"), "italic_font": (None, "auto"),
    "bold_italic_font": (None, "auto"), "background_opacity": (None, "1.00"),
    "window_padding_width": (None, "0"), "hide_window_decorations": (["no", "yes"], "no"),
    "cursor_shape": (None, "block"), "cursor_shape_unfocused": (["hollow", "unchanged"], "hollow"),
    "cursor_beam_thickness": (None, "1.5"), "cursor_underline_thickness": (None, "2.0"),
    "cursor_blink_interval": (["0", "0.3", "0.5", "0.8", "1.2"], "0"),
}
FOOT_FONTS = {"font_family": "font", "bold_font": "font-bold", "italic_font": "font-italic",
              "bold_italic_font": "font-bold-italic"}
FOOT_COLORS = {"foreground": "foreground", "background": "background",
               "selection_foreground": "selection-foreground", "selection_background": "selection-background",
               **{f"color{i}": f"{'regular' if i < 8 else 'bright'}{i % 8}" for i in range(16)}}


def ini_line(line):
    """("section", None) for a [section] line, (None, (key, value)) for key=value, else (None, None)."""
    text = line.strip()
    if not text or text.startswith("#"):
        return None, None
    m = re.fullmatch(r"\[([^\]]+)\]", text)
    if m:
        return m.group(1).strip(), None
    key, eq, value = text.partition("=")
    return None, ((key.strip(), value.strip()) if eq else None)


class FootTerminal:
    """foot. Keys foot.ini sets itself are edited in place; the rest go in a block at the end, with
    the theme as an include there (later lines win). The theme file is not under themes/: Noctalia's
    foot hook deletes every include of a themes/ file when it adds its own. foot reads its config
    when a window opens, so open windows keep theirs."""

    name = "foot"
    label = "foot"
    live_reload = False
    kitty_only = False
    neutral = NO_KITTY_EXTRAS
    THEME_FILE = "shellac.ini"
    BEGIN = "# BEGIN_KITTY_STYLER (managed by kitty-styler; keep this block last so it wins)"
    END = "# END_KITTY_STYLER"

    def __init__(self, conf=None):
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        self.conf = conf or os.environ.get("KITTY_STYLER_FOOT") or os.path.join(xdg, "foot", "foot.ini")
        self.dir = os.path.dirname(self.conf)
        self.theme_file = os.path.join(self.dir, self.THEME_FILE)
        self.noctalia_theme = os.path.join(self.dir, "themes", "noctalia")
        self.notes = []

    def supports(self, key):
        return key in FOOT_OPTIONS or key in FOOT_FONTS or key not in TERMINAL_KEYS

    def choices(self, key, choices):
        return FOOT_OPTIONS.get(key, (None,))[0] or choices

    def theme_note(self):
        return (f"Apply saves this theme as {self.THEME_FILE} and includes it at the end of foot.ini. "
                f'Pick "{BASE_LABEL}" to go back to your own colours.')

    def include_path(self, path):
        home = os.path.expanduser("~") + os.sep
        return "~/" + path[len(home):] if path.startswith(home) else path

    def parse(self, path, skip=(), seen=None):
        """{(section, key): value} as foot reads it: later lines win, includes read in place."""
        seen = set() if seen is None else seen
        real = os.path.realpath(path)
        if real in seen or real in skip:
            return {}
        seen.add(real)
        out, section = {}, "main"
        for line in read(path).splitlines():
            sec, kv = ini_line(line)
            if sec:
                section = "colors-dark" if sec == "colors" else sec
            elif kv and kv[0] == "include":
                out.update(self.parse(os.path.expanduser(kv[1]), skip, seen))
            elif kv:
                out[(section, kv[0])] = kv[1]
        return out

    @property
    def noctalia(self):
        own = self.parse(self.conf, skip={os.path.realpath(self.noctalia_theme),
                                          os.path.realpath(self.theme_file)})
        on = any(kv and kv[0] == "include" and "noctalia" in kv[1]
                 for _sec, kv in map(ini_line, read(self.conf).splitlines()))
        return bool(on and os.path.exists(self.noctalia_theme) and '"foot"' in read(NOCTALIA_SETTINGS)
                    and not any(sec == "colors-dark" and key in FOOT_COLORS.values() for sec, key in own))

    @staticmethod
    def font_parts(value):
        """("Family", size or None, the rest of the value) of a foot font list."""
        first, sep, rest = (value or "").partition(",")
        family, *opts = first.split(":")
        size = next((o.split("=", 1)[1] for o in opts if o.startswith("size=")), None)
        return family.strip(), size, opts, sep + rest

    def to_kitty(self, data):
        out = {key: default for key, (_choices, default) in FOOT_OPTIONS.items()}
        family, size, _opts, _rest = self.font_parts(data.get(("main", "font"), "monospace:size=8"))
        out["font_family"] = family or "monospace"
        if size:
            try:
                out["font_size"] = f"{float(size):.1f}" if round(float(size), 1) == float(size) else size
            except ValueError:
                pass
        for key, name in FOOT_FONTS.items():
            if key != "font_family":
                other = self.font_parts(data.get(("main", name)))[0]
                out[key] = other if other and other != out["font_family"] else "auto"
        if ("colors-dark", "alpha") in data:
            try:
                out["background_opacity"] = f"{float(data[('colors-dark', 'alpha')]):.2f}"
            except ValueError:
                pass
        m = re.match(r"(\d+)x\d+", data.get(("main", "pad"), ""))
        if m:
            out["window_padding_width"] = m.group(1)
        out["hide_window_decorations"] = "yes" if data.get(("csd", "preferred")) == "none" else "no"
        style = data.get(("cursor", "style"), "block")
        out["cursor_shape"] = style if style in ("block", "beam", "underline") else "block"
        unfocused = data.get(("cursor", "unfocused-style"), "hollow")
        out["cursor_shape_unfocused"] = unfocused if unfocused in ("hollow", "unchanged") else "hollow"
        for key, name in (("cursor_beam_thickness", "beam-thickness"),
                          ("cursor_underline_thickness", "underline-thickness")):
            if ("cursor", name) in data and not data[("cursor", name)].endswith("px"):
                out[key] = data[("cursor", name)]
        if data.get(("cursor", "blink")) in ("yes", "true", "1"):
            out["cursor_blink_interval"] = seconds(int(data.get(("cursor", "blink-rate"), "500")) / 1000)
        if ("main", "shell") in data:
            out["shell"] = data[("main", "shell")]
        for key, name in FOOT_COLORS.items():
            color = hex_color("#" + data.get(("colors-dark", name), ""))
            if color:
                out[key] = color
        cursor = data.get(("colors-dark", "cursor"), "").split()
        if len(cursor) == 2 and all(hex_color("#" + c) for c in cursor):
            out["cursor_text_color"], out["cursor"] = (hex_color("#" + c) for c in cursor)
        return out

    def to_foot(self, s, data):
        """{kitty key: {(section, key): value}}; data is what foot uses now, so font options stay."""
        out = {}
        _family, _size, opts, rest = self.font_parts(data.get(("main", "font"), "monospace:size=8"))
        opts = [o for o in opts if not o.startswith("size=")]
        main_font = ":".join([s["font_family"], *opts, f"size={s['font_size']}"]) + rest
        out["font_family"] = out["font_size"] = {("main", "font"): main_font}
        for key, name in FOOT_FONTS.items():
            if key != "font_family":
                out[key] = {("main", name): s["font_family"] if s[key] == "auto" else s[key]}
        out["background_opacity"] = {("colors-dark", "alpha"): s["background_opacity"]}
        pad = int(float(s["window_padding_width"]))
        out["window_padding_width"] = {("main", "pad"): f"{pad}x{pad}"}
        out["hide_window_decorations"] = {("csd", "preferred"): "none" if s["hide_window_decorations"] == "yes"
                                          else "server"}
        out["cursor_shape"] = {("cursor", "style"): s["cursor_shape"]}
        out["cursor_shape_unfocused"] = {("cursor", "unfocused-style"): s["cursor_shape_unfocused"]}
        out["cursor_beam_thickness"] = {("cursor", "beam-thickness"): s["cursor_beam_thickness"]}
        out["cursor_underline_thickness"] = {("cursor", "underline-thickness"): s["cursor_underline_thickness"]}
        blink = float(s["cursor_blink_interval"])
        out["cursor_blink_interval"] = {("cursor", "blink"): "yes" if blink > 0 else "no"}
        if blink > 0:
            out["cursor_blink_interval"][("cursor", "blink-rate")] = str(round(blink * 1000))
        return out

    def read_conf(self, without_theme=False):
        return {**TERMINAL_DEFAULT_COLORS["foot"], **self.to_kitty(
            self.parse(self.conf, skip={os.path.realpath(self.theme_file)} if without_theme else ()))}

    def theme(self):
        text = read(self.theme_file)
        if not (text.startswith("# Theme: ") and self.include_path(self.theme_file) in read(self.conf)):
            return None, ""
        colors = self.to_kitty(self.parse(self.theme_file))
        return text.split("\n", 1)[0][len("# Theme: "):].strip(), "".join(
            f"{k} {colors[k]}\n" for k in (*FOOT_COLORS, "cursor", "cursor_text_color") if k in colors)

    def noctalia_colors(self):
        colors = self.to_kitty(self.parse(self.noctalia_theme))
        return {k: v for k, v in colors.items() if k in FOOT_COLORS or k in ("cursor", "cursor_text_color")}

    def shell(self):
        return kitty_shell(self.read_conf())

    def write(self, s, themes, file_conf):
        hidden = hidden_system_config(self.conf, ["foot/foot.ini"])
        if hidden:
            raise OSError(f"no {self.conf} yet, and creating one would hide {hidden}: "
                          "copy that file there first")
        text = read(self.conf)
        lines, block, in_block = [], {}, False
        for line in text.splitlines():
            if line.strip().startswith("# BEGIN_KITTY_STYLER"):
                in_block, section = True, "main"
                continue
            if in_block:
                if line.strip() == self.END:
                    in_block = False
                    continue
                sec, kv = ini_line(line)
                if sec:
                    section = sec
                elif kv and kv[0] != "include":
                    block[(section, kv[0])] = kv[1]
                continue
            lines.append(line)
        while lines and not lines[-1].strip():
            lines.pop()
        now = self.parse(self.conf)
        changed = {key for key in FOOT_FONTS | FOOT_OPTIONS if not same(str(s[key]), str(file_conf.get(key, "")))}
        if "font_family" in changed:
            changed |= {key for key in FOOT_FONTS if s[key] == "auto"}
        wanted = {}
        for key, paths in self.to_foot(s, now).items():
            if key in changed:
                wanted.update(paths)
        # Lines the file sets itself are changed where they are (the last one, which wins).
        section, last = "main", {}
        for i, line in enumerate(lines):
            sec, kv = ini_line(line)
            if sec:
                section = "colors-dark" if sec == "colors" else sec
            elif kv:
                last[(section, kv[0])] = i
        for path, value in wanted.items():
            if path in last and path not in block:
                i = last[path]
                indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
                lines[i] = f"{indent}{path[1]}={value}"
            else:
                block[path] = value
        out = list(lines)
        theme = s["theme"] if s["theme"] != BASE_LABEL else None
        if block or theme:
            out += ([""] if out else []) + [self.BEGIN]
            sections = {}
            for (sec, key), value in block.items():
                sections.setdefault(sec, []).append(f"{key}={value}")
            if theme:
                sections.setdefault("main", []).insert(0, f"include={self.include_path(self.theme_file)}")
            for sec, entries in sections.items():
                out += [f"[{sec}]", *entries]
            out.append(self.END)
        new_text = "\n".join(out) + "\n" if out else ""
        theme_text = None
        if theme:
            colors = parse_conf(themes[theme])
            entries = [f"{name}={hex_color(colors[k])[1:]}" for k, name in FOOT_COLORS.items()
                       if hex_color(colors.get(k, ""))]
            if hex_color(colors.get("cursor", "")):
                text_color = hex_color(colors.get("cursor_text_color", "")) or hex_color(colors.get("background", ""))
                if text_color:
                    entries.append(f"cursor={text_color[1:]} {hex_color(colors['cursor'])[1:]}")
            theme_text = "\n".join([f"# Theme: {theme}", "# Written by Shellac from kitty's theme collection.",
                                    "[colors-dark]", *entries]) + "\n"
        if shutil.which("foot"):
            # foot checks the whole config, includes too; nothing is written if it says no.
            with tempfile.TemporaryDirectory() as tmp:
                conf = os.path.join(tmp, "foot.ini")
                check_text = new_text
                if theme_text:
                    with open(os.path.join(tmp, self.THEME_FILE), "w", encoding="utf-8") as f:
                        f.write(theme_text)
                    check_text = new_text.replace(f"include={self.include_path(self.theme_file)}",
                                                  f"include={os.path.join(tmp, self.THEME_FILE)}")
                with open(conf, "w", encoding="utf-8") as f:
                    f.write(check_text)
                r = subprocess.run(["foot", "--check-config", "-c", conf], capture_output=True, text=True,
                                   timeout=10, check=False)
                if r.returncode != 0:
                    err = (r.stderr or r.stdout).strip().splitlines()
                    raise OSError(f"left {self.conf} alone: foot rejects the result ({err[-1] if err else '?'})")
        backup = None
        if text and new_text != text:
            backup = f"{self.conf}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(self.conf, backup)
            prune_backups(self.conf)
        if theme_text:
            write_atomic(self.theme_file, theme_text)
        if new_text != text:
            write_atomic(self.conf, new_text)
        if not theme and os.path.exists(self.theme_file):
            os.remove(self.theme_file)
        self.notes = ["foot reads its config when a window opens: new foot windows get this, open ones keep theirs."]
        return backup

    def reload(self):
        pass


GHOSTTY_OPTIONS = {
    "font_size": (None, "12.0"), "bold_font": (None, "auto"), "italic_font": (None, "auto"),
    "bold_italic_font": (None, "auto"), "modify_font cell_height": (None, "100%"),
    "modify_font cell_width": (None, "100%"), "background_opacity": (None, "1.00"),
    "window_padding_width": (None, "2"), "hide_window_decorations": (["no", "yes"], "no"),
    "cursor_shape": (None, "block"), "cursor_blink_interval": (["-1", "0", "0.5"], "-1"),
}
GHOSTTY_FONTS = {"font_family": "font-family", "bold_font": "font-family-bold", "italic_font": "font-family-italic",
                 "bold_italic_font": "font-family-bold-italic"}
GHOSTTY_COLORS = {"foreground": "foreground", "background": "background", "cursor": "cursor-color",
                  "cursor_text_color": "cursor-text", "selection_foreground": "selection-foreground",
                  "selection_background": "selection-background"}
GHOSTTY_SHAPES = {"block": "block", "beam": "bar", "underline": "underline"}


def ghostty_line(line):
    """(key, value) of a Ghostty config line, quotes taken off; None for comments and blank lines."""
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        return None
    key, _eq, value = text.partition("=")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return key.strip(), value


class GhosttyTerminal:
    """Ghostty. Keys its config sets itself are edited in place; the rest, and a picked theme's colours,
    go in shellac.ghostty, loaded with config-file. Ghostty loads config-file files after the
    whole config, and colours set outside a theme win over it, so Noctalia's theme = noctalia line
    can stay as it is."""

    name = "ghostty"
    label = "Ghostty"
    live_reload = True
    kitty_only = False
    neutral = NO_KITTY_EXTRAS
    MANAGED = "shellac.ghostty"
    INCLUDE = f"config-file = ?{MANAGED}"
    INCLUDE_NOTE = "# Shellac's settings; loaded after this file, so they win."

    def __init__(self, conf=None):
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        folder = os.path.join(xdg, "ghostty")
        found = next((p for p in (os.path.join(folder, "config.ghostty"), os.path.join(folder, "config"))
                      if os.path.isfile(p)), os.path.join(folder, "config.ghostty"))
        self.conf = conf or os.environ.get("KITTY_STYLER_GHOSTTY") or found
        self.dir = os.path.dirname(self.conf)
        self.managed = os.path.join(self.dir, self.MANAGED)
        self.noctalia_theme = os.path.join(self.dir, "themes", "noctalia")
        self.notes = []

    def supports(self, key):
        return key in GHOSTTY_OPTIONS or key in GHOSTTY_FONTS or key not in TERMINAL_KEYS

    def choices(self, key, choices):
        return GHOSTTY_OPTIONS.get(key, (None,))[0] or choices

    def theme_note(self):
        return (f"Apply saves this theme in {self.MANAGED}, which your Ghostty config loads last, so it wins "
                f'over its theme line. Pick "{BASE_LABEL}" to go back to your own colours.')

    def parse(self, path, skip=(), seen=None):
        """{key: value} as Ghostty reads it: the file, then its config-file files in order. Repeatable
        keys: font families keep their first value after a "" reset; palette entries become palette N."""
        seen = set() if seen is None else seen
        real = os.path.realpath(path)
        if real in seen or real in skip:
            return {}
        seen.add(real)
        out, later = {}, []
        for line in read(path).splitlines():
            kv = ghostty_line(line)
            if not kv:
                continue
            key, value = kv
            if key == "config-file":
                later.append(value)
            elif key == "theme" and not os.path.isabs(value) and "/" not in value and ":" not in value:
                out["theme"] = value
            elif key in GHOSTTY_FONTS.values():
                if value == "":
                    out.pop(key, None)
                else:
                    out.setdefault(key, value)
            elif key == "palette" and "=" in value:
                index, _eq, color = value.partition("=")
                out[f"palette {index.strip()}"] = color.strip()
            else:
                out[key] = value
        for entry in later:
            target = os.path.join(os.path.dirname(path), os.path.expanduser(entry.removeprefix("?")))
            out.update(self.parse(target, skip, seen))
        return out

    def theme_colors(self, name):
        """Colours of a Ghostty theme by name: the user's themes folder, then Ghostty's own."""
        for folder in (os.path.join(self.dir, "themes"), "/usr/share/ghostty/themes"):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return self.parse(path)
        return {}

    @property
    def noctalia(self):
        own = self.parse(self.conf, skip={os.path.realpath(self.managed)})
        return bool(own.get("theme") == "noctalia" and os.path.exists(self.noctalia_theme)
                    and '"ghostty"' in read(NOCTALIA_SETTINGS)
                    and not any(k in own for k in GHOSTTY_COLORS.values()))

    def to_kitty(self, data):
        if data.get("theme"):
            data = {**self.theme_colors(data["theme"]), **data}
        out = {key: default for key, (_choices, default) in GHOSTTY_OPTIONS.items()}
        out["font_family"] = data.get("font-family") or "monospace"
        for key, name in GHOSTTY_FONTS.items():
            if key != "font_family":
                out[key] = data.get(name) or "auto"
        try:
            size = float(data.get("font-size", "12"))
            out["font_size"] = f"{size:.1f}" if round(size, 1) == size else seconds(size)
        except ValueError:
            pass
        for key, name in (("modify_font cell_height", "adjust-cell-height"),
                          ("modify_font cell_width", "adjust-cell-width")):
            m = re.fullmatch(r"(-?\d+(?:\.\d+)?)%", data.get(name, ""))
            if m:
                out[key] = f"{100 + float(m.group(1)):g}%"
        try:
            out["background_opacity"] = f"{float(data.get('background-opacity', '1')):.2f}"
        except ValueError:
            pass
        if re.fullmatch(r"\d+", data.get("window-padding-x", "")):
            out["window_padding_width"] = data["window-padding-x"]
        out["hide_window_decorations"] = "yes" if data.get("window-decoration") in ("none", "false") else "no"
        shape = data.get("cursor-style", "block")
        out["cursor_shape"] = next((k for k, v in GHOSTTY_SHAPES.items() if v == shape), "block")
        blink = data.get("cursor-style-blink", "")
        out["cursor_blink_interval"] = "0" if blink == "false" else "0.5" if blink == "true" else "-1"
        if data.get("command"):
            out["shell"] = data["command"]
        for key, name in GHOSTTY_COLORS.items():
            color = hex_color(data.get(name, "")) or hex_color("#" + data.get(name, ""))
            if color:
                out[key] = color
        for i in range(16):
            color = hex_color(data.get(f"palette {i}", ""))
            if color:
                out[f"color{i}"] = color
        return out

    def to_ghostty(self, s):
        """{kitty key: {ghostty key: value}}."""
        # "auto" is no line at all: Ghostty then takes bold and italic from the main font.
        out = {key: {name: "" if s[key] == "auto" else s[key]} for key, name in GHOSTTY_FONTS.items()}
        out["font_size"] = {"font-size": s["font_size"]}
        for key, name in (("modify_font cell_height", "adjust-cell-height"),
                          ("modify_font cell_width", "adjust-cell-width")):
            out[key] = {name: f"{float(s[key].rstrip('%')) - 100:g}%"}
        out["background_opacity"] = {"background-opacity": s["background_opacity"]}
        pad = str(int(float(s["window_padding_width"])))
        out["window_padding_width"] = {"window-padding-x": pad, "window-padding-y": pad}
        out["hide_window_decorations"] = {"window-decoration": "none" if s["hide_window_decorations"] == "yes"
                                          else "auto"}
        out["cursor_shape"] = {"cursor-style": GHOSTTY_SHAPES.get(s["cursor_shape"], "block")}
        blink = float(s["cursor_blink_interval"])
        out["cursor_blink_interval"] = {"cursor-style-blink": "" if blink < 0 else "true" if blink > 0 else "false"}
        return out

    def read_conf(self, without_theme=False):
        return {**TERMINAL_DEFAULT_COLORS["ghostty"], **self.to_kitty(
            self.parse(self.conf, skip={os.path.realpath(self.managed)} if without_theme else ()))}

    def theme(self):
        first = read(self.managed).split("\n", 1)[0]
        if not first.startswith("# Theme: "):
            return None, ""
        colors = self.to_kitty({k: v for k, v in self.parse(self.managed).items() if k != "theme"})
        return first[len("# Theme: "):].strip(), "".join(
            f"{k} {colors[k]}\n" for k in (*GHOSTTY_COLORS, *(f"color{i}" for i in range(16))) if k in colors)

    def noctalia_colors(self):
        colors = self.to_kitty(self.parse(self.noctalia_theme))
        return {k: v for k, v in colors.items() if k in GHOSTTY_COLORS or k.startswith("color")}

    def shell(self):
        return kitty_shell(self.read_conf())

    def write(self, s, themes, file_conf):
        text = read(self.conf)
        lines = text.splitlines()
        managed = {}
        for line in read(self.managed).splitlines():
            kv = ghostty_line(line)
            if kv and kv[0] not in GHOSTTY_COLORS.values() and kv[0] != "palette" and kv[1] != "":
                managed[kv[0]] = kv[1]
        changed = {key for key in GHOSTTY_FONTS | GHOSTTY_OPTIONS if not same(str(s[key]), str(file_conf.get(key, "")))}
        if "font_family" in changed:
            changed |= {key for key in GHOSTTY_FONTS if s[key] == "auto"}
        # Where the file sets each key itself: the last line (font families: the first, the main font).
        own = {}
        for i, line in enumerate(lines):
            kv = ghostty_line(line)
            if kv and kv[0] not in ("palette", "config-file") and kv[1] != "" \
                    and (kv[0] not in GHOSTTY_FONTS.values() or kv[0] not in own):
                own[kv[0]] = i
        for key, values in self.to_ghostty(s).items():
            if key not in changed:
                continue
            for name, value in values.items():
                if name in own and name not in managed:
                    i = own[name]
                    lines[i] = f"{name} = {value}" if value else f"{name} ="
                elif value:
                    managed[name] = value
                else:
                    managed.pop(name, None)
        theme = s["theme"] if s["theme"] != BASE_LABEL else None
        body = []
        for name, value in managed.items():
            if name in GHOSTTY_FONTS.values():
                body.append(f'{name} = ""')  # a font family adds a fallback unless the list is reset first
            body.append(f"{name} = {value}")
        if theme:
            colors = parse_conf(themes[theme])
            body += [f"{name} = {hex_color(colors[k])}" for k, name in GHOSTTY_COLORS.items()
                     if hex_color(colors.get(k, ""))]
            body += [f"palette = {i}={hex_color(colors[f'color{i}'])}" for i in range(16)
                     if hex_color(colors.get(f"color{i}", ""))]
        include = [i for i, line in enumerate(lines) if line.strip() == self.INCLUDE]
        if body and not include:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ([""] if lines else []) + [self.INCLUDE_NOTE, self.INCLUDE]
        elif not body and include:
            for i in reversed(include):
                del lines[i]
                if i > 0 and lines[i - 1] == self.INCLUDE_NOTE:
                    del lines[i - 1]
            while lines and not lines[-1].strip():
                lines.pop()
        new_text = "\n".join(lines) + "\n" if lines else ""
        managed_text = "\n".join(([f"# Theme: {theme}"] if theme else [])
                                 + ["# Written by Shellac; loaded last from the Ghostty config.",
                                    "# Changes here are overwritten.", *body]) + "\n"
        if shutil.which("ghostty"):
            with tempfile.TemporaryDirectory() as tmp:
                conf = os.path.join(tmp, "config.ghostty")
                with open(conf, "w", encoding="utf-8") as f:
                    f.write(new_text)
                with open(os.path.join(tmp, self.MANAGED), "w", encoding="utf-8") as f:
                    f.write(managed_text)
                for name in os.listdir(self.dir) if os.path.isdir(self.dir) else []:
                    if name == "themes":
                        os.symlink(os.path.join(self.dir, name), os.path.join(tmp, name))
                r = subprocess.run(["ghostty", "+validate-config", f"--config-file={conf}"], capture_output=True,
                                   text=True, timeout=15, check=False)
                if r.returncode != 0:
                    err = (r.stderr or r.stdout).strip().splitlines()
                    raise OSError(f"left {self.conf} alone: Ghostty rejects the result "
                                  f"({err[-1].replace(tmp, self.dir) if err else '?'})")
        backup = None
        if text and new_text != text:
            backup = f"{self.conf}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(self.conf, backup)
            prune_backups(self.conf)
        if body:
            write_atomic(self.managed, managed_text)
        elif os.path.exists(self.managed):
            os.remove(self.managed)
        if new_text != text:
            write_atomic(self.conf, new_text)
        return backup

    def reload(self):
        """Like Noctalia's reload hook: the systemd unit's process, then the single-instance process on
        D-Bus, then SIGUSR2 to every other Ghostty process (windows started with gtk-single-instance=false)."""
        def run(*cmd):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                return None

        done = set()
        r = run("systemctl", "--user", "show", "--property=MainPID", "--value", "app-com.mitchellh.ghostty.service")
        pid = r.stdout.strip() if r else ""
        if pid.isdigit() and pid != "0":
            r = run("systemctl", "--user", "reload", "app-com.mitchellh.ghostty.service")
            if r and r.returncode == 0:
                done.add(int(pid))
        r = run("gdbus", "call", "--session", "--dest", "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus",
                "--method", "org.freedesktop.DBus.GetConnectionUnixProcessID", "com.mitchellh.ghostty")
        m = re.match(r"\(uint32 (\d+),\)", r.stdout.strip()) if r else None
        if m and int(m.group(1)) not in done:
            r = run("gdbus", "call", "--session", "--dest", "com.mitchellh.ghostty", "--object-path",
                    "/com/mitchellh/ghostty", "--method", "org.gtk.Actions.Activate", "reload-config", "[]", "{}")
            if r and r.returncode == 0:
                done.add(int(m.group(1)))
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or int(entry) in done or int(entry) == os.getpid():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as f:
                    argv0 = f.read().split(b"\0", 1)[0].decode(errors="replace")
                # Matched on the program name, so a shell whose command line mentions ghostty is left alone.
                if os.path.basename(argv0) in ("ghostty", ".ghostty-wrapped") \
                        and os.stat(f"/proc/{entry}").st_uid == os.getuid():
                    os.kill(int(entry), signal.SIGUSR2)
            except OSError:
                continue


def default_colors(fg, bg, palette):
    """Kitty colour keys for a terminal's built-in colours (from its documentation). Cursor and selection
    default to the inverse of the text in all three."""
    return {"foreground": fg, "background": bg, "cursor": fg, "cursor_text_color": bg,
            "selection_foreground": bg, "selection_background": fg,
            **{f"color{i}": c for i, c in enumerate(palette.split())}}


TERMINAL_DEFAULT_COLORS = {
    "alacritty": default_colors("#d8d8d8", "#181818", "#181818 #ac4242 #90a959 #f4bf75 #6a9fb5 #aa759f #75b5aa "
                                "#d8d8d8 #6b6b6b #c55555 #aac474 #feca88 #82b8c8 #c28cb8 #93d3c3 #f8f8f8"),
    "foot": default_colors("#ffffff", "#242424", "#242424 #f62b5a #47b413 #e3c401 #24acd4 #f2affd #13c299 "
                           "#e6e6e6 #616161 #ff4d51 #35d450 #e9e836 #5dc5f8 #feabf2 #24dfc4 #ffffff"),
    "ghostty": default_colors("#ffffff", "#282c34", "#1d1f21 #cc6666 #b5bd68 #f0c674 #81a2be #b294bb #8abeb7 "
                              "#c5c8c6 #666666 #d54e53 #b9ca4a #e7c547 #7aa6da #c397d8 #70c0b1 #eaeaea"),
}


# Settings that belong to the terminal (the rest are the prompt's and the shell's).
TERMINAL_KEYS = {key for _l, key, _c, _d in OPTIONS} | {"font_family", "tab_bar", "trail_style", "effect"}
TERMINALS = {"kitty": KittyTerminal, "alacritty": AlacrittyTerminal, "foot": FootTerminal, "ghostty": GhosttyTerminal}
TERMINAL_BINARIES = {"kitty": "kitty", "alacritty": "alacritty", "foot": "foot", "ghostty": "ghostty"}
TERMINAL_STATE = os.path.join(os.path.dirname(UPDATE_STATE), "terminal")
ZSH_INSTALLED = ZSH_AVAILABLE


def available_terminals():
    """The terminals installed here, or with a config file; kitty first."""
    out = [name for name, cls in TERMINALS.items()
           if shutil.which(TERMINAL_BINARIES[name]) or os.path.exists(cls().conf)]
    return out or ["kitty"]


def default_terminal():
    """KITTY_STYLER_TERMINAL, else the one picked last time, else the one the app was started from."""
    have = available_terminals()
    term = os.environ.get("TERM", "")
    launched = "alacritty" if os.environ.get("ALACRITTY_WINDOW_ID") else \
               "kitty" if os.environ.get("KITTY_WINDOW_ID") else "foot" if term.startswith("foot") else \
               "ghostty" if os.environ.get("GHOSTTY_RESOURCES_DIR") else None
    for name in (os.environ.get("KITTY_STYLER_TERMINAL"), read(TERMINAL_STATE).strip(), launched):
        if name in have:
            return name
    return have[0]


def set_terminal(name, remember=False):
    """Point the app at another terminal; the globals below follow it."""
    global TERMINAL, BASE_LABEL, KITTY_SHELL, ZSH_AVAILABLE
    TERMINAL = TERMINALS[name]()
    BASE_LABEL = "Noctalia (follows wallpaper)" if TERMINAL.noctalia else "Keep current theme"
    KITTY_SHELL = TERMINAL.shell()
    # The Shell (zsh) section edits .zshrc, which only matters when the terminal runs zsh.
    ZSH_AVAILABLE = ZSH_INSTALLED and KITTY_SHELL == "zsh"
    if remember:
        write_atomic(TERMINAL_STATE, name + "\n")


set_terminal(default_terminal())


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


# Seconds the blaze and the lightning bolt trails stay on screen, and the blaze colours.
TRAIL_LIFE = {"blaze": 0.4, "lightning": 0.3}
BLAZE_FILL = (1.0, 0.69, 0.0)
BLAZE_EDGE = (1.0, 0.27, 0.0)


def convex_hull(points):
    """The outline around points, so a shape made from moving corners never folds over itself."""
    pts_ = sorted(set(map(tuple, points)))
    if len(pts_) < 3:
        return pts_

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    hull = []
    for seq in (pts_, pts_[::-1]):
        half = []
        for p in seq:
            while len(half) >= 2 and cross(half[-2], half[-1], p) <= 0:
                half.pop()
            half.append(p)
        hull += half[:-1]
    return hull


class Trail(Gtk.Widget):
    """kitty's cursor trail, on its own layer like the cursor.

    Plain: each corner of the trail chases the same corner of the new cursor. Corners on the side
    the cursor moved towards use the fast decay time, the ones behind it the slow one, so the trail
    stretches and then shrinks into the cursor. The shader styles (blaze, lightning, motion blur)
    are kitty GPU shaders; these are look-alikes drawn from their descriptions, not ports.

    Drawn as shape nodes, not a DrawingArea: that repainted a window-sized image every frame,
    25-30 ms each, which made the whole app crawl while the trail demo ran.
    """


    def __init__(self):
        super().__init__(can_target=False)
        self.corners = None
        self.tick_id = None

    @staticmethod
    def corner_points(rect):
        x, y, w, h = rect
        return [[x + w, y], [x + w, y + h], [x, y + h], [x, y]]

    def start(self, old, new, decay, color, style="plain"):
        self.corners = self.corner_points(old)
        self.prev = [list(c) for c in self.corners]
        self.target = self.corner_points(new)
        self.decay, self.color, self.style, self.last, self.age = decay, color, style, None, 0.0
        self.swept = convex_hull(self.corners + self.target)
        if style == "lightning":
            self.make_bolt(old, new)
        if not self.tick_id:
            self.tick_id = self.add_tick_callback(self.tick)

    def make_bolt(self, old, new):
        """A jagged line between the two cursor centres, plus a few short forks off it."""
        (ax, ay), (bx, by) = ((r[0] + r[2] / 2, r[1] + r[3] / 2) for r in (old, new))
        length = max(1.0, math.hypot(bx - ax, by - ay))
        # Unit vector across the line, for the sideways kinks.
        nx, ny = -(by - ay) / length, (bx - ax) / length
        n = 9
        self.bolt = [(ax, ay)]
        for i in range(1, n):
            f, kink = i / n, random.uniform(-0.12, 0.12) * length
            self.bolt.append((ax + (bx - ax) * f + nx * kink, ay + (by - ay) * f + ny * kink))
        self.bolt.append((bx, by))
        self.forks = []
        for i in (2, 4, 6):
            x, y = self.bolt[i]
            side = random.choice((-1, 1))
            reach = random.uniform(0.08, 0.16) * length
            self.forks.append([(x, y), (x + ((bx - ax) / length * 0.5 + nx * side) * reach,
                                        y + ((by - ay) / length * 0.5 + ny * side) * reach)])

    def tick(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        dt, self.last = (now - self.last if self.last else 0), now
        self.age += dt
        if self.style in TRAIL_LIFE:
            done = self.age >= TRAIL_LIFE[self.style]
        else:
            done = self.chase(dt)
        self.queue_draw()
        if done:
            self.corners = self.tick_id = None
            return False
        return True

    def chase(self, dt):
        """Move the corners one frame towards the cursor; True once they are all there."""
        self.prev = [list(c) for c in self.corners]
        fast, slow = self.decay
        cx = sum(p[0] for p in self.target) / 4
        cy = sum(p[1] for p in self.target) / 4
        done = True
        for (px, py), corner in zip(self.target, self.corners):
            dx, dy = px - corner[0], py - corner[1]
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 0.5:
                corner[:] = px, py
                continue
            done = False
            # Which way the corner points, as a cell diagonal: a beam's corners would otherwise all
            # point up or down, and a sideways jump would move them all at the same speed.
            ox, oy = (1 if px > cx else -1), (1 if py > cy else -1)
            # 1 for a corner leading the move, 0 for one trailing it.
            lead = ((ox * dx + oy * dy) / (2 ** 0.5 * dist) + 1) / 2
            step = 1 - 2 ** (-10 * dt / max(1e-3, slow + (fast - slow) * lead))
            corner[0] += dx * step
            corner[1] += dy * step
        return done

    def shapes(self):
        """What to draw this frame: (points, closed, rgb, alpha, stroke width or 0 for a fill)."""
        if self.style == "blaze":
            fade = (1 - self.age / TRAIL_LIFE["blaze"]) ** 2
            return [(self.swept, True, BLAZE_FILL, 0.55 * fade, 0),
                    (self.swept, True, BLAZE_EDGE, fade, 1.5)]
        if self.style == "lightning":
            fade = 1 - self.age / TRAIL_LIFE["lightning"]
            lines = [self.bolt, *self.forks]
            return ([(pts, False, self.color, 0.3 * fade, 7) for pts in lines]
                    + [(pts, False, (1.0, 1.0, 1.0), fade, 1.5) for pts in lines])
        if self.style == "motion blur":
            # The trail at in-between positions since the last frame, blended into a soft streak.
            steps = 6
            return [(convex_hull([[a + (b - a) * k / steps for a, b in zip(p, c)]
                                  for p, c in zip(self.prev, self.corners)]), True, self.color, 0.3, 0)
                    for k in range(1, steps + 1)]
        return [(convex_hull(self.corners), True, self.color, 1.0, 0)]

    def do_snapshot(self, snapshot):
        if not self.corners:
            return
        shapes = [s for s in self.shapes() if len(s[0]) >= (3 if s[1] else 2) and s[3] > 0]
        if not shapes:
            return
        if hasattr(Gsk, "PathBuilder"):
            for pts, closed, rgb, alpha, width in shapes:
                builder = Gsk.PathBuilder.new()
                builder.move_to(*pts[0])
                for p in pts[1:]:
                    builder.line_to(*p)
                if closed:
                    builder.close()
                color = Gdk.RGBA()
                color.red, color.green, color.blue, color.alpha = (*rgb, alpha)
                if width:
                    stroke = Gsk.Stroke.new(width)
                    stroke.set_line_join(Gsk.LineJoin.ROUND)
                    stroke.set_line_cap(Gsk.LineCap.ROUND)
                    snapshot.append_stroke(builder.to_path(), stroke, color)
                else:
                    snapshot.append_fill(builder.to_path(), Gsk.FillRule.WINDING, color)
            return
        # GTK before 4.14: cairo, but only over the box the shapes cover.
        xs = [p[0] for s in shapes for p in s[0]]
        ys = [p[1] for s in shapes for p in s[0]]
        x0, y0 = math.floor(min(xs)) - 4, math.floor(min(ys)) - 4
        cr = snapshot.append_cairo(Graphene.Rect().init(x0, y0, math.ceil(max(xs)) - x0 + 5,
                                                        math.ceil(max(ys)) - y0 + 5))
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for pts, closed, rgb, alpha, width in shapes:
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            if closed:
                cr.close_path()
            cr.set_source_rgba(*rgb, alpha)
            if width:
                cr.set_line_width(width)
                cr.stroke()
            else:
                cr.fill()


class Preview(Gtk.Widget):
    """The kitty mock-up, drawn with cairo into a texture that is kept until queue_draw.

    Not a DrawingArea: GTK's Vulkan renderer replays a DrawingArea's cairo drawing wherever
    anything on top changes, so every cursor trail frame redrew the wallpaper and text under
    it (30-40 ms a frame for blaze and lightning, 70 ms for the whole preview).
    """

    def __init__(self, app):
        super().__init__(hexpand=True, vexpand=True)
        self.app = app
        # (width, height, scale, texture) of the last drawing; None after queue_draw.
        self.texture = None
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
        # Which cursor marker the focused cursor sits on; the trail demo moves it.
        self.cursor_at = "\0cursor"
        self.cursor_spots = {}
        self.hover = False
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self.on_hover, True)
        motion.connect("leave", self.on_hover, False)
        self.add_controller(motion)

    def queue_draw(self):
        self.texture = None
        super().queue_draw()

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        if w < 1 or h < 1:
            return
        native = self.get_native()
        scale = native.get_surface().get_scale() if native and native.get_surface() else self.get_scale_factor()
        if self.texture is None or self.texture[:3] != (w, h, scale):
            pw, ph = math.ceil(w * scale), math.ceil(h * scale)
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, pw, ph)
            surf.set_device_scale(pw / w, ph / h)
            self.draw(self, cairo.Context(surf), w, h)
            surf.flush()
            tex = Gdk.MemoryTexture.new(pw, ph, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                        GLib.Bytes.new(bytes(surf.get_data())), surf.get_stride())
            self.texture = (w, h, scale, tex)
        snapshot.append_texture(self.texture[3], Graphene.Rect().init(0, 0, w, h))

    def on_hover(self, _ctl, *args):
        self.hover = args[-1]
        if "hovered" in self.app.settings["scrollbar"]:
            self.queue_draw()

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
        if not TILING and s["hide_window_decorations"] == "no":
            win = self.draw_titlebar(cr, win)
        cr.rectangle(*win)
        cr.set_source_rgba(*hex_rgb(c["background"]), float(s["background_opacity"]))
        cr.fill()

        font = Pango.FontDescription.from_string(f"{s['font_family']} {s['font_size']}")
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(font)
        layout.set_text("M", -1)
        _ink, logical = layout.get_pixel_extents()
        # modify_font cell_width/cell_height widen the cells; glyphs stay their size, centred vertically.
        self.cw = logical.width * float(s["modify_font cell_width"].rstrip("%")) / 100
        self.lh = logical.height * float(s["modify_font cell_height"].rstrip("%")) / 100
        self.yoff = (self.lh - logical.height) / 2
        self.per_cell = self.cw != logical.width

        cr.save()
        cr.rectangle(*win)
        cr.clip()
        style, pl = TAB_STYLES[s["tab_bar"]]
        tab_h = self.lh if style != "hidden" else 0
        top = s["tab_bar_edge"] == "top"
        # Tab bar margins: outer is between the window edge and the bar, inner between the bar and the text.
        outer, inner = (pts(s["tab_bar_margin_height"], i) for i in (0, 1)) if tab_h else (0, 0)
        tab_y = win[1] + outer if top else win[1] + win[3] - outer - tab_h
        # The rest holds two kitty windows: the main one, and an inactive one split off below it,
        # so borders, faded text and the unfocused cursor have something to show on.
        y0 = win[1] + (outer + tab_h + inner if top else 0)
        y1 = win[1] + win[3] - (0 if top else outer + tab_h + inner)
        main, other = self.app.sample_lines()
        other = other[-INACTIVE_ROWS:]
        frame = (pts(s["window_margin_width"]) + self.border_width() + pts(s["window_padding_width"]))
        # The main window keeps at least 60% of the height.
        split = max(y0 + (y1 - y0) * 0.6, y1 - 2 * frame - len(other) * self.lh)
        if not TERMINAL.kitty_only:
            split = y1  # no splits: one window
        spots, box = self.draw_window(cr, layout, (win[0], y0, win[2], split - y0), True, main)
        unfocused = {}
        if split < y1:
            unfocused, _box = self.draw_window(cr, layout, (win[0], split, win[2], y1 - split), False, other)
        self.draw_scrollbar(cr, box)
        if tab_h:
            # After the text, so a preview taller than the window tucks under the tab bar.
            mw = pts(s["tab_bar_margin_width"])
            self.draw_tabs(cr, layout, win[0] + mw, tab_y, win[2] - 2 * mw, tab_h, style, pl, self.cw)
        if "\0cursor" in unfocused:
            shape = s["cursor_shape_unfocused"]
            shape = s["cursor_shape"] if shape == "unchanged" else shape
            cr.set_source_rgb(*hex_rgb(c["cursor"]))
            if shape == "hollow":
                x, y, cw, lh = self.cursor_rect(*unfocused["\0cursor"], "block")
                cr.set_line_width(1)
                cr.rectangle(x + 0.5, y + 0.5, cw - 1, lh - 1)
                cr.stroke()
            else:
                cr.rectangle(*self.cursor_rect(*unfocused["\0cursor"], shape))
                cr.fill()
        # Where the focused cursor can be: after the command, or top left while the trail demo runs.
        self.cursor_spots = {k: self.cursor_rect(x, y, s["cursor_shape"]) for k, (x, y) in spots.items()}
        rect = self.cursor_spots.get(self.cursor_at) or self.cursor_spots.get("\0cursor")
        if rect:
            self.cursor.place(rect, c["cursor"])
        cr.restore()

    def border_width(self):
        # kitty rounds borders to whole pixels, at least one.
        return max(1.0, round(pts(self.app.settings["window_border_width"])))

    def cursor_rect(self, x, y, shape):
        s = self.app.settings
        if shape == "block":
            return (x, y, self.cw, self.lh)
        if shape == "beam":
            return (x, y, max(1.0, pts(s["cursor_beam_thickness"])), self.lh)
        th = max(1.0, pts(s["cursor_underline_thickness"]))
        return (x, y + self.lh - th, self.cw, th)

    def draw_titlebar(self, cr, win):
        """The title bar kitty draws on desktops without a tiling compositor; returns the window below it."""
        x, y, w, h = win
        th = 30
        cr.set_source_rgb(0.18, 0.18, 0.18)
        cr.rectangle(x, y, w, th)
        cr.fill()
        title = PangoCairo.create_layout(cr)
        title.set_markup(f"<b>{KITTY_SHELL or 'zsh'}</b>", -1)
        _ink, logical = title.get_pixel_extents()
        cr.set_source_rgb(0.87, 0.87, 0.87)
        cr.move_to(x + (w - logical.width) / 2, y + (th - logical.height) / 2)
        PangoCairo.show_layout(cr, title)
        title.set_text("✕", -1)
        cr.move_to(x + w - th, y + (th - logical.height) / 2)
        PangoCairo.show_layout(cr, title)
        return (x, y + th, w, h - th)

    def draw_window(self, cr, layout, rect, active, lines):
        """One kitty window: margin, border, padding, then its last lines that fit.

        Returns where the cursor markers landed and the box inside the margin.
        """
        s, c = self.app.settings, self.app.colors
        margin = pts(s["window_margin_width"])
        border = self.border_width()
        x, y, w, h = rect[0] + margin, rect[1] + margin, rect[2] - 2 * margin, rect[3] - 2 * margin
        color = c["active_border_color"] if active else c["inactive_border_color"]
        if s["draw_minimal_borders"] == "no" or margin > 0:
            # Full borders; kitty also draws these whenever there is a margin.
            if color:
                rounded_rect(cr, x + border / 2, y + border / 2, w - border, h - border,
                             pts(s["window_border_radius"]))
                cr.set_line_width(border)
                cr.set_source_rgb(*hex_rgb(color))
                cr.stroke()
        elif active:
            # Minimal borders: only the line between this window and the one below.
            cr.set_source_rgb(*hex_rgb(color or c["inactive_border_color"]))
            cr.rectangle(x, y + h - border, w, border)
            cr.fill()
        inset = border + pts(s["window_padding_width"])
        tx, ty, tw, th = x + inset, y + inset, max(0, w - 2 * inset), max(0, h - 2 * inset)
        rows = int(th / self.lh + 0.01)  # n rows of float height must give n back
        cr.save()
        cr.rectangle(tx, ty, tw, th)
        cr.clip()
        # Like a terminal, the newest lines stay and older ones scroll off the top.
        alpha = 1.0 if active else float(s["inactive_text_alpha"])
        spots = self.draw_lines(cr, layout, lines[-rows:] if rows else [], tx, ty, tx + tw, alpha)
        # The trail demo's far spot: the top left cell, so the jump crosses the whole window.
        spots["\0top"] = (tx, ty)
        cr.restore()
        return spots, (x, y, w, h)

    def draw_lines(self, cr, layout, lines, x0, y0, right, alpha):
        """Preview lines of (text, fg, bg, attrs) runs; returns where the \\0 markers are."""
        cw, lh, yoff = self.cw, self.lh, self.yoff
        spots = {}
        for i, line in enumerate(lines):
            y = y0 + i * lh
            x = x0
            for j, (text, fg, bg, attrs) in enumerate(line):
                if text == "\0cursor":
                    spots[text] = (x, y)
                    continue
                if text.startswith("\0right"):
                    # Right prompt: the rest of the line ends at the right padding. Like zsh,
                    # drop it when it would run into the command. A character after the marker
                    # fills the gap (Powerlevel10k's lines between left and right).
                    rest = sum(len(t) for t, *_ in line[j + 1:])
                    right_x = right - rest * cw
                    if right_x < x + cw:
                        break
                    fill = text[len("\0right"):]
                    if fill:
                        cr.set_source_rgba(*hex_rgb(fg), alpha)
                        self.draw_cells(cr, layout, fill * int((right_x - x) / cw), attrs, x, y)
                    x = right_x
                    continue
                n = len(text)
                if bg:
                    cr.set_source_rgb(*hex_rgb(bg))
                    cr.rectangle(x, y, n * cw, lh)
                    cr.fill()
                cr.set_source_rgba(*hex_rgb(fg), alpha)
                if text.isascii() and not self.per_cell:
                    layout.set_markup(self.markup(text, attrs), -1)
                    cr.move_to(x, y + yoff)
                    PangoCairo.show_layout(cr, layout)
                else:
                    self.draw_cells(cr, layout, text, attrs, x, y)
                x += n * cw
        return spots

    def markup(self, text, attrs, underline=True):
        """Pango markup for a run: kitty's bold/italic font settings, ligatures, underline."""
        s = self.app.settings
        out = GLib.markup_escape_text(text)
        bold, italic = "b" in attrs, "i" in attrs
        key = "bold_italic_font" if bold and italic else "bold_font" if bold else "italic_font" if italic else None
        face = s[key] if key else "auto"
        if face != "auto":
            # A family name, or a face like "JetBrainsMono Nerd Font ExtraBold" whose weight then
            # wins over <b> below.
            attr = "font_family" if face in self.app.fonts else "font_desc"
            out = f'<span {attr}="{GLib.markup_escape_text(face)}">{out}</span>'
        if bold:
            out = f"<b>{out}</b>"
        if italic:
            out = f"<i>{out}</i>"
        if underline and "u" in attrs:
            out = f"<u>{out}</u>"
        if s["disable_ligatures"] == "always":
            out = f'<span font_features="liga=0, calt=0, dlig=0">{out}</span>'
        return out

    def draw_scrollbar(self, cr, box):
        s, c = self.app.settings, self.app.colors
        mode = s["scrollbar"]
        # The preview counts as scrolled back, so only the mouse modes wait for a hover.
        if mode == "never" or mode in ("hovered", "scrolled-and-hovered") and not self.hover:
            return
        x, y, w, h = box
        border = self.border_width()
        sw = float(s["scrollbar_width"]) * self.cw
        sx = x + w - border - 0.1 * self.cw - sw
        top, height = y + border, h - 2 * border
        radius = min(float(s["scrollbar_radius"]) * self.cw, sw / 2)
        track = float(s["scrollbar_track_opacity"])
        if track:
            cr.set_source_rgba(*hex_rgb(c["foreground"]), track)
            rounded_rect(cr, sx, top, sw, height, radius)
            cr.fill()
        cr.set_source_rgba(*color_rgb(s["scrollbar_handle_color"], c), float(s["scrollbar_handle_opacity"]))
        rounded_rect(cr, sx, top + height * 0.45, sw, max(self.lh, height * 0.3), radius)
        cr.fill()

    def draw_cells(self, cr, layout, text, attrs, x, y):
        """One character per cell, squeezing wide glyphs (Nerd Font icons) into one cell like kitty."""
        cw, lh, yoff = self.cw, self.lh, self.yoff
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
            if "u" in attrs:
                # Underline the whole cell, so letter-spaced text keeps a continuous line.
                cr.rectangle(x, y + yoff + layout.get_pixel_extents()[1].height - 2, cw, 1)
                cr.fill()
            layout.set_markup(self.markup(ch, attrs, underline=False), -1)
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
        tabs = [(f"1: {KITTY_SHELL or 'zsh'}", True), ("2: nvim", False), ("3: btop", False)]
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
            font_style = self.app.settings["active_tab_font_style" if active else "inactive_tab_font_style"]
            layout.set_markup(self.markup(label, ("b" if "bold" in font_style else "")
                                          + ("i" if "italic" in font_style else "")), -1)
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


class PluginPicker(Gtk.Window):
    """Searchable tick list of oh-my-zsh plugins, with what each does and the aliases it adds."""

    def __init__(self, win):
        super().__init__(transient_for=win, modal=True, title="oh-my-zsh plugins",
                         default_width=640, default_height=760)
        self.win = win
        header = Gtk.HeaderBar()
        done = Gtk.Button(label="Done")
        done.add_css_class("suggested-action")
        done.connect("clicked", lambda _b: self.close())
        header.pack_end(done)
        self.set_titlebar(header)

        self.search = Gtk.SearchEntry(placeholder_text="Search names and descriptions", hexpand=True)
        self.search.connect("search-changed", lambda _e: self.listbox.invalidate_filter())
        self.only_on = Gtk.CheckButton(label="Only ticked")
        self.only_on.connect("toggled", lambda _c: self.listbox.invalidate_filter())
        bar = Gtk.Box(spacing=10, margin_top=10, margin_bottom=6, margin_start=12, margin_end=12)
        bar.append(self.search)
        bar.append(self.only_on)
        hint = Gtk.Label(label="Ticked plugins load in new shells after Apply. Each plugin adds a little "
                               "to shell start-up time.", xalign=0, wrap=True, margin_start=12, margin_end=12)
        hint.add_css_class("dim-label")

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.set_filter_func(self.filter)
        self.rows = {}
        on = win.settings["z_plugins"]
        for name, (desc, aliases) in win.plugin_catalogue.items():
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["plugin-card"])
            check = Gtk.CheckButton(active=name in on)
            title = Gtk.Label(label=name, xalign=0)
            title.add_css_class("heading")
            check.set_child(title)
            check.connect("toggled", self.on_toggled, name)
            card.append(check)
            elsewhere = loaded_elsewhere(name, win.zsh_info) and name not in on
            if elsewhere:
                check.set_sensitive(False)
                desc = "Already loaded by your zsh config. " + desc
            if desc:
                card.append(Gtk.Label(label=desc, xalign=0, wrap=True, max_width_chars=70))
            if aliases:
                more = f" and {len(aliases) - 12} more" if len(aliases) > 12 else ""
                text = f"Adds {len(aliases)} alias{'es' if len(aliases) > 1 else ''}: {', '.join(aliases[:12])}{more}"
                extra = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=70, selectable=True)
                extra.add_css_class("dim-label")
                card.append(extra)
            row = Gtk.ListBoxRow(child=card, activatable=False)
            row.search_text = f"{name} {desc}".lower()
            row.check = check
            self.rows[name] = row
            self.listbox.append(row)
        if not self.rows:
            self.listbox.append(Gtk.Label(label="No oh-my-zsh plugins found.", margin_top=20))

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.listbox)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(bar)
        box.append(hint)
        box.append(scroll)
        self.set_child(box)

    def filter(self, row):
        if not hasattr(row, "check"):
            return True
        if self.only_on.get_active() and not row.check.get_active():
            return False
        return all(word in row.search_text for word in self.search.get_text().lower().split())

    def on_toggled(self, check, name):
        on = self.win.settings["z_plugins"]
        if check.get_active() and name not in on:
            on.append(name)
        elif not check.get_active() and name in on:
            on.remove(name)
        self.win.refresh()


SHARE_FILE_NAME = "shellac-share.txt"


class ShareWindow(Gtk.Window):
    """Copy or save a share code of the current settings, or paste or open one to load.

    Files are for chat apps: Discord sends text over 2000 characters as a file anyway, and a file
    arrives exactly as sent."""

    def __init__(self, win):
        super().__init__(transient_for=win, modal=True, title="Share settings", default_width=560)
        self.win = win
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=16, margin_bottom=16,
                      margin_start=16, margin_end=16)

        def text(label, heading=False):
            widget = Gtk.Label(label=label, xalign=0, wrap=True, max_width_chars=60)
            widget.add_css_class("heading" if heading else "dim-label")
            box.append(widget)
            return widget

        text("Your settings", heading=True)
        text("Everything picked here, also what isn't applied yet: kitty options, theme colours, prompt "
             "(with your whole starship.toml, including any [custom] commands and what they contain) and shell.")
        code = win.make_code()
        row = Gtk.Box(spacing=8)
        shown = Gtk.Entry(text=code, editable=False, hexpand=True)
        copy = Gtk.Button(label="Copy")
        copy.add_css_class("suggested-action")
        copy.connect("clicked", self.on_copy, code)
        save = Gtk.Button(label="Save to file…")
        save.connect("clicked", self.on_save, code)
        row.append(shown)
        row.append(copy)
        row.append(save)
        box.append(row)
        self.copied = text("")
        self.copied.set_visible(False)

        heading = text("Load a share code", heading=True)
        heading.set_margin_top(12)
        text("Loads the settings into this window. Nothing changes in kitty until you press Apply.")
        row = Gtk.Box(spacing=8)
        self.paste = Gtk.Entry(placeholder_text=f"Paste a code starting with {SHARE_PREFIX}", hexpand=True)
        self.paste.connect("activate", self.on_load)
        load = Gtk.Button(label="Load")
        load.connect("clicked", self.on_load)
        open_btn = Gtk.Button(label="Open file…")
        open_btn.connect("clicked", self.on_open)
        row.append(self.paste)
        row.append(load)
        row.append(open_btn)
        box.append(row)
        self.error = text("")
        self.error.set_visible(False)
        self.set_child(box)

    def on_copy(self, _btn, code):
        self.get_clipboard().set_content(Gdk.ContentProvider.new_for_bytes(
            "text/plain;charset=utf-8", GLib.Bytes.new(code.encode())))
        self.copied.set_label("Copied. Send it to anyone with Shellac.")
        self.copied.set_visible(True)

    def pick_file(self, saving, done):
        """Ask for a file to save to or open, then call done(path); nothing on cancel."""
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Save share code" if saving else "Open share code")

            def finished(dlg, result):
                try:
                    picked = dlg.save_finish(result) if saving else dlg.open_finish(result)
                except GLib.Error:
                    return  # cancelled
                if picked and picked.get_path():
                    done(picked.get_path())
            if saving:
                dialog.set_initial_name(SHARE_FILE_NAME)
                dialog.save(self, None, finished)
            else:
                dialog.open(self, None, finished)
            return
        # GTK before 4.10.
        action = Gtk.FileChooserAction.SAVE if saving else Gtk.FileChooserAction.OPEN
        self.chooser = Gtk.FileChooserNative.new("Save share code" if saving else "Open share code", self,
                                                 action, "Save" if saving else "Open", "Cancel")
        if saving:
            self.chooser.set_current_name(SHARE_FILE_NAME)

        def response(chooser, answer):
            if answer == Gtk.ResponseType.ACCEPT and chooser.get_file() and chooser.get_file().get_path():
                done(chooser.get_file().get_path())
        self.chooser.connect("response", response)
        self.chooser.show()

    def on_save(self, _btn, code):
        def save(path):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(code + "\n")
            except OSError as e:
                self.copied.set_label(f"Could not save: {e}")
            else:
                self.copied.set_label(f"Saved to {path}. Send the file; they load it with Open file….")
            self.copied.set_visible(True)
        self.pick_file(True, save)

    def on_open(self, _btn):
        def opened(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read(SHARE_MAX_CHARS + 1000)
            except OSError as e:
                self.error.set_label(f"Could not open the file: {e}")
                self.error.set_visible(True)
                return
            self.paste.set_text(text.strip())
            self.on_load(None)
        self.pick_file(False, opened)

    def on_load(self, _widget):
        try:
            data = read_share_code(self.paste.get_text())
        except ValueError as e:
            self.error.set_label(str(e))
            self.error.set_visible(True)
            return
        self.close()
        self.win.load_share(data, self.paste.get_text())


class ImportCheck(Gtk.Window):
    """What in a share code doesn't fit this PC, with installs to tick, before loading the rest."""

    def __init__(self, win, notes, installs, on_done, heading=None, ok_label=None, cancel_label="Cancel",
                 on_cancel=None, options=()):
        super().__init__(transient_for=win, modal=True, title="Load share code", default_width=520)
        self.on_done, self.on_cancel = on_done, on_cancel
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=16, margin_bottom=16,
                      margin_start=16, margin_end=16)
        heading = heading or ("Some of this share code doesn't fit this PC:" if notes else
                              "This share code uses something that needs installing:" if installs else
                              "Before loading this share code:")
        head = Gtk.Label(label=heading, xalign=0, wrap=True, max_width_chars=60)
        head.add_css_class("heading")
        box.append(head)
        for note in notes:
            box.append(Gtk.Label(label=f"• {note}", xalign=0, wrap=True, max_width_chars=60))
        self.checks = {}
        for key, text, default in options:
            check = Gtk.CheckButton(active=default)
            check.set_child(Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=58))
            self.checks[key] = check
            box.append(check)
        for name, why in installs:
            check = Gtk.CheckButton(label=f"Install {name}: {why}", active=True)
            # Prefixed: the "starship" option (use their starship.toml) must not read as an install.
            self.checks[f"install_{name}"] = check
            box.append(check)
        if installs:
            tip = Gtk.Label(label="Installing may ask for your password.", xalign=0)
            tip.add_css_class("dim-label")
            box.append(tip)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        cancel = Gtk.Button(label=cancel_label)
        cancel.connect("clicked", self.on_cancel_clicked)
        go = Gtk.Button(label=ok_label or ("Load the rest" if notes else "Load"))
        go.add_css_class("suggested-action")
        go.connect("clicked", self.on_go)
        buttons.append(cancel)
        buttons.append(go)
        box.append(buttons)
        self.set_child(box)
        self.set_default_widget(go)
        go.grab_focus()

    def on_go(self, _btn):
        self.close()
        self.on_done([name for name, check in self.checks.items() if check.get_active()])

    def on_cancel_clicked(self, _btn):
        self.close()
        if self.on_cancel:
            self.on_cancel()


class StylerWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Shellac", default_width=1350, default_height=780)
        self.themes = load_themes()
        # Themes made from a share code's colours: {"Shared: …": conf text}.
        self.shared_themes = {}
        self.fonts = mono_fonts()
        self.prompt_choices = prompt_choices()
        CHOICE_NOTES["prompt_mode"] = dict(PROMPT_MODES)
        if KITTY_SHELL:
            CHOICE_NOTES["prompt_mode"]["base"] = f"Your own {KITTY_SHELL} prompt"
        # Real prompts drawn by the shell or starship, per prompt mode; None when that failed.
        self.renders = {}
        self.render_gen = 0
        self.load_current()
        self.plugin_catalogue = None
        css = Gtk.CssProvider()
        if hasattr(css, "load_from_string"):
            css.load_from_string(CSS)
        else:
            css.load_from_data(CSS, -1)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        header = Gtk.HeaderBar()
        apply_btn = Gtk.Button(label=f"Apply to {TERMINAL.label}")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self.on_apply)
        reset_btn = Gtk.Button(label="Reset to current")
        reset_btn.connect("clicked", self.on_reset)
        share_btn = Gtk.Button(label="Share…", tooltip_text="Copy these settings as a code, or load someone's code")
        share_btn.connect("clicked", lambda _b: ShareWindow(self).present())
        try_btn = Gtk.Button(label="Try in kitty",
                             tooltip_text="Open a kitty window with these settings, without saving them")
        try_btn.connect("clicked", self.on_try)
        header.pack_end(apply_btn)
        if TERMINAL.kitty_only:
            header.pack_end(try_btn)
        header.pack_start(reset_btn)
        header.pack_start(share_btn)
        self.set_titlebar(header)

        grid = Gtk.Grid(row_spacing=10, column_spacing=12, margin_top=16, margin_bottom=16,
                        margin_start=16, margin_end=16)
        self.dropdowns = {}
        # Row names, for what a share code couldn't load.
        self.row_labels = {}
        row = 0
        terminals = available_terminals()
        if len(terminals) > 1:
            label = Gtk.Label(label="Terminal", xalign=0)
            label.add_css_class("heading")
            grid.attach(label, 0, row, 1, 1)
            dd = Gtk.DropDown.new_from_strings([TERMINALS[n].label for n in terminals])
            dd.set_selected(terminals.index(TERMINAL.name))
            dd.connect("notify::selected", self.on_terminal, terminals)
            grid.attach(dd, 1, row, 1, 1)
            row += 1
        rows = [("Theme", "theme", list(self.themes))]
        for heading, opts in OPTION_GROUPS:
            group = [("Font", "font_family", self.fonts)] if heading == "Text" else []
            for label, key, choices, _d in opts:
                if TERMINAL.supports(key):
                    group.append((label, key, TERMINAL.choices(
                        key, ["auto", *self.fonts] if choices is FONT_CHOICES else choices)))
                if not TERMINAL.kitty_only:
                    continue
                if key == "cursor_trail" and len(TRAIL_STYLES) > 1:
                    group.append(("Trail style", "trail_style", list(TRAIL_STYLES)))
                if key == "tab_bar_align":
                    group.append(("Tab bar style", "tab_bar", list(TAB_STYLES)))
                if key == "hide_window_decorations" and len(EFFECTS) > 1:
                    group.append(("Effect", "effect", list(EFFECTS)))
            if group:
                rows += [(heading, None, None), *group]
        if self.prompt_choices:
            rows.append(("Prompt", None, None))
            rows.append(("Prompt", "prompt_mode", self.prompt_choices))
            if "styled" in self.prompt_choices:
                rows += [(label, key, list(choices) if key else None)
                         for label, key, choices, _d, _n in PROMPT_OPTIONS]
        if ZSH_AVAILABLE:
            has = dict(self.zsh_info, colours=self.zsh_info["hl"] or self.zsh_info["suggest"])
            rows.append(("Shell (zsh)", None, None))
            if has["omz"]:
                rows.append(("Plugins (oh-my-zsh)", "z_plugins", None))
            rows += [(label, key, choices) for label, key, choices, _n, need in ZSH_OPTIONS
                     if need is None or has[need]]
        # Everything below the prompt switch; hidden while it is off.
        self.prompt_widgets = []
        # Rows hidden while the setting they depend on is off.
        self.dep_widgets = {parent: [] for parent in DEPENDS}
        # Install notes for missing extras: name -> [(box, label, button)].
        self.extra_boxes = {}
        in_prompt = False
        for label, key, choices in rows:
            if key is None:
                top = label in TOP_HEADINGS
                head = Gtk.Label(label=label, xalign=0, margin_top=12)
                head.add_css_class("heading" if top else "dim-label")
                grid.attach(head, 0, row, 2, 1)
                if top:
                    in_prompt = label.startswith("Prompt")
                elif in_prompt:
                    self.prompt_widgets.append(head)
                row += 1
                if label.startswith("Shell"):
                    note = Gtk.Label(label='Apply writes these into .zshrc (backed up first). "Leave as is" '
                                           "writes nothing, so your own zsh config decides.",
                                     xalign=0, wrap=True, max_width_chars=34)
                    note.add_css_class("dim-label")
                    grid.attach(note, 0, row, 2, 1)
                    row += 1
                continue
            if key == "z_plugins":
                grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
                btn = Gtk.Button(label="Choose plugins…")
                btn.connect("clicked", self.on_plugins)
                grid.attach(btn, 1, row, 1, 1)
                row += 1
                self.plugins_note = Gtk.Label(xalign=0, wrap=True, max_width_chars=34)
                self.plugins_note.add_css_class("dim-label")
                grid.attach(self.plugins_note, 0, row, 2, 1)
                row += 1
                continue
            name_label = Gtk.Label(label=label, xalign=0)
            grid.attach(name_label, 0, row, 1, 1)
            notes = CHOICE_NOTES.get(key, {})
            dd = Gtk.DropDown.new_from_strings([notes.get(ch, ch) for ch in choices])
            dd.choices = choices
            self.row_labels[key] = label
            dd.set_hexpand(True)
            if len(choices) > 15 or key == "theme":
                dd.set_enable_search(True)
                dd.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
            # The button's label sized itself to the picked text, so stepping through a list moved
            # the previous/next buttons out from under the mouse. Fixed width, long names ellipsized;
            # the open list still shows them in full. Set after the search expression: setting that
            # puts the default factory back.
            dd.set_factory(label_factory(ellipsize=True))
            dd.set_list_factory(label_factory(ellipsize=False))
            dd.connect("notify::selected", self.on_changed, key)
            self.dropdowns[key] = dd
            stepped = len(choices) > 6 or key == "theme"
            if stepped:
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
            if in_prompt and key != "prompt_mode":
                self.prompt_widgets += [name_label, step if stepped else dd]
            row += 1
            if key == "prompt_mode":
                self.prompt_note = Gtk.Label(xalign=0, wrap=True, max_width_chars=34)
                self.prompt_note.add_css_class("dim-label")
                grid.attach(self.prompt_note, 0, row, 2, 1)
                row += 1
                if not STARSHIP_BIN:
                    grid.attach(self.extra_box("starship", "Styling the prompt needs starship."), 0, row, 2, 1)
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
            parent = DEPENDENT.get(key)
            if parent:
                self.dep_widgets[parent] += [name_label, step if stepped else dd]
            if key in ("trail_style", "effect") and not SLANGC:
                box = self.extra_box("slangc", SLANG_NOTE)
                grid.attach(box, 0, row, 2, 1)
                if parent:
                    self.dep_widgets[parent].append(box)
                row += 1
            note = ROW_NOTES.get(key)
            if note:
                note = Gtk.Label(label=note, xalign=0, wrap=True, max_width_chars=34)
                note.add_css_class("dim-label")
                grid.attach(note, 0, row, 2, 1)
                if parent:
                    self.dep_widgets[parent].append(note)
                row += 1
        self.status = Gtk.Label(xalign=0, wrap=True, max_width_chars=34, selectable=True)
        grid.attach(self.status, 0, row, 2, 1)
        row += 1
        if updater:
            row = self.add_update_rows(grid, row)

        side = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=440, hexpand=False)
        side.set_child(grid)
        self.preview = Preview(self)
        self.preview.cursor = Cursor()
        self.preview.trail = Trail()
        overlay = Gtk.Overlay(child=self.preview)
        overlay.add_overlay(self.preview.trail)
        overlay.add_overlay(self.preview.cursor)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        box.append(side)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        box.append(overlay)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self.make_update_banner())
        outer.append(box)
        self.set_child(outer)

        self.blink_id = None
        self.jump_id = None
        self.history_have = None
        self.sync_dropdowns()
        self.ensure_render()
        if updater:
            self.check_update()
        # Install queue of the last share code load, and its code while starship needs a restart.
        self.share_installs = []
        self.share_code = None
        self.font_names = None
        if os.path.exists(PENDING_IMPORT):
            code = read(PENDING_IMPORT)
            os.remove(PENDING_IMPORT)
            try:
                GLib.idle_add(self.load_share, read_share_code(code), code)
            except ValueError:
                pass

    # ---- updates ----
    def make_update_banner(self):
        """'vX is available' with an Update button, across the top of the window."""
        self.update_banner = Gtk.Revealer()
        box = Gtk.Box(spacing=12, css_classes=["update-banner"])
        self.update_banner_label = Gtk.Label(hexpand=True, wrap=True, margin_top=8, margin_bottom=8,
                                             margin_start=12)
        btn = Gtk.Button(label="Update", valign=Gtk.Align.CENTER, margin_end=12)
        btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda _b: self.install_update())
        box.append(self.update_banner_label)
        box.append(btn)
        self.update_banner.set_child(box)
        return self.update_banner

    def add_update_rows(self, grid, row):
        self.updates = updater.Updater(REPO, VERSION, os.path.dirname(os.path.realpath(__file__)),
                                       UPDATE_STATE)
        self.latest = None       # a newer release, once the check finds one
        self.installing = False
        self.installed = False   # installed, by this window or an earlier one; a restart starts it
        head = Gtk.Label(label="Updates", xalign=0, margin_top=12)
        head.add_css_class("heading")
        grid.attach(head, 0, row, 2, 1)
        row += 1
        grid.attach(Gtk.Label(label="Install updates automatically", xalign=0), 0, row, 1, 1)
        self.auto_update_switch = Gtk.Switch(active=self.updates.auto, halign=Gtk.Align.START,
                                             valign=Gtk.Align.CENTER)
        self.auto_update_switch.connect("notify::active", self.on_auto_update)
        grid.attach(self.auto_update_switch, 1, row, 1, 1)
        row += 1
        note = Gtk.Label(label="Off: the app tells you about a new version and waits for you to press "
                               "Update. Checks GitHub once a day.", xalign=0, wrap=True, max_width_chars=34)
        note.add_css_class("dim-label")
        grid.attach(note, 0, row, 2, 1)
        row += 1
        grid.attach(Gtk.Label(label=f"Version {VERSION}", xalign=0), 0, row, 1, 1)
        self.check_btn = Gtk.Button(label="Check now", halign=Gtk.Align.START)
        self.check_btn.connect("clicked", lambda _b: self.check_update(force=True))
        self.restart_btn = Gtk.Button(label="Restart", halign=Gtk.Align.START, visible=False)
        self.restart_btn.add_css_class("suggested-action")
        self.restart_btn.connect("clicked", lambda _b: os.execv(
            sys.executable, [sys.executable, os.path.realpath(__file__)]))
        buttons = Gtk.Box(spacing=8)
        buttons.append(self.check_btn)
        buttons.append(self.restart_btn)
        grid.attach(buttons, 1, row, 1, 1)
        row += 1
        self.update_note = Gtk.Label(xalign=0, wrap=True, max_width_chars=34)
        self.update_note.add_css_class("dim-label")
        grid.attach(self.update_note, 0, row, 2, 1)
        return row + 1

    def on_auto_update(self, switch, _pspec):
        self.updates.auto = switch.get_active()
        if self.updates.auto and self.latest and not self.updates.failed(self.latest):
            self.install_update()

    def show_installed(self, tag):
        self.installed = True
        self.update_banner.set_reveal_child(False)
        self.update_note.set_label(f"{tag} installed. Restart to use it; changes not applied yet are lost.")
        self.check_btn.set_visible(False)
        self.restart_btn.set_visible(True)

    def check_update(self, force=False):
        if self.installing or self.installed:
            return
        self.update_note.set_label("Checking…")

        def work():
            try:
                latest, error = self.updates.check(force), None
            except (OSError, ValueError, KeyError) as e:  # offline, GitHub down, odd reply
                latest, error = None, e
            GLib.idle_add(found, latest, error)

        def found(latest, error):
            self.latest = latest
            if error:
                # only a check you asked for complains; the daily one stays quiet offline
                self.update_note.set_label("Couldn't reach GitHub." if force else "")
            elif not latest:
                self.update_note.set_label("Up to date.")
            elif self.updates.installed(latest):
                self.show_installed(latest["tag"])
            else:
                self.update_note.set_label(f"{latest['tag']} is available.")
                if self.updates.auto and not self.updates.dev_checkout \
                        and not self.updates.failed(latest):
                    self.install_update()
                else:
                    self.update_banner_label.set_label(f"Shellac {latest['tag']} is available")
                    self.update_banner.set_reveal_child(True)
            return False

        threading.Thread(target=work, daemon=True).start()

    def install_update(self):
        if self.installing or self.installed or not self.latest:
            return
        if self.updates.dev_checkout:
            self.status.set_label("Running from a git checkout: update it with git pull.")
            return
        latest = self.latest
        self.installing = True
        self.update_banner.set_reveal_child(False)
        self.update_note.set_label(f"Installing {latest['tag']}…")

        def work():
            try:
                self.updates.install(latest)
                error = None
            except (OSError, RuntimeError) as e:  # network, disk, installer: shown to the user
                error = str(e)
            GLib.idle_add(finished, error)

        def finished(error):
            self.installing = False
            if error:
                self.update_note.set_label(f"Couldn't update: {error}")
                return False
            self.show_installed(latest["tag"])
            return False

        threading.Thread(target=work, daemon=True).start()

    # ---- state ----
    def load_current(self):
        conf = TERMINAL.read_conf()
        # The colours the terminal has without our theme file, for "Keep current theme".
        self.base_colors = TERMINAL.read_conf(without_theme=True)
        s = {"font_family": conf.get("font_family", "monospace")}
        for _label, key, _choices, default in OPTIONS:
            s[key] = conf.get(key, default)
        style = conf.get("tab_bar_style", "fade")
        pl = conf.get("tab_powerline_style", "angled")
        s["tab_bar"] = next((k for k, v in TAB_STYLES.items() if v == (style, pl) or (v[0] == style and v[1] is None)),
                            "fade")
        shaders = conf.get("custom_shaders", "").split()
        s["trail_style"] = shader_pick(shaders, TRAIL_STYLES)[1] or "plain"
        s["effect"] = shader_pick(shaders, EFFECTS)[1] or "none"
        s.update(TERMINAL.neutral or {})
        s["theme"] = BASE_LABEL
        name, theme_text = TERMINAL.theme()
        if name and name.startswith(SHARED_THEME) and name not in self.themes:
            self.add_shared_theme(name, theme_text)
        if name in self.themes:
            s["theme"] = name
        s.update(load_prompt_settings())
        styled = PROMPT_BEGIN in read(STARSHIP_CONF).splitlines()
        uses_starship = (not KITTY_SHELL or own_starship(KITTY_SHELL)
                         or strip_switch(read(SHELL_RC[KITTY_SHELL]).splitlines())[1])
        mode = ("styled" if styled else "starship") if uses_starship else "base"
        s["prompt_mode"] = mode if mode in self.prompt_choices or not self.prompt_choices \
            else self.prompt_choices[0]
        self.zsh_info = {"omz": False, "hl": False, "suggest": False}
        if ZSH_AVAILABLE:
            shell, self.zsh_info = load_shell()
            s.update(shell)
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
        for widget in self.prompt_widgets:
            widget.set_visible(self.settings["prompt_mode"] == "styled")
        for parent in DEPENDS:
            self.show_dependent(parent)
        self.syncing = False
        self.refresh()

    def on_terminal(self, dd, _pspec, terminals):
        name = terminals[dd.get_selected()]
        if name == TERMINAL.name:
            return
        # Everything (rows, themes, the shell, Noctalia) depends on the terminal: build a new window
        # from its config. Changes not applied yet are dropped, like Reset does.
        set_terminal(name, remember=True)
        app = self.get_application()
        self.destroy()
        StylerWindow(app).present()

    def on_step(self, _btn, dd, delta):
        dd.set_selected((dd.get_selected() + delta) % len(dd.choices))

    def extra_box(self, name, text):
        """A note saying an option needs an extra program, with a button that installs it."""
        box = Gtk.Box(spacing=8)
        label = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=26, hexpand=True)
        label.add_css_class("dim-label")
        btn = Gtk.Button(label="Install", valign=Gtk.Align.CENTER)
        btn.connect("clicked", self.on_install, name)
        box.append(label)
        box.append(btn)
        self.extra_boxes.setdefault(name, []).append((box, label, btn))
        return box

    def on_install(self, _btn, name):
        # One at a time: two pacman runs at once fail on pacman's lock.
        for boxes in self.extra_boxes.values():
            for _box, _label, btn in boxes:
                btn.set_sensitive(False)
        for _box, _label, btn in self.extra_boxes.get(name, []):
            btn.set_label("Installing…")
        self.status.set_label(f"Installing {name}…")

        def work():
            try:
                ok, msg = install_extra(name)
            except Exception as e:  # noqa: BLE001 - the button must not stay stuck on "Installing…"
                ok, msg = False, f"Could not install {name}: {e}"
            GLib.idle_add(self.on_installed, name, ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def on_installed(self, name, ok, msg):
        global SLANGC
        self.status.set_label(msg)
        for boxes in self.extra_boxes.values():
            for _box, _label, btn in boxes:
                btn.set_sensitive(True)
        for box, label, btn in self.extra_boxes.get(name, []):
            if not ok:
                btn.set_label("Install")
            elif not find_extra(name):
                # Installed into ~/.local/bin, which is not on PATH: the message says what to do.
                btn.set_label("Install")
            elif name == "slangc":
                box.set_visible(False)
            else:
                # The prompt choices are worked out at start, so starship shows up after a restart.
                label.set_label("starship is installed. Restart Shellac to style the prompt; "
                                "changes not applied yet are lost.")
                btn.set_label("Restart")
                btn.disconnect_by_func(self.on_install)
                btn.connect("clicked", lambda _b: os.execv(sys.executable, [sys.executable,
                                                                           os.path.abspath(sys.argv[0])]))
        if name == "slangc":
            SLANGC = find_extra("slangc")
        if name == "starship" and self.share_code:
            if ok and find_extra("starship"):
                code = self.share_code

                def restart(_installs):
                    write_atomic(PENDING_IMPORT, code)
                    os.execv(sys.executable, [sys.executable, os.path.abspath(sys.argv[0])])
                ImportCheck(self, [("Shellac restarts, then loads the share code again with its prompt "
                                    "settings. Changes not applied yet are lost.")], [], restart,
                            heading="starship is installed.", ok_label="Restart now", cancel_label="Later",
                            on_cancel=lambda: self.status.set_label(
                                "starship is installed. Load the share code again after restarting Kitty "
                                "Styler to get its prompt settings.")).present()
            self.share_code = None
        if self.share_installs:
            self.on_install(None, self.share_installs.pop(0))
        return False

    # ---- share codes ----
    def check_import(self, data):
        """Split a share code into values to load and notes on what doesn't fit this PC.

        Returns (values, notes, installs, options, pending): options are ticks for the load dialog,
        pending what they decide. Only values the app offers itself get through: the dropdown's own
        choices, numbers and colours for kitty options, installed fonts, themes and plugins, colour
        lines for a shared theme, and a starship.toml that is valid TOML."""
        incoming, values, notes, installs, skipped = data["settings"], {}, [], [], {}
        options, pending = [], {}
        numeric = {key for _l, key, _c, _d in OPTIONS}
        for key, value in incoming.items():
            if key == "theme":
                self.check_theme(value, data, values, notes, options, pending)
                continue
            if key == "z_plugins":
                if not isinstance(value, list):
                    continue
                if not (ZSH_AVAILABLE and self.zsh_info["omz"]):
                    notes.append("oh-my-zsh isn't set up here, so its plugins are left out.")
                    continue
                if self.plugin_catalogue is None:
                    self.plugin_catalogue = omz_plugins(self.zsh_info)
                have = [v for v in value if isinstance(v, str) and v in self.plugin_catalogue
                        and not loaded_elsewhere(v, self.zsh_info)]
                missing = [v for v in value if isinstance(v, str) and v not in self.plugin_catalogue]
                values[key] = have
                if missing:
                    notes.append(f"oh-my-zsh plugins not installed here, left out: {', '.join(missing)}.")
                continue
            dd = self.dropdowns.get(key)
            if dd is None:
                group = "prompt" if key == "prompt_mode" or key.startswith("p_") else \
                        "shell" if key.startswith("z_") else "kitty"
                skipped.setdefault(group, []).append(key)
                continue
            # dd.choices also holds this PC's own kitty.conf values, which are safe to take back.
            pick = next((ch for ch in dd.choices if isinstance(value, str) and same(ch, value)), None)
            if pick is None and key in numeric and isinstance(value, str) and SHARE_NUMBER.fullmatch(value):
                pick = value
            if pick is None and key in FONT_KEYS and isinstance(value, str) and value.isprintable() \
                    and len(value) < 200:
                if self.font_names is None:
                    self.font_names = font_names()
                if value.lower() in self.font_names:
                    pick = value
            if pick is None:
                label = self.row_labels.get(key, key)
                what = "isn't installed here" if key in FONT_KEYS else "isn't available here"
                shown = " ".join(str(value).split())
                shown = shown if len(shown) <= 40 else shown[:39] + "…"
                notes.append(f'{label}: "{shown}" {what}. Yours stays.')
                continue
            values[key] = pick
        if not SLANGC and (TRAIL_STYLES.get(values.get("trail_style")) or EFFECTS.get(values.get("effect"))):
            installs.append(("slangc", EXTRAS["slangc"]["what"]))
        if "prompt" in skipped:
            if incoming.get("prompt_mode") in ("styled", "starship") and not STARSHIP_BIN:
                notes.append("The prompt settings need starship, which isn't installed.")
                installs.append(("starship", "then Shellac restarts and loads the prompt settings"))
            else:
                notes.append("The prompt settings are left out: Shellac can't style this PC's shell prompt.")
        if "shell" in skipped:
            notes.append("Some shell settings are left out: zsh, or the zsh plugin they change, isn't "
                         "set up here.")
        if "kitty" in skipped:
            notes.append(f"Some {TERMINAL.label} options are left out: this Shellac or "
                         f"{TERMINAL.label} doesn't have them.")
        if incoming.get("prompt_mode") == "base" and "prompt_mode" in values:
            notes.append("They use their shell's own prompt, which a share code can't carry. You get your "
                         "shell's own prompt.")
        starship = data.get("starship")
        if isinstance(starship, str) and starship.strip() and incoming.get("prompt_mode") in ("starship", "styled"):
            try:
                config = tomllib.loads(starship)
            except tomllib.TOMLDecodeError:
                notes.append("Their starship.toml can't be read, so it is left out.")
            else:
                options.append(("starship", ("Use their starship.toml for the prompt. Yours is backed up when "
                                            "you press Apply."), True))
                runs = starship_runs(config)
                if runs:
                    shown = ", ".join(runs[:6]) + (f" and {len(runs) - 6} more" if len(runs) > 6 else "")
                    options.append(("starship_runs", (f"Also keep the parts of it that run programs ({shown}). "
                                                     "Only tick this if you trust them."), False))
                pending["starship"] = (starship, config, runs)
        return values, notes, installs, options, pending

    def check_theme(self, value, data, values, notes, options, pending):
        """The theme: by name when this PC has it, else a theme made from the code's colours."""
        colors = color_snapshot(data.get("colors"))
        if isinstance(value, str) and value in self.themes and not value.startswith(SHARED_THEME):
            values["theme"] = value
            return
        if not colors:
            # A code without colours (Kitty Styler 1.2.0).
            if value is None and data.get("noctalia") and TERMINAL.noctalia:
                values["theme"] = BASE_LABEL
            elif value is None:
                notes.append("The sharer's colours aren't in the code. Your colours stay.")
            else:
                notes.append(f'The theme "{value}" isn\'t available here. Your colours stay.')
            return
        if isinstance(value, str) and value.isprintable() and len(value) < 80:
            label = value.removeprefix(SHARED_THEME)
        else:
            label = "their Noctalia colours" if data.get("noctalia") else "their colours"
        text = "".join(f"{k} {v}\n" for k, v in colors.items())
        ask = value is None and data.get("noctalia") and TERMINAL.noctalia
        if ask:
            options.append(("exact_colours", ("Use their exact colours. Otherwise Noctalia picks colours from "
                                              "your wallpaper, like it does for them."), True))
        pending["theme"] = (SHARED_THEME + label, text, ask)

    def load_share(self, data, code):
        theme = data["settings"].get("theme")
        if isinstance(theme, str) and theme not in self.themes:
            # Read the theme list again (it can change while the app runs) before calling it missing.
            self.on_reload_themes(None)
        values, notes, installs, options, pending = self.check_import(data)
        if not notes and not installs and not options:
            self.use_share(values, [], code, pending)
        else:
            ImportCheck(self, notes, installs, lambda chosen: self.use_share(values, chosen, code, pending),
                        options=options).present()
        return False

    def use_share(self, values, chosen, code, pending):
        if "theme" in pending:
            name, text, ask = pending["theme"]
            if ask and "exact_colours" not in chosen:
                values["theme"] = BASE_LABEL
            else:
                self.add_shared_theme(name, text)
                values["theme"] = name
        self.settings.pop("shared_starship", None)
        if "starship" in pending and "starship" in chosen:
            text, config, runs = pending["starship"]
            if runs and "starship_runs" not in chosen:
                text = "\n".join(["# Shared through Shellac; the parts that run programs were left out.",
                                  *toml_dump(drop_options(config, runs))]).strip() + "\n"
            values["shared_starship"] = text
        self.renders.pop("starship", None)
        self.render_gen += 1
        installs = [name[len("install_"):] for name in chosen if name.startswith("install_")]
        self.settings.update(values)
        self.sync_dropdowns()
        self.ensure_render()
        self.status.set_label("Loaded the share code. Press Apply to keep it"
                              + (", or Try in kitty to test it first." if TERMINAL.kitty_only else "."))
        # starship last: it ends in a restart.
        queue = sorted(installs, key=lambda name: name == "starship")
        if "starship" in queue:
            self.share_code = code
        if queue:
            self.share_installs = queue[1:]
            self.on_install(None, queue[0])

    def on_changed(self, dd, _pspec, key):
        if self.syncing:
            return
        self.settings[key] = dd.choices[dd.get_selected()]
        if key == "prompt_mode":
            for widget in self.prompt_widgets:
                widget.set_visible(self.settings[key] == "styled")
            self.ensure_render()
        self.refresh()
        if key in DEPENDS:
            self.show_dependent(key)

    def show_dependent(self, parent):
        on = not same(self.settings[parent], DEPENDS[parent][0])
        for widget in self.dep_widgets[parent]:
            widget.set_visible(on)

    def ensure_render(self):
        """Start drawing the real prompt for the picked mode in the background, once."""
        mode = self.settings["prompt_mode"]
        if mode == "styled" or mode in self.renders:
            return
        if mode == "base":
            job = (render_shell_prompt, KITTY_SHELL)
        else:
            # starship.toml as it is without our prompt block, which Apply removes in this mode.
            job = (render_starship, strip_prompt_block(self.settings.get("shared_starship") or read(STARSHIP_CONF)))
        self.renders[mode] = "pending"
        gen = self.render_gen

        def work():
            result = job[0](job[1])
            GLib.idle_add(self.on_rendered, gen, mode, result)

        threading.Thread(target=work, daemon=True).start()
        self.refresh()

    def on_rendered(self, gen, mode, result):
        if gen == self.render_gen:
            self.renders[mode] = result
            self.refresh()
        return False

    def on_plugins(self, _btn):
        if self.plugin_catalogue is None:
            self.plugin_catalogue = omz_plugins(self.zsh_info)
        PluginPicker(self).present()

    def add_shared_theme(self, name, text):
        self.shared_themes[name] = text
        self.themes[name] = text

    def on_reload_themes(self, _btn):
        self.themes = load_themes()
        self.themes.update(self.shared_themes)
        dd = self.dropdowns["theme"]
        self.syncing = True
        dd.choices[:] = list(self.themes)
        dd.get_model().splice(0, dd.get_model().get_n_items(), dd.choices)
        self.syncing = False
        self.sync_dropdowns()

    def theme_conf(self):
        """The kitty settings of the picked theme (for the base theme: Noctalia's, or kitty.conf's)."""
        name = self.settings["theme"]
        if name != BASE_LABEL:
            return parse_conf(self.themes[name])
        return TERMINAL.noctalia_colors() if TERMINAL.noctalia else self.base_colors

    def make_code(self):
        starship = None
        if self.settings.get("prompt_mode") in ("starship", "styled"):
            starship = self.settings.get("shared_starship") or strip_styler_starship(read(STARSHIP_CONF))
        # Colour options the app has rows for (cursor_trail_color) travel as settings; in the theme
        # they would override the row after Apply.
        options = {key for _l, key, _c, _d in OPTIONS}
        colors = {k: v for k, v in color_snapshot(self.theme_conf()).items() if k not in options}
        # Settings this terminal doesn't have only hold values that turn them off; leave them out.
        settings = {k: v for k, v in self.settings.items() if TERMINAL.supports(k)}
        return make_share_code(settings, colors, starship or None)

    def refresh(self):
        name = self.settings["theme"]
        conf = self.theme_conf()
        colors = dict(KITTY_COLORS)
        colors.update({k: v for k, v in conf.items() if k in colors and v.startswith("#")})
        if conf.get("active_border_color") == "none":
            colors["active_border_color"] = None
        self.colors = colors
        if len(self.themes) == 1:
            note = ("No kitty themes found. Run  kitty +kitten themes  once (Esc quits it) so kitty downloads "
                    "its theme collection, then press Reload themes.")
        elif name == BASE_LABEL:
            note = ""
        elif TERMINAL.noctalia:
            note = (f"{TERMINAL.label.capitalize()} and the starship prompt will stop following Noctalia's "
                    "wallpaper colours. Pick Noctalia again to go back.")
        else:
            note = TERMINAL.theme_note()
        self.theme_note.set_label(note)
        self.theme_note.set_visible(bool(note))
        if self.prompt_choices:
            self.prompt_note.set_label(self.prompt_note_text())
        self.reload_btn.set_visible(len(self.themes) == 1)
        if ZSH_AVAILABLE and self.zsh_info["omz"]:
            on = self.settings["z_plugins"]
            note = f"On: {' '.join(on)}" if on else "None ticked."
            if not on and self.zsh_info["default_if_empty"]:
                note += f" Your zsh config then loads its default: {' '.join(self.zsh_info['plugins'])}."
            self.plugins_note.set_label(note)
        if self.blink_id:
            GLib.source_remove(self.blink_id)
            self.blink_id = None
        self.preview.cursor.set_opacity(1)
        interval = float(self.settings["cursor_blink_interval"])
        if interval != 0:
            ms = 500 if interval < 0 else int(interval * 1000)
            self.blink_id = GLib.timeout_add(ms, self.blink)
            self.blink_since = time.monotonic()
        # With a cursor trail, the cursor jumps to the top left corner and back to show it.
        trail = self.settings["cursor_trail"] != "0"
        if trail and not self.jump_id:
            self.jump_id = GLib.timeout_add(1500, self.jump)
        elif not trail and self.jump_id:
            GLib.source_remove(self.jump_id)
            self.jump_id = None
            self.preview.cursor_at = "\0cursor"
        self.preview.queue_draw()

    def prompt_note_text(self):
        mode = self.settings["prompt_mode"]
        rc = SHELL_RC_NAMES.get(KITTY_SHELL)
        switch = KITTY_SHELL and not own_starship(KITTY_SHELL)
        render = self.renders.get(mode, "pending")
        if render == "pending":
            seen = "Reading your prompt…"
        elif render is None and mode != "styled":
            seen = "Could not read your prompt, so the preview shows a sample one."
        else:
            seen = ""
        if mode == "base":
            name = render[0] if isinstance(render, tuple) else ""
            name = name if name and not name.endswith(" prompt") else "its own prompt"
            out = f"{TERMINAL.label.capitalize()} starts {KITTY_SHELL}, and its prompt is {name}. Apply leaves it as it is."
            if strip_switch(read(SHELL_RC[KITTY_SHELL]).splitlines())[1]:
                out += f" Apply removes Shellac's starship lines from {rc}."
        else:
            out = ("Your starship.toml as it is." if mode == "starship" else
                   "Apply writes a prompt block into starship.toml (backed up first).")
            if switch:
                out += f" Apply adds a few lines to {rc} so {KITTY_SHELL} uses starship; " \
                       f'"{CHOICE_NOTES["prompt_mode"]["base"]}" takes them out again.'
        return " ".join(x for x in (out, seen) if x)

    def blink(self):
        cursor = self.preview.cursor
        stop = float(self.settings["cursor_stop_blinking_after"])
        if stop > 0 and time.monotonic() - self.blink_since > stop:
            # kitty stops blinking after that long without typing; changing a setting counts as typing.
            cursor.set_opacity(1)
            self.blink_id = None
            return False
        cursor.set_opacity(0 if cursor.get_opacity() else 1)
        return True

    def jump(self):
        pv = self.preview
        spots = pv.cursor_spots
        new = "\0top" if pv.cursor_at == "\0cursor" else "\0cursor"
        if "\0top" not in spots or "\0cursor" not in spots:
            return True
        old_rect, new_rect = spots[pv.cursor_at], spots[new]
        pv.cursor_at = new
        pv.cursor.place(new_rect, self.colors["cursor"])
        pv.cursor.set_opacity(1)
        s = self.settings
        tx, ty = (int(v) for v in (s["cursor_trail_start_threshold"].split() * 2)[:2])
        if abs(new_rect[0] - old_rect[0]) / pv.cw >= tx or abs(new_rect[1] - old_rect[1]) / pv.lh >= ty:
            color = self.colors["cursor"] if s["cursor_trail_color"] == "none" else s["cursor_trail_color"]
            fast, slow = (float(v) for v in (s["cursor_trail_decay"].split() * 2)[:2])
            pv.trail.start(old_rect, new_rect, (fast, slow), color_rgb(color, self.colors), s.get("trail_style", "plain"))
        return True

    def sample_lines(self):
        c = self.colors
        fg = c["foreground"]
        sel = (c["selection_foreground"], c["selection_background"])
        k = [c[f"color{i}"] for i in range(16)]

        p = self.settings
        real = self.renders.get(p["prompt_mode"])
        real = real if isinstance(real, tuple) else None
        if p["prompt_mode"] != "styled":
            # Shown until the real prompt is read, or when that failed: ours with its defaults.
            p = dict(p, **{key: default for _l, key, _c, default, _n in PROMPT_KEYS})

        def spec(value, default):
            """A colour from the rendered prompt: theme slot 0-15, hex, or None for the default."""
            return default if value is None else k[value] if isinstance(value, int) else value

        def real_runs(runs):
            return [(text, spec(f, fg), spec(b, None), attrs) for text, f, b, attrs in runs]

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

        hl = self.zsh_info["hl"]
        z = {key: self.settings.get(key, KEEP) for _l, key, _s, _d in ZSH_HL}
        z_default = {key: default for _l, key, _s, default in ZSH_HL}

        def zcol(key):
            """(colour, attrs) zsh-syntax-highlighting uses for this kind of word."""
            color, attrs = z_default[key]
            color = color if z[key] == KEEP else z[key]
            return (col(color) if color else fg), attrs

        def typed(cmd, unknown=PREVIEW_UNKNOWN):
            """The command line as zsh-syntax-highlighting colours it."""
            out, first = [], True
            for tok in re.findall(r'"[^"]*"|\'[^\']*\'|\s+|[^\s"\']+', cmd):
                key = None
                if tok.isspace():
                    pass
                elif first:
                    key, first = ("z_unknown" if tok in unknown else "z_cmd"), False
                elif tok[0] in "\"'":
                    key = "z_string"
                elif tok.startswith("-"):
                    key = "z_option"
                elif "/" in tok or tok.startswith("~"):
                    key = "z_path"
                color, attrs = zcol(key) if key and hl else (fg, "")
                out.append((tok, color, None, attrs))
            return out

        def command(cmd, cursor, ghost, comment, unknown):
            """What follows the prompt: the typed command, cursor, suggestion and a comment."""
            out = typed(cmd, unknown)
            if cursor:
                out.append(("\0cursor", fg, None, ""))
            if ghost and self.zsh_info["suggest"]:
                out.append((ghost, zcol("z_suggest")[0], None, ""))
            if comment:
                out.append((f"  # {comment}", k[8], None, ""))
            return out

        def prompt(cmd, cursor=False, error=None, slow=False, ghost="", comment="", unknown=PREVIEW_UNKNOWN):
            """The prompt as preview lines: optional blank line, then one or two lines."""
            if real:
                rows, right = real[1][int(error or 0)]
                lines = [real_runs(row) for row in rows] or [[]]
                lines[-1] += command(cmd, cursor, ghost, comment, unknown)
                if right:
                    lines[-1] += [("\0right", fg, None, "")] + real_runs(right)
                return lines
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
                      (" ", fg, None, ""), *command(cmd, cursor, ghost, comment, unknown)]
            right = [("\0right", fg, None, "")]
            if error and p["p_status"] == "show":
                right += seg(f"✘ {error}", p["p_status_color"])
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

        # Examples of the zsh behaviour settings that are not left as is.
        z = self.settings if ZSH_AVAILABLE else {}
        demos = []
        if z.get("z_case") == "insensitive":
            demos += prompt("cd Documents/", comment='typed "cd doc", then Tab')
        elif z.get("z_case") == "sensitive":
            demos += prompt("cd doc", comment="Tab finds nothing: the folder is Documents/")
        if z.get("z_correct") == "off":
            demos += [*prompt("gti status"), [t("zsh: command not found: gti")]]
        elif z.get("z_correct") == "commands":
            demos += [*prompt("gti status"), [t("zsh: correct 'gti' to 'git' [nyae]? "), t("y", fg, "b")]]
        elif z.get("z_correct") == "all":
            demos += [*prompt("ls Docments"), [t("zsh: correct 'Docments' to 'Documents' [nyae]? "), t("y", fg, "b")]]
        if z.get("z_autocd") == "on":
            demos += prompt("Documents", comment="same as cd Documents", unknown=())
        elif z.get("z_autocd") == "off":
            demos += [*prompt("Documents", unknown={"Documents"}), [t("zsh: command not found: Documents")]]
        if z.get("z_history", KEEP) != KEEP:
            if self.history_have is None:
                self.history_have = history_lines()
            # Apply leaves the size alone when that would cut the history file down.
            ok = int(z["z_history"]) >= self.history_have
            demos += [*prompt("echo $SAVEHIST"), [t(z["z_history"] if ok else "(unchanged)"), t(
                "  # commands kept in ~/.zsh_history" if ok else
                f"  # your history already has {self.history_have} lines", k[8])]]
        if z.get("z_share") == "on":
            demos += [*prompt("fc -ln -1"), [t("make deploy"), t("  # typed in another kitty window", k[8])]]
        elif z.get("z_share") == "off":
            demos += [*prompt("fc -ln -1"), [t("ls -la ~/code"), t("  # only this window's commands", k[8])]]
        if self.zsh_info["suggest"] and z.get("z_suggest_src") == "history completion":
            demos += prompt("cd Doc", ghost="uments/", comment="suggested by completion")
        elif self.zsh_info["suggest"] and z.get("z_suggest_src") == "history":
            demos += prompt("cd Doc", comment="no suggestion: not in your history yet")
        if z.get("z_beep") in ("on", "off"):
            demos += prompt("cd xyz", comment="Tab finds nothing: " + ("beep" if z["z_beep"] == "on" else "no beep"))

        inactive = [
            *prompt("cat todo.txt"),
            [t("- try a new theme")],
            [t("- fix the build")],
            *prompt("", cursor=True),
        ]
        return [
            *prompt("ls -la ~/code"),
            [t("drwxr-xr-x  "), t("themes/", k[12], "b"), t("   "), t("run.sh", k[10], "b"), t("   "),
             t("shellac.py", k[2]), t("   README.md   "), t("backup.tar.gz", k[1], "b"),
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
            *prompt("sl", error="1", slow=True),
            [t(NOT_FOUND.get(KITTY_SHELL, NOT_FOUND["zsh"]))],
            *demos,
            *prompt("git sta", cursor=True, error="127", ghost="tus"),
        ], inactive

    # ---- actions ----
    def on_reset(self, _btn):
        self.load_current()
        self.renders.clear()
        self.render_gen += 1
        self.sync_dropdowns()
        self.ensure_render()
        self.status.set_label(f"Reset to what {os.path.basename(TERMINAL.conf)} has now.")

    def on_try(self, btn):
        btn.set_sensitive(False)
        self.status.set_label("Opening a kitty window with these settings…")
        settings, colors = dict(self.settings), dict(self.colors)
        themes = {settings["theme"]: self.themes[settings["theme"]]} if settings["theme"] in self.shared_themes else {}

        def work():
            error = try_in_kitty(settings, colors, themes)
            GLib.idle_add(done, error)

        def done(error):
            btn.set_sensitive(True)
            msg = "Opened a kitty window with these settings. Nothing was saved; press Apply to keep them."
            if TRAIL_STYLES.get(settings.get("trail_style")) or EFFECTS.get(settings.get("effect")):
                # kitty compiles a shader the first time it is used and doesn't respond meanwhile.
                msg += (" The first time an effect or trail style is used, kitty compiles it, so the new"
                        " window can stay blank or show as not responding for a few seconds.")
            self.status.set_label(error or msg)
            return False
        threading.Thread(target=work, daemon=True).start()

    def on_apply(self, _btn):
        try:
            msg = apply_all(self.settings, self.themes, self.colors, self.file_conf)
            # Written now; a later Apply keeps whatever starship.toml has then.
            self.settings.pop("shared_starship", None)
        except OSError as e:
            msg = f"Could not save: {e}"
        self.file_conf = TERMINAL.read_conf()
        self.status.set_label(msg)
        self.renders.clear()
        self.render_gen += 1
        self.ensure_render()
        self.refresh()

def same(a, b):
    try:
        return float(a) == float(b)
    except ValueError:
        return a == b


# Share codes: "ks2:" + base62(zlib(JSON)). The number goes up if the format changes, so
# older codes keep working. Letters and digits only: chat apps turn text between two underscores
# into italics and drop them, which broke ks1 codes (base64url). A share code comes from someone
# else: import only takes values the app offers itself (see StylerWindow.check_import), never raw
# text that ends up in a config file.
SHARE_PREFIX = "ks2:"
SHARE_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
FONT_KEYS = ("font_family", "bold_font", "italic_font", "bold_italic_font")
SHARE_MAX_CHARS = 20000
SHARE_MAX_JSON = 200000
# How far apart the two underscores a chat app ate from a ks1 code may be, for the repair.
KS1_ITALIC_SPAN = 120
# Numbers and colours kitty.conf can hold that aren't in our lists, like font_size 13.5,
# "2.0 2.0" or #dde1e6.
SHARE_NUMBER = re.compile(r"-?\d+(\.\d+)?(pt|px|%)?( -?\d+(\.\d+)?(pt|px|%)?){0,3}|#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?")
# Theme colours a share code may carry: colour keys only (kitty also has keys like "shell"), and
# only colour values.
SHARE_COLOR_KEY = re.compile(r"[a-z0-9_]*(color|foreground|background)[a-z0-9_]*")
SHARE_COLOR = re.compile(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?|none")
SHARED_THEME = "Shared: "
# starship.toml options that make starship run a program: the [custom] modules, plus these keys
# and any *_binary key in the other modules (starship print-config --default, 1.27).
STARSHIP_RUNS = ("command", "commands", "when", "use_stdin", "shell")
# Where an import waits while Shellac restarts after installing starship.
PENDING_IMPORT = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), "shellac-import")


def color_snapshot(conf):
    """The colour lines of a kitty theme, as {key: value}; anything else is left out."""
    if not isinstance(conf, dict):
        return {}
    return {k: v for k, v in conf.items()
            if isinstance(k, str) and isinstance(v, str) and SHARE_COLOR_KEY.fullmatch(k) and SHARE_COLOR.fullmatch(v)}


def strip_styler_starship(text):
    """starship.toml without Shellac's prompt and palette blocks, for a share code."""
    out, block = [], None
    for line in strip_prompt_block(text).splitlines():
        if line == STARSHIP_BEGIN:
            block = True
        elif block:
            block = line != STARSHIP_END
        elif line.strip() != STARSHIP_PALETTE_LINE:
            out.append(line)
    return "\n".join(out).strip() + "\n" if any(ln.strip() for ln in out) else ""


def starship_runs(config):
    """Dotted names of the starship.toml options that would run a program."""
    found = [f"custom.{name}" for name in config.get("custom", {})] if isinstance(config.get("custom"), dict) else \
        (["custom"] if "custom" in config else [])

    def walk(table, path):
        for key, value in table.items():
            if path and (key in STARSHIP_RUNS or key.endswith("_binary")):
                found.append(".".join((*path, key)))
            elif isinstance(value, dict):
                walk(value, (*path, key))
    walk({k: v for k, v in config.items() if k != "custom"}, ())
    return found


def drop_options(config, dotted):
    """A copy of a parsed TOML table without the given dotted option names."""
    out = {}
    for key, value in config.items():
        if key in dotted:
            continue
        inner = [d[len(key) + 1:] for d in dotted if d.startswith(key + ".")]
        if isinstance(value, dict) and inner:
            value = drop_options(value, inner)
            if not value:
                continue  # a table that only held dropped options, like [custom]
        out[key] = value
    return out


def toml_key(key):
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key, ensure_ascii=False)


def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{toml_key(k)} = {toml_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # JSON string escapes are valid TOML ones
    return str(value)  # dates and times


def toml_dump(table, path=()):
    """TOML text for a parsed table (comments are gone; tomllib keeps none)."""
    lines = [f"{toml_key(k)} = {toml_value(v)}" for k, v in table.items() if not isinstance(v, dict)]
    for key, value in table.items():
        if isinstance(value, dict):
            name = (*path, key)
            lines += ["", "[" + ".".join(toml_key(n) for n in name) + "]", *toml_dump(value, name)]
    return lines


def make_share_code(settings, colors=None, starship=None):
    """The settings as a share code, with the theme's colours and the starship.toml the prompt
    uses, so the result looks the same on the other PC. The base theme travels as None: its label
    differs per PC."""
    data = {k: v for k, v in settings.items() if k != "shared_starship"}
    if data.get("theme") == BASE_LABEL:
        data["theme"] = None
    payload = {"settings": data, "noctalia": TERMINAL.noctalia, "colors": colors or {}}
    if starship:
        payload["starship"] = starship
    blob = json.dumps(payload, separators=(",", ":")).encode()
    # zlib output starts with 0x78, so no leading zero bytes get lost in the number.
    n, digits = int.from_bytes(zlib.compress(blob, 9), "big"), []
    while n:
        n, d = divmod(n, 62)
        digits.append(SHARE_DIGITS[d])
    return SHARE_PREFIX + "".join(reversed(digits))


def unpack_share(packed):
    """The JSON object in zlib bytes, or None when they are damaged or cut short."""
    try:
        unzip = zlib.decompressobj()
        blob = unzip.decompress(packed, SHARE_MAX_JSON)
        if unzip.unconsumed_tail or not unzip.eof:
            return None
        data = json.loads(blob)
        data["settings"].keys()  # a dict inside a dict, or one of these errors
    except (ValueError, zlib.error, KeyError, TypeError, AttributeError):
        return None
    return data


def ks1_bytes(body):
    try:
        return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except ValueError:
        return b""


def read_share_code(text):
    """The {"settings": ..., "noctalia": ...} in a share code; ValueError says what is wrong."""
    code = "".join(text.split())
    if not code:
        raise ValueError("Paste a share code first.")
    if len(code) > SHARE_MAX_CHARS:
        raise ValueError("That is too long to be a share code.")
    data = None
    if code.startswith(SHARE_PREFIX):
        body = code[len(SHARE_PREFIX):]
        if body and all(ch in SHARE_DIGITS for ch in body):
            n = 0
            for ch in body:
                n = n * 62 + SHARE_DIGITS.index(ch)
            data = unpack_share(n.to_bytes((n.bit_length() + 7) // 8, "big"))
    elif code.startswith("ks1:"):
        # Kitty Styler 1.2.0 codes. If a chat app ate one pair of underscores, put it back.
        body = code[4:]
        data = unpack_share(ks1_bytes(body))
        for i in range(len(body) + 1) if data is None else ():
            for j in range(i, min(i + KS1_ITALIC_SPAN, len(body)) + 1):
                data = unpack_share(ks1_bytes(f"{body[:i]}_{body[i:j]}_{body[j:]}"))
                if data is not None:
                    break
            if data is not None:
                break
    elif re.match(r"ks\d+:", code):
        raise ValueError("That share code is from a newer Shellac. Update this one first.")
    else:
        raise ValueError(f'That is not a share code; they start with "{SHARE_PREFIX}".')
    if data is None:
        raise ValueError("That share code is damaged or incomplete. Copy it again.")
    return data


def prune_backups(conf=CONF):
    """Delete all but the newest KEEP_BACKUPS <conf>.bak-<time> files; other files are left alone."""
    folder, name = os.path.split(conf)
    pattern = re.compile(re.escape(name) + r"\.bak-\d{8}-\d{6}$")
    for old in sorted(f for f in os.listdir(folder) if pattern.match(f))[:-KEEP_BACKUPS]:
        os.remove(os.path.join(folder, old))


def apply_all(s, themes, colors, file_conf, reload=True):
    """Write the terminal's config, the theme file and starship.toml from the settings; returns a status message."""
    backup = TERMINAL.write(s, themes, file_conf)
    picked = [EFFECTS.get(s["effect"]), TRAIL_STYLES.get(s["trail_style"])]
    if reload:
        TERMINAL.reload()
    msg = [f"Applied and reloaded open {TERMINAL.label} windows." if TERMINAL.live_reload
           else f"Applied to {TERMINAL.label}.", *getattr(TERMINAL, "notes", [])]
    if backup:
        msg.append(f"Backup: {backup}")
    if any(picked) and not SLANGC:
        msg.append(SLANG_NOTE + " Until it is installed they do nothing.")
    mode = s.get("prompt_mode")
    if s.get("shared_starship") and mode in ("starship", "styled"):
        # From a share code. Written before the prompt and palette blocks, which go on top of it.
        backup = None
        if os.path.exists(STARSHIP_CONF):
            backup = f"{STARSHIP_CONF}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(STARSHIP_CONF, backup)
        write_atomic(STARSHIP_CONF, s["shared_starship"])
        msg.append("starship.toml replaced with the shared one." + (f" Backup: {backup}" if backup else ""))
    if STARSHIP_AVAILABLE and mode:
        msg.append(apply_prompt(s if mode == "styled" else None))
    if KITTY_SHELL and mode:
        msg.append(apply_switch(KITTY_SHELL, mode != "base" and bool(STARSHIP_BIN)
                                and not own_starship(KITTY_SHELL)))
    # starship.toml is shared by every terminal; only kitty's theme moves its palette for now.
    if TERMINAL.kitty_only and (TERMINAL.noctalia or STARSHIP_BEGIN in read(STARSHIP_CONF).splitlines()):
        # Without Noctalia the prompt's colour names already follow kitty's colours; this
        # branch then only cleans up a palette left from before.
        msg.append(apply_starship(s["theme"] if TERMINAL.noctalia else BASE_LABEL, colors))
    if ZSH_AVAILABLE:
        msg.append(apply_zsh(s))
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
        "# Managed by Shellac; changes here are overwritten when you press Apply.",
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
        # Only this machine's OS: starship drops the whole module on a name it doesn't know,
        # and older versions lack some.
        *(toml_kv(name, OS_SYMBOLS[name]) for name in dict.fromkeys(["Linux", detect_os()])),
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
    tmp = f"{path}.shellac-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    if os.path.exists(path):
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def set_noctalia_starship(enable, dry_run=False):
    """Add or remove "starship" in Noctalia's [theme.templates] builtin_ids. Returns True if it (would have) changed."""
    text = read(NOCTALIA_SETTINGS)
    sec = re.search(r"^\s*\[theme\.templates\]\s*$", text, re.MULTILINE)
    if not sec:
        return False
    arr = re.compile(r"^(\s*)builtin_ids\s*=\s*\[(.*?)\]", re.MULTILINE | re.DOTALL).search(text, sec.end())
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
    noctalia = TERMINAL.noctalia_colors()
    slots = [f"color{i}" for i in range(16)] + ["foreground", "background"]
    m = re.search(r"^\[palettes\.noctalia\]\s*$(.*?)(?=^\[|^# <<<|\Z)", starship_text, re.MULTILINE | re.DOTALL)
    names = re.findall(r'^\s*(\w+)\s*=\s*"(#[0-9a-fA-F]{6})"', m.group(1), re.MULTILINE) if m else []
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
        return "Starship is back on Noctalia's colours." if TERMINAL.noctalia else "Starship palette put back."

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
    block = [STARSHIP_BEGIN, "# Written by Shellac; pick Noctalia in Shellac to undo."]
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


def expand_path(value):
    return os.path.expanduser(os.path.expandvars(value.strip().strip("\"'")))


def strip_zsh_blocks(lines):
    """.zshrc lines without our two blocks, the saved shell settings and the plugin list in our block."""
    kept, saved, plugins, block = [], {}, None, None
    for line in lines:
        if line in (ZSH_PLUGINS_BEGIN, ZSH_BEGIN):
            block = line
            # Drop the blank line that was added in front of the settings block.
            if line == ZSH_BEGIN and kept and not kept[-1].strip():
                kept.pop()
        elif block:
            if line in (ZSH_PLUGINS_END, ZSH_END):
                block = None
            elif line.startswith(ZSH_STATE):
                try:
                    saved = json.loads(line[len(ZSH_STATE):])
                except ValueError:
                    pass
            elif line.startswith("plugins=("):
                plugins = line[len("plugins=("):].rstrip(")").split()
        else:
            kept.append(line)
    return kept, saved, plugins


def zsh_setup(lines):
    """What .zshrc (without our blocks) does with oh-my-zsh.

    at: index of the line that loads oh-my-zsh, directly or through a distro config it sources
    (CachyOS's cachyos-config.zsh), or None. plugins: the list loaded now. default_if_empty: the
    loader fills in its own list when plugins is empty. text: .zshrc plus the loader, for detection.
    """
    info = {"at": None, "dir": None, "custom": None, "plugins": [], "default_if_empty": False}
    loader = ""
    for i, line in enumerate(lines):
        m = re.match(r"^\s*(?:source|\.)\s+(\S+)", line)
        if not m:
            continue
        path = m.group(1).strip("\"'")
        if path.endswith("oh-my-zsh.sh"):
            info["at"] = i
            break
        text = read(expand_path(path))
        if re.search(r"^\s*(?:source|\.)\s+\S*oh-my-zsh\.sh", text, re.MULTILINE):
            info["at"], loader = i, text
            break
    before = "\n".join(lines[:info["at"]] if info["at"] is not None else lines)
    info["text"] = "\n".join(lines) + "\n" + loader
    for name, var in (("dir", "ZSH"), ("custom", "ZSH_CUSTOM")):
        for text in (before, loader):
            found = re.findall(rf"^\s*(?:export\s+)?{var}=(\S+)", text, re.MULTILINE)
            if found:
                info[name] = expand_path(found[-1])
                break
    if not info["dir"] or not os.path.isdir(info["dir"]):
        info["dir"] = next((d for d in (os.path.expanduser("~/.oh-my-zsh"), "/usr/share/oh-my-zsh")
                            if os.path.isdir(d)), None)
    if info["dir"] and not info["custom"]:
        info["custom"] = os.path.join(info["dir"], "custom")
    plugins_re = re.compile(r"(?:^|&&)[ \t]*plugins=\(([^)]*)\)", re.MULTILINE | re.DOTALL)
    for text in (before, loader):
        found = list(plugins_re.finditer(text))
        if found:
            body = re.sub(r"#[^\n]*", "", found[-1].group(1))
            info["plugins"] = body.split()
            break
    info["default_if_empty"] = re.search(r"-z[^\n]*&&[ \t]*plugins=\(", before + "\n" + loader) is not None
    info["omz"] = info["at"] is not None and info["dir"] is not None
    info["hl"] = "zsh-syntax-highlighting" in info["text"]
    info["suggest"] = "zsh-autosuggestions" in info["text"]
    return info


def load_shell():
    """Shell settings from .zshrc and what it loads; plugins start from what the shell loads now."""
    kept, saved, plugins = strip_zsh_blocks(read(ZSHRC).splitlines())
    info = zsh_setup(kept)
    s = {key: KEEP for _l, key, *_r in ZSH_KEYS}
    s.update({k: v for k, v in saved.items() if k in s})
    s["z_plugins"] = plugins if plugins is not None else list(info["plugins"])
    return s, info


def readme_summary(path):
    """First paragraph of a plugin README as one line, without markdown."""
    para = []
    for line in read(path).splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "<", "![", "[!", "|", "```", "---", "===", ">")):
            if para:
                break
            continue
        para.append(s)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", " ".join(para))
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").replace("**", "")
    return text if len(text) <= 240 else text[:240].rsplit(" ", 1)[0] + " …"


def omz_plugins(info):
    """{name: (description, alias names)} for the oh-my-zsh and custom plugins, read at runtime."""
    out = {}
    for folder in (os.path.join(info["dir"], "plugins"), os.path.join(info["custom"], "plugins")):
        for script in glob.glob(os.path.join(folder, "*", "*.plugin.zsh")):
            plugin_dir = os.path.dirname(script)
            name = os.path.basename(plugin_dir)
            if name == "example" or os.path.basename(script) != f"{name}.plugin.zsh":
                continue
            aliases = re.findall(r"^\s*alias\s+(?:-[gs]\s+)?['\"]?([^=\s'\"]+)=", read(script), re.MULTILINE)
            out[name] = (readme_summary(os.path.join(plugin_dir, "README.md")), list(dict.fromkeys(aliases)))
    return dict(sorted(out.items()))


def loaded_elsewhere(name, info):
    """True when the shell config already sources this plugin itself (CachyOS does for a few)."""
    return re.search(rf"(?:source|\.)\s+\S*/(?:zsh-)?{re.escape(name)}[/.]", info["text"]) is not None


def zsh_style(value, default, style=None):
    """zsh-syntax-highlighting style for a colour choice, keeping the default's bold/underline."""
    color, attrs = default
    attrs += STYLE_ATTRS.get(style, "")
    color = color if value == KEEP else value
    parts = ["fg=" + COLOR_SLOTS[color][5:]] if color else []
    parts += [word for flag, word in (("b", "bold"), ("u", "underline")) if flag in attrs]
    return ",".join(parts) or "none"


def history_lines():
    """Lines in ~/.zsh_history (next to .zshrc), 0 when there is none."""
    try:
        with open(os.path.join(os.path.dirname(ZSHRC), ".zsh_history"), "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def zsh_lines(s, note):
    """The settings block body; empty when everything is left as is. note collects warnings."""
    out = []
    styles = []
    for _label, key, hl_styles, default in ZSH_HL:
        if s[key] == KEEP:
            continue
        if hl_styles is None:
            out.append(f"ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='{zsh_style(s[key], default)}'")
        else:
            styles += [f"ZSH_HIGHLIGHT_STYLES[{name}]='{zsh_style(s[key], default, name)}'" for name in hl_styles]
    if styles:
        out += ["typeset -gA ZSH_HIGHLIGHT_STYLES", *styles]
    case = {"insensitive": "'m:{[:lower:][:upper:]}={[:upper:][:lower:]}' 'r:|=*' 'l:|=* r:|=*'",
            "sensitive": "'r:|=*' 'l:|=* r:|=*'"}.get(s["z_case"])
    if case:
        out.append(f"zstyle ':completion:*' matcher-list {case}")
    out += {"off": ["unsetopt correct correct_all"], "commands": ["setopt correct", "unsetopt correct_all"],
            "all": ["setopt correct_all"]}.get(s["z_correct"], [])
    for key, option in (("z_autocd", "auto_cd"), ("z_share", "share_history"), ("z_beep", "beep")):
        if s[key] != KEEP:
            out.append(f"{'setopt' if s[key] == 'on' else 'unsetopt'} {option}")
    if s["z_history"] != KEEP:
        size = int(s["z_history"])
        have = history_lines()
        if size < have:
            # A smaller SAVEHIST cuts the history file down on the next save.
            note.append(f"History size not changed: your history already has {have} lines.")
        else:
            out.append(f"HISTSIZE={size} SAVEHIST={size}")
    if s["z_suggest_src"] != KEEP:
        out.append(f"ZSH_AUTOSUGGEST_STRATEGY=({s['z_suggest_src']})")
    return out


def zsh_syntax_ok(text):
    try:
        return subprocess.run(["zsh", "-n"], input=text, capture_output=True, text=True,
                              timeout=10, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def apply_zsh(s):
    """Write our .zshrc blocks from the settings, or remove them; returns a status line."""
    text = read(ZSHRC)
    kept, _saved, _plugins = strip_zsh_blocks(text.splitlines())
    info = zsh_setup(kept)
    out = list(kept)
    note = []
    plugins = s["z_plugins"]
    if info["omz"] and plugins != info["plugins"]:
        if plugins or not info["default_if_empty"]:
            out[info["at"]:info["at"]] = [ZSH_PLUGINS_BEGIN,
                                          "# Managed by Shellac; this list wins over any plugins= above.",
                                          f"plugins=({' '.join(plugins)})", ZSH_PLUGINS_END]
        else:
            note.append("Plugins not changed: with none ticked, your zsh config loads its own default list.")
    body = zsh_lines(s, note)
    if body:
        while out and not out[-1].strip():
            out.pop()
        state = {key: s[key] for _l, key, *_r in ZSH_KEYS}
        out += ["", ZSH_BEGIN, "# Managed by Shellac; changes here are overwritten when you press Apply.",
                ZSH_STATE + json.dumps(state), *body, ZSH_END]
    new = "\n".join(out) + "\n"
    if new == text or (not text.endswith("\n") and new == text + "\n"):
        return " ".join(note)
    # Only refuse when the file passed before, so an odd but working .zshrc doesn't block saving.
    if zsh_syntax_ok(text) and not zsh_syntax_ok(new):
        return "Shell settings not saved: .zshrc would not be valid zsh."
    if text and not os.path.exists(f"{ZSHRC}.bak-kitty-styler"):
        shutil.copy2(ZSHRC, f"{ZSHRC}.bak-kitty-styler")
    write_atomic(ZSHRC, new)
    return " ".join(note + ["Shell settings saved; they apply in new shells, or run  exec zsh  in an open one."])


# ---- Try in kitty: Apply on copies of the config files, then a kitty that uses the copies ----
TRY_ZSH_LINKS = (".zshenv", ".zprofile", ".zlogin", ".zlogout", ".zsh_history")


def try_paths(tmp):
    """Where each config file goes in a try directory, keyed by the env variable that points the app there."""
    return {"KITTY_STYLER_DIR": os.path.join(tmp, "kitty"),
            "KITTY_STYLER_STARSHIP": os.path.join(tmp, "starship.toml"),
            "KITTY_STYLER_NOCTALIA": os.path.join(tmp, "noctalia-settings.toml"),
            "KITTY_STYLER_ZSHRC": os.path.join(tmp, "zsh", ".zshrc"),
            "KITTY_STYLER_FISHRC": os.path.join(tmp, "config.fish"),
            "KITTY_STYLER_BASHRC": os.path.join(tmp, ".bashrc")}


def try_in_kitty(settings, colors, themes=None):
    """Open a kitty window with the settings as Apply would write them, without touching the real files.

    Copies the config files to a temp directory, runs Apply there in a child process (the paths come
    from env variables, read at import), then starts kitty on the copies. Returns an error message
    or "".
    """
    kitty = shutil.which("kitty")
    if not kitty:
        return "kitty was not found."
    tmp = tempfile.mkdtemp(prefix="shellac-try-", dir=os.environ.get("XDG_RUNTIME_DIR"))
    paths = try_paths(tmp)
    try:
        if os.path.isdir(KITTY_DIR):
            shutil.copytree(KITTY_DIR, paths["KITTY_STYLER_DIR"], ignore=shutil.ignore_patterns("*.bak-*"),
                            ignore_dangling_symlinks=True)
        for env, real in (("KITTY_STYLER_STARSHIP", STARSHIP_CONF), ("KITTY_STYLER_NOCTALIA", NOCTALIA_SETTINGS),
                          ("KITTY_STYLER_ZSHRC", ZSHRC), ("KITTY_STYLER_FISHRC", SHELL_RC["fish"]),
                          ("KITTY_STYLER_BASHRC", SHELL_RC["bash"])):
            os.makedirs(os.path.dirname(paths[env]), exist_ok=True)
            if os.path.isfile(real):
                shutil.copy2(real, paths[env])
        # With ZDOTDIR moved, zsh would skip the other startup files; link the real ones in.
        zdot = os.path.dirname(paths["KITTY_STYLER_ZSHRC"])
        for name in TRY_ZSH_LINKS:
            real = os.path.join(os.path.dirname(ZSHRC), name)
            if os.path.exists(real):
                os.symlink(real, os.path.join(zdot, name))
        with open(os.path.join(tmp, "try.json"), "w", encoding="utf-8") as f:
            json.dump({"settings": settings, "colors": colors, "themes": themes or {}}, f)
        env = dict(os.environ, KITTY_STYLER_SHELL=KITTY_SHELL or "", KITTY_STYLER_TERMINAL="kitty", **paths)
        r = subprocess.run([sys.executable, os.path.realpath(__file__), "--try", tmp], env=env,
                           capture_output=True, text=True, timeout=60, check=False)
        if r.returncode:
            raise OSError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                          else f"exit code {r.returncode}")
    except (OSError, subprocess.SubprocessError, shutil.Error) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return f"Could not open the try window: {e}"

    kdir = paths["KITTY_STYLER_DIR"]
    kenv = dict(os.environ, KITTY_CONFIG_DIRECTORY=kdir)
    if os.path.exists(paths["KITTY_STYLER_STARSHIP"]):
        kenv["STARSHIP_CONFIG"] = paths["KITTY_STYLER_STARSHIP"]
    cmd = [kitty, "--config", os.path.join(kdir, "kitty.conf"), "--class", "shellac-try",
           "--title", "kitty (try)"]
    if KITTY_SHELL == "zsh":
        kenv["ZDOTDIR"] = zdot
    elif KITTY_SHELL == "bash":
        cmd += ["bash", "--rcfile", paths["KITTY_STYLER_BASHRC"], "-i"]
    elif KITTY_SHELL == "fish":
        conf_d = os.path.join(os.path.dirname(SHELL_RC["fish"]), "conf.d")
        cmd += ["fish", "--no-config", "-C",
                f"for f in {shlex.quote(conf_d)}/*.fish; source $f; end; source {shlex.quote(paths['KITTY_STYLER_FISHRC'])}"]
    # The temp directory goes away when the window closes.
    subprocess.Popen(["sh", "-c", 'trap \'rm -rf "$0"\' EXIT; "$@"', tmp, *cmd], env=kenv,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return ""


def try_apply(tmp):
    """The child process of try_in_kitty: Apply to the copies in tmp. Refuses any real path."""
    tmp = os.path.realpath(tmp)
    if TERMINAL.name != "kitty":
        print(f"refusing: the terminal is {TERMINAL.name}, not kitty", file=sys.stderr)
        return 2
    for path in (CONF, STARSHIP_CONF, NOCTALIA_SETTINGS, *SHELL_RC.values()):
        if not os.path.realpath(path).startswith(tmp + os.sep):
            print(f"refusing: {path} is outside {tmp}", file=sys.stderr)
            return 2
    with open(os.path.join(tmp, "try.json"), encoding="utf-8") as f:
        data = json.load(f)
    themes = load_themes()
    themes.update(data.get("themes", {}))
    apply_all(data["settings"], themes, data["colors"], TERMINAL.read_conf(), reload=False)
    return 0


class StylerApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        (self.props.active_window or StylerWindow(self)).present()


def move_old_state():
    """Shellac was Kitty Styler until 1.4.0: its state folder (update settings, picked terminal) moves over once."""
    old, new = os.path.join(CONFIG_HOME, "kitty-styler"), os.path.dirname(UPDATE_STATE)
    if os.path.isdir(old) and not os.path.exists(new):
        try:
            os.rename(old, new)
        except OSError:
            pass


if __name__ == "__main__":
    move_old_state()
    # Used by install.sh: list the missing extras, or install them from a terminal (sudo, not pkexec).
    if sys.argv[1:2] == ["--missing-extras"]:
        for name in EXTRAS:
            # slangc only matters when this kitty has shaders to offer.
            if not find_extra(name) and (name != "slangc" or len(TRAIL_STYLES) > 1 or len(EFFECTS) > 1):
                print(f"{name}\t{EXTRAS[name]['what']}")
        sys.exit(0)
    if sys.argv[1:2] == ["--try"] and len(sys.argv) == 3:
        sys.exit(try_apply(sys.argv[2]))
    if sys.argv[1:2] == ["--install-extras"]:
        results = [install_extra(name, gui=False) for name in sys.argv[2:] or
                   [n for n in EXTRAS if not find_extra(n)]]
        print("\n".join(msg for _ok, msg in results))
        sys.exit(0 if all(ok for ok, _msg in results) else 1)
    sys.exit(StylerApp().run(sys.argv))
