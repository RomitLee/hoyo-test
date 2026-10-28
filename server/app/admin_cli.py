"""Grant or revoke the private site-admin role from the server console."""

from __future__ import annotations

import argparse

from .config import Settings
from .database import RelayDatabase


def main() -> None:
    parser = argparse.ArgumentParser(description="管理总后台账号权限")
    parser.add_argument("action", choices=("grant", "revoke"))
    parser.add_argument("username", help="已注册的用户名")
    args = parser.parse_args()
    database = RelayDatabase(Settings.from_env().database_path)
    database.initialize()
    if not database.set_admin(args.username, args.action == "grant"):
        parser.error("找不到该账号")
    print(f"已{'授予' if args.action == 'grant' else '撤销'} {args.username} 的总后台权限")


if __name__ == "__main__":
    main()
