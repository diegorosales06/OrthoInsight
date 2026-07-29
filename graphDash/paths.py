from pathlib import Path

_PI_HOME = Path("/home/sparkrnd")


def log_root() -> Path:
    if _PI_HOME.exists():
        return _PI_HOME / "OrthoInsightLogs"
    return Path.home() / "Downloads" / "OrthoInsightLogs"


def logs_dir() -> Path:
    return log_root() / "logs"


def db_path() -> Path:
    return log_root() / "recordings.db"


def ensure_dirs() -> None:
    logs_dir().mkdir(parents=True, exist_ok=True)
