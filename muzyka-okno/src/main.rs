//! Okno „Lira” — WebKitGTK z interfejsem lokalnego serwera `muzyka-bps` (backend, osobny projekt).
//! Samo okno nic nie gra: dźwięk robi mpv w usłudze `muzyka-bps-mpv`. Zamknięcie okna
//! NIE pauzuje muzyki („niech gra dalej”) — sterowanie zostaje w
//! zasobniku i klawiszach multimediów.
//!
//! Logowanie do YouTube Music (Twoje playlisty): przycisk w interfejsie otwiera tu
//! stronę logowania Google; gdy wrócimy na music.youtube.com, zabieramy ciasteczka
//! sesji z magazynu WebKita i oddajemy serwerowi (POST /api/zaloguj) — ytmusicapi
//! używa ich jak przeglądarka. Hasło wpisuje się tylko na stronie Google.
//!
//! Bez belki tytułowej (celowo): CSD z pustym, zerowym paskiem — zostają
//! krawędzie do zmiany rozmiaru, a przeciąganie i guziki robi złoty górny pasek strony
//! (wiadomości `okno`: przesun/min/max/zamknij).
use gtk::glib;
use gtk::prelude::*;
use std::cell::{Cell, RefCell};
use std::io::Write;
use std::process::{Command, Stdio};
use std::rc::Rc;
use javascriptcore::ValueExt;
use webkit2gtk::{
    CookieManager, CookieManagerExt, CookiePersistentStorage, LoadEvent, SettingsExt,
    UserContentManager, UserContentManagerExt, WebContext, WebView, WebViewExt,
    WebsiteDataManager, WebsiteDataManagerExt,
};

const APP_ID: &str = "io.github.longer54.Lira";
const START_URL: &str = "http://127.0.0.1:26540/";
const YTM: &str = "https://music.youtube.com";
const CSS: &str = "
.lira-pasek { min-height: 0; padding: 0; margin: 0; border: 0; background: #050404; box-shadow: none; }
decoration { border-radius: 0; box-shadow: 0 0 0 1px #3a2c10, 0 6px 22px rgba(0,0,0,.6); }
window.csd, window.csd > * { border-radius: 0; }
";

fn json_str(s: &str) -> String {
    let mut o = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            c if (c as u32) < 0x20 => {}
            c => o.push(c),
        }
    }
    o.push('"');
    o
}

/// Ciasteczka music.youtube.com → serwer. Wraca do interfejsu, gdy się udało.
fn oddaj_sesje(cm: &CookieManager, wv: &WebView, start_url: String) {
    let ua = WebViewExt::settings(wv)
        .and_then(|s| s.user_agent())
        .map(|s| s.to_string())
        .unwrap_or_default();
    let wv = wv.clone();
    cm.cookies(YTM, None::<&gtk::gio::Cancellable>, move |wynik| {
        let Ok(ciastka) = wynik else { return };
        let mut pary = Vec::new();
        for c in ciastka.iter() {
            let mut c = c.clone();
            if let (Some(n), Some(v)) = (c.name(), c.value()) {
                pary.push(format!("{n}={v}"));
            }
        }
        let cookie = pary.join("; ");
        if !cookie.contains("SAPISID=") {
            return; // jeszcze niezalogowany
        }
        let cialo = format!("{{\"cookie\":{},\"user_agent\":{}}}", json_str(&cookie), json_str(&ua));
        let ok = Command::new("curl")
            .args(["-sf", "-m", "20", "-X", "POST", "-H", "Content-Type: application/json",
                   "--data-binary", "@-", "http://127.0.0.1:26540/api/zaloguj"])
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .spawn()
            .and_then(|mut p| {
                p.stdin.take().unwrap().write_all(cialo.as_bytes())?;
                p.wait()
            })
            .map(|s| s.success())
            .unwrap_or(false);
        if ok {
            wv.load_uri(&start_url);
        }
    });
}

fn build_window(app: &gtk::Application) -> gtk::ApplicationWindow {
    // Usługa mogła nie wstać (pierwsze uruchomienie, restart po aktualizacji) — szturchamy.
    let _ = Command::new("systemctl").args(["--user", "start", "muzyka-bps"]).spawn();

    let mut data_dir = glib::user_data_dir();
    data_dir.push("muzyka-okno");
    let mut cache_dir = glib::user_cache_dir();
    cache_dir.push("muzyka-okno");
    let data_manager = WebsiteDataManager::builder()
        .base_data_directory(data_dir.to_string_lossy().as_ref())
        .base_cache_directory(cache_dir.to_string_lossy().as_ref())
        .build();
    let cm = data_manager.cookie_manager().expect("cookie manager");
    let mut ciastka = data_dir.clone();
    ciastka.push("cookies.sqlite");
    cm.set_persistent_storage(&ciastka.to_string_lossy(), CookiePersistentStorage::Sqlite);
    let web_context = WebContext::builder().website_data_manager(&data_manager).build();

    let url = std::env::var("MUZYKA_URL").unwrap_or_else(|_| START_URL.to_string());
    let ucm = UserContentManager::new();
    ucm.register_script_message_handler("okno");
    let webview = WebView::builder()
        .web_context(&web_context)
        .user_content_manager(&ucm)
        .build();
    webview.load_uri(&url);

    // Serwer jeszcze się podnosi → WebKit pokazuje błąd; ponawiamy co sekundę.
    let url2 = url.clone();
    webview.connect_load_failed(move |wv, _ev: LoadEvent, uri, _err| {
        if !uri.starts_with(&url2) {
            return false;
        }
        let wv = wv.clone();
        let url = url2.clone();
        glib::timeout_add_seconds_local_once(1, move || wv.load_uri(&url));
        true
    });

    // Po logowaniu Google przekierowuje na music.youtube.com → zabieramy sesję i wracamy.
    webview.connect_load_changed(move |wv, ev| {
        if ev == LoadEvent::Finished && wv.uri().is_some_and(|u| u.starts_with(YTM)) {
            oddaj_sesje(&cm, wv, url.clone());
        }
    });

    let window = gtk::ApplicationWindow::new(app);
    window.set_title("Lira");
    let pasek = gtk::Box::new(gtk::Orientation::Horizontal, 0);
    pasek.style_context().add_class("lira-pasek");
    window.set_titlebar(Some(&pasek));
    let css = gtk::CssProvider::new();
    if css.load_from_data(CSS.as_bytes()).is_ok() {
        gtk::StyleContext::add_provider_for_screen(
            &gtk::prelude::WidgetExt::screen(&window).expect("ekran"),
            &css,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
    }

    // Ostatnie wciśnięcie myszy — begin_move_drag potrzebuje jego przycisku, pozycji i czasu.
    let klik: Rc<Cell<Option<(u32, f64, f64, u32)>>> = Rc::new(Cell::new(None));
    let klik2 = klik.clone();
    webview.connect_button_press_event(move |_, ev| {
        let (x, y) = ev.root();
        klik2.set(Some((ev.button(), x, y, ev.time())));
        glib::Propagation::Proceed
    });
    let okno = window.downgrade();
    ucm.connect_script_message_received(Some("okno"), move |_, wynik| {
        let Some(win) = okno.upgrade() else { return };
        let co = wynik.js_value().map(|v| v.to_str().to_string()).unwrap_or_default();
        match co.as_str() {
            "przesun" => {
                if let Some((b, x, y, t)) = klik.get() {
                    win.begin_move_drag(b as i32, x as i32, y as i32, t);
                }
            }
            "min" => win.iconify(),
            "max" => {
                if win.is_maximized() {
                    win.unmaximize()
                } else {
                    win.maximize()
                }
            }
            "zamknij" => win.close(),
            _ => {}
        }
    });
    window.set_icon_name(Some("muzyka-bps"));
    window.set_default_size(1280, 820);
    window.add(&webview);
    window
}

fn main() {
    glib::set_prgname(Some(APP_ID));
    let app = gtk::Application::new(Some(APP_ID), gtk::gio::ApplicationFlags::empty());
    let window: Rc<RefCell<Option<gtk::ApplicationWindow>>> = Rc::new(RefCell::new(None));

    app.connect_activate(move |app| {
        if let Some(win) = window.borrow().as_ref() {
            win.present();
            return;
        }
        let win = build_window(app);
        win.show_all();
        win.present();
        *window.borrow_mut() = Some(win);
    });

    app.run();
}
