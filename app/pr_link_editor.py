"""Standard editing commands for the multiline PR list."""

import tkinter as tk


EDIT_ACTIONS = {
    "a": "<<SelectAll>>",
    "c": "<<Copy>>",
    "v": "<<Paste>>",
    "x": "<<Cut>>",
    "z": "<<Undo>>",
    "y": "<<Redo>>",
}
RUSSIAN_KEYS = {
    "ф": "a", "с": "c", "м": "v", "ч": "x", "я": "z", "н": "y",
    "cyrillic_ef": "a", "cyrillic_es": "c", "cyrillic_em": "v",
    "cyrillic_che": "x", "cyrillic_ya": "z", "cyrillic_en": "y",
}


def shortcut_action(keysym, keycode, state, windowing_system):
    # AltGr must keep its native Ctrl+Alt behavior. Windows uses a separate
    # Alt flag as well as the Mod1 flag used by other Tk platforms.
    if state & (0x0008 | 0x20000):
        return None
    if windowing_system == "win32" and keycode in (65, 67, 86, 88, 90, 89):
        key = chr(keycode).lower()
    else:
        key = keysym.lower()
        key = RUSSIAN_KEYS.get(key, key)
    if key == "z" and state & 0x0001:
        return "<<Redo>>"
    return EDIT_ACTIONS.get(key)


class PullRequestLinks(tk.Text):
    def __init__(self, parent, **options):
        options.setdefault("undo", True)
        options.setdefault("exportselection", False)
        super().__init__(parent, **options)
        self.windowing_system = self.tk.call("tk", "windowingsystem")
        modifier = "Command" if self.windowing_system == "aqua" else "Control"
        self.bind(f"<{modifier}-KeyPress>", self.on_shortcut)
        self.bind("<<SelectAll>>", self.select_all)
        self.bind("<Button-3>", self.show_context_menu)
        self.bind("<Shift-F10>", self.show_context_menu)
        self.bind("<KeyPress-Menu>", self.show_context_menu)
        if self.windowing_system == "aqua":
            self.bind("<Button-2>", self.show_context_menu)
            self.bind("<Control-Button-1>", self.show_context_menu)

        accelerator = "Cmd" if self.windowing_system == "aqua" else "Ctrl"
        self.context_menu = tk.Menu(self, tearoff=False)
        self.menu_entries = {}
        for label, event, key in (
            ("Отменить", "<<Undo>>", "Z"),
            ("Повторить", "<<Redo>>", "Shift+Z"),
            ("Вырезать", "<<Cut>>", "X"),
            ("Копировать", "<<Copy>>", "C"),
            ("Вставить", "<<Paste>>", "V"),
            ("Удалить выделенное", "<<Clear>>", None),
            ("Выделить всё", "<<SelectAll>>", "A"),
        ):
            if event in ("<<Cut>>", "<<SelectAll>>"):
                self.context_menu.add_separator()
            self.context_menu.add_command(
                label=label, accelerator=f"{accelerator}+{key}" if key else "Del",
                command=lambda action=event: self.perform(action),
            )
            self.menu_entries[event] = self.context_menu.index("end")
        self.context_menu.add_command(label="Очистить список", command=lambda: self.replace_content(""))
        self.clear_entry = self.context_menu.index("end")

    def on_shortcut(self, event):
        action = shortcut_action(event.keysym, event.keycode, event.state, self.windowing_system)
        if action:
            return self.perform(action)
        return None

    def perform(self, action):
        self.focus_set()
        self.event_generate(action)
        # Suppress the native key binding so copy/paste/undo only happens once.
        return "break"

    def select_all(self, _event=None):
        self.tag_add("sel", "1.0", "end-1c")
        self.mark_set("insert", "end-1c")
        self.see("insert")
        return "break"

    def replace_content(self, content):
        # Loading a file or clearing the list is one undoable edit.
        self.edit_separator()
        self.replace("1.0", "end-1c", content)
        self.edit_separator()
        self.tag_remove("sel", "1.0", "end")
        self.mark_set("insert", "1.0")
        self.see("insert")

    def update_menu_state(self):
        selected = bool(self.tag_ranges("sel"))
        editable = self.cget("state") == "normal"
        has_text = self.compare("end-1c", ">", "1.0")
        try:
            can_paste = bool(self.clipboard_get())
        except tk.TclError:
            can_paste = False
        enabled = {
            "<<Undo>>": editable and self.tk.getboolean(self.tk.call(self._w, "edit", "canundo")),
            "<<Redo>>": editable and self.tk.getboolean(self.tk.call(self._w, "edit", "canredo")),
            "<<Cut>>": editable and selected,
            "<<Copy>>": selected,
            "<<Paste>>": editable and can_paste,
            "<<Clear>>": editable and selected,
            "<<SelectAll>>": has_text,
        }
        for action, index in self.menu_entries.items():
            self.context_menu.entryconfigure(index, state="normal" if enabled[action] else "disabled")
        self.context_menu.entryconfigure(self.clear_entry, state="normal" if editable and has_text else "disabled")

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
