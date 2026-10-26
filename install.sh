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

# Optional extras some options need (shader compiler, starship). Ask once; the app also has
# Install buttons for anything skipped here.
missing=$(python3 "$DEST/kitty-styler.py" --missing-extras 2>/dev/null || true)
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
        *) python3 "$DEST/kitty-styler.py" --install-extras \
               $(printf '%s\n' "$missing" | cut -f1) \
               || say "Some extras did not install; the app's Install buttons can try again." ;;
    esac
fi

say "Done. Open \"Kitty Styler\" from your app menu, or run: $NAME"
}
