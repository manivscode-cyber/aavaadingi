from pathlib import Path
import runpy


PROJECT_APP = Path(__file__).resolve().parent / "tickets-main" / "app.py"

if not PROJECT_APP.exists():
    raise FileNotFoundError(f"Could not find app file at {PROJECT_APP}")

runpy.run_path(str(PROJECT_APP), run_name="__main__")
