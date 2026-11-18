import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bot_handlers import fake  # noqa: E402,F401
