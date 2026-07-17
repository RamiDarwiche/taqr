import sys
from loguru import logger

logger.remove()

logger.add(
    sys.stderr,
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <light-magenta>{module}</light-magenta> - <level>{message}</level>'",
    level="TRACE",
)

logger.add(
    "taqr.log",
    rotation="5 MB",
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <light-magenta>{module}</light-magenta> - <level>{message}</level>'",
    mode="w",
    level="TRACE",
)
