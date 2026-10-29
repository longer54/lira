# lira-serwer

Local backend for the Lira player: YouTube Music search / radio / playlists (`ytmusicapi`),
playback through `mpv` + `yt-dlp`, and the web UI (English / Polish). Everything runs on
one computer — no other server needed.

## Run

```sh
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt          # also install mpv with your package manager
python3 lira_serwer.py                   # http://127.0.0.1:26540/
```

Options: `--host`, `--port`, `--config FILE` (or env `LIRA_HOST`, `LIRA_PORT`).
Data: `$XDG_DATA_HOME/lira-serwer` (`~/.local/share/lira-serwer`),
config: `$XDG_CONFIG_HOME/lira-serwer/config.json` — see `config.example.json`
(`language`, `region`, `mpv`, `yt_dlp`, `proxy_stream`, `token`).

## Language

The UI picks the language from `?lang=en|pl`, then the saved choice (localStorage), then
`navigator.language` (Polish → pl, anything else → en). The 🌐 button in the top bar switches it.
Text sent by the server is English and translated in the UI; content language of YouTube
titles follows `language` in the config (default: your locale).

## Other devices (optional)

Listening on a non-loopback address (`--host 0.0.0.0`) creates an access token
(`<data dir>/token`, or `token` in the config / env `LIRA_TOKEN`). Open `http://HOST:PORT/?t=TOKEN`
once on the other device. Requests from 127.0.0.1 never need it.

## systemd user service

See `lira-serwer.service` (instructions inside).

## Sign in to YouTube Music

Use the `muzyka-okno` window (it passes the Google session cookies to `/api/zaloguj`).
Without signing in, search, radio and playback still work.
