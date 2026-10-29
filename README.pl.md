# Lira — okno i zasobnik odtwarzacza muzyki

🇬🇧 [English version](README.md)

![Lira — zrzut z przykładowymi danymi](docs/screenshot.png)

*Zrzut z przykładowymi danymi — podgląd angielskiego interfejsu, który wejdzie z najbliższą wersją lira-serwer.*

Dwie małe aplikacje na pulpit Linuksa (Rust) do odtwarzacza muzyki **Lira**:

- **`muzyka-okno`** — okno (GTK3 + WebKitGTK) z interfejsem odtwarzacza, bez belki tytułowej (górny pasek strony służy za belkę), logowanie do YouTube Music w oknie.
- **`muzyka-zasobnik`** — ikona w zasobniku (StatusNotifierItem) + sterowanie MPRIS: graj/pauza, następny/poprzedni, „Graj na:” (wybór urządzenia), otwarcie okna.

> **Ważne:** to tylko klienci. Oba programy wymagają backendu **`lira-serwer`**
> (lokalny serwer HTTP na `127.0.0.1:26540`, który wyszukuje i odtwarza muzykę przez
> `mpv`/`yt-dlp`). Serwer **nie jest częścią tej paczki** — pojawi się w osobnej
> wersji. Bez niego okno pokaże pustą stronę/błąd połączenia, a zasobnik będzie nieaktywny.

## Jak Spotify, tylko u Ciebie w domu

Masz domowy serwer (mini-PC, Raspberry Pi, NAS z Linuxem)? Stawiasz na nim backend
`lira-serwer` raz, a potem słuchasz z każdego urządzenia w domu:

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
- backend `lira-serwer` na `127.0.0.1:26540` (osobny projekt, zob. wyżej)
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

## Stan / plany
Interfejs jest obecnie po polsku; angielski interfejs i samodzielnie hostowany backend (lira-serwer) pojawią się w następnym wydaniu.

---

**Zastrzeżenie:** Prywatny projekt hobbystyczny, tworzony do nauki i własnego użytku. Nie jest powiązany z YouTube ani Google. Korzysta z otwartych narzędzi (m.in. mpv, yt-dlp, WebKitGTK). Każdy użytkownik loguje się własnym kontem i sam odpowiada za zgodność z regulaminem serwisu. Oprogramowanie udostępniane „tak jak jest”, bez gwarancji.

Licencja: MIT — zob. [LICENSE](LICENSE).
