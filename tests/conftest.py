"""Close every user database and converter pool a test opened.

Tests make apps with ``TestClient(app)`` (not ``with TestClient(app)``), so the
app's shutdown never runs. Without this, each test leaves its SQLite connection
and process pool open, and the run fails with "Too many open files" under
macOS's default limit of 256.
"""

import pytest

_opened: list = []


def _track(cls):
    init = cls.__init__

    def __init__(self, *args, **kwargs):
        init(self, *args, **kwargs)
        _opened.append(self)

    cls.__init__ = __init__


try:
    from pdf2phone.web.jobs import JobQueue
    from pdf2phone.web.users import UserStore
except ImportError:  # the web extra isn't installed; those tests are skipped
    pass
else:
    _track(UserStore)
    _track(JobQueue)


@pytest.fixture(autouse=True)
def _close_what_the_test_opened():
    yield
    # Queues first: a conversion still running may write to the user store.
    for thing in sorted(_opened, key=lambda t: not isinstance(t, JobQueue)):
        thing.close()
    _opened.clear()
