#!/usr/bin/env python3
"""lira-serwer — local backend for the Lira music player.

YouTube Music search/radio/playlists via ytmusicapi, audio playback through mpv + yt-dlp,
a web UI (English / Polish) served on http://127.0.0.1:26540/ by default.

    python3 lira_serwer.py [--host 127.0.0.1] [--port 26540] [--config FILE]

Everything runs on one computer: no other server is needed. Data lives in
$XDG_DATA_HOME/lira-serwer (~/.local/share/lira-serwer), the optional config in
$XDG_CONFIG_HOME/lira-serwer/config.json (see config.example.json).
Requires: python3, mpv, yt-dlp, ytmusicapi (see requirements.txt).
"""
import argparse
import atexit
import json
import locale
import os
import secrets
import signal
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent                      # where the UI files (static/) live


def _xdg(zmienna, domyslna):
    return Path(os.environ.get(zmienna) or (Path.home() / domyslna))


KAT = Path(os.environ.get("LIRA_DATA_DIR") or _xdg("XDG_DATA_HOME", ".local/share") / "lira-serwer")
KAT_CFG = Path(os.environ.get("LIRA_CONFIG_DIR") or _xdg("XDG_CONFIG_HOME", ".config") / "lira-serwer")
CIASTKA = KAT / "cookies.txt"   # for yt-dlp: without a YouTube account it gets blocked now and then (429, "not a bot")

_arg = argparse.ArgumentParser(description="Lira music server (local mode by default)")
_arg.add_argument("--host", help="listen address (default 127.0.0.1)")
_arg.add_argument("--port", type=int, help="listen port (default 26540)")
_arg.add_argument("--config", help="config file (default: %s)" % (KAT_CFG / "config.json"))
ARGS = _arg.parse_args()

KAT.mkdir(parents=True, exist_ok=True)
_cfg_plik = Path(ARGS.config) if ARGS.config else KAT_CFG / "config.json"
try:
    CFG = json.loads(_cfg_plik.read_text()) if _cfg_plik.exists() else {}
except Exception as _e:
    sys.exit(f"lira-serwer: cannot read {_cfg_plik}: {_e}")

import random
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ytmusicapi import YTMusic

HOST = ARGS.host or os.environ.get("LIRA_HOST") or CFG.get("host") or "127.0.0.1"
PORT = int(ARGS.port or os.environ.get("LIRA_PORT") or CFG.get("port") or 26540)
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
SOCK = Path(os.environ.get("XDG_RUNTIME_DIR") or KAT) / f"lira-mpv-{PORT}.sock"
URL = "https://music.youtube.com/watch?v="
MPV = CFG.get("mpv") or shutil.which("mpv") or "mpv"
_yt_obok = Path(sys.executable).parent / "yt-dlp"          # yt-dlp installed next to ytmusicapi (venv)
YTDLP = CFG.get("yt_dlp") or (str(_yt_obok) if _yt_obok.exists() else None) or shutil.which("yt-dlp") or "yt-dlp"
# Old mpv builds (e.g. 0.37) can get 403 from googlevideo although yt-dlp alone works:
# {"proxy_stream": true} makes mpv play from /strumien/<id>, which this server fetches through yt-dlp.
POSREDNIK = bool(CFG.get("proxy_stream"))


def _locale_lang():
    for z in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(z):
            return os.environ[z]
    return (locale.getlocale()[0] or "")


_lc = _locale_lang()
HL = CFG.get("language") if CFG.get("language") not in (None, "", "auto") else ("pl" if _lc.lower().startswith("pl") else "en")
REGION = (CFG.get("region") or (_lc.split("_")[1][:2].upper() if "_" in _lc and len(_lc.split("_")[1]) >= 2 else "US")).upper()

# Access token: only needed when the server listens on something else than loopback
# (other devices on the network). Local requests (127.0.0.1) never need it.
TOKEN = os.environ.get("LIRA_TOKEN") or CFG.get("token") or ""
if not TOKEN and (KAT / "token").exists():
    TOKEN = (KAT / "token").read_text().strip()
if not TOKEN and HOST not in ("127.0.0.1", "localhost", "::1"):
    TOKEN = secrets.token_urlsafe(24)
    (KAT / "token").write_text(TOKEN)
    (KAT / "token").chmod(0o600)


def adres(vid):
    return f"http://127.0.0.1:{PORT}/strumien/{vid}" if POSREDNIK else URL + vid


def _po_polsku(y):
    """YouTube tłumaczy tytuły na język interfejsu („He built a million-dollar business…”).
    ytmusicapi nie ma polskiego, więc podmieniamy samo hl. NIE dla search() i get_artist() —
    ich parser rozpoznaje sekcje po angielskich nazwach."""
    y.context["context"]["client"]["hl"] = HL
    return y


ytm_anon = YTMusic(location=REGION)
ytm_anon_pl = _po_polsku(YTMusic(location=REGION))
PO_ANGIELSKU = {"search", "get_artist", "get_search_suggestions"}
KONTO = KAT / "konto.json"         # ciasteczka YT Music z okna (muzyka-okno → /api/zaloguj), 0600


def _konto():
    """YTMusic for the signed-in account (own playlists, personalised home) or None."""
    if not KONTO.exists():
        return None
    try:
        return YTMusic(str(KONTO), location=REGION)
    except Exception as e:
        log("konto:", e)
        return None


ytm_konto = _konto()
ytm_konto_pl = _po_polsku(_konto()) if ytm_konto else None
konto_wygaslo = False


class _Ytm:
    """Najpierw konto; gdy sesja wygasła — anonimowo i flaga dla okna („Zaloguj ponownie”)."""

    def __getattr__(self, nazwa):
        def f(*a, **kw):
            global konto_wygaslo
            pl = nazwa not in PO_ANGIELSKU
            if ytm_konto and not konto_wygaslo:
                try:
                    return getattr(ytm_konto_pl if pl and ytm_konto_pl else ytm_konto, nazwa)(*a, **kw)
                except Exception as e:
                    if "401" in str(e) or "403" in str(e) or "Sign in" in str(e):
                        log("konto wygasło:", e)
                        konto_wygaslo = True
                        _start["dane"] = None
                    else:
                        raise
            return getattr(ytm_anon_pl if pl else ytm_anon, nazwa)(*a, **kw)
        return f


ytm = _Ytm()
META_PLIK = KAT / "meta.json"
try:
    META: dict[str, dict] = json.loads(META_PLIK.read_text())  # videoId → {tytul, wykonawca, okladka…}
except Exception:
    META = {}
blokada = threading.Lock()
stan_ladowania = {"id": None, "od": 0.0, "blad": None}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class Blad(Exception):
    pass


# ── ytmusicapi → nasz format ──────────────────────────────────────────────

def _okladka(x, duza=False):
    t = x.get("thumbnails") or x.get("thumbnail") or []
    if isinstance(t, dict):
        t = t.get("thumbnails") or []
    if not t:
        return None
    u = t[-1]["url"]
    # Okładki googleusercontent mają rozmiar w adresie — prosimy o ładny kwadrat.
    return re.sub(r"=w\d+-h\d+.*$", "=w544-h544-l90-rj" if duza else "=w226-h226-l90-rj", u)


def _sek(d):
    if isinstance(d, int):
        return d
    if not d:
        return None
    m = re.fullmatch(r"(?:(\d+) (?:hr|godz\.?))?\s*(?:i\s*)?(?:(\d+) min)?\s*(?:(\d+) (?:sec|s))?", str(d).strip())
    if m and any(m.groups()):  # odcinki podcastów: „1 hr 16 min”
        h, mi, se = (int(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + se
    s = 0
    for c in str(d).split(":"):
        if not c.isdigit():
            return None
        s = s * 60 + int(c)
    return s


def _utwor(x, okladka=None):
    vid = x and x.get("videoId")
    if not vid:
        return None
    podcast = x.get("podcast") if isinstance(x.get("podcast"), dict) else None
    u = {"videoId": vid, "tytul": x.get("title") or "",
         "wykonawca": ", ".join(a["name"] for a in x.get("artists") or [] if a.get("name"))
         or (podcast or {}).get("name") or "",
         "album": (x.get("album") or {}).get("name") if isinstance(x.get("album"), dict) else None,
         "wykonawcaId": next((a["id"] for a in x.get("artists") or [] if a.get("id")), None),
         "okladka": _okladka(x) or okladka,
         "dlugosc": _sek(x.get("duration_seconds") or x.get("duration") or x.get("length"))}
    if x.get("resultType") == "episode" or "PODCAST" in str(x.get("videoType") or "") or podcast:
        u["odcinek"] = True   # po odcinku nie dokładamy muzyki, pamiętamy miejsce
    stary = META.get(vid)
    if stary:  # nie zamazuj lepszych danych gorszymi
        for k, v in stary.items():
            if not u.get(k):
                u[k] = v
    META[vid] = u
    return u


def _kafel(x):
    """Element strony głównej / wyników: utwór, playlista, album albo wykonawca."""
    if not x:  # na koncie get_home zwraca czasem puste miejsca w sekcjach
        return None
    if x.get("videoId"):
        u = _utwor(x)
        return {"typ": "utwor", **u, "podpis": u["wykonawca"]} if u else None
    podpis = ", ".join(a["name"] for a in x.get("artists") or [] if a.get("name")) \
        or x.get("description") or x.get("year") or ""
    if x.get("playlistId") and not x.get("browseId", "").startswith("MPRE"):
        return {"typ": "playlista", "id": x["playlistId"], "tytul": x.get("title"),
                "podpis": podpis, "okladka": _okladka(x)}
    b = x.get("browseId") or ""
    if b.startswith("MPSP") or x.get("resultType") == "podcast":
        return {"typ": "podcast", "id": b, "tytul": x.get("title"),
                "podpis": (x.get("channel") or {}).get("name") if isinstance(x.get("channel"), dict) else podpis,
                "okladka": _okladka(x)}
    if b.startswith("MPRE"):
        return {"typ": "album", "id": b, "tytul": x.get("title"), "podpis": podpis, "okladka": _okladka(x)}
    if b.startswith("UC"):
        return {"typ": "wykonawca", "id": b, "tytul": x.get("title") or x.get("artist"),
                "podpis": "Wykonawca", "okladka": _okladka(x)}
    if b.startswith("VL"):
        return {"typ": "playlista", "id": b[2:], "tytul": x.get("title"), "podpis": podpis, "okladka": _okladka(x)}
    return None


TYTULY = {}   # section titles stay as YouTube sends them; the UI translates them
_start = {"czas": 0, "dane": None}


def moje_playlisty():
    if not ytm_konto or konto_wygaslo:
        return []
    kafle = [{"typ": "playlista", "id": "LM", "tytul": "Liked songs", "podpis": "Your liked songs",
              "okladka": None}]
    for x in ytm.get_library_playlists(limit=100) or []:
        if x.get("playlistId") in ("LM", "SE"):
            continue
        k = _kafel(x)
        if k:
            k["podpis"] = x.get("count") and f"{x['count']} utw." or k.get("podpis") or ""
            kafle.append(k)
    return kafle


def strona_glowna():
    if _start["dane"] and time.time() - _start["czas"] < 1800:
        return _start["dane"]
    sekcje = []
    for s in ytm.get_home(limit=8):
        kafle = [k for k in (_kafel(x) for x in s.get("contents") or []) if k]
        if kafle:
            t = s.get("title") or ""
            sek = {"tytul": TYTULY.get(t, t), "kafle": kafle}
            # „Szybki wybór” (same utwory) na górę, jak w YT Music
            sekcje.insert(0, sek) if all(k["typ"] == "utwor" for k in kafle) else sekcje.append(sek)
    try:
        moje = moje_playlisty()
        if moje:
            sekcje.insert(1 if sekcje and all(k["typ"] == "utwor" for k in sekcje[0]["kafle"]) else 0,
                          {"tytul": "Your playlists", "kafle": moje})
    except Exception as e:
        log("moje playlisty:", e)
    _start.update(czas=time.time(), dane=sekcje)
    return sekcje


def szukaj(q):
    with ThreadPoolExecutor(6) as p:
        f5 = p.submit(szukaj_kanalow, q)
        f6 = p.submit(szukaj_stacji, q, 8)
        f1 = p.submit(ytm.search, q, filter="songs", limit=20)
        f2 = p.submit(ytm.search, q, limit=20)
        f3 = p.submit(ytm.search, q, filter="podcasts", limit=10)
        f4 = p.submit(ytm.search, q, filter="episodes", limit=10)
        utwory = [u for u in (_utwor(x) for x in f1.result() or []) if u]
        reszta = f2.result() or []
        podcasty = [k for k in (_kafel(x) for x in f3.result() or []) if k]
        odcinki = [u for u in (_utwor({**x, "resultType": "episode"}) for x in f4.result() or []) if u]

        def po_polsku(u):
            try:
                t = ytm.get_song(u["videoId"]).get("videoDetails", {}).get("title")
                if t:
                    u["tytul"] = META[u["videoId"]]["tytul"] = t
            except Exception:
                pass
        list(p.map(po_polsku, odcinki))
    if not utwory:
        utwory = [u for u in (_utwor(x) for x in reszta if x.get("resultType") in ("song", "video")) if u]
    najlepszy = next((_kafel(x) for x in reszta if x.get("category") == "Top result"), None)
    inne = [k for k in (_kafel(x) for x in reszta if x.get("resultType") in ("album", "playlist", "artist")) if k]
    kanaly, stacje = [], []
    try:
        kanaly = f5.result()
    except Exception as e:
        log("szukaj kanałów:", e)
    try:
        stacje = f6.result()
    except Exception as e:
        log("szukaj stacji:", e)
    return {"najlepszy": najlepszy, "utwory": utwory, "inne": inne, "podcasty": podcasty, "odcinki": odcinki,
            "kanaly": kanaly, "stacje": stacje}


def wykonawca_utworu(vid):
    m = META.get(vid) or {}
    if m.get("wykonawcaId"):
        return {"id": m["wykonawcaId"]}
    try:  # brak w META (np. z historii) — kanał utworu w YT Music to kanał wykonawcy
        kid = ((ytm.get_song(vid) or {}).get("videoDetails") or {}).get("channelId")
    except Exception as e:
        log("wykonawca:", e)
        kid = None
    if not kid:
        raise Blad("Unknown artist for this track.")
    if m:
        m["wykonawcaId"] = kid
    return {"id": kid}


def lista(typ, id_):
    if typ == "playlista":
        p = ytm.get_playlist(id_, limit=500 if id_ == "LM" else 200)
        ok = _okladka(p, True) or next((_okladka(x, True) for x in p.get("tracks") or [] if x.get("thumbnails")), None)
        return {"tytul": p.get("title"), "podpis": (p.get("author") or {}).get("name") if isinstance(p.get("author"), dict) else "",
                "okladka": ok, "utwory": [u for u in (_utwor(x) for x in p.get("tracks") or []) if u]}
    if typ == "album":
        a = ytm.get_album(id_)
        ok = _okladka(a, True)
        return {"tytul": a.get("title"), "okladka": ok,
                "podpis": " · ".join(filter(None, [", ".join(x["name"] for x in a.get("artists") or []), a.get("year")])),
                "utwory": [u for u in (_utwor(x, ok) for x in a.get("tracks") or []) if u]}
    if typ == "wykonawca":
        a = ytm.get_artist(id_)
        utwory = [u for u in (_utwor(x) for x in (a.get("songs") or {}).get("results") or []) if u]
        pl = (a.get("songs") or {}).get("browseId")
        if pl:  # pełna lista „Utwory” wykonawcy, nie tylko 5 z nagłówka
            try:
                utwory = [u for u in (_utwor(x) for x in ytm.get_playlist(pl, limit=50).get("tracks") or []) if u] or utwory
            except Exception as e:
                log("wykonawca: pełna lista", e)
        kafle = []
        for klucz in ("albums", "singles"):
            kafle += [k for k in (_kafel({**x, "browseId": x.get("browseId")}) for x in (a.get(klucz) or {}).get("results") or []) if k]
        return {"tytul": a.get("name"), "podpis": "Wykonawca", "okladka": _okladka(a, True),
                "utwory": utwory, "kafle": kafle}
    if typ == "podcast":
        pc = ytm.get_podcast(id_)
        ok = _okladka(pc, True)
        autor = (pc.get("author") or {}).get("name") if isinstance(pc.get("author"), dict) else ""
        return {"tytul": pc.get("title"), "podpis": autor, "okladka": ok, "opis": pc.get("description"),
                "ulubiony": any(k["id"] == id_ for k in ZAKLADKI.get(ULUBIONE, [])),
                "utwory": [u for u in (_utwor({**x, "resultType": "episode", "artists": [{"name": pc.get("title")}]}, ok)
                                       for x in pc.get("episodes") or []) if u]}
    if typ == "kanaly":
        return kanaly_start()
    if typ == "kanal":
        return kanal(id_)
    if typ == "radio":
        return radio_start()
    if typ == "podcasty":  # zakładka „Podcasty”: do posłuchania + nowe + ulubione + Twoje + na później + popularne
        sek = sekcje_sluchania()
        if ZAKLADKI.get(ULUBIONE) and any(k["typ"] == "podcast" for k in ZAKLADKI[ULUBIONE]):
            sek.append({"tytul": "⭐ Favourite podcasts", "kafle": [k for k in ZAKLADKI[ULUBIONE] if k["typ"] == "podcast"]})
        if ytm_konto and not konto_wygaslo:
            moje = [k for k in (_kafel(x) for x in ytm.get_library_podcasts(limit=50) or [])
                    if k and k.get("id") not in ("SE",)]
            if moje:
                sek.append({"tytul": "Your podcasts", "kafle": moje})
            try:
                pozniej = [u for u in (_utwor({**x, "resultType": "episode"}) for x in
                                       ytm.get_playlist("SE", limit=50).get("tracks") or []) if u]
                if pozniej:
                    sek.append({"tytul": "Episodes for later", "utwory": pozniej})
            except Exception as e:
                log("na później:", e)
        for fraza, tytul in (("podcast", "Popularne podcasty"), ("podcast rozmowa wywiad", "Rozmowy i wywiady"),
                             ("podcast biznes", "Biznes")):
            try:
                kafle = [k for k in (_kafel(x) for x in ytm.search(fraza, filter="podcasts", limit=12) or []) if k]
                if kafle:
                    sek.append({"tytul": tytul, "kafle": kafle})
            except Exception as e:
                log("podcasty:", e)
        return {"sekcje": sek, "do_posluchania": _dp_ids()}
    raise Blad("unknown list type")


def radio(video_id, bez=()):
    """„Radio” utworu — to samo, co YT Music puszcza po nim. Pierwszy = on sam."""
    t = ytm.get_watch_playlist(video_id, limit=40).get("tracks") or []
    return [u for u in (_utwor(x) for x in t) if u and u["videoId"] not in bez]


_odk_podane: set[str] = set()   # „Odkrywaj” nie powtarza utworów w obrębie dnia
ODK_PLIK = KAT / "odkrywaj.json"  # czego słuchasz w „Odkrywaj”, a co przewijasz od razu
try:
    ODK_GUST = json.loads(ODK_PLIK.read_text())
except Exception:
    ODK_GUST = {"dobre": [], "zle": [], "zli_wyk": {}}


def odkrywaj_ocena(d):
    """Uczy się z przewijania: słuchane ≥ 25 s albo polubione = dobre (nasiona następnych),
    przewinięte w < 6 s = złe (wykonawca dostaje minus, po 3 minusach znika z „Odkrywaj”)."""
    vid, sek = d.get("videoId"), float(d.get("sekundy") or 0)
    if not vid:
        return {"ok": False}
    wyk = (META.get(vid) or {}).get("wykonawca") or ""
    if d.get("polubiony") or sek >= 25:
        if vid not in ODK_GUST["dobre"]:
            ODK_GUST["dobre"] = (ODK_GUST["dobre"] + [vid])[-300:]
        if wyk in ODK_GUST["zli_wyk"]:
            ODK_GUST["zli_wyk"][wyk] = max(0, ODK_GUST["zli_wyk"][wyk] - 1)
    elif sek < 6:
        ODK_GUST["zle"] = (ODK_GUST["zle"] + [vid])[-2000:]
        if wyk:
            ODK_GUST["zli_wyk"][wyk] = ODK_GUST["zli_wyk"].get(wyk, 0) + 1
    try:
        ODK_PLIK.write_text(json.dumps(ODK_GUST))
    except Exception as e:
        log("odkrywaj:", e)
    return {"ok": True}


def odkrywaj(ile=12):
    """„Odkrywaj” (jak Samples w YT Music): losowa muzyka do przewijania palcem.
    Radio z losowych polubionych/słuchanych, bez tego, co już znasz, przemieszane."""
    if len(_odk_podane) > 3000:
        _odk_podane.clear()
    znane = lubiane() | set(WYSLUCHANE) | set(ODK_GUST["zle"])
    pula = sorted(LUBIANE) or [v for v, m in META.items() if not m.get("odcinek")][-300:]
    nasiona = random.sample(pula, min(2, len(pula)))
    dobre = ODK_GUST["dobre"][-40:]   # to, co ostatnio wciągnęło Cię w „Odkrywaj”, ma pierwszeństwo
    nasiona += random.sample(dobre, min(2, len(dobre))) or random.sample(pula, min(1, len(pula)))
    zli = {w for w, n in ODK_GUST["zli_wyk"].items() if n >= 3}
    wynik, widziane = [], set()
    for v in nasiona:
        try:
            for u in radio(v, bez=znane | _odk_podane):
                if u["videoId"] not in widziane and not u.get("odcinek") and u.get("wykonawca") not in zli:
                    widziane.add(u["videoId"])
                    wynik.append(u)
        except Exception as e:
            log("odkrywaj:", e)
    random.shuffle(wynik)
    wynik = wynik[:ile]
    _odk_podane.update(u["videoId"] for u in wynik)
    return {"utwory": wynik}


# ── kanały YouTube (zwykłe, nie YT Music) — przez yt-dlp, z ciasteczkami konta ──

_ytdl_cache: dict[str, tuple[float, dict]] = {}


def _ytdl(url, ile=30, ttl=900):
    """Płaska lista z youtube.com (kanał, wyszukiwarka, subskrypcje). Tytuły po polsku (lang=HL)."""
    k = f"{url}|{ile}"
    if k in _ytdl_cache and time.time() - _ytdl_cache[k][0] < ttl:
        return _ytdl_cache[k][1]
    import yt_dlp
    opcje = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": ile, "skip_download": True,
             "extractor_args": {"youtube": {"lang": [HL]}}}
    if ytm_konto and CIASTKA.exists():
        opcje["cookiefile"] = str(CIASTKA)
    with yt_dlp.YoutubeDL(opcje) as y:
        d = y.extract_info(url, download=False) or {}
    _ytdl_cache[k] = (time.time(), d)
    return d


def _url_img(u):
    return ("https:" + u) if u and u.startswith("//") else u


def _kafel_kanalu(e):
    cid = e.get("channel_id") or e.get("id") or ""
    if not cid.startswith("UC"):
        return None
    t = [x for x in e.get("thumbnails") or [] if x.get("url")]
    subs = e.get("channel_follower_count")
    podpis = "Channel" + (f" · {subs / 1e6:.1f} mln subskr.".replace(".", ",") if subs and subs >= 1e6
                        else f" · {subs // 1000} tys. subskr." if subs and subs >= 1000 else "")
    return {"typ": "kanal", "id": cid, "tytul": e.get("channel") or e.get("title") or cid,
            "podpis": podpis, "okladka": _url_img(t[-1]["url"]) if t else None}


def szukaj_kanalow(q, ile=8):
    d = _ytdl("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q) + "&sp=EgIQAg%3D%3D", ile)
    return [k for k in (_kafel_kanalu(e) for e in d.get("entries") or []) if k]


def subskrypcje():
    if not ytm_konto or konto_wygaslo:
        return []
    d = _ytdl("https://www.youtube.com/feed/channels", 200, ttl=3600)
    return [k for k in (_kafel_kanalu(e) for e in d.get("entries") or []) if k]


def kanal(cid):
    d = _ytdl(f"https://www.youtube.com/channel/{cid}/videos", 40)
    nazwa = (d.get("channel") or d.get("title") or "").removesuffix(" - Wideo").removesuffix(" - Videos")
    avatar = next((_url_img(t["url"]) for t in reversed(d.get("thumbnails") or [])
                   if t.get("id") == "avatar_uncropped" or (t.get("width") and t.get("width") == t.get("height"))), None)
    filmy = []
    for e in d.get("entries") or []:
        if not e.get("id") or e.get("live_status") == "is_upcoming":
            continue
        u = {"videoId": e["id"], "tytul": e.get("title") or "", "wykonawca": nazwa,
             "okladka": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
             "dlugosc": int(e["duration"]) if e.get("duration") else None, "odcinek": True, "film": True}
        META[e["id"]] = {**META.get(e["id"], {}), **u}
        filmy.append(u)
    return {"tytul": nazwa, "podpis": "YouTube channel", "okladka": avatar, "opis": d.get("description"),
            "utwory": filmy, "ulubiony": any(k["id"] == cid for k in ZAKLADKI.get(ULUBIONE, []))}


# ── bookmarks: favourite channels/podcasts + own groups (local file) ──

ZAKLADKI_PLIK = KAT / "zakladki.json"
ULUBIONE = "⭐ Ulubione"
try:
    ZAKLADKI: dict[str, list] = json.loads(ZAKLADKI_PLIK.read_text())
except Exception:
    ZAKLADKI = {ULUBIONE: []}


def _zapisz_zakladki():
    ZAKLADKI_PLIK.write_text(json.dumps(ZAKLADKI, ensure_ascii=False, indent=1))


def zakladka(d):
    """{nazwa, kafel, tak} — dodaj/usuń kafel (kanał/podcast/playlista) w zakładce; {nazwa, usun_zakladke}."""
    nazwa = (d.get("nazwa") or ULUBIONE).strip()[:60]
    if d.get("usun_zakladke"):
        if nazwa != ULUBIONE:
            ZAKLADKI.pop(nazwa, None)
    else:
        k = d.get("kafel") or {}
        if k.get("typ") not in ("kanal", "podcast", "playlista", "album", "wykonawca") or not k.get("id"):
            raise Blad("This cannot be added to a bookmark.")
        lista = [x for x in ZAKLADKI.setdefault(nazwa, []) if x["id"] != k["id"]]
        if d.get("tak", True):
            lista.insert(0, {x: k.get(x) for x in ("typ", "id", "tytul", "podpis", "okladka")})
        ZAKLADKI[nazwa] = lista
        if k["typ"] == "podcast" and nazwa == ULUBIONE:
            podcast_w_bibliotece(k["id"], d.get("tak", True))
    _zapisz_zakladki()
    return {"ok": True, "zakladki": {n: [x["id"] for x in l] for n, l in ZAKLADKI.items()}}


def podcast_w_bibliotece(pid, tak):
    """Ulubiony podcast = zapisany w bibliotece YT Music (widać go też w Metroliście na telefonie)."""
    if not ytm_konto or konto_wygaslo:
        return
    from ytmusicapi.models.content.enums import LikeStatus
    try:
        ytm_konto.rate_playlist(pid[4:] if pid.startswith("MPSP") else pid,
                                LikeStatus.LIKE if tak else LikeStatus.INDIFFERENT)
    except Exception as e:
        log("podcast → biblioteka:", e)


# ── „Do posłuchania” (własna kolejka odcinków/filmów) + nowe z ulubionych ──

DP_PLIK, WYS_PLIK = KAT / "do_posluchania.json", KAT / "wysluchane.json"
DP_DOMYSLNA = "🕒 Listen later"
try:
    DO_POSL: dict[str, list[dict]] = json.loads(DP_PLIK.read_text())   # nazwa listy → odcinki
    if isinstance(DO_POSL, list):
        DO_POSL = {DP_DOMYSLNA: DO_POSL}
except Exception:
    DO_POSL = {DP_DOMYSLNA: []}


def _dp_ids():
    return {n: [x["videoId"] for x in l] for n, l in DO_POSL.items()}
try:
    WYSLUCHANE: list[str] = json.loads(WYS_PLIK.read_text())
except Exception:
    WYSLUCHANE = []


def do_posluchania(d):
    """{utwor, tak, lista?} — dodaj/usuń odcinek z listy (domyślnie „Do posłuchania”); {lista, usun_liste};
    {utwor, wszedzie: true, tak: false} — usuń ze wszystkich list."""
    nazwa = (d.get("lista") or DP_DOMYSLNA).strip()[:60]
    if d.get("usun_liste"):
        if nazwa != DP_DOMYSLNA:
            DO_POSL.pop(nazwa, None)
    else:
        u = d.get("utwor") or {}
        vid = u.get("videoId")
        if not vid:
            raise Blad("No episode given.")
        for n in (list(DO_POSL) if d.get("wszedzie") else [nazwa]):
            l = [x for x in DO_POSL.setdefault(n, []) if x["videoId"] != vid]
            if d.get("tak", True):
                l.append({k: v for k, v in {**(META.get(vid) or {}), **u}.items() if v is not None and k in
                          ("videoId", "tytul", "wykonawca", "album", "okladka", "dlugosc", "odcinek", "film")})
            DO_POSL[n] = l
    DP_PLIK.write_text(json.dumps(DO_POSL, ensure_ascii=False))
    return {"ok": True, "listy": _dp_ids()}


def wysluchany(vid):
    """Dosłuchany odcinek znika z „Do posłuchania” i z „Nowych”."""
    if vid not in WYSLUCHANE:
        WYSLUCHANE.append(vid)
        WYS_PLIK.write_text(json.dumps(WYSLUCHANE[-3000:]))
    if any(x["videoId"] == vid for l in DO_POSL.values() for x in l):
        do_posluchania({"utwor": {"videoId": vid}, "tak": False, "wszedzie": True})


_nowe = {"czas": 0.0, "dane": []}


def nowe_z_ulubionych(ile=3):
    """Po kilka najnowszych odcinków z każdego ulubionego kanału i podcastu, na przemian (cache 30 min)."""
    ul = [k for k in ZAKLADKI.get(ULUBIONE, []) if k["typ"] in ("kanal", "podcast")]
    if not ul:
        return []
    if time.time() - _nowe["czas"] > 1800 or _nowe.get("klucz") != [k["id"] for k in ul]:
        def jeden(k):
            try:
                return (kanal(k["id"]) if k["typ"] == "kanal" else lista("podcast", k["id"]))["utwory"][:ile + 3]
            except Exception as e:
                log("nowe:", k["tytul"], e)
                return []
        with ThreadPoolExecutor(6) as p:
            listy = list(p.map(jeden, ul))
        _nowe.update(czas=time.time(), dane=listy, klucz=[k["id"] for k in ul])
    listy = [[u for u in l if u["videoId"] not in WYSLUCHANE][:ile] for l in _nowe["dane"]]
    return [l[i] for i in range(ile) for l in listy if i < len(l)]


def sekcje_sluchania():
    sek = [{"tytul": n, "utwory": l, "po_kolei": True, "lista": n} for n, l in DO_POSL.items() if l]
    try:
        nowe = nowe_z_ulubionych()
        if nowe:
            sek.append({"tytul": "🆕 New from favourites", "utwory": nowe, "po_kolei": True})
    except Exception as e:
        log("nowe z ulubionych:", e)
    return sek


def kanaly_start():
    sek = sekcje_sluchania() + [{"tytul": n, "kafle": l, "zakladka": n} for n, l in ZAKLADKI.items() if l or n == ULUBIONE]
    try:
        s = subskrypcje()
        if s:
            sek.append({"tytul": "Your subscriptions", "kafle": s})
    except Exception as e:
        log("subskrypcje:", e)
    return {"sekcje": sek, "zakladki": list(ZAKLADKI), "do_posluchania": _dp_ids()}


# ── internet radio (radio-browser.info directory; favourite stations kept in a local file) ──

RADIO_PLIK = KAT / "radio.json"   # bieżąca stacja przeżywa restart serwera (mpv gra dalej)
try:
    RADIO: dict = json.loads(RADIO_PLIK.read_text())   # {url, nazwa, okladka, id?}
except Exception:
    RADIO = {}
RADIA = [  # polecane (z pompki 2026-09-28); pls = aktualne adresy od nadawcy, url = zapasowy
    {"id": "rmf-classic", "nazwa": "RMF Classic", "opis": "Muzyka filmowa i klasyczna",
     "pls": "http://www.rmfon.pl/n/rmfclassic.pls", "url": "https://rs101-krk.rmfstream.pl/rmf_classic"},
    {"id": "dwojka", "nazwa": "Polskie Radio Dwójka", "opis": "Klasyka, jazz, kultura",
     "url": "https://stream12.polskieradio.pl/pr2/pr2.sdp/playlist.m3u8"},
    {"id": "classic-fm", "nazwa": "Classic FM", "opis": "Klasyka z Londynu",
     "url": "https://media-ice.musicradio.com/ClassicFMMP3"},
    {"id": "nowy-swiat", "nazwa": "Radio Nowy Świat", "opis": "Muzyka i rozmowy",
     "url": "https://stream.rcs.revma.com/ypqt40u0x1zuv"},
    {"id": "radio-357", "nazwa": "Radio 357", "opis": "Muzyka i rozmowy",
     "url": "https://stream.rcs.revma.com/ye5kghkgcm0uv"},
    {"id": "trojka", "nazwa": "Polskie Radio Trójka", "opis": "Program 3",
     "url": "https://stream13.polskieradio.pl/pr3/pr3.sdp/playlist.m3u8"},
    {"id": "chillizet", "nazwa": "Chillizet", "opis": "Spokojna muzyka",
     "url": "https://ch.cdn.eurozet.pl/chi-net.mp3"},
    {"id": "rmf-fm", "nazwa": "RMF FM", "opis": "Przeboje",
     "pls": "http://www.rmfon.pl/n/rmffm.pls", "url": "https://rs201-krk.rmfstream.pl/rmf_fm"},
    {"id": "radio-zet", "nazwa": "Radio ZET", "opis": "Przeboje",
     "url": "https://zt.cdn.eurozet.pl/zet-net.mp3"},
    {"id": "tok-fm", "nazwa": "TOK FM", "opis": "Informacje i rozmowy",
     "url": "https://radiostream.pl/tuba10-1.mp3"},
]


def _adres_radia(r):
    if r.get("pls"):
        try:
            with urllib.request.urlopen(r["pls"], timeout=5) as o:
                pliki = re.findall(r"^File\d+=(\S+)", o.read().decode("utf-8", "replace"), re.M)
            return next((u for u in pliki if u.startswith("https")), pliki[0])
        except Exception as e:
            log("radio pls:", r["id"], e)
    return r["url"]


def _radio_browser(sciezka):
    for host in ("de1", "de2", "fi1", "nl1"):
        try:
            req = urllib.request.Request(f"https://{host}.api.radio-browser.info/json/{sciezka}",
                                         headers={"User-Agent": "lira-serwer/1.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                return json.loads(r.read())
        except Exception as e:
            log("radio-browser", host, e)
    return []


def _stacja(s):
    return {"typ": "stacja", "id": s.get("stationuuid"), "tytul": (s.get("name") or "").strip(),
            "url": s.get("url_resolved") or s.get("url"), "okladka": s.get("favicon") or None,
            "podpis": " · ".join(filter(None, [s.get("codec"), f"{s['bitrate']} kbps" if s.get("bitrate") else ""]))}


def szukaj_stacji(q, ile=12):
    q = urllib.parse.quote(q)
    w = _radio_browser(f"stations/search?name={q}&countrycode={REGION}&order=clickcount&reverse=true&hidebroken=true&limit={ile}") \
        or _radio_browser(f"stations/search?name={q}&order=clickcount&reverse=true&hidebroken=true&limit={ile}")
    return [_stacja(s) for s in w if s.get("url_resolved") or s.get("url")]


RADIO_ULUB_PLIK = KAT / "radio_ulubione.json"


def ulubione_stacje(d=None):
    """Favourite radio stations, local file. With d={stacja:{nazwa,url,okladka,rid}, tak} adds/removes one."""
    try:
        lista = json.loads(RADIO_ULUB_PLIK.read_text())
    except Exception:
        lista = []
    if d is None:
        return lista
    st = d.get("stacja") or {}
    lista = [x for x in lista if x.get("nazwa") != st.get("nazwa")]
    if d.get("tak", True) and st.get("url"):
        lista.append({"nazwa": st["nazwa"], "url": st["url"], "okladka": st.get("okladka"), "id": st.get("rid")})
    RADIO_ULUB_PLIK.write_text(json.dumps(lista, ensure_ascii=False))
    return {"ok": True}


def radio_start():
    sek = []
    try:
        ul = ulubione_stacje()
        sek.append({"tytul": "⭐ Favourite stations", "kafle": [{"typ": "stacja", "id": s["url"], "rid": s.get("id"), "tytul": s["nazwa"], "url": s["url"],
                                                                "okladka": s.get("okladka"), "podpis": ""} for s in ul]})
    except Exception as e:
        log("ulubione stacje:", e)
    sek.append({"tytul": "Recommended", "kafle": [{"typ": "stacja", "id": x["id"], "rid": x["id"], "tytul": x["nazwa"],
                                                "url": x["url"], "okladka": None, "podpis": x["opis"]} for x in RADIA]})
    pop = _radio_browser(f"stations/search?countrycode={REGION}&order=clickcount&reverse=true&hidebroken=true&limit=24")
    if pop:
        sek.append({"tytul": "Popular in your country", "kafle": [_stacja(s) for s in pop]})
    return {"sekcje": sek}


def graj_radio(url, nazwa, okladka=None, id_=None):
    if id_:  # polecana stacja — świeży adres od nadawcy
        x = next((x for x in RADIA if x["id"] == id_), None)
        if not x:
            raise Blad("no such radio")
        url, nazwa = _adres_radia(x), nazwa or x["nazwa"]
    if not re.match(r"https?://", url or ""):
        raise Blad("Invalid radio stream address.")
    with blokada:
        RADIO.clear()
        RADIO.update(url=url, nazwa=nazwa or "Radio", okladka=okladka, id=id_)
        RADIO_PLIK.write_text(json.dumps(RADIO, ensure_ascii=False))
        stan_ladowania.update(id=None, blad=None)
        mpv("playlist-clear")
        mpv("loadfile", url, "replace")
        mpv("set_property", "pause", False)
    for _ in range(40):
        if mpv.get("time-pos") is not None or (mpv.get("audio-bitrate") or 0) > 0:
            return True
        time.sleep(0.25)
    raise Blad(f"Station “{nazwa}” is not responding (stream did not start).")


def _to_radio(sciezka):
    return bool(RADIO) and sciezka == RADIO.get("url")


LUBIANE: set[str] = set()        # videoId z „Polubione” (LM) — serduszko w oknie
_lubiane = {"czas": 0.0}


def lubiane():
    """Zbiór polubionych; odświeżany co 10 min (lubić można też w telefonie)."""
    if ytm_konto and not konto_wygaslo and time.time() - _lubiane["czas"] > 600:
        _lubiane["czas"] = time.time()
        try:
            t = ytm.get_playlist("LM", limit=5000).get("tracks") or []
            LUBIANE.clear()
            LUBIANE.update(x["videoId"] for x in t if x.get("videoId"))
        except Exception as e:
            log("lubiane:", e)
    return LUBIANE


def _na_koncie():
    if not ytm_konto or konto_wygaslo:
        raise Blad("Sign in to YouTube Music first (button on the start page).")
    return ytm_konto


def polub(vid, tak):
    from ytmusicapi.models.content.enums import LikeStatus
    _na_koncie().rate_song(vid, LikeStatus.LIKE if tak else LikeStatus.INDIFFERENT)
    (LUBIANE.add if tak else LUBIANE.discard)(vid)
    return {"ok": True, "polubiony": tak}


def playlisty_do_edycji():
    """Własne playlisty (PL…) — do nich da się dopisywać; zapisane cudze/radia pomijamy."""
    out = []
    for x in _na_koncie().get_library_playlists(limit=200) or []:
        pid = x.get("playlistId") or ""
        if pid.startswith("PL"):
            out.append({"id": pid, "tytul": x.get("title"), "ile": x.get("count")})
    return out


def dodaj_do(pid, vid):
    r = _na_koncie().add_playlist_items(pid, [vid], duplicates=False)
    if isinstance(r, dict) and r.get("status") == "STATUS_SUCCEEDED":
        return {"ok": True}
    if "duplicate" in json.dumps(r).lower() or "already" in json.dumps(r).lower():
        raise Blad("This track is already on that playlist.")
    raise Blad("YouTube did not add the track to the playlist.")


def nowa_playlista(tytul, vid):
    pid = _na_koncie().create_playlist(tytul.strip() or "Nowa playlista", "", "PRIVATE",
                                       video_ids=[vid] if vid else None)
    if not isinstance(pid, str):
        raise Blad("YouTube did not create the playlist.")
    _start["dane"] = None
    return {"ok": True, "id": pid}


def zapisz_ciasteczka():
    """konto.json (nagłówek Cookie z okna) → cookies.txt dla yt-dlp w mpv."""
    linie = ["# Netscape HTTP Cookie File"]
    if KONTO.exists():
        for para in json.loads(KONTO.read_text()).get("cookie", "").split("; "):
            n, _, v = para.partition("=")
            if n:
                linie.append("\t".join([".youtube.com", "TRUE", "/", "TRUE", "2147483647", n, v]))
    CIASTKA.write_text("\n".join(linie) + "\n")
    CIASTKA.chmod(0o600)


def zaloguj(d):
    global ytm_konto, ytm_konto_pl, konto_wygaslo
    cookie = (d.get("cookie") or "").strip()
    if "SAPISID=" not in cookie:
        raise Blad("This does not look like a signed-in YouTube session.")
    naglowki = {"cookie": cookie, "authorization": "SAPISIDHASH 0", "x-goog-authuser": "0",
                "user-agent": d.get("user_agent") or "Mozilla/5.0", "accept": "*/*",
                "content-type": "application/json", "origin": "https://music.youtube.com",
                "x-origin": "https://music.youtube.com"}
    tmp = KONTO.with_suffix(".tmp")
    tmp.write_text(json.dumps(naglowki))
    tmp.chmod(0o600)
    proba = YTMusic(str(tmp), location=REGION)
    proba.get_library_playlists(limit=1)   # sprawdź, zanim zapiszemy
    tmp.replace(KONTO)
    zapisz_ciasteczka()
    ytm_konto, konto_wygaslo = proba, False
    ytm_konto_pl = _po_polsku(YTMusic(str(KONTO), location=REGION))
    _start["dane"] = None
    log("konto: zalogowano")
    return {"ok": True}


def wyloguj():
    global ytm_konto
    KONTO.unlink(missing_ok=True)
    zapisz_ciasteczka()
    ytm_konto = None
    _start["dane"] = None
    return {"ok": True}


# ── mpv ───────────────────────────────────────────────────────────────────

class Mpv:
    """mpv is started on demand as a child process; this talks to it over a JSON IPC socket."""
    proc = None

    def _gotowy(self):
        try:
            with socket.socket(socket.AF_UNIX) as t:
                t.settimeout(1)
                t.connect(str(SOCK))
            return True
        except OSError:
            return False

    def _start(self):
        surowe = f"yes-playlist=,cookies={CIASTKA}"
        if shutil.which("node") and not shutil.which("deno"):
            surowe += ",js-runtimes=node"
        if not CIASTKA.exists():
            CIASTKA.write_text("# Netscape HTTP Cookie File\n")
            CIASTKA.chmod(0o600)
        SOCK.unlink(missing_ok=True)
        try:
            self.proc = subprocess.Popen(
                [MPV, "--idle=yes", "--no-video", "--no-terminal", "--force-window=no", "--volume=70",
                 f"--input-ipc-server={SOCK}", f"--script-opts=ytdl_hook-ytdl_path={YTDLP}",
                 "--ytdl-format=bestaudio/best", f"--ytdl-raw-options={surowe}", "--prefetch-playlist=yes",
                 "--cache=yes", "--audio-client-name=Lira", "--title=Lira", "--load-scripts=no"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise Blad("mpv is not installed (or not in PATH).")

    def _uruchom(self):
        if self._gotowy():
            return
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        for _ in range(60):
            if self._gotowy():
                return
            time.sleep(0.1)
        raise Blad("Player is not running (mpv failed to start).")

    def zamknij(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        SOCK.unlink(missing_ok=True)

    def __call__(self, *polecenie):
        self._uruchom()
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(5)
            s.connect(str(SOCK))
            s.sendall((json.dumps({"command": list(polecenie), "request_id": 7}) + "\n").encode())
            buf = b""
            while True:
                kawalek = s.recv(65536)
                if not kawalek:
                    raise Blad("mpv closed the connection")
                buf += kawalek
                while b"\n" in buf:
                    linia, buf = buf.split(b"\n", 1)
                    o = json.loads(linia)
                    if o.get("request_id") == 7:
                        return o.get("data") if o.get("error") == "success" else None

    def get(self, p):
        return self("get_property", p)


mpv = Mpv()
atexit.register(mpv.zamknij)



def _vid(sciezka):
    m = re.search(r"\[([A-Za-z0-9_-]{11})\]\.\w+$", sciezka or "") or \
        re.search(r"(?:v=|youtu\.be/|/strumien/|/plik/)([A-Za-z0-9_-]{11})", sciezka or "")
    return m.group(1) if m else None


def kolejka_ids():
    return [_vid(e.get("filename")) for e in mpv.get("playlist") or []]


def graj(utwory, od=0, sekunda=0, czekaj=True):
    """Zastąp kolejkę listą utworów i graj od indeksu `od`. Pojedynczy utwór → dokłada radio."""
    with blokada:
        if len(utwory) == 1 and not (META.get(utwory[0]["videoId"]) or {}).get("odcinek"):
            try:
                utwory = utwory + radio(utwory[0]["videoId"], bez={utwory[0]["videoId"]})
            except Exception as e:
                log("radio:", e)
        start = utwory[od]["videoId"]
        stan_ladowania.update(id=start, od=time.time(), blad=None)
        mpv("playlist-clear")
        mpv("loadfile", adres(start), "replace")
        mpv("set_property", "pause", False)
        for i, u in enumerate(utwory):
            if i != od:
                mpv("loadfile", adres(u["videoId"]), "append")
        for i in range(od):  # utwory sprzed klikniętego wracają przed niego
            mpv("playlist-move", i + 1, i)
    if czekaj:
        return czekaj_na_start(start, sekunda)
    threading.Thread(target=_w_tle, args=(czekaj_na_start, start, sekunda), daemon=True).start()


def _w_tle(f, *a):
    try:
        f(*a)
    except Exception as e:
        log("w tle:", e)


def czekaj_na_start(vid, sekunda=0, limit=45):  # yt-dlp przy 429 potrafi myśleć ~20 s
    for _ in range(limit * 4):
        if _vid(mpv.get("path")) == vid and mpv.get("time-pos") is not None:
            if sekunda > 3:
                mpv("seek", sekunda, "absolute")
            stan_ladowania["id"] = None
            return True
        time.sleep(0.25)
    stan_ladowania.update(id=None, blad="Playback failed: YouTube returned no audio (yt-dlp).")
    raise Blad(stan_ladowania["blad"])


def stan():
    try:
        pl = mpv.get("playlist") or []
    except Blad:
        pl = []
    poz = mpv.get("playlist-pos") if pl else None
    teraz = None
    if poz is not None and poz >= 0 and _to_radio(pl[poz].get("filename")):
        meta = mpv.get("metadata") or {}
        icy = (meta.get("icy-title") or "").strip(" -")
        teraz = {"videoId": None, "radio": RADIO.get("id") or True, "stacja": RADIO["nazwa"], "url": RADIO["url"],
                 "tytul": icy or RADIO["nazwa"], "wykonawca": "📻 " + RADIO["nazwa"],
                 "okladka": RADIO.get("okladka"), "sekunda": 0, "sekunda_dokl": 0, "dlugosc": None,
                 "gra": not mpv.get("pause"), "laduje": mpv.get("time-pos") is None, "polubiony": False}
    elif poz is not None and poz >= 0:
        vid = _vid(pl[poz].get("filename"))
        m = META.get(vid, {})
        tpos = mpv.get("time-pos")
        teraz = {**m, "videoId": vid, "tytul": m.get("tytul") or mpv.get("media-title") or "",
                 "wykonawca": m.get("wykonawca") or "", "okladka": m.get("okladka"),
                 "sekunda": int(tpos or 0), "sekunda_dokl": tpos,
                 "dlugosc": int(mpv.get("duration") or 0) or m.get("dlugosc"),
                 "gra": not mpv.get("pause"), "laduje": tpos is None,
                 "polubiony": vid in LUBIANE, "odcinek": bool(m.get("odcinek"))}
    kol = []
    for i, e in enumerate(pl):
        vid = _vid(e.get("filename"))
        m = META.get(vid, {"videoId": vid, "tytul": e.get("title") or vid}) if vid else \
            {"videoId": None, "tytul": RADIO.get("nazwa") if _to_radio(e.get("filename")) else e.get("filename"),
             "wykonawca": "📻 radio", "okladka": RADIO.get("okladka")}
        kol.append({**m, "biezacy": i == poz})
    return {"nazwa": "lira", "wersja": WERSJA, "utwor": teraz, "kolejka": kol, "pozycja": poz,
            "konto": "wygaslo" if konto_wygaslo else ("tak" if ytm_konto else "nie"),
            "glosnosc": mpv.get("volume"), "blad": stan_ladowania["blad"]}


POZYCJE_PLIK = KAT / "pozycje.json"
try:
    POZYCJE: dict[str, int] = json.loads(POZYCJE_PLIK.read_text())   # odcinek → sekunda
except Exception:
    POZYCJE = {}
_odcinek = {"vid": None}


def pilnuj_odcinka(vid):
    """Podcasty: po starcie odcinka wróć tam, gdzie skończyłeś; w trakcie zapisuj miejsce."""
    if not vid or not (META.get(vid) or {}).get("odcinek"):
        _odcinek["vid"] = vid
        return
    t = mpv.get("time-pos")
    if t is None:
        return
    if _odcinek["vid"] != vid:  # właśnie wystartował
        _odcinek["vid"] = vid
        if POZYCJE.get(vid, 0) > 30 and t < 15:
            mpv("seek", POZYCJE[vid] - 5, "absolute")
        return
    dl = mpv.get("duration") or 0
    zmiana = False
    if dl and t > dl - 90:  # dosłuchany
        zmiana = POZYCJE.pop(vid, None) is not None
        if vid not in WYSLUCHANE:
            wysluchany(vid)
    elif t > 30 and abs(POZYCJE.get(vid, 0) - t) > 20:
        POZYCJE[vid] = int(t)
        zmiana = True
    if zmiana:
        POZYCJE_PLIK.write_text(json.dumps(dict(list(POZYCJE.items())[-500:])))


def dokladaj_radio():
    """Jak YT Music: gdy kolejka się kończy, dociągnij radio ostatniego utworu."""
    zapisane = len(META)
    while True:
        time.sleep(5)
        lubiane()
        if len(META) != zapisane:
            try:
                ids = set(kolejka_ids())
                trzymaj = dict(list(META.items())[-3000:])
                trzymaj.update({k: v for k, v in META.items() if k in ids})
                META_PLIK.write_text(json.dumps(trzymaj, ensure_ascii=False))
                zapisane = len(META)
            except Exception as e:
                log("meta:", e)
        try:
            ids = kolejka_ids()
            poz = mpv.get("playlist-pos")
            vid = ids[poz] if ids and poz is not None and 0 <= poz < len(ids) else None
            pilnuj_odcinka(vid)
            if not vid or (META.get(vid) or {}).get("odcinek"):
                continue  # po podcaście/filmie z kanału i przy radiu nie dokładamy muzyki
            if ids and poz is not None and poz >= len(ids) - 3 and not blokada.locked():
                nowe = radio(ids[-1], bez=set(ids))
                for u in nowe[:20]:
                    mpv("loadfile", adres(u["videoId"]), "append")
                log(f"radio: +{min(len(nowe), 20)}")
        except Exception as e:
            log("dokladaj_radio:", e)
            time.sleep(30)


# ── aktualizacje: Google zmienia YouTube, poprawki przychodzą w yt-dlp/ytmusicapi ──

# ── pośrednik strumienia (tryb posrednik) ─────────────────────────────────

_zrodla: dict[str, dict] = {}     # videoId → {url, naglowki, rozmiar, czas}
_zrodla_blokada = threading.Lock()
KAWALEK = 4 * 1024 * 1024         # googlevideo nie lubi otwartych zakresów — prosimy po 4 MB


def zrodlo(vid, odswiez=False):
    with _zrodla_blokada:
        z = _zrodla.get(vid)
        if z and not odswiez and time.time() - z["czas"] < 3 * 3600:
            return z
    cmd = [YTDLP, "-j", "--no-warnings", "-f", "bestaudio/best", "--no-playlist",
           "--cookies", str(CIASTKA), URL + vid]
    if shutil.which("node") and not shutil.which("deno"):
        cmd[1:1] = ["--js-runtimes", "node"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise Blad("yt-dlp: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200])
    i = json.loads(r.stdout)
    z = {"url": i["url"], "naglowki": i.get("http_headers") or {}, "czas": time.time(),
         "rozmiar": i.get("filesize") or i.get("filesize_approx"), "typ": "audio/" + (i.get("ext") or "webm")}
    with _zrodla_blokada:
        _zrodla[vid] = z
        for k in [k for k, v in _zrodla.items() if time.time() - v["czas"] > 4 * 3600]:
            del _zrodla[k]
    return z


def _pobierz(z, a, b):
    req = urllib.request.Request(z["url"], headers={**z["naglowki"], "Range": f"bytes={a}-{b}"})
    return urllib.request.urlopen(req, timeout=20)


def strumien(h, vid):
    z = zrodlo(vid)
    if not z["rozmiar"]:  # rozmiar z nagłówka pierwszego kawałka
        with _pobierz(z, 0, 0) as o:
            z["rozmiar"] = int(o.headers.get("Content-Range", "/0").split("/")[-1] or 0)
    total = z["rozmiar"]
    rng = re.match(r"bytes=(\d*)-(\d*)", h.headers.get("Range") or "")
    a = int(rng.group(1)) if rng and rng.group(1) else 0
    b = int(rng.group(2)) if rng and rng.group(2) else total - 1
    b = min(b, total - 1)
    h.send_response(206 if rng else 200)
    h.send_header("Content-Type", z["typ"])
    h.send_header("Accept-Ranges", "bytes")
    h.send_header("Content-Length", str(b - a + 1))
    if rng:
        h.send_header("Content-Range", f"bytes {a}-{b}/{total}")
    h.end_headers()
    poz = a
    while poz <= b:
        kon = min(poz + KAWALEK - 1, b)
        try:
            o = _pobierz(z, poz, kon)
        except urllib.error.HTTPError as e:
            if e.code != 403:
                raise
            z = zrodlo(vid, odswiez=True)   # link wygasł — nowy i dalej
            o = _pobierz(z, poz, kon)
        with o:
            while True:
                kaw = o.read(65536)
                if not kaw:
                    break
                try:
                    h.wfile.write(kaw)
                except (BrokenPipeError, ConnectionResetError):
                    return   # mpv przewinął albo przeszedł dalej
                poz += len(kaw)


# ── HTTP ──────────────────────────────────────────────────────────────────

def _cialo(h):
    if getattr(h, "_cialo_zapas", None) is not None:   # już przeczytane przez _do_mozgu
        return json.loads(h._cialo_zapas or b"{}")
    n = int(h.headers.get("Content-Length") or 0)
    return json.loads(h.rfile.read(n) or b"{}") if n else {}


def _pojedynczy(d):
    vid = d.get("videoId")
    if not vid:
        q = d.get("zapytanie") or " ".join(filter(None, [d.get("tytul"), d.get("wykonawca")]))
        w = szukaj(q)["utwory"] if q else []
        if not w:
            raise Blad("Nothing found.")
        vid = w[0]["videoId"]
    return META.get(vid) or {"videoId": vid}


def akcja(sciezka, d):
    if sciezka == "/api/graj":
        if d.get("playlista"):  # e.g. {"playlista": "LM", "losowo": true} = play liked songs shuffled
            ut = lista("playlista", d["playlista"])["utwory"]
            if not ut:
                raise Blad("This playlist is empty.")
            if d.get("losowo"):
                random.shuffle(ut)
            graj(ut, 0, czekaj=d.get("czekaj", True))
        elif d.get("utwory"):  # lista z widoku (playlista/album/wyniki) + indeks klikniętego
            ut = [META.get(v) or {"videoId": v} for v in d["utwory"]]
            graj(ut, int(d.get("od") or 0), int(d.get("sekunda") or 0), czekaj=d.get("czekaj", True))
        else:
            graj([_pojedynczy(d)], 0, int(d.get("sekunda") or 0), czekaj=d.get("czekaj", True))
    elif sciezka == "/api/nastepny":  # „graj jako następny”
        u = _pojedynczy(d)
        poz = mpv.get("playlist-pos")
        mpv("loadfile", adres(u["videoId"]), "append")
        if poz is not None and poz >= 0:
            mpv("playlist-move", len(kolejka_ids()) - 1, poz + 1)
    elif sciezka == "/api/pauza":
        mpv("set_property", "pause", True)
    elif sciezka == "/api/wznow":
        mpv("set_property", "pause", False)
    elif sciezka == "/api/przelacz":
        mpv("cycle", "pause")
    elif sciezka == "/api/dalej":
        mpv("playlist-prev" if d.get("wstecz") else "playlist-next", "force")
        mpv("set_property", "pause", False)
    elif sciezka == "/api/skocz":
        mpv("playlist-play-index", int(d["indeks"]))
        mpv("set_property", "pause", False)
    elif sciezka == "/api/usun":
        mpv("playlist-remove", int(d["indeks"]))
    elif sciezka == "/api/przewin":
        mpv("seek", float(d["sekunda"]), "absolute")
    elif sciezka == "/api/glosnosc":
        mpv("set_property", "volume", max(0, min(130, float(d["wartosc"]))))
    elif sciezka == "/api/odkrywaj/ocena":
        return odkrywaj_ocena(d)
    elif sciezka == "/api/polub":  # bez videoId = to, co teraz gra
        vid = d.get("videoId") or ((stan().get("utwor") or {}).get("videoId"))
        if not vid:
            raise Blad("Nothing from YouTube is playing, so there is nothing to like.")
        return {**polub(vid, bool(d.get("tak", True))), "utwor": META.get(vid)}
    elif sciezka == "/api/radio":  # {url, nazwa, okladka}
        graj_radio(d.get("url"), d.get("nazwa"), d.get("okladka"), d.get("id"))
    elif sciezka == "/api/zakladka":
        return zakladka(d)
    elif sciezka == "/api/do-posluchania":
        return do_posluchania(d)
    elif sciezka == "/api/radio/ulubione":  # {stacja, tak}
        return ulubione_stacje(d)
    elif sciezka == "/api/dodaj-do":
        return dodaj_do(d["playlistId"], d["videoId"])
    elif sciezka == "/api/nowa-playlista":
        return nowa_playlista(d.get("tytul") or "", d.get("videoId"))
    elif sciezka == "/api/zaloguj":
        return zaloguj(d)
    elif sciezka == "/api/wyloguj":
        return wyloguj()
    else:
        raise Blad("no such action")
    time.sleep(0.15)
    return stan()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _wolno(self):
        if not TOKEN or self.client_address[0] in LOOPBACK:
            return True
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        return self.headers.get("Authorization") == "Bearer " + TOKEN or q.get("t") == TOKEN

    def _wyslij(self, kod, tresc, typ="application/json; charset=utf-8", cache="no-store"):
        b = tresc if isinstance(tresc, bytes) else json.dumps(tresc, ensure_ascii=False).encode()
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(b)

    def _obsluz(self, metoda):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path in ("/", "/index.html"):
            return self._wyslij(200, (AQUI / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path.startswith("/czcionka/") and u.path.endswith(".woff2"):
            p = AQUI / "static" / "fonts" / Path(u.path).name
            if not p.is_file():
                return self._wyslij(404, {"detail": "not found"})
            return self._wyslij(200, p.read_bytes(), "font/woff2", "max-age=31536000, immutable")
        if not self._wolno():
            return self._wyslij(401, {"detail": "access token required"})
        if u.path.startswith("/strumien/"):
            vid = u.path.rsplit("/", 1)[-1]
            try:
                return strumien(self, vid)
            except Exception as e:
                log("stream:", e)
                return self._wyslij(502, {"detail": str(e)}) if not self.wfile.closed else None
        try:
            if metoda == "GET":
                if u.path == "/api/stan":
                    return self._wyslij(200, stan())
                if u.path == "/api/start":
                    return self._wyslij(200, [{"tytul": n, "utwory": l, "po_kolei": True, "lista": n}
                                              for n, l in DO_POSL.items() if l] + strona_glowna())
                if u.path == "/api/szukaj":
                    return self._wyslij(200, szukaj(q.get("q", "")))
                if u.path == "/api/do-posluchania":
                    return self._wyslij(200, _dp_ids())
                if u.path == "/api/radia":
                    return self._wyslij(200, [{k: r[k] for k in ("id", "nazwa", "opis")} for r in RADIA])
                if u.path == "/api/radio/ulubione":
                    return self._wyslij(200, ulubione_stacje())
                if u.path == "/api/radio/szukaj":
                    return self._wyslij(200, szukaj_stacji(q.get("q", "")))
                if u.path == "/api/odkrywaj":
                    return self._wyslij(200, odkrywaj())
                if u.path == "/api/wykonawca":  # click on an artist in the player -> artist page
                    return self._wyslij(200, wykonawca_utworu(q.get("vid", "")))
                if u.path == "/api/lista":
                    return self._wyslij(200, lista(q["typ"], q["id"]))
                if u.path == "/api/moje-playlisty":
                    return self._wyslij(200, playlisty_do_edycji())
                return self._wyslij(404, {"detail": "not found"})
            return self._wyslij(200, akcja(u.path, _cialo(self)))
        except Blad as e:
            return self._wyslij(409, {"detail": str(e)})
        except Exception as e:
            import traceback
            log("error", u.path, "".join(traceback.format_exception(e)[-4:]))
            return self._wyslij(500, {"detail": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        self._obsluz("GET")

    def do_POST(self):
        self._obsluz("POST")


WERSJA = "1"


def main():
    global WERSJA
    import hashlib
    WERSJA = hashlib.sha1((AQUI / "static" / "index.html").read_bytes()).hexdigest()[:10]
    try:
        zapisz_ciasteczka()
    except Exception as e:
        log("cookies:", e)
    try:
        mpv._uruchom()
    except Blad as e:
        log(e)
    threading.Thread(target=dokladaj_radio, daemon=True).start()
    serwer = ThreadingHTTPServer((HOST, PORT), H)
    signal.signal(signal.SIGTERM, lambda *a: (mpv.zamknij(), os._exit(0)))
    log(f"lira-serwer: http://{HOST}:{PORT}/" + (" (access token required from other hosts)" if TOKEN else ""))
    try:
        serwer.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
