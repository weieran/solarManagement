import datetime
import logging
import os
import sys
import time
from decimal import *
from enum import Enum
from logging.handlers import RotatingFileHandler
import subprocess


import ShellyPy
import pymodbus
import solaredge_modbus
import yaml
from dateutil.tz import tzlocal
from srf_weather.weather import Weather
from suntime import Sun


# === DEVICE NETWORK CONFIGURATION ===
SHELLY_IP = "192.168.2.78"
SOLAREDGE_MAC = "28:b7:7c:1e:66:67"

# === FILE PATHS ===
SOLAR_JSON_PATH = "/tmp/solar.json"
SOLAR_LOG_PATH = "/tmp/solar.log"
SOLAR_DATA_PATH = "/tmp/solardata.json"



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
        self.device = ShellyPy.Shelly(SHELLY_IP)
        self.charge_time_today_sec = 0
        self.json_data = self._read_or_create_yaml_data(SOLAR_JSON_PATH,
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
        with open(SOLAR_JSON_PATH, 'w') as f:
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

    def set_new_day(self, sachseln: Weather):
        self.log.debug("set new day")

        try:
            forcast = sachseln.get_weather_forecast(Weather.ForecastDuration.day)
            sun_h_today, sun_h_tomorrow = Weather.get_hours_of_sun(forcast)
            self.log.info(f"sun hours today: {sun_h_today}, tomorrow: {sun_h_tomorrow}")

        except Exception as e:
            self.log.error(f"failed to get weather forecast, reason: {e}")

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
        inverter_mac = SOLAREDGE_MAC
        inverter_ip = get_ip_from_mac(inverter_mac)
        if not inverter_ip:
            raise RuntimeError(f"Could not find IP for inverter MAC {inverter_mac}. Is it online?")
        self.log.info(f"Found inverter IP: {inverter_ip} for MAC: {inverter_mac}")

        self.inverter = solaredge_modbus.Inverter(host=inverter_ip, port=1502, timeout=1, retries=1)
        self.meter = solaredge_modbus.Meter(parent=self.inverter, offset=0)

        for attempt in range(10):
            try:
                self.inverter.connect()
                self.meter.connect()
            except pymodbus.exceptions.ConnectionException:
                self.log.error("Could not connect with inverter or meter, try again in 1s")
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
            self.meter.disconnect()
            time.sleep(1)
            self.inverter.connect()
            self.meter.connect()
        except Exception as e:
            self.log.error(f"failed to recover, reason: {e}")

        self.log.debug("recovery done")

    def __del__(self):
        self.inverter.disconnect()
        self.meter.disconnect()


def is_between_1_and_4_am():
    now = datetime.datetime.now(tz=tzlocal())
    return 1 <= now.hour <= 4

def is_between_1_and_4_pm():
    now = datetime.datetime.now(tz=tzlocal())
    return 13 <= now.hour <= 16

def is_between_10_am_and_6_pm():
    'this is the time where we want to charge the boiler with solar power and where the price are anyway low'
    now = datetime.datetime.now(tz=tzlocal())
    return 10 <= now.hour <= 18


def is_night():
    sun = Sun(48.86718056, 8.23343889)
    sunset = sun.get_local_sunset_time()
    sunrise = sun.get_local_sunrise_time()
    actual_local_time = datetime.datetime.now(tz=tzlocal())
    # Make sunrise and sunset timezone-aware if they are naive
    if sunrise.tzinfo is None:
        sunrise = sunrise.replace(tzinfo=tzlocal())
    if sunset.tzinfo is None:
        sunset = sunset.replace(tzinfo=tzlocal())
    is_night = actual_local_time < sunrise or actual_local_time > sunset
    return is_night


def main() -> int:
    logging_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    formatter = logging.Formatter(logging_format)

    logging.basicConfig(format=logging_format, level=logging.DEBUG)
    logger = logging.getLogger("Solar")
    logger.setLevel(level=logging.INFO)

    file_handler = RotatingFileHandler(SOLAR_LOG_PATH, mode='a', maxBytes=5 * 1024 * 1024, backupCount=2)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    pymodus_logger = logging.getLogger('pymodbus.logging')
    pymodus_logger.setLevel(level=logging.INFO)
    pymodus_logger.addHandler(file_handler)
    in_night_time_charging_mode = False
    was_night = is_night()
    logger.info("Start Application")
    try:
        sachseln = Weather(os.environ.get("SRF_METEO_CLIENT_ID"),
                           os.environ.get("SRF_METEO_CLIENT_SECRET"),
                           "Sachseln")
        forcast = sachseln.get_weather_forecast(Weather.ForecastDuration.day)
        sun_h_today, sun_h_tomorrow = Weather.get_hours_of_sun(forcast)
        logger.info(f"sun hours today: {sun_h_today}, tomorrow: {sun_h_tomorrow}")
    except Exception as e:
        logger.error(f"failed to get weather forecast, reason: {e}")

    e = Energy(logger)
    boiler = Boiler(logger)

    logger.info("Start continuous reading")
    try:
        while True:
            if not is_between_10_am_and_6_pm():
                #no reason to do anything, just keep as it is and dont measure anything
                # just sleep for a minute
                time.sleep(60)
            else:  # 'good time to measure and charge if it makes sense
                time.sleep(2)

                if is_between_1_and_4_pm() and not boiler.is_boiler_charged_enough_for_one_day():
                    #if our boiler is not charge enouth to provide heat from the evening and night,
                    # we should charge it during the day, even if we do not have much sun,
                    # because the price are low and we can use all the power we can get.
                    boiler.enable()

                prod, export = e.read()
                write_data_to_json(prod, export)

                if prod is None or export is None:
                    logger.error("invalid reading, do nothing")
                else:
                    logger.debug(f"prod_w: {prod}: export_w:{export} on-time:{boiler.charge_time_today_sec}")

                    # we should make sure that before we enable the boiler
                    # we do not consume too much for something else and have
                    # 3kw Reserve (3kW + a bit of noise)
                    if prod > 3000:
                        if boiler.enable():
                            logger.info(f"Enable: prod_w: {prod}: export_w:{export}")
                    if export >= -1000:  # we import more than 1kW, so we should not charge the boiler
                        if boiler.disable():
                            logger.info(f"Disable: prod_w: {prod}: export_w:{export}")


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


# write the produced and consumed energy to solardata.json and rotate it if bigger then 5MB
def write_data_to_json(production_w, export_w):
    timestamp = datetime.datetime.now().isoformat()
    line = f"{timestamp}, prod[W]:{production_w}, export[W]:{export_w}\n"
    # Write to main data file
    with open(SOLAR_DATA_PATH, 'a') as f:
        f.write(line)
        f.flush()
        if os.path.getsize(SOLAR_DATA_PATH) > 10 * 1024 * 1024:
            os.rename(SOLAR_DATA_PATH, SOLAR_DATA_PATH + ".old")


def get_ip_from_mac(mac_address):
    """Return the IP address for a given MAC address by parsing the ARP table."""
    try:
        # Use 'ip neigh' for modern systems
        output = subprocess.check_output(['ip', 'neigh'], encoding='utf-8')
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[4].lower() == mac_address.lower():
                return parts[0]
    except Exception as e:
        pass
    return None


if __name__ == '__main__':
    sys.exit(main())
