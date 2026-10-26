#!/bin/sh
# Installs Shellac for the current user, or updates it when run again:
# copies the app to ~/.local/share/shellac and adds it to the app menu.
# Shellac was called Kitty Styler before 1.4.0; an old install is moved over.
# Run ./install.sh --uninstall to remove it again.
# Without cloning the repo:
#   curl -fsSL https://raw.githubusercontent.com/Soulmanyes/shellac/main/install.sh | sh
# The whole script is one { } block, so a download cut off midway runs nothing.
{
set -eu

NAME=shellac
APP_ID=io.github.soulmanyes.Shellac
OLD_NAME=kitty-styler
OLD_APP_ID=io.github.soulmanyes.KittyStyler
SRC=$(cd "$(dirname "$0")" && pwd)
DEST=${XDG_DATA_HOME:-$HOME/.local/share}/$NAME
APPS=${XDG_DATA_HOME:-$HOME/.local/share}/applications
BIN=$HOME/.local/bin
OLD_DEST=${XDG_DATA_HOME:-$HOME/.local/share}/$OLD_NAME

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

if [ "${1:-}" = "--uninstall" ]; then
    say "Removing Shellac"
    rm -rf "$DEST" "$APPS/$APP_ID.desktop" "$APPS/$NAME.desktop" "$BIN/$NAME"
    # what Kitty Styler left: its folder (with downloaded extras), launchers and menu entries
    for extra in "$BIN"/*; do
        case $(readlink "$extra" 2>/dev/null) in "$OLD_DEST"/*) rm -f "$extra" ;; esac
    done
    rm -rf "$OLD_DEST" "$APPS/$OLD_APP_ID.desktop" "$APPS/$OLD_NAME.desktop" "$BIN/$OLD_NAME"
    say "Done. Your terminal configs are unchanged."
    exit 0
fi

if [ ! -f "$SRC/shellac.py" ]; then
    # run through curl | sh: download the latest release (main if there is none yet) and
    # run its own install.sh. releases/latest redirects to .../tag/<tag>
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    say "Downloading Shellac"
    tag=$(curl -fsSLo /dev/null -w '%{url_effective}' \
        https://github.com/Soulmanyes/shellac/releases/latest | sed -n 's|.*/tag/||p')
    ref=${tag:+tags/$tag}
    curl -fsSL "https://github.com/Soulmanyes/shellac/archive/refs/${ref:-heads/main}.tar.gz" |
        tar -xz -C "$tmp" --strip-components=1
    sh "$tmp/install.sh" "$@" < /dev/tty
    exit
fi

if ! python3 - <<'PY' 2>/dev/null
import sys
assert sys.version_info >= (3, 11)
import cairo, gi
gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, PangoCairo
PY
then
    echo "Missing: Python 3.11+ with GTK 4, PyGObject and pycairo." >&2
    echo "On Arch: sudo pacman -S python-gobject python-cairo gtk4" >&2
    echo "On Debian/Ubuntu: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0" >&2
    exit 1
fi

say "Installing the app to $DEST"
mkdir -p "$DEST" "$APPS" "$BIN"
install -m 644 "$SRC/updater.py" "$DEST/"
install -m 755 "$SRC/shellac.py" "$DEST/"
ln -sf "$DEST/shellac.py" "$BIN/$NAME"

if [ -d "$OLD_DEST" ]; then
    say "Moving over from Kitty Styler"
    # A Kitty Styler that is still open restarts through its old path after updating, so that
    # path starts Shellac now. Its extras (downloaded slangc/starship) stay where their links point.
    cat > "$OLD_DEST/kitty-styler.py" <<STUB
#!/usr/bin/env python3
# Kitty Styler is now Shellac. This keeps old launchers working.
import os
import sys

os.execv(sys.executable, [sys.executable, "$DEST/shellac.py", *sys.argv[1:]])
STUB
    chmod 755 "$OLD_DEST/kitty-styler.py"
    rm -f "$OLD_DEST/updater.py" "$APPS/$OLD_APP_ID.desktop" "$APPS/$OLD_NAME.desktop"
    ln -sf "$DEST/shellac.py" "$BIN/$OLD_NAME"
fi

# named after the app ID so docks and taskbars match the window to this launcher;
# kitty-styler.desktop is the name the README used to suggest for a hand-made one
rm -f "$APPS/$NAME.desktop"
cat > "$APPS/$APP_ID.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Shellac
Comment=Style your terminal and shell prompt with a live preview
Exec=python3 $DEST/shellac.py
Icon=utilities-terminal
Categories=Settings;Utility;
StartupWMClass=$APP_ID
DESKTOP

# Optional extras some options need (shader compiler, starship). Ask once; the app also has
# Install buttons for anything skipped here.
missing=$(python3 "$DEST/shellac.py" --missing-extras 2>/dev/null || true)
if [ -n "$missing" ]; then
    say "Optional extras that some options need:"
    printf '%s\n' "$missing" | while IFS="	" read -r extra what; do
        printf '  %s: %s\n' "$extra" "$what"
    done
    printf 'Install them now? Arch-based systems use pacman (asks for your password); others get a download in ~/.local/bin. [Y/n] '
    # read from the terminal, so this also works when the script comes through curl | sh
    answer=n
    { read -r answer </dev/tty; } 2>/dev/null || { answer=n; echo; }
    case $answer in
        [nN]*) say "Skipped. The app shows an Install button where an option needs one." ;;
        *) python3 "$DEST/shellac.py" --install-extras \
               $(printf '%s\n' "$missing" | cut -f1) \
               || say "Some extras did not install; the app's Install buttons can try again." ;;
    esac
fi

say "Done. Open \"Shellac\" from your app menu, or run: $NAME"
}
