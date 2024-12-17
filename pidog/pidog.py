import logging
import multiprocessing as mp
import os
import threading
from math import acos, atan, atan2, cos, pi, sin, sqrt
from time import sleep, time
from typing import List, Union

import numpy as np
from robot_hat import Battery, Music, Pin, Robot, Ultrasonic, reset_mcu_sync
from robot_hat.pin import Pin
from robot_hat.robot import Robot

from .dual_touch import DualTouch
from .paths import DEFAULT_SOUNDS_DIR, config_file
from .rgb_strip import RGBStrip
from .sh3001 import Sh3001
from .sound_direction import SoundDirection

''' servos order
                     4,
                   5, '6'
                     |
              3,2 --[ ]-- 7,8
                    [ ]
              1,0 --[ ]-- 10,11
                     |
                    '9'
                    /

    legs pins: [2, 3, 7, 8, 0, 1, 10, 11]
        left front leg, left front leg
        right front leg, right front leg
        left hind leg, left hind leg,
        right hind leg, right hind leg,

    head pins: [4, 6, 5]
        yaw, roll, pitch

    tail pin: [9]
'''

logger = logging.getLogger(__name__)


def compare_version(original_version, object_version):
    or_v = tuple(int(val) for val in original_version.split('.'))
    ob_v = tuple(int(val) for val in object_version.split('.'))
    return or_v >= ob_v


if compare_version(np.__version__, '2.0.0'):

    def numpy_mat(data):
        return np.asmatrix(data)

else:

    def numpy_mat(data):
        if isinstance(data, np.ndarray):
            return np.matrix(data)
        return np.matrix(data)


class Pidog:

    # structure constants
    LEG = 42
    FOOT = 76
    BODY_LENGTH = 117
    BODY_WIDTH = 98
    BODY_STRUCT = numpy_mat(
        [
            [-BODY_WIDTH / 2, -BODY_LENGTH / 2, 0],
            [BODY_WIDTH / 2, -BODY_LENGTH / 2, 0],
            [-BODY_WIDTH / 2, BODY_LENGTH / 2, 0],
            [BODY_WIDTH / 2, BODY_LENGTH / 2, 0],
        ]
    ).T
    SOUND_DIR = DEFAULT_SOUNDS_DIR
    # Servo Speed
    # HEAD_DPS = 300
    # LEGS_DPS = 350
    # TAIL_DPS = 500
    HEAD_DPS = 300  # dps, degrees per second
    LEGS_DPS = 428
    TAIL_DPS = 500
    # PID Constants
    KP = 0.033
    KI = 0.0
    KD = 0.0
    # Left Front Leg, Left Front Leg, Right Front Leg, Right Front Leg, Left Hind Leg, Left Hind Leg, Right Hind Leg, Right Hind Leg
    DEFAULT_LEGS_PINS = [2, 3, 7, 8, 0, 1, 10, 11]
    # Head Yaw, Roll, Pitch
    DEFAULT_HEAD_PINS = [4, 6, 5]
    DEFAULT_TAIL_PIN = [9]

    HEAD_PITCH_OFFSET = 45

    HEAD_YAW_MIN = -90
    HEAD_YAW_MAX = 90
    HEAD_ROLL_MIN = -70
    HEAD_ROLL_MAX = 70
    HEAD_PITCH_MIN = -45
    HEAD_PITCH_MAX = 30

    # init
    def __init__(
        self,
        leg_pins=DEFAULT_LEGS_PINS,
        head_pins=DEFAULT_HEAD_PINS,
        tail_pin=DEFAULT_TAIL_PIN,
        leg_init_angles=None,
        head_init_angles=None,
        tail_init_angle=None,
    ):

        reset_mcu_sync()
        from .actions_dictionary import ActionDict

        self.actions_dict = ActionDict()
        self.battery_service = Battery("A4")

        self.body_height = 80
        self.pose = numpy_mat([0.0, 0.0, self.body_height]).T  # target position vector
        self.rpy = (
            np.array([0.0, 0.0, 0.0]) * pi / 180
        )  # Euler angle, converted to radian value
        self.leg_point_struc = numpy_mat(
            [
                [-self.BODY_WIDTH / 2, -self.BODY_LENGTH / 2, 0],
                [self.BODY_WIDTH / 2, -self.BODY_LENGTH / 2, 0],
                [-self.BODY_WIDTH / 2, self.BODY_LENGTH / 2, 0],
                [self.BODY_WIDTH / 2, self.BODY_LENGTH / 2, 0],
            ]
        ).T
        self.pitch = 0
        self.roll = 0

        self.coord_temp = None

        self.roll_last_error = 0
        self.roll_error_integral = 0
        self.pitch_last_error = 0
        self.pitch_error_integral = 0
        self.target_rpy = [0, 0, 0]

        if leg_init_angles == None:
            leg_init_angles = self.actions_dict['lie'][0][0]
        if head_init_angles == None:
            head_init_angles = [0, 0, self.HEAD_PITCH_OFFSET]
        else:
            head_init_angles[2] += self.HEAD_PITCH_OFFSET
            # head_init_angles = [0]*3
        if tail_init_angle == None:
            tail_init_angle = [0]

        self.thread_list = []

        try:
            logger.info(f"config_file: {config_file}")
            self.legs = Robot(
                pin_list=leg_pins,
                name='legs',
                init_angles=leg_init_angles,
                init_order=[0, 2, 4, 6, 1, 3, 5, 7],
                db=config_file,
            )
            self.head = Robot(
                pin_list=head_pins,
                name='head',
                init_angles=head_init_angles,
                db=config_file,
            )
            self.tail = Robot(
                pin_list=tail_pin,
                name='tail',
                init_angles=tail_init_angle,
                db=config_file,
            )
            # add thread
            self.thread_list.extend(["legs", "head", "tail"])
            # via
            self.legs.max_dps = self.LEGS_DPS
            self.head.max_dps = self.HEAD_DPS
            self.tail.max_dps = self.TAIL_DPS

            self.legs_action_buffer = []
            self.head_action_buffer = []
            self.tail_action_buffer = []

            self.legs_thread_lock = threading.Lock()
            self.head_thread_lock = threading.Lock()
            self.tail_thread_lock = threading.Lock()

            self.legs_actions_coords_buffer = []

            self.leg_current_angles = leg_init_angles
            self.head_current_angles = head_init_angles
            self.tail_current_angles = tail_init_angle

            self.legs_speed = 90
            self.head_speed = 90
            self.tail_speed = 90

            # done
            logger.info("done")
        except OSError:
            logger.error("fail")
            raise OSError("rotbot_hat I2C init failed. Please try again.")

        try:
            logger.info("imu_sh3001 init ... ")
            self.imu = Sh3001(db=config_file)
            self.imu_acc_offset = [0.0, 0.0, 0.0]
            self.imu_gyro_offset = [0.0, 0.0, 0.0]
            self.accData: List[Union[float, int]] = [0.0, 0.0, 0.0]  # ax,ay,az
            self.gyroData = [0.0, 0.0, 0.0]  # gx,gy,gz
            self.imu_fail_count = 0
            # add imu thread
            self.thread_list.append("imu")
            logger.info("done")
        except OSError:
            logger.error("fail")

        try:
            logger.info("rgb_strip init ... ")
            self.rgb_thread_run = True
            self.rgb_strip = RGBStrip(addr=0x74, nums=11)
            self.rgb_strip.set_mode('breath', 'black')
            self.rgb_fail_count = 0
            # add rgb thread
            self.thread_list.append("rgb")
            logger.info("done")
        except OSError:
            logger.error("fail")

        try:
            logger.info("dual_touch init ... ")
            self.dual_touch = DualTouch('D2', 'D3')
            self.touch = 'N'
            logger.info("done")
        except:
            logger.error("fail")

        try:
            logger.info("sound_direction init ... ")
            self.ears = SoundDirection()
            # self.sound_direction = -1
            logger.info("done")
        except:
            logger.error("fail")

        try:
            logger.info("sound_effect init ... ")
            self.music = Music()
            logger.info("done")
        except:
            logger.error("fail")

        self.distance = mp.Value('f', -1.0)

        self.sensory_process = None
        self.sensory_lock = mp.Lock()

        self.exit_flag = False
        self.action_threads_start()
        self.sensory_process_start()

    def read_distance(self):
        return round(self.distance.value, 2)

    # action related: legs,head,tail,imu,rgb_strip
    def close_all_thread(self):
        self.exit_flag = True

    def close(self):
        import signal
        import sys

        def handler(signal, frame):
            logger.info('Please wait %s %s', signal, frame)

        signal.signal(signal.SIGINT, handler)

        def _handle_timeout(signum, frame):
            logger.info('Please wait %s %s', signum, frame)
            raise TimeoutError('function timeout')

        timeout_sec = 5
        signal.signal(signal.SIGALRM, _handle_timeout)
        signal.alarm(timeout_sec)

        logger.info('\rStopping and returning to the initial position ... ')

        try:
            if self.exit_flag == True:
                self.exit_flag = False
                self.action_threads_start()

            self.stop_and_lie()
            self.close_all_thread()

            self.legs_thread.join()
            self.head_thread.join()
            self.tail_thread.join()

            if 'rgb' in self.thread_list:
                self.rgb_thread_run = False
                self.rgb_strip_thread.join()
                self.rgb_strip.close()
            if 'imu' in self.thread_list:
                self.imu_thread.join()
            if self.sensory_process != None:
                self.sensory_process.terminate()

            logger.info('Quit')
        except Exception as e:
            logger.error(f'Close logger.error: {e}')
        finally:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.alarm(0)
            sys.exit(0)

    def legs_simple_move(self, angles_list, speed=90):

        tt = time()

        max_delay = 0.05
        min_delay = 0.005

        if speed > 100:
            speed = 100
        elif speed < 0:
            speed = 0

        delay = (100 - speed) / 100 * (max_delay - min_delay) + min_delay

        rel_angles_list = []
        for i in range(len(angles_list)):
            rel_angles_list.append(angles_list[i] + self.legs.offset[i])
        self.legs.servo_write_raw(rel_angles_list)

        tt2 = time() - tt
        delay2 = 0.001 * len(angles_list) - tt2

        if delay2 < -delay:
            delay2 = -delay
        sleep(delay + delay2)

    def legs_switch(self, flag=False):
        self.legs_sw_flag = flag

    def action_threads_start(self):
        # Immutable objects int, float, string, tuple, etc., need to be declared with global
        # Variable object lists, dicts, instances of custom classes, etc., do not need to be declared with global
        if 'legs' in self.thread_list:
            self.legs_thread = threading.Thread(
                name='legs_thread', target=self._legs_action_thread
            )
            self.legs_thread.daemon = True
            self.legs_thread.start()
        if 'head' in self.thread_list:
            self.head_thread = threading.Thread(
                name='head_thread', target=self._head_action_thread
            )
            self.head_thread.daemon = True
            self.head_thread.start()
        if 'tail' in self.thread_list:
            self.tail_thread = threading.Thread(
                name='tail_thread', target=self._tail_action_thread
            )
            self.tail_thread.daemon = True
            self.tail_thread.start()
        if 'rgb' in self.thread_list:
            self.rgb_strip_thread = threading.Thread(
                name='rgb_strip_thread', target=self._rgb_strip_thread
            )
            self.rgb_strip_thread.daemon = True
            self.rgb_strip_thread.start()
        if 'imu' in self.thread_list:
            self.imu_thread = threading.Thread(
                name='imu_thread', target=self._imu_thread
            )
            self.imu_thread.daemon = True
            self.imu_thread.start()

    # legs
    def _legs_action_thread(self):
        while not self.exit_flag:
            try:
                with self.legs_thread_lock:
                    self.leg_current_angles = list.copy(self.legs_action_buffer[0])
                # Release lock after copying data before the next operations
                self.legs.servo_move(self.leg_current_angles, self.legs_speed)
                with self.legs_thread_lock:
                    self.legs_action_buffer.pop(0)
            except IndexError:
                sleep(0.001)
            except Exception as e:
                logger.error(f'\r_legs_action_thread Exception:{e}')
                break

    # head
    def _head_action_thread(self):
        while not self.exit_flag:
            try:
                with self.head_thread_lock:
                    self.head_current_angles = list.copy(self.head_action_buffer[0])
                    self.head_action_buffer.pop(0)
                # Release lock after copying data before the next operations
                _angles = list.copy(self.head_current_angles)
                _angles[0] = self.limit(
                    self.HEAD_YAW_MIN, self.HEAD_YAW_MAX, _angles[0]
                )
                _angles[1] = self.limit(
                    self.HEAD_ROLL_MIN, self.HEAD_ROLL_MAX, _angles[1]
                )
                _angles[2] = self.limit(
                    self.HEAD_PITCH_MIN, self.HEAD_PITCH_MAX, _angles[2]
                )
                _angles[2] += self.HEAD_PITCH_OFFSET
                self.head.servo_move(_angles, self.head_speed)
            except IndexError:
                sleep(0.001)
            except Exception as e:
                logger.error(f'\r_head_action_thread Exception:{e}')
                break

    # tail
    def _tail_action_thread(self):
        while not self.exit_flag:
            try:
                with self.tail_thread_lock:
                    self.tail_current_angles = list.copy(self.tail_action_buffer[0])
                    self.tail_action_buffer.pop(0)
                # Release lock after copying data before the next operations
                self.tail.servo_move(self.tail_current_angles, self.tail_speed)
            except IndexError:
                sleep(0.001)
            except Exception as e:
                logger.error(f'\r_tail_action_thread Exception:{e}')
                break

    # rgb strip
    def _rgb_strip_thread(self):
        while self.rgb_thread_run:
            try:
                self.rgb_strip.show()
                self.rgb_fail_count = 0
            except Exception as e:
                self.rgb_fail_count += 1
                sleep(0.001)
                if self.rgb_fail_count > 10:
                    logger.error(f'\r_rgb_strip_thread Exception:{e}')
                    break

    # IMU

    def _imu_thread(self):
        # imu calibrate
        _ax = 0
        _ay = 0
        _az = 0
        _gx = 0
        _gy = 0
        _gz = 0
        time = 10
        for _ in range(time):
            data = self.imu._sh3001_getimudata()
            if data == False:
                break

            if data:
                self.accData, self.gyroData = data
            _ax += self.accData[0]
            _ay += self.accData[1]
            _az += self.accData[2]
            _gx += self.gyroData[0]
            _gy += self.gyroData[1]
            _gz += self.gyroData[2]
            sleep(0.1)

        self.imu_acc_offset[0] = round(-16384 - _ax / time, 0)
        self.imu_acc_offset[1] = round(0 - _ay / time, 0)
        self.imu_acc_offset[2] = round(0 - _az / time, 0)
        self.imu_gyro_offset[0] = round(0 - _gx / time, 0)
        self.imu_gyro_offset[1] = round(0 - _gy / time, 0)
        self.imu_gyro_offset[2] = round(0 - _gz / time, 0)

        while not self.exit_flag:
            try:

                data = self.imu._sh3001_getimudata()
                if data:
                    self.accData, self.gyroData = data
                else:
                    if data == False:
                        self.imu_fail_count += 1
                        if self.imu_fail_count > 10:
                            logger.error('\r_imu_thread imu data logger.error')
                            break
                    logger.error("IMU data invalid")
                    continue

                self.accData[0] += self.imu_acc_offset[0]
                self.accData[1] += self.imu_acc_offset[1]
                self.accData[2] += self.imu_acc_offset[2]
                self.gyroData[0] += self.imu_gyro_offset[0]
                self.gyroData[1] += self.imu_gyro_offset[1]
                self.gyroData[2] += self.imu_gyro_offset[2]
                ax = self.accData[0]
                ay = self.accData[1]
                az = self.accData[2]
                ay = -ay
                az = -az

                self.pitch = atan(ay / sqrt(ax * ax + az * az)) * 57.2957795
                self.roll = atan(az / sqrt(ax * ax + ay * ay)) * 57.2957795

                self.imu_fail_count = 0
                sleep(0.05)
            except Exception as e:
                self.imu_fail_count += 1
                sleep(0.001)
                if self.imu_fail_count > 10:
                    logger.error(f'\r_imu_thread Exception:{e}')
                    self.exit_flag = True
                    break

    # clear actions buff
    def legs_stop(self):
        with self.legs_thread_lock:
            self.legs_action_buffer.clear()
        self.wait_legs_done()

    def head_stop(self):
        with self.head_thread_lock:
            self.head_action_buffer.clear()
        self.wait_head_done()

    def tail_stop(self):
        with self.tail_thread_lock:
            self.tail_action_buffer.clear()
        self.wait_tail_done()

    def body_stop(self):
        self.legs_stop()
        self.head_stop()
        self.tail_stop()

    # move
    def legs_move(self, target_angles, immediately=True, speed=50):
        if immediately == True:
            self.legs_stop()
        self.legs_speed = speed
        with self.legs_thread_lock:
            self.legs_action_buffer += target_angles

    def head_rpy_to_angle(self, target_yrp, roll_comp=0, pitch_comp=0):
        yaw, roll, pitch = target_yrp
        signed = -1 if yaw < 0 else 1
        ratio = abs(yaw) / 90
        pitch_servo = roll * ratio + pitch * (1 - ratio) + pitch_comp
        roll_servo = -(signed * (roll * (1 - ratio) + pitch * ratio) + roll_comp)
        yaw_servo = yaw
        return [yaw_servo, roll_servo, pitch_servo]

    def head_move(
        self, target_yrps, roll_comp=0, pitch_comp=0, immediately=True, speed=50
    ):
        if immediately == True:
            self.head_stop()
        self.head_speed = speed

        angles = [
            self.head_rpy_to_angle(target_yrp, roll_comp, pitch_comp)
            for target_yrp in target_yrps
        ]

        with self.head_thread_lock:
            self.head_action_buffer += angles

    def head_move_raw(self, target_angles, immediately=True, speed=50):
        if immediately == True:
            self.head_stop()
        self.head_speed = speed
        with self.head_thread_lock:
            self.head_action_buffer += target_angles

    def tail_move(self, target_angles, immediately=True, speed=50):
        if immediately == True:
            self.tail_stop()
        self.tail_speed = speed
        with self.tail_thread_lock:
            self.tail_action_buffer += target_angles

    # ultrasonic
    def _ultrasonic_thread(self, distance_addr, lock):
        while True:
            try:
                with lock:
                    val = round(float(self.ultrasonic.read()), 2)
                    distance_addr.value = val
                sleep(0.01)
            except Exception:
                logger.error('ultrasonic_thread  except', exc_info=True)
                sleep(0.1)
                break

    # sensory_process : ultrasonic
    def sensory_process_work(self, distance_addr, lock):
        try:
            logger.info("ultrasonic init ... ")
            echo = Pin('D0')
            trig = Pin('D1')
            self.ultrasonic = Ultrasonic(trig, echo, timeout=0.017)
            # add ultrasonic thread
            self.thread_list.append("ultrasonic")
            logger.info("done")
        except Exception as e:
            logger.error("fail")
            raise ValueError(e)

        if 'ultrasonic' in self.thread_list:
            ultrasonic_thread = threading.Thread(
                name='ultrasonic_thread',
                target=self._ultrasonic_thread,
                args=(
                    distance_addr,
                    lock,
                ),
            )
            # ultrasonic_thread.daemon = True
            ultrasonic_thread.start()

    def sensory_process_start(self):
        if self.sensory_process != None:
            self.sensory_process.terminate()
        self.sensory_process = mp.Process(
            name='sensory_process',
            target=self.sensory_process_work,
            args=(self.distance, self.sensory_lock),
        )
        self.sensory_process.start()

    # reset: stop, stop_and_lie
    def stop_and_lie(self, speed=85):
        try:
            self.body_stop()
            self.legs_move(self.actions_dict['lie'][0], speed=speed)
            self.head_move_raw([[0, 0, 0]], speed=speed)
            self.tail_move([[0, 0, 0]], speed=speed)
            self.wait_all_done()
            sleep(0.1)
        except Exception as e:
            logger.error(f'\rstop_and_lie logger.error:{e}')

    def speak(self, name, volume=100):
        """
        speak, play audio

        :param name: the file name int the folder(SOUND_DIR)
        :type name: str
        :param volume: volume, 0-100
        :type volume: int
        """
        if os.path.isfile(name):
            self.music.sound_play_threading(name, volume)
        elif os.path.isfile(self.SOUND_DIR + name + '.mp3'):
            self.music.sound_play_threading(self.SOUND_DIR + name + '.mp3', volume)
        elif os.path.isfile(self.SOUND_DIR + name + '.wav'):
            self.music.sound_play_threading(self.SOUND_DIR + name + '.wav', volume)
        else:
            logger.warning(f'No sound found for {name}')
            return False

    def speak_block(self, name, volume=100):
        """
        speak, play audio with block

        :param name: the file name int the folder(SOUND_DIR)
        :type name: str
        :param volume: volume, 0-100
        :type volume: int
        """
        if os.path.isfile(name):
            self.music.sound_play(name, volume)
        elif os.path.isfile(self.SOUND_DIR + name + '.mp3'):
            self.music.sound_play(self.SOUND_DIR + name + '.mp3', volume)
        elif os.path.isfile(self.SOUND_DIR + name + '.wav'):
            self.music.sound_play(self.SOUND_DIR + name + '.wav', volume)
        else:
            logger.warning(f'No sound found for {name}')
            return False

    # calibration
    def set_leg_offsets(self, cali_list, reset_list=None):
        self.legs.set_offset(cali_list)
        if reset_list is None:
            self.legs.reset()
            self.leg_current_angles = [0] * 8
        else:
            self.legs.servo_positions = list.copy(reset_list)

            setattr(self.legs, "leg_current_angles", list.copy(reset_list))
            self.legs.servo_write_all(reset_list)

    def set_head_offsets(self, cali_list):
        self.head.set_offset(cali_list)
        # self.head.reset()
        self.head_move([[0] * 3], immediately=True, speed=80)
        self.head_current_angles = [0] * 3

    def set_tail_offset(self, cali_list):
        self.tail.set_offset(cali_list)
        self.tail.reset()
        self.tail_current_angles = [0]

    # calculate angles and coords

    def set_pose(self, x=None, y=None, z=None):
        if x != None:
            self.pose[0, 0] = float(x)
        if y != None:
            self.pose[1, 0] = float(y)
        if z != None:
            self.pose[2, 0] = float(z)

    def set_rpy(self, roll=None, pitch=None, yaw=None, pid=False):
        if roll is None:
            roll = self.rpy[0]
        if pitch is None:
            pitch = self.rpy[1]
        if yaw is None:
            yaw = self.rpy[2]

        if pid:
            roll_error = self.target_rpy[0] - self.roll
            pitch_error = self.target_rpy[1] - self.pitch

            roll_offset = (
                self.KP * roll_error
                + self.KI * self.roll_error_integral
                + self.KD * (roll_error - self.roll_last_error)
            )
            pitch_offset = (
                self.KP * pitch_error
                + self.KI * self.pitch_error_integral
                + self.KD * (pitch_error - self.pitch_last_error)
            )

            self.roll_error_integral += roll_error
            self.pitch_error_integral += pitch_error
            self.roll_last_error = roll_error
            self.pitch_last_error = pitch_error

            roll_offset = roll_offset / 180.0 * pi
            pitch_offset = pitch_offset / 180.0 * pi

            self.rpy[0] += roll_offset
            self.rpy[1] += pitch_offset
        else:
            self.rpy[0] = roll / 180.0 * pi
            self.rpy[1] = pitch / 180.0 * pi
            self.rpy[2] = yaw / 180.0 * pi

    def set_legs(self, legs_list):
        self.legpoint_struc = numpy_mat(
            [
                [
                    -self.BODY_WIDTH / 2,
                    -self.BODY_LENGTH / 2 + legs_list[0][0],
                    self.body_height - legs_list[0][1],
                ],
                [
                    self.BODY_WIDTH / 2,
                    -self.BODY_LENGTH / 2 + legs_list[1][0],
                    self.body_height - legs_list[1][1],
                ],
                [
                    -self.BODY_WIDTH / 2,
                    self.BODY_LENGTH / 2 + legs_list[2][0],
                    self.body_height - legs_list[2][1],
                ],
                [
                    self.BODY_WIDTH / 2,
                    self.BODY_LENGTH / 2 + legs_list[3][0],
                    self.body_height - legs_list[3][1],
                ],
            ]
        ).T

    # pose and Euler Angle algorithm
    def pose2coords(self):
        roll = self.rpy[0]
        pitch = self.rpy[1]
        yaw = self.rpy[2]

        rotx = numpy_mat(
            [[cos(roll), 0, -sin(roll)], [0, 1, 0], [sin(roll), 0, cos(roll)]]
        )
        roty = numpy_mat(
            [[1, 0, 0], [0, cos(-pitch), -sin(-pitch)], [0, sin(-pitch), cos(-pitch)]]
        )
        rotz = numpy_mat([[cos(yaw), -sin(yaw), 0], [sin(yaw), cos(yaw), 0], [0, 0, 1]])
        rot_mat = rotx * roty * rotz
        AB = numpy_mat(np.zeros((3, 4)))
        for i in range(4):
            AB[:, i] = (
                -self.pose
                - rot_mat * self.BODY_STRUCT[:, i]
                + self.legpoint_struc[:, i]
            )

        body_coor_list = []
        for i in range(4):
            body_coor_list.append(
                [
                    (self.legpoint_struc - AB).T[i, 0],
                    (self.legpoint_struc - AB).T[i, 1],
                    (self.legpoint_struc - AB).T[i, 2],
                ]
            )

        leg_coor_list = []
        for i in range(4):
            leg_coor_list.append(
                [
                    self.legpoint_struc.T[i, 0],
                    self.legpoint_struc.T[i, 1],
                    self.legpoint_struc.T[i, 2],
                ]
            )

        return {"leg": leg_coor_list, "body": body_coor_list}

    def pose2legs_angle(self):
        data = self.pose2coords()
        leg_coor_list = data["leg"]
        body_coor_list = data["body"]
        coords = []
        angles = []

        for i in range(4):
            coords.append(
                [
                    leg_coor_list[i][1] - body_coor_list[i][1],
                    body_coor_list[i][2] - leg_coor_list[i][2],
                ]
            )

        angles = []

        for i, coord in enumerate(coords):

            leg_angle, foot_angle = self.fieldcoord2polar(coord)
            # The left and right sides are opposite
            leg_angle = leg_angle
            foot_angle = foot_angle - 90
            if i % 2 != 0:
                leg_angle = -leg_angle
                foot_angle = -foot_angle
            angles += [leg_angle, foot_angle]

        return angles

    # Pose calculated coord is Field coord, acoord refer to field, not refer to robot
    def fieldcoord2polar(self, coord):
        y, z = coord
        u = sqrt(pow(y, 2) + pow(z, 2))
        cos_angle1 = (self.FOOT**2 + self.LEG**2 - u**2) / (2 * self.FOOT * self.LEG)
        cos_angle1 = min(max(cos_angle1, -1), 1)
        beta = acos(cos_angle1)

        angle1 = atan2(y, z)
        cos_angle2 = (self.LEG**2 + u**2 - self.FOOT**2) / (2 * self.LEG * u)
        cos_angle2 = min(max(cos_angle2, -1), 1)
        angle2 = acos(cos_angle2)
        alpha = angle2 + angle1 + self.rpy[1]

        alpha = alpha / pi * 180
        beta = beta / pi * 180

        return alpha, beta

    @classmethod
    def coord2polar(cls, coord):
        y, z = coord
        u = sqrt(pow(y, 2) + pow(z, 2))
        cos_angle1 = (cls.FOOT**2 + cls.LEG**2 - u**2) / (2 * cls.FOOT * cls.LEG)
        cos_angle1 = min(max(cos_angle1, -1), 1)
        beta = acos(cos_angle1)

        angle1 = atan2(y, z)
        cos_angle2 = (cls.LEG**2 + u**2 - cls.FOOT**2) / (2 * cls.LEG * u)
        cos_angle2 = min(max(cos_angle2, -1), 1)
        angle2 = acos(cos_angle2)
        alpha = angle2 + angle1

        alpha = alpha / pi * 180
        beta = beta / pi * 180

        return alpha, beta

    @classmethod
    def legs_angle_calculation(cls, coords):
        translate_list = []
        for i, coord in enumerate(coords):
            leg_angle, foot_angle = cls.coord2polar(coord)
            leg_angle = leg_angle
            foot_angle = foot_angle - 90
            if i % 2 != 0:
                leg_angle = -leg_angle
                foot_angle = -foot_angle
            translate_list += [leg_angle, foot_angle]

        return translate_list

    # limit
    def limit(self, min, max, x):
        if x > max:
            return max
        elif x < min:
            return min
        else:
            return x

    def do_action(self, action_name, step_count=1, speed=50, pitch_comp=0):
        try:
            actions, part = self.actions_dict[action_name]
            if part == 'legs':
                for _ in range(step_count):
                    self.legs_move(actions, immediately=False, speed=speed)
            elif part == 'head':
                for _ in range(step_count):
                    self.head_move(
                        actions, pitch_comp=pitch_comp, immediately=False, speed=speed
                    )
            elif part == 'tail':
                for _ in range(step_count):
                    self.tail_move(actions, immediately=False, speed=speed)
        except KeyError:
            logger.error("do_action: No such action")
        except Exception as e:
            logger.error(f"do_action:{e}")

    def wait_legs_done(self):
        while not self.is_legs_done():
            sleep(0.001)

    def wait_head_done(self):
        while not self.is_head_done():
            sleep(0.001)

    def wait_tail_done(self):
        while not self.is_tail_done():
            sleep(0.001)

    def wait_all_done(self):
        self.wait_legs_done()
        self.wait_head_done()
        self.wait_tail_done()

    def is_legs_done(self):
        return not bool(len(self.legs_action_buffer) > 0)

    def is_head_done(self):
        return not bool(len(self.head_action_buffer) > 0)

    def is_tail_done(self):
        return not bool(len(self.tail_action_buffer) > 0)

    def is_all_done(self):
        return self.is_legs_done() and self.is_head_done() and self.is_tail_done()

    def get_battery_voltage(self):
        return self.battery_service.get_battery_voltage()
