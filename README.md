# ESP8266 Water Meter


This is a simple ESP8266 powered water flow sensor. A typical use case might
be to measure the amount of water that has passed through a reverse osmosis
water filter to know when the filter media should be changed.

## Hardware

Pretty simple, really. Take one NodeMCU, connect the 3.3V regulated output
pin to Vcc (red wire) on a hall effect sensor. Ground pin to GND (black wire).
GPIO4 (it happens to be D2 on my board) to OUT (yellow wire) on the sensor.

That's it.

The circuit toggles one of the on-board LEDs whenever a pulse arrives from
the sensor but that's purely eye-candy.

![Actual Device](https://raw.githubusercontent.com/ckuethe/esp8266-watermeter/master/device.jpg)

![Schematic](https://raw.githubusercontent.com/ckuethe/esp8266-watermeter/master/schematic.jpg)

## Firmware
The watermeter is based on micropython with a customized set of frozen
modules. This is necessary due to the limited amount of memory available
for runtime compilation of modules on the file system, the fact that not
all the modules are available to be installed with upip, and an upper bound
on flash image size prevents me from just shipping all of `micropython-lib`
as frozen modules. As recommended by the documentation, the water meter
application is in a single module which can be imported and then invoked by
`main()`.

After startup, the water meter will emit a UDP broadcast packet to port 1900
every 30 seconds announcing its presence. The UDP packet is sourced from the
same IP and port where the TCP listener is running. The same information is
logged on the serial console.

```
Log:
INFO:None:advertised 192.168.1.42:2782 to 192.168.1.255

Network Advertisement:
08:57:55.771389 IP 192.168.1.42.2782 > 192.168.1.255.1900: UDP, length 46
	0x0000:  4500 004a 0018 0000 ff11 3711 c0a8 012a  E..J......7....*
	0x0010:  c0a8 01ff 0ade 076c 0036 6178 7761 7465  .......l.6axwate
	0x0020:  726d 6574 6572 2072 756e 6e69 6e67 206f  rmeter.running.o
	0x0030:  6e20 6874 7470 3a2f 2f31 3932 2e31 3638  n.http://192.168
	0x0040:  2e31 2e34 323a 3237 3832                 .1.42:2782

```

#### Installation
1. Flash micropython to the target board
	1. I have provided a binary of my micropython build [here](esp8266-firmware-git.bin)
	1. You can build your own by adding and removing the listed modules
1. Copy in watermeter.py, eg. `ampy -p /dev/ttyUSB0 watermeter.py`
1. Connect to the ESP8266 over its serial console
1. `import watermeter`
1. `watermeter.netconfig('your-wifi-ssid-here', 'your-wifi-password-here')`  # or just `watermeter.netconfig()`
1. `watermeter.install_and_reboot()` # renames watermeter.py to main.py so that the bootloader will run it at startup

#### Removed Modules
- apa102
- dht
- ds18x20
- neopixel
- onewire
- port_diag
- upip
- upip_utarfile

#### Added Modules

- logging
- picoweb
- pkg_resources
- uasyncio


## JSON API

```
/
```
List all the API endpoints

#### Current Usage

```
/usage
```
This endpoint returns a timestamped report of current usage, eg. `{"timestamp": "2018-11-21 08:14:22.002", "volume": 1.6704, "pulses": 1305, "k": 1.28, "unit": "litre"}`

#### Calibration

```
/calibrate?mls=<x>&pulses=<y>
/calibrate?k=<mls_per_pulse>
```

This endpoint sets the calibration value for the sensor. Different turbines,
impellers, and pipe will have varying volumes per pulse.

#### Unit selection

```
/metric
/liter
/litre
/liters
/litres
```

```
/imperial
/gallon
/gallons
```

Switch the reporting unit.
