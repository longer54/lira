//! Ikonka „Muzyka” w zasobniku + klawisze multimediów (MPRIS) dla lira-serwer.
//!
//! Steruje TAM, GDZIE GRA („Graj na:” — serwer, laptop, telefon…): wszystko idzie do lokalnego
//! serwera lira-serwer (127.0.0.1:26540), który w razie potrzeby przekazuje polecenia dalej.
//! Dlatego mpv muzyki ma wyłączone mpv-mpris (--load-scripts=no) — jeden odtwarzacz w MPRIS, ten.
//!
//! Zasobnik: klik = graj/pauza, środkowy klik = okno, menu = poprzedni/następny, „Graj na”, okno.

use ksni::menu::{RadioGroup, RadioItem, StandardItem, SubMenu};
use ksni::{MenuItem, ToolTip, Tray, TrayMethods};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::net::ToSocketAddrs;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use zbus::zvariant::{ObjectPath, OwnedValue, Value as ZValue};
use zbus::{interface, object_server::SignalEmitter};

/// Server address host:port: LIRA_SERVER, else the host:port of MUZYKA_URL, else 127.0.0.1:26540.
fn serwer() -> &'static str {
    static S: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    S.get_or_init(|| {
        if let Ok(v) = std::env::var("LIRA_SERWER").or_else(|_| std::env::var("LIRA_SERVER")) {
            if !v.trim().is_empty() { return v.trim().to_string(); }
        }
        if let Ok(u) = std::env::var("MUZYKA_URL") {
            let h = u.split("://").nth(1).unwrap_or(&u).split(['/', '?']).next().unwrap_or("");
            if !h.is_empty() { return h.to_string(); }
        }
        "127.0.0.1:26540".to_string()
    })
}
const SCIEZKA: &str = "/org/mpris/MediaPlayer2";

// ── HTTP do lokalnego lira-serwer (bez zależności — to tylko localhost) ─────────────
fn http(metoda: &str, sciezka: &str, cialo: Option<Value>) -> Option<Value> {
    let mut s = TcpStream::connect_timeout(&serwer().to_socket_addrs().ok()?.next()?, Duration::from_secs(2)).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(20))).ok()?;
    let b = cialo.map(|c| c.to_string()).unwrap_or_default();
    let zap = format!(
        "{metoda} {sciezka} HTTP/1.0\r\nHost: {}\r\nUser-Agent: muzyka-zasobnik\r\n\
         Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{b}", serwer(), b.len());
    s.write_all(zap.as_bytes()).ok()?;
    let mut odp = Vec::new();
    s.read_to_end(&mut odp).ok()?;
    let odp = String::from_utf8_lossy(&odp);
    let (_, tresc) = odp.split_once("\r\n\r\n")?;
    serde_json::from_str(tresc).ok()
}

fn polecenie(sciezka: &'static str, cialo: Value) {
    std::thread::spawn(move || { http("POST", sciezka, Some(cialo)); });
}

fn otworz_okno() {
    let bin = std::env::var("HOME").map(|h| format!("{h}/.local/bin/muzyka-okno")).unwrap_or_default();
    let _ = std::process::Command::new(bin).spawn();
}

// ── wspólny stan (odświeżany co sekundę) ────────────────────────────────────────
#[derive(Clone, Default, PartialEq)]
struct Utwor {
    id: String,
    tytul: String,
    wykonawca: String,
    okladka: String,
    dlugosc: f64,
}

#[derive(Clone, Default)]
struct Stan {
    jest: bool,             // serwer odpowiada
    utwor: Option<Utwor>,
    gra: bool,
    sekunda: f64,
    kiedy: Option<Instant>, // kiedy odczytaliśmy `sekunda`
    glosnosc: f64,
    cel: String,
    tu: String,
    cele: Vec<(String, bool)>,
}

impl Stan {
    fn pozycja_us(&self) -> i64 {
        let mut t = self.sekunda;
        if self.gra {
            if let Some(k) = self.kiedy { t += k.elapsed().as_secs_f64(); }
        }
        if let Some(u) = &self.utwor { if u.dlugosc > 0.0 { t = t.min(u.dlugosc); } }
        (t * 1e6) as i64
    }
}

type Wspolny = Arc<Mutex<Stan>>;

fn odczytaj() -> Stan {
    let Some(d) = http("GET", "/api/stan", None) else { return Stan::default() };
    let u = d.get("utwor").filter(|u| u.is_object());
    let tekst = |v: Option<&Value>, k: &str| v.and_then(|x| x.get(k)).and_then(|x| x.as_str()).unwrap_or("").to_string();
    Stan {
        jest: true,
        utwor: u.map(|u| Utwor {
            id: u.get("videoId").and_then(|x| x.as_str()).map(str::to_string)
                .unwrap_or_else(|| format!("radio{}", tekst(Some(u), "stacja").len())),
            tytul: tekst(Some(u), "tytul"),
            wykonawca: tekst(Some(u), "wykonawca"),
            okladka: tekst(Some(u), "okladka"),
            dlugosc: u.get("dlugosc").and_then(|x| x.as_f64()).unwrap_or(0.0),
        }),
        gra: u.and_then(|u| u.get("gra")).and_then(|x| x.as_bool()).unwrap_or(false),
        sekunda: u.and_then(|u| u.get("sekunda_dokl").or(u.get("sekunda"))).and_then(|x| x.as_f64()).unwrap_or(0.0),
        kiedy: Some(Instant::now()),
        glosnosc: d.get("glosnosc").and_then(|x| x.as_f64()).unwrap_or(70.0),
        cel: tekst(Some(&d), "cel"),
        tu: tekst(Some(&d), "tu"),
        cele: d.get("cele").and_then(|c| c.as_array()).map(|a| a.iter().map(|c| (
            c.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            c.get("online").and_then(|x| x.as_bool()).unwrap_or(false))).collect()).unwrap_or_default(),
    }
}

fn nazwa(id: &str) -> String {
    match id {
        "serwer" => "🔊 Serwer".into(),
        "laptop" => "💻 Laptop".into(),
        "telefon" => "📱 Telefon".into(),
        "tablet" => "📟 Tablet".into(),
        x => format!("💻 {x}"),
    }
}

// ── MPRIS: org.mpris.MediaPlayer2 + .Player ─────────────────────────────────────
struct Glowny;

#[interface(name = "org.mpris.MediaPlayer2")]
impl Glowny {
    fn raise(&self) { otworz_okno(); }
    fn quit(&self) {}
    #[zbus(property)] fn can_quit(&self) -> bool { false }
    #[zbus(property)] fn can_raise(&self) -> bool { true }
    #[zbus(property)] fn has_track_list(&self) -> bool { false }
    #[zbus(property)] fn identity(&self) -> String { "Lira".into() }
    #[zbus(property)] fn desktop_entry(&self) -> String { "lira-serwer".into() }
    #[zbus(property)] fn supported_uri_schemes(&self) -> Vec<String> { vec![] }
    #[zbus(property)] fn supported_mime_types(&self) -> Vec<String> { vec![] }
}

struct Gracz(Wspolny);

impl Gracz {
    fn stan(&self) -> Stan { self.0.lock().unwrap().clone() }
}

#[interface(name = "org.mpris.MediaPlayer2.Player")]
impl Gracz {
    fn next(&self) { polecenie("/api/dalej", json!({})); }
    fn previous(&self) {
        if self.stan().pozycja_us() > 5_000_000 { polecenie("/api/przewin", json!({"sekunda": 0})); }
        else { polecenie("/api/dalej", json!({"wstecz": true})); }
    }
    fn pause(&self) { polecenie("/api/pauza", json!({})); }
    fn play_pause(&self) { polecenie("/api/przelacz", json!({})); }
    fn stop(&self) { polecenie("/api/pauza", json!({})); }
    fn play(&self) { polecenie("/api/wznow", json!({})); }
    fn seek(&self, offset: i64) {
        let s = ((self.stan().pozycja_us() + offset).max(0) / 1_000_000) as i64;
        polecenie("/api/przewin", json!({"sekunda": s}));
    }
    fn set_position(&self, _track: ObjectPath<'_>, pozycja: i64) {
        polecenie("/api/przewin", json!({"sekunda": pozycja.max(0) / 1_000_000}));
    }
    fn open_uri(&self, _uri: String) {}

    #[zbus(property)]
    fn playback_status(&self) -> String {
        let s = self.stan();
        match (&s.utwor, s.gra) { (None, _) => "Stopped", (_, true) => "Playing", _ => "Paused" }.into()
    }
    #[zbus(property)] fn rate(&self) -> f64 { 1.0 }
    #[zbus(property)] fn set_rate(&self, _r: f64) {}
    #[zbus(property)] fn minimum_rate(&self) -> f64 { 1.0 }
    #[zbus(property)] fn maximum_rate(&self) -> f64 { 1.0 }
    #[zbus(property)]
    fn metadata(&self) -> HashMap<String, OwnedValue> {
        let mut m = HashMap::new();
        let Some(u) = self.stan().utwor else { return m };
        let sciezka = format!("/io/github/longer54/lira/{}", u.id.chars().filter(|c| c.is_ascii_alphanumeric()).collect::<String>());
        let mut wstaw = |k: &str, v: ZValue| { if let Ok(o) = OwnedValue::try_from(v) { m.insert(k.to_string(), o); } };
        if let Ok(p) = ObjectPath::try_from(sciezka) { wstaw("mpris:trackid", ZValue::from(p)); }
        wstaw("xesam:title", ZValue::from(u.tytul.clone()));
        wstaw("xesam:artist", ZValue::from(vec![u.wykonawca.clone()]));
        if !u.okladka.is_empty() { wstaw("mpris:artUrl", ZValue::from(u.okladka.clone())); }
        if u.dlugosc > 0.0 { wstaw("mpris:length", ZValue::from((u.dlugosc * 1e6) as i64)); }
        m
    }
    #[zbus(property)]
    fn volume(&self) -> f64 { (self.stan().glosnosc / 100.0).clamp(0.0, 1.3) }
    #[zbus(property)]
    fn set_volume(&self, v: f64) { polecenie("/api/glosnosc", json!({"wartosc": (v * 100.0).clamp(0.0, 130.0)})); }
    #[zbus(property(emits_changed_signal = "false"))]
    fn position(&self) -> i64 { self.stan().pozycja_us() }
    #[zbus(property)] fn can_go_next(&self) -> bool { true }
    #[zbus(property)] fn can_go_previous(&self) -> bool { true }
    #[zbus(property)] fn can_play(&self) -> bool { true }
    #[zbus(property)] fn can_pause(&self) -> bool { true }
    #[zbus(property)] fn can_seek(&self) -> bool { self.stan().utwor.map(|u| u.dlugosc > 0.0).unwrap_or(false) }
    #[zbus(property(emits_changed_signal = "const"))] fn can_control(&self) -> bool { true }

    #[zbus(signal)]
    async fn seeked(emitter: &SignalEmitter<'_>, pozycja: i64) -> zbus::Result<()>;
}

// ── powiadomienie z okładką przy zmianie utworu (jak Spotify) ─────────────────────
const POWIADOMIENIA: &str = "org.freedesktop.Notifications";

fn pobierz_okladke(url: &str) -> Option<String> {
    let kat = format!("{}/.cache/muzyka-zasobnik", std::env::var("HOME").ok()?);
    std::fs::create_dir_all(&kat).ok()?;
    let plik = format!("{kat}/okladka.jpg");
    let ok = std::process::Command::new("curl").args(["-sfL", "-m", "5", "-o", &plik, url])
        .status().map(|s| s.success()).unwrap_or(false);
    ok.then_some(plik)
}

async fn powiadom(p: &zbus::Connection, s: &Stan, poprzednie: u32) -> u32 {
    let Some(u) = &s.utwor else { return poprzednie };
    let okl = if u.okladka.is_empty() { None } else {
        let url = u.okladka.clone();
        tokio::task::spawn_blocking(move || pobierz_okladke(&url)).await.ok().flatten()
    };
    let mut wskazowki: HashMap<&str, ZValue> = HashMap::new();
    wskazowki.insert("desktop-entry", ZValue::from("lira-serwer"));
    wskazowki.insert("category", ZValue::from("x-gnome.music"));
    if let Some(o) = &okl { wskazowki.insert("image-path", ZValue::from(o.as_str())); }
    let tresc = if s.cel != s.tu && !s.cel.is_empty() {
        format!("{}\ngra na: {}", u.wykonawca, nazwa(&s.cel))
    } else { u.wykonawca.clone() };
    let odp = p.call_method(Some(POWIADOMIENIA), "/org/freedesktop/Notifications", Some(POWIADOMIENIA), "Notify",
        &("Lira", poprzednie, "lira-serwer", u.tytul.as_str(), tresc.as_str(),
          vec!["default", "Otwórz", "dalej", "⏭ Następny"], wskazowki, 5000i32)).await;
    odp.ok().and_then(|m| m.body().deserialize::<u32>().ok()).unwrap_or(poprzednie)
}

/// Kliknięcia w powiadomienie: „⏭ Następny” albo samo powiadomienie (= okno).
async fn sluchaj_powiadomien(p: zbus::Connection, moje: Arc<Mutex<u32>>) {
    use futures_util::StreamExt;
    let Ok(proxy) = zbus::Proxy::new(&p, POWIADOMIENIA, "/org/freedesktop/Notifications", POWIADOMIENIA).await else { return };
    let Ok(mut klik) = proxy.receive_signal("ActionInvoked").await else { return };
    while let Some(m) = klik.next().await {
        let Ok((id, akcja)) = m.body().deserialize::<(u32, String)>() else { continue };
        if id != *moje.lock().unwrap() { continue; }
        match akcja.as_str() {
            "dalej" => polecenie("/api/dalej", json!({})),
            _ => otworz_okno(),
        }
    }
}

// ── zasobnik ────────────────────────────────────────────────────────────────────
struct Zasobnik(Stan);

impl Tray for Zasobnik {
    fn id(&self) -> String { "lira-serwer".into() }
    fn icon_name(&self) -> String {
        "lira-serwer-zasobnik".into()   // złota lira (lira-serwer --instaluj kładzie ją do hicolor)
    }
    fn title(&self) -> String { "Lira".into() }
    fn tool_tip(&self) -> ToolTip {
        let s = &self.0;
        let (title, description) = match &s.utwor {
            _ if !s.jest => ("Lira".into(), "serwer lira-serwer nie odpowiada".into()),
            None => ("Lira".into(), "Nic nie gra — klik: otwórz".into()),
            Some(u) => (format!("{} {}", if s.gra { "▶" } else { "⏸" }, u.tytul),
                        format!("{}\ngra na: {}", u.wykonawca, nazwa(&s.cel))),
        };
        ToolTip { title, description, ..Default::default() }
    }
    fn activate(&mut self, _x: i32, _y: i32) {
        if self.0.utwor.is_some() { polecenie("/api/przelacz", json!({})); } else { otworz_okno(); }
    }
    fn secondary_activate(&mut self, _x: i32, _y: i32) { otworz_okno(); }
    fn scroll(&mut self, delta: i32, dir: ksni::Orientation) {
        if matches!(dir, ksni::Orientation::Vertical) {
            polecenie("/api/glosnosc", json!({"wartosc": (self.0.glosnosc - delta.signum() as f64 * 5.0).clamp(0.0, 130.0)}));
        }
    }
    fn menu(&self) -> Vec<MenuItem<Self>> {
        let s = &self.0;
        let mut m: Vec<MenuItem<Self>> = Vec::new();
        if let Some(u) = &s.utwor {
            let mut t: String = u.tytul.chars().take(48).collect();
            if !u.wykonawca.is_empty() { t = format!("{t} — {}", u.wykonawca.chars().take(30).collect::<String>()); }
            m.push(StandardItem { label: t, enabled: false, ..Default::default() }.into());
            m.push(MenuItem::Separator);
        }
        m.push(StandardItem {
            label: if s.gra { "⏸  Pauza".into() } else { "▶  Graj".into() },
            activate: Box::new(|_| polecenie("/api/przelacz", json!({}))), ..Default::default() }.into());
        m.push(StandardItem { label: "⏭  Następny".into(),
            activate: Box::new(|_| polecenie("/api/dalej", json!({}))), ..Default::default() }.into());
        m.push(StandardItem { label: "⏮  Poprzedni".into(),
            activate: Box::new(|_| polecenie("/api/dalej", json!({"wstecz": true}))), ..Default::default() }.into());
        if !s.cele.is_empty() {
            let cele = s.cele.clone();
            let wybrany = cele.iter().position(|(id, _)| *id == s.cel).unwrap_or(0);
            m.push(MenuItem::Separator);
            m.push(SubMenu {
                label: format!("Graj na: {}", nazwa(&s.cel)),
                submenu: vec![RadioGroup {
                    selected: wybrany,
                    select: Box::new(move |_: &mut Self, i| {
                        if let Some((id, _)) = cele.get(i) { polecenie("/api/cel", json!({"cel": id})); }
                    }),
                    options: s.cele.iter().map(|(id, online)| RadioItem {
                        label: format!("{}{}{}", nazwa(id),
                                       if *id == s.tu { " · tutaj" } else { "" },
                                       if *online { "" } else { " (wyłączony)" }),
                        enabled: *online || *id == s.cel, ..Default::default() }).collect(),
                }.into()],
                ..Default::default()
            }.into());
        }
        m.push(MenuItem::Separator);
        m.push(StandardItem { label: "Otwórz Muzykę".into(), icon_name: "lira-serwer".into(),
            activate: Box::new(|_| otworz_okno()), ..Default::default() }.into());
        m
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> zbus::Result<()> {
    let wspolny: Wspolny = Arc::new(Mutex::new(Stan::default()));

    let polaczenie = zbus::connection::Builder::session()?
        .name("org.mpris.MediaPlayer2.lira_serwer")?
        .serve_at(SCIEZKA, Glowny)?
        .serve_at(SCIEZKA, Gracz(wspolny.clone()))?
        .build().await?;

    // StatusNotifierWatcher bywa po zalogowaniu jeszcze niegotowy — ponawiamy.
    let zasobnik = loop {
        match Zasobnik(Stan::default()).spawn().await {
            Ok(h) => break h,
            Err(e) => {
                eprintln!("muzyka-zasobnik: rejestracja ikony nieudana ({e}), ponawiam za 2 s");
                tokio::time::sleep(Duration::from_secs(2)).await;
            }
        }
    };

    let gracz = polaczenie.object_server().interface::<_, Gracz>(SCIEZKA).await?;
    let id_powiadomienia = Arc::new(Mutex::new(0u32));
    tokio::spawn(sluchaj_powiadomien(polaczenie.clone(), id_powiadomienia.clone()));
    let mut pierwszy = true;
    let mut poprzedni = Stan::default();
    let mut klucz_menu = String::new();
    loop {
        let nowy = tokio::task::spawn_blocking(odczytaj).await.unwrap_or_default();
        *wspolny.lock().unwrap() = nowy.clone();

        let emiter = gracz.signal_emitter();
        let g = gracz.get().await;
        if nowy.utwor != poprzedni.utwor {
            let _ = g.metadata_changed(emiter).await;
            let _ = g.can_seek_changed(emiter).await;
        }
        // nowy utwór (albo start grania) → dymek; nie przy uruchomieniu i nie przy samym tytule radia bez grania
        let zmiana_utworu = nowy.utwor.as_ref().map(|u| (&u.id, &u.tytul)) != poprzedni.utwor.as_ref().map(|u| (&u.id, &u.tytul));
        if !pierwszy && nowy.gra && (zmiana_utworu || !poprzedni.gra && poprzedni.utwor.is_none()) {
            let stare = *id_powiadomienia.lock().unwrap();
            let id = powiadom(&polaczenie, &nowy, stare).await;
            *id_powiadomienia.lock().unwrap() = id;
        }
        pierwszy = false;
        if nowy.gra != poprzedni.gra || nowy.utwor.is_some() != poprzedni.utwor.is_some() {
            let _ = g.playback_status_changed(emiter).await;
        }
        if nowy.glosnosc != poprzedni.glosnosc { let _ = g.volume_changed(emiter).await; }
        // przewinięcie (skok pozycji > 3 s względem oczekiwanej) → Seeked, żeby paski w panelu nadążały
        if nowy.utwor.is_some() && nowy.utwor == poprzedni.utwor
            && (nowy.pozycja_us() - poprzedni.pozycja_us()).abs() > 3_000_000 {
            let _ = Gracz::seeked(emiter, nowy.pozycja_us()).await;
        }
        drop(g);

        let km = format!("{}|{}|{:?}|{}|{}|{:?}", nowy.jest, nowy.gra,
                         nowy.utwor.as_ref().map(|u| &u.id), nowy.cel, nowy.tu, nowy.cele);
        if km != klucz_menu {
            klucz_menu = km;
            let s = nowy.clone();
            zasobnik.update(move |z: &mut Zasobnik| z.0 = s).await;
        }
        poprzedni = nowy;
        tokio::time::sleep(Duration::from_millis(1000)).await;
    }
}
