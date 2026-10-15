"""Cross-platform editing for credentials, without copying masked secrets."""

import tkinter as tk

from pr_link_editor import shortcut_action


class CredentialEntry(tk.Entry):
    def __init__(self, parent, **options):
        options.setdefault("exportselection", False)
        super().__init__(parent, **options)
        self.windowing_system = self.tk.call("tk", "windowingsystem")
        modifier = "Command" if self.windowing_system == "aqua" else "Control"
        self.bind(f"<{modifier}-KeyPress>", self.on_shortcut)
        for action, handler in (
            ("<<SelectAll>>", self.select_all),
            ("<<Paste>>", self.paste_clipboard),
            ("<<Copy>>", self.copy_selection),
            ("<<Cut>>", self.cut_selection),
            ("<<Clear>>", self.clear_selection),
        ):
            self.bind(action, handler)
        for sequence in ("<Button-3>", "<Shift-F10>", "<KeyPress-Menu>"):
            self.bind(sequence, self.show_context_menu)
        if self.windowing_system == "aqua":
            self.bind("<Button-2>", self.show_context_menu)
            self.bind("<Control-Button-1>", self.show_context_menu)

        accelerator = "Cmd" if self.windowing_system == "aqua" else "Ctrl"
        self.context_menu = tk.Menu(self, tearoff=False)
        self.menu_entries = {}
        for label, action, key in (
            ("Вырезать", "<<Cut>>", "X"),
            ("Копировать", "<<Copy>>", "C"),
            ("Вставить", "<<Paste>>", "V"),
            ("Удалить выделенное", "<<Clear>>", None),
            ("Выделить всё", "<<SelectAll>>", "A"),
        ):
            self.context_menu.add_command(
                label=label, accelerator=f"{accelerator}+{key}" if key else "Del",
                command=lambda event=action: self.perform(event),
            )
            self.menu_entries[action] = self.context_menu.index("end")

    def on_shortcut(self, event):
        action = shortcut_action(event.keysym, event.keycode, event.state, self.windowing_system)
        if action in self.menu_entries:
            return self.perform(action)
        return None

    def perform(self, action):
        self.focus_set()
        self.event_generate(action)
        # Prevent the platform binding from applying an edit a second time.
        return "break"

    def select_all(self, _event=None):
        if self.cget("state") != "disabled":
            self.selection_range(0, "end")
            self.icursor("end")
            self.xview("end")
        return "break"

    def paste_clipboard(self, _event=None):
        if self.cget("state") != "normal":
            return "break"
        try:
            content = self.clipboard_get()
        except tk.TclError:
            return "break"
        if not content:
            return "break"
        position = self.index("sel.first") if self.selection_present() else self.index("insert")
        if self.selection_present():
            self.delete("sel.first", "sel.last")
        self.icursor(position)
        self.insert(position, content)
        self.selection_clear()
        self.xview("insert")
        return "break"

    def copy_selection(self, _event=None):
        # Tk's native password-copy behavior differs between versions. Never
        # reveal a masked credential, even via a virtual event or shortcut.
        if self.cget("state") != "disabled" and not self.cget("show") and self.selection_present():
            text = self.tk.call("string", "range", self.get(),
                                self.index("sel.first"), self.index("sel.last") - 1)
            self.clipboard_clear()
            self.clipboard_append(text)
        return "break"

    def cut_selection(self, _event=None):
        if self.cget("state") == "normal" and not self.cget("show"):
            self.copy_selection()
            self.clear_selection()
        return "break"

    def clear_selection(self, _event=None):
        if self.cget("state") == "normal" and self.selection_present():
            position = self.index("sel.first")
            self.delete("sel.first", "sel.last")
            self.icursor(position)
        return "break"

    def update_menu_state(self):
        selected = self.selection_present()
        editable = self.cget("state") == "normal"
        readable = self.cget("state") != "disabled"
        visible = not self.cget("show")
        try:
            can_paste = bool(self.clipboard_get())
        except tk.TclError:
            can_paste = False
        enabled = {
            "<<Cut>>": editable and visible and selected,
            "<<Copy>>": readable and visible and selected,
            "<<Paste>>": editable and can_paste,
            "<<Clear>>": editable and selected,
            "<<SelectAll>>": readable and bool(self.get()),
        }
        for action, index in self.menu_entries.items():
            self.context_menu.entryconfigure(index, state="normal" if enabled[action] else "disabled")

    def show_context_menu(self, event):
        self.focus_set()
        self.update_menu_state()
        if getattr(event, "num", None) in (1, 2, 3):
            x, y = event.x_root, event.y_root
        else:
            bbox = self.bbox("insert") or (0, 0, 0, 0)
            x = self.winfo_rootx() + bbox[0]
            y = self.winfo_rooty() + bbox[1] + bbox[3]
        try:
            self.context_menu.tk_popup(x, y)
        finally:
            self.context_menu.grab_release()
        return "break"
