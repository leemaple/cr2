"""Interactive setup and loopback-only application entry point."""
import argparse
import getpass
import json
import os
import sys

from . import NAME, VERSION
from .config import Settings
from .database import Database
from .security import Auth


def main() -> int:
    parser = argparse.ArgumentParser(description=NAME + " " + VERSION)
    parser.add_argument("command", choices=["setup", "serve", "doctor"])
    parser.add_argument("--username", default="admin")
    arguments = parser.parse_args()
    settings = Settings.from_env()
    settings.prepare()
    database = Database(settings.database)
    database.initialize()
    auth = Auth(database, settings.session_seconds)
    if arguments.command == "setup":
        if auth.is_initialized():
            print("管理员已经初始化，无需重复设置。")
            return 0
        password = os.environ.pop("VECLAB_INIT_PASSWORD", None)
        if password is None:
            password = getpass.getpass("设置管理员口令（至少12字符）: ")
            confirm = getpass.getpass("再次输入口令: ")
            if password != confirm:
                print("两次口令不一致。", file=sys.stderr)
                return 2
        try:
            auth.setup(arguments.username, password)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        finally:
            del password
        print("管理员初始化完成。口令不会打印或写入日志。")
        return 0
    if arguments.command == "doctor":
        from .engines import engine_status
        status = engine_status()
        status["initialized"] = auth.is_initialized()
        status["database_integrity"] = database.one("PRAGMA integrity_check")
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["ckks_available"] else 1
    if not auth.is_initialized():
        print("请先执行 python -m veclab setup。", file=sys.stderr)
        return 2
    import uvicorn
    from .api import create_app
    print(NAME + " " + VERSION)
    print(f"浏览器打开 http://127.0.0.1:{settings.port}")
    print("本地可信工作台：不要录入涉密数据或暴露到公网。")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
