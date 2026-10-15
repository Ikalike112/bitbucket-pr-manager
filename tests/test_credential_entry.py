import os
import sys
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from credential_entry import CredentialEntry


class CredentialEntryTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            if os.environ.get("PR_MANAGER_REQUIRE_TK") == "1":
                raise
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.addCleanup(self.destroy_root)
        # Do not read or overwrite the user's clipboard, even on native Tk.
        self.root.tk.eval("""
            rename clipboard native_test_clipboard
            set test_clipboard {}
            proc clipboard {command args} {
                switch -- $command {
                    clear {set ::test_clipboard {}}
                    append {append ::test_clipboard [lindex $args end]}
                    get {
                        if {$::test_clipboard eq {}} {
                            return -code error {no text in clipboard}
                        }
                        return $::test_clipboard
                    }
                    default {return -code error {unsupported clipboard command}}
                }
            }
        """)
        self.entry = CredentialEntry(self.root, show="*")
        self.entry.pack()
        # Entry on Windows needs a native window ID before generated events
        # are dispatched, even though the test root remains hidden.
        self.entry.winfo_id()

    def destroy_root(self):
        self.root.update_idletasks()
        self.root.destroy()

    def contents(self, value):
        self.entry.delete(0, "end")
        self.entry.insert(0, value)

    def key(self, keysym, keycode=0, *, platform=None):
        platform = platform or self.entry.windowing_system
        modifier = 8 if platform == "aqua" else 4
        event = SimpleNamespace(keysym=keysym, keycode=keycode, state=modifier)
        with patch.object(self.entry, "windowing_system", platform):
            return self.entry.on_shortcut(event)

    def test_russian_macos_select_all_and_paste_replace_once(self):
        self.contents("old credential")
        self.entry.clipboard_append("replacement-token")
        self.assertEqual(self.key("Cyrillic_ef", platform="aqua"), "break")
        self.assertEqual(self.key("Cyrillic_em", platform="aqua"), "break")
        self.assertEqual(self.entry.get(), "replacement-token")
        self.assertFalse(self.entry.selection_present())
        self.assertEqual(self.entry.index("insert"), len("replacement-token"))

    def test_windows_russian_shortcuts_use_physical_keys(self):
        self.contents("old credential")
        self.entry.clipboard_append("replacement-token")
        self.key("Cyrillic_ef", 65, platform="win32")
        self.key("Cyrillic_em", 86, platform="win32")
        self.assertEqual(self.entry.get(), "replacement-token")

    def test_virtual_paste_replaces_only_selection_and_moves_cursor(self):
        self.contents("before OLD after")
        self.entry.selection_range(7, 10)
        self.entry.icursor("end")
        self.entry.clipboard_append("новое✓")
        self.entry.event_generate("<<Paste>>")
        self.assertEqual(self.entry.get(), "before новое✓ after")
        self.assertEqual(self.entry.index("insert"), 13)
        self.assertFalse(self.entry.selection_present())

    def test_paste_without_selection_uses_cursor(self):
        self.contents("left right")
        self.entry.icursor(5)
        self.entry.clipboard_append("middle ")
        self.entry.event_generate("<<Paste>>")
        self.assertEqual(self.entry.get(), "left middle right")

    def test_empty_or_unavailable_clipboard_preserves_selected_token(self):
        self.contents("keep-token")
        self.entry.selection_range(0, "end")
        for result in (None, ""):
            with self.subTest(result=result):
                if result is None:
                    clipboard = patch.object(self.entry, "clipboard_get", side_effect=tk.TclError)
                else:
                    clipboard = patch.object(self.entry, "clipboard_get", return_value=result)
                with clipboard:
                    self.entry.event_generate("<<Paste>>")
                self.assertEqual(self.entry.get(), "keep-token")
                self.assertTrue(self.entry.selection_present())

    def test_masked_copy_and_cut_never_put_secret_on_clipboard(self):
        self.contents("secret-token")
        self.entry.clipboard_append("existing clipboard")
        self.entry.selection_range(0, "end")
        for action in ("<<Copy>>", "<<Cut>>"):
            with self.subTest(action=action):
                self.entry.event_generate(action)
                self.assertEqual(self.entry.clipboard_get(), "existing clipboard")
                self.assertEqual(self.entry.get(), "secret-token")
        self.entry.update_menu_state()
        for action in ("<<Copy>>", "<<Cut>>"):
            self.assertEqual(self.entry.context_menu.entrycget(self.entry.menu_entries[action], "state"), "disabled")

    def test_visible_copy_and_cut_work_without_copying_other_text(self):
        self.contents("left TOKEN right")
        self.entry.configure(show="")
        self.entry.selection_range(5, 10)
        self.entry.event_generate("<<Copy>>")
        self.assertEqual(self.entry.clipboard_get(), "TOKEN")
        self.assertEqual(self.entry.get(), "left TOKEN right")
        self.entry.event_generate("<<Cut>>")
        self.assertEqual(self.entry.clipboard_get(), "TOKEN")
        self.assertEqual(self.entry.get(), "left  right")

    def test_disabled_and_readonly_entry_reject_mutation(self):
        for state in ("disabled", "readonly"):
            with self.subTest(state=state):
                self.entry.configure(state="normal", show="")
                self.contents("keep-token")
                self.entry.selection_range(0, "end")
                self.entry.clipboard_clear()
                self.entry.clipboard_append("replacement")
                self.entry.configure(state=state)
                for action in ("<<Paste>>", "<<Cut>>", "<<Clear>>"):
                    self.entry.event_generate(action)
                    self.assertEqual(self.entry.get(), "keep-token")
                self.entry.update_menu_state()
                self.assertEqual(self.entry.context_menu.entrycget(self.entry.menu_entries["<<Paste>>"], "state"), "disabled")

    def test_context_menu_paste_replaces_selection(self):
        self.contents("old-token")
        self.entry.selection_range(0, "end")
        self.entry.clipboard_append("new-token")
        self.entry.update_menu_state()
        self.entry.context_menu.invoke(self.entry.menu_entries["<<Paste>>"])
        self.assertEqual(self.entry.get(), "new-token")

    def test_context_menu_preserves_selection_and_has_keyboard_location(self):
        self.contents("token")
        self.entry.selection_range(0, "end")
        with patch.object(self.entry.context_menu, "tk_popup") as popup:
            self.entry.show_context_menu(SimpleNamespace(num=3, x_root=10, y_root=20))
            popup.assert_called_once_with(10, 20)
        self.assertTrue(self.entry.selection_present())
        with patch.object(self.entry.context_menu, "tk_popup") as popup:
            self.entry.show_context_menu(SimpleNamespace(num="??"))
            popup.assert_called_once()

    def test_shortcuts_are_scoped_and_do_not_intercept_typing_or_navigation(self):
        modifier = "Command" if self.entry.windowing_system == "aqua" else "Control"
        self.assertTrue(self.entry.bind(f"<{modifier}-KeyPress>"))
        self.assertFalse(self.root.bind_all(f"<{modifier}-KeyPress>"))
        self.assertIn("Entry", self.entry.bindtags())
        for keysym, state in (("a", 0), ("v", 0), ("Left", 4), ("BackSpace", 0)):
            event = SimpleNamespace(keysym=keysym, keycode=0, state=state)
            self.assertIsNone(self.entry.on_shortcut(event))
        # Selection must not become X11 PRIMARY selection on token focus loss.
        self.assertFalse(self.root.tk.getboolean(self.entry.cget("exportselection")))


if __name__ == "__main__":
    unittest.main()
