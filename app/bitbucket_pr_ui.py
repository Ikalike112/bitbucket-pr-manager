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


def system_font(root):
    families = set(tkfont.families(root))
    if sys.platform == "darwin":
        preferred = ("SF Pro Text", "Helvetica Neue", "Arial")
    elif sys.platform == "win32":
        preferred = ("Segoe UI", "Arial", "Helvetica")
    else:
        preferred = ("Inter", "Noto Sans", "DejaVu Sans")
    return next((name for name in preferred if name in families), "TkDefaultFont")


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
        logo.create_rectangle(0, 0, 48, 48, fill=COLORS["accent"], outline=COLORS["accent"])
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
        self.show_button = ttk.Button(credential_row, text="Показать", style="Ghost.TButton",
                                      command=self.toggle_token)
        self.show_button.grid(row=0, column=1, padx=(8, 0))
        self.test_button = ttk.Button(credential_row, text="Проверить доступ",
                                      style="Secondary.TButton", command=self.check_connection)
        self.test_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(credential_row, text="Настройка", style="Ghost.TButton",
                   command=self.show_guide).grid(row=0, column=3, padx=(8, 0))
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
        self.load_button = ttk.Button(links_top, text="Загрузить .txt",
                                      style="Secondary.TButton", command=self.load_file)
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
        links_scroll = ttk.Scrollbar(text_container, orient="vertical", command=self.links.yview)
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
        ttk.Checkbutton(options, text="Approve", variable=self.approve,
                        style="Card.TCheckbutton").pack(side="left", padx=(0, 14))
        ttk.Checkbutton(options, text="Merge", variable=self.merge,
                        style="Card.TCheckbutton").pack(side="left")
        self.run_button = ttk.Button(options, text="Одобрить PR",
                                     style="Accent.TButton", command=self.run)
        self.run_button.pack(side="right")
        self.preview_button = ttk.Button(options, text="Предпросмотр",
                                         style="Secondary.TButton", command=self.preview)
        self.preview_button.pack(side="right", padx=(0, 10))
        self.approve.trace_add("write", self.update_run_label)
        self.merge.trace_add("write", self.update_run_label)

        results = self.card(shell, 4)
        results.columnconfigure(0, weight=1)
        results.rowconfigure(1, weight=1)
        results_top = tk.Frame(results, bg=COLORS["surface"])
        results_top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        results_top.columnconfigure(0, weight=1)
        tk.Label(results_top, text="Результаты", bg=COLORS["surface"],
                 fg=COLORS["text"], font=self.font_section).grid(row=0, column=0, sticky="w")
        ttk.Button(results_top, text="Очистить", style="Ghost.TButton",
                   command=self.clear_output).grid(row=0, column=1)
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
        output_scroll = ttk.Scrollbar(output_container, orient="vertical", command=self.output.yview)
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
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="white",
                        borderwidth=0, relief="flat", padding=(16, 10), font=self.font)
        style.map("Accent.TButton", background=[("disabled", COLORS["border"]),
                                                  ("active", COLORS["accent_hover"])],
                  foreground=[("disabled", COLORS["muted"])])
        style.configure("Secondary.TButton", background=COLORS["accent_soft"],
                        foreground=COLORS["accent"], borderwidth=0, relief="flat",
                        padding=(14, 10), font=self.font_small)
        style.map("Secondary.TButton", background=[("active", "#DCE9FF")])
        style.configure("Ghost.TButton", background=COLORS["surface"],
                        foreground=COLORS["muted"], borderwidth=0, relief="flat",
                        padding=(8, 9), font=self.font_small)
        style.map("Ghost.TButton", foreground=[("active", COLORS["accent"])])
        style.configure("Card.TCheckbutton", background=COLORS["surface"],
                        foreground=COLORS["text"], font=self.font, padding=(0, 4))
        style.map("Card.TCheckbutton", background=[("active", COLORS["surface"])])
        style.configure("Accent.Horizontal.TProgressbar", background=COLORS["accent"],
                        troughcolor=COLORS["border"], borderwidth=0)

    def card(self, parent, row, *, pady=(0, 0)):
        card = tk.Frame(parent, bg=COLORS["surface"], highlightthickness=1,
                        highlightbackground=COLORS["border"], padx=20, pady=14)
        card.grid(row=row, column=0, sticky="nsew", pady=pady)
        return card

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
        self.show_button.configure(text="Скрыть" if self._token_visible else "Показать")

    def update_link_count(self, _event=None):
        if self.links.edit_modified():
            lines = [line.strip() for line in self.links.get("1.0", "end").splitlines()]
            count = len({line for line in lines if line and not line.startswith("#")})
            self.link_count.set(f"{count} PR")
            self.links.edit_modified(False)

    def update_run_label(self, *_):
        if self.approve.get() and self.merge.get():
            label = "Одобрить и слить"
        elif self.merge.get():
            label = "Слить PR"
        elif self.approve.get():
            label = "Одобрить PR"
        else:
            label = "Выбери действие"
        self.run_button.configure(text=label)

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
        state = "disabled" if value else "normal"
        for button in (self.test_button, self.load_button, self.preview_button, self.run_button):
            button.configure(state=state)
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
        action = "Проверка" if dry_run else "Выполнение"
        self.log(f"{action}: {len(refs)} PR; approve={approve}, merge={merge}, target={target or 'any'}")
        self.set_busy(True)
        threading.Thread(
            target=self.batch_worker,
            args=(token, refs, approve, merge, dry_run, target),
            daemon=True,
        ).start()

    def batch_worker(self, token, refs, approve, merge, dry_run, target):
        api = BitbucketAPI(token)
        try:
            me = api.call("GET", "/user")
            user_uuid = me["uuid"]
            failures = 0
            for ref in refs:
                try:
                    result = process_pr(
                        api, ref, user_uuid,
                        approve=approve, merge=merge, dry_run=dry_run, target=target,
                    )
                    self.events.put(("log", f"OK   {ref.url} -> {result}"))
                except (BitbucketError, KeyError, ValueError, OSError) as error:
                    failures += 1
                    self.events.put(("log", f"FAIL {ref.url} -> {error}"))
            self.events.put(("log", f"Готово: {len(refs)} PR, ошибок: {failures}."))
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
