# !/usr/bin/env python3
''' play sound effecfs
    Note that you need to run with "sudo"
API:
    Pidog.speak(name)
        play sound effecf in the file "../sounds"
        - name    str, file name of sound effect, no suffix required, eg: "angry"

'''
from pidog import Pidog
import os
import time

my_dog = Pidog()

print("\033[033m Note that you need to run with sudo \033[m")

# my_dog.speak("angry")
# time.sleep(2)

for name in os.listdir('../sounds'):
    name = name.split('.')[0] # remove suffix
    print(name)
    my_dog.speak(name)
    time.sleep(3) # Note that the duration of each sound effect is different