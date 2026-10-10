"""Execute the launcher's actual dependency check, without its installer or launcher."""
from pathlib import Path
import subprocess
import sys


def test_launcher_dependency_check_rejects_wrong_adapter_version(tmp_path):
    script = (Path(__file__).parents[1] / "Start-Tomahawk.ps1").read_text(encoding="utf-8")
    line = next(line for line in script.splitlines() if line.strip().startswith("$check =") and "import sys;" in line)
    code = line.split("=", 1)[1].strip()[1:-1].replace("''", "'")[1:-1]
    good = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    (tmp_path / "ib_async.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")
    bad = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert bad.returncode != 0 and "AssertionError" in bad.stderr
