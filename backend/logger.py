# logger.py
import logging
import os
import sys


def setup_logger():
    """Настраивает корневой логгер согласно переменной окружения DEBUG."""
    debug = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")
    level = logging.DEBUG if debug else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if root_logger.handlers:
        root_logger.handlers.clear()
    root_logger.addHandler(handler)

    logging.getLogger("asyncssh").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    return root_logger


logger = logging.getLogger("amnezia_panel")