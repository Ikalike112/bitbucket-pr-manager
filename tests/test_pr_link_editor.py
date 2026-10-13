import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from bitbucket_pr_ui import PullRequestApp
from pr_link_editor import PullRequestLinks, shortcut_action


class ShortcutTests(unittest.TestCase):
    def test_windows_shortcuts_use_physical_keys_in_both_layouts(self):
        for keysym, keycode, action in (
            ("Cyrillic_ef", 65, "<<SelectAll>>"),
            ("Cyrillic_es", 67, "<<Copy>>"),
            ("Cyrillic_em", 86, "<<Paste>>"),
            ("Cyrillic_che", 88, "<<Cut>>"),
            ("Cyrillic_ya", 90, "<<Undo>>"),
            ("Cyrillic_en", 89, "<<Redo>>"),
        ):
            with self.subTest(action=action):
                self.assertEqual(shortcut_action(keysym, keycode, 4, "win32"), action)
                self.assertEqual(shortcut_action(chr(keycode), keycode, 4, "win32"), action)

    def test_other_platforms_use_keysyms_and_shift_z_redoes(self):
        for platform in ("x11", "aqua"):
            modifier = 8 if platform == "aqua" else 4
            self.assertEqual(shortcut_action("a", 38, modifier, platform), "<<SelectAll>>")
            self.assertEqual(shortcut_action("Cyrillic_es", 54, modifier, platform), "<<Copy>>")
            self.assertEqual(shortcut_action("м", 55, modifier, platform), "<<Paste>>")
            self.assertEqual(shortcut_action("Z", 52, modifier | 1, platform), "<<Redo>>")

    def test_macos_command_is_accepted_but_option_and_plain_control_are_not(self):
        self.assertEqual(shortcut_action("c", 8, 0x0008, "aqua"), "<<Copy>>")
        self.assertIsNone(shortcut_action("c", 8, 0x0018, "aqua"))
        self.assertIsNone(shortcut_action("c", 8, 0x0004, "aqua"))

    def test_linux_num_lock_does_not_disable_control_shortcuts(self):
        self.assertEqual(shortcut_action("v", 55, 0x0014, "x11"), "<<Paste>>")

    def test_keys_without_the_platform_modifier_are_not_shortcuts(self):
        for platform in ("win32", "x11", "aqua"):
            self.assertIsNone(shortcut_action("a", 65, 0, platform))

    def test_altgr_and_unrelated_navigation_are_not_intercepted(self):
        for alt in (8, 0x20000):
            self.assertIsNone(shortcut_action("c", 67, alt | 4, "win32"))
        self.assertIsNone(shortcut_action("Left", 37, 4, "win32"))


class EditorTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            if os.environ.get("PR_MANAGER_REQUIRE_TK") == "1":
                raise
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.addCleanup(self.destroy_root)
        # Exercise Tk's actual copy/paste bindings without touching the user's
        # system clipboard. Every test has a separate hidden Tcl interpreter.
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
            rename ::tk::GetSelection ::tk::native_test_GetSelection
            proc ::tk::GetSelection {widget selection} {
                if {$selection eq {CLIPBOARD}} {
                    return [clipboard get -displayof $widget]
                }
                return [::tk::native_test_GetSelection $widget $selection]
            }
        """)
        self.editor = PullRequestLinks(self.root)
        self.editor.pack()

    def destroy_root(self):
        self.root.update_idletasks()
        self.root.destroy()

    def text(self):
        return self.editor.get("1.0", "end-1c")

    def contents(self, text):
        self.editor.replace_content(text)
        self.editor.edit_reset()

    def key(self, letter, *, shift=False):
        modifier = 8 if self.editor.windowing_system == "aqua" else 4
        event = SimpleNamespace(keysym=letter, keycode=ord(letter.upper()), state=modifier | int(shift))
        return self.editor.on_shortcut(event)

    def test_select_all_and_copy_preserve_exact_multiline_list(self):
        links = "https://bitbucket.org/team/one/pull-requests/1\nhttps://bitbucket.org/team/two/pull-requests/2"
        self.contents(links)
        self.assertEqual(self.key("a"), "break")
        self.key("c")
        self.assertEqual(self.editor.clipboard_get(), links)
        self.assertEqual(self.text(), links)

    def test_paste_replaces_selection_once_and_supports_undo_redo(self):
        self.contents("old links")
        self.editor.clipboard_append("first\nsecond")
        self.key("a")
        self.key("v")
        self.assertEqual(self.text(), "first\nsecond")
        self.key("z")
        self.assertEqual(self.text(), "old links")
        self.key("z", shift=True)
        self.assertEqual(self.text(), "first\nsecond")

    def test_cut_and_ctrl_y_redo(self):
        self.contents("a\nb")
        self.key("a")
        self.key("x")
        self.assertEqual(self.text(), "")
        self.assertEqual(self.editor.clipboard_get(), "a\nb")
        self.key("z")
        self.assertEqual(self.text(), "a\nb")
        self.key("y")
        self.assertEqual(self.text(), "")

    def test_paste_replaces_only_the_selection_and_preserves_unicode(self):
        self.contents("before OLD after")
        self.editor.tag_add("sel", "1.7", "1.10")
        self.editor.mark_set("insert", "end-1c")
        self.editor.clipboard_append("новые ссылки ✓")
        self.key("v")
        self.assertEqual(self.text(), "before новые ссылки ✓ after")
        self.assertEqual(self.editor.index("insert"), "1.21")
        self.assertFalse(self.editor.tag_ranges("sel"))
        self.key("z")
        self.assertEqual(self.text(), "before OLD after")

    def test_paste_without_selection_uses_the_insertion_point(self):
        self.contents("left right")
        self.editor.mark_set("insert", "1.5")
        self.editor.clipboard_append("middle ")
        self.key("v")
        self.assertEqual(self.text(), "left middle right")
        self.key("z")
        self.assertEqual(self.text(), "left right")

    def test_paste_in_disabled_editor_preserves_text(self):
        self.contents("keep this")
        self.key("a")
        self.editor.clipboard_append("replacement")
        self.editor.configure(state="disabled")
        self.key("v")
        self.assertEqual(self.text(), "keep this")

    def test_context_menu_paste_uses_the_same_selection_replacement(self):
        self.contents("original")
        self.key("a")
        self.editor.clipboard_append("replacement")
        self.editor.update_menu_state()
        self.editor.context_menu.invoke(self.editor.menu_entries["<<Paste>>"])
        self.assertEqual(self.text(), "replacement")
        self.key("z")
        self.assertEqual(self.text(), "original")

    def test_empty_clipboard_and_empty_undo_do_not_erase_selection(self):
        self.contents("keep this")
        self.key("a")
        self.key("v")
        self.assertEqual(self.text(), "keep this")
        self.key("z")
        self.key("y")
        self.assertEqual(self.text(), "keep this")

    def test_load_and_clear_are_separate_undoable_edits(self):
        self.contents("original")
        self.editor.replace_content("new\nlist\n")
        self.editor.replace_content("")
        self.key("z")
        self.assertEqual(self.text(), "new\nlist\n")
        self.key("z")
        self.assertEqual(self.text(), "original")

    def test_context_menu_disables_unavailable_operations(self):
        self.editor.update_menu_state()
        for action in ("<<Undo>>", "<<Redo>>", "<<Cut>>", "<<Copy>>", "<<Paste>>", "<<SelectAll>>"):
            self.assertEqual(self.editor.context_menu.entrycget(self.editor.menu_entries[action], "state"), "disabled")
        self.contents("selected")
        self.key("a")
        self.editor.clipboard_append("new")
        self.editor.update_menu_state()
        for action in ("<<Cut>>", "<<Copy>>", "<<Paste>>", "<<Clear>>", "<<SelectAll>>"):
            self.assertEqual(self.editor.context_menu.entrycget(self.editor.menu_entries[action], "state"), "normal")
        self.editor.context_menu.invoke(self.editor.menu_entries["<<Clear>>"])
        self.assertEqual(self.text(), "")
        self.key("z")
        self.assertEqual(self.text(), "selected")

    def test_context_menu_preserves_selection_and_supports_keyboard_position(self):
        self.contents("selected")
        self.key("a")
        with patch.object(self.editor.context_menu, "tk_popup") as popup:
            result = self.editor.show_context_menu(SimpleNamespace(num=3, x_root=20, y_root=30))
            popup.assert_called_once_with(20, 30)
        self.assertEqual(result, "break")
        self.assertEqual(self.editor.get("sel.first", "sel.last"), "selected")
        with patch.object(self.editor.context_menu, "tk_popup") as popup:
            self.editor.show_context_menu(SimpleNamespace(num="??"))
            popup.assert_called_once()

    def test_editor_is_scoped_and_retains_native_navigation_bindings(self):
        modifier = "Command" if self.editor.windowing_system == "aqua" else "Control"
        self.assertTrue(self.editor.bind(f"<{modifier}-KeyPress>"))
        self.assertFalse(self.root.bind_all(f"<{modifier}-KeyPress>"))
        self.assertIn("Text", self.editor.bindtags())
        self.assertIsNone(self.editor.on_shortcut(SimpleNamespace(keysym="Left", keycode=37, state=4)))

    def test_paste_clear_and_undo_update_count_and_invalidate_preview(self):
        with patch.dict(os.environ, {"BITBUCKET_API_TOKEN": ""}):
            app = PullRequestApp(self.root)
        self.editor = app.links
        self.root.update()
        self.editor.clipboard_append("https://bitbucket.org/team/repo/pull-requests/1\n" * 2)
        app._preview_all_approved = True
        # Exercise the paste handler without displaying the nested app widget.
        self.editor.paste_clipboard()
        self.root.update()
        self.assertEqual(app.link_count.get(), "1 PR")
        self.assertFalse(app._preview_all_approved)
        self.editor.replace_content("")
        self.root.update()
        self.assertEqual(app.link_count.get(), "0 PR")
        self.key("z")
        self.root.update()
        self.assertEqual(app.link_count.get(), "1 PR")

    def test_loading_file_through_app_can_be_undone(self):
        with patch.dict(os.environ, {"BITBUCKET_API_TOKEN": ""}):
            app = PullRequestApp(self.root)
        self.editor = app.links
        self.contents("original")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "links.txt"
            path.write_text("https://bitbucket.org/team/repo/pull-requests/2", encoding="utf-8-sig")
            with patch("bitbucket_pr_ui.filedialog.askopenfilename", return_value=str(path)):
                app.load_file()
        self.root.update()
        self.assertEqual(app.link_count.get(), "1 PR")
        self.key("z")
        self.assertEqual(self.text(), "original")


if __name__ == "__main__":
    unittest.main()
