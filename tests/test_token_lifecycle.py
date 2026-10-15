"""Exercise the GUI lifecycle without accessing the user's credential vault."""

import os
import sys
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from bitbucket_pr_ui import PullRequestApp
from token_store import TokenStoreError


if os.environ.get("PR_MANAGER_REQUIRE_TK") == "1":
    import faulthandler

    faulthandler.dump_traceback_later(60, exit=True)


class MemoryTokenStore:
    """A fake vault with controllable delays and safe error messages."""

    def __init__(self, token=""):
        self.token = token
        self.calls = []
        self.errors = set()
        self.gates = {name: threading.Event() for name in ("load", "save", "delete")}
        for gate in self.gates.values():
            gate.set()
        self.started = {name: threading.Event() for name in self.gates}

    def operation(self, name, token=None):
        self.calls.append((name, token))
        self.started[name].set()
        if not self.gates[name].wait(5):
            raise TokenStoreError("Тестовое хранилище не ответило.")
        if name in self.errors:
            raise TokenStoreError("Системное хранилище недоступно.")

    def load_token(self):
        self.operation("load")
        return self.token

    def save_token(self, token):
        self.operation("save", token)
        self.token = token

    def delete_token(self):
        self.operation("delete")
        self.token = ""


class TokenLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.apps = []
        self.stores = []
        self.results = {}
        self.environment = patch.dict(os.environ, {"BITBUCKET_API_TOKEN": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.cleanup_apps)

    def cleanup_apps(self):
        # Release delayed fake calls before asking the worker threads to stop.
        for store in self.stores:
            for gate in store.gates.values():
                gate.set()
        for app in reversed(self.apps):
            try:
                if app.root.winfo_exists():
                    app.root.destroy()
            except tk.TclError:
                pass  # A normal close already destroyed this interpreter.
            app._credential_thread.join(timeout=1)
            self.assertFalse(app._credential_thread.is_alive())

    def make_app(self, store):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            if os.environ.get("PR_MANAGER_REQUIRE_TK") == "1":
                raise
            self.skipTest(f"Tk display unavailable: {error}")
        root.withdraw()
        app = PullRequestApp(root, token_store=store)
        self.results[app] = []
        original_result = app.credential_result

        def record_result(result):
            closed = original_result(result)
            self.results[app].append(result)
            return closed

        app.credential_result = record_result
        self.apps.append(app)
        self.stores.append(store)
        return app

    def wait_until(self, app, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for the app's credential operation")
            # Aqua redraws can make update() unbounded for withdrawn windows.
            timer = app.root.after(10, app.root.quit)
            try:
                app.root.mainloop()
            finally:
                # A close can end mainloop before this timer fires. Remove it
                # even after destroy, while the Tcl interpreter still exists.
                app.root.after_cancel(timer)

    def wait_for_load(self, app):
        self.wait_until(app, lambda: self.completed(app, "load"))

    def completed(self, app, action, token=None):
        return any(result[0] == action and (token is None or result[1] == token)
                   and result[-1] is None for result in self.results[app])

    def test_save_on_close_and_restore_masked_after_restart(self):
        store = MemoryTokenStore()
        app = self.make_app(store)
        self.wait_for_load(app)
        app.token.set("first-test-token")
        with patch.object(app.root, "destroy", wraps=app.root.destroy) as destroy:
            app.close()
            self.wait_until(app, lambda: destroy.called)
        self.assertEqual(store.token, "first-test-token")

        restarted = self.make_app(store)
        self.wait_for_load(restarted)
        self.assertEqual(restarted.token.get(), "first-test-token")
        self.assertEqual(restarted.token_entry.cget("show"), "*")
        self.assertEqual(restarted.show_button.label, "Показать")
        self.assertNotIn("first-test-token", restarted.output.get("1.0", "end"))

    def test_check_connection_saves_the_updated_token(self):
        store = MemoryTokenStore("old-test-token")
        app = self.make_app(store)
        self.wait_for_load(app)
        app.token.set("updated-test-token")
        with patch("bitbucket_pr_ui.BitbucketAPI") as api:
            api.return_value.call.return_value = {"uuid": "test-user"}
            app.check_connection()
            self.wait_until(app, lambda: not app.busy and self.completed(app, "save", "updated-test-token"))
        self.assertEqual(store.token, "updated-test-token")

    def test_starting_a_batch_saves_the_token(self):
        store = MemoryTokenStore()
        app = self.make_app(store)
        self.wait_for_load(app)
        app.token.set("batch-test-token")
        app.links.replace_content("https://bitbucket.org/team/repo/pull-requests/1")
        with patch("bitbucket_pr_ui.BitbucketAPI") as api, patch(
            "bitbucket_pr_ui.process_pr", return_value="PLAN approve"
        ):
            api.return_value.call.return_value = {"uuid": "test-user"}
            app.start_batch(dry_run=True)
            self.wait_until(app, lambda: not app.busy and self.completed(app, "save", "batch-test-token"))
        self.assertEqual(store.token, "batch-test-token")

    def test_environment_token_takes_precedence_over_saved_token(self):
        store = MemoryTokenStore("saved-test-token")
        with patch.dict(os.environ, {"BITBUCKET_API_TOKEN": "env-test-token"}):
            app = self.make_app(store)
        self.wait_for_load(app)
        self.assertEqual(app.token.get(), "env-test-token")
        self.assertEqual(app.token_entry.cget("show"), "*")
        self.assertIn("BITBUCKET_API_TOKEN", app.credential_status.get())
        self.assertEqual(store.token, "saved-test-token")

    def test_delayed_load_does_not_replace_an_edit(self):
        store = MemoryTokenStore("saved-test-token")
        store.gates["load"].clear()
        app = self.make_app(store)
        self.assertTrue(store.started["load"].wait(1))
        app.token.set("user-entered-token")
        store.gates["load"].set()
        self.wait_for_load(app)
        self.assertEqual(app.token.get(), "user-entered-token")

    def test_delayed_load_does_not_restore_token_after_user_clears_input(self):
        store = MemoryTokenStore("saved-test-token")
        store.gates["load"].clear()
        app = self.make_app(store)
        self.assertTrue(store.started["load"].wait(1))
        app.token.set("temporary-test-token")
        app.token.set("")
        store.gates["load"].set()
        self.wait_for_load(app)
        self.assertEqual(app.token.get(), "")

    def test_empty_input_on_close_preserves_saved_token(self):
        store = MemoryTokenStore("saved-test-token")
        app = self.make_app(store)
        self.wait_for_load(app)
        app.token.set("")
        with patch.object(app.root, "destroy", wraps=app.root.destroy) as destroy:
            app.close()
            self.wait_until(app, lambda: destroy.called)
        self.assertEqual(store.token, "saved-test-token")
        self.assertEqual([name for name, _ in store.calls], ["load"])

    def test_forget_during_delayed_load_clears_without_restoring_token(self):
        store = MemoryTokenStore("saved-test-token")
        store.gates["load"].clear()
        app = self.make_app(store)
        self.assertTrue(store.started["load"].wait(1))
        app.forget_token()
        store.gates["load"].set()
        self.wait_until(app, lambda: "удалён" in app.credential_status.get())
        self.assertEqual(app.token.get(), "")
        self.assertEqual(store.token, "")
        self.assertEqual([name for name, _ in store.calls], ["load", "delete"])

    def test_forget_runs_after_a_pending_save(self):
        store = MemoryTokenStore("saved-test-token")
        app = self.make_app(store)
        self.wait_for_load(app)
        store.gates["save"].clear()
        app.token.set("updated-test-token")
        app.save_token()
        self.assertTrue(store.started["save"].wait(1))
        app.forget_token()
        store.gates["save"].set()
        self.wait_until(app, lambda: "удалён" in app.credential_status.get())
        self.assertEqual(app.token.get(), "")
        self.assertEqual(store.token, "")
        self.assertEqual([name for name, _ in store.calls], ["load", "save", "delete"])

    def test_reentering_same_token_during_delete_saves_it_again_on_close(self):
        store = MemoryTokenStore("saved-test-token")
        app = self.make_app(store)
        self.wait_for_load(app)
        store.gates["delete"].clear()
        app.forget_token()
        self.assertTrue(store.started["delete"].wait(1))
        app.token.set("saved-test-token")
        with patch.object(app.root, "destroy", wraps=app.root.destroy) as destroy:
            app.close()
            store.gates["delete"].set()
            self.wait_until(app, lambda: destroy.called)
        self.assertEqual(store.token, "saved-test-token")

    def test_returning_to_original_token_during_pending_save_preserves_latest_input(self):
        store = MemoryTokenStore("original-test-token")
        app = self.make_app(store)
        self.wait_for_load(app)
        store.gates["save"].clear()
        app.token.set("temporary-test-token")
        app.save_token()
        self.assertTrue(store.started["save"].wait(1))
        app.token.set("original-test-token")
        with patch.object(app.root, "destroy", wraps=app.root.destroy) as destroy:
            app.close()
            store.gates["save"].set()
            self.wait_until(app, lambda: destroy.called)
        self.assertEqual(store.token, "original-test-token")

    def test_close_waits_for_save_and_is_idempotent(self):
        store = MemoryTokenStore()
        app = self.make_app(store)
        self.wait_for_load(app)
        store.gates["save"].clear()
        app.token.set("closing-test-token")
        with patch.object(app.root, "destroy", wraps=app.root.destroy) as destroy:
            app.close()
            app.close()
            self.assertTrue(store.started["save"].wait(1))
            self.assertFalse(destroy.called)
            self.assertEqual(app.token_entry.cget("state"), "disabled")
            store.gates["save"].set()
            self.wait_until(app, lambda: destroy.called)
            destroy.assert_called_once_with()
        self.assertEqual(store.token, "closing-test-token")
        self.assertEqual([name for name, _ in store.calls], ["load", "save"])

    def test_failed_load_keeps_manual_entry_usable(self):
        store = MemoryTokenStore()
        store.errors.add("load")
        app = self.make_app(store)
        self.wait_until(app, lambda: "недоступно" in app.credential_status.get())
        self.assertEqual(app.token_entry.cget("state"), "normal")
        app.token.set("manual-test-token")
        app.save_token()
        self.wait_until(app, lambda: self.completed(app, "save", "manual-test-token"))
        self.assertEqual(store.token, "manual-test-token")
        self.assertNotIn("manual-test-token", app.credential_status.get())
        self.assertNotIn("manual-test-token", app.output.get("1.0", "end"))

    def test_unresponsive_store_can_be_closed_without_waiting(self):
        store = MemoryTokenStore()
        store.gates["load"].clear()
        app = self.make_app(store)
        self.assertTrue(store.started["load"].wait(1))
        app.close()
        # Exercise the timeout callback without waiting three seconds in a test.
        app.allow_close_without_waiting()
        self.assertIn("не ответило", app.status.get())
        with patch("bitbucket_pr_ui.messagebox.askyesno", return_value=False):
            app.close()
        self.assertTrue(app.root.winfo_exists())
        with patch("bitbucket_pr_ui.messagebox.askyesno", return_value=True) as confirm, patch.object(
            app.root, "destroy", wraps=app.root.destroy
        ) as destroy:
            app.close()
            destroy.assert_called_once_with()
        confirm.assert_called_once()

    def test_failed_save_can_be_retried_without_losing_input(self):
        store = MemoryTokenStore()
        app = self.make_app(store)
        self.wait_for_load(app)
        store.errors.add("save")
        app.token.set("retry-test-token")
        app.save_token()
        self.wait_until(app, lambda: "недоступно" in app.credential_status.get())
        self.assertEqual(app.token.get(), "retry-test-token")
        self.assertEqual(app.token_entry.cget("state"), "normal")
        store.errors.clear()
        app.save_token()
        self.wait_until(app, lambda: self.completed(app, "save", "retry-test-token"))
        self.assertEqual(store.token, "retry-test-token")

    def test_failed_save_warns_and_still_closes(self):
        store = MemoryTokenStore()
        app = self.make_app(store)
        self.wait_for_load(app)
        store.errors.add("save")
        app.token.set("unsaved-test-token")
        with patch("bitbucket_pr_ui.messagebox.showwarning") as warning, patch.object(
            app.root, "destroy", wraps=app.root.destroy
        ) as destroy:
            app.close()
            self.wait_until(app, lambda: destroy.called)
            warning.assert_called_once()
            self.assertNotIn("unsaved-test-token", str(warning.call_args))
        self.assertEqual(store.token, "")

    def test_failed_delete_keeps_manual_entry_usable_and_can_be_retried(self):
        store = MemoryTokenStore("saved-test-token")
        app = self.make_app(store)
        self.wait_for_load(app)
        store.errors.add("delete")
        app.forget_token()
        self.wait_until(app, lambda: "недоступно" in app.credential_status.get())
        self.assertEqual(app.token.get(), "")
        self.assertEqual(app.token_entry.cget("state"), "normal")
        self.assertEqual(store.token, "saved-test-token")
        self.assertNotIn("saved-test-token", app.credential_status.get())
        store.errors.clear()
        app.forget_token()
        self.wait_until(app, lambda: self.completed(app, "delete"))
        self.assertEqual(store.token, "")


if __name__ == "__main__":
    unittest.main()
