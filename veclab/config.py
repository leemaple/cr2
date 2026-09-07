"""Explicit local-only runtime configuration."""
from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    session_seconds: int = 28800
    max_body_bytes: int = 2 * 1024 * 1024
    secure_cookie: bool = False

    @property
    def database(self) -> Path:
        return self.data_dir / "workbench.sqlite3"

    @property
    def backups(self) -> Path:
        return self.data_dir / "backups"

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.environ.get("VECLAB_DATA", "data")).resolve()
        return cls(
            data_dir=root,
            host="127.0.0.1",
            port=int(os.environ.get("VECLAB_PORT", "8765")),
            secure_cookie=os.environ.get("VECLAB_HTTPS") == "1",
        )

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            self.data_dir.chmod(0o700)
            self.backups.chmod(0o700)
