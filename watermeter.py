# vim: tabstop=4:softtabstop=4:shiftwidth=4:expandtab:
from network import WLAN, STA_IF, AP_IF
import btree
from ntptime import settime as ntp_settime
from machine import Pin, I2C, Timer, RTC, WDT, reset, freq
import usocket as socket
import time
import picoweb
import logging
import os
from db import DB_fram as DB

led_pin = None
oled = None
bus = I2C(sda=Pin(5), scl=Pin(4))
dbh = DB(bus=bus)

logger = logging.Logger('watermeter')

# need to create this early so the decorator works
app = picoweb.WebApp(None)

# global, various functions can share them
ip = None
port = 80
pulse_ctr = 0
gal_to_l = 3.78541

# YF-S402B = 1.5 mlpp
# FL-308 = 1.28 mlpp

state = {
    'last_save_time': (2018,12,21, 0,0,0,0, 0),  # when the data was last saved
    'ml_per_pulse': 1.5,    # calibration
    'metric': True,         # report in metric or imperial units
    'usage': 0,             # pulses
    'indicator': None,      # [None, 'blink', 'oled']
}


# Create a station interface and activate it. It'll be used for the device
# advertisement broadcast. Just in case there was a previous AP configuration
# drop that interface
net = WLAN(AP_IF)
net.active(0)
net = WLAN(STA_IF)
net.active(1)

# i was getting some watchdog resets after a while. So maybe this can fix it.
doggo = None
def doggo_treats(_=None):
    doggo.feed()

def inet_pton(dottedquad):
    a = list(map(int, dottedquad.strip().split('.')))
    n = a[0]<<24 | a[1]<<16 | a[2]<<8 | a[3]
    return n

def inet_ntop(n):
    a = [n>>24&0xff, n>>16&0xff, n>>8&0xff, n&0xff]
    return '{:d}.{:d}.{:d}.{:d}'.format(*a)

def calculate_broadcast(ip, nm):
    netaddr = inet_pton(ip) & inet_pton(nm)
    bcast_host = inet_pton('255.255.255.255') & ~inet_pton(nm)
    return inet_ntop(netaddr+bcast_host)

def send_adv_msg(_=None):
    global ip
    global port
    i = net.ifconfig()
    ip = i[0]
    dst = calculate_broadcast(ip, i[1])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('0.0.0.0', port))
    try:
        s.sendto(b'watermeter running on http://{}'.format(ip), (dst, 1900))
    except OSError:
        pass
    s.close()
    logger.info('advertised http://%s to %s', ip, dst)

def ntp_sync(_=None):
    # this function is called once an hour by a periodic timer to do two
    # things. First, avoid an overflow of the ESP8266 RTC by calling time()
    # as documented (time() and localtime() do some internal compensation)
    # and then call ntp_settime() to resync the clock which is apparently
    # pretty terrible
    if not net.isconnected():
        return False
    try:
        t = time.time()
        # this could fail if the network isn't available
        ntp_settime()
        t = time.time() - t
        logger.debug('NTP synced, delta %f', t)
        return True
    except Exception as e:
        logger.warning('NTP Sync failed: %s', e)
        return False

def load_state():
    global state
    global pulse_ctr

    rtc = RTC()
    if time.time() < 500_000_000:
        # NTP has not set time, bootstrap the clock with default time
        rtc.datetime(state['last_save_time'])
        logger.debug('bootstrapped clock to %s', str(state['last_save_time']))

    state = dbh.load()
    pulse_ctr = state['usage']

def save_state():
    global state
    global pulse_ctr

    state['usage'] = pulse_ctr
    dbh.save(state)
    logger.debug('saved database')
    state['last_save_time'] = time.localtime()


def data_sync(_=None):
    logger.debug('auto sync')
    if pulse_ctr == state['usage']:
        logger.debug('no sync needed')
        return
    if time.time() - time.mktime(state['last_save_time']) >= 600:
        save_state()
    else:
        logger.debug('not yet time to sync')


def pulse_handler(_=None):
    # increment the pulse counter
    global pulse_ctr
    global led_pin
    pulse_ctr += 1
    # eye candy: blink the LED. Maybe.
    if led_pin:
        led_pin.value(led_pin.value()^1)

def setup_oled(bus):
    from ssd1306 import SSD1306_I2C
    # this assumes a particular board.

    p_rst = Pin(16, Pin.OUT)
    p_rst.off()
    p_rst.on()

    # h=64 works on a 0.96" big lcd, h=32 is for a 0.91" small one, but
    # using h=32 on a big LCD can be used to create a double height font
    return SSD1306_I2C(128, 32, bus)

def oled_output(_=None):
    doggo_treats() # just in case the OLED is slow
    u = 'litre'
    v = pulse_ctr * state['ml_per_pulse'] / 1000.0

    if state['metric'] is False:
        u = 'gallon'
        v /= gal_to_l

    t = time.localtime()
    oled.fill(0)
    oled.text("{}".format(ip), 0, 0)
    oled.text("{:02d}/{:02d} {:02d}:{:02d}:{:02d}".format(t[1], t[2], t[3], t[4], t[5]), 0, 8)
    oled.text("{:.1f} {}".format(v, u), 0, 16)
    oled.show()

@app.route("/")
def show_endpoints(req, resp):
    endpoints = list(
        filter(lambda x: str(x).startswith('/'),
            map(lambda x: x[0],
                app.url_map
                )
            )
        )
    yield from picoweb.jsonify(resp, endpoints)


@app.route("/usage")
def show_config(req, resp):
    t = '{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}.{:03d}'.format(*(time.localtime()))
    u = 'litre'
    v = pulse_ctr * state['ml_per_pulse'] / 1000.0

    if state['metric'] is False:
        u = 'gal'
        v /= gal_to_l

    msg = {
        'timestamp': t,
        'unit': u,
        'volume': v,
        'pulses': pulse_ctr,
        'k': state['ml_per_pulse'],
    }
    yield from picoweb.jsonify(resp, msg)


@app.route("/sync")
def sync(req, resp):
    save_state()
    yield from picoweb.jsonify(resp, {'msg': 'database saved'})

@app.route("/calibrate")
def calibrate(req, resp):
    global state
    rv = {'updated': False}

    req.parse_qs()
    v = req.form.get('mls', None)
    n = req.form.get('pulses', None)
    k = req.form.get('k', None)
    rv['mls'] = v
    rv['k'] = k
    rv['pulses'] = n
    if k:
        state['ml_per_pulse'] = float(k[0])
        rv['updated'] = True
    elif v and k:
        try:
            state['ml_per_pulse'] = float(v[0])/float(n[0])
            rv['updated'] = True
        except ZeroDivisionError:
            pass
    else:
        rv['msg'] = "Must supply either 'k' or 'mls' and 'pulses' parameters to change calibration"
    rv['ml_per_pulse'] = state['ml_per_pulse']
    if rv['updated']:
        save_state()

    yield from picoweb.jsonify(resp, rv)

@app.route("/metric")
def go_metric(req, resp):
    global state
    if not state['metric']:
        state['metric'] = True
        save_state()
    yield from picoweb.jsonify(resp, {'metric': True})

@app.route("/imperial")
def no_metric(req, resp):
    global state
    if state['metric']:
        state['metric'] = False
        save_state()
    yield from picoweb.jsonify(resp, {'metric': False})

@app.route("/uninstall")
def uninstall(req=None, resp=None):
    msg = 'uninstalled watermeter app.'
    try:
        os.remove('main.py')
    except Exception as e:
        msg = 'caught exception: {}'.format(str(e))
    if req is None or resp is None:
        print(msg)
        return
    yield from picoweb.jsonify(resp, {'msg': msg})

@app.route("/install")
def install(req=None, resp=None):
    msg = 'install failed'
    with open('main.py', 'w') as fd:
        rv = fd.write('import watermeter\nwatermeter.main(1)\n')
        msg = 'install success'
    if req is None or resp is None:
        print(msg)
        return msg
    yield from picoweb.jsonify(resp, {'msg': msg})

def initconfig(**kwargs):
    '''
    Parameters
        ssid (str): Wifi Name
        password (str): Wifi password if required
        hostname (str): custom hostname, rather than "ESP_%06X"
        use_oled (bool): use OLED display if available
        k (float): calibration constant, ml/pulse
        pulses (int): a positive integer, used for loading previous measurements

    '''
    global state
    global pulse_ctr
    global oled
    global bus

    if kwargs.get('pulses', None):
        try:
            n = int(kwargs['pulses'])
            if n > 0:
                pulse_ctr = n
        except Exception:
            pass

    if kwargs.get('k', None):
        try:
            n = float(kwargs['k'])
            if n > 0:
                state['ml_per_pulse'] = n
        except Exception:
            pass

    if kwargs.get('hostname', None):
        tmp = input('hostname?').lower().strip()
    if len(kwargs['hostname']):
            state['hostname'] = kwargs['hostname'].strip()
            net.config(dhcp_hostname=state['hostname'])
            net.active(False)
            net.active(True)
    else:
        state['hostname'] = ''

    use_oled = False
    if kwargs.get('use_oled', None) is None:
        tmp = input("Use OLED [N]/y? ").lower().strip()
        if tmp in ['y', 'yes', 't', 'true', 1, '1']:
            use_oled = True
        else:
            use_oled = False

    if use_oled == True:
        state['indicator'] = 'oled'
        oled = setup_oled(bus)
        oled.fill(0)
        oled.text("ESP8266 WiFi", 0, 8)
        oled.text("Water  Meter", 0, 16)
        oled.show()
    else:
        state['indicator'] = 'blnk'

    ssid = None
    password = None
    if kwargs.get('ssid', None) is None:
        ssid = input('SSID? ')
        password = input('Password? ')
        if password == '':
            password = None
    else:
        ssid = kwargs['ssid']
        password = kwargs['password']

    if ssid:
        net.connect(ssid, password)
    else:
        logger.info('skipped network configuration')

    ip = None
    for _ in range(30):
        time.sleep(1)
        i = net.ifconfig()
        if i[0] != '0.0.0.0':
            ip = i[0]
            break
    if ip:
        logger.info('IP: %s', ip)
        # new board, let's set the time right away and retry
        # a few times just in case the network is slow
        for i in range(5):
            if ntp_sync():
                break
            else:
                time.sleep(2)
    else:
        logger.info('DHCP configuration failed')

    save_state()

def ms(s=None, m=None, h=None):
    t = 0
    if s is not None:
        t += s * 1000
    if m is not None:
        t += m * 60 * 1000
    if h is not None:
        t += h * 60 * 60 * 1000
    return t

def main(debug=0):
    global doggo
    global led_pin
    global oled
    global dbh
    global bus

    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    load_state()

    for i in range(30):
        logger.debug('waiting for network')
        time.sleep(2)  # give the wifi time to connect
        if net.isconnected():
            break

    logger.debug('starting NTP task')
    ntp_sync()
    ntp_timer = Timer(-1)
    ntp_timer.init(period=ms(m=5), mode=Timer.PERIODIC, callback=ntp_sync)

    logger.debug('starting device announcement task')
    send_adv_msg()
    adv_timer = Timer(-1)
    adv_timer.init(period=ms(m=1), mode=Timer.PERIODIC, callback=send_adv_msg)

    save_state()

    logger.debug('starting watchdog task')
    doggo = WDT()
    wd_timer = Timer(-1)
    wd_timer.init(period=ms(s=1), mode=Timer.PERIODIC, callback=doggo_treats)

    dpin = 4 # D2
    if state['indicator'] == 'oled':
        logger.debug('starting OLED task')
        oled = setup_oled(bus)
        oled_output()
        oled_timer = Timer(-1)
        oled_timer.init(period=ms(s=1), mode=Timer.PERIODIC, callback=oled_output)
        dpin = 12 # D6
    else:
        logger.debug('using LED blinks')
        led_pin = Pin(2, Pin.OUT, value=1)

    logger.debug('starting data sync task')
    save_timer = Timer(-1)
    save_timer.init(period=ms(m=5), mode=Timer.PERIODIC, callback=data_sync)

    data_pin = Pin(dpin, Pin.IN, Pin.PULL_UP)
    data_irq = data_pin.irq(trigger=Pin.IRQ_FALLING, handler=pulse_handler)

    logger.info('starting watermeter app')
    app.run(debug=debug, port=port, host='0.0.0.0')

if __name__ == '__main__':
    main()
