#!/usr/bin/env python3
"""Small cross-platform desktop UI for selected Bitbucket Cloud PR actions."""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from bitbucket_pr_batch import BitbucketAPI, BitbucketError, collect_refs, process_pr


COLORS = {
    "background": "#F4F6FA",
    "surface": "#FFFFFF",
    "field": "#F8FAFD",
    "border": "#DFE5EE",
    "text": "#172033",
    "muted": "#66758B",
    "accent": "#2463EB",
    "accent_hover": "#174FC9",
    "accent_soft": "#EAF1FF",
    "success": "#16824A",
    "danger": "#C33642",
}


def action_label(approve, merge, all_approved=False):
    if approve and merge:
        return "Merge" if all_approved else "Approve and merge"
    if merge:
        return "Merge"
    if approve:
        return "Approve"
    return "Выбери действие"


def system_font(root):
    families = set(tkfont.families(root))
    if sys.platform == "darwin":
        preferred = ("SF Pro Text", "Helvetica Neue", "Arial")
    elif sys.platform == "win32":
        preferred = ("Segoe UI", "Arial", "Helvetica")
    else:
        preferred = ("Inter", "Noto Sans", "DejaVu Sans")
    return next((name for name in preferred if name in families), "TkDefaultFont")


def rounded_rect(canvas, x0, y0, x1, y1, radius, color, tag="shape"):
    """Draw a filled rounded rectangle using Tk primitives on every platform."""
    if x1 <= x0 or y1 <= y0:
        return
    radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
    corners = (
        (x0, y0, 90),
        (x1 - 2 * radius, y0, 0),
        (x1 - 2 * radius, y1 - 2 * radius, 270),
        (x0, y1 - 2 * radius, 180),
    )
    for left, top, start in corners:
        canvas.create_arc(left, top, left + 2 * radius, top + 2 * radius,
                          start=start, extent=90, style="pieslice",
                          fill=color, outline=color, tags=tag)
    canvas.create_rectangle(x0 + radius, y0, x1 - radius, y1,
                            fill=color, outline=color, tags=tag)
    canvas.create_rectangle(x0, y0 + radius, x1, y1 - radius,
                            fill=color, outline=color, tags=tag)


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command, font, kind="secondary"):
        self.label = text
        self.command = command
        self.kind = kind
        self.enabled = True
        self.hovered = False
        self.text_font = tkfont.Font(font=font)
        self.pad_x = 16 if kind != "ghost" else 9
        self.button_height = 42 if kind == "accent" else 38
        super().__init__(parent, height=self.button_height, bg=COLORS["surface"],
                         highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self.set_label(text)
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", self.on_click)
        self.bind("<Return>", self.on_click)
        self.bind("<space>", self.on_click)

    def set_label(self, text):
        self.label = text
        self.configure(width=self.text_font.measure(text) + 2 * self.pad_x)
        self.draw()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self.draw()

    def on_enter(self, _event):
        self.hovered = True
        self.draw()

    def on_leave(self, _event):
        self.hovered = False
        self.draw()

    def on_click(self, _event):
        if self.enabled:
            self.focus_set()
            self.command()

    def draw(self):
        self.delete("all")
        if self.kind == "accent":
            fill = COLORS["accent_hover"] if self.hovered else COLORS["accent"]
            foreground = "#FFFFFF"
        elif self.kind == "ghost":
            fill = "#F1F5FB" if self.hovered else COLORS["surface"]
            foreground = COLORS["accent"] if self.hovered else COLORS["muted"]
        else:
            fill = "#DCE9FF" if self.hovered else COLORS["accent_soft"]
            foreground = COLORS["accent"]
        if not self.enabled:
            fill, foreground = "#EEF1F6", "#9AA7B8"
        width = int(self.cget("width"))
        height = int(self.cget("height"))
        rounded_rect(self, 1, 1, width - 1, height - 1, 9, fill)
        self.create_text(width / 2, height / 2, text=self.label,
                         fill=foreground, font=self.text_font)


class TickBox(tk.Frame):
    def __init__(self, parent, text, variable, font):
        super().__init__(parent, bg=COLORS["surface"], takefocus=1, cursor="hand2")
        self.variable = variable
        self.icon = tk.Canvas(self, width=20, height=20, bg=COLORS["surface"],
                              highlightthickness=0, bd=0, cursor="hand2")
        self.icon.pack(side="left", padx=(0, 7))
        self.text = tk.Label(self, text=text, bg=COLORS["surface"],
                             fg=COLORS["text"], font=font, cursor="hand2")
        self.text.pack(side="left")
        for widget in (self, self.icon, self.text):
            widget.bind("<Button-1>", self.toggle)
        self.bind("<Return>", self.toggle)
        self.bind("<space>", self.toggle)
        variable.trace_add("write", lambda *_: self.draw())
        self.draw()

    def toggle(self, _event):
        self.focus_set()
        self.variable.set(not self.variable.get())

    def draw(self):
        self.icon.delete("all")
        selected = self.variable.get()
        rounded_rect(self.icon, 1, 1, 19, 19, 5,
                     COLORS["accent"] if selected else COLORS["border"])
        if selected:
            rounded_rect(self.icon, 2, 2, 18, 18, 4, COLORS["accent"])
            self.icon.create_line(5, 10, 9, 14, 16, 6, fill="white",
                                  width=2.2, capstyle="round", joinstyle="round")
        else:
            rounded_rect(self.icon, 2, 2, 18, 18, 4, COLORS["surface"])


class PillScrollbar(tk.Canvas):
    def __init__(self, parent, command):
        super().__init__(parent, width=14, height=1, bg=COLORS["field"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.first = 0.0
        self.last = 1.0
        self.hovered = False
        self.drag_offset = 0.0
        self.thumb_top = 0.0
        self.thumb_height = 0.0
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<MouseWheel>", self.on_wheel)
        self.bind("<Button-4>", lambda _event: self.command("scroll", -1, "units"))
        self.bind("<Button-5>", lambda _event: self.command("scroll", 1, "units"))

    def set(self, first, last):
        self.first = max(0.0, min(1.0, float(first)))
        self.last = max(self.first, min(1.0, float(last)))
        self.draw()

    def draw(self):
        self.delete("all")
        height = self.winfo_height()
        if height <= 2:
            return
        rounded_rect(self, 5, 7, 9, height - 7, 2, "#E6ECF4")
        visible = self.last - self.first
        if visible >= 0.999:
            return
        span = max(1.0, height - 14)
        self.thumb_height = min(span, max(30.0, span * visible))
        available = max(0.0, span - self.thumb_height)
        self.thumb_top = 7 + available * self.first / max(0.001, 1 - visible)
        color = COLORS["accent"] if self.hovered else "#AAB8CC"
        rounded_rect(self, 3, self.thumb_top, 11,
                     self.thumb_top + self.thumb_height, 4, color)

    def on_enter(self, _event):
        self.hovered = True
        self.draw()

    def on_leave(self, _event):
        self.hovered = False
        self.draw()

    def on_press(self, event):
        if self.last - self.first >= 0.999:
            return
        if self.thumb_top <= event.y <= self.thumb_top + self.thumb_height:
            self.drag_offset = event.y - self.thumb_top
        else:
            self.drag_offset = self.thumb_height / 2
            self.on_drag(event)

    def on_drag(self, event):
        visible = self.last - self.first
        if visible >= 0.999:
            return
        available = max(1.0, self.winfo_height() - 14 - self.thumb_height)
        offset = max(0.0, min(available, event.y - self.drag_offset - 7))
        self.command("moveto", offset / available * (1 - visible))

    def on_wheel(self, event):
        direction = -1 if event.delta > 0 else 1
        self.command("scroll", direction, "units")


class PullRequestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bitbucket PR Manager")
        self.root.configure(bg=COLORS["background"])
        self.root.minsize(860, 780)
        self.root.geometry("980x820")
        self.events = queue.Queue()
        self.busy = False
        self._has_output = False
        self._token_visible = False
        self._preview_all_approved = False
        self.font_name = system_font(root)
        self.font = (self.font_name, 11)
        self.font_small = (self.font_name, 10)
        self.font_title = (self.font_name, 22, "bold")
        self.font_section = (self.font_name, 14, "bold")
        mono = "Consolas" if sys.platform == "win32" else (
            "Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono"
        )
        self.font_mono = (mono, 10)
        self.configure_styles()

        self.token = tk.StringVar(value=os.environ.get("BITBUCKET_API_TOKEN", ""))
        self.target = tk.StringVar(value="develop")
        self.approve = tk.BooleanVar(value=True)
        self.merge = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Готово к работе")
        self.link_count = tk.StringVar(value="0 PR")

        shell = tk.Frame(root, bg=COLORS["background"])
        shell.pack(fill="both", expand=True, padx=24, pady=16)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=3)
        shell.rowconfigure(4, weight=1)

        header = tk.Frame(shell, bg=COLORS["background"])
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)
        logo = tk.Canvas(header, width=48, height=48, bg=COLORS["background"], highlightthickness=0)
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 14))
        rounded_rect(logo, 0, 0, 48, 48, 10, COLORS["accent"])
        logo.create_text(24, 24, text="B", fill="white", font=(self.font_name, 24, "bold"))
        tk.Label(header, text="Pull request manager", bg=COLORS["background"],
                 fg=COLORS["text"], font=self.font_title).grid(row=0, column=1, sticky="w")
        tk.Label(header, text="Одобряй и объединяй выбранные PR без переключения между вкладками",
                 bg=COLORS["background"], fg=COLORS["muted"],
                 font=self.font_small).grid(row=1, column=1, sticky="w")
        tk.Label(header, text="BITBUCKET CLOUD", bg=COLORS["accent_soft"],
                 fg=COLORS["accent"], font=(self.font_name, 9, "bold"),
                 padx=12, pady=7).grid(row=0, column=2, rowspan=2, sticky="e")

        account = self.card(shell, 1, pady=(0, 10))
        account.columnconfigure(0, weight=1)
        self.section_header(account, "Подключение", "Токен остаётся только в памяти приложения", 0)
        credential_row = tk.Frame(account, bg=COLORS["surface"])
        credential_row.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        credential_row.columnconfigure(0, weight=1)
        self.token_entry = tk.Entry(
            credential_row, textvariable=self.token, show="*", font=self.font,
            bg=COLORS["field"], fg=COLORS["text"], insertbackground=COLORS["text"],
            relief="flat", highlightthickness=1, highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"], bd=0,
        )
        self.token_entry.grid(row=0, column=0, sticky="ew", ipady=9)
        self.show_button = RoundedButton(credential_row, "Показать", self.toggle_token,
                                         self.font_small, kind="ghost")
        self.show_button.grid(row=0, column=1, padx=(8, 0))
        self.test_button = RoundedButton(credential_row, "Проверить доступ",
                                         self.check_connection, self.font_small)
        self.test_button.grid(row=0, column=2, padx=(8, 0))
        RoundedButton(credential_row, "Настройка", self.show_guide,
                      self.font_small, kind="ghost").grid(row=0, column=3, padx=(8, 0))
        tk.Label(account, text="Вставь API token или используй BITBUCKET_API_TOKEN. Репозитории определяются по ссылкам.",
                 bg=COLORS["surface"], fg=COLORS["muted"],
                 font=self.font_small).grid(row=2, column=0, sticky="w", pady=(8, 0))

        links_card = self.card(shell, 2, pady=(0, 10))
        links_card.columnconfigure(0, weight=1)
        links_card.rowconfigure(1, weight=1)
        links_top = tk.Frame(links_card, bg=COLORS["surface"])
        links_top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        links_top.columnconfigure(0, weight=1)
        tk.Label(links_top, text="Ссылки на pull requests", bg=COLORS["surface"],
                 fg=COLORS["text"], font=self.font_section).grid(row=0, column=0, sticky="w")
        tk.Label(links_top, text="По одной ссылке в строке · дубликаты будут пропущены",
                 bg=COLORS["surface"], fg=COLORS["muted"],
                 font=self.font_small).grid(row=1, column=0, sticky="w", pady=(3, 0))
        tk.Label(links_top, textvariable=self.link_count, bg=COLORS["accent_soft"],
                 fg=COLORS["accent"], font=(self.font_name, 9, "bold"),
                 padx=10, pady=6).grid(row=0, column=1, rowspan=2, padx=(10, 10))
        self.load_button = RoundedButton(links_top, "Загрузить .txt",
                                         self.load_file, self.font_small)
        self.load_button.grid(row=0, column=2, rowspan=2)
        text_container = tk.Frame(links_card, bg=COLORS["border"], padx=1, pady=1)
        text_container.grid(row=1, column=0, sticky="nsew")
        text_container.columnconfigure(0, weight=1)
        text_container.rowconfigure(0, weight=1)
        self.links = tk.Text(
            text_container, wrap="none", undo=True, height=6, font=self.font_mono,
            bg=COLORS["field"], fg=COLORS["text"], insertbackground=COLORS["text"],
            selectbackground=COLORS["accent_soft"], relief="flat", bd=0, padx=14, pady=12,
        )
        self.links.grid(row=0, column=0, sticky="nsew")
        links_scroll = PillScrollbar(text_container, command=self.links.yview)
        links_scroll.grid(row=0, column=1, sticky="ns")
        self.links.configure(yscrollcommand=links_scroll.set)
        self.links.bind("<<Modified>>", self.update_link_count)

        actions = self.card(shell, 3, pady=(0, 10))
        actions.columnconfigure(0, weight=1)
        tk.Label(actions, text="Действия", bg=COLORS["surface"],
                 fg=COLORS["text"], font=self.font_section).grid(row=0, column=0, sticky="w")
        options = tk.Frame(actions, bg=COLORS["surface"])
        options.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        tk.Label(options, text="Целевая ветка", bg=COLORS["surface"],
                 fg=COLORS["muted"], font=self.font_small).pack(side="left", padx=(0, 10))
        tk.Entry(options, textvariable=self.target, width=15, font=self.font,
                 bg=COLORS["field"], fg=COLORS["text"], relief="flat",
                 highlightthickness=1, highlightbackground=COLORS["border"],
                 highlightcolor=COLORS["accent"], bd=0).pack(side="left", ipady=7, padx=(0, 18))
        TickBox(options, "Approve", self.approve, self.font).pack(side="left", padx=(0, 14))
        TickBox(options, "Merge", self.merge, self.font).pack(side="left")
        self.run_button = RoundedButton(options, "Approve", self.run,
                                        self.font, kind="accent")
        self.run_button.pack(side="right")
        self.preview_button = RoundedButton(options, "Предпросмотр",
                                            self.preview, self.font_small)
        self.preview_button.pack(side="right", padx=(0, 10))
        self.approve.trace_add("write", self.invalidate_preview)
        self.merge.trace_add("write", self.invalidate_preview)
        self.token.trace_add("write", self.invalidate_preview)
        self.target.trace_add("write", self.invalidate_preview)

        results = self.card(shell, 4)
        results.columnconfigure(0, weight=1)
        results.rowconfigure(1, weight=1)
        results_top = tk.Frame(results, bg=COLORS["surface"])
        results_top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        results_top.columnconfigure(0, weight=1)
        tk.Label(results_top, text="Результаты", bg=COLORS["surface"],
                 fg=COLORS["text"], font=self.font_section).grid(row=0, column=0, sticky="w")
        RoundedButton(results_top, "Очистить", self.clear_output,
                      self.font_small, kind="ghost").grid(row=0, column=1)
        output_container = tk.Frame(results, bg=COLORS["border"], padx=1, pady=1)
        output_container.grid(row=1, column=0, sticky="nsew")
        output_container.columnconfigure(0, weight=1)
        output_container.rowconfigure(0, weight=1)
        self.output = tk.Text(
            output_container, wrap="word", height=5, state="disabled", font=self.font_small,
            bg=COLORS["field"], fg=COLORS["text"], relief="flat", bd=0,
            padx=14, pady=12,
        )
        self.output.grid(row=0, column=0, sticky="nsew")
        output_scroll = PillScrollbar(output_container, command=self.output.yview)
        output_scroll.grid(row=0, column=1, sticky="ns")
        self.output.configure(yscrollcommand=output_scroll.set)
        for tag, color in (("ok", COLORS["success"]), ("fail", COLORS["danger"]),
                           ("meta", COLORS["muted"]), ("plan", COLORS["accent"])):
            self.output.tag_configure(tag, foreground=color)
        self.clear_output()

        footer = tk.Frame(shell, bg=COLORS["background"])
        footer.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        tk.Label(footer, textvariable=self.status, bg=COLORS["background"],
                 fg=COLORS["muted"], font=self.font_small).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=130,
                                        style="Accent.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=1, sticky="e")
        self.progress.grid_remove()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._after_id = self.root.after(100, self.drain_events)
        self.root.bind("<Destroy>", self.on_destroy, add="+")

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Accent.Horizontal.TProgressbar", background=COLORS["accent"],
                        troughcolor=COLORS["border"], borderwidth=0)

    def card(self, parent, row, *, pady=(0, 0)):
        outer = tk.Frame(parent, bg=COLORS["background"])
        outer.grid(row=row, column=0, sticky="nsew", pady=pady)
        backdrop = tk.Canvas(outer, bg=COLORS["background"],
                             highlightthickness=0, bd=0)
        backdrop.place(x=0, y=0, relwidth=1, relheight=1)
        content = tk.Frame(outer, bg=COLORS["surface"], padx=8, pady=2)
        content.pack(fill="both", expand=True, padx=12, pady=12)
        content.lift()

        def redraw(event):
            width, height = event.width, event.height
            backdrop.delete("all")
            rounded_rect(backdrop, 2, 3, width - 1, height - 1, 14, "#E8EDF5")
            rounded_rect(backdrop, 1, 1, width - 2, height - 3, 14, COLORS["border"])
            rounded_rect(backdrop, 2, 2, width - 3, height - 4, 13, COLORS["surface"])

        outer.bind("<Configure>", redraw)
        return content

    def section_header(self, parent, title, subtitle, row):
        group = tk.Frame(parent, bg=COLORS["surface"])
        group.grid(row=row, column=0, sticky="w")
        tk.Label(group, text=title, bg=COLORS["surface"], fg=COLORS["text"],
                 font=self.font_section).pack(anchor="w")
        tk.Label(group, text=subtitle, bg=COLORS["surface"], fg=COLORS["muted"],
                 font=self.font_small).pack(anchor="w", pady=(3, 0))

    def toggle_token(self):
        self._token_visible = not self._token_visible
        self.token_entry.configure(show="" if self._token_visible else "*")
        self.show_button.set_label("Скрыть" if self._token_visible else "Показать")

    def update_link_count(self, _event=None):
        if self.links.edit_modified():
            self.invalidate_preview()
            lines = [line.strip() for line in self.links.get("1.0", "end").splitlines()]
            count = len({line for line in lines if line and not line.startswith("#")})
            self.link_count.set(f"{count} PR")
            self.links.edit_modified(False)

    def invalidate_preview(self, *_):
        self._preview_all_approved = False
        self.update_run_label()

    def update_run_label(self):
        self.run_button.set_label(action_label(
            self.approve.get(), self.merge.get(), self._preview_all_approved,
        ))

    def clear_output(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("end", "Здесь появятся результаты проверки и выполнения.", "meta")
        self.output.configure(state="disabled")
        self._has_output = False

    def log(self, message):
        self.output.configure(state="normal")
        if not self._has_output:
            self.output.delete("1.0", "end")
            self._has_output = True
        tag = "fail" if message.startswith("FAIL") or "Ошибка" in message else (
            "ok" if message.startswith("OK") or message.startswith("Подключено") else (
                "plan" if "PLAN" in message else "meta"
            )
        )
        self.output.insert("end", message + "\n", tag)
        self.output.see("end")
        self.output.configure(state="disabled")

    def set_busy(self, value):
        self.busy = value
        for button in (self.test_button, self.load_button, self.preview_button, self.run_button):
            button.set_enabled(not value)
        self.status.set("Работаю с Bitbucket..." if value else "Готово.")
        if value:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()

    def load_file(self):
        path = filedialog.askopenfilename(
            title="Файл со ссылками на PR",
            filetypes=[("Text files", "*.txt"), ("All files", "*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as source:
                content = source.read()
        except OSError as error:
            messagebox.showerror("Не удалось прочитать файл", str(error))
            return
        self.links.delete("1.0", "end")
        self.links.insert("1.0", content)
        self.status.set(f"Загружен файл: {path}")

    def show_guide(self):
        messagebox.showinfo(
            "Настройка Bitbucket",
            "Создай Bitbucket Cloud API token для аккаунта с доступом к нужным репозиториям "
            "и правами читать PR, одобрять и сливать их.\n\n"
            "Вставь токен в скрытое поле или задай BITBUCKET_API_TOKEN до запуска. "
            "Нажми «Проверить», чтобы увидеть, под каким аккаунтом работает приложение.\n\n"
            "Токен не сохраняется на диск. Подробнее — README.md в каталоге проекта.",
        )

    def check_connection(self):
        token = self.token.get().strip()
        if not token:
            messagebox.showerror("Нужен токен", "Вставьте Bitbucket API token.")
            return
        self.set_busy(True)
        threading.Thread(target=self.connection_worker, args=(token,), daemon=True).start()

    def connection_worker(self, token):
        try:
            me = BitbucketAPI(token).call("GET", "/user")
            name = me.get("display_name") or me.get("username") or me.get("uuid") or "unknown"
            self.events.put(("log", f"Подключено как: {name}"))
        except (BitbucketError, KeyError, ValueError, OSError) as error:
            self.events.put(("log", f"Ошибка подключения: {error}"))
        finally:
            self.events.put(("done", None))

    def preview(self):
        self.start_batch(dry_run=True)

    def run(self):
        self.start_batch(dry_run=False)

    def start_batch(self, *, dry_run):
        token = self.token.get().strip()
        if not token:
            messagebox.showerror("Нужен токен", "Вставьте Bitbucket API token.")
            return
        approve = self.approve.get()
        merge = self.merge.get()
        if not approve and not merge:
            messagebox.showerror("Нет действия", "Выберите Approve, Merge или оба действия.")
            return
        try:
            refs = collect_refs(self.links.get("1.0", "end").splitlines(), None)
        except ValueError as error:
            messagebox.showerror("Неверная ссылка", str(error))
            return
        if not refs:
            messagebox.showerror("Нет ссылок", "Вставьте хотя бы одну ссылку на PR.")
            return

        target = self.target.get().strip() or None
        preview_context = (
            token, target, approve, merge, self.links.get("1.0", "end"),
        )
        action = "Проверка" if dry_run else "Выполнение"
        self.log(f"{action}: {len(refs)} PR; approve={approve}, merge={merge}, target={target or 'any'}")
        self.set_busy(True)
        threading.Thread(
            target=self.batch_worker,
            args=(token, refs, approve, merge, dry_run, target, preview_context),
            daemon=True,
        ).start()

    def batch_worker(self, token, refs, approve, merge, dry_run, target, preview_context):
        api = BitbucketAPI(token)
        try:
            me = api.call("GET", "/user")
            user_uuid = me["uuid"]
            failures = 0
            all_approved = True
            for ref in refs:
                try:
                    result = process_pr(
                        api, ref, user_uuid,
                        approve=approve, merge=merge, dry_run=dry_run, target=target,
                        include_approval_state=dry_run and approve and merge,
                    )
                    if dry_run and approve and merge:
                        result, already_approved = result
                        all_approved = all_approved and already_approved
                    self.events.put(("log", f"OK   {ref.url} -> {result}"))
                except (BitbucketError, KeyError, ValueError, OSError) as error:
                    failures += 1
                    self.events.put(("log", f"FAIL {ref.url} -> {error}"))
            self.events.put(("log", f"Готово: {len(refs)} PR, ошибок: {failures}."))
            if dry_run and approve and merge:
                self.events.put(("approval_preview", (
                    preview_context, failures == 0 and all_approved,
                )))
            elif not dry_run:
                self.events.put(("clear_preview", None))
        except (BitbucketError, KeyError, ValueError, OSError) as error:
            self.events.put(("log", f"Операция прервана: {error}"))
        finally:
            self.events.put(("done", None))

    def drain_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log(value)
                elif kind == "approval_preview":
                    context, all_approved = value
                    current = (
                        self.token.get().strip(), self.target.get().strip() or None,
                        self.approve.get(), self.merge.get(), self.links.get("1.0", "end"),
                    )
                    if context == current:
                        self._preview_all_approved = all_approved
                        self.update_run_label()
                elif kind == "clear_preview":
                    self.invalidate_preview()
                elif kind == "done":
                    self.set_busy(False)
        except queue.Empty:
            pass
        self._after_id = self.root.after(100, self.drain_events)

    def on_destroy(self, event):
        if event.widget is self.root and self._after_id is not None:
            self.root.after_cancel(self._after_id)
            self._after_id = None

    def close(self):
        if self.busy:
            messagebox.showinfo("Операция выполняется", "Дождитесь завершения текущей операции.")
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    PullRequestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
