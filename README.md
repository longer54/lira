# Lira — music player window & tray

🇵🇱 [Polska wersja](README.pl.md)

![Lira — screenshot with sample data](docs/screenshot.png)

*Screenshot with mock data — English UI preview.*

Two small Linux desktop apps (Rust) for the **Lira** music player:

- **`muzyka-okno`** (the window app) — a window (GTK3 + WebKitGTK) with the player UI and no title bar (the page's top bar doubles as the title bar); YouTube Music sign-in happens inside the window.
- **`muzyka-zasobnik`** (the tray app) — a tray icon (StatusNotifierItem) + MPRIS controls: play/pause, next/previous, open the window.

> **Server included:** the `lira-serwer/` directory is the backend (a local HTTP server on
> `127.0.0.1:26540` that searches for and plays music via `mpv`/`yt-dlp`). Everything runs on one
> computer — see [`lira-serwer/README.md`](lira-serwer/README.md). The UI is available in English and Polish.

## Quick start (local, one computer)

```sh
sudo apt install mpv python3-venv      # or your distro's equivalent; yt-dlp comes with pip below
cd lira-serwer
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
python3 lira_serwer.py                 # http://127.0.0.1:26540/ (--host / --port to change)
```

Then open `http://127.0.0.1:26540/` in a browser, or build and start `muzyka-okno` / `muzyka-zasobnik`.
Optional: run it as a systemd user service — see `lira-serwer/lira-serwer.service`.
Config: `~/.config/lira-serwer/config.json` (`lira-serwer/config.example.json`); data: `~/.local/share/lira-serwer`.

## Optional: a home server

The server can also run on a mini-PC / Raspberry Pi / NAS and be used from other devices in your network:
start it with `--host 0.0.0.0` (an access token is created automatically; open `http://HOST:26540/?t=TOKEN` once
on each device) and point the window at it with `MUZYKA_URL=http://home-server:26540/ muzyka-okno`.
For access from outside your home use a VPN/tunnel — don't expose the server publicly.

Features: **your playlists and liked songs** from your own YouTube Music account (sign in inside the window), played through `mpv`; internet **radio** (favourite stations), **podcasts** and YouTube channels (a “Listen later” list), a **Discover** tab, search, queue, English/Polish UI.

**No home server needed** — the backend runs on the same computer
(`127.0.0.1:26540`, the default for both the window and the tray).

## Features
- window (`muzyka-okno`): the server's UI in WebKitGTK, custom window buttons, YouTube Music sign-in cookies handed over to the local server (`/api/zaloguj`)
- closing the window doesn't stop the music — controls stay available in the tray and on the media keys
- tray (`muzyka-zasobnik`): menu with the current track, playback controls, device picker, MPRIS (media keys, sound applet)

## Requirements
- the `lira-serwer` backend on `127.0.0.1:26540` (a separate project, see above)
- Linux with GTK3 + WebKitGTK 4.1 (`libwebkit2gtk-4.1-dev`, `libgtk-3-dev`, `libsoup-3.0-dev`), D-Bus, Rust (rustup)
- a tray that supports StatusNotifierItem

## Building
```sh
(cd muzyka-okno && cargo build --release && cp target/release/muzyka-okno ~/.local/bin/)
(cd muzyka-zasobnik && cargo build --release && cp target/release/muzyka-zasobnik ~/.local/bin/)
```
The tray launches the window from `~/.local/bin/muzyka-okno`.

## Configuration
- `MUZYKA_URL` — UI address for the window (default `http://127.0.0.1:26540/`); also used for the sign-in hand-over (`/api/zaloguj`)
- `LIRA_SERWER` — `host:port` of the server used by the tray (fallback: host:port of `MUZYKA_URL`, then `127.0.0.1:26540`)
- UI language: `?lang=en|pl`, the 🌐 button, or the browser language (Polish → pl, otherwise en)
- sign-in cookies are stored by the backend, not by these programs

## Status
The self-hosted backend (`lira-serwer`) and the English/Polish UI are included. Windows/Android clients and multi-device “Play on:” switching are not part of this repository.
