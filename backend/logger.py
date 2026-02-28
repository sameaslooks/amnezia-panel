import logging
import os
import sys

def setup_logger():
    """Настраивает корневой логгер согласно переменной окружения DEBUG."""
    debug = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")
    level = logging.DEBUG if debug else logging.INFO

    # Создаем обработчик для вывода в stdout
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)

    # Настраиваем корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # Удаляем существующие обработчики (если есть), чтобы избежать дублирования
    if root_logger.handlers:
        root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Устанавливаем уровень для библиотек (опционально)
    logging.getLogger("asyncssh").setLevel(logging.WARNING)

    return root_logger

# Создаём логгер для использования в других модулях
logger = logging.getLogger("amnezia_panel")