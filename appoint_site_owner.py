#!/usr/bin/env python3
"""Appoint the first station owner by an explicitly verified user ID.

This command is intentionally not a general role editor. It is idempotent for
an already-appointed user and refuses to replace a different station owner.
"""

from __future__ import annotations

import argparse

from app import create_app
from app.maintenance import appoint_initial_site_owner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Appoint the first verified station owner by user ID"
    )
    parser.add_argument("user_id", type=int, help="immutable numeric user ID")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        owner = appoint_initial_site_owner(args.user_id)
        print(f"station owner: id={owner.id} username={owner.username}")


if __name__ == "__main__":
    main()
