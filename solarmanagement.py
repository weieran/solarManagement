import datetime
import logging
import sys
import time
from decimal import *
from enum import Enum
from logging.handlers import RotatingFileHandler

import ShellyPy
import pymodbus
import solaredge_modbus
import yaml


# https://www.home-assistant.io/integrations/solaredge_modbus/

class SolarStatus(Enum):
    NOT_CHARGED = 1,
    ACTIVE_DAY = 2,
    ACTIVE_NIGHT = 3,


class BoilerStatus(Enum):
    UNDEF = 0
    ON = 1,
    OFF = 2


# class Modem:
# https://unix.stackexchange.com/questions/122745/how-to-get-address-of-connected-usb-modem
# device = '/sys/devices/platform/soc@0/32c00000.bus/32e50000.usb/ci_hdrc.1/usb2/2-1'
# modem = Modem(device)


class Boiler:
    FULL_CHARGE_TIME_SEC = 3600 * 3
    USAGE_PER_DAY_SEC = FULL_CHARGE_TIME_SEC / 2

    def __init__(self, logger):
        self.log = logger
        self.device = ShellyPy.Shelly("shellypro2pm-ec6260822974.alarm")
        self.charge_time_today_sec = 0
        self.json_data = self._read_or_create_yaml_data('/tmp/solar.json',
                                                        {'version': '1.0',
                                                         'charge_time_yesterday': Boiler.FULL_CHARGE_TIME_SEC,
                                                         'charge_time_today': 0})
        self.charge_time_today_sec = self.json_data['charge_time_today']
        self.charge_time_yesterday_sec = self.json_data['charge_time_yesterday']

        self.is_enabled = False
        self.is_disabled = False
        self.start_time = 0
        self.stop_time = 0

    def write_charge_times_to_tmp_file(self):
        self.json_data['charge_time_today'] = self.charge_time_today_sec
        self.json_data['charge_time_yesterday'] = self.charge_time_yesterday_sec
        with open('/tmp/solar.json', 'w') as f:
            yaml.dump(self.json_data, f, default_flow_style=False, allow_unicode=True)

    def _read_or_create_yaml_data(self, file_path, initial_data=None):
        try:
            with open(file_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            with open(file_path, 'w') as f:
                yaml.dump(initial_data, f, default_flow_style=False, allow_unicode=True)
                return initial_data

    def enable(self) -> bool:
        if not self.is_enabled:
            self.device.relay(0, turn=True)
            self.log.info(f"Enable Boiler: "
                          f"today: {datetime.timedelta(seconds=self.charge_time_today_sec)}, "
                          f"yesterday: {datetime.timedelta(seconds=self.charge_time_yesterday_sec)}")
            self.start_time = time.time()
            self.is_enabled = True
            self.is_disabled = False
            return True
        return False

    def disable(self) -> bool:
        if not self.is_disabled:
            self.stop_time = time.time()
            if self.start_time != 0:
                self.charge_time_today_sec = self.charge_time_today_sec + (self.stop_time - self.start_time)
                self.write_charge_times_to_tmp_file()
            self.log.info(f"Disable Boiler: "
                          f"today: {datetime.timedelta(seconds=self.charge_time_today_sec)}, "
                          f"yesterday: {datetime.timedelta(seconds=self.charge_time_yesterday_sec)}")
            self.device.relay(0, turn=False)
            self.is_enabled = False
            self.is_disabled = True
            return True
        return False

    def set_new_day(self):
        self.log.debug("set new day")
        # if we charged more than 3h, we set it to 3h
        self.charge_time_yesterday_sec = min(self.charge_time_today_sec, Boiler.FULL_CHARGE_TIME_SEC)
        self.charge_time_today_sec = 0
        self.write_charge_times_to_tmp_file()

    def reset_counter(self):
        self.log.debug("reset total elapsed time")
        self.charge_time_today_sec = 0
        self.write_charge_times_to_tmp_file()

    def is_fully_charged(self) -> bool:
        return self.charge_time_today_sec > Boiler.FULL_CHARGE_TIME_SEC

    def is_charged_for_one_day(self) -> bool:
        return self.charge_time_today_sec > Boiler.USAGE_PER_DAY_SEC

    def charge_time_of_last_two_days(self):
        return self.charge_time_today_sec + self.charge_time_yesterday_sec

    def is_boiler_charged_enough_for_one_day(self):
        # we expect that the boiler is only discharged half of the max usage per day
        return self.charge_time_of_last_two_days() >= Boiler.USAGE_PER_DAY_SEC


class Energy:
    def __init__(self, logger):
        self.log = logger
        self.inverter = solaredge_modbus.Inverter(host="192.168.2.10", port=1502, timeout=2, retries=3)
        self.meter = solaredge_modbus.Meter(parent=self.inverter, offset=0)

        for attempt in range(10):
            try:
                self.inverter.connect()
            except pymodbus.exceptions.ConnectionException:
                self.log.error("Could not connect with inverter")
                time.sleep(1)
                continue
            break

    def read(self):
        production_w = None
        export_w = None
        for attempt in range(10):
            try:
                inverter_data = self.inverter.read_all()
                export_w = self.meter.read("power")['power']
            except pymodbus.exceptions.ConnectionException:
                self.log.warning(f"Inverter Read Error ({attempt}), try to reconnect")
                self._try_recover()
                continue
            try:  # maybe we timed-out and the inverter data are not there.
                prod = inverter_data['power_ac']
                prod_scale = inverter_data["power_ac_scale"]
                production_w = Decimal(prod).shift(prod_scale)
            except KeyError:
                self.log.warning(f"Invalid data ({attempt}), try to reconnect")
                self._try_recover()
                continue
            return production_w, export_w
        return production_w, export_w

    def _try_recover(self):
        try:
            self.inverter.disconnect()
            time.sleep(1)
            self.inverter.connect()
        except Exception as e:
            self.log.error(f"failed to recover, reason: {e}")

        self.log.debug("recovery done")


def is_night():
    now = datetime.datetime.now()
    return now.hour < 7 or now.hour > 20


def main() -> int:
    logging_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    formatter = logging.Formatter(logging_format)

    logging.basicConfig(format=logging_format, level=logging.DEBUG)
    logger = logging.getLogger("Solar")
    logger.setLevel(level=logging.INFO)

    file_handler = RotatingFileHandler("/tmp/solar.log", mode='a', maxBytes=5 * 1024 * 1024, backupCount=2)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    pymodus_logger = logging.getLogger('pymodbus.logging')
    pymodus_logger.setLevel(level=logging.INFO)
    pymodus_logger.addHandler(file_handler)

    was_night = is_night()
    logger.info("Start Application")

    e = Energy(logger)
    boiler = Boiler(logger)

    logger.info("Start continuous reading")
    try:
        while True:
            if not is_night():
                if was_night:
                    logger.info("Manager in day mode")
                    boiler.set_new_day()
                    was_night = False

                prod, export = e.read()
                if prod is None or export is None:
                    logger.error("invalid reading, do nothing")
                else:
                    logger.debug(f"prod_w: {prod}: export_w:{export} on-time:{boiler.charge_time_today_sec}")

                    # we should make sure that before we enable the boiler
                    # we do not consume too much for something else and have
                    # 3kw Reserve (3kW + a bit of noise)
                    if not boiler.is_enabled:
                        enable_export_limit = 3500
                    else:
                        enable_export_limit = 500

                    if prod > 3500 and export > enable_export_limit:
                        if boiler.enable():
                            logger.info(f"Enable: prod_w: {prod}: export_w:{export}")

                    if export <= 0:
                        if boiler.disable():
                            logger.info(f"Disable: prod_w: {prod}: export_w:{export}")
            else:  # is night
                if not was_night:
                    was_night = True
                    logger.info("Manager in night mode")

                if boiler.is_boiler_charged_enough_for_one_day():
                    if boiler.disable():
                        logger.info(
                            f"Boiler is charge enough for on more day: {boiler.charge_time_of_last_two_days()}[s]")
                        logger.info("Disable it")
                        logger.info("Manager go to sleep")
                else:
                    if boiler.enable():
                        logger.info("Boiler is not charged, enable it")

            time.sleep(2)
    except KeyboardInterrupt:
        logger.info("Stopper by user")
        boiler.disable()
        boiler.write_charge_times_to_tmp_file()
        return 0
    except Exception as e:
        logger.error(f"Exception: {e}")
        boiler.disable()
        boiler.write_charge_times_to_tmp_file()
        return 1


if __name__ == '__main__':
    sys.exit(main())
