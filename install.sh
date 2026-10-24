#!/bin/sh
# Installs Kitty Styler for the current user, or updates it when run again:
# copies the app to ~/.local/share/kitty-styler and adds it to the app menu.
# Run ./install.sh --uninstall to remove it again.
# Without cloning the repo:
#   curl -fsSL https://raw.githubusercontent.com/Soulmanyes/kitty-styler/main/install.sh | sh
# The whole script is one { } block, so a download cut off midway runs nothing.
{
set -eu

NAME=kitty-styler
APP_ID=io.github.soulmanyes.KittyStyler
SRC=$(cd "$(dirname "$0")" && pwd)
DEST=${XDG_DATA_HOME:-$HOME/.local/share}/$NAME
APPS=${XDG_DATA_HOME:-$HOME/.local/share}/applications
BIN=$HOME/.local/bin

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

if [ "${1:-}" = "--uninstall" ]; then
    say "Removing Kitty Styler"
    rm -rf "$DEST" "$APPS/$APP_ID.desktop" "$APPS/$NAME.desktop" "$BIN/$NAME"
    say "Done. Your kitty config is unchanged."
    exit 0
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
if [ -f "$SRC/kitty-styler.py" ]; then
    install -m 755 "$SRC/kitty-styler.py" "$DEST/"
else
    # run through curl | sh: the app is one file, so fetch only that
    curl -fsSL -o "$DEST/kitty-styler.py.part" \
        https://raw.githubusercontent.com/Soulmanyes/kitty-styler/main/kitty-styler.py
    chmod 755 "$DEST/kitty-styler.py.part"
    mv "$DEST/kitty-styler.py.part" "$DEST/kitty-styler.py"
fi
ln -sf "$DEST/kitty-styler.py" "$BIN/$NAME"

# named after the app ID so docks and taskbars match the window to this launcher;
# kitty-styler.desktop is the name the README used to suggest for a hand-made one
rm -f "$APPS/$NAME.desktop"
cat > "$APPS/$APP_ID.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Kitty Styler
Comment=Pick kitty's theme, font and look with a live preview
Exec=python3 $DEST/kitty-styler.py
Icon=kitty
Categories=Settings;Utility;
StartupWMClass=$APP_ID
DESKTOP

say "Done. Open \"Kitty Styler\" from your app menu, or run: $NAME"
}
