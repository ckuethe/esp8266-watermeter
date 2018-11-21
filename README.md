# ESP8266 Water Meter


This is a simple ESP8266 powered water flow sensor. A typical use case might be to measure the amount of water that has passed through a reverse osmosis water filter to know when the filter media should be changed.

## Hardware

Pretty simple, really. Take one NodeMCU, connect the 3.3V regulated output pin to Vcc (red wire) on a hall effect sensor. Ground pin to GND (black wire). GPIO4 (it happens to be D2 on my board) to OUT (yellow wire) on the sensor. That's it. The circuit toggles one of the on-board LEDs whenever a pulse arrives from the sensor but that's purely eye-candy.


## Firmware
The watermeter is based on micropython with a customized set of frozen modules. This is necessary due to the limited amount of memory available for runtime compilation of modules on the file system, the fact that not all the modules are available to be installed with upip, and an upper bound on flash image size prevents me from just shipping all of `micropython-lib` as frozen modules. As recommended by the documentation, the water meter application is in a single module which can be imported and then invoked by `main()`

#### Installation
1. Flash micropython to the target board
	1. I have provided a binary of my micropython build [here](esp8266-firmware-git.bin)
	1. You can build your own by adding and removing the listed modules
1. Copy in watermeter.py, eg. `ampy -p /dev/ttyUSB0 watermeter.py`
1. Connect to the ESP8266 over its serial console
1. `import watermeter`
1. `watermeter.netconfig('your-wifi-ssid-here', 'your-wifi-password-here)`
1. `watermeter.install_and_reboot()

`

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
This endpoint returns a timestamped report of current usage:  `{"unit": "gal", "timestamp": "2018-11-19 07:43:46.000", "volume": 1.03463}`

#### Calibration

```
/calibrate?mls=<x>&pulses=<y>
/calibrate?k=<mls_per_pulse>
```

This endpoint sets the calibration value for the sensor. Different turbines, impellers, and pipe will have varying volumes per pulse.

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