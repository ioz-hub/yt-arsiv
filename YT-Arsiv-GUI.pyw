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
import webbrowser
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

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
        f"ytarsiv_core.py bulunamadi.\n\nHata: {e}\n\n"
        f"Aranan klasor: {SCRIPT_DIR}",
    )
    sys.exit(1)

# yt-dlp kurulu mu?
if core.YoutubeDL is None:
    tk.Tk().withdraw()
    messagebox.showerror(
        "YT-Arsiv",
        "yt-dlp kurulu degil.\n\nKomut satirinda calistir:\n  pip install yt-dlp",
    )
    sys.exit(1)


STATUS_TR = {
    "active":                 "Aktif",
    "unavailable":            "YouTube'da erisilemiyor (YouTube yapti)",
    "removed_from_playlist":  "Listeden cikarilmis (sen yaptin)",
    "user_deleted":           "Kullanici sildi (geri inmez)",
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("YT-Arsiv  -  playlist arsivleyici")
        self.root.geometry("1100x700")
        self.root.minsize(800, 500)

        self.conn = core.init_db()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()

        # Manuel koleksiyon sekmeleri (dinamik): playlist_id -> (frame, treeview, index_dict)
        self._manual_tabs: dict[int, tuple] = {}

        self._build_ui()
        # Acilirken: manuel klasorleri tara + dosyalari yeniden bagla
        try:
            core.sync_manual_collections(self.conn, log=lambda m: None)
        except Exception as e:
            self.log_queue.put(f"[!] Manuel klasor taramasi basarisiz: {e}")
        try:
            s = core.scan_and_relink_files(self.conn, log=lambda m: None)
            if s["relinked"]:
                self.log_queue.put(f"[+] Acilista {s['relinked']} dosya otomatik baglandi")
        except Exception as e:
            self.log_queue.put(f"[!] Dosya tarama basarisiz: {e}")
        self._refresh_all()
        self._poll_log_queue()

    # ---------------- UI ----------------

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        # Toolbar
        toolbar = ttk.Frame(self.root, padding=(8, 6))
        toolbar.pack(side="top", fill="x")

        ttk.Button(toolbar, text="+ Playlist / Kanal Ekle", command=self.on_add).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Sec.Playlisti Tara",     command=self.on_sync_selected).pack(side="left", padx=2)
        ttk.Button(toolbar, text="HEPSINI TARA",           command=self.on_sync_all).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Silinme onaylari",       command=self.on_silinme_onaylari).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Manuel klasorleri tara", command=self.on_scan_manual).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Tum klasorleri tara", command=self.on_scan_local_files).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Klasoru Ac",             command=self.on_open_root).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Yenile",                 command=self._refresh_all).pack(side="left", padx=2)

        self.cancel_btn = ttk.Button(toolbar, text="Iptal", command=self.on_cancel, state="disabled")
        self.cancel_btn.pack(side="right", padx=2)

        self.progress = ttk.Progressbar(toolbar, mode="indeterminate", length=180)
        self.progress.pack(side="right", padx=8)

        # Hizli arama kutusu - progress'in soluna
        ttk.Separator(toolbar, orient="vertical").pack(side="right", fill="y", padx=6)
        self.toolbar_search_var = tk.StringVar()
        ttk.Label(toolbar, text="Ara:").pack(side="right")
        toolbar_entry = ttk.Entry(toolbar, textvariable=self.toolbar_search_var, width=35)
        toolbar_entry.pack(side="right", padx=4)
        toolbar_entry.bind("<Return>", lambda e: self._toolbar_search_go())

        # Status bar (en alta yapissin diye paned'den ONCE pack et)
        self.status_var = tk.StringVar(value=f"Veritabani: {core.DB_PATH}")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w",
                  relief="sunken", padding=(6, 2)).pack(side="bottom", fill="x")

        # Notebook + Log: dikey PanedWindow (kullanici sash'i surukleyebilir)
        self.paned = ttk.PanedWindow(self.root, orient="vertical")
        self.paned.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 4))

        self.nb = ttk.Notebook(self.paned)
        self.paned.add(self.nb, weight=1)

        self._build_tab_playlists()
        self._build_tab_lost()
        self._build_tab_unavailable()
        self._build_tab_duplicates()
        self._build_tab_search()
        self._build_tab_history()
        self._build_log_panel()  # log paned'in ALT bolumune eklenir

        # Acilista %40 notebook, %60 log. Pencere boyutu hazirlanir hazirlanmaz uygula.
        self.root.after(50, self._apply_initial_sash)

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
            self.paned.sashpos(0, int(total * 0.40))
        except tk.TclError:
            pass

    def _build_tab_playlists(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Playlistler")

        cols = ("id", "alias", "title", "active", "downloaded", "unavailable", "removed", "last_checked")
        headers = ("ID", "Takma Ad", "Baslik", "Aktif", "Indirilen", "Yayindan Kalkan", "Liste Disi", "Son Tarama")
        widths = (40, 150, 320, 60, 80, 90, 70, 140)

        self.tv_pl = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, headers, widths):
            self.tv_pl.heading(c, text=h)
            self.tv_pl.column(c, width=w, anchor="w" if c in ("alias", "title") else "center")

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_pl.yview)
        self.tv_pl.configure(yscrollcommand=vsb.set)
        self.tv_pl.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Sag tik menusu
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Bu playlisti tara", command=self.on_sync_selected)
        menu.add_command(label="YouTube'da ac",     command=self.on_open_playlist_url)
        menu.add_command(label="Indirme klasorunu ac", command=self.on_open_playlist_folder)
        menu.add_command(label="_ListeDisi/ klasorunu ac", command=self.on_open_playlist_removed_folder)
        menu.add_separator()
        menu.add_command(label="SIL",                command=self.on_delete_playlist)
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
        self.nb.add(f, text="Liste Disi")

        top = ttk.Frame(f)
        top.pack(side="top", fill="x", padx=4, pady=4)
        self.lost_count_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.lost_count_var, font=("Segoe UI", 10, "bold")).pack(side="left")

        self.lost_only_missing = tk.BooleanVar(value=False)  # varsayilan: listeden cikardigin TUM videolar
        ttk.Checkbutton(top, text="Sadece dosyasi olmayanlari goster",
                        variable=self.lost_only_missing,
                        command=self._refresh_lost).pack(side="left", padx=(12, 4))
        ttk.Button(top, text="Yerel dosyalarda ara",
                   command=self.on_scan_local_files).pack(side="left", padx=2)

        ttk.Button(top, text="Secili videoyu YouTube'da ac", command=self.on_open_video).pack(side="right", padx=2)
        ttk.Button(top, text="Yerel dosyayi ac",              command=self.on_open_local_file).pack(side="right", padx=2)
        ttk.Button(top, text="Klasorunu ac",                  command=self.on_open_local_folder).pack(side="right", padx=2)
        ttk.Button(top, text="_ListeDisi/ ac",               command=self.on_open_removed_folder).pack(side="right", padx=2)
        ttk.Separator(top, orient="vertical").pack(side="right", fill="y", padx=6)
        ttk.Button(top, text="Sadece kaydı sil", command=self.on_delete_lost_record_only).pack(side="right", padx=2)
        ttk.Button(top, text="DB + dosyayı sil", command=self.on_delete_lost_permanent).pack(side="right", padx=2)

        cols = ("status", "title", "uploader", "playlist", "file", "yt", "last_seen", "video_id")
        headers = ("Durum", "Baslik", "Uploader", "Playlist", "Yerel Dosya", "YouTube'da", "Son Goruldu", "Video ID")
        widths = (170, 310, 130, 130, 80, 150, 125, 100)

        self.tv_lost = ttk.Treeview(f, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, headers, widths):
            self.tv_lost.heading(c, text=h)
            self.tv_lost.column(c, width=w, anchor="w" if c in ("title", "uploader", "playlist") else "center")

        self.tv_lost.tag_configure("has_file",   foreground="#0a7d18")
        self.tv_lost.tag_configure("no_file",    foreground="#b00020")
        self.tv_lost.tag_configure("file_gone",  foreground="#aa5500")
        self.tv_lost.tag_configure("yt_gone", foreground="#b00020", background="#fde8e8")

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

    ES_ETIKET = {"ayni": "AYNI VIDEO", "guclu": "GUCLU", "olasi": "olasi - kontrol et"}

    def _build_tab_unavailable(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Yayindan Kalkan")

        top = ttk.Frame(f)
        top.pack(side="top", fill="x", padx=4, pady=(4, 0))
        self.una_count_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.una_count_var, font=("Segoe UI", 10, "bold")).pack(side="left")
        self.una_only_twin = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Sadece ayni videosu baska yerde olanlar", variable=self.una_only_twin,
                        command=self._refresh_unavailable).pack(side="left", padx=(12, 4))
        ttk.Button(top, text="Yenile", command=self._refresh_unavailable).pack(side="right", padx=2)

        row2 = ttk.Frame(f)
        row2.pack(side="top", fill="x", padx=4, pady=(2, 4))
        ttk.Button(row2, text="Sec.ilenleri diskten sil + DB'den kaldir",
                   command=self.on_una_delete).pack(side="left", padx=2)
        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(row2, text="Yerel dosyayi ac", command=self.on_una_open_file).pack(side="left", padx=2)
        ttk.Button(row2, text="Ayni videonun dosyasini ac", command=self.on_una_open_twin).pack(side="left", padx=2)
        ttk.Button(row2, text="Klasorunu ac", command=self.on_una_open_folder).pack(side="left", padx=2)
        ttk.Button(row2, text="YouTube'da ac", command=self.on_una_open_youtube).pack(side="left", padx=2)
        ttk.Button(row2, text="Wayback'te ara", command=self.on_una_open_wayback).pack(side="left", padx=2)
        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(row2, text="YouTube'da yayinda mi? (kontrol)",
                   command=self.on_una_youtube_kontrol).pack(side="left", padx=2)

        cols = ("title", "uploader", "playlist", "mb", "file", "twin", "yt", "where", "since", "video_id")
        headers = ("Baslik", "Uploader", "Playlist", "MB", "Yerel Dosya", "Ayni Video (baska yerde)",
                   "YouTube'da", "Konum", "Kalkis Tespiti", "Video ID")
        widths = (300, 120, 90, 60, 70, 330, 150, 120, 115, 100)

        self.tv_una = ttk.Treeview(f, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, headers, widths):
            self.tv_una.heading(c, text=h)
            self.tv_una.column(c, width=w,
                               anchor="w" if c in ("title", "uploader", "playlist", "twin", "where") else "center")
        self.tv_una.tag_configure("twin_strong", background="#e3f4e3")
        self.tv_una.tag_configure("twin_maybe", background="#fff8d6")
        self.tv_una.tag_configure("has_file", foreground="#0a7d18")
        self.tv_una.tag_configure("no_file", foreground="#b00020")
        self.tv_una.tag_configure("file_gone", foreground="#aa5500")
        self.tv_una.tag_configure("yt_live", background="#cfe6ff")      # en son tanimlanan = en yuksek oncelik

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_una.yview)
        self.tv_una.configure(yscrollcommand=vsb.set)
        self.tv_una.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tv_una.bind("<Double-1>", lambda e: self.on_una_open_file())
        self.tv_una.bind("<Control-a>", lambda e: (self.tv_una.selection_set(self.tv_una.get_children()), "break"))

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Basligi kopyala", command=lambda: self._una_copy("title"))
        menu.add_command(label="Video ID'sini kopyala", command=lambda: self._una_copy("video_id"))
        menu.add_separator()
        menu.add_command(label="Yerel dosyayi ac", command=self.on_una_open_file)
        menu.add_command(label="Ayni videonun dosyasini ac", command=self.on_una_open_twin)
        menu.add_command(label="Klasorunu ac", command=self.on_una_open_folder)

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
                konum = core.YAYINDAN_KALKAN_DIR if core.YAYINDAN_KALKAN_DIR in p.parts else "playlist klasorunde"
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
                yt_txt = f"kalkmis ({tarih})"
            else:
                yt_txt = "? (yoklanmadi)"
            iid = self.tv_una.insert("", "end", values=(
                r["title"] or "(baslik yok)",
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
            f"{len(tum)} video YouTube'da erisilemiyor gorunuyor  -  {n_var} tanesinin yerel kopyasi var  -  "
            f"{len(guclu)} tanesinin ayni videosu baska yerde var (~{mb_guclu:.0f} MB bosa yer)  -  "
            f"YouTube kontrolu: {n_canli} yayinda, {n_kalkmis} kalkmis, {n_yok} yoklanmadi")

    def on_una_youtube_kontrol(self):
        """Secili (yoksa tum) 'Yayindan Kalkan' videolarin YouTube'da gercekten yayinda olup olmadigini yoklar.
        Yalniz bilgi: video/dosya/durum degismez, sonuc youtube_durum'a yazilir."""
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("YT-Arsiv", "Baska bir is devam ediyor.")
            return
        secili = [self._una_index[i] for i in self.tv_una.selection() if i in self._una_index]
        ids = sorted({r["video_id"] for r in secili}) if secili else None
        n = len(ids) if ids is not None else len({r["video_id"] for r in self._una_index.values()})
        if not n:
            messagebox.showinfo("YT-Arsiv", "Listede video yok.")
            return
        kapsam = "secili" if ids is not None else "TUM"
        if not messagebox.askyesno(
                "YouTube'da kontrol",
                f"{kapsam} {n} video YouTube'da yoklanacak (video basina ~2-4 sn; yt-dlp, indirme yok).\n"
                "Yalniz bilgi alinir: videolara ve dosyalara dokunulmaz.\n\nBaslansin mi?"):
            return
        self.cancel_flag.clear()
        self.cancel_btn.configure(state="normal")
        self.progress.start(80)
        self.status_var.set("YouTube'da yayinda mi kontrol ediliyor...")

        def worker():
            conn = core.init_db()
            try:
                self._enqueue_log(f"\n=== Yayindan kalkanlar: YouTube kontrolu ({n} video) ===")
                s = core.yayindan_kalkanlari_yokla(conn, video_idleri=ids, log=self._enqueue_log,
                                                   should_cancel=self.cancel_flag.is_set)
                self._enqueue_log(
                    f"\nYayinda: {s['yayinda']}   kalkmis: {s['kalkmis']}   dogrulanamadi: {s['belirsiz']}"
                    + ("   [iptal edildi]" if s["iptal"] else "") + "\n")
                if s["geri_gelen"]:
                    self._enqueue_log("YouTube'da tekrar YAYINDA olan videolar:")
                    for vid, baslik in s["geri_gelen"]:
                        self._enqueue_log(f"   - {(baslik or '?')[:70]} [{vid}]")
            except Exception as e:
                self._enqueue_log(f"\n[!] YouTube kontrol hatasi: {e}")
            finally:
                conn.close()
                self.root.after(0, self._on_una_kontrol_done)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_una_kontrol_done(self):
        self.progress.stop()
        self.cancel_btn.configure(state="disabled")
        self.status_var.set("YouTube kontrolu bitti.")
        self._refresh_unavailable()

    def _selected_una_rows(self) -> list:
        sel = [i for i in self.tv_una.selection() if i in self._una_index]
        if not sel:
            messagebox.showinfo("YT-Arsiv", "Once bir video sec.")
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
            messagebox.showinfo("YT-Arsiv", "Bu videonun yerel dosyasi yok.")
            return
        try:
            os.startfile(str(p))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Acilamadi: {e}")

    def on_una_open_twin(self):
        r = self._selected_una_row()
        if not r:
            return
        if not r["es"]:
            messagebox.showinfo("YT-Arsiv", "Bu video icin ayni video bulunamadi (tek kopya).")
            return
        try:
            os.startfile(r["es"]["dosya"])
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Acilamadi: {e}")

    def on_una_open_folder(self):
        r = self._selected_una_row()
        if not r:
            return
        p = self._una_local_path(r)
        if not p:
            messagebox.showinfo("YT-Arsiv", "Bu videonun yerel dosyasi yok.")
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
        if not rows:
            return
        secili = {(r["video_id"], r["playlist_id"]) for r in rows}
        cift = [r for r in rows if r["es"] and (r["es"]["video_id"], r["es"]["playlist_id"]) in secili]
        if cift:
            messagebox.showwarning(
                "YT-Arsiv",
                f"{len(cift)} videonun ayni videosu da secili. Ayni videonun iki kopyasini birden silme; "
                f"birini secimden cikar.")
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
                etiket = f"ayni video var: {r['es']['alias']} [{r['es']['klasor']}] ({self.ES_ETIKET[r['es']['guc']]})"
            return f"  - {r['title'] or r['video_id']}  [{etiket}]"

        uyarilar = ""
        if tek:
            uyarilar += (f"\nUYARI: {len(tek)} videonun baska kopyasi YOK. YouTube'dan da kalktigi icin "
                         f"silinirse bir daha indirilemez!\n")
        if olasi:
            uyarilar += (f"\nDIKKAT: {len(olasi)} videonun eslesmesi yalnizca 'olasi'; farkli video olabilir. "
                         f"'Ayni videonun dosyasini ac' ile karsilastir.\n")
        onizleme = "\n".join(satir(r) for r in rows[:14]) + (f"\n  ... ve {len(rows) - 14} tane daha" if len(rows) > 14 else "")
        if not messagebox.askyesno(
                "Sec.ilenleri diskten sil + DB'den kaldir",
                f"{len(rows)} video DB'den kaldirilacak, yerel dosyalari (yan dosyalariyla) diskten silinecek "
                f"(~{mb:.0f} MB).\nBu playlist'ler bu videolari bir daha almayacak.\n{uyarilar}\n{onizleme}\n\nDevam edilsin mi?",
                icon="warning", default="no"):
            return
        n_ok = n_fail = 0
        for r in rows:
            ok, msg = core.kopya_kaldir(self.conn, r["video_id"], r["playlist_id"])
            if ok:
                n_ok += 1
            else:
                n_fail += 1
                self.log(f"  [!] Kaldirilamadi: {r['title'] or r['video_id']} ({msg})")
        self.log(f"[YAYINDAN KALKAN SIL] {n_ok} video kaldirildi (~{mb:.0f} MB)"
                 + (f", {n_fail} kaldirilamadi" if n_fail else ""))
        self._refresh_all()

    def _build_tab_duplicates(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="Ayni Videolar")

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
        ttk.Checkbutton(row1, text="Liste disi / yayindan kalkanlari da goster",
                        variable=self.dup_include_inactive,
                        command=self._refresh_duplicates).pack(side="right", padx=4)

        # 2. SATIR: aksiyon butonlari
        row2 = ttk.Frame(f)
        row2.pack(side="top", fill="x", padx=4, pady=(0, 4))
        ttk.Button(row2, text="Yenile", command=self._refresh_duplicates).pack(side="left", padx=2)
        ttk.Button(row2, text="YouTube'da ac",   command=self.on_dup_open_video).pack(side="left", padx=2)
        ttk.Button(row2, text="Sec.dosyayi ac",  command=self.on_dup_open_file).pack(side="left", padx=2)
        ttk.Button(row2, text="Ses analizi yap", command=self.on_dup_ses_analizi).pack(side="left", padx=2)
        ttk.Button(row2, text="Tum kutuphanede ses analizi",
                   command=self.on_dup_ses_kutuphane).pack(side="left", padx=2)
        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(row2, text="Sec.ilenleri diskten sil + DB'den kaldir",
                   command=self.on_dup_delete_selected_files).pack(side="left", padx=2)

        cols = ("title", "playlist", "pos", "file", "video_id", "ses")
        headers = ("Baslik", "Playlist", "Sira", "Dosya", "Video ID", "Ses")
        widths = (380, 150, 50, 70, 110, 95)

        # Ctrl/Shift ile coklu secim
        self.tv_dup = ttk.Treeview(f, columns=cols, show="tree headings", selectmode="extended")
        self.tv_dup.heading("#0", text="Grup")
        self.tv_dup.column("#0", width=320, anchor="w")
        for c, h, w in zip(cols, headers, widths):
            self.tv_dup.heading(c, text=h)
            self.tv_dup.column(c, width=w, anchor="w" if c in ("title", "playlist") else "center")

        self.tv_dup.tag_configure("group_exact", background="#ffe6e6")
        self.tv_dup.tag_configure("group_fuzzy", background="#fff8d6")
        self.tv_dup.tag_configure("has_file",    foreground="#0a7d18")
        self.tv_dup.tag_configure("no_file",     foreground="#b00020")

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_dup.yview)
        self.tv_dup.configure(yscrollcommand=vsb.set)
        self.tv_dup.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tv_dup.bind("<Double-1>", lambda e: self.on_dup_open_file())
        self.tv_dup.bind("<Control-c>", lambda e: (self.on_dup_copy_title(), "break"))
        self.tv_dup.bind("<Control-C>", lambda e: (self.on_dup_copy_title(), "break"))

        # Sag tik menusu
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Basligi kopyala (Ctrl+C)",     command=self.on_dup_copy_title)
        menu.add_command(label="Tum grup basliklarini kopyala", command=self.on_dup_copy_group_titles)
        menu.add_command(label="Video URL'sini kopyala",        command=self.on_dup_copy_url)
        menu.add_command(label="Video ID'sini kopyala",         command=self.on_dup_copy_videoid)
        menu.add_separator()
        menu.add_command(label="YouTube'da ac",  command=self.on_dup_open_video)
        menu.add_command(label="Dosyayi ac",     command=self.on_dup_open_file)
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

        top = ttk.Frame(f)
        top.pack(side="top", fill="x", padx=4, pady=4)
        ttk.Label(top, text="Ara (link / video ID / baslik / uploader):").pack(side="left")
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
        headers = ("Baslik", "Playlist / Koleksiyon", "Durum", "Dosya", "Sira", "Uploader", "Video ID")
        widths = (320, 170, 130, 70, 50, 130, 110)
        self.tv_search = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, headers, widths):
            self.tv_search.heading(c, text=h)
            self.tv_search.column(c, width=w,
                                  anchor="w" if c in ("title", "playlist", "uploader") else "center")
        self.tv_search.tag_configure("has_file", foreground="#0a7d18")
        self.tv_search.tag_configure("no_file",  foreground="#b00020")
        self.tv_search.tag_configure("inactive", foreground="#888888")

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_search.yview)
        self.tv_search.configure(yscrollcommand=vsb.set)
        self.tv_search.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tv_search.bind("<Double-1>", lambda e: self.on_search_open_file())

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Basligi kopyala",      command=self.on_search_copy_title)
        menu.add_command(label="URL'sini kopyala",     command=self.on_search_copy_url)
        menu.add_command(label="Video ID'sini kopyala", command=self.on_search_copy_videoid)
        menu.add_command(label="Dosya yolunu kopyala", command=self.on_search_copy_filepath)
        menu.add_separator()
        menu.add_command(label="YouTube'da ac", command=self.on_search_open_yt)
        menu.add_command(label="Dosyayi ac",    command=self.on_search_open_file)
        menu.add_command(label="Klasoru ac",    command=self.on_search_open_folder)
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
        # Arama sekmesini bul ve sec
        for i, tab_id in enumerate(self.nb.tabs()):
            if self.nb.tab(tab_id, "text") == "Arama":
                self.nb.select(i)
                break
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
                r.get("title") or "(baslik yok)",
                plist,
                STATUS_TR.get(r.get("status"), r.get("status") or ""),
                "VAR" if has_file else ("KAYIP" if r.get("file_path") else "YOK"),
                r.get("playlist_index") or "",
                r.get("uploader") or "",
                r.get("video_id"),
            ), tags=(tag,))
            self._search_index[iid] = r

        self.search_count_var.set(f"{len(rows)} sonuc")

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
            messagebox.showerror("YT-Arsiv", f"Acilamadi: {e}")

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
            self._copy_to_clipboard(r["title"], "baslik")

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
        self.nb.add(f, text="Gecmis")

        top = ttk.Frame(f)
        top.pack(side="top", fill="x", padx=4, pady=4)
        ttk.Label(top, text="Limit:").pack(side="left")
        self.history_limit = tk.IntVar(value=200)
        ttk.Spinbox(top, from_=50, to=5000, increment=50, textvariable=self.history_limit, width=8,
                    command=self._refresh_history).pack(side="left", padx=4)
        ttk.Button(top, text="Yenile", command=self._refresh_history).pack(side="left")

        cols = ("ts", "type", "video_id", "playlist", "details")
        headers = ("Zaman", "Olay", "Video ID", "Playlist", "Detay")
        widths = (140, 180, 100, 140, 500)

        self.tv_hist = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, headers, widths):
            self.tv_hist.heading(c, text=h)
            self.tv_hist.column(c, width=w, anchor="w" if c in ("details", "playlist") else "center")

        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tv_hist.yview)
        self.tv_hist.configure(yscrollcommand=vsb.set)
        self.tv_hist.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def _build_log_panel(self):
        # Paned window'a alt bolum olarak ekle; weight=2 -> log uste 2 kat alan kapla
        pane = ttk.LabelFrame(self.paned, text="Log", padding=4)
        self.paned.add(pane, weight=2)
        self.log_txt = tk.Text(pane, height=10, wrap="word", font=("Consolas", 9))
        vsb = ttk.Scrollbar(pane, orient="vertical", command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=vsb.set, state="disabled")
        self.log_txt.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    # ---------------- Refresh ----------------

    def _refresh_all(self):
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
        ttk.Button(top, text="Klasoru ac",
                   command=lambda p=pid: self._open_playlist_subdir(p, None)).pack(side="right", padx=2)
        ttk.Button(top, text="Yenile",
                   command=lambda p=pid: self._populate_manual_tab(p)).pack(side="right", padx=2)
        ttk.Button(top, text="Dosyayi ac",
                   command=lambda p=pid: self._open_manual_selected(p)).pack(side="right", padx=2)
        ttk.Button(top, text="YouTube'da ac",
                   command=lambda p=pid: self._open_manual_video_yt(p)).pack(side="right", padx=2)

        cols = ("idx", "title", "uploader", "file", "video_id")
        headers = ("Sira", "Baslik", "Uploader", "Dosya", "Video ID")
        widths = (50, 420, 150, 70, 110)
        tv = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, headers, widths):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="w" if c in ("title", "uploader") else "center")
        tv.tag_configure("has_file", foreground="#0a7d18")
        tv.tag_configure("no_file",  foreground="#b00020")

        vsb = ttk.Scrollbar(f, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        tv.bind("<Double-1>", lambda e, p=pid: self._open_manual_selected(p))

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
            messagebox.showinfo("YT-Arsiv", "Bir video sec.")
            return
        p = core.resolve_video_path(r.get("file_path"))
        if not p or not p.exists():
            messagebox.showinfo("YT-Arsiv", "Dosya bulunamadi.")
            return
        try:
            os.startfile(str(p))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Acilamadi: {e}")

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
                self._enqueue_log("\n=== Manuel klasor taramasi ===")
                summary = core.manuel_tarama(conn, log=self._enqueue_log)
                bil = summary.get("bilinmeyen") or {}
                self._enqueue_log(
                    f"\nKoleksiyonlar: {summary['collections']}   "
                    f"Yeni dosya: {summary['new_files']}   "
                    f"Metadata eslesti: {summary['matched_from_db']}   "
                    f"Silinenden aktarilan: {summary['from_removed']}\n"
                    f"Silinen koleksiyon: {summary.get('deleted_collections', 0)}   "
                    f"Silinen video: {summary.get('deleted_videos', 0)}\n"
                    f"_Bilinmeyen'e tasinan: {bil.get('tasinan_video', 0)} video + {bil.get('tasinan_yan', 0)} yan dosya\n"
                )
            except Exception as e:
                self._enqueue_log(f"\n[!] Manuel tarama hatasi: {e}")
            finally:
                conn.close()
                self.root.after(0, self._refresh_all)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _refresh_playlists(self):
        sel = self.tv_pl.selection()
        sel_id = self.tv_pl.item(sel[0], "values")[0] if sel else None
        self.tv_pl.delete(*self.tv_pl.get_children())
        for r in core.list_playlists(self.conn):
            self.tv_pl.insert("", "end", values=(
                r["id"],
                r["alias"] or "",
                r["title"] or "",
                r["active"],
                r["downloaded"],
                r["unavailable"],
                r["removed"],
                (r["last_checked"] or "")[:19],
            ))
        if sel_id:
            for iid in self.tv_pl.get_children():
                if self.tv_pl.item(iid, "values")[0] == sel_id:
                    self.tv_pl.selection_set(iid)
                    break

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
            yt_metin = {"yayinda": "yayinda", "kalkmis": "KALKMIS (YouTube'da da yok)"}.get(yt_durum, "?")
            if yt_durum == "kalkmis":
                tag = "yt_gone"
            iid = self.tv_lost.insert("", "end", values=(
                STATUS_TR.get(r["status"], r["status"]),
                r["title"] or "(baslik yok)",
                r["uploader"] or "?",
                plist,
                file_state,
                yt_metin,
                (r["last_seen"] or "")[:19],
                r["video_id"],
            ), tags=(tag,))
            # Yeni yapida tek video_id birden cok kayda denk gelebilir; sakla
            self._lost_index[iid] = r
        n_recoverable = sum(
            1 for r in rows
            if (lambda p: p and p.exists())(core.resolve_video_path(r["file_path"]))
        )
        filter_note = " (sadece dosyasi olmayanlar)" if only_missing else ""
        self.lost_count_var.set(
            f"Listeden cikardigin {len(rows)} video{filter_note}  -  {n_recoverable} tanesinin yerel kopyasi var"
        )

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
            f"{len(exact)} kesin grup ({n_exact_videos} kayit)   |   "
            f"{len(fuzzy)} benzer baslik grubu ({n_fuzzy_videos} kayit)   |   "
            f"{len(disk_dupes)} disk tekrari ({n_disk_files} dosya)   |   "
            f"{len(disk_orphans)} DB disi grup ({n_orphan_files} dosya)"
        )

        # DISK-LEVEL TEKRARLAR (DB'den bagimsiz, ayni vid'in birden cok dosyasi)
        if disk_dupes:
            self.tv_dup.tag_configure("group_disk", background="#e6f0ff")
            for video_id, info in disk_dupes.items():
                parent = self.tv_dup.insert(
                    "", "end",
                    text=f"[DISKTE] {info['title'][:70]}  ({len(info['paths'])} dosya)",
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
                    text=f"[DB DISI] {info['title'][:70]}  ({len(info['paths'])} dosya)",
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
                base_title = items[0]["title"] or "(baslik yok)"
                parent = self.tv_dup.insert(
                    "", "end",
                    text=f"[KESIN] {base_title[:70]}  ({len(items)} playliste)",
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
                    text=f"[BENZER] '{norm[:60]}'  ({len(items)} kayit, {uniq_n} farkli video)",
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
            messagebox.showinfo("YT-Arsiv", "Baska bir is devam ediyor.")
            return
        include_inactive = self.dup_include_inactive.get()
        self.cancel_flag.clear()
        self.cancel_btn.configure(state="normal")
        self.progress.start(80)
        self.status_var.set("Ses analizi...")

        def worker():
            conn = core.init_db()
            try:
                self._enqueue_log("\n=== Ses analizi (Ayni Videolar gruplari) ===")
                s = core.ses_analizi(conn, log=self._enqueue_log, should_cancel=self.cancel_flag.is_set,
                                     include_inactive=include_inactive)
                self._enqueue_log(
                    f"\nGrup: {s['grup']}   Cift: {s['cift']}   (yeni hesaplanan: {s['yeni_cift']}, yeni iz: {s['yeni_iz']})\n"
                    f"AYNI kayit: {s['ayni']}   benzer surum: {s['benzer']}   farkli: {s['farkli']}   hata: {s['hata']}"
                    + ("   [iptal edildi]" if s["iptal"] else "") + "\n")
            except Exception as e:
                self._enqueue_log(f"\n[!] Ses analizi hatasi: {e}")
            finally:
                conn.close()
                self.root.after(0, self._on_ses_done)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def on_dup_ses_kutuphane(self):
        """TUM kutuphanedeki dosyalarin ses izini cikarir, ayni kayitlari basligi farkli olsa bile bulur (dosyaya dokunmaz)."""
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("YT-Arsiv", "Baska bir is devam ediyor.")
            return
        if not messagebox.askyesno(
                "Tum kutuphanede ses analizi",
                "Kutuphanedeki tum video dosyalarinin ses izi cikarilacak (dosya basina ~1 sn, ilk seferde yaklasik "
                "20-30 dk surebilir). Cikarilan izler kaydedilir; iptal edersen kaldigi yerden devam eder.\n\n"
                "Bulunan ayni kayitlar 'Ayni Videolar' listesine eklenir. Dosyalara dokunulmaz.\n\nBaslansin mi?"):
            return
        include_inactive = self.dup_include_inactive.get()
        self.cancel_flag.clear()
        self.cancel_btn.configure(state="normal")
        self.progress.start(80)
        self.status_var.set("Tum kutuphanede ses analizi...")

        def worker():
            conn = core.init_db()
            try:
                self._enqueue_log("\n=== Tum kutuphanede ses analizi ===")
                s = core.ses_kutuphane_analizi(conn, log=self._enqueue_log, should_cancel=self.cancel_flag.is_set,
                                               include_inactive=include_inactive)
                self._enqueue_log(
                    f"\nDosya: {s['dosya']}   yeni iz: {s['yeni_iz']}   iz hatasi: {s['hata']}\n"
                    f"Aday cift: {s['aday']}   yeni karsilastirilan: {s['yeni_cift']}   "
                    f"AYNI kayit: {s['ayni']}   benzer: {s['benzer']}   farkli: {s['farkli']}"
                    + ("   [iptal edildi]" if s["iptal"] else "") + "\n")
            except Exception as e:
                self._enqueue_log(f"\n[!] Kutuphane ses analizi hatasi: {e}")
            finally:
                conn.close()
                self.root.after(0, self._on_ses_done)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_ses_done(self):
        self.progress.stop()
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
            messagebox.showinfo("YT-Arsiv", "Bir kayit sec (grup basligi degil).")
            return
        webbrowser.open(f"https://youtu.be/{r['video_id']}")

    def on_dup_open_file(self):
        r = self._selected_dup_video()
        if not r:
            messagebox.showinfo("YT-Arsiv", "Bir kayit sec (grup basligi degil).")
            return
        p = core.resolve_video_path(r.get("file_path"))
        if not p or not p.exists():
            messagebox.showinfo("YT-Arsiv", "Bu kaydin yerel dosyasi yok.")
            return
        try:
            os.startfile(str(p))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Acilamadi: {e}")

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
        self.status_var.set(f"Kopyalandi{(' ('+label+')') if label else ''}: {preview}")

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
            messagebox.showinfo("YT-Arsiv", "Once en az bir kayit sec (gerekirse Ctrl/Shift ile coklu).")
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
                files.append({"file": Path(abs_path), "label": f"[DISKTE] {vals[1]}/{vals[0]}"})
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
            messagebox.showinfo("YT-Arsiv", "Silinecek kayit bulunamadi (grup basligi disinda bir satir sec).")
            return

        n_var = sum(1 for r in rows if r["var"])
        preview = "\n".join(
            [f"  - {r['label']}  [{'dosya var' if r['var'] else 'dosya yok'}]" for r in rows[:15]]
            + [f"  - {t['label']}" for t in files[:max(0, 15 - len(rows))]])
        total = len(rows) + len(files)
        more = f"\n  ... ve {total - 15} tane daha" if total > 15 else ""
        ans = messagebox.askyesno(
            "Sec.ilenleri diskten sil + DB'den kaldir",
            f"{len(rows)} kayit DB'den kaldirilacak ({n_var} tanesinin dosyasi diskten de silinecek, "
            f"dosyasi olmayanlar yalniz kayit olarak kaldirilir).\n"
            f"{len(files)} DB disi dosya diskten silinecek.\n\n"
            f"Video hala o playlist'teyse sonraki taramada geri gelmez (playlist'in bu videoyu almasi engellenir).\n"
            f"Yan dosyalar (.info.json, .webp) da silinir.\n\nSilinecekler:\n{preview}{more}\n\nDevam edilsin mi?",
            icon="warning",
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
                self.log(f"  [!] Kaldirilamadi: {r['label']} ({msg})")
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
        self.log(f"[TOPLU SIL] {n_rows} kayit kaldirildi, {n_files} DB disi dosya silindi"
                 + (f", {n_fail} kayit kaldirilamadi" if n_fail else ""))
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
            self._copy_to_clipboard("\n".join(titles), f"{len(titles)} baslik")

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
                (r["timestamp"] or "")[:19],
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
        url = simpledialog.askstring(
            "Playlist / Kanal Ekle",
            "YouTube playlist veya kanal URL'i:\n(ornek: .../playlist?list=...  ya da  https://www.youtube.com/@kanaladi)",
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
                self.log(f"Kanal video sayisi okunamadi: {e}")
            finally:
                self.root.config(cursor="")
            if n == 0:
                messagebox.showwarning("YT-Arsiv", "Bu kanalda video bulunamadi (adres yanlis ya da kanal bos olabilir).")
                return
            sayi = f"{n} video" if n else "tum videolar"
            if not messagebox.askyesno(
                    "Kanal Ekle",
                    f"Kanal: {url}\n\nBu kanalda {sayi} var; tarama bunlarin HEPSINI indirir (disk + ag).\n"
                    "Yeni yuklenenler sonraki taramalarda otomatik eklenir.\n\nEklensin mi?"):
                return
        alias = simpledialog.askstring(
            "Takma Ad", ("Bu kanal" if kanal else "Bu playlist") + " icin takma ad (klasor adi olarak kullanilir; bos birakilabilir):",
            parent=self.root,
        )
        pid, err = core.add_playlist(self.conn, url, alias or "")
        if err:
            messagebox.showwarning("YT-Arsiv", err)
            return
        self.log(f"{'Kanal' if kanal else 'Playlist'} eklendi [id={pid}]: {url}")
        self._refresh_playlists()
        if messagebox.askyesno("YT-Arsiv", "Simdi taransin (videolar indirilsin) mi?"):
            self._start_sync(pid)

    def on_delete_playlist(self):
        pid = self._selected_playlist_id()
        if pid is None:
            return
        delete_files = messagebox.askyesnocancel(
            "Playlisti Sil",
            f"Playlist [{pid}] silinecek.\n\n"
            "EVET: dosyalar da silinsin\n"
            "HAYIR: sadece kayitlari sil, dosyalari birak\n"
            "IPTAL: vazgec",
        )
        if delete_files is None:
            return
        core.delete_playlist(self.conn, pid, delete_files=bool(delete_files))
        self.log(f"Playlist [{pid}] silindi (dosyalar={'evet' if delete_files else 'hayir'})")
        self._refresh_all()

    def on_sync_selected(self):
        pid = self._selected_playlist_id()
        if pid is None:
            messagebox.showinfo("YT-Arsiv", "Once bir playlist sec.")
            return
        self._start_sync(pid)

    def on_sync_all(self):
        if not core.list_playlists(self.conn):
            messagebox.showinfo("YT-Arsiv", "Once 'Playlist Ekle' ile ekle.")
            return
        self._start_sync(None)

    def on_cancel(self):
        self.cancel_flag.set()
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
            messagebox.showinfo("YT-Arsiv", "Once bir video sec.")
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
                self._enqueue_log("\n=== Yerel dosya taramasi (kayiplari yeniden bagla) ===")
                s = core.tum_klasorleri_tara(conn, log=self._enqueue_log)
                bil = s.get("bilinmeyen") or {}
                self._enqueue_log(
                    f"\nTaranan dosya: {s['scanned_files']}   "
                    f"Yeniden baglandi: {s['relinked']}   "
                    f"Status duzeltildi: {s.get('status_reset', 0)}   "
                    f"Title duzeltildi: {s.get('title_fixed', 0)}   "
                    f"Hala kayip: {s['no_match']}   "
                    f"Klasor orphan: {s.get('folder_orphans', 0)}   "
                    f"Cift tasinan: {s.get('folder_duplicates', 0)}\n"
                    f"_Bilinmeyen'e tasinan: {bil.get('tasinan_video', 0)} video + {bil.get('tasinan_yan', 0)} yan dosya\n"
                )
                bilinmeyen_not = ""
                if bil.get("tasinan_video") or bil.get("tasinan_yan"):
                    bilinmeyen_not = (f"\n\nVeritabaninda bagi olmayan {bil.get('tasinan_video', 0)} video + "
                                      f"{bil.get('tasinan_yan', 0)} yan dosya _Bilinmeyen klasorune tasindi.")
                if bil.get("durdu"):
                    bilinmeyen_not += f"\n\n[!] _Bilinmeyen tasimasi DURDU: {bil['durdu']}"
                if s["relinked"]:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "YT-Arsiv",
                        f"{s['relinked']} dosya yeniden baglandi ve kayiptan cikti." + bilinmeyen_not
                    ))
                else:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "YT-Arsiv",
                        "Hicbir yeni eslesme bulunamadi." + bilinmeyen_not
                    ))
            except Exception as e:
                self._enqueue_log(f"[!] Tarama hatasi: {e}")
            finally:
                conn.close()
                self.root.after(0, self._refresh_all)

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
            satirlar.append(f"  - {(r.get('title') or '(baslik yok)')[:60]}  [{r['video_id']}]  "
                            f"dosya: {'VAR' if p and p.exists() else 'yok'}")
        daha = f"\n  ... ve {len(rows) - 12} video daha" if len(rows) > 12 else ""
        dosyali = sum(1 for r in rows if (lambda p: p and p.exists())(core.resolve_video_path(r.get("file_path"))))
        return (f"{len(rows)} video, {len(hedefler)} DB kaydi  (yerel dosyasi olan: {dosyali})\n\n"
                + "\n".join(satirlar) + daha)

    def on_delete_lost_record_only(self):
        """Secili kayip videolarin DB kayitlarini siler, yerel dosyalara dokunmaz."""
        rows = self._selected_lost_rows()
        if not rows:
            messagebox.showinfo("YT-Arsiv", "Once bir veya daha fazla video sec.")
            return
        hedefler = self._lost_hedefleri(rows)
        if not messagebox.askyesno(
            "Sadece Kaydi Sil",
            "Secili videolarin DB kayitlari silinecek, yerel dosyalar KORUNACAK.\n\n"
            + self._lost_ozet_metni(rows, hedefler) + "\n\nOnayliyor musun?",
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
        self.log(f"[KAYIT SIL] {len(rows)} video: {silinen} DB kaydi silindi, dosyalar korundu"
                 + (f", {hata} hata" if hata else ""))
        self._refresh_all()

    def on_delete_lost_permanent(self):
        """Secili kayip videolari DB'den ve diskten siler (core.kopya_kaldir - TEK KAPI: baska kayitla paylasilan
        dosyaya dokunmaz).  Biri basarisiz olursa digerleri devam eder."""
        rows = self._selected_lost_rows()
        if not rows:
            messagebox.showinfo("YT-Arsiv", "Once bir veya daha fazla video sec.")
            return
        hedefler = self._lost_hedefleri(rows)
        if not messagebox.askyesno(
            "Tamamen Sil",
            "Secili videolar TUM kayitlariyla DB'den ve diskten (yan dosyalariyla) KALICI silinecek.\n"
            "Baska bir kayitla paylasilan dosyaya dokunulmaz.\n\n"
            + self._lost_ozet_metni(rows, hedefler) + "\n\nOnayliyor musun?",
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
        self.log(f"[KALICI SIL] {len(rows)} video: {silinen} kayit silindi" + (f", {hata} hata" if hata else ""))
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
            label = f"'{subdir}/' alt klasoru" if subdir else "Playlist klasoru"
            messagebox.showinfo("YT-Arsiv",
                f"{label} bulunamadi:\n{path}\n\n"
                f"({'Henuz silinen video tasinmamis olabilir.' if subdir else 'Henuz indirme yapilmamis.'})")
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
            messagebox.showinfo("YT-Arsiv", "Bu videonun yerel kopyasi yok.")
            return
        try:
            os.startfile(str(p))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Acilamadi: {e}")

    def on_open_local_folder(self):
        r = self._selected_lost_row()
        if not r:
            return
        p = core.resolve_video_path(r.get("file_path"))
        if not p:
            messagebox.showinfo("YT-Arsiv", "Bu videonun yerel kopyasi yok.")
            return
        if p.exists():
            subprocess.run(["explorer", "/select,", str(p)])
        elif p.parent.exists():
            self._open_in_explorer(p.parent)
        else:
            messagebox.showinfo("YT-Arsiv", "Klasor bulunamadi.")

    def _open_in_explorer(self, path: Path):
        try:
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror("YT-Arsiv", f"Acilamadi: {e}")

    # ---------------- Sync worker ----------------

    def _start_sync(self, playlist_id: int | None):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("YT-Arsiv", "Tarama zaten devam ediyor.")
            return

        self.cancel_flag.clear()
        self.cancel_btn.configure(state="normal")
        self.progress.start(80)
        self.status_var.set("Taraniyor...")

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
                    f"\n=== OZET ===\n"
                    f"Yerel baglanan          : {((summary.get('local') or {}).get('baglama') or {}).get('relinked', 0)}\n"
                    f"Taranan playlist        : {summary['playlists']}\n"
                    f"Mevcut dosya kullanildi : {summary.get('linked_existing', 0)}\n"
                    f"Yeni indirilen          : {summary['new']}\n"
                    f"Listeden cikarilan (sen): {summary['removed']}\n"
                    f"Yayindan kalkan (YouTube): {summary['unavailable']}\n"
                    f"_YayındanKalkan'a tasinan: {summary.get('yayindan_kalkan_tasinan', 0)}\n"
                    f"Sabit bagla birlesen     : {summary.get('kopya_baglanan', 0)} dosya ({summary.get('kopya_kazanilan_mb', 0):.0f} MB)\n"
                    f"Farkli kopya (kayitli)   : {summary.get('kopya_farkli', 0)}\n"
                    f"Hata                    : {summary['errors']}\n"
                )
            except Exception as e:
                self._enqueue_log(f"\n[!] Beklenmeyen hata: {e}")
            finally:
                conn.close()
                self.root.after(0, self._on_sync_done)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_sync_done(self):
        self.progress.stop()
        self.cancel_btn.configure(state="disabled")
        self.status_var.set("Tarama bitti.")
        self._refresh_all()
        self.root.after(300, lambda: self._silinme_sor(otomatik=True))

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
        win.title("Silinme onaylari")
        win.geometry("980x540")
        win.transient(self.root)
        ttk.Label(
            win, justify="left", wraplength=940,
            text=("Asagidaki videolar YouTube listesinde GORUNMUYOR, ama liste bir anda cok kuculdugu icin (yanlis alarm korumasi) "
                  "otomatik 'silindi' sayilmadi ve baska listede kopyalari da yok.\n"
                  "Secililer icin:  'Listeden cikmis say' = sen cikardin (dosyasi _ListeDisi'ne gider)  |  "
                  "'Koru' = silinmedi, kalsin (video listeye donene kadar bir daha sorulmaz)."),
        ).pack(fill="x", padx=10, pady=8)
        cols = ("playlist", "title", "uploader", "file", "video_id")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10)
        tv = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c, ad, w in (("playlist", "Playlist", 110), ("title", "Baslik", 480), ("uploader", "Yukleyen", 140),
                         ("file", "Dosya", 60), ("video_id", "Video ID", 110)):
            tv.heading(c, text=ad)
            tv.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for b in bek:
            tv.insert("", "end", iid=f"{b['playlist_id']}|{b['video_id']}", values=(
                b["playlist_alias"], b["title"] or "(baslik yok)", b["uploader"] or "?",
                "VAR" if b["dosya_var"] else "YOK", b["video_id"]))
        tv.selection_set(tv.get_children())
        durum = tk.StringVar(value=f"{len(bek)} video onay bekliyor.")
        ttk.Label(win, textvariable=durum).pack(anchor="w", padx=10, pady=4)

        def secilenler():
            return [(int(i.split("|", 1)[0]), i.split("|", 1)[1]) for i in tv.selection()]

        def uygula(islem, mesaj):
            anahtarlar = secilenler()
            if not anahtarlar:
                messagebox.showinfo("YT-Arsiv", "Once video sec.", parent=win)
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
            uygula(lambda a: core.silinmeyi_onayla(self.conn, a, log=self.log), "Listeden cikmis sayildi")

        def koru():
            uygula(lambda a: core.silinmeyi_koru(self.conn, a), "Korundu (silinmedi)")

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=8)
        ttk.Button(bar, text="Listeden cikmis say", command=onayla).pack(side="left", padx=2)
        ttk.Button(bar, text="Koru (silinmedi)", command=koru).pack(side="left", padx=2)
        ttk.Button(bar, text="Tumunu sec", command=lambda: tv.selection_set(tv.get_children())).pack(side="left", padx=12)
        ttk.Button(bar, text="Sonra sor", command=win.destroy).pack(side="right", padx=2)

    # ---------------- Log ----------------

    def _enqueue_log(self, msg: str):
        self.log_queue.put(str(msg))

    def log(self, msg: str):
        self._enqueue_log(msg)

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_txt.configure(state="normal")
                self.log_txt.insert("end", msg + "\n")
                self.log_txt.see("end")
                self.log_txt.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_log_queue)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
