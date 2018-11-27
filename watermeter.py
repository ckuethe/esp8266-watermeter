# vim: tabstop=4:softtabstop=4:shiftwidth=4:expandtab:
from network import WLAN, STA_IF, AP_IF
from ntptime import settime as ntp_settime
from machine import Pin, Timer, RTC, WDT, reset
import usocket as socket
import time
import picoweb
import logging
import json
import os

led_pin = Pin(2, Pin.OUT) # implicitly turns on the LED. 

logger = logging.Logger('watermeter')

# need to create this early so the decorator works
app = picoweb.WebApp(None)

# global, various functions can share them
port = 2782  # Spells 'AQUA' on a phone keypad
pulse_ctr = 0
gal_to_l = 3.78541

# YF-S402B = 1.5 mlpp
# FL-308 = 1.28 mlpp

state = {
    'last_save_time': (2018,11,22, 12,0,0,0, 0),  # when the data was last saved
    'ml_per_pulse': 1.5,    # calibration
    'metric': True,         # report in metric or imperial units
    'usage': 0,             # pulses
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
    i = net.ifconfig()
    dst = calculate_broadcast(i[0], i[1])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('0.0.0.0', port))
    s.sendto(b'watermeter running on http://{}:{}'.format(i[0],port), (dst, 1900))
    s.close()
    logger.info('advertised %s:%d to %s', i[0], port, dst)

def ntp_sync(_=None):
    # this function is called once an hour by a periodic timer to do two
    # things. First, avoid an overflow of the ESP8266 RTC by calling time()
    # as documented (time() and localtime() do some internal compensation)
    # and then call ntp_settime() to resync the clock which is apparently
    # pretty terrible
    time.time()

    try:
        # this could fail if the network isn't available
        ntp_settime()
        logger.debug('NTP synced')
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

    try:
        with open('watermeter.json', 'r') as fd:
            tmp = json.load(fd)
            for k,v in tmp.items():
                logger.debug('restored %s = %s', k, v)
                if k in ['usage']:
                    state[k] = int(v)
                elif k in ['ml_per_pulse']:
                    state[k] = float(v)
                else:
                    state[k] = v

        pulse_ctr = state['usage']
    except Exception:
        # catches JSON parse failures from empty or nonexistent files,
        # unexpected structure, failed conversions...
        pass

    if time.time() < time.mktime(state['last_save_time']):
        rtc.datetime(state['last_save_time'])
        logger.debug('updated clock to %s', str(state['last_save_time']))

def save_state():
    global state
    global pulse_ctr

    state['last_save_time'] = time.localtime()
    state['usage'] = pulse_ctr
    try:
        fd = open('watermeter.json.tmp', 'w')
        json.dump(state, fd)
        fd.close()
        os.rename('watermeter.json.tmp', 'watermeter.json')
        logger.debug('saved data')
    except Exception:
        pass

def data_sync(_=None):
    if pulse_ctr == state['usage']:
        return
    if time.time() - time.mktime(state['last_save_time']) >= 60:
        save_state()


def pulse_handler(unused_arg=None):
    # increment the pulse counter 
    global pulse_ctr
    global led_pin
    pulse_ctr += 1
    # eye candy. blink the 
    led_pin.value(led_pin.value()^1)

@app.route("/")
def show_endpoints(req, resp):
    endpoints = {
        '/': 'show endpoints',
        '/usage': 'show current usage',
        '/sync': 'save database to flash',
        '/calibrate': 'calibrate the flowmeter',
        '/metric': 'switch to metric units',
        '/imperial': 'switch to imperial units',
    }
    yield from picoweb.jsonify(resp, endpoints)


@app.route("/usage")
def show_config(req, resp):
    t = '{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}.{:03d}'.format(*(time.localtime()))
    u = 'litre'
    v = pulse_ctr * state['ml_per_pulse'] / 1000.0

    if state['metric'] is False:
        u = 'gal'
        v /= 3.78541

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
@app.route("/liter")
@app.route("/litre")
@app.route("/liters")
@app.route("/litres")
def go_metric(req, resp):
    global state
    if not state['metric']:
        state['metric'] = True
        save_state()
    yield from picoweb.jsonify(resp, {'metric': True})

@app.route("/imperial")
@app.route("/gallon")
@app.route("/gallons")
def no_metric(req, resp):
    global state
    if state['metric']:
        state['metric'] = False
        save_state()
    yield from picoweb.jsonify(resp, {'metric': False})

@app.route("/uninstall")
def uninstall(req, resp):
    import os
    files = os.listdir()
    if 'watermeter.py' in files:
        os.rename('watermeter.py', 'watermeter.py.bak')
    if 'main.py' in files:
        os.rename('main.py', 'watermeter.py')
    yield from picoweb.jsonify(resp, {'msg': 'uninstalled watermeter app. please reboot'})

def install_and_reboot():
    import os
    os.rename('watermeter.py', 'main.py')
    reset()

def netconfig(ssid=None, password=None):
    if ssid is None:
        ssid = input('SSID? ')
        password = input('Password? ')
        if password == '':
            password = None
    net.connect(ssid, password)

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
        
def ms(s=None, m=None, h=None):
    if s is not None:
        return s * 1000
    if h is not None:
        return m * 60 * 1000
    if h is not None:
        return h * 60 * 60 * 1000

def main(debug=0):
    global doggo

    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    time.sleep(2)  # give the wifi time to connect

    logger.debug('starting NTP task')
    ntp_sync()
    ntp_timer = Timer(-1)
    ntp_timer.init(period=ms(h=1), mode=Timer.PERIODIC, callback=ntp_sync)

    logger.debug('starting device announcement task')
    send_adv_msg()
    adv_timer = Timer(-1)
    adv_timer.init(period=ms(m=1), mode=Timer.PERIODIC, callback=send_adv_msg)

    load_state()
    save_state()

    logger.debug('starting watchdog task')
    doggo = WDT()
    wd_timer = Timer(-1)
    wd_timer.init(period=ms(s=1), mode=Timer.PERIODIC, callback=doggo_treats)

    logger.debug('starting data sync task')
    save_timer = Timer(-1)
    save_timer.init(period=ms(m=10), mode=Timer.PERIODIC, callback=data_sync)

    led_pin.on() # turns led off, because of the way they drive the pins.
    data_pin = Pin(4, Pin.IN, Pin.PULL_UP)
    data_irq = data_pin.irq(trigger=Pin.IRQ_FALLING, handler=pulse_handler)

    logger.info('starting watermeter app')
    app.run(debug=debug, port=port, host='0.0.0.0')

if __name__ == '__main__':
    main()
