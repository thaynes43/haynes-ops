"""Owner-thread SOURCE health and per-traversal brackets; no database import."""
import contextlib


class SourceHealth:
    def __init__(self, fence, collectors, emit, fields):
        self.fence, self.collectors, self.emit = fence, collectors, emit
        self.fields = dict(fields)
        self.original_health = fence.health
        self.entered = False

    def health(self):
        row = self.original_health()
        self.emit({"type": "fence_healthy", **self.fields,
                   "backend_pid": row["pid"], "share_tables": row["share_tables"],
                   "read_only": row["read_only"]})
        return row

    def scan_guard(self):
        # The published guard invokes this controller's health only when real
        # SQL is due; its original local/thread/connection checks run each time.
        return self.fence.scan_guard()

    @contextlib.contextmanager
    def complete_walks(self):
        original = self.collectors._walk
        def bracketed(*args, **kwargs):
            self.health()
            value = original(*args, **kwargs)
            self.health()
            return value
        self.collectors._walk = bracketed
        try:
            yield
        finally:
            self.collectors._walk = original

    def collect(self, root, deadline, metadata, copies, binding):
        with self.complete_walks():
            return self.collectors.stat_census(root, deadline, metadata, copies,
                                              self.scan_guard, binding)

    def __enter__(self):
        if self.entered:
            raise RuntimeError('SOURCE controller cannot re-enter')
        self.entered = True
        self.fence.health = self.health
        return self

    def __exit__(self, *_):
        self.fence.health = self.original_health
