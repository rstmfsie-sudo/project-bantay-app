import os
import tempfile

# Keep tests out of the real data/ folder; must run before backend.config is imported.
os.environ.setdefault("BANTAY_DATA", tempfile.mkdtemp(prefix="bantay-test-"))
