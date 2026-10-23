# Kitty Styler

A small GTK4 app for picking how the [kitty](https://sw.kovidgoyal.net/kitty/) terminal looks: theme, font,
opacity, padding, cursor, line height, tab bar and, optionally, a [starship](https://starship.rs/) prompt.
Every change shows up in a live preview before anything is written.

![Kitty Styler with the Dracula theme](docs/screenshot.png)

## Features

- **Themes**: all of kitty's own theme collection, with a search box and previous/next buttons.
- **Kitty settings**: font, size, background opacity, window padding, cursor shape, blink and trail, line
  height, letter spacing, and tab bar position, alignment and style.
- **Live preview**: a mock terminal with a prompt, `ls`, a diff, an error, all 16 colours and the tab bar,
  drawn on your wallpaper when it can find one, so opacity is visible.
- **Starship prompt (optional)**: segment styles (plain, bubbles, arrows, slanted, blocks), one or two
  lines, and per-part colours for the OS icon, user and host, folder, git, error code, duration,
  language versions and clock. Off by default.
- **Noctalia (optional)**: if [Noctalia](https://github.com/noctalia-dev/noctalia) manages your kitty
  colours, the first theme entry follows its wallpaper colours, and the app works alongside Noctalia's
  templates.

## What it changes

- **kitty.conf**: settings already in the file are edited in place. New ones go into a block at the end,
  between `# BEGIN_KITTY_STYLER` and `# END_KITTY_STYLER`. That block is rebuilt on every Apply, so put
  your own settings outside it. Open kitty windows reload straight away.
- **Theme**: a picked theme is saved to `themes/kitty-styler.conf` next to kitty.conf and included from the
  block. Pick "Keep current theme" to drop it and go back to your own colours.
- **Backups**: before each Apply, kitty.conf is copied to `kitty.conf.bak-<date>-<time>`. The newest 5 are
  kept.
- **starship.toml**: untouched unless you turn on "Style the prompt". Then Apply writes a block between
  `# >>> KITTY STYLER PROMPT >>>` markers and makes a one-time backup, `starship.toml.bak-kitty-styler`.
  If your own config already sets the same things (for example a top-level `format` or a `[directory]`
  table), nothing is written and the window says why. Turning the switch off and applying removes the
  block again. Your prompt settings stay saved in one comment line.

Symlinked config files (from a dotfile manager) are written through the link.

## Requirements

- Linux with Python 3.11 or newer
- GTK 4, PyGObject and pycairo (Arch: `python-gobject python-cairo gtk4`; Debian/Ubuntu:
  `python3-gi python3-gi-cairo gir1.2-gtk-4.0`)
- fontconfig (`fc-list`), for the font list
- kitty
- Optional: starship, and a [Nerd Font](https://www.nerdfonts.com/) for the prompt icons

The themes come from kitty's own theme collection. If the list is empty, run `kitty +kitten themes` once and
press Esc to quit it: kitty downloads the collection into `~/.cache/kitty/kitty-themes.zip`. Then press
"Reload themes".

## Install

```sh
git clone https://github.com/Soulmanyes/kitty-styler.git
cd kitty-styler
python3 kitty-styler.py
```

For a menu entry, save this as `~/.local/share/applications/kitty-styler.desktop`, with the path changed
to where you cloned it:

```ini
[Desktop Entry]
Type=Application
Name=Kitty Styler
Comment=Pick kitty's theme, font and look with a live preview
Exec=python3 /path/to/kitty-styler/kitty-styler.py
Icon=kitty
Categories=Settings;Utility;
StartupWMClass=io.github.soulmanyes.KittyStyler
```

## Testing on copies

These environment variables point the app at other files, so you can try it without touching your real
config:

| Variable | Default |
| --- | --- |
| `KITTY_STYLER_DIR` | `~/.config/kitty` |
| `KITTY_STYLER_STARSHIP` | `$STARSHIP_CONFIG`, else `~/.config/starship.toml` |
| `KITTY_STYLER_NOCTALIA` | `~/.local/state/noctalia/settings.toml` |
| `KITTY_STYLER_THEMES` | `~/.cache/kitty/kitty-themes.zip` |

Apply still sends a reload signal to every running kitty.

## Tested on

CachyOS with Hyprland, kitty, starship 1.26 and Noctalia, plus test setups without Noctalia and without a
kitty config.

## License

[CC0 1.0](LICENSE): no rights reserved. Themes are not included; they are read from kitty's own download at
runtime.
