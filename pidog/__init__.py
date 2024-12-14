#!/usr/bin/env python3
from time import sleep

from robot_hat import reset_mcu_sync

from .pidog import Pidog
from .setup_env import setup_env
from .version import __version__

setup_env()


def __main__():
    print(f"Thanks for using Pidog {__version__} ! woof, woof, woof !")
    reset_mcu_sync()
    sleep(0.2)
