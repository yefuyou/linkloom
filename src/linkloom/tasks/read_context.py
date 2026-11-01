"""Request-local reuse of immutable reads, restricted to one SQLite transaction."""
from contextlib import contextmanager
from contextvars import ContextVar


_current = ContextVar('task_request_reads', default=None)


@contextmanager
def request_scope(store):
    active = _current.get()
    if active is not None and active[0] is store:
        yield
        return
    token = _current.set((store, {}))
    try:
        yield
    finally:
        _current.reset(token)


def memo(connection, key, read):
    active = _current.get()
    if active is None or active[0]._connection is not connection or not connection.in_transaction:
        return read()
    transaction = getattr(active[0], '_read_transaction_token', None)
    if transaction is None:
        return read()
    cache = active[1]
    key = (transaction, key)
    if key not in cache:
        try:
            cache[key] = (False, read())
        except Exception as error:
            cache[key] = (True, error)
    failed, value = cache[key]
    if failed:
        raise value.with_traceback(None)
    return value