#!/usr/bin/env python3
from time import sleep

from robot_hat import Servo
from robot_hat.utils import reset_mcu_sync

reset_mcu_sync()
sleep(1)

if __name__ == '__main__':
    for i in range(12):
        print(f"Servo {i} set to zero")
        Servo(i).angle(10)
        sleep(0.1)
        Servo(i).angle(0)
        sleep(0.1)
    while True:
        sleep(1)
