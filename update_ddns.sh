#!/bin/bash

# update ddns server see also https://dynv6.com/zones/3725941/instructions
# dns password has to be passed as first argument


## config
protocol=dyndns2
server=dynv6.com
password=$1
domain=weier-home.dynv6.net
# get current ip
ip=$(curl -s api.ipify.org)

# update ddns
curl -s "https://$server/api/update?hostname=${domain}&token=${password}&ipv4=$ip"

echo ": ip in $server == $ip"