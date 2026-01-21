# Controller daemon

Handles the button clicks and send MIDI messages to PiPedal.

## Install dependencies
```
sudo apt install python3-mido python3-rtmidi
```

## Run as daemon on start
```
crontab -e
```
add line
```
@reboot /usr/bin/python3 -u /home/pi/controller-daemon.py > /home/pi/controller.log 2>&1
```
