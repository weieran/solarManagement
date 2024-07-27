#!/bin/bash

PATH=/mnt/node/bin:/appl/bin:/$PATH

. /mnt/node/cfg/node_cfg.sh

CONFIG="/mnt/node/cfg/config.yaml"


startAppls()
{
    # I had to patch solaredge modbus to use Endian.BIG instead of Endian.Big (since 3.5)
    echo Starting Applications [$node_name]
    #--- Applications to Start ---
    app start solar.sh
    app start iothub.sh
    update_ddns.sh $ddns_password
}

stopAppls()
{
    echo Stopping Applications [$node_name]
    app stop solar.sh
    app stop iothub.sh
}


# Main entry point
case $1 in
  start)
    startAppls
    ;;

  stop)
    stopAppls
    ;;

  restart)
    # On the Edge the DigitalBootstrapManager should not be restarted during Digital Bootstrapping, therefore a special handling is implemented here.
    # So if the restart application is executed through the NodeManager (IF-47) -> node_commands.sh (IF-93) -> runAppl.sh the DigitalBootstrapManager is kept alive.
    stopAppls restart
    startAppls
    ;;
esac
