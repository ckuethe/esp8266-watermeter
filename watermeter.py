# vim: tabstop=4:softtabstop=4:shiftwidth=4:expandtab:
from network import WLAN, STA_IF
from btree import open as db_open
from ntptime import settime as ntp_settime
from machine import Pin, Timer, RTC, WDT, reset
import socket
import time
import picoweb
import logging

led_pin = Pin(2, Pin.OUT) # implicitly turns on the LED. 

# need to create this early so the decorator works
app = picoweb.WebApp(None)

# global, various functions can share them
port = 2782  # Spells 'AQUA' on a phone keypad
pulse_ctr = 0
gal_to_l = 3.78541

# YF-S402B = 1.5 mlpp
# FL-308 = 0.875657 mlpp

state = {
    'boot_time':      '2018 11 17 12 0 0 0',  # when the board was last booted
    'last_save_time': '2018 11 17 12 0 0 0',  # when the data was last saved
    'ml_per_pulse': 1.5,    # calibration
    'metric': True,         # report in metric or imperial units
    # It's not clear to me yet what behavior to take here:
    # - store usage in pulses?
    # - store usage in litres?
    # - preserve/clear usage when the calibration changes?
    'usage': 0,
}


# create an interface and activate it. Might be used later to configure the
# wifi on a new board, and will definitely be used for the SSDP-ish broadcast
net = WLAN(STA_IF)
net.active(1)

# i was getting some watchdog resets after a while. So maybe this can fix it.
wdt = WDT()
def feed_watchdog(_=None):
    global wdt
    wdt.feed()

_wd_timer = Timer(-1)
_wd_timer.init(period=1_000, mode=Timer.PERIODIC, callback=feed_watchdog)


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
    global net
    global port
    i = net.ifconfig()
    dst = calculate_broadcast(i[0], i[1])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n = s.sendto('watermeter running on http://{}:{}'.format(i[0],port), (dst, port))
    s.close()
    logging.info('advertised {}:{}'.format(i[0], port))

def ntp_sync(_=None):
    # this function is called once an hour by a periodic timer to do two
    # things. First, avoid an overflow of the ESP8266 RTC by calling time()
    # as documented (time() and localtime() do some internal compensation)
    # and then call ntp_settime() to resync the clock which is apparently
    # pretty terrible
    time.time()  # this is to keep the stupid RTC fed
    ntp_settime()

def serialize_localtime(t=None):
    # Convert a time tuple into a bytes() object as require by btree
    if t is None:
        t = time.localtime()
    return ' '.join(map(str, t))

def deserialize_localtime(t=None):
    # Convert a string representation of a time tuple into a tuple
    if t is None:
        return None
    t = tuple(map(int, t.decode('utf-8').split()))
    if len(t) != 8:
        return None
    return t

def load_state():
    global state
    fd = open('watermeter.db', 'w+b')
    db = db_open(fd)
    db.flush()

    # update the default state with any saved state (which might be null)
    state.update(dict(db.items()))

    # we're done with the database for now
    db.flush()
    db.close()
    fd.close()

    if time.time() < 1000000:
        # NTP has not yet kicked in, bootstrap the clock
        state['boot_time'] = state['last_save_time']
        now = deserialize_localtime(state['last_save_time'])
        RTC().init(now)


def save_state():
    global state
    fd = open('watermeter.db', 'w+b')
    db = db_open(fd)
    state['last_save_time'] = serialize_localtime()
    for k,v in state.items():
        db[k] = str(v)
    db.flush()
    db.close()
    fd.close()


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
    global pulse_ctr
    global state
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
        logging.info('IP: {}'.format(ip))
        # new board, let's set up the time right away
        ntp_sync() 
    else:
        logging.warning('DHCP configuration failed')
        

def main(debug=0, mlpp=0, do_ntp=True, do_netadv=True):
    global app
    global port

    logging.info('starting watermeter app')
    time.sleep(2)  # give the wifi time to connect
    if do_ntp:
        logging.info('starting NTP task')
        ntp_sync()
        _ntp_timer = Timer(-1)
        _ntp_timer.init(period=3_600_000, mode=Timer.PERIODIC, callback=ntp_sync)

    if do_netadv:
        logging.info('starting device announcement task')
        send_adv_msg()
        _adv_timer = Timer(-1)
        _adv_timer.init(period=30_000, mode=Timer.PERIODIC, callback=send_adv_msg)


    load_state()
    save_state()

    led_pin.on() # turns led off, because of the way they drive the pins.
    data_pin = Pin(4, Pin.IN, Pin.PULL_UP)
    data_irq = data_pin.irq(trigger=Pin.IRQ_FALLING, handler=pulse_handler)

    app.run(debug=debug, port=port, host='0.0.0.0')

if __name__ == '__main__':
    main()
