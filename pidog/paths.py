from os import getlogin, path

from platformdirs import user_config_dir

from .file_util import resolve_absolute_path

USER = getlogin()
USER_HOME = path.expanduser(f"~{USER}")

CONFIG_USER_DIR = user_config_dir()
PIDOG_CONFIG_DIR = path.join(CONFIG_USER_DIR, "pidog")

CURRENT_DIR = path.dirname(path.realpath(__file__))

PROJECT_DIR = path.dirname(CURRENT_DIR)

config_file = path.join(PIDOG_CONFIG_DIR, "pidog.conf")
DEFAULT_SOUNDS_DIR = resolve_absolute_path("sounds", PROJECT_DIR)
