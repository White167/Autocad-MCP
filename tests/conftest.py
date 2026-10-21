"""Shared fixtures for AutoCAD MCP tests."""

import pytest
import pytest_asyncio

from backends.ezdxf_backend import EzdxfBackend


@pytest.fixture(autouse=True)
def pin_headless_backend(request, monkeypatch):
    """Every test talks to the headless engine unless it says otherwise.

    This suite runs on developer machines that have AutoCAD installed, and a
    test that opens ``Client(server.mcp)`` without pinning would run the real
    lifespan: ``AUTOCAD_MCP_BACKEND`` unset means ``auto``, which on Windows
    means attaching to the operator's open, unsaved drawing and creating
    entities in it. That has actually happened here. Defaulting the whole suite
    to ``ezdxf`` is the only guard that covers tests nobody has written yet.

    Opt out with ``@pytest.mark.live_backend`` for a test that deliberately
    exercises backend selection.
    """
    if request.node.get_closest_marker("live_backend"):
        return
    monkeypatch.setenv("AUTOCAD_MCP_BACKEND", "ezdxf")


@pytest.fixture(autouse=True)
def reset_discovery_mode():
    """Leave the server's catalog unswapped for the next test.

    ``_apply_discovery_mode("search")`` installs a server-level transform that
    no lifespan undoes, so a test that turned it on and did not turn it off
    left the next test in the same xdist worker listing only
    ``search_tools`` / ``call_tool`` (``test_cad_tool_meta_reaches_the_wire``
    failed with a KeyError, depending on the order). Only when ``server`` is
    already imported: this fixture must not import it for every test.
    """
    yield
    import sys

    server = sys.modules.get("server")
    if server is not None:
        server._apply_discovery_mode("off")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_backend: test drives backend selection itself; do not pin AUTOCAD_MCP_BACKEND",
    )


@pytest_asyncio.fixture
async def backend():
    """Create an ezdxf backend with a fresh empty document."""
    b = EzdxfBackend()
    await b.connect()
    await b.drawing_new()
    yield b
    await b.disconnect()


class WithoutPrivateState:
    """The real backend with its ezdxf-only private attributes taken away.

    ``EzdxfBackend`` tracks the current tab in ``self._current_space``;
    ``ComBackend`` does not -- the live tab lives in AutoCAD
    (``doc.ActiveLayout``), so there is nothing of that name on it. Shared code
    that reaches for the private attribute therefore silently takes the
    ``getattr`` default on every live seat, and no ezdxf-backed test can see
    it. Wrapping the real backend (rather than writing a fake that owns members
    the real object never had) keeps every other call honest.
    """

    def __init__(self, inner, hide=("_current_space",)):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_hidden", frozenset(hide))

    def __getattr__(self, name):
        if name in self._hidden:
            raise AttributeError(f"{type(self._inner).__name__} has no attribute {name!r}")
        return getattr(self._inner, name)


@pytest_asyncio.fixture
async def backend_without_private_state(backend):
    """`backend`, minus the private attributes only the headless engine has."""
    return WithoutPrivateState(backend)
