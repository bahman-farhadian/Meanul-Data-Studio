"""A health check may not reach the stack through a host loopback address.

Every curl/wget in a Makefile must either run inside a container
(`$(COMPOSE) exec ...`, where localhost is that container's own loopback and
always correct) or address a compose service on nus-backbone. A host-side
loopback call is a latent failure: Docker's published-port DNAT cannot send
a 127.0.0.1 destination out to a real interface unless route_localnet is 1,
so docker-proxy accepts the connection and closes it without a reply.

This is not theoretical. tiles-health curled 127.0.0.1 and reported a tiles
failure against a stack whose tiles were healthy - the same request returned
200 through the same HAProxy from ::1, from the LAN address, from the
container IP, and from inside the network. _wait-haproxy-pg-settled had the
same shape and survived only because localhost happened to resolve to ::1 on
that host; on a host that resolves localhost to 127.0.0.1 first it would
have failed the bootstrap it exists to protect.

Host addresses belong in `make urls`, which enumerates the host's real
addresses and never offers loopback.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# this file lives at not-uber-service/z-lib/nus-common/tests/
NUS = TESTS.parents[2]

LOOPBACK = re.compile(r"127\.0\.0\.1|\[::1\]")
FETCHES = re.compile(r"\b(curl|wget)\b")


def _makefiles() -> list[Path]:
    assert NUS.name == "not-uber-service", NUS
    found = [NUS / "Makefile"] + sorted(NUS.glob("*/Makefile"))
    assert len(found) > 5, f"expected the component Makefiles under {NUS}"
    return [p for p in found if p.is_file()]


def _fetch_lines() -> list[tuple[Path, int, str]]:
    """Every Makefile line that actually invokes curl or wget."""
    lines: list[tuple[Path, int, str]] = []
    for path in _makefiles():
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if FETCHES.search(line):
                lines.append((path, number, line))
    return lines


def test_no_health_check_uses_a_host_loopback_literal():
    offenders = [
        f"{path.relative_to(NUS)}:{number}"
        for path, number, line in _fetch_lines()
        if LOOPBACK.search(line)
    ]
    assert not offenders, (
        "curl/wget against a host loopback literal: "
        + ", ".join(offenders)
        + " - address the compose service through nus-backbone instead"
    )


def test_localhost_fetches_only_run_inside_a_container():
    offenders = [
        f"{path.relative_to(NUS)}:{number}"
        for path, number, line in _fetch_lines()
        if "localhost" in line and "exec" not in line
    ]
    assert not offenders, (
        "curl/wget to localhost outside a container: "
        + ", ".join(offenders)
        + " - that is the host's loopback, not the service's"
    )


def test_the_guard_can_actually_see_the_makefiles():
    """A regex that silently matches nothing would pass both tests above."""
    assert _fetch_lines(), "found no curl/wget lines at all - the guard is blind"
