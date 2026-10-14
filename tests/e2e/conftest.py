"""End-to-end test infrastructure (Playwright, real browser).

Run:  E2E=1 .venv/bin/pytest tests/e2e -q        (or: make e2e)
Skipped by default (E2E=1 not set, or playwright not installed), so
`make test` is unaffected.

The test session starts a dedicated server: temporary DATA_DIR ->
`python -m app.cli demo` (sample company, ~1 year of transactions) ->
`tests/e2e/setup_data.py` (members for every role, another company,
AI assistant pointed at the fake LLM) -> uvicorn on a free port.
Each test gets a new browser context; JS errors (pageerror, console
error) and responses >= 500 FAIL the test unless it is marked
`@pytest.mark.allow_console_errors`.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
ENABLED = os.environ.get("E2E") == "1"

try:
    import playwright  # noqa: F401

    HAVE_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    HAVE_PLAYWRIGHT = False


def pytest_collection_modifyitems(config, items):
    reason = None
    if not ENABLED:
        reason = "E2E disabled: run with E2E=1 pytest tests/e2e (or make e2e)"
    elif not HAVE_PLAYWRIGHT:
        reason = "playwright is not installed (uv pip install playwright)"
    for item in items:
        if "tests/e2e/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.e2e)
            if reason:
                item.add_marker(pytest.mark.skip(reason=reason))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------- server


class Env:
    """E2E environment info shared by the tests."""

    def __init__(self, base_url: str, data: dict, data_dir: Path, llm):
        self.base_url = base_url
        self.data = data
        self.data_dir = data_dir
        self.llm = llm
        self.password = data["password"]
        self.users = data["users"]
        self._states: dict[str, str] = {}

    def url(self, path: str) -> str:
        return self.base_url + path


@pytest.fixture(scope="session")
def e2e_env(tmp_path_factory):
    from tests.e2e.fake_llm_server import FakeLLMServer

    data_dir = tmp_path_factory.mktemp("e2e") / "data"
    env = dict(
        os.environ,
        DATA_DIR=str(data_dir),
        SECRET_KEY="e2e-secret-key-0123456789-abcdefghijkl",
        COOKIE_SECURE="false",
        ALLOW_SIGNUP="false",
        EINVOICE_SIMULATOR="true",
        PYTHONPATH=str(ROOT),
        VFAS_TOLERANT_FEATURES="0",
    )
    env.pop("DATABASE_URL", None)
    llm = FakeLLMServer().start()
    subprocess.run(
        [sys.executable, "-m", "app.cli", "demo"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        timeout=300,
    )
    out = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests" / "e2e" / "setup_data.py"),
            llm.url,
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    data = json.loads(out.stdout.strip().splitlines()[-1])
    port = _free_port()
    log = open(data_dir.parent / "server.log", "w")  # noqa: SIM115
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError(
                "E2E server failed to start: "
                + (data_dir.parent / "server.log").read_text()
            )
        time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("E2E server does not answer /healthz")
    yield Env(base, data, data_dir, llm)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()
    llm.stop()


# -------------------------------------------------------------- browser


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            br = p.chromium.launch(headless=True)
        except Exception:
            # Playwright's chromium is not downloaded -> use the locally
            # installed Chrome
            br = p.chromium.launch(channel="chrome", headless=True)
        yield br
        br.close()


IGNORED_CONSOLE = (
    re.compile(
        r"Failed to load resource: the server responded with a status of 4\d\d"
    ),
)


class Session:
    """A browser context + page that records JS and server errors."""

    def __init__(
        self, env: Env, browser, *, viewport=None, storage_state=None, **ctx_kw
    ):
        self.env = env
        self.context = browser.new_context(
            viewport=viewport or {"width": 1440, "height": 900},
            locale="vi-VN",
            storage_state=storage_state,
            **ctx_kw,
        )
        self.context.set_default_timeout(15000)
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.page.on(
            "pageerror", lambda e: self.errors.append(f"pageerror: {e}")
        )
        self.page.on("console", self._console)
        self.page.on("response", self._response)

    def _console(self, msg) -> None:
        if msg.type == "error" and not any(
            p.search(msg.text) for p in IGNORED_CONSOLE
        ):
            self.errors.append(f"console: {msg.text}")

    def _response(self, resp) -> None:
        if resp.status >= 500:
            self.errors.append(f"HTTP {resp.status}: {resp.url}")

    # -- helpers
    def goto(self, path: str, **kw):
        kw.setdefault("wait_until", "networkidle")
        return self.page.goto(self.env.url(path), **kw)

    def get(self, path: str):
        """GET via the context's API request (shared cookies).

        Used to download PDF/Excel/XML files.
        """
        return self.context.request.get(self.env.url(path))

    def main_text(self) -> str:
        return self.page.locator("main").inner_text()

    def close(self) -> None:
        self.context.close()


@pytest.fixture()
def new_session(e2e_env, browser, request):
    """new_session(email=None, **kw) -> Session, logged in if email.

    Checks for errors automatically when the test ends.
    """
    sessions: list[Session] = []

    def make(email: str | None = None, **kw) -> Session:
        state = None
        if email:
            state = e2e_env._states.get(email)
            if state is None:
                s = Session(e2e_env, browser)
                login(s, email, e2e_env.password)
                state = str(
                    e2e_env.data_dir.parent
                    / f"state-{re.sub(r'[^a-z0-9]', '_', email)}.json"
                )
                s.context.storage_state(path=state)
                s.close()
                e2e_env._states[email] = state
        s = Session(e2e_env, browser, storage_state=state, **kw)
        sessions.append(s)
        return s

    yield make
    allow = request.node.get_closest_marker("allow_console_errors") is not None
    errors = [e for s in sessions for e in s.errors]
    for s in sessions:
        s.close()
    if not allow:
        assert not errors, "Browser errors:\n" + "\n".join(errors)


@pytest.fixture()
def owner(new_session, e2e_env):
    return new_session(e2e_env.users["owner"])


@pytest.fixture()
def accountant(new_session, e2e_env):
    return new_session(e2e_env.users["accountant"])


def login(s: Session, email: str, password: str) -> None:
    s.goto("/login")
    s.page.fill("input[name=email]", email)
    s.page.fill("input[name=password]", password)
    s.page.click("button[type=submit]")
    s.page.wait_for_load_state("networkidle")
    assert "/login" not in s.page.url, f"Could not log in as {email}"


def pick(s: Session, locator, text: str) -> None:
    """Pick the first combobox option with the keyboard, like a user.

    Types into the combobox, then presses Down and Enter.
    """
    locator.click()
    locator.fill(text)
    s.page.wait_for_timeout(400)
    s.page.wait_for_load_state("networkidle")
    locator.press("ArrowDown")
    locator.press("Enter")
    s.page.wait_for_timeout(150)
