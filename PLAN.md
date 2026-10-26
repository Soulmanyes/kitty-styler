# Plan: other terminals (Alacritty, foot, Ghostty)

Goal: Kitty Styler also styles Alacritty, foot and Ghostty. Kitty keeps working exactly as now.
The prompt, starship and zsh parts already work in any terminal and stay as they are.

## Facts checked (2026-10-26)

Installed here: kitty, alacritty 0.17.0. foot 1.28.0 and ghostty 1.3.1 are in the repos, not installed.

Noctalia 5.4.0 has templates for all three (`/usr/share/noctalia/assets/templates/<name>/`,
ids `alacritty`, `foot`, `ghostty` in `[theme.templates] builtin_ids`):

| | Alacritty | foot | Ghostty |
|---|---|---|---|
| Config | `~/.config/alacritty/alacritty.toml` (TOML) | `~/.config/foot/foot.ini` (INI) | `~/.config/ghostty/config` or `config.ghostty` (key = value) |
| Noctalia theme file | `themes/noctalia.toml` | `themes/noctalia` (`[colors-dark]`) | `themes/noctalia` (loaded by `theme = noctalia`) |
| Noctalia apply | adds the theme to `[general] import` if missing | if no noctalia include: **deletes every `include=.*themes` line**, adds its include under `[main]` | rewrites any `theme =` line to `theme = noctalia`, then reloads |
| Precedence | imports load first, the main file wins | later lines win | the theme loads first, the user's keys win |
| Reload | `live_config_reload` (on by default) | none, new windows only | `reload.sh`: systemd reload, D-Bus `reload-config`, else SIGUSR2 |

Consequences:
- foot: our theme include must not have `themes` in its path (else Noctalia deletes it). Use `~/.config/foot/kitty-styler.ini`, included from a managed block at the end of foot.ini.
- Ghostty: a non-Noctalia theme means `theme = <name>` (Ghostty ships its own theme collection, `ghostty +list-themes`), and "ghostty" must come out of Noctalia's `builtin_ids`, as already done for starship.
- Alacritty: a managed import cannot beat keys in alacritty.toml itself, so we must edit keys there in place. The owner's alacritty.toml has its own `[colors]` table, which already beats Noctalia's colours.
- Alacritty TOML editing: no tomlkit on most systems. Edit line-based, like starship.toml, and check the result with `tomllib` before writing.

Still to check, per terminal, before its backend is written: exact key names in the installed version (`man 5 foot.ini`, `ghostty +show-config --default --docs`, `man 5 alacritty`), validators (`foot --check-config`, `ghostty +validate-config`), and where each one reads its shell from (Alacritty `[terminal] shell`, foot `shell=`, Ghostty `command =`).

## Design

- Keep the name, `APP_ID`, `REPO` and the updater. Renaming is a separate question for the owner.
- kitty's option keys stay the internal schema. Each terminal is a backend that:
  - names its config path(s) (with a `KITTY_STYLER_<TERM>` env override for tests),
  - reads the current values into kitty keys (`read_conf`),
  - lists the kitty keys it supports; the other rows are hidden, the way `DEPENDS` hides rows now,
  - writes the settings (`apply`), with backups and the "only write what changed" rule,
  - lists its themes, and says whether Noctalia's template for it is active,
  - reloads open windows, and names the shell it starts (for the prompt part).
- Preview, share codes and the starship palette keep working on kitty keys. Preview parts a terminal lacks (tab bar, trail, shaders, inactive window) are drawn only when the backend supports them.
- Themes: kitty's theme zip (colour slots) converted to each format, as long as it exists. Ghostty uses its own collection. Without kitty, fetch the themes at runtime. Never bundle them, and check the licence before any release.

## Phases

0. ✅ **Golden output.** A harness runs `apply_all` on scratch copies for about 5 setting combinations and saves the files it writes. Every later step must leave kitty's output byte-identical.
1. ✅ (core part) **Backend seam, kitty only.** Move the kitty-specific parts behind a `Kitty` backend: config constants, `parse_conf_file`, `apply_all`'s kitty part, `NOCTALIA`, `kitty_shell`, `load_themes`, `color_snapshot`, `try_in_kitty`, and the kitty-only preview parts. Pure refactor, own branch, no version bump.
2. ✅ **Terminal picker.** Owner's choice: a Terminal dropdown at the top, listing installed or configured terminals.
3. ✅ **Alacritty backend.** Installed here, so first. Owner's choice (2026-10-26): while a theme is picked, the `[colors]` tables of alacritty.toml move to `alacritty-colors.kitty-styler.toml` (imported before ours, so `draw_bold_text_with_bright_colors` etc. still apply); picking the base theme puts them back byte-identical.
4. **foot backend.**
5. **Ghostty backend.**
6. Try-in-terminal for the new backends, README, screenshots, licence check, release.

Testing: only scratch copies (`KITTY_STYLER_*` env), harness refuses paths outside scratch. Never touch the real alacritty.toml, foot.ini or Ghostty config. foot and Ghostty need installing first (`sudo pacman -S foot ghostty`, owner runs it).

## Progress

- 2026-10-26, branch `multi-terminal`: phase 0 harness is `tests/golden.py` (gitignored; run `python tests/golden.py <app.py> <out>` on main and branch, then `diff -r`; six scenarios, Noctalia on/off, theme/font/trail/effect/tab bar/styled prompt/back to base). Phase 1 core done: `KittyTerminal` (`read_conf`, `theme`, `noctalia_colors`, `shell`, `write`, `reload`) and `TERMINAL`; `apply_all`, `load_current`, `theme_conf`, `starship_palette`, `try_apply` go through it. Output byte-identical to main. Still kitty-only: `load_themes`, `try_in_kitty`/`try_paths`, shader lookup, `BASE_LABEL`/`NOCTALIA` constants, preview parts.
- Phase 3 backend written (not yet in the UI): `AlacrittyTerminal`, line-based TOML editor (`toml_find`/`toml_set`, keeps formatting, edits one-line inline tables), `ALACRITTY_OPTIONS` (kitty key -> alacritty choices/default; unsupported rows to be hidden). Own keys are edited in place; others go to `kitty-styler.toml`, imported last; the import array is rewritten one path per line (Noctalia's undo deletes lines naming noctalia.toml) and the original import text is saved in the managed file and restored. `tests/alacritty_test.py` (gitignored, run with `TMPDIR=<scratch>`): 15 checks incl. no-change and theme-then-base byte-identical, Noctalia's real undo/apply scripts, `alacritty migrate --dry-run`. Real alacritty 0.17 reloads when an imported file changes, so `reload()` does nothing.
- Known: if Noctalia's alacritty template is re-run after its undo, it appends its import after ours, so Noctalia's colours win over a picked theme. Not handled.
- Next: phase 2 picker. `TERMINAL`, `BASE_LABEL`, `KITTY_SHELL`, `ZSH_AVAILABLE`, prompt-mode notes are set once at import and must follow the picked terminal; rows not in the backend's options hidden (and choices overridden); preview must not draw tab bar/scrollbar/trail/inactive window for alacritty; `backend.notes` from write() should reach the status line; Try in kitty only for kitty for now.
- Phase 2 done: `set_terminal(name)` resets `TERMINAL`, `BASE_LABEL`, `KITTY_SHELL`, `ZSH_AVAILABLE`; picking another terminal rebuilds the window (unapplied changes dropped, like Reset) and remembers it in `~/.config/kitty-styler/terminal` (`KITTY_STYLER_TERMINAL` overrides; else the terminal the app was started from). Rows the terminal lacks are hidden (`supports`, `choices`); `NO_KITTY_EXTRAS` turns tab bar/trail/scrollbar/splits/ligatures off in the preview, and share codes leave those keys out. Try in kitty only for kitty. Checked: kitty golden identical, Alacritty tests, scratch end-to-end apply, GUI shots of both.
- starship.toml is shared by every terminal, so for now only kitty's theme changes the starship palette / Noctalia's starship template (an Alacritty theme would otherwise recolour kitty's prompt). Ask the owner whether that's what they want.
- Before release: `/etc/alacritty/alacritty.toml`-only systems (don't create a user file that hides it); imports inside imports resolve against the importing file's folder.
