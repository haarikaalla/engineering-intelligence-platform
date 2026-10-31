import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="eip-tests-"))
os.environ.update({
    "WORKSPACE_DIR": str(_TMP / "workspace"),
    "DATABASE_URL": f"sqlite:///{(_TMP / 'eip.db').as_posix()}",
    "ENABLE_OSV": "false",
    "LLM_PROVIDER": "none",
    "USE_CHROMA": "false",
    "EMBEDDING_PROVIDER": "local",
    "NEO4J_URI": "",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
