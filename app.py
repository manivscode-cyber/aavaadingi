from pathlib import Path
import runpy


PROJECT_APP = Path(__file__).resolve().parent / "tickets-main" / "app.py"

if not PROJECT_APP.exists():
    raise FileNotFoundError(f"Could not find app file at {PROJECT_APP}")

project_globals = runpy.run_path(str(PROJECT_APP))
app = project_globals["app"]
application = app


if __name__ == "__main__":
    app.run(port=5002)
