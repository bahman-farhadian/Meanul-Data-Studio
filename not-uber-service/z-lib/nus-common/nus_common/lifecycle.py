"""Starting up and shutting down properly.

Two things every long-running service in this stack has to get right:

1. **Do not start generating before the data is there.** `h-bootstrap` sets
   a marker in Redis when the database has been migrated and seeded. Until
   then, the services wait.
2. **Stop when Docker says stop.** Docker sends SIGTERM and waits about ten
   seconds before killing the container. A service that ignores it loses
   whatever it had in flight; a service that notices it can finish the
   current message, commit its position, and exit cleanly.
"""

import signal
import threading
import time
from collections.abc import Callable

from nus_common.logging import get_logger

log = get_logger(__name__)

# The marker h-bootstrap sets as its very last action.
BOOTSTRAP_DONE_KEY = "system:bootstrap:done"


class Shutdown:
    """A flag that turns true when Docker asks the container to stop.

    Use it as the condition of the main loop:

        shutdown = Shutdown()
        while not shutdown.requested:
            do_one_round_of_work()
            shutdown.wait(1.0)
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum: int, _frame: object) -> None:
        log.info("stop requested, finishing current work", extra={"signal": signum})
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep, but wake up at once if a stop is requested.

        Returns True when a stop was requested during the wait. Using this
        instead of time.sleep is what makes a service with a slow loop still
        stop quickly.
        """
        return self._event.wait(seconds)


def wait_for(
    check: Callable[[], bool],
    description: str,
    attempts: int = 60,
    delay_seconds: float = 5.0,
    shutdown: Shutdown | None = None,
) -> None:
    """Wait until `check` returns True, then return. Give up loudly.

    Every attempt is logged, so a service that is waiting says what it is
    waiting for instead of looking frozen. It never waits forever: something
    that is not coming should end as a failed container, not a silent one.
    """
    for attempt in range(1, attempts + 1):
        if shutdown is not None and shutdown.requested:
            raise SystemExit(0)
        try:
            if check():
                log.info("ready", extra={"waiting_for": description})
                return
        except Exception as err:  # noqa: BLE001 - any failure just means "not yet"
            log.debug("check failed", extra={"waiting_for": description, "error": str(err)})

        log.info(
            "waiting",
            extra={"waiting_for": description, "attempt": attempt, "of": attempts},
        )
        if shutdown is not None:
            if shutdown.wait(delay_seconds):
                raise SystemExit(0)
        else:
            time.sleep(delay_seconds)

    raise TimeoutError(f"gave up waiting for {description} after {attempts} attempts")


def wait_for_bootstrap(redis_client, shutdown: Shutdown | None = None) -> None:
    """Block until h-bootstrap has finished preparing the data.

    Every service that generates or consumes activity calls this before its
    main loop. Starting earlier would mean writing trips for drivers that do
    not exist yet.
    """
    wait_for(
        lambda: bool(redis_client.exists(BOOTSTRAP_DONE_KEY)),
        description="h-bootstrap to finish (Redis key system:bootstrap:done)",
        attempts=360,          # bootstrap imports a city map; it takes a while
        delay_seconds=10.0,
        shutdown=shutdown,
    )
