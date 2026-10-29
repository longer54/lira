# Lira — okno i zasobnik odtwarzacza muzyki

![Lira — zrzut z przykładowymi danymi](docs/screenshot.png)

*Zrzut z przykładowymi, zmyślonymi danymi (mock API).*

Dwie małe aplikacje na pulpit Linuksa (Rust) do odtwarzacza muzyki **Lira**:

- **`muzyka-okno`** — okno (GTK3 + WebKitGTK) z interfejsem odtwarzacza, bez belki tytułowej (górny pasek strony służy za belkę), logowanie do YouTube Music w oknie.
- **`muzyka-zasobnik`** — ikona w zasobniku (StatusNotifierItem) + sterowanie MPRIS: graj/pauza, następny/poprzedni, „Graj na:” (wybór urządzenia), otwarcie okna.

> **Ważne:** to tylko klienci. Oba programy wymagają backendu **`muzyka-bps`**
> (lokalny serwer HTTP na `127.0.0.1:26540`, który wyszukuje i odtwarza muzykę przez
> `mpv`/`yt-dlp`). Serwer **nie jest częścią tej paczki** — pojawi się w osobnej
> wersji. Bez niego okno pokaże pustą stronę/błąd połączenia, a zasobnik będzie nieaktywny.

## Jak Spotify, tylko u Ciebie w domu

Masz domowy serwer (mini-PC, Raspberry Pi, NAS z Linuxem)? Stawiasz na nim backend
`muzyka-bps` raz, a potem słuchasz z każdego urządzenia w domu:

- **okno na Linuxie** (`muzyka-okno`) i **ikona w zasobniku** (`muzyka-zasobnik`) z tej paczki,
- **Windows** i **Android** — osobne aplikacje-klienci obsługiwane przez backend (komputer z Windows lub telefon może być „głośnikiem”); nie są częścią tej paczki,
- **„Graj na:”** — przełączasz, na którym urządzeniu gra muzyka, z kolejką i miejscem w utworze (jak Spotify Connect); telefon może grać to samo co serwer, zsynchronizowane,
- **wspólna kolejka** i stan odtwarzania widoczne na wszystkich urządzeniach,
- **Twoje playlisty i polubione** z własnego konta YouTube Music (logujesz się w oknie), odtwarzanie przez `mpv` bez reklam,
- **radio** internetowe (ulubione stacje), **podcasty** i kanały YouTube (lista „Do posłuchania”), zakładka **Odkrywaj** z podpowiedziami.

Okno łączy się z adresem z `MUZYKA_URL`, np. `MUZYKA_URL=http://serwer-domowy:26540/ muzyka-okno`.
Do dostępu spoza domu używaj VPN/tunelu — nie wystawiaj backendu publicznie.

**Bez serwera w domu też się da** — backend uruchamiasz na tym samym komputerze
(`127.0.0.1:26540`, ustawienie domyślne okna i zasobnika).

## Funkcje
- okno: interfejs serwera w WebKitGTK, własne przyciski okna, przekazanie ciasteczek logowania YouTube Music do lokalnego serwera (`/api/zaloguj`)
- zamknięcie okna nie zatrzymuje muzyki — sterowanie zostaje w zasobniku i klawiszach multimediów
- zasobnik: menu z bieżącym utworem, sterowanie, wybór urządzenia, MPRIS (klawisze multimediów, aplet dźwięku)

## Wymagania
- backend `muzyka-bps` na `127.0.0.1:26540` (osobny projekt, zob. wyżej)
- Linux z GTK3 + WebKitGTK 4.1 (`libwebkit2gtk-4.1-dev`, `libgtk-3-dev`, `libsoup-3.0-dev`), D-Bus, Rust (rustup)
- zasobnik obsługujący StatusNotifierItem

## Budowanie
```sh
(cd muzyka-okno && cargo build --release && cp target/release/muzyka-okno ~/.local/bin/)
(cd muzyka-zasobnik && cargo build --release && cp target/release/muzyka-zasobnik ~/.local/bin/)
```
Zasobnik uruchamia okno z `~/.local/bin/muzyka-okno`.

## Konfiguracja
- `MUZYKA_URL` — adres interfejsu dla okna (domyślnie `http://127.0.0.1:26540/`)
- adres serwera w zasobniku: stała `SERWER` w `muzyka-zasobnik/src/main.rs`
- ciasteczka logowania trzyma backend, nie te programy

---

## English

Two small Linux desktop apps (Rust) for the **Lira** music player:

- **`muzyka-okno`** — a borderless GTK3 + WebKitGTK window with the player UI; YouTube Music sign-in happens inside the window.
- **`muzyka-zasobnik`** — a tray icon (StatusNotifierItem) + MPRIS controls: play/pause, next/previous, “Play on:” device picker, open window.

> **Important:** these are clients only. Both require the **`muzyka-bps`** backend
> (a local HTTP server on `127.0.0.1:26540` that searches and plays music via
> `mpv`/`yt-dlp`). The server is **not included** — it will be published separately.
> Without it the window shows a connection error and the tray stays inactive.

### Like Spotify, on your own home server

Got a home server (mini-PC, Raspberry Pi, a Linux NAS)? Set up the `muzyka-bps` backend
on it once and listen from any device at home:

- the **Linux window** (`muzyka-okno`) and **tray icon** (`muzyka-zasobnik`) from this package,
- **Windows** and **Android** — separate client apps served by the backend (a Windows PC or a phone can act as a “speaker”); not part of this package,
- **“Play on:”** — switch which device plays, keeping the queue and position (like Spotify Connect); a phone can play in sync with the server,
- a **shared queue** and playback state on every device,
- **your playlists and likes** from your own YouTube Music account (sign in inside the window), played through `mpv` without ads,
- internet **radio** (favourite stations), **podcasts** and YouTube channels (“Listen later” list), a **Discover** tab with suggestions.

Point the window at the server with `MUZYKA_URL`, e.g. `MUZYKA_URL=http://home-server:26540/ muzyka-okno`.
Use a VPN/tunnel for access from outside — don't expose the backend publicly.

**No home server? That works too** — run the backend on the same computer
(`127.0.0.1:26540`, the default for the window and tray).

### Requirements
- the `muzyka-bps` backend on `127.0.0.1:26540` (separate project)
- Linux with GTK3 + WebKitGTK 4.1, D-Bus, Rust; a StatusNotifierItem tray

### Building / configuration
See above. `MUZYKA_URL` overrides the window URL; the tray server address is the `SERWER` constant.

---

**Zastrzeżenie:** Prywatny projekt hobbystyczny, tworzony do nauki i własnego użytku. Nie jest powiązany z YouTube ani Google. Korzysta z otwartych narzędzi (m.in. mpv, yt-dlp, WebKitGTK). Każdy użytkownik loguje się własnym kontem i sam odpowiada za zgodność z regulaminem serwisu. Oprogramowanie udostępniane „tak jak jest”, bez gwarancji.

**Disclaimer:** Private hobby project, built for learning and personal use. Not affiliated with YouTube or Google. Built with open-source tools (incl. mpv, yt-dlp, WebKitGTK). Every user signs in with their own account and is responsible for complying with the service's terms. Provided “as is”, without warranty of any kind.

Licencja / License: MIT — see [LICENSE](LICENSE).
