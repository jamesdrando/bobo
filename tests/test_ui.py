from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bobo.chat.service import ChatService
from bobo.chat.store import ChatStore
from bobo.providers.base import ProviderRegistry
from bobo.ui import ChatLaunchOptions, TEXTUAL_AVAILABLE, run_chat_app
from bobo.workspace import load_workspace_settings, resolve_chat_storage_dir


class UISmokeTests(unittest.TestCase):
    def test_run_chat_app_requires_textual_dependency(self) -> None:
        if TEXTUAL_AVAILABLE:
            self.skipTest("Textual is installed; dependency guard path is not active.")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = load_workspace_settings(root)
            store = ChatStore(root, resolve_chat_storage_dir(root, settings))
            service = ChatService(store, settings, registry=ProviderRegistry())

            with self.assertRaisesRegex(ValueError, "requires textual"):
                run_chat_app(service, ChatLaunchOptions())
