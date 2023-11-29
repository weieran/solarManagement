# solarManagement

# Remote connection to the device

Using serveo.net as a jump-host for the ssh tunnel

1. send sms to my device to temporary open a jump host
   2. the device will activate the jump-host tunnel
   3. ssh -J serveo.net user@myalias
2. connect to the jump host
   ssh -J serveo.net root@myalias -i mykey