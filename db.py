# vim: tabstop=4:softtabstop=4:shiftwidth=4:expandtab:
import time

class DB_generic(object):
    '''generic interface for persisting and restoring state of my watermeter'''

    indicators = ['none', 'blnk', 'oled']
    defaults = {
        'hostname':'watermeter',
        'indicator': indicators[1],
        'last_save_time': 0,
        'metric': True,
        'ml_per_pulse': 1.5,
        'usage': 0,
    }

    def __init__(self):
        pass

    def load(self):
        '''load persisted state into running variables'''
        return {}

    def save(self, db):
        '''save running state into a persistent storage'''
        # this function must update db['last_save_time']
        return False

    def dbinit(self):
        '''initialize a blank datastore with defaults'''
        self.save(self.defaults)

    def edit(self, k, v):
        '''EVIL. Modify a value in the persistent store'''
        d = self.load()
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
        return self.save(d)

    def dump(self):
        '''Dump the database contents'''
        for k,v in self.load().items():
            if k == 'last_save_time':
                v = self.time_int2str(v)
            print(k, '=', v)

    def time_str2int(self, t):
        '''deserialize time into an int'''
        return time.mktime([int(i) for i in t.split()[:6]] + [0,0,0])


    def time_int2str(self, t=None):
        '''deserialize time into an int'''
        return ' '.join([str(i) for i in time.localtime(t)[:6]])


class DB_flat(DB_generic):
    '''Flat File storage'''
    _db_file = None
    _iobuf = None
    def __init__(self, db_file='watermeter.dat'):
        self._db_file = db_file
        self._iobuf = bytearray(72)

    def save(self, d):
        d['last_save_time'] = self.time_int2str()
        with open(self._db_file, 'w') as fd:
            s = '{metric:d},{usage:d},{ml_per_pulse:0.2f},{last_save_time:s},{indicator:s},{hostname:s},EOF'.format(**d)
            fd.write(s)
        d['last_save_time'] = int(time.time())

    def load(self):
        with open(self._db_file) as fd:
            fd.readinto(self._iobuf)
        v = self._iobuf.decode('utf-8').strip().split(',')
        d = {
            'metric': bool(v[0]),
            'usage': int(v[1]),
            'ml_per_pulse': float(v[2]),
            'last_save_time': self.time_str2int(v[3]),
            'indicator': v[4],
            'hostname': v[5],
        }
        if d['indicator'] not in self.indicators:
            d['indicator'] = self.indicators[0]
        return d


class DB_btree(DB_generic):
    '''Use the btree module to save state'''
    import btree
    _db_file = None

    def __init__(self, db_file='watermeter.db'):
        self._db_file = db_file

    def save(self, d):
        with open(self._db_file, 'w+b') as fd:
            dbh = self.btree.open(fd, pagesize=512, cachesize=512)
            d['last_save_time'] = self.time_int2str()
            for k,v in d.items():
                dbh[k] = str(v)
            dbh.close()
            d['last_save_time'] = int(time.time())
        return True

    def load(self):
        d = dict()
        with open(self._db_file, 'r+b') as fd:
            dbh = self.btree.open(fd, pagesize=512, cachesize=512)
            for k,v in dbh.items():
                d[k.decode('utf-8')] = v.decode('utf-8')
        d['last_save_time'] = self.time_str2int( d['last_save_time'])
        d['metric'] = bool( d['metric'])

        if d['indicator'] not in self.indicators:
            d['indicator'] = self.indicators[0]
        return d


class DB_json(DB_generic):
    '''Serialize to JSON'''
    import json
    _db_file = None

    def __init__(self, db_file='watermeter.json'):
        self._db_file = db_file

    def load(self):
        with open(self._db_file) as fd:
            d = self.json.load(fd)
            try:
                # Check for required keys

                # non-negative usage
                d['usage'] = int(d['usage'])

                # non-negative calibration
                d['ml_per_pulse'] = float(d['ml_per_pulse'])

                # this will explode if the stored content is invalid
                d['last_save_time'] = self.time_str2int(d['last_save_time'])

                assert (sorted(self.defaults.keys()) == sorted(d.keys()))
                assert(d['usage'] >= 0)
                assert(d['ml_per_pulse'] > 0)
                assert(d['indicator'] in self.indicators)

            except Exception as e:
                return self.defaults
            return d

    def save(self, d):
        d['last_save_time'] = self.time_int2str()
        with open(self._db_file, 'w') as fd:
            self.json.dump(d, fd)
        d['last_save_time'] = int(time.time())
        return True


class DB_fram(DB_generic):
    '''Fujitsu Ferroelectric Random Access Memory (FRAM)'''
    from machine import Pin, I2C
    _devaddr = None
    _bus = None
    _iobuf = None
    _memaddr = 0

    def __init__(self, scl=4, sda=5, dev=0x50, memaddr=0, bus=None):
        if bus:
            self._bus = bus
        else:
            self._bus = self.I2C(sda=self.Pin(sda), scl=self.Pin(scl))

        self._devaddr = dev
        if self._devaddr not in self._bus.scan():
            raise IOError('No F-RAM found at address {}'.format(self._devaddr))
        self._iobuf = bytearray(64)
        self._memaddr = memaddr

    def save(self, d):
        d['last_save_time'] = self.time_int2str()
        b = '{metric:d},{usage:d},{ml_per_pulse:0.2f},{last_save_time:s},{indicator:s},{hostname:s},EOF'.format(**d)
        self._bus.writeto_mem(self._devaddr, self._memaddr, b, addrsize=16)
        d['last_save_time'] = int(time.time())

    def load(self):
        self._bus.readfrom_mem_into(self._devaddr, self._memaddr, self._iobuf, addrsize=16)
        v = self._iobuf.decode('utf-8').strip().split(',')
        d = {
            'metric': bool(v[0]),
            'usage': int(v[1]),
            'ml_per_pulse': float(v[2]),
            'last_save_time': self.time_str2int(v[3]),
            'indicator': v[4],
            'hostname': v[5],
        }
        if d['indicator'] not in self.indicators:
            d['indicator'] = self.indicators[0]
        return d


class DB_eeprom(DB_generic):
    from machine import Pin, I2C
    '''Use a generic I2C EEPROM to save state. NOT WORKING YET'''
    _bus = None
    capacity = None
    pagesize = None
    page = 0

    def __init__(self, sda=12, scl=13, addr=50, device_kbits=256, pagesize=64, bus=None):
        '''
        Ooof, this one is tricky. EEPROMs can be multiple sizes: 16kb, 32kb,
        64kb, 128kb, 256kb. They may also have various page sizes: 16B, 32B,
        64B and for maximum lifetime whole pages should be written
        '''
        if bus:
            self._bus = bus
        else:
            self._bus = self.I2C(sda=self.Pin(sda), scl=self.Pin(scl))
        self.capacity = device_kbits / 8
        self.pagesize = pagesize

    def test(self):
        if self.addr not in self.bus.scan():
            raise IOError('No EEPROM found at address 0x50')

