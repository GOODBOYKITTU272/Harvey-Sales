"""Shared test setup.

Brain's Claude path checks the installed CLI's ``--help`` before sending a
prompt (harvey/claude_runtime.py). Unit tests stub the subprocess itself, so
they also get a stand-in CLI here: found as plain ``claude``, with a help
text listing every isolation flag. Tests of the check itself override these,
and ``@pytest.mark.real_claude_cli`` tests use the real installed CLI. This
lives in tests/ only; the shipped package has no way to skip the check.
"""

import pytest

from harvey import claude_runtime

FAKE_HELP = "\n".join(f"  {flag} <value>   test" for flag in claude_runtime.REQUIRED_FLAGS)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_claude_cli: runs the installed Claude CLI (no stand-in)")


@pytest.fixture(autouse=True)
def _private_dashboard_token(tmp_path_factory):
    """Never create or read the checkout's real data/dashboard-token."""
    import harvey.dashboard as dash

    dash.token_store.configure(tmp_path_factory.mktemp("token") / "dashboard-token")
    yield
    dash.token_store.configure(dash.TOKEN_FILE)


@pytest.fixture(autouse=True)
def _stand_in_claude_cli(request, monkeypatch):
    claude_runtime.clear_cache()
    if request.node.get_closest_marker("real_claude_cli"):
        yield
        claude_runtime.clear_cache()
        return
    monkeypatch.setattr(claude_runtime, "find_claude", lambda env=None: "claude")
    monkeypatch.setattr(claude_runtime, "_probe_help", lambda exe: FAKE_HELP)
    yield
    claude_runtime.clear_cache()


import harvey.humanize  # noqa: E402


@pytest.fixture(autouse=True)
def _no_installed_humanizer(monkeypatch, tmp_path):
    """Tests never pick up the operator's real ~/.claude Humanizer skill."""
    monkeypatch.setattr(harvey.humanize, "SKILL_PATH", tmp_path / "no-humanizer.md")


@pytest.fixture(autouse=True)
def _mock_mx_resolution(monkeypatch, request):
    """Default MX resolver in test suite: provides a mock mail server for fake test
    domains so offline unit tests don't fail DNS lookups. Modules testing MX
    resolution (test_email_finder) or explicit DNS blocks override this."""
    from harvey.integrations import email_finder
    if "test_email_finder" in request.node.nodeid:
        yield
        return

    async def _stub_mx(domain):
        clean = (domain or "").strip().lower()
        if clean in ("dead.invalid", "nonexistent.example", "invalid.test"):
            return None
        return "mail." + clean

    monkeypatch.setattr(email_finder, "get_mx_host", _stub_mx)
    yield

