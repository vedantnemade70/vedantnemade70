import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import resolve_channels  # noqa: E402


class FakeClient:
    def __init__(self, names):
        self.dialogs = [NS(id=-1000 - i, name=n) for i, n in enumerate(names)]

    async def iter_dialogs(self):
        for d in self.dialogs:
            yield d


def resolve(names, wanted):
    return asyncio.run(resolve_channels(FakeClient(names), wanted))


def test_matches_channel_name_ignoring_case():
    assert resolve(["Family", "TOPG VIP 💎", "News"], ["topg vip"]) == [-1001]


def test_exact_name_beats_partial():
    assert resolve(["TopG VIP Chat", "TopG VIP"], ["topg vip"]) == [-1001]


def test_usernames_and_ids_pass_through():
    assert resolve([], ["@somechannel", -100123]) == ["@somechannel", -100123]


def test_missing_or_ambiguous_name_stops():
    with pytest.raises(SystemExit):
        resolve(["Family"], ["topg vip"])
    with pytest.raises(SystemExit):
        resolve(["TopG VIP 1", "TopG VIP 2"], ["topg vip"])
