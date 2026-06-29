import logging
from io import StringIO

from core import logger as logger_mod


def test_setup_logging_uses_openaver_log_dir_env(tmp_path, monkeypatch):
    log_dir = tmp_path / "custom-logs"
    monkeypatch.setenv("OPENAVER_LOG_DIR", str(log_dir))
    monkeypatch.setattr(logger_mod, "_initialized", False)
    monkeypatch.setattr(logger_mod, "_log_dir", None)
    monkeypatch.setattr(logger_mod.sys, "stdout", StringIO())

    root_logger = logging.getLogger("OpenAver")
    old_handlers = list(root_logger.handlers)
    root_logger.handlers.clear()

    try:
        logger_mod.setup_logging()

        assert logger_mod._log_dir == log_dir
        assert (log_dir / "debug.log").exists()
    finally:
        new_handlers = [handler for handler in root_logger.handlers if handler not in old_handlers]
        for handler in new_handlers:
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.handlers[:] = old_handlers
        logger_mod._initialized = False
