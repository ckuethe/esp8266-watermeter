# -*- coding: utf-8 -*-
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4 syn=python

# quick demo of displaying text on an esp8266 with integrated SSD1306
# OLED such as this: https://smile.amazon.com/gp/product/B076JDVRLP
# This also works https://smile.amazon.com/gp/product/B00O2LLT30

from machine import Pin, I2C
from ssd1306 import SSD1306_I2C
from random import getrandbits
from time import sleep
from network import WLAN, STA_IF

# Consult your schematic for the right pins to use
def demo(scl=4, sda=5, rst=16):

    # this doesn't seem necessary on my board, but the
    # datasheet says that this reset may be required
    p_rst = Pin(rst, Pin.OUT)
    p_rst.off()
    p_rst.on()

    bus = I2C(sda=Pin(sda), scl=Pin(scl))
    print("i2c devices:", bus.scan())

    oled = SSD1306_I2C(128, 32, bus)

    hdr = "ESP8266  SSD1306"
    wl = WLAN(STA_IF)
    if wl.active() and wl.isconnected():
        hdr = "{:16s}".format(wl.ifconfig()[0])

    fmt="0x{:08x}"
    while True:
	oled.fill(0)
	oled.text(hdr, 0, 0)
	oled.text(fmt.format(getrandbits(32)), 0, 8)
	oled.text(fmt.format(getrandbits(32)), 0, 16)
	oled.text(fmt.format(getrandbits(32)), 0, 24)
	oled.show()
        sleep(0.1)
