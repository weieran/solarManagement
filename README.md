# solarManagement

# Remote connection to the device

Using serveo.net as a jump-host for the ssh tunnel

1. send sms to my device to temporary open a jump host
   2. the device will activate the jump-host tunnel
   3. ssh -J serveo.net user@myalias
2. connect to the jump host
   ssh -J serveo.net root@myalias -i mykey

# Solar Management System

This project provides an automated management system for solar energy and boiler control, integrating with a SolarEdge inverter and a Shelly smart relay. The main script, `solarmanagement.py`, monitors solar production, manages boiler charging, and logs energy data for analysis and troubleshooting.

## What `solarmanagement.py` Does
- Connects to a SolarEdge inverter (via Modbus TCP) to read solar production and export data.
- Connects to a Shelly smart relay to control a boiler based on solar energy availability and time of day.
- Uses weather forecasts to optimize boiler charging.
- Logs all measurements and actions to log files for monitoring and debugging.
- Automatically discovers the SolarEdge inverter's IP address on the local network using its MAC address, so the system is robust to DHCP changes.
- Rotates log and data files to prevent uncontrolled growth.

## Configuration
Before running the system, you must configure the following in `solarmanagement.py` (at the top of the file):

```
# === DEVICE NETWORK CONFIGURATION ===
SHELLY_IP = "192.168.2.78"           # Set this to the IP address of your Shelly relay
SOLAREDGE_MAC = "28:b7:7c:1e:66:67"  # Set this to the MAC address of your SolarEdge inverter
```

- **SHELLY_IP**: The IP address of your Shelly relay (find this in your router's device list or Shelly app).
- **SOLAREDGE_MAC**: The MAC address of your SolarEdge inverter (find this on the inverter label or your router's device list).

If your network changes (e.g., DHCP assigns a new IP), you only need to update these values.

## Log and Data Files
The following files are created in `/tmp/`:
- `solar.log`: Main application log (rotated automatically).
- `solardata.json`: All measurement data (rotated automatically).
- `solar.json`: Stores boiler charge state and counters.

## Requirements
- Python 3.8+
- ShellyPy, pymodbus, solaredge_modbus, suntime, dateutil, yaml, and other dependencies (see requirements.txt)

## Usage
1. Configure the device IP and MAC at the top of `solarmanagement.py`.
2. Run the script:
   ```
   python solarmanagement.py
   ```
3. Monitor logs and data in `/tmp/` as needed.

---
For further details, see comments in the code or contact the project maintainer.
