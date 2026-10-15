import sys
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import token_store
from token_store import ACCOUNT_NAME, SERVICE_NAME, TokenStoreError


class NativeBackendTests(unittest.TestCase):
    def test_each_supported_platform_selects_its_native_backend_directly(self):
        for platform, module_name, class_name in (
            ("darwin", "keyring.backends.macOS", "Keyring"),
            ("win32", "keyring.backends.Windows", "WinVaultKeyring"),
            ("linux", "keyring.backends.SecretService", "Keyring"),
        ):
            with self.subTest(platform=platform):
                factory = Mock()
                module = SimpleNamespace(**{class_name: factory})
                with patch.object(token_store.sys, "platform", platform), \
                        patch.object(token_store.importlib, "import_module", return_value=module) as imported:
                    self.assertIs(token_store._get_backend(), factory.return_value)
                imported.assert_called_once_with(module_name)
                factory.assert_called_once_with()

    def test_unsupported_platform_does_not_try_other_plugins_or_files(self):
        with patch.object(token_store.sys, "platform", "freebsd13"), \
                patch.object(token_store.importlib, "import_module") as imported:
            with self.assertRaises(TokenStoreError):
                token_store._get_backend()
        imported.assert_not_called()

    def test_optional_missing_dependency_returns_safe_error(self):
        with patch.object(token_store.sys, "platform", "darwin"), \
                patch.object(token_store.importlib, "import_module", side_effect=ImportError("private detail")):
            with self.assertRaises(TokenStoreError) as caught:
                token_store._get_backend()
        self.assertNotIn("private detail", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_unavailable_native_backend_returns_safe_error(self):
        module = SimpleNamespace(Keyring=Mock(side_effect=RuntimeError("private detail")))
        with patch.object(token_store.sys, "platform", "darwin"), \
                patch.object(token_store.importlib, "import_module", return_value=module):
            with self.assertRaises(TokenStoreError) as caught:
                token_store._get_backend()
        self.assertNotIn("private detail", str(caught.exception))


class TokenStoreTests(unittest.TestCase):
    def setUp(self):
        # All credential access is mocked. These tests never touch a user's vault.
        self.backend = Mock()
        backend_patch = patch.object(token_store, "_get_backend", return_value=self.backend)
        self.get_backend = backend_patch.start()
        self.addCleanup(backend_patch.stop)

    def test_vault_identifiers_are_stable_and_independent_of_paths(self):
        self.assertEqual(SERVICE_NAME, "BitbucketPRManager")
        self.assertEqual(ACCOUNT_NAME, "bitbucket-cloud-token")

    def test_load_returns_empty_when_no_token_is_saved(self):
        self.backend.get_password.return_value = None
        self.assertEqual(token_store.load_token(), "")
        self.backend.get_password.assert_called_once_with(SERVICE_NAME, ACCOUNT_NAME)

    def test_load_restores_and_trims_the_saved_token(self):
        self.backend.get_password.return_value = "  example-token\n"
        self.assertEqual(token_store.load_token(), "example-token")

    def test_invalid_stored_value_is_reported_as_read_error(self):
        self.backend.get_password.return_value = 123
        with self.assertRaisesRegex(TokenStoreError, "прочитать"):
            token_store.load_token()

    def test_save_trims_whitespace_and_updates_the_same_record(self):
        token_store.save_token("  example-token\r\n")
        token_store.save_token("replacement-token")
        self.assertEqual(self.backend.set_password.call_args_list, [
            call(SERVICE_NAME, ACCOUNT_NAME, "example-token"),
            call(SERVICE_NAME, ACCOUNT_NAME, "replacement-token"),
        ])

    def test_empty_input_never_replaces_an_existing_token(self):
        for token in ("", " \t\n", None):
            with self.subTest(token=token):
                with self.assertRaisesRegex(TokenStoreError, "Введите токен"):
                    token_store.save_token(token)
        self.get_backend.assert_not_called()
        self.backend.set_password.assert_not_called()

    def test_delete_reads_and_removes_the_saved_token(self):
        self.backend.get_password.return_value = "example-token"
        token_store.delete_token()
        self.backend.get_password.assert_called_once_with(SERVICE_NAME, ACCOUNT_NAME)
        self.backend.delete_password.assert_called_once_with(SERVICE_NAME, ACCOUNT_NAME)

    def test_delete_is_successful_when_no_record_exists(self):
        self.backend.get_password.return_value = None
        token_store.delete_token()
        self.backend.delete_password.assert_not_called()

    def test_delete_removes_an_existing_empty_record_too(self):
        self.backend.get_password.return_value = ""
        token_store.delete_token()
        self.backend.delete_password.assert_called_once_with(SERVICE_NAME, ACCOUNT_NAME)

    def test_read_failure_prevents_deletion_and_is_reported(self):
        self.backend.get_password.side_effect = RuntimeError("private detail")
        with self.assertRaisesRegex(TokenStoreError, "удалить"):
            token_store.delete_token()
        self.backend.delete_password.assert_not_called()

    def test_backend_errors_do_not_leak_secrets_in_message_or_traceback(self):
        for operation, method, args in (
            (token_store.load_token, "get_password", ()),
            (token_store.save_token, "set_password", ("example-token",)),
            (token_store.delete_token, "delete_password", ()),
        ):
            with self.subTest(operation=operation.__name__):
                self.backend.reset_mock(side_effect=True)
                self.backend.get_password.return_value = "example-token"
                getattr(self.backend, method).side_effect = RuntimeError("secret-token-in-backend-error")
                try:
                    operation(*args)
                except TokenStoreError as error:
                    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
                    self.assertNotIn("secret-token-in-backend-error", rendered)
                    self.assertTrue(error.__suppress_context__)
                else:
                    self.fail("Credential-store failure was ignored")

    def test_denied_deletion_is_not_mistaken_for_an_absent_token(self):
        class PasswordDeleteError(Exception):
            pass

        self.backend.get_password.return_value = "example-token"
        self.backend.delete_password.side_effect = PasswordDeleteError("access denied")
        with self.assertRaisesRegex(TokenStoreError, "удалить"):
            token_store.delete_token()


if __name__ == "__main__":
    unittest.main()
