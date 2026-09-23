"""Application-wide constants.

Values that never vary across environments and never come from the user or
a server. Anything configurable belongs in :mod:`app.config.settings`.
"""

APP_NAME = "Employee Monitoring"
APP_NAME_SHORT = "EmployeeMonitoring"
ORG_NAME = "EmployeeMonitoring Inc."

PRODUCT_DIR_NAME = "EmployeeMonitoring"

# Per-user storage layout beneath the data directory (docs/DATA_MODEL.md).
DATABASE_DIR = "database"
DB_FILENAME = "agent.db"
SCREENSHOTS_DIR = "screenshots"
SCREENSHOTS_PENDING_DIR = "screenshots/pending"
SCREENSHOTS_PROCESSING_DIR = "screenshots/processing"
LOGS_DIR = "logs"
LOG_FILENAME = "agent.log"
CACHE_DIR = "cache"
CONFIG_DIR = "config"

# Dev-only token file name (explicit "dev-file" credential backend only —
# never a default; production uses the OS keyring, docs/SECURITY_PRIVACY.md).
AUTH_TOKEN_FILENAME = "auth.token"  # noqa: S105 -- filename, not a credential

# Logging rotation: 5 MiB per file, 5 backups (docs/SECURITY_PRIVACY.md).
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# Screenshot / idle defaults and floors (docs/TESTING_AND_DOD.md).
MIN_SCREENSHOT_INTERVAL_SECONDS = 60
DEFAULT_SCREENSHOT_INTERVAL_SECONDS = 60
DEFAULT_IDLE_THRESHOLD_SECONDS = 300

# Exponential backoff schedule (docs/ENGINEERING_RULES.md § Retry strategy).
RETRY_BACKOFF_SECONDS: tuple[int, ...] = (0, 30, 120, 300, 900)

APP_VERSION = "0.1.0"
