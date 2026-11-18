"""YT-Arsiv GUI - Tkinter arayuzu.

Cift tiklayarak ac (veya Baslat.bat ile).
.pyw uzantisi sayesinde konsol penceresi acilmaz.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import time
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

if os.name == "nt":                          # yuksek DPI ekranda bulanik/tasmis gorunmesin (sistem DPI farkindaligi)
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# Calisma dizini guvence altina al (cift tiklamada bos olabiliyor)
FROZEN = getattr(sys, "frozen", False)      # PyInstaller exe: dosyalar exe'nin yaninda
SCRIPT_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
if sys.stdout is None:                       # --noconsole exe: yt-dlp/print yazacak yer bulamasin
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
os.chdir(SCRIPT_DIR)
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import ytarsiv_core as core
except ImportError as e:
    tk.Tk().withdraw()
    messagebox.showerror(
        "YT-Arsiv",
        f"ytarsiv_core.py bulunamadı.\n\nHata: {e}\n\n"
        f"Aranan klasör: {SCRIPT_DIR}",
    )
    sys.exit(1)

# yt-dlp kurulu mu?
if core.YoutubeDL is None:
    tk.Tk().withdraw()
    messagebox.showerror(
        "YT-Arsiv",
        "yt-dlp kurulu değil.\n\nKomut satırında çalıştır:\n  pip install yt-dlp",
    )
    sys.exit(1)


STATUS_TR = {
    "active":                 "Aktif",
    "unavailable":            "YouTube'dan kalktı",
    "removed_from_playlist":  "Listeden çıkarıldı",
    "user_deleted":           "Kullanıcı sildi",
}


PROJE_SAYFASI = "https://github.com/ioz-hub/yt-arsiv"
GUNLUK_KLASORU = SCRIPT_DIR / "logs"            # gunluk dosyalari: logs\gui-YYYYAAGG.log (bkz. App._gunluge_yaz - TEK yazma kapisi)
GUNLUK_SATIR_SINIRI = 5000                     # ekrandaki gunluk kutusunda tutulan en fazla satir

# Renkler (tek yerde): tablo satir etiketleri + gunluk satirlari
RENK = {
    "var": "#0a7d18", "yok": "#b00020", "kayip": "#aa5500", "soluk": "#888888",
    "zebra": "#f5f7fa", "kirmizi_zemin": "#fde8e8", "sari_zemin": "#fff8d6", "yesil_zemin": "#e3f4e3", "mavi_zemin": "#cfe6ff",
    "grup_kesin": "#ffe6e6", "hata": "#b00020", "uyari": "#9a5b00", "iyi": "#0a7d18", "baslik": "#1f4e9a",
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("YT-Arsiv")
        # Olcek: 96 DPI = 1.0.  Piksel verilen her olcu (pencere, sutun genisligi, satir yuksekligi) bununla carpilir.
        self._olcek = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.root.geometry(f"{self._px(1180)}x{self._px(740)}")
        self.root.minsize(self._px(980), self._px(540))

        self.conn = core.init_db()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()
        self._gunluk_dosyasi = None
        self._zebra_bekleyen: set = set()
        self._siralama: dict = {}

        # Manuel koleksiyon sekmeleri (dinamik): playlist_id -> (frame, treeview, index_dict)
        self._manual_tabs: dict[int, tuple] = {}

        self._build_ui()
        # Acilirken: manuel klasorleri tara + dosyalari yeniden bagla
        try:
            core.sync_manual_collections(self.conn, log=lambda m: None)
        except Exception as e:
            self.log_queue.put(f"[!] Manuel klasör taraması başarısız: {e}")
        try:
            s = core.scan_and_relink_files(self.conn, log=lambda m: None)
            if s["relinked"]:
                self.log_queue.put(f"[+] Açılışta {s['relinked']} dosya otomatik bağlandı")
        except Exception as e:
            self.log_queue.put(f"[!] Dosya tarama başarısız: {e}")
        self._refresh_all()
        self._poll_log_queue()

    # ---------------- UI ----------------

    def _px(self, n) -> int:
        """96 DPI'a gore verilen piksel olcusunu ekran olcegine cevirir."""
        return int(round(n * self._olcek))

    def _stil_kur(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        for ad in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(ad).configure(family="Segoe UI", size=10)
            except tk.TclError:
                pass
        satir = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        style.configure("Treeview", rowheight=satir + self._px(8))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), padding=(self._px(4), self._px(3)))
        style.configure("TNotebook.Tab", padding=(self._px(12), self._px(5)))
        style.configure("TButton", padding=(self._px(8), self._px(3)))
        style.configure("Ana.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Uyari.TButton", font=("Segoe UI", 10, "bold"), foreground=RENK["uyari"])
        style.configure("TMenubutton", padding=(self._px(8), self._px(3)))
        style.configure("Durum.TLabel", padding=(self._px(8), self._px(3)))
        style.configure("Soluk.TLabel", foreground=RENK["soluk"])
        style.configure("BosDurum.TLabel", foreground=RENK["soluk"], font=("Segoe UI", 12), justify="center")

    def _menu_kur(self):
        m = tk.Menu(self.root)
        dosya = tk.Menu(m, tearoff=0)
        dosya.add_command(label="Playlist / Kanal Ekle...", accelerator="Ctrl+N", command=self.on_add)
        dosya.add_command(label="Ayarlar...", accelerator="Ctrl+,", command=self.on_ayarlar)
        dosya.add_separator()
        dosya.add_command(label="Arşiv klasörünü aç", command=self.on_open_root)
        dosya.add_command(label="Günlük klasörünü aç", command=self.on_open_log_folder)
        dosya.add_separator()
        dosya.add_command(label="Çıkış", command=self.on_close)
        m.add_cascade(label="Dosya", menu=dosya)

        tara = tk.Menu(m, tearoff=0)
        tara.add_command(label="Tümünü tara ve indir", accelerator="Ctrl+T", command=self.on_sync_all)
        tara.add_command(label="Seçili playlisti tara", command=self.on_sync_selected)
        tara.add_separator()
        tara.add_command(label="Çalışan işi iptal et", accelerator="Esc", command=self.on_cancel)
        m.add_cascade(label="Tara", menu=tara)

        m.add_cascade(label="Bakım ve Temizlik", menu=self._bakim_menusu(m))

        gorunum = tk.Menu(m, tearoff=0)
        gorunum.add_command(label="Yenile", accelerator="F5", command=self._refresh_all)
        gorunum.add_command(label="Aramaya git", accelerator="Ctrl+F", command=self._aramaya_odaklan)
        gorunum.add_separator()
        self._gunluk_gorunur = tk.BooleanVar(value=True)
        gorunum.add_checkbutton(label="Günlük panelini göster", accelerator="Ctrl+L", variable=self._gunluk_gorunur,
                                command=self._gunluk_panelini_ayarla)
        gorunum.add_command(label="Günlüğü temizle", command=self._gunlugu_temizle)
        m.add_cascade(label="Görünüm", menu=gorunum)

        yardim = tk.Menu(m, tearoff=0)
        yardim.add_command(label="Proje sayfası (GitHub)", command=lambda: webbrowser.open(PROJE_SAYFASI))
        yardim.add_command(label="Hakkında", command=self.on_hakkinda)
        m.add_cascade(label="Yardım", menu=yardim)
        self.root.config(menu=m)

        self.root.bind("<Control-n>", lambda e: self.on_add())
        self.root.bind("<Control-comma>", lambda e: self.on_ayarlar())
        self.root.bind("<Control-t>", lambda e: self.on_sync_all())
        self.root.bind("<Control-f>", lambda e: self._aramaya_odaklan())
        self.root.bind("<Control-l>", lambda e: (self._gunluk_gorunur.set(not self._gunluk_gorunur.get()),
                                                 self._gunluk_panelini_ayarla()))
        self.root.bind("<F5>", lambda e: self._refresh_all())
        self.root.bind("<Escape>", lambda e: self.on_cancel() if self._is_calisiyor() else None)

    def _bakim_menusu(self, ust):
        """BAKIM = arsivi duzenli tutan isler (tarama/indirme DEGIL): onay bekleyenler, klasor onarimi, kopya temizligi, kontrol.
        Ayni menu hem menu cubugunda hem arac cubugundaki 'Bakım ve Temizlik' dugmesinde kullanilir (iki ayri Menu nesnesi, tek tanim)."""
        b = tk.Menu(ust, tearoff=0)
        b.add_command(label="Silinme onayları...", command=self.on_silinme_onaylari)
        b.add_separator()
        b.add_command(label="Klasörleri onar: kayıp dosyaları yeniden bağla  (tüm klasörler)", command=self.on_scan_local_files)
        b.add_command(label="Elle eklenen klasörleri tara  ('_' ile başlayan klasörler)", command=self.on_scan_manual)
        b.add_separator()
        b.add_command(label="Aynı videoları göster  (kopya temizliği)", command=lambda: self._sekmeye_git("dup"))
        b.add_command(label="Ses analizi: aynı video gruplarını karşılaştır", command=self.on_dup_ses_analizi)
        b.add_command(label="Ses analizi: tüm arşiv", command=self.on_dup_ses_kutuphane)
        b.add_separator()
        b.add_command(label="Yayından kalkanları YouTube'da yeniden kontrol et", command=self.on_una_youtube_kontrol)
        return b

    def _sekmeye_git(self, anahtar):
        self.nb.select(self._sekme[anahtar])

    def _sekme_sayisi(self, anahtar, ad, n):
        """Sekme basliginda kayit sayisi: 'Liste Dışı (5)' - temizlik gereken yer bir bakista gorunsun."""
        try:
            self.nb.tab(self._sekme[anahtar], text=f"{ad} ({n})" if n else ad)
        except (KeyError, tk.TclError):
            pass

    def _build_ui(self):
        self._stil_kur()
        self._menu_kur()

        # --- Arac cubugu: en sik kullanilan isler.  Arama kutusu ONCE yerlestirilir ki dar pencerede kirpilmasin. ---
        toolbar = ttk.Frame(self.root, padding=(self._px(8), self._px(6)))
        toolbar.pack(side="top", fill="x")

        self.toolbar_search_var = tk.StringVar()
        ttk.Button(toolbar, text="Ara", width=5, command=self._toolbar_search_go).pack(side="right", padx=(2, 0))
        self.toolbar_entry = ttk.Entry(toolbar, textvariable=self.toolbar_search_var, width=22)
        self.toolbar_entry.pack(side="right", padx=4)
        self.toolbar_entry.bind("<Return>", lambda e: self._toolbar_search_go())
        ttk.Label(toolbar, text="Video ara:").pack(side="right")

        ttk.Button(toolbar, text="Ayarlar", command=self.on_ayarlar).pack(side="right", padx=(self._px(10), 0))
        # 1) ARSIVE EKLE   2) TARA / INDIR   3) BAKIM (temizlik + onarim)   4) dikkat isteyen: onay bekleyen silinmeler
        ttk.Button(toolbar, text="+ Playlist / Kanal Ekle", command=self.on_add).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=self._px(8))
        ttk.Button(toolbar, text="Tümünü Tara", style="Ana.TButton", command=self.on_sync_all).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Seçiliyi Tara", command=self.on_sync_selected).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=self._px(8))
        bakim = ttk.Menubutton(toolbar, text="Bakım ve Temizlik", direction="below")
        bakim["menu"] = self._bakim_menusu(bakim)
        bakim.pack(side="left", padx=2)
        # Yalniz onay bekleyen silinme VARSA gorunur (bkz. _onay_dugmesini_guncelle)
        self.onay_btn = ttk.Button(toolbar, text="", style="Uyari.TButton", command=self.on_silinme_onaylari)

        # --- Durum cubugu (en altta; paned'den ONCE yerlestirilir).  Iptal + ilerleme burada: her pencere genisliginde gorunur. ---
        durum = ttk.Frame(self.root)
        durum.pack(side="bottom", fill="x")
        ttk.Separator(durum, orient="horizontal").pack(side="top", fill="x")
        self.cancel_btn = ttk.Button(durum, text="İptal", command=self.on_cancel, state="disabled")
        self.cancel_btn.pack(side="right", padx=(2, self._px(8)), pady=self._px(3))
        self.progress = ttk.Progressbar(durum, mode="determinate", length=self._px(160))
        self.progress.pack(side="right", padx=self._px(6))
        self.ozet_var = tk.StringVar(value="")
        ttk.Label(durum, textvariable=self.ozet_var, style="Soluk.TLabel", anchor="e").pack(side="right", padx=self._px(10))
        self.status_var = tk.StringVar(value="Hazır.")
        ttk.Label(durum, textvariable=self.status_var, anchor="w", style="Durum.TLabel").pack(side="left", fill="x", expand=True)

        # --- Sekmeler + Gunluk: dikey PanedWindow (ayirici suruklenebilir) ---
        self.paned = ttk.PanedWindow(self.root, orient="vertical")
        self.paned.pack(side="top", fill="both", expand=True, padx=self._px(8), pady=(0, self._px(4)))

        self.nb = ttk.Notebook(self.paned)
        self.paned.add(self.nb, weight=3)

        self._sekme = {}                 # anahtar -> sekme cercevesi (sayac / 'sekmeye git' icin)
        self._build_tab_playlists()      # ARSIV
        self._build_tab_search()
        self._build_tab_lost()           # TEMIZLIK (gozden gecirilecekler)
        self._build_tab_unavailable()
        self._build_tab_duplicates()
        self._build_tab_history()        # KAYIT
        self._build_log_panel()  # gunluk paned'in ALT bolumune eklenir

        self._tablolari_hazirla(self.nb)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Acilista ~%68 sekmeler, ~%32 gunluk.  Pencere boyutu hazirlanir hazirlanmaz uygula.
        self.root.after(50, self._apply_initial_sash)

    # ---------------- Tablo yardimcilari: olcek, zebra, basliga tiklayarak siralama ----------------

    def _tablolari_hazirla(self, kap):
        """kap altindaki TUM Treeview'lar: sutun genisliklerini ekran olcegine uydurur, basliklara siralama baglar,
        duz (agac olmayan) tablolarda zebra satirlari acar.  Her tablo yalniz BIR kez hazirlanir."""
        for w in kap.winfo_children():
            if isinstance(w, ttk.Treeview) and not getattr(w, "_hazir", False):
                w._hazir = True
                duz = "tree" not in str(w.cget("show"))
                for c in w["columns"]:
                    w.column(c, width=self._px(int(w.column(c, "width"))), minwidth=self._px(30))
                    if duz:
                        w.heading(c, command=lambda t=w, s=c: self._sirala(t, s))
                if not duz:
                    w.column("#0", width=self._px(int(w.column("#0", "width"))))
                else:
                    self._zebra_kur(w)
            self._tablolari_hazirla(w)

    def _zebra_kur(self, tv):
        tv.tag_configure("zebra", background=RENK["zebra"])
        ham_insert = tv.insert

        def insert(*a, **k):
            iid = ham_insert(*a, **k)
            self._zebra_planla(tv)
            return iid
        tv.insert = insert

    def _zebra_planla(self, tv):
        if tv in self._zebra_bekleyen:
            return
        self._zebra_bekleyen.add(tv)
        self.root.after_idle(lambda: self._zebra_uygula(tv))

    def _zebra_uygula(self, tv):
        self._zebra_bekleyen.discard(tv)
        try:
            for i, iid in enumerate(tv.get_children("")):
                etiketler = [t for t in tv.item(iid, "tags") if t != "zebra"]
                # 'zebra' BASA eklenir: satirin kendi (renkli zeminli) etiketi oncelikli kalir
                tv.item(iid, tags=(["zebra"] if i % 2 else []) + etiketler)
        except tk.TclError:
            pass                                   # tablo bu arada kapanmis olabilir (pencere kapandi)

    def _sirala(self, tv, sutun):
        """Sutun basligina tiklaninca satirlari o sutuna gore siralar (tekrar tiklama: tersine).  Sayilar sayi olarak."""
        ters = self._siralama.get((tv, sutun), False)

        def anahtar(iid):
            v = str(tv.set(iid, sutun)).strip()
            try:
                return (0, float(v.replace(",", ".").split()[0]), "")
            except (ValueError, IndexError):
                return (1, 0.0, v.casefold())
        satirlar = sorted(tv.get_children(""), key=anahtar, reverse=ters)
        for i, iid in enumerate(satirlar):
            tv.move(iid, "", i)
        self._siralama[(tv, sutun)] = not ters
        self._zebra_planla(tv)

    def _is_calisiyor(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def _mesgul(self) -> bool:
        """KAPI: arka planda is (tarama/indirme/analiz) surerken veritabanini ya da dosyalari DEGISTIREN bir eylem baslatilmaz
        (iki yazar cakismasin, taranan kayit ortadan kalkmasin).  Mesgulse kullaniciya soyler ve True doner."""
        if self._is_calisiyor():
            messagebox.showinfo("YT-Arsiv", "Arka planda bir iş sürüyor (tarama / indirme / analiz).\n\n"
                                            "Bu işlem için önce onun bitmesini bekleyin ya da İptal'e basın.")
            return True
        return False

    def _ilerleme(self, calisiyor: bool):
        """Ilerleme cubugunun TEK kapisi: is surerken hareketli, bostayken bos."""
        if calisiyor:
            self.progress.configure(mode="indeterminate")
            self.progress.start(80)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)

    def _is_basladi(self, durum: str, iptal_edilebilir: bool = True):
        self.cancel_flag.clear()
        self.cancel_btn.configure(state="normal" if iptal_edilebilir else "disabled")
        self._ilerleme(True)
        self.status_var.set(durum)

    def _is_bitti(self, durum: str):
        self._ilerleme(False)
        self.cancel_btn.configure(state="disabled")
        self.status_var.set(durum)

    def _aramaya_odaklan(self):
        self.toolbar_entry.focus_set()
        self.toolbar_entry.selection_range(0, "end")

    def on_hakkinda(self):
        messagebox.showinfo(
            "YT-Arsiv hakkında",
            "YT-Arsiv - YouTube oynatma listesi ve kanal arşivleyici\n\n"
            "Listelerinizi ve kanallarınızı diske indirir; listeden çıkan ve YouTube'dan kalkan videoları "
            "ayrı klasörlerde saklar.\n\n"
            f"Veritabanı: {core.DB_PATH}\nArşiv klasörü: {core.DOWNLOADS}\n\n{PROJE_SAYFASI}")

    def on_open_log_folder(self):
        GUNLUK_KLASORU.mkdir(exist_ok=True)
        self._open_in_explorer(GUNLUK_KLASORU)

    def on_close(self):
        """Pencere kapatma: is surerken sormadan kapanmaz (yarim indirme / yarim tasima birakmamak icin)."""
        if self._is_calisiyor():
            if not messagebox.askyesno(
                    "YT-Arsiv", "Arka planda bir iş sürüyor (tarama / indirme).\n\n"
                                "Şimdi kapatırsanız iş yarıda kesilir; yarım kalan indirme sonraki taramada baştan denenir.\n"
                                "Önce İptal'e basıp işin durmasını beklemek daha güvenlidir.\n\nYine de kapatılsın mı?",
                    icon="warning", default="no"):
                return
            self.cancel_flag.set()
        try:
            if self._gunluk_dosyasi:
                self._gunluk_dosyasi.close()
        except OSError:
            pass
        self.root.destroy()

    def _apply_initial_sash(self):
        try:
            total = self.paned.winfo_height()
        except tk.TclError:
            return
        if total < 100:
            # Pencere henuz cizilmemis; tekrar dene
            self.root.after(50, self._apply_initial_sash)
            return
        try:
            self.paned.sashpos(0, int(total * 0.68))
        except tk.TclError:
            pass

    def _gunluk_panelini_ayarla(self):
        """Gorunum > 'Gunluk panelini goster': paneli gizler / geri getirir."""
        gorunur = str(self.log_pane) in self.paned.panes()
        if self._gunluk_gorunur.get() and not gorunur:
            self.paned.add(self.log_pane, weight=1)
            self.root.after(50, self._apply_initial_sash)
        elif not self._gunluk_gorunur.get() and gorunur:
            self.paned.forget(self.log_pane)

    def _gunlugu_temizle(self):
        self.log_txt.configure(state="normal")
        self.log_txt.delete("1.0", "end")
        self.log_txt.configure(state="disabled")

    def _build_tab_playlists(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Playlistler")

        cols = ("id", "alias", "title", "active", "downloaded", "unavailable", "removed", "last_checked")
        headers = ("ID", "Takma Ad", "Başlık", "Aktif", "İndirilen", "Yayından Kalkan", "Liste Dışı", "Son Tarama")
        widths = (45, 160, 330, 70, 90, 135, 95, 140)

        self.tv_pl = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, headers, widths):
            self.tv_pl.heading(c, text=h)
            self.tv_pl.column(c, width=w, anchor="w" if c in ("alias", "title") else "center")

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_pl.yview)
        self.tv_pl.configure(yscrollcommand=vsb.set)
        self.tv_pl.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Bos durum: hic playlist yokken ne yapilacagini soyler (bkz. _refresh_playlists)
        self.bos_durum = ttk.Label(
            self.tv_pl, style="BosDurum.TLabel", background="white", anchor="center", justify="center",
            text="Henüz playlist ya da kanal eklenmedi.\n\n"
                 "Başlamak için  « + Playlist / Kanal Ekle »  düğmesine basın\n"
                 "ve bir YouTube oynatma listesi ya da kanal adresi yapıştırın.")

        # Sag tik menusu
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Bu playlisti tara", command=self.on_sync_selected)
        menu.add_command(label="YouTube'da aç",     command=self.on_open_playlist_url)
        menu.add_command(label="İndirme klasörünü aç", command=self.on_open_playlist_folder)
        menu.add_command(label="_ListeDisi/ klasörünü aç", command=self.on_open_playlist_removed_folder)
        menu.add_separator()
        menu.add_command(label="SİL",                command=self.on_delete_playlist)
        self._menu_pl = menu

        def show_menu(ev):
            row = self.tv_pl.identify_row(ev.y)
            if row:
                self.tv_pl.selection_set(row)
                menu.tk_popup(ev.x_root, ev.y_root)
        self.tv_pl.bind("<Button-3>", show_menu)
        self.tv_pl.bind("<Double-1>", lambda e: self.on_open_playlist_folder())

    def _build_tab_lost(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Liste Dışı")
        self._sekme["lost"] = f

        top = ttk.Frame(f)
        top.pack(side="top", fill="x", padx=4, pady=4)
        self.lost_count_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.lost_count_var, font=("Segoe UI", 10, "bold")).pack(side="left")

        self.lost_only_missing = tk.BooleanVar(value=False)  # varsayilan: listeden cikardigin TUM videolar
        ttk.Checkbutton(top, text="Sadece dosyası olmayanları göster",
                        variable=self.lost_only_missing,
                        command=self._refresh_lost).pack(side="left", padx=(12, 4))

        row2 = ttk.Frame(f)
        row2.pack(side="top", fill="x", padx=4, pady=(0, 4))
        ttk.Button(row2, text="Dosyayı aç", command=self.on_open_local_file).pack(side="left", padx=2)
        ttk.Button(row2, text="Klasörünü aç", command=self.on_open_local_folder).pack(side="left", padx=2)
        ttk.Button(row2, text="YouTube'da aç", command=self.on_open_video).pack(side="left", padx=2)
        ttk.Button(row2, text="_ListeDisi klasörü", command=self.on_open_removed_folder).pack(side="left", padx=2)
        ttk.Button(row2, text="Kaydı ve dosyayı sil...", command=self.on_delete_lost_permanent).pack(side="right", padx=2)
        ttk.Button(row2, text="Yalnızca kaydı sil...", command=self.on_delete_lost_record_only).pack(side="right", padx=2)

        cols = ("status", "title", "uploader", "playlist", "file", "yt", "last_seen", "video_id")
        headers = ("Durum", "Başlık", "Yükleyen", "Playlist", "Dosya", "YouTube'da", "Son Görüldü", "Video ID")
        widths = (140, 330, 140, 130, 70, 150, 135, 115)

        self.tv_lost = ttk.Treeview(f, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, headers, widths):
            self.tv_lost.heading(c, text=h)
            self.tv_lost.column(c, width=w, anchor="w" if c in ("title", "uploader", "playlist") else "center")

        self.tv_lost.tag_configure("has_file",   foreground=RENK["var"])
        self.tv_lost.tag_configure("no_file",    foreground=RENK["yok"])
        self.tv_lost.tag_configure("file_gone",  foreground=RENK["kayip"])
        self.tv_lost.tag_configure("yt_gone", foreground=RENK["yok"], background=RENK["kirmizi_zemin"])

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_lost.yview)
        self.tv_lost.configure(yscrollcommand=vsb.set)
        self.tv_lost.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tv_lost.bind("<Double-1>", lambda e: self.on_open_local_file())
        self.tv_lost.bind("<Control-a>", lambda e: (self.tv_lost.selection_set(self.tv_lost.get_children()), "break"))
        self.tv_lost.bind("<Control-A>", lambda e: (self.tv_lost.selection_set(self.tv_lost.get_children()), "break"))

        # Side-index: ayni video_id farkli playlistlerde olabilir, dogru kayda ulasmak icin
        self._lost_index = {}  # tree iid -> (video_id, playlist_id)

    # ---------------- Yayindan Kalkan sekmesi ----------------
    # Yayindan kalkan = YouTube'da artik erisilemeyen (ozel/silinmis) video.  Yerel dosya cogu zaman TEK kopyadir;
    # ayni videonun canli baska bir kopyasi varsa "Ayni Video" sutununda gosterilir (guclu/olasi).

    ES_ETIKET = {"ayni": "AYNI VİDEO", "guclu": "GÜÇLÜ", "olasi": "olası - kontrol et"}

    def _build_tab_unavailable(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Yayından Kalkan")
        self._sekme["una"] = f

        top = ttk.Frame(f)
        top.pack(side="top", fill="x", padx=4, pady=(4, 0))
        self.una_count_var = tk.StringVar(value="")
        una_ozet = ttk.Label(top, textvariable=self.una_count_var, font=("Segoe UI", 10, "bold"), justify="left")
        una_ozet.pack(side="left")
        top.bind("<Configure>", lambda e: una_ozet.configure(wraplength=max(self._px(300), e.width - self._px(330))))
        self.una_only_twin = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Sadece aynı videosu başka yerde olanlar", variable=self.una_only_twin,
                        command=self._refresh_unavailable).pack(side="left", padx=(12, 4))

        row2 = ttk.Frame(f)
        row2.pack(side="top", fill="x", padx=4, pady=(2, 4))
        ttk.Button(row2, text="Seçilenleri sil...", command=self.on_una_delete).pack(side="right", padx=2)
        ttk.Button(row2, text="Dosyayı aç", command=self.on_una_open_file).pack(side="left", padx=2)
        ttk.Button(row2, text="Aynı videoyu aç", command=self.on_una_open_twin).pack(side="left", padx=2)
        ttk.Button(row2, text="Klasörünü aç", command=self.on_una_open_folder).pack(side="left", padx=2)
        ttk.Button(row2, text="YouTube'da aç", command=self.on_una_open_youtube).pack(side="left", padx=2)
        ttk.Button(row2, text="Wayback'te ara", command=self.on_una_open_wayback).pack(side="left", padx=2)
        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(row2, text="Yayında mı? (kontrol et)",
                   command=self.on_una_youtube_kontrol).pack(side="left", padx=2)

        cols = ("title", "uploader", "playlist", "mb", "file", "twin", "yt", "where", "since", "video_id")
        headers = ("Başlık", "Yükleyen", "Playlist", "MB", "Dosya", "Aynı Video (başka yerde)",
                   "YouTube'da", "Konum", "Kalkış Tespiti", "Video ID")
        widths = (300, 120, 90, 60, 70, 320, 130, 130, 125, 115)

        self.tv_una = ttk.Treeview(f, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, headers, widths):
            self.tv_una.heading(c, text=h)
            self.tv_una.column(c, width=w,
                               anchor="w" if c in ("title", "uploader", "playlist", "twin", "where") else "center")
        self.tv_una.tag_configure("twin_strong", background=RENK["yesil_zemin"])
        self.tv_una.tag_configure("twin_maybe", background=RENK["sari_zemin"])
        self.tv_una.tag_configure("has_file", foreground=RENK["var"])
        self.tv_una.tag_configure("no_file", foreground=RENK["yok"])
        self.tv_una.tag_configure("file_gone", foreground=RENK["kayip"])
        self.tv_una.tag_configure("yt_live", background=RENK["mavi_zemin"])      # en son tanimlanan = en yuksek oncelik

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_una.yview)
        self.tv_una.configure(yscrollcommand=vsb.set)
        self.tv_una.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tv_una.bind("<Double-1>", lambda e: self.on_una_open_file())
        self.tv_una.bind("<Control-a>", lambda e: (self.tv_una.selection_set(self.tv_una.get_children()), "break"))

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Başlığı kopyala", command=lambda: self._una_copy("title"))
        menu.add_command(label="Video ID'sini kopyala", command=lambda: self._una_copy("video_id"))
        menu.add_separator()
        menu.add_command(label="Yerel dosyayı aç", command=self.on_una_open_file)
        menu.add_command(label="Aynı videonun dosyasını aç", command=self.on_una_open_twin)
        menu.add_command(label="Klasörünü aç", command=self.on_una_open_folder)

        def show_menu(ev):
            row = self.tv_una.identify_row(ev.y)
            if row:
                if row not in self.tv_una.selection():
                    self.tv_una.selection_set(row)
                menu.tk_popup(ev.x_root, ev.y_root)
        self.tv_una.bind("<Button-3>", show_menu)
        self._una_index = {}  # tree iid -> kayit sozlugu

    def _refresh_unavailable(self):
        self.tv_una.delete(*self.tv_una.get_children())
        self._una_index = {}
        tum = core.yayindan_kalkanlar(self.conn)
        only_twin = self.una_only_twin.get()
        rows = [r for r in tum if r["es"]] if only_twin else tum
        n_var = sum(1 for r in tum if r["boyut"])
        guclu = [r for r in tum if r["es"] and r["es"]["guc"] in ("ayni", "guclu")]
        mb_guclu = sum(r["boyut"] for r in guclu) / 1048576
        for r in rows:
            fp = r["file_path"]
            p = core.resolve_video_path(fp) if fp else None
            if r["boyut"]:
                durum, tag = "VAR", "has_file"
                konum = core.YAYINDAN_KALKAN_DIR if core.YAYINDAN_KALKAN_DIR in p.parts else "playlist klasöründe"
            elif fp:
                durum, tag, konum = "KAYIP", "file_gone", "-"
            else:
                durum, tag, konum = "YOK", "no_file", "-"
            es = r["es"]
            if es:
                es_txt = f"{self.ES_ETIKET[es['guc']]}: {es['alias']} [{es['klasor']}] - {es['title'] or '?'}"
                tag = "twin_strong" if es["guc"] in ("ayni", "guclu") else "twin_maybe"
            else:
                es_txt = "-  (TEK KOPYA)" if r["boyut"] else "-"
            tags = (tag,)
            tarih = (r.get("yt_kontrol") or "")[:10]
            if r.get("yt_durum") == "yayinda":
                yt_txt, tags = f"YAYINDA ({tarih})", (tag, "yt_live")
            elif r.get("yt_durum") == "kalkmis":
                yt_txt = f"kalkmış ({tarih})"
            else:
                yt_txt = "? (yoklanmadı)"
            iid = self.tv_una.insert("", "end", values=(
                r["title"] or "(başlık yok)",
                r["uploader"] or "?",
                r["playlist_alias"] or r["playlist_title"] or f"#{r['playlist_id']}",
                f"{r['boyut'] / 1048576:.0f}" if r["boyut"] else "",
                durum,
                es_txt,
                yt_txt,
                konum,
                (r["kalkis"] or "")[:16].replace("T", " "),
                r["video_id"],
            ), tags=tags)
            self._una_index[iid] = r
        n_canli = sum(1 for r in tum if r.get("yt_durum") == "yayinda")
        n_kalkmis = sum(1 for r in tum if r.get("yt_durum") == "kalkmis")
        n_yok = len(tum) - n_canli - n_kalkmis
        self.una_count_var.set(
            f"{len(tum)} video YouTube'da erişilemiyor görünüyor  -  {n_var} tanesinin yerel kopyası var  -  "
            f"{len(guclu)} tanesinin aynı videosu başka yerde var (~{mb_guclu:.0f} MB boşa yer)  -  "
            f"YouTube kontrolü: {n_canli} yayında, {n_kalkmis} kalkmış, {n_yok} yoklanmadı")
        self._sekme_sayisi("una", "Yayından Kalkan", len(tum))

    def on_una_youtube_kontrol(self):
        """Secili (yoksa tum) 'Yayindan Kalkan' videolarin YouTube'da gercekten yayinda olup olmadigini yoklar.
        Yalniz bilgi: video/dosya/durum degismez, sonuc youtube_durum'a yazilir."""
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("YT-Arsiv", "Başka bir iş devam ediyor.")
            return
        secili = [self._una_index[i] for i in self.tv_una.selection() if i in self._una_index]
        ids = sorted({r["video_id"] for r in secili}) if secili else None
        n = len(ids) if ids is not None else len({r["video_id"] for r in self._una_index.values()})
        if not n:
            messagebox.showinfo("YT-Arsiv", "Listede video yok.")
            return
        kapsam = "seçili" if ids is not None else "TÜM"
        if not messagebox.askyesno(
                "YouTube'da kontrol",
                f"{kapsam} {n} video YouTube'da yoklanacak (video başına ~2-4 sn; yt-dlp, indirme yok).\n"
                "Yalnız bilgi alınır: videolara ve dosyalara dokunulmaz.\n\nBaşlansın mı?"):
            return
        self.cancel_flag.clear()
        self.cancel_btn.configure(state="normal")
        self._ilerleme(True)
        self.status_var.set("YouTube'da yayında mı kontrol ediliyor...")

        def worker():
            conn = core.init_db()
            try:
                self._enqueue_log(f"\n=== Yayından kalkanlar: YouTube kontrolü ({n} video) ===")
                s = core.yayindan_kalkanlari_yokla(conn, video_idleri=ids, log=self._enqueue_log,
                                                   should_cancel=self.cancel_flag.is_set)
                self._enqueue_log(
                    f"\nYayında: {s['yayinda']}   kalkmış: {s['kalkmis']}   doğrulanamadı: {s['belirsiz']}"
                    + ("   [iptal edildi]" if s["iptal"] else "") + "\n")
                if s["geri_gelen"]:
                    self._enqueue_log("YouTube'da tekrar YAYINDA olan videolar:")
                    for vid, baslik in s["geri_gelen"]:
                        self._enqueue_log(f"   - {(baslik or '?')[:70]} [{vid}]")
            except Exception as e:
                self._enqueue_log(f"\n[!] YouTube kontrol hatası: {e}")
            finally:
                conn.close()
                self.root.after(0, self._on_una_kontrol_done)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_una_kontrol_done(self):
        self._ilerleme(False)
        self.cancel_btn.configure(state="disabled")
        self.status_var.set("YouTube kontrolü bitti.")
        self._refresh_unavailable()

    def _selected_una_rows(self) -> list:
        sel = [i for i in self.tv_una.selection() if i in self._una_index]
        if not sel:
            messagebox.showinfo("YT-Arsiv", "Önce bir video seç.")
            return []
        return [self._una_index[i] for i in sel]

    def _selected_una_row(self) -> dict | None:
        rows = self._selected_una_rows()
        return rows[0] if rows else None

    def _una_local_path(self, r):
        p = core.resolve_video_path(r["file_path"]) if r["file_path"] else None
        return p if p and p.exists() else None

    def on_una_open_file(self):
        r = self._selected_una_row()
        if not r:
            return
        p = self._una_local_path(r)
        if not p:
            messagebox.showinfo("YT-Arsiv", "Bu videonun yerel dosyası yok.")
            return
        try:
            os.startfile(str(p))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Açılamadı: {e}")

    def on_una_open_twin(self):
        r = self._selected_una_row()
        if not r:
            return
        if not r["es"]:
            messagebox.showinfo("YT-Arsiv", "Bu video için aynı video bulunamadı (tek kopya).")
            return
        try:
            os.startfile(r["es"]["dosya"])
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Açılamadı: {e}")

    def on_una_open_folder(self):
        r = self._selected_una_row()
        if not r:
            return
        p = self._una_local_path(r)
        if not p:
            messagebox.showinfo("YT-Arsiv", "Bu videonun yerel dosyası yok.")
            return
        subprocess.run(["explorer", "/select,", str(p)])

    def on_una_open_youtube(self):
        r = self._selected_una_row()
        if r:
            webbrowser.open(f"https://youtu.be/{r['video_id']}")

    def on_una_open_wayback(self):
        r = self._selected_una_row()
        if r:
            webbrowser.open(f"https://web.archive.org/web/0/https://www.youtube.com/watch?v={r['video_id']}")

    def _una_copy(self, alan):
        r = self._selected_una_row()
        if r and r.get(alan):
            self._copy_to_clipboard(str(r[alan]), alan)

    def on_una_delete(self):
        """Secilileri diskten ve DB'den kaldirir (core.kopya_kaldir - tek kapi).  Tek kopya olanlar icin
        ayrica uyarir: YouTube'dan da kalktigi icin bir daha indirilemez."""
        rows = self._selected_una_rows()
        if not rows or self._mesgul():
            return
        secili = {(r["video_id"], r["playlist_id"]) for r in rows}
        cift = [r for r in rows if r["es"] and (r["es"]["video_id"], r["es"]["playlist_id"]) in secili]
        if cift:
            messagebox.showwarning(
                "YT-Arsiv",
                f"{len(cift)} videonun aynı videosu da seçili. Aynı videonun iki kopyasını birden silme; "
                f"birini seçimden çıkar.")
            return
        tek = [r for r in rows if r["boyut"] and not r["es"]]
        olasi = [r for r in rows if r["es"] and r["es"]["guc"] == "olasi"]
        mb = sum(r["boyut"] for r in rows) / 1048576

        def satir(r):
            if not r["boyut"]:
                etiket = "dosya yok"
            elif not r["es"]:
                etiket = "TEK KOPYA"
            else:
                etiket = f"aynı video var: {r['es']['alias']} [{r['es']['klasor']}] ({self.ES_ETIKET[r['es']['guc']]})"
            return f"  - {r['title'] or r['video_id']}  [{etiket}]"

        uyarilar = ""
        if tek:
            uyarilar += (f"\nUYARI: {len(tek)} videonun başka kopyası YOK. YouTube'dan da kalktığı için "
                         f"silinirse bir daha indirilemez!\n")
        if olasi:
            uyarilar += (f"\nDİKKAT: {len(olasi)} videonun eşleşmesi yalnızca 'olası'; farklı video olabilir. "
                         f"'Aynı videonun dosyasını aç' ile karşılaştır.\n")
        onizleme = "\n".join(satir(r) for r in rows[:14]) + (f"\n  ... ve {len(rows) - 14} tane daha" if len(rows) > 14 else "")
        if not messagebox.askyesno(
                "Seçilenleri diskten sil + kaydını kaldır",
                f"{len(rows)} video DB'den kaldırılacak, yerel dosyaları (yan dosyalarıyla) diskten silinecek "
                f"(~{mb:.0f} MB).\nBu playlist'ler bu videoları bir daha almayacak.\n{uyarilar}\n{onizleme}\n\nDevam edilsin mi?",
                icon="warning", default="no"):
            return
        n_ok = n_fail = 0
        for r in rows:
            ok, msg = core.kopya_kaldir(self.conn, r["video_id"], r["playlist_id"])
            if ok:
                n_ok += 1
            else:
                n_fail += 1
                self.log(f"  [!] Kaldırılamadı: {r['title'] or r['video_id']} ({msg})")
        self.log(f"[YAYINDAN KALKAN SİL] {n_ok} video kaldırıldı (~{mb:.0f} MB)"
                 + (f", {n_fail} kaldırılamadı" if n_fail else ""))
        self._refresh_all()

    def _build_tab_duplicates(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Aynı Videolar")
        self._sekme["dup"] = f

        # 1. SATIR: sayim + filtreler
        row1 = ttk.Frame(f)
        row1.pack(side="top", fill="x", padx=4, pady=(4, 2))
        self.dup_count_var = tk.StringVar(value="")
        ttk.Label(row1, textvariable=self.dup_count_var,
                  font=("Segoe UI", 10, "bold")).pack(side="left")

        self.dup_show_exact = tk.BooleanVar(value=True)
        self.dup_show_fuzzy = tk.BooleanVar(value=True)
        self.dup_include_inactive = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="Kesin",
                        variable=self.dup_show_exact,
                        command=self._refresh_duplicates).pack(side="right", padx=4)
        ttk.Checkbutton(row1, text="Benzer",
                        variable=self.dup_show_fuzzy,
                        command=self._refresh_duplicates).pack(side="right", padx=4)
        ttk.Checkbutton(row1, text="Liste dışı / yayından kalkanları da göster",
                        variable=self.dup_include_inactive,
                        command=self._refresh_duplicates).pack(side="right", padx=4)

        # 2. SATIR: aksiyon butonlari
        row2 = ttk.Frame(f)
        row2.pack(side="top", fill="x", padx=4, pady=(0, 4))
        ttk.Button(row2, text="Seçilenleri sil...", command=self.on_dup_delete_selected_files).pack(side="right", padx=2)
        ttk.Button(row2, text="Dosyayı aç",  command=self.on_dup_open_file).pack(side="left", padx=2)
        ttk.Button(row2, text="YouTube'da aç",   command=self.on_dup_open_video).pack(side="left", padx=2)
        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(row2, text="Ses analizi yap", command=self.on_dup_ses_analizi).pack(side="left", padx=2)
        ttk.Button(row2, text="Tüm arşivde ses analizi",
                   command=self.on_dup_ses_kutuphane).pack(side="left", padx=2)

        cols = ("title", "playlist", "pos", "file", "video_id", "ses")
        headers = ("Başlık", "Playlist", "Sıra", "Dosya", "Video ID", "Ses")
        widths = (360, 140, 55, 70, 115, 95)

        # Ctrl/Shift ile coklu secim
        self.tv_dup = ttk.Treeview(f, columns=cols, show="tree headings", selectmode="extended")
        self.tv_dup.heading("#0", text="Grup")
        self.tv_dup.column("#0", width=380, anchor="w")
        for c, h, w in zip(cols, headers, widths):
            self.tv_dup.heading(c, text=h)
            self.tv_dup.column(c, width=w, anchor="w" if c in ("title", "playlist") else "center")

        self.tv_dup.tag_configure("group_exact", background=RENK["grup_kesin"])
        self.tv_dup.tag_configure("group_fuzzy", background=RENK["sari_zemin"])
        self.tv_dup.tag_configure("has_file",    foreground=RENK["var"])
        self.tv_dup.tag_configure("no_file",     foreground=RENK["yok"])

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_dup.yview)
        self.tv_dup.configure(yscrollcommand=vsb.set)
        self.tv_dup.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tv_dup.bind("<Double-1>", lambda e: self.on_dup_open_file())
        self.tv_dup.bind("<Control-c>", lambda e: (self.on_dup_copy_title(), "break"))
        self.tv_dup.bind("<Control-C>", lambda e: (self.on_dup_copy_title(), "break"))

        # Sag tik menusu
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Başlığı kopyala (Ctrl+C)",     command=self.on_dup_copy_title)
        menu.add_command(label="Tüm grup başlıklarını kopyala", command=self.on_dup_copy_group_titles)
        menu.add_command(label="Video URL'sini kopyala",        command=self.on_dup_copy_url)
        menu.add_command(label="Video ID'sini kopyala",         command=self.on_dup_copy_videoid)
        menu.add_separator()
        menu.add_command(label="YouTube'da aç",  command=self.on_dup_open_video)
        menu.add_command(label="Dosyayı aç",     command=self.on_dup_open_file)
        self._menu_dup = menu

        def show_menu(ev):
            row = self.tv_dup.identify_row(ev.y)
            if row:
                self.tv_dup.selection_set(row)
                menu.tk_popup(ev.x_root, ev.y_root)
        self.tv_dup.bind("<Button-3>", show_menu)

        self._dup_index = {}  # tree iid -> (video_id, playlist_id)

    def _build_tab_search(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Arama")
        self._sekme["ara"] = f

        top = ttk.Frame(f)
        top.pack(side="top", fill="x", padx=4, pady=4)
        ttk.Label(top, text="Ara (link / video ID / başlık / yükleyen):").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.search_var, width=60)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self._run_search())
        ttk.Button(top, text="Ara", command=self._run_search).pack(side="left", padx=2)
        ttk.Button(top, text="Temizle",
                   command=lambda: (self.search_var.set(""), self._run_search())).pack(side="left", padx=2)
        self.search_count_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.search_count_var,
                  font=("Segoe UI", 9, "bold")).pack(side="left", padx=12)

        cols = ("title", "playlist", "status", "file", "pi", "uploader", "video_id")
        headers = ("Başlık", "Playlist / Koleksiyon", "Durum", "Dosya", "Sıra", "Yükleyen", "Video ID")
        widths = (330, 170, 150, 70, 55, 140, 115)
        self.tv_search = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, headers, widths):
            self.tv_search.heading(c, text=h)
            self.tv_search.column(c, width=w,
                                  anchor="w" if c in ("title", "playlist", "uploader") else "center")
        self.tv_search.tag_configure("has_file", foreground=RENK["var"])
        self.tv_search.tag_configure("no_file",  foreground=RENK["yok"])
        self.tv_search.tag_configure("inactive", foreground=RENK["soluk"])

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_search.yview)
        self.tv_search.configure(yscrollcommand=vsb.set)
        self.tv_search.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tv_search.bind("<Double-1>", lambda e: self.on_search_open_file())

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Başlığı kopyala",      command=self.on_search_copy_title)
        menu.add_command(label="URL'sini kopyala",     command=self.on_search_copy_url)
        menu.add_command(label="Video ID'sini kopyala", command=self.on_search_copy_videoid)
        menu.add_command(label="Dosya yolunu kopyala", command=self.on_search_copy_filepath)
        menu.add_separator()
        menu.add_command(label="YouTube'da aç", command=self.on_search_open_yt)
        menu.add_command(label="Dosyayı aç",    command=self.on_search_open_file)
        menu.add_command(label="Klasörü aç",    command=self.on_search_open_folder)
        self._menu_search = menu

        def show_menu(ev):
            row = self.tv_search.identify_row(ev.y)
            if row:
                self.tv_search.selection_set(row)
                menu.tk_popup(ev.x_root, ev.y_root)
        self.tv_search.bind("<Button-3>", show_menu)

        self._search_index = {}  # iid -> dict

    def _toolbar_search_go(self):
        """Toolbar'daki arama kutusundan tetikle: Arama sekmesine gec ve aramayi yap."""
        q = self.toolbar_search_var.get().strip()
        if not q:
            return
        self.search_var.set(q)
        self._sekmeye_git("ara")
        self._run_search()

    def _run_search(self):
        q = self.search_var.get()
        self.tv_search.delete(*self.tv_search.get_children())
        self._search_index = {}
        if not q.strip():
            self.search_count_var.set("")
            return
        rows = core.search_videos(self.conn, q)
        for r in rows:
            abs_p = core.resolve_video_path(r.get("file_path"))
            has_file = bool(abs_p) and abs_p.exists()
            if r.get("status") == "active":
                tag = "has_file" if has_file else "no_file"
            else:
                tag = "inactive"
            # Playlist gosterimi - manuel mi YouTube mu
            plist = r.get("playlist_alias") or r.get("playlist_title") or "?"
            if r.get("playlist_url", "").startswith(core.MANUAL_URL_PREFIX):
                plist = f"[K] {plist}"
            iid = self.tv_search.insert("", "end", values=(
                r.get("title") or "(başlık yok)",
                plist,
                STATUS_TR.get(r.get("status"), r.get("status") or ""),
                "VAR" if has_file else ("KAYIP" if r.get("file_path") else "YOK"),
                r.get("playlist_index") or "",
                r.get("uploader") or "",
                r.get("video_id"),
            ), tags=(tag,))
            self._search_index[iid] = r

        self.search_count_var.set(f"{len(rows)} sonuç")

    def _selected_search_row(self) -> dict | None:
        sel = self.tv_search.selection()
        if not sel:
            return None
        return self._search_index.get(sel[0])

    def on_search_open_yt(self):
        r = self._selected_search_row()
        if not r:
            return
        webbrowser.open(f"https://youtu.be/{r['video_id']}")

    def on_search_open_file(self):
        r = self._selected_search_row()
        if not r:
            return
        p = core.resolve_video_path(r.get("file_path"))
        if not p or not p.exists():
            messagebox.showinfo("YT-Arsiv", "Yerel dosya yok.")
            return
        try:
            os.startfile(str(p))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Açılamadı: {e}")

    def on_search_open_folder(self):
        r = self._selected_search_row()
        if not r:
            return
        p = core.resolve_video_path(r.get("file_path"))
        if not p:
            messagebox.showinfo("YT-Arsiv", "Dosya yolu yok.")
            return
        if p.exists():
            subprocess.run(["explorer", "/select,", str(p)])
        elif p.parent.exists():
            self._open_in_explorer(p.parent)

    def on_search_copy_title(self):
        r = self._selected_search_row()
        if r and r.get("title"):
            self._copy_to_clipboard(r["title"], "başlık")

    def on_search_copy_url(self):
        r = self._selected_search_row()
        if r:
            self._copy_to_clipboard(f"https://youtu.be/{r['video_id']}", "URL")

    def on_search_copy_videoid(self):
        r = self._selected_search_row()
        if r:
            self._copy_to_clipboard(r["video_id"], "video ID")

    def on_search_copy_filepath(self):
        r = self._selected_search_row()
        if r:
            p = core.resolve_video_path(r.get("file_path"))
            if p:
                self._copy_to_clipboard(str(p), "dosya yolu")

    def _build_tab_history(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Geçmiş")

        top = ttk.Frame(f)
        top.pack(side="top", fill="x", padx=4, pady=4)
        ttk.Label(top, text="Limit:").pack(side="left")
        self.history_limit = tk.IntVar(value=200)
        ttk.Spinbox(top, from_=50, to=5000, increment=50, textvariable=self.history_limit, width=8,
                    command=self._refresh_history).pack(side="left", padx=4)
        ttk.Button(top, text="Yenile", command=self._refresh_history).pack(side="left")

        cols = ("ts", "type", "video_id", "playlist", "details")
        headers = ("Zaman", "Olay", "Video ID", "Playlist", "Detay")
        widths = (150, 190, 115, 140, 500)

        self.tv_hist = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, headers, widths):
            self.tv_hist.heading(c, text=h)
            self.tv_hist.column(c, width=w, anchor="w" if c in ("details", "playlist") else "center")

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_hist.yview)
        self.tv_hist.configure(yscrollcommand=vsb.set)
        self.tv_hist.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def _build_log_panel(self):
        # Paned window'un alt bolumu.  Satirlar turune gore renklenir (bkz. _gunluk_etiketi); ayni satirlar dosyaya da yazilir.
        pane = ttk.LabelFrame(self.paned, text="Günlük", padding=self._px(4))
        self.log_pane = pane
        self.paned.add(pane, weight=1)
        self.log_txt = tk.Text(pane, height=8, wrap="word", font=("Consolas", 9), relief="flat",
                               background="#fbfbfc", padx=self._px(6), pady=self._px(4))
        vsb = ttk.Scrollbar(pane, orient="vertical", command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=vsb.set, state="disabled")
        self.log_txt.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.log_txt.tag_configure("hata", foreground=RENK["hata"])
        self.log_txt.tag_configure("uyari", foreground=RENK["uyari"])
        self.log_txt.tag_configure("iyi", foreground=RENK["iyi"])
        self.log_txt.tag_configure("baslik", foreground=RENK["baslik"], font=("Consolas", 9, "bold"))
        self.log_txt.tag_configure("soluk", foreground=RENK["soluk"])

    # ---------------- Refresh ----------------

    def _onay_dugmesini_guncelle(self):
        try:
            n = len(core.silinme_bekleyenler(self.conn))
        except Exception:
            n = 0
        if n:
            self.onay_btn.configure(text=f"Onay bekleyen: {n}")
            self.onay_btn.pack(side="left", padx=(self._px(10), 2))
        else:
            self.onay_btn.pack_forget()

    def _refresh_all(self):
        self._onay_dugmesini_guncelle()
        self._refresh_playlists()
        self._refresh_lost()
        self._refresh_unavailable()
        self._refresh_duplicates()
        self._refresh_history()
        self._refresh_manual_tabs()

    # ---------------- Manuel koleksiyon sekmeleri (dinamik) ----------------

    def _refresh_manual_tabs(self):
        """DB'deki manuel koleksiyon playlist'leri ile sekmeleri senkronize et:
           yeni gelenler icin sekme ac, kaybolanlar icin sekmeyi kapat,
           varolanlarin icerigini guncelle."""
        collections = core.list_manual_collections(self.conn)
        current_ids = {c["id"] for c in collections}

        # Artik olmayan sekmeleri kaldir
        for pid in list(self._manual_tabs.keys()):
            if pid not in current_ids:
                frame, _, _ = self._manual_tabs.pop(pid)
                try:
                    self.nb.forget(frame)
                except tk.TclError:
                    pass

        # Yeni gelenler icin sekme ac, mevcutlari guncelle
        for c in collections:
            pid = c["id"]
            if pid not in self._manual_tabs:
                self._add_manual_tab(pid, c["alias"] or c["title"] or f"#{pid}")
            self._populate_manual_tab(pid)

    def _add_manual_tab(self, pid: int, name: str):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text=f"[K] {name}")  # [K]oleksiyon prefiks

        top = ttk.Frame(f)
        top.pack(side="top", fill="x", padx=4, pady=4)
        count_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=count_var, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(top, text="Klasörü aç",
                   command=lambda p=pid: self._open_playlist_subdir(p, None)).pack(side="right", padx=2)
        ttk.Button(top, text="Yenile",
                   command=lambda p=pid: self._populate_manual_tab(p)).pack(side="right", padx=2)
        ttk.Button(top, text="Dosyayı aç",
                   command=lambda p=pid: self._open_manual_selected(p)).pack(side="right", padx=2)
        ttk.Button(top, text="YouTube'da aç",
                   command=lambda p=pid: self._open_manual_video_yt(p)).pack(side="right", padx=2)

        cols = ("idx", "title", "uploader", "file", "video_id")
        headers = ("Sıra", "Başlık", "Yükleyen", "Dosya", "Video ID")
        widths = (55, 440, 160, 70, 115)
        tv = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, headers, widths):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="w" if c in ("title", "uploader") else "center")
        tv.tag_configure("has_file", foreground=RENK["var"])
        tv.tag_configure("no_file",  foreground=RENK["yok"])

        vsb = ttk.Scrollbar(f, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        tv.bind("<Double-1>", lambda e, p=pid: self._open_manual_selected(p))

        self._tablolari_hazirla(f)
        idx_map: dict[str, str] = {}  # tree iid -> video_id
        self._manual_tabs[pid] = (f, tv, idx_map, count_var)

    def _populate_manual_tab(self, pid: int):
        entry = self._manual_tabs.get(pid)
        if not entry:
            return
        frame, tv, idx_map, count_var = entry
        tv.delete(*tv.get_children())
        idx_map.clear()
        rows = core.get_collection_videos(self.conn, pid)
        n_with_file = 0
        for r in rows:
            abs_p = core.resolve_video_path(r["file_path"])
            has_file = bool(abs_p) and abs_p.exists()
            if has_file:
                n_with_file += 1
            tag = "has_file" if has_file else "no_file"
            iid = tv.insert("", "end", values=(
                r["playlist_index"] or "",
                r["title"] or "",
                r["uploader"] or "",
                "VAR" if has_file else "YOK",
                r["video_id"],
            ), tags=(tag,))
            idx_map[iid] = r["video_id"]
        count_var.set(f"{len(rows)} video  -  {n_with_file} tanesi yerel kopyaya sahip")

    def _selected_manual_video(self, pid: int) -> dict | None:
        entry = self._manual_tabs.get(pid)
        if not entry:
            return None
        _, tv, idx_map, _ = entry
        sel = tv.selection()
        if not sel:
            return None
        vid = idx_map.get(sel[0])
        if not vid:
            return None
        row = self.conn.execute(
            "SELECT * FROM videos WHERE video_id=? AND playlist_id=?", (vid, pid)
        ).fetchone()
        return dict(row) if row else None

    def _open_manual_selected(self, pid: int):
        r = self._selected_manual_video(pid)
        if not r:
            messagebox.showinfo("YT-Arsiv", "Bir video seç.")
            return
        p = core.resolve_video_path(r.get("file_path"))
        if not p or not p.exists():
            messagebox.showinfo("YT-Arsiv", "Dosya bulunamadı.")
            return
        try:
            os.startfile(str(p))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Açılamadı: {e}")

    def _open_manual_video_yt(self, pid: int):
        r = self._selected_manual_video(pid)
        if not r:
            return
        webbrowser.open(f"https://youtu.be/{r['video_id']}")

    def on_scan_manual(self):
        """Toolbar butonu: downloads/ altinda '_' klasorleri tara, DB'ye yansit."""
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("YT-Arsiv", "Bir tarama zaten devam ediyor.")
            return

        def worker():
            conn = core.init_db()
            try:
                self._enqueue_log("\n=== Manuel klasör taraması ===")
                summary = core.manuel_tarama(conn, log=self._enqueue_log)
                bil = summary.get("bilinmeyen") or {}
                self._enqueue_log(
                    f"\nKoleksiyonlar: {summary['collections']}   "
                    f"Yeni dosya: {summary['new_files']}   "
                    f"Metadata eşleşti: {summary['matched_from_db']}   "
                    f"Silinenden aktarılan: {summary['from_removed']}\n"
                    f"Silinen koleksiyon: {summary.get('deleted_collections', 0)}   "
                    f"Silinen video: {summary.get('deleted_videos', 0)}\n"
                    f"_Bilinmeyen'e taşınan: {bil.get('tasinan_video', 0)} video + {bil.get('tasinan_yan', 0)} yan dosya\n"
                )
                if bil.get("durdu"):
                    self._enqueue_log(f"[!] _Bilinmeyen taşıması DURDU: {bil['durdu']}")
            except Exception as e:
                self._enqueue_log(f"\n[!] Manuel tarama hatası: {e}")
            finally:
                conn.close()
                self.root.after(0, lambda: self._tarama_bitti("Manuel klasör taraması bitti."))

        self._is_basladi("Manuel klasörler taranıyor...", iptal_edilebilir=False)
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _tarama_bitti(self, durum: str):
        self._is_bitti(durum)
        self._refresh_all()

    def _refresh_playlists(self):
        sel = self.tv_pl.selection()
        sel_id = self.tv_pl.item(sel[0], "values")[0] if sel else None
        self.tv_pl.delete(*self.tv_pl.get_children())
        listeler = core.list_playlists(self.conn)
        if listeler:
            self.bos_durum.place_forget()
        else:
            self.bos_durum.place(relx=0.5, rely=0.45, anchor="center")
        self._ozet_guncelle(listeler)
        for r in listeler:
            self.tv_pl.insert("", "end", values=(
                r["id"],
                r["alias"] or "",
                r["title"] or "",
                r["active"],
                r["downloaded"],
                r["unavailable"],
                r["removed"],
                (r["last_checked"] or "")[:16].replace("T", " "),
            ))
        if sel_id:
            for iid in self.tv_pl.get_children():
                if self.tv_pl.item(iid, "values")[0] == sel_id:
                    self.tv_pl.selection_set(iid)
                    break

    def _ozet_guncelle(self, listeler):
        """Durum cubugunun sagindaki ozet: liste / video sayisi + birincil depodaki bos yer (core.depo_durumu kapisindan)."""
        try:
            n_video = sum(int(r["active"] or 0) for r in listeler)
            metin = f"{len(listeler)} liste  ·  {n_video} aktif video"
            ana = next((d for d in core.depo_durumu(self.conn) if d["ana"]), None)
            if ana and ana["var"]:
                metin += f"  ·  {ana['bos'] / 2 ** 30:.1f} GB boş"
            self.ozet_var.set(metin)
        except Exception:
            self.ozet_var.set("")

    def _refresh_lost(self):
        self.tv_lost.delete(*self.tv_lost.get_children())
        self._lost_index = {}
        only_missing = getattr(self, "lost_only_missing", None)
        only_missing = only_missing.get() if only_missing else False
        # Bu sekme YALNIZ senin listeden cikardigin videolari gosterir; YouTube'un kaldirdiklari 'Yayindan Kalkan'da.
        rows = [r for r in core.get_lost_videos(self.conn, only_without_file=only_missing)
                if r["status"] == "removed_from_playlist"]
        yt = core.youtube_durumlari(self.conn)
        for r in rows:
            fpath = r["file_path"]
            abs_p = core.resolve_video_path(fpath)
            if abs_p and abs_p.exists():
                file_state = "VAR"
                tag = "has_file"
            elif fpath:
                file_state = "KAYIP"
                tag = "file_gone"
            else:
                file_state = "YOK"
                tag = "no_file"
            plist = r["playlist_alias"] or r["playlist_title"] or f"#{r['playlist_id']}"
            if r.get("playlist_count", 1) > 1:
                plist = f"{plist} ({r['playlist_count']})"
            yt_durum = yt.get(r["video_id"])
            yt_metin = {"yayinda": "yayında", "kalkmis": "KALKMIŞ (YouTube'da da yok)"}.get(yt_durum, "?")
            if yt_durum == "kalkmis":
                tag = "yt_gone"
            iid = self.tv_lost.insert("", "end", values=(
                STATUS_TR.get(r["status"], r["status"]),
                r["title"] or "(başlık yok)",
                r["uploader"] or "?",
                plist,
                file_state,
                yt_metin,
                (r["last_seen"] or "")[:16].replace("T", " "),
                r["video_id"],
            ), tags=(tag,))
            # Yeni yapida tek video_id birden cok kayda denk gelebilir; sakla
            self._lost_index[iid] = r
        n_recoverable = sum(
            1 for r in rows
            if (lambda p: p and p.exists())(core.resolve_video_path(r["file_path"]))
        )
        filter_note = " (sadece dosyası olmayanlar)" if only_missing else ""
        self.lost_count_var.set(
            f"Listeden çıkardığın {len(rows)} video{filter_note}  -  {n_recoverable} tanesinin yerel kopyası var"
        )
        self._sekme_sayisi("lost", "Liste Dışı", len(rows))

    def _refresh_duplicates(self):
        self.tv_dup.delete(*self.tv_dup.get_children())
        self._dup_index = {}

        dups = core.find_duplicates(self.conn, include_inactive=self.dup_include_inactive.get())
        exact = dups["exact"]
        fuzzy = dups["fuzzy"]
        disk_dupes = dups.get("disk_dupes", {})
        disk_orphans = dups.get("disk_orphans", {})
        ses = core.ses_etiketleri(self.conn, dups)

        def ses_metni(yol):
            return core.ses_metni(ses.get(str(Path(yol)))) if yol else "-"

        n_exact_videos = sum(len(items) for items in exact.values())
        n_fuzzy_videos = sum(len(items) for items in fuzzy.values())
        n_disk_files = sum(len(d["paths"]) for d in disk_dupes.values())
        n_orphan_files = sum(len(d["paths"]) for d in disk_orphans.values())
        self.dup_count_var.set(
            f"{len(exact)} kesin grup ({n_exact_videos} kayıt)   |   "
            f"{len(fuzzy)} benzer başlık grubu ({n_fuzzy_videos} kayıt)   |   "
            f"{len(disk_dupes)} disk tekrarı ({n_disk_files} dosya)   |   "
            f"{len(disk_orphans)} DB dışı grup ({n_orphan_files} dosya)"
        )
        self._sekme_sayisi("dup", "Aynı Videolar", len(exact) + len(fuzzy) + len(disk_dupes) + len(disk_orphans))

        # DISK-LEVEL TEKRARLAR (DB'den bagimsiz, ayni vid'in birden cok dosyasi)
        if disk_dupes:
            self.tv_dup.tag_configure("group_disk", background="#e6f0ff")
            for video_id, info in disk_dupes.items():
                parent = self.tv_dup.insert(
                    "", "end",
                    text=f"[DİSKTE] {info['title'][:70]}  ({len(info['paths'])} dosya)",
                    values=("", "", "", "", video_id),
                    tags=("group_disk",), open=True,
                )
                for path, rel in zip(info["paths"], info["relative_paths"]):
                    p = Path(path)
                    folder_name = p.parent.name
                    iid = self.tv_dup.insert(parent, "end", values=(
                        p.name,
                        folder_name,
                        "",
                        "VAR",
                        video_id,
                        ses_metni(p),
                    ), tags=("has_file",))
                    # Disk-level entry: tree'den dosyayi acabilmek icin mutlak yolu sakla
                    self._dup_index[iid] = (video_id, None, str(path))

        # DB DISI DOSYALAR: diskten bulunur, ayni ekrandan secilip silinebilir.
        if disk_orphans:
            self.tv_dup.tag_configure("group_orphan", background="#fce4d6")
            for video_id, info in disk_orphans.items():
                parent = self.tv_dup.insert(
                    "", "end",
                    text=f"[DB DIŞI] {info['title'][:70]}  ({len(info['paths'])} dosya)",
                    values=("", "", "", "", video_id),
                    tags=("group_orphan",), open=True,
                )
                for path in info["paths"]:
                    p = Path(path)
                    iid = self.tv_dup.insert(parent, "end", values=(
                        p.name, p.parent.name, "", "VAR", video_id, ses_metni(p)
                    ), tags=("has_file",))
                    self._dup_index[iid] = (video_id, None, str(path))
        def _file_state(it):
            abs_p = core.resolve_video_path(it["file_path"])
            return ("VAR", "has_file") if (abs_p and abs_p.exists()) else \
                   (("YOK", "no_file") if not it["file_path"] else ("KAYIP", "no_file"))

        if self.dup_show_exact.get():
            for video_id, items in exact.items():
                base_title = items[0]["title"] or "(başlık yok)"
                parent = self.tv_dup.insert(
                    "", "end",
                    text=f"[KESİN] {base_title[:70]}  ({len(items)} playliste)",
                    values=("", "", "", "", video_id),
                    tags=("group_exact",), open=True,
                )
                for it in items:
                    plist = it["playlist_alias"] or it["playlist_title"] or f"#{it['playlist_id']}"
                    state, ftag = _file_state(it)
                    iid = self.tv_dup.insert(parent, "end", values=(
                        it["title"] or "",
                        plist,
                        it["playlist_index"] or "",
                        state,
                        it["video_id"],
                        ses_metni(core.resolve_video_path(it["file_path"])),
                    ), tags=(ftag,))
                    self._dup_index[iid] = (it["video_id"], it["playlist_id"])

        if self.dup_show_fuzzy.get():
            # Once kesin tekrarlarda zaten gosterilenleri ele (cift gosterim olmasin)
            exact_vids = set(exact.keys())
            for norm, items in fuzzy.items():
                # Bu grup tum video_id'leri exact ile aciklaniyorsa atla
                unique_vids = {it["video_id"] for it in items}
                if unique_vids.issubset(exact_vids):
                    continue
                uniq_n = len(unique_vids)
                parent = self.tv_dup.insert(
                    "", "end",
                    text=f"[BENZER] '{norm[:60]}'  ({len(items)} kayıt, {uniq_n} farklı video)",
                    values=("", "", "", "", ""),
                    tags=("group_fuzzy",), open=True,
                )
                for it in items:
                    plist = it["playlist_alias"] or it["playlist_title"] or f"#{it['playlist_id']}"
                    state, ftag = _file_state(it)
                    iid = self.tv_dup.insert(parent, "end", values=(
                        it["title"] or "",
                        plist,
                        it["playlist_index"] or "",
                        state,
                        it["video_id"],
                        ses_metni(core.resolve_video_path(it["file_path"])),
                    ), tags=(ftag,))
                    self._dup_index[iid] = (it["video_id"], it["playlist_id"])

    def on_dup_ses_analizi(self):
        """Ayni Videolar'da listelenen dosyalari ses parmak iziyle karsilastirir (yalniz bilgi; dosyaya dokunmaz)."""
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("YT-Arsiv", "Başka bir iş devam ediyor.")
            return
        include_inactive = self.dup_include_inactive.get()
        self.cancel_flag.clear()
        self.cancel_btn.configure(state="normal")
        self._ilerleme(True)
        self.status_var.set("Ses analizi...")

        def worker():
            conn = core.init_db()
            try:
                self._enqueue_log("\n=== Ses analizi (Aynı Videolar grupları) ===")
                s = core.ses_analizi(conn, log=self._enqueue_log, should_cancel=self.cancel_flag.is_set,
                                     include_inactive=include_inactive)
                self._enqueue_log(
                    f"\nGrup: {s['grup']}   Çift: {s['cift']}   (yeni hesaplanan: {s['yeni_cift']}, yeni iz: {s['yeni_iz']})\n"
                    f"AYNI kayıt: {s['ayni']}   benzer sürüm: {s['benzer']}   farklı: {s['farkli']}   hata: {s['hata']}"
                    + ("   [iptal edildi]" if s["iptal"] else "") + "\n")
            except Exception as e:
                self._enqueue_log(f"\n[!] Ses analizi hatası: {e}")
            finally:
                conn.close()
                self.root.after(0, self._on_ses_done)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def on_dup_ses_kutuphane(self):
        """TUM kutuphanedeki dosyalarin ses izini cikarir, ayni kayitlari basligi farkli olsa bile bulur (dosyaya dokunmaz)."""
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("YT-Arsiv", "Başka bir iş devam ediyor.")
            return
        if not messagebox.askyesno(
                "Tüm kütüphanede ses analizi",
                "Kütüphanedeki tüm video dosyalarının ses izi çıkarılacak (dosya başına ~1 sn, ilk seferde yaklaşık "
                "20-30 dk sürebilir). Çıkarılan izler kaydedilir; iptal edersen kaldığı yerden devam eder.\n\n"
                "Bulunan aynı kayıtlar 'Aynı Videolar' listesine eklenir. Dosyalara dokunulmaz.\n\nBaşlansın mı?"):
            return
        include_inactive = self.dup_include_inactive.get()
        self.cancel_flag.clear()
        self.cancel_btn.configure(state="normal")
        self._ilerleme(True)
        self.status_var.set("Tüm kütüphanede ses analizi...")

        def worker():
            conn = core.init_db()
            try:
                self._enqueue_log("\n=== Tüm kütüphanede ses analizi ===")
                s = core.ses_kutuphane_analizi(conn, log=self._enqueue_log, should_cancel=self.cancel_flag.is_set,
                                               include_inactive=include_inactive)
                self._enqueue_log(
                    f"\nDosya: {s['dosya']}   yeni iz: {s['yeni_iz']}   iz hatası: {s['hata']}\n"
                    f"Aday çift: {s['aday']}   yeni karşılaştırılan: {s['yeni_cift']}   "
                    f"AYNI kayıt: {s['ayni']}   benzer: {s['benzer']}   farklı: {s['farkli']}"
                    + ("   [iptal edildi]" if s["iptal"] else "") + "\n")
            except Exception as e:
                self._enqueue_log(f"\n[!] Kütüphane ses analizi hatası: {e}")
            finally:
                conn.close()
                self.root.after(0, self._on_ses_done)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_ses_done(self):
        self._ilerleme(False)
        self.cancel_btn.configure(state="disabled")
        self.status_var.set("Ses analizi bitti.")
        self._refresh_duplicates()

    def _selected_dup_video(self) -> dict | None:
        sel = self.tv_dup.selection()
        if not sel:
            return None
        info = self._dup_index.get(sel[0])
        if not info:
            return None  # Grup satiri secildi
        # Disk-level entry: 3-tuple (vid, None, abs_path)
        if len(info) == 3:
            vid, _, abs_path = info
            return {"video_id": vid, "file_path": abs_path}
        vid, pid = info
        row = self.conn.execute(
            "SELECT * FROM videos WHERE video_id=? AND playlist_id=?", (vid, pid),
        ).fetchone()
        return dict(row) if row else None

    def on_dup_open_video(self):
        r = self._selected_dup_video()
        if not r:
            messagebox.showinfo("YT-Arsiv", "Bir kayıt seç (grup başlığı değil).")
            return
        webbrowser.open(f"https://youtu.be/{r['video_id']}")

    def on_dup_open_file(self):
        r = self._selected_dup_video()
        if not r:
            messagebox.showinfo("YT-Arsiv", "Bir kayıt seç (grup başlığı değil).")
            return
        p = core.resolve_video_path(r.get("file_path"))
        if not p or not p.exists():
            messagebox.showinfo("YT-Arsiv", "Bu kaydın yerel dosyası yok.")
            return
        try:
            os.startfile(str(p))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Açılamadı: {e}")

    # ---- Kopyalama ----

    def _copy_to_clipboard(self, text: str, label: str = ""):
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # baska uygulamalarda gorunsun
        preview = text.replace("\n", " | ")
        if len(preview) > 70:
            preview = preview[:70] + "..."
        self.status_var.set(f"Kopyalandı{(' ('+label+')') if label else ''}: {preview}")

    def _selected_dup_iid(self) -> str | None:
        sel = self.tv_dup.selection()
        return sel[0] if sel else None

    def _selected_dup_group_iid(self) -> str | None:
        """Secili satir grup ise kendisini, yaprak ise parent'ini doner."""
        iid = self._selected_dup_iid()
        if not iid:
            return None
        return iid if iid not in self._dup_index else self.tv_dup.parent(iid)

    def on_dup_delete_selected_files(self):
        """Ayni Videolar: secili satirlari diskten ve DB'den kaldirir.
        - Kayit satiri: core.kopya_kaldir (TEK KAPI).  Dosya yoksa da calisir (hata vermez).  Video hala
          playlist'teyse sonraki taramada geri gelmesin diye o playlist icin 'haric' listesine yazilir.
        - [DISKTE] / [DB DISI] satiri (DB kaydi olmayan dosya): dosya + yan dosyalari silinir."""
        sel = self.tv_dup.selection()
        if not sel:
            messagebox.showinfo("YT-Arsiv", "Önce en az bir kayıt seç (gerekirse Ctrl/Shift ile çoklu).")
            return
        if self._mesgul():
            return
        rows = []    # {vid, pid, label, var}
        files = []   # {file, label}
        for iid in sel:
            info = self._dup_index.get(iid)
            if not info:
                continue  # grup basligi
            if len(info) == 3:
                _vid, _, abs_path = info
                vals = self.tv_dup.item(iid, "values")
                files.append({"file": Path(abs_path), "label": f"[DİSKTE] {vals[1]}/{vals[0]}"})
            else:
                vid, pid = info
                row = self.conn.execute(
                    "SELECT file_path, title FROM videos WHERE video_id=? AND playlist_id=?", (vid, pid),
                ).fetchone()
                if not row:
                    continue
                p = core.resolve_video_path(row["file_path"]) if row["file_path"] else None
                rows.append({"vid": vid, "pid": pid, "label": row["title"] or "?",
                             "var": bool(p and p.exists())})

        if not rows and not files:
            messagebox.showinfo("YT-Arsiv", "Silinecek kayıt bulunamadı (grup başlığı dışında bir satır seç).")
            return

        n_var = sum(1 for r in rows if r["var"])
        preview = "\n".join(
            [f"  - {r['label']}  [{'dosya var' if r['var'] else 'dosya yok'}]" for r in rows[:15]]
            + [f"  - {t['label']}" for t in files[:max(0, 15 - len(rows))]])
        total = len(rows) + len(files)
        more = f"\n  ... ve {total - 15} tane daha" if total > 15 else ""
        ans = messagebox.askyesno(
            "Seçilenleri diskten sil + kaydını kaldır",
            f"{len(rows)} kayıt DB'den kaldırılacak ({n_var} tanesinin dosyası diskten de silinecek, "
            f"dosyası olmayanlar yalnız kayıt olarak kaldırılır).\n"
            f"{len(files)} DB dışı dosya diskten silinecek.\n\n"
            f"Video hâlâ o playlist'teyse sonraki taramada geri gelmez (playlist'in bu videoyu alması engellenir).\n"
            f"Yan dosyalar (.info.json, .webp) da silinir.\n\nSilinecekler:\n{preview}{more}\n\nDevam edilsin mi?",
            icon="warning", default="no",
        )
        if not ans:
            return

        n_rows = n_fail = n_files = 0
        for r in rows:
            ok, msg = core.kopya_kaldir(self.conn, r["vid"], r["pid"])
            if ok:
                n_rows += 1
            else:
                n_fail += 1
                self.log(f"  [!] Kaldırılamadı: {r['label']} ({msg})")
        for t in files:
            p = t["file"]
            if not p.exists():
                continue            # dosya zaten yok: hata degil
            stem, parent = p.stem, p.parent
            for sf in list(parent.iterdir()):
                if sf.is_file() and (sf.stem == stem or sf.name.startswith(stem + ".")):
                    try:
                        sf.unlink()
                        n_files += 1
                    except OSError as e:
                        self.log(f"  [!] Silinemedi: {sf} ({e})")
        self.log(f"[TOPLU SİL] {n_rows} kayıt kaldırıldı, {n_files} DB dışı dosya silindi"
                 + (f", {n_fail} kayıt kaldırılamadı" if n_fail else ""))
        self._refresh_all()

    def on_dup_copy_title(self):
        iid = self._selected_dup_iid()
        if not iid:
            return
        if iid in self._dup_index:
            # Yaprak: sadece bu satirin basligi
            vals = self.tv_dup.item(iid, "values")
            title = vals[0] if vals else ""
            if title:
                self._copy_to_clipboard(title, "baslik")
        else:
            # Grup secili: tum gruptaki basliklari kopyala
            self.on_dup_copy_group_titles()

    def on_dup_copy_group_titles(self):
        group_iid = self._selected_dup_group_iid()
        if not group_iid:
            return
        titles = []
        seen = set()
        for child in self.tv_dup.get_children(group_iid):
            vals = self.tv_dup.item(child, "values")
            t = vals[0] if vals else ""
            if t and t not in seen:
                seen.add(t)
                titles.append(t)
        if titles:
            self._copy_to_clipboard("\n".join(titles), f"{len(titles)} başlık")

    def on_dup_copy_url(self):
        iid = self._selected_dup_iid()
        if not iid:
            return
        if iid in self._dup_index:
            vid = self._dup_index[iid][0]
            self._copy_to_clipboard(f"https://youtu.be/{vid}", "URL")
        else:
            # Grup: tum benzersiz URL'leri
            urls, seen = [], set()
            for child in self.tv_dup.get_children(iid):
                info = self._dup_index.get(child)
                if info and info[0] not in seen:
                    seen.add(info[0])
                    urls.append(f"https://youtu.be/{info[0]}")
            if urls:
                self._copy_to_clipboard("\n".join(urls), f"{len(urls)} URL")

    def on_dup_copy_videoid(self):
        iid = self._selected_dup_iid()
        if not iid:
            return
        if iid in self._dup_index:
            self._copy_to_clipboard(self._dup_index[iid][0], "video ID")
        else:
            ids, seen = [], set()
            for child in self.tv_dup.get_children(iid):
                info = self._dup_index.get(child)
                if info and info[0] not in seen:
                    seen.add(info[0])
                    ids.append(info[0])
            if ids:
                self._copy_to_clipboard("\n".join(ids), f"{len(ids)} ID")

    def _refresh_history(self):
        self.tv_hist.delete(*self.tv_hist.get_children())
        for r in core.get_events(self.conn, self.history_limit.get()):
            plist = r["playlist_alias"] or r["playlist_title"] or ""
            self.tv_hist.insert("", "end", values=(
                (r["timestamp"] or "")[:19].replace("T", " "),
                r["event_type"],
                r["video_id"] or "",
                plist,
                (r["details"] or "")[:200],
            ))

    # ---------------- Selection helpers ----------------

    def _selected_playlist_id(self) -> int | None:
        sel = self.tv_pl.selection()
        if not sel:
            return None
        return int(self.tv_pl.item(sel[0], "values")[0])

    def _selected_lost_row(self) -> dict | None:
        """Yeni dedup'lanmis yapida secili satirin tum bilgilerini doner.
        Donus dict 'instances' icerir: [{playlist_id, playlist_alias, status, file_path}, ...]"""
        sel = self.tv_lost.selection()
        if not sel:
            return None
        return self._lost_index.get(sel[0])

    # ---------------- Actions ----------------

    def on_add(self):
        if self._mesgul():
            return
        url = simpledialog.askstring(
            "Playlist / Kanal Ekle",
            "YouTube playlist veya kanal URL'i:\n(örnek: .../playlist?list=...  ya da  https://www.youtube.com/@kanaladi)",
            parent=self.root)
        if not url:
            return
        url = url.strip()
        kanal = core.kanal_mi(url)
        if kanal:
            url = core.kanal_url_duzenle(url)          # kanalin video listesi sekmesi
            self.root.config(cursor="watch")
            self.root.update_idletasks()
            try:
                n = len((core.fetch_playlist_meta(url) or {}).get("entries") or [])
            except Exception as e:
                n = None
                self.log(f"Kanal video sayısı okunamadı: {e}")
            finally:
                self.root.config(cursor="")
            if n == 0:
                messagebox.showwarning("YT-Arsiv", "Bu kanalda video bulunamadı (adres yanlış ya da kanal boş olabilir).")
                return
            sayi = f"{n} video" if n else "tüm videolar"
            if not messagebox.askyesno(
                    "Kanal Ekle",
                    f"Kanal: {url}\n\nBu kanalda {sayi} var; tarama bunların HEPSİNİ indirir (disk + ağ).\n"
                    "Yeni yüklenenler sonraki taramalarda otomatik eklenir.\n\nEklensin mi?"):
                return
        alias = simpledialog.askstring(
            "Takma Ad", ("Bu kanal" if kanal else "Bu playlist") + " için takma ad (klasör adı olarak kullanılır; boş bırakılabilir):",
            parent=self.root,
        )
        if alias is None:                    # takma ad penceresinde Iptal: ekleme de iptal (bos birakip Tamam = adsiz ekle)
            return
        pid, err = core.add_playlist(self.conn, url, alias.strip())
        if err:
            messagebox.showwarning("YT-Arsiv", err)
            return
        self.log(f"{'Kanal' if kanal else 'Playlist'} eklendi [id={pid}]: {url}")
        self._refresh_playlists()
        if messagebox.askyesno("YT-Arsiv", "Şimdi taransın (videolar indirilsin) mi?"):
            self._start_sync(pid)

    def on_delete_playlist(self):
        pid = self._selected_playlist_id()
        if pid is None:
            messagebox.showinfo("YT-Arsiv", "Önce bir playlist seçin.")
            return
        if self._mesgul():
            return
        r = next((x for x in core.list_playlists(self.conn) if x["id"] == pid), None)
        ad = (r["alias"] or r["title"] or f"#{pid}") if r else f"#{pid}"
        sayim = (f"Aktif: {r['active']}   İndirilen: {r['downloaded']}   Yayından kalkan: {r['unavailable']}   "
                 f"Liste dışı: {r['removed']}") if r else ""
        # 1. onay: yalniz kayitlar.  Varsayilan dugme HAYIR (Enter ile yanlislikla silinmesin).
        if not messagebox.askyesno(
                "Playlisti Sil",
                f"«{ad}»  [id={pid}]  programdan kaldırılsın mı?\n\n{sayim}\n\n"
                "Bu listenin tüm video kayıtları ve olay geçmişi veritabanından silinir.\n"
                "Dosyalar için bir sonraki adımda ayrıca sorulacak.",
                icon="warning", default="no"):
            return
        # 2. onay: dosyalar.  Yayindan kalkan / liste disi videolarin dosyalari TEK KOPYA olabilir.
        tek = (int(r["unavailable"] or 0) + int(r["removed"] or 0)) if r else 0
        uyari = (f"\n\nDİKKAT: Bu listede YouTube'dan kalkmış ya da listeden çıkmış {tek} video var. "
                 "Dosyaları silerseniz bunlar bir daha indirilemeyebilir.") if tek else ""
        delete_files = messagebox.askyesno(
            "Dosyalar da silinsin mi?",
            f"«{ad}» listesinin indirilmiş dosyaları DİSKTEN de silinsin mi?\n\n"
            "EVET: dosyalar kalıcı olarak silinir (geri alınamaz)\n"
            "HAYIR: dosyalar yerinde kalır, yalnızca kayıtlar silinir" + uyari,
            icon="warning", default="no")
        core.delete_playlist(self.conn, pid, delete_files=bool(delete_files))
        self.log(f"Playlist «{ad}» [{pid}] silindi (dosyalar: {'silindi' if delete_files else 'yerinde bırakıldı'})")
        self._refresh_all()

    def on_sync_selected(self):
        pid = self._selected_playlist_id()
        if pid is None:
            messagebox.showinfo("YT-Arsiv", "Önce bir playlist seç.")
            return
        self._start_sync(pid)

    def on_sync_all(self):
        if not core.list_playlists(self.conn):
            messagebox.showinfo("YT-Arsiv", "Henüz playlist yok. Önce '+ Playlist / Kanal Ekle' ile bir liste ya da kanal ekleyin.")
            return
        self._start_sync(None)

    def on_cancel(self):
        if not self._is_calisiyor():
            return
        self.cancel_flag.set()
        self.cancel_btn.configure(state="disabled")
        self.status_var.set("İptal istendi - süren adım bitince duracak...")
        self.log("[iptal istendi - mevcut indirme bitince duracak]")

    def on_open_root(self):
        self._open_in_explorer(core.ROOT)

    def on_open_playlist_url(self):
        pid = self._selected_playlist_id()
        if pid is None:
            return
        r = self.conn.execute("SELECT url FROM playlists WHERE id=?", (pid,)).fetchone()
        if r:
            webbrowser.open(r["url"])

    def on_open_playlist_folder(self):
        pid = self._selected_playlist_id()
        if pid is None:
            return
        self._open_playlist_subdir(pid, None)

    def on_open_playlist_removed_folder(self):
        pid = self._selected_playlist_id()
        if pid is None:
            return
        self._open_playlist_subdir(pid, core.REMOVED_SUBDIR)

    def on_open_removed_folder(self):
        """Liste Disi sekmesinden: downloads\\_ListeDisi\\ arsiv klasoru."""
        r = self._selected_lost_row()
        if not r:
            messagebox.showinfo("YT-Arsiv", "Önce bir video seç.")
            return
        self._open_playlist_subdir(r["playlist_id"], core.REMOVED_SUBDIR)

    def on_scan_local_files(self):
        """Yerel klasorleri tarayip kayip kayitlarin dosyalarini otomatik bagla."""
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("YT-Arsiv", "Bir tarama zaten devam ediyor.")
            return

        def worker():
            conn = core.init_db()
            try:
                self._enqueue_log("\n=== Yerel dosya taraması (kayıpları yeniden bağla) ===")
                s = core.tum_klasorleri_tara(conn, log=self._enqueue_log)
                bil = s.get("bilinmeyen") or {}
                self._enqueue_log(
                    f"\nTaranan dosya: {s['scanned_files']}   "
                    f"Yeniden bağlandı: {s['relinked']}   "
                    f"Status düzeltildi: {s.get('status_reset', 0)}   "
                    f"Title düzeltildi: {s.get('title_fixed', 0)}   "
                    f"Hâlâ kayıp: {s['no_match']}   "
                    f"Klasör orphan: {s.get('folder_orphans', 0)}   "
                    f"Çift taşınan: {s.get('folder_duplicates', 0)}\n"
                    f"_Bilinmeyen'e taşınan: {bil.get('tasinan_video', 0)} video + {bil.get('tasinan_yan', 0)} yan dosya\n"
                )
                bilinmeyen_not = ""
                if bil.get("tasinan_video") or bil.get("tasinan_yan"):
                    bilinmeyen_not = (f"\n\nVeritabanında bağı olmayan {bil.get('tasinan_video', 0)} video + "
                                      f"{bil.get('tasinan_yan', 0)} yan dosya _Bilinmeyen klasörüne taşındı.")
                if bil.get("durdu"):
                    bilinmeyen_not += f"\n\n[!] _Bilinmeyen taşıması DURDU: {bil['durdu']}"
                if s["relinked"]:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "YT-Arsiv",
                        f"{s['relinked']} dosya yeniden bağlandı ve kayıptan çıktı." + bilinmeyen_not
                    ))
                else:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "YT-Arsiv",
                        "Hiçbir yeni eşleşme bulunamadı." + bilinmeyen_not
                    ))
            except Exception as e:
                self._enqueue_log(f"[!] Tarama hatası: {e}")
            finally:
                conn.close()
                self.root.after(0, lambda: self._tarama_bitti("Klasör taraması bitti."))

        self._is_basladi("Tüm klasörler taranıyor...", iptal_edilebilir=False)
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _selected_lost_rows(self) -> list:
        """Liste Disi sekmesinde secili TUM satirlar (Ctrl/Shift ile coklu secim)."""
        return [r for r in (self._lost_index.get(i) for i in self.tv_lost.selection()) if r]

    def _lost_hedefleri(self, rows: list) -> list:
        """[(satir, kayit), ...]: secili videolarin TUM playlist kayitlari (ayni video birden cok listede olabilir)."""
        hedefler = []
        for r in rows:
            kayitlar = r.get("instances") or [{"playlist_id": r["playlist_id"], "playlist_alias": r["playlist_alias"],
                                                "file_path": r.get("file_path")}]
            hedefler.extend((r, k) for k in kayitlar)
        return hedefler

    def _lost_ozet_metni(self, rows: list, hedefler: list) -> str:
        satirlar = []
        for r in rows[:12]:
            p = core.resolve_video_path(r.get("file_path"))
            satirlar.append(f"  - {(r.get('title') or '(başlık yok)')[:60]}  [{r['video_id']}]  "
                            f"dosya: {'VAR' if p and p.exists() else 'yok'}")
        daha = f"\n  ... ve {len(rows) - 12} video daha" if len(rows) > 12 else ""
        dosyali = sum(1 for r in rows if (lambda p: p and p.exists())(core.resolve_video_path(r.get("file_path"))))
        return (f"{len(rows)} video, {len(hedefler)} DB kaydı  (yerel dosyası olan: {dosyali})\n\n"
                + "\n".join(satirlar) + daha)

    def on_delete_lost_record_only(self):
        """Secili kayip videolarin DB kayitlarini siler, yerel dosyalara dokunmaz."""
        rows = self._selected_lost_rows()
        if not rows:
            messagebox.showinfo("YT-Arsiv", "Önce bir veya daha fazla video seç.")
            return
        if self._mesgul():
            return
        hedefler = self._lost_hedefleri(rows)
        if not messagebox.askyesno(
            "Sadece Kaydı Sil",
            "Seçili videoların DB kayıtları silinecek, yerel dosyalar KORUNACAK.\n\n"
            + self._lost_ozet_metni(rows, hedefler) + "\n\nOnaylıyor musun?",
            icon="warning",
        ):
            return
        silinen = hata = 0
        for r, k in hedefler:
            try:
                ok, msg = core.permanently_delete_video(self.conn, r["video_id"], k["playlist_id"], delete_file=False)
            except Exception as e:
                ok, msg = False, f"hata: {e}"
            silinen += bool(ok)
            hata += not ok
            if not ok:
                self.log(f"  [!] {(r.get('title') or '')[:50]} [{k.get('playlist_alias') or '?'}]: {msg}")
        self.log(f"[KAYIT SİL] {len(rows)} video: {silinen} DB kaydı silindi, dosyalar korundu"
                 + (f", {hata} hata" if hata else ""))
        self._refresh_all()

    def on_delete_lost_permanent(self):
        """Secili kayip videolari DB'den ve diskten siler (core.kopya_kaldir - TEK KAPI: baska kayitla paylasilan
        dosyaya dokunmaz).  Biri basarisiz olursa digerleri devam eder."""
        rows = self._selected_lost_rows()
        if not rows:
            messagebox.showinfo("YT-Arsiv", "Önce bir veya daha fazla video seç.")
            return
        if self._mesgul():
            return
        hedefler = self._lost_hedefleri(rows)
        if not messagebox.askyesno(
            "Tamamen Sil",
            "Seçili videolar TÜM kayıtlarıyla DB'den ve diskten (yan dosyalarıyla) KALICI silinecek.\n"
            "Başka bir kayıtla paylaşılan dosyaya dokunulmaz.\n\n"
            + self._lost_ozet_metni(rows, hedefler) + "\n\nOnaylıyor musun?",
            icon="warning",
        ):
            return
        silinen = hata = 0
        for r, k in hedefler:
            try:
                ok, msg = core.kopya_kaldir(self.conn, r["video_id"], k["playlist_id"])
            except Exception as e:
                ok, msg = False, f"hata: {e}"
            silinen += bool(ok)
            hata += not ok
            self.log(f"  [{k.get('playlist_alias') or '?'}] {(r.get('title') or '')[:50]}: {msg}")
        self.log(f"[KALICI SİL] {len(rows)} video: {silinen} kayıt silindi" + (f", {hata} hata" if hata else ""))
        self._refresh_all()

    def _open_playlist_subdir(self, pid: int, subdir: str | None):
        r = self.conn.execute("SELECT alias, title FROM playlists WHERE id=?", (pid,)).fetchone()
        if not r:
            return
        folder = core.sanitize(r["alias"] or r["title"] or f"playlist_{pid}")
        path = core.DOWNLOADS / folder
        if subdir:
            # Arsiv (liste disi / yayindan kalkan) artik playlist altinda degil, downloads\ altinda tek yerde
            path = core.arsiv_kok(str(subdir))
        if not path.exists():
            label = f"'{subdir}/' alt klasörü" if subdir else "Playlist klasörü"
            messagebox.showinfo("YT-Arsiv",
                f"{label} bulunamadı:\n{path}\n\n"
                f"({'Henüz silinen video taşınmamış olabilir.' if subdir else 'Henüz indirme yapılmamış.'})")
            return
        self._open_in_explorer(path)

    def on_open_video(self):
        r = self._selected_lost_row()
        if not r:
            return
        webbrowser.open(f"https://youtu.be/{r['video_id']}")

    def on_open_local_file(self):
        r = self._selected_lost_row()
        if not r:
            return
        p = core.resolve_video_path(r.get("file_path"))
        if not p or not p.exists():
            messagebox.showinfo("YT-Arsiv", "Bu videonun yerel kopyası yok.")
            return
        try:
            os.startfile(str(p))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Açılamadı: {e}")

    def on_open_local_folder(self):
        r = self._selected_lost_row()
        if not r:
            return
        p = core.resolve_video_path(r.get("file_path"))
        if not p:
            messagebox.showinfo("YT-Arsiv", "Bu videonun yerel kopyası yok.")
            return
        if p.exists():
            subprocess.run(["explorer", "/select,", str(p)])
        elif p.parent.exists():
            self._open_in_explorer(p.parent)
        else:
            messagebox.showinfo("YT-Arsiv", "Klasör bulunamadı.")

    def _open_in_explorer(self, path: Path):
        try:
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Açılamadı: {e}")

    # ---------------- Sync worker ----------------

    def _start_sync(self, playlist_id: int | None):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("YT-Arsiv", "Tarama zaten devam ediyor.")
            return

        self._is_basladi("Taranıyor...")
        self._son_ozet = None

        def worker():
            # Worker thread'inde ayri DB baglantisi sart
            conn = core.init_db()
            try:
                summary = core.sync_two_phase(
                    conn,
                    playlist_id=playlist_id,
                    log=self._enqueue_log,
                    should_cancel=self.cancel_flag.is_set,
                    on_phase=lambda t: self.root.after(0, lambda: self.status_var.set(t)),
                )
                self._enqueue_log(
                    f"\n=== ÖZET ===\n"
                    f"Yerel bağlanan          : {((summary.get('local') or {}).get('baglama') or {}).get('relinked', 0)}\n"
                    f"Taranan playlist        : {summary['playlists']}\n"
                    f"Mevcut dosya kullanıldı : {summary.get('linked_existing', 0)}\n"
                    f"Yeni indirilen          : {summary['new']}\n"
                    f"Listeden çıkarılan (sen): {summary['removed']}\n"
                    f"Yayından kalkan (YouTube): {summary['unavailable']}\n"
                    f"_YayındanKalkan'a taşınan: {summary.get('yayindan_kalkan_tasinan', 0)}\n"
                    f"Sabit bağla birleşen     : {summary.get('kopya_baglanan', 0)} dosya ({summary.get('kopya_kazanilan_mb', 0):.0f} MB)\n"
                    f"Farklı kopya (kayıtlı)   : {summary.get('kopya_farkli', 0)}\n"
                    f"Ek depoya giden indirme : {summary.get('depo_yonlendirilen', 0)}\n"
                    f"Depo dolu (atlanan)     : {summary.get('depo_dolu', 0)}\n"
                    f"Hata                    : {summary['errors']}\n"
                )
                self._son_ozet = summary
            except Exception as e:
                self._enqueue_log(f"\n[!] Beklenmeyen hata: {e}\n{traceback.format_exc()}")
                self._son_ozet = {"beklenmeyen_hata": str(e)}
            finally:
                conn.close()
                self.root.after(0, self._on_sync_done)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_sync_done(self):
        ozet = getattr(self, "_son_ozet", None) or {}
        if ozet.get("beklenmeyen_hata"):
            durum = "Tarama HATA ile durdu - ayrıntı günlükte."
        elif self.cancel_flag.is_set():
            durum = "Tarama iptal edildi."
        else:
            parcalar = [f"{ozet.get('new', 0)} yeni indirildi"]
            if ozet.get("errors"):
                parcalar.append(f"{ozet['errors']} HATA (ayrıntı günlükte)")
            if ozet.get("depo_dolu"):
                parcalar.append(f"{ozet['depo_dolu']} video depo dolu olduğu için atlandı")
            if ozet.get("yakinda"):
                parcalar.append(f"{ozet['yakinda']} planlı prömiyer bekliyor")
            durum = "Tarama bitti: " + ", ".join(parcalar) + "."
        self._is_bitti(durum)
        self._refresh_all()
        self.root.after(300, lambda: self._silinme_sor(otomatik=True))
        ozet, self._son_ozet = getattr(self, "_son_ozet", None), None
        if ozet and ozet.get("depo_dolu"):
            self.root.after(600, lambda: self._depo_dolu_uyar(ozet["depo_dolu"]))

    # ---------------- Coklu silinme onayi ----------------

    def on_silinme_onaylari(self):
        self._silinme_sor(otomatik=False)

    def _silinme_sor(self, otomatik=False):
        """Cekirdegin 'sayim dustu' korumasi yuzunden silinmis SAYILMAYAN videolari kullaniciya sorar (kendi basina karar vermez)."""
        bek = core.silinme_bekleyenler(self.conn)
        if not bek:
            if not otomatik:
                messagebox.showinfo("YT-Arsiv", "Onay bekleyen silinme yok.")
            return
        eski = getattr(self, "_silinme_pencere", None)
        if eski is not None and eski.winfo_exists():
            eski.lift()
            return
        win = tk.Toplevel(self.root)
        self._silinme_pencere = win
        win.title("Silinme onayları")
        win.geometry(f"{self._px(980)}x{self._px(540)}")
        win.transient(self.root)
        ttk.Label(
            win, justify="left", wraplength=self._px(940),
            text=("Aşağıdaki videolar YouTube listesinde GÖRÜNMÜYOR, ama liste bir anda çok küçüldüğü için (yanlış alarm koruması) "
                  "otomatik 'silindi' sayılmadı ve başka listede kopyaları da yok.\n"
                  "Seçililer için:  'Listeden çıkmış say' = sen çıkardın (dosyası _ListeDisi'ne gider)  |  "
                  "'Koru' = silinmedi, kalsın (video listeye dönene kadar bir daha sorulmaz)."),
        ).pack(fill="x", padx=10, pady=8)
        cols = ("playlist", "title", "uploader", "file", "video_id")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10)
        tv = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c, ad, w in (("playlist", "Playlist", 110), ("title", "Başlık", 480), ("uploader", "Yükleyen", 140),
                         ("file", "Dosya", 60), ("video_id", "Video ID", 110)):
            tv.heading(c, text=ad)
            tv.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for b in bek:
            tv.insert("", "end", iid=f"{b['playlist_id']}|{b['video_id']}", values=(
                b["playlist_alias"], b["title"] or "(başlık yok)", b["uploader"] or "?",
                "VAR" if b["dosya_var"] else "YOK", b["video_id"]))
        self._tablolari_hazirla(frame)
        durum = tk.StringVar(value=f"{len(bek)} video onay bekliyor.  Karar vereceklerinizi seçin (Ctrl/Shift ile çoklu).")
        ttk.Label(win, textvariable=durum).pack(anchor="w", padx=10, pady=4)

        def secilenler():
            return [(int(i.split("|", 1)[0]), i.split("|", 1)[1]) for i in tv.selection()]

        def uygula(islem, mesaj, onay=None):
            anahtarlar = secilenler()
            if not anahtarlar:
                messagebox.showinfo("YT-Arsiv", "Önce video seç.", parent=win)
                return
            if self._mesgul():
                return
            if onay and not messagebox.askyesno("YT-Arsiv", onay.format(n=len(anahtarlar)), icon="warning", default="no", parent=win):
                return
            n = islem(anahtarlar)
            for pid, vid in anahtarlar:
                if tv.exists(f"{pid}|{vid}"):
                    tv.delete(f"{pid}|{vid}")
            self.log(f"{mesaj}: {n} video")
            durum.set(f"{len(tv.get_children())} video onay bekliyor.")
            self._refresh_all()
            if not tv.get_children():
                win.destroy()

        def onayla():
            uygula(lambda a: core.silinmeyi_onayla(self.conn, a, log=self.log), "Listeden çıkmış sayıldı",
                   onay="Seçili {n} video LİSTEDEN ÇIKMIŞ sayılsın mı?\n\nDosyaları silinmez; _ListeDisi klasörüne taşınır.")

        def koru():
            uygula(lambda a: core.silinmeyi_koru(self.conn, a), "Korundu (silinmedi)")

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=8)
        ttk.Button(bar, text="Listeden çıkmış say", command=onayla).pack(side="left", padx=2)
        ttk.Button(bar, text="Koru (silinmedi)", command=koru).pack(side="left", padx=2)
        ttk.Button(bar, text="Tümünü seç", command=lambda: tv.selection_set(tv.get_children())).pack(side="left", padx=12)
        ttk.Button(bar, text="Sonra sor", command=win.destroy).pack(side="right", padx=2)

    # ---------------- Depolama ----------------

    def _depo_dolu_uyar(self, n):
        if messagebox.askyesno(
                "YT-Arsiv",
                f"Depo dolu: {n} video indirilemedi (sonraki taramada yeniden denenir).\n\n"
                "Boş yeri görmek / ek depo eklemek için Ayarlar > Depolama açılsın mı?"):
            self.on_depolama()

    def on_depolama(self):
        self.on_ayarlar(sayfa="depolama")

    def on_ayarlar(self, sayfa=None):
        """AYARLAR penceresi: Depolama (depolar + bos yer siniri), Indirme (bekleme + uzun video kalitesi), Bilgi (konum/surum).
        Tum okuma/yazma core kapilarindan (depo_*, indirme_ayarlari*, ortam_bilgisi); GUI ayar dosyasina dogrudan dokunmaz."""
        eski = getattr(self, "_ayar_pencere", None)
        if eski is not None and eski.winfo_exists():
            eski.lift()
            if sayfa:
                self._ayar_nb.select(self._ayar_sayfa[sayfa])
            return
        win = tk.Toplevel(self.root)
        self._ayar_pencere = win
        win.title("Ayarlar")
        win.geometry(f"{self._px(980)}x{self._px(560)}")
        win.transient(self.root)
        alt = ttk.Frame(win)
        alt.pack(side="bottom", fill="x", padx=self._px(10), pady=self._px(8))
        ttk.Button(alt, text="Kapat", command=win.destroy).pack(side="right")
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=self._px(8), pady=(self._px(8), 0))
        self._ayar_nb, self._ayar_sayfa = nb, {}
        for anahtar, ad, kur in (("depolama", "Depolama", self._depolama_paneli), ("indirme", "İndirme", self._indirme_paneli),
                                 ("bilgi", "Bilgi", self._bilgi_paneli)):
            cerceve = ttk.Frame(nb)
            nb.add(cerceve, text=ad)
            self._ayar_sayfa[anahtar] = cerceve
            kur(cerceve)
        if sayfa:
            nb.select(self._ayar_sayfa[sayfa])

    def _indirme_paneli(self, win):
        ay = core.indirme_ayarlari()
        ic = ttk.Frame(win, padding=self._px(14))
        ic.pack(fill="both", expand=True)
        ttk.Label(ic, text="İndirmeler arası bekleme", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=6, sticky="w")
        ttk.Label(ic, justify="left", wraplength=self._px(880), style="Soluk.TLabel",
                  text="Art arda indirmeler arasında bu iki değer arasında rastgele bir süre beklenir. Kısa bekleme taramayı "
                       "hızlandırır ama YouTube'un \"bot musun\" engeline takılma riskini artırır. 0 - 0 = hiç bekleme."
                  ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(2, 8))
        en_az, en_cok = tk.StringVar(value=f"{ay['bekleme'][0]:g}"), tk.StringVar(value=f"{ay['bekleme'][1]:g}")
        s1 = ttk.Frame(ic)
        s1.grid(row=2, column=0, columnspan=6, sticky="w")
        ttk.Label(s1, text="En az").pack(side="left")
        ttk.Entry(s1, textvariable=en_az, width=7).pack(side="left", padx=6)
        ttk.Label(s1, text="en çok").pack(side="left")
        ttk.Entry(s1, textvariable=en_cok, width=7).pack(side="left", padx=6)
        ttk.Label(s1, text="saniye").pack(side="left")

        ttk.Label(ic, text="Uzun videolarda kalite sınırı", font=("Segoe UI", 10, "bold")).grid(
            row=3, column=0, columnspan=6, sticky="w", pady=(self._px(22), 0))
        ttk.Label(ic, justify="left", wraplength=self._px(880), style="Soluk.TLabel",
                  text="Uzun videolar (ör. DJ setleri) en iyi kalitede çok yer tutar. Belirtilen süreden uzun videolar en fazla "
                       "seçilen çözünürlükte indirilir; kısa videolar her zaman en iyi kalitede iner."
                  ).grid(row=4, column=0, columnspan=6, sticky="w", pady=(2, 8))
        uzun_dk = tk.StringVar(value=str(ay["uzun_dk"]))
        secenek = {("Sınır yok" if x == 0 else f"{x}p"): x for x in core.UZUN_VIDEO_COZUNURLUKLERI}
        uzun_p = tk.StringVar(value=next(k for k, v in secenek.items() if v == ay["uzun_p"]))
        s2 = ttk.Frame(ic)
        s2.grid(row=5, column=0, columnspan=6, sticky="w")
        ttk.Entry(s2, textvariable=uzun_dk, width=7).pack(side="left")
        ttk.Label(s2, text="dakikadan uzun videolar en fazla").pack(side="left", padx=6)
        ttk.Combobox(s2, textvariable=uzun_p, values=list(secenek), state="readonly", width=10).pack(side="left")
        ttk.Label(s2, text="olarak indirilsin").pack(side="left", padx=6)

        durum = tk.StringVar(value="")

        def kaydet():
            ok, sonuc = core.indirme_ayarlari_yaz(en_az.get(), en_cok.get(), uzun_dk.get(), secenek[uzun_p.get()])
            if not ok:
                messagebox.showerror("YT-Arsiv", str(sonuc), parent=win)
                return
            durum.set("Kaydedildi. Bir sonraki indirmeden itibaren geçerli.")
            self.log(f"Indirme ayarlari kaydedildi: bekleme {sonuc['bekleme'][0]:g}-{sonuc['bekleme'][1]:g} sn, "
                     f"{sonuc['uzun_dk']} dk'dan uzun videolar: {'sinir yok' if not sonuc['uzun_p'] else str(sonuc['uzun_p']) + 'p'}")

        s3 = ttk.Frame(ic)
        s3.grid(row=6, column=0, columnspan=6, sticky="w", pady=(self._px(22), 0))
        ttk.Button(s3, text="Kaydet", command=kaydet).pack(side="left")
        ttk.Label(s3, textvariable=durum, style="Soluk.TLabel").pack(side="left", padx=10)

    def _bilgi_paneli(self, win):
        b = core.ortam_bilgisi()
        ic = ttk.Frame(win, padding=self._px(14))
        ic.pack(fill="both", expand=True)
        var, yok = "var", "YOK"
        satirlar = [
            ("Uygulama klasörü", b["uygulama"]),
            ("Arşiv (indirme) klasörü", b["arsiv"]),
            ("Veritabanı", b["veritabani"]),
            ("Ayar dosyası", b["ayar_dosyasi"] + ("   [BOZUK - düzeltin ya da silin]" if b["ayar_dosyasi_bozuk"] else "")),
            ("Python", b["python"]),
            ("yt-dlp (indirici)", b["yt_dlp"] or yok),
            ("ffmpeg (ses+görüntü birleştirme)", b["ffmpeg"] or f"{yok} - videolar tek dosya halinde birleştirilemeyebilir"),
            ("Ses analizi aracı", b["ses_araci"] or f"{yok} - ses analizi çalışmaz"),
            ("Çerez dosyası", (f"{var}: {b['cerez']}" if b["cerez"] else f"yok  (isteğe bağlı: {b['cerez_yolu']})")),
        ]
        for i, (ad, deger) in enumerate(satirlar):
            ttk.Label(ic, text=ad, font=("Segoe UI", 10, "bold")).grid(row=i, column=0, sticky="nw", pady=2)
            ttk.Label(ic, text=str(deger), wraplength=self._px(640), justify="left").grid(row=i, column=1, sticky="w", padx=self._px(14), pady=2)
        ttk.Label(ic, justify="left", wraplength=self._px(900), style="Soluk.TLabel",
                  text="YouTube sık değişir; indirmeler birden çalışmaz olursa yt-dlp eskimiş olabilir: proje sayfasından yeni sürümü "
                       "indirin. Çerez dosyası yalnızca YouTube \"bot musun\" kontrolü istediğinde kullanılır; hesabınızın oturum "
                       "bilgisini içerir, kimseyle paylaşmayın."
                  ).grid(row=len(satirlar), column=0, columnspan=2, sticky="w", pady=(self._px(16), 8))
        bar = ttk.Frame(ic)
        bar.grid(row=len(satirlar) + 1, column=0, columnspan=2, sticky="w")
        ttk.Button(bar, text="Uygulama klasörünü aç", command=lambda: self._open_in_explorer(Path(b["uygulama"]))).pack(side="left", padx=2)
        ttk.Button(bar, text="Arşiv klasörünü aç", command=self.on_open_root).pack(side="left", padx=2)
        ttk.Button(bar, text="Günlük klasörünü aç", command=self.on_open_log_folder).pack(side="left", padx=2)
        ttk.Button(bar, text="Proje sayfası", command=lambda: webbrowser.open(PROJE_SAYFASI)).pack(side="left", padx=2)

    def _depolama_paneli(self, win):
        """Birincil + ek depolar: bos yer, kayit sayisi; ek depo ekle/kaldir; minimum bos yer.  Tum isler core.depo_* kapilarindan."""
        ttk.Label(
            win, justify="left", wraplength=self._px(900),
            text=("Birincil depo (downloads) dolarsa yeni indirmeler sırayla EK DEPOLARA gider (ayrı sürücü ya da klasör). "
                  "Hiçbir depoda yeterli boş yer yoksa indirme temiz durur ve sonraki taramada yeniden denenir.\n"
                  "Dosyalar bulundukları depoda kalır (_ListeDisi arşivi de aynı depoda tutulur). Sabit bağ ile yer kazandıran "
                  "birleştirme yalnız aynı sürücüde çalışır."),
        ).pack(fill="x", padx=10, pady=8)
        cols = ("yol", "tur", "bos", "toplam", "kayit", "durum")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10)
        tv = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse", height=6)
        for c, ad, w in (("yol", "Depo klasörü", 380), ("tur", "Tür", 70), ("bos", "Boş", 90), ("toplam", "Toplam", 90),
                         ("kayit", "Video kaydı", 90), ("durum", "Durum", 120)):
            tv.heading(c, text=ad)
            tv.column(c, width=w, anchor="w")
        tv.pack(fill="both", expand=True)
        self._tablolari_hazirla(frame)
        durum = tk.StringVar(value="")
        ttk.Label(win, textvariable=durum).pack(anchor="w", padx=10, pady=2)

        def doldur():
            tv.delete(*tv.get_children())
            for d in core.depo_durumu(self.conn):
                tv.insert("", "end", iid=d["yol"], values=(
                    d["yol"], "Birincil" if d["ana"] else "Ek", f"{d['bos'] / 2 ** 30:.1f} GB", f"{d['toplam'] / 2 ** 30:.1f} GB",
                    d["kayit"], "Takılı" if d["var"] else "TAKILI DEĞİL"))

        def ekle():
            if self._mesgul():
                return
            yol = filedialog.askdirectory(parent=win, title="Ek depo klasörü seç (örnek D:/YT-Arsiv-2)")
            if not yol:
                return
            ok, sonuc = core.depo_ekle(yol)
            if not ok:
                messagebox.showerror("YT-Arsiv", str(sonuc), parent=win)
                return
            self.log(f"Ek depo eklendi: {sonuc}")
            durum.set(f"Ek depo eklendi: {sonuc}")
            doldur()

        def kaldir():
            sec = tv.selection()
            if not sec:
                messagebox.showinfo("YT-Arsiv", "Önce bir ek depo seç.", parent=win)
                return
            yol = sec[0]
            if self._mesgul():
                return
            if any(d["ana"] and d["yol"] == yol for d in core.depo_durumu()):
                messagebox.showinfo("YT-Arsiv", "Birincil depo kaldırılamaz.", parent=win)
                return
            if not messagebox.askyesno("YT-Arsiv", f"{yol}\n\nEk depo listeden kaldırılsın mı? Dosyalar SİLİNMEZ ama depo artık taranmaz.",
                                       parent=win):
                return
            ok, sonuc = core.depo_kaldir(self.conn, yol)
            if not ok:
                messagebox.showerror("YT-Arsiv", str(sonuc), parent=win)
                return
            self.log(f"Ek depo kaldırıldı: {yol} ({sonuc} video kaydı bu depoda duruyor)")
            durum.set(f"Kaldırıldı. {sonuc} video kaydı bu depoda duruyor (dosyalar yerinde).")
            doldur()

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=8)
        ttk.Button(bar, text="Ek depo ekle...", command=ekle).pack(side="left", padx=2)
        ttk.Button(bar, text="Seçili ek depoyu kaldır", command=kaldir).pack(side="left", padx=2)
        ttk.Button(bar, text="Yenile", command=doldur).pack(side="left", padx=2)
        mb = tk.StringVar(value=str(core.depo_min_bos_mb()))
        ayar = ttk.Frame(win)
        ayar.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(ayar, text="Bir depoda en az bu kadar boş yer (MB) kalmalı, yoksa yeni indirme o depoya başlamaz:").pack(side="left")
        ttk.Entry(ayar, textvariable=mb, width=8).pack(side="left", padx=6)

        def kaydet():
            ok, sonuc = core.depo_min_bos_ayarla(mb.get())
            if not ok:
                messagebox.showerror("YT-Arsiv", str(sonuc), parent=win)
                return
            durum.set(f"Minimum boş yer {sonuc} MB olarak kaydedildi.")

        ttk.Button(ayar, text="Kaydet", command=kaydet).pack(side="left")
        doldur()

    # ---------------- Log ----------------

    def _enqueue_log(self, msg: str):
        self.log_queue.put(str(msg))

    def log(self, msg: str):
        self._enqueue_log(msg)

    @staticmethod
    def _gunluk_etiketi(satir: str) -> str:
        """Gunluk satirinin rengi: cekirdegin satir basi isaretlerine gore ([!] hata, [?] uyari, [+] iyi, === baslik)."""
        s = satir.lstrip()
        if s.startswith("[!]") or "HATA" in s or "Beklenmeyen hata" in s:
            return "hata"
        if s.startswith(("[?]", "[~]", "[iptal")) or "DEPO DOLU" in s:
            return "uyari"
        if s.startswith(("[+]", "[>]", "[<]")):
            return "iyi"
        if s.startswith("==="):
            return "baslik"
        if s.startswith(("[i]", "[=]", "[-]", "[_]")):
            return "soluk"
        return ""

    def _gunluge_yaz(self, satirlar: list):
        """Gunluk DOSYASINA yazan TEK kapi: logs\\gui-YYYYAAGG.log (pencere kapaninca gunluk kaybolmasin).
        Dosya yazilamazsa (salt okunur klasor vb.) sessizce vazgecer: gunluk ekranda kalir, uygulama etkilenmez."""
        try:
            if self._gunluk_dosyasi is None:
                GUNLUK_KLASORU.mkdir(exist_ok=True)
                self._gunluk_dosyasi = open(GUNLUK_KLASORU / time.strftime("gui-%Y%m%d.log"), "a", encoding="utf-8")
            damga = time.strftime("%H:%M:%S ")
            self._gunluk_dosyasi.write("".join(damga + s + "\n" for m in satirlar for s in m.split("\n")))
            self._gunluk_dosyasi.flush()
        except OSError:
            self._gunluk_dosyasi = False            # bir daha deneme

    def _poll_log_queue(self):
        yeni = []
        try:
            while True:
                yeni.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if yeni:
            if self._gunluk_dosyasi is not False:
                self._gunluge_yaz(yeni)
            self.log_txt.configure(state="normal")
            for msg in yeni:
                for satir in msg.split("\n"):
                    etiket = self._gunluk_etiketi(satir)
                    self.log_txt.insert("end", satir + "\n", (etiket,) if etiket else ())
            fazla = int(self.log_txt.index("end-1c").split(".")[0]) - GUNLUK_SATIR_SINIRI
            if fazla > 0:
                self.log_txt.delete("1.0", f"{fazla + 1}.0")
            self.log_txt.see("end")
            self.log_txt.configure(state="disabled")
        self.root.after(150, self._poll_log_queue)


def main():
    root = tk.Tk()
    root.withdraw()                                  # hazirlik bitene kadar yarim cizilmis pencere gorunmesin
    try:
        App(root)
    except Exception as e:                           # konsolsuz exe'de sessizce kapanmasin: nedenini goster
        messagebox.showerror(
            "YT-Arsiv açılamadı",
            f"{e}\n\nUygulama klasörü: {SCRIPT_DIR}\n"
            "Klasör yazılabilir olmalı (Program Files altında çalışmaz) ve veritabanı dosyası bozuk olmamalı.\n\n"
            + traceback.format_exc()[-1200:])
        root.destroy()
        sys.exit(1)
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
