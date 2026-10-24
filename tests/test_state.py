import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from kepler import config, controller
from kepler.sources.base import SourceData


def test_tick_waits_for_another_process_holding_its_lock(tmp_path):
    cfg = config.from_dict({"sources": [{"name": "crm", "type": "csv", "path": "unused.csv"}]}, tmp_path)
    script = """
import sys
from pathlib import Path
from kepler.state import locked
with locked(Path(sys.argv[1])):
    print("locked", flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(cfg.state_dir / "tick.lock")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    read_started = threading.Event()

    class Source:
        def fetch(self, since, until):
            import datetime as dt

            read_started.set()
            return SourceData(dt.datetime.now().astimezone())

    try:
        assert child.stdout.readline().strip() == "locked"
        with ThreadPoolExecutor(1) as pool:
            tick = pool.submit(controller.run_tick, cfg, built={"crm": Source()})
            try:
                assert not read_started.wait(0.1)
            finally:
                child.communicate("release\n", timeout=5)
            assert tick.result(5)["sources"][0]["status"] == "ok"
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
