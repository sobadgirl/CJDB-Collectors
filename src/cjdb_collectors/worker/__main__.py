from __future__ import annotations

import argparse
import signal

from cjdb_collectors.config import load_settings
from cjdb_collectors.db import create_db_engine, migrate_database

from .scheduler import Worker


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    args = parser.parse_args()
    settings = load_settings(args.config)
    migrate_database(settings)
    engine = create_db_engine(settings.app.database_path)
    worker = Worker(settings, db_engine=engine)
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())
    signal.signal(signal.SIGINT, lambda *_: worker.stop())
    worker.run_forever()


if __name__ == "__main__":
    main()
