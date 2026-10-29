# Lira — music player window & tray

🇵🇱 [Polska wersja](README.pl.md)

![Lira — screenshot with sample data](docs/screenshot.png)

*Screenshot with mock data — English UI preview; the English interface ships with the upcoming lira-serwer release.*

Two small Linux desktop apps (Rust) for the **Lira** music player:

- **`muzyka-okno`** (the window app) — a window (GTK3 + WebKitGTK) with the player UI and no title bar (the page's top bar doubles as the title bar); YouTube Music sign-in happens inside the window.
- **`muzyka-zasobnik`** (the tray app) — a tray icon (StatusNotifierItem) + MPRIS controls: play/pause, next/previous, “Play on:” (device picker), open the window.

> **Important:** these are clients only. Both programs require the **`lira-serwer`** backend
> (a local HTTP server on `127.0.0.1:26540` that searches for and plays music via
> `mpv`/`yt-dlp`). The server is **not part of this package** — it will ship in a separate
> release. Without it the window shows a blank page/connection error and the tray icon stays inactive.

## Like Spotify, on your own home server

Got a home server (a mini-PC, Raspberry Pi, a Linux NAS)? Set up the `lira-serwer` backend
on it once, then listen from any device in your home:

- the **Linux window** (`muzyka-okno`) and the **tray icon** (`muzyka-zasobnik`) from this package,
- **Windows** and **Android** — separate client apps served by the backend (a Windows PC or a phone can act as a “speaker”); not part of this package,
- **“Play on:”** — switch which device is playing, keeping the queue and the position in the track (like Spotify Connect); a phone can play the same thing as the server, in sync,
- a **shared queue** and playback state visible on every device,
- **your playlists and liked songs** from your own YouTube Music account (you sign in inside the window), played through `mpv` without ads,
- internet **radio** (favourite stations), **podcasts** and YouTube channels (a “Listen later” list), and a **Discover** tab with suggestions.

The window connects to the address in `MUZYKA_URL`, e.g. `MUZYKA_URL=http://home-server:26540/ muzyka-okno`.
For access from outside your home, use a VPN/tunnel — don't expose the backend publicly.

**No home server? That works too** — run the backend on the same computer
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
- `MUZYKA_URL` — UI address for the window (default `http://127.0.0.1:26540/`)
- server address used by the tray: the `SERWER` constant in `muzyka-zasobnik/src/main.rs`
- sign-in cookies are stored by the backend, not by these programs

## Status / Roadmap
The UI is currently in Polish; English UI and the self-hosted backend (lira-serwer) are coming in the next release.

---

**Disclaimer:** Private hobby project, built for learning and personal use. Not affiliated with YouTube or Google. Built with open-source tools (incl. mpv, yt-dlp, WebKitGTK). Every user signs in with their own account and is responsible for complying with the service's terms. Provided “as is”, without warranty of any kind.

License: MIT — see [LICENSE](LICENSE).
