import sys
import os

sys.path.append('./modules/gui')

import json
import re

USER_DATA_DIR = os.getenv('HOME') + '/.config/wgf'
USER_DATA_CFG = USER_DATA_DIR + '/wgf_cfg.db'
USER_DATA_NET = USER_DATA_DIR + '/wgf.db'

class AppContainer:
  def __init__(self):
    self.app = None

  def register_app(self, _app):
    self.app = _app

  def get_app(self):
    return self.app


class CurrentNetworksList:
  def __init__(self):
    self.nets = {}

  def add_network(self, bssid, net_detail):
    ns = {'freq': net_detail[0],
          'signal_strenght': net_detail[1],
          'flags': re.sub('\]\[', '\t', net_detail[2])[1:-1].split('\t'),
          'ssid': net_detail[3]}

    # Check if this network is saved
    is_known = False
    np = stor.get_net_prefs_by_bssid(bssid)
    if np:
      is_known = True
    else:
      np = {}

    # Store net key, value
    self.nets[bssid] = {'net_stat': ns,
                        'is_known': is_known,
                        'net_prefs': np}

  def get_networks_list(self):
    return self.nets

  def get_network_by_bssid(self, bssid):
    if bssid in self.nets:
      return self.nets[bssid]
    else:
      return None

  def set_network_properties_by_bssid(self, bssid, props):
    if bssid in self.nets:
      self.nets[bssid]['net_prefs'] = props

  def save_network_by_bssid(self, bssid, props):
    stor.store_net_prefs(bssid, props)
    if bssid in self.nets:
      self.nets[bssid]['is_known'] = True

  def del_network_by_bssid(self, bssid):
    stor.del_net_from_db(bssid)
    if bssid in self.nets:
      self.nets[bssid]['net_prefs'] = {}
      self.nets[bssid]['is_known'] = False

  def flush_netslist(self):
    self.nets = {}


class AppStorageCoreSimple:
  def __init__(self,
               cfg_db_name=USER_DATA_CFG,
               net_db_name=USER_DATA_NET):

    self.cfg_db_name = cfg_db_name
    self.net_db_name = net_db_name

    self.cfg_db = {}
    self.net_db = {}

    if not os.path.exists(USER_DATA_CFG):
      self.save_cfg_db()

    if not os.path.exists(USER_DATA_NET):
      self.save_net_db()

    self.load_cfg()
    self.load_net()

  def save_cfg_db(self):
    data = json.dumps(self.cfg_db).encode()
    fd = os.open(self.cfg_db_name, os.O_CREAT|os.O_WRONLY|os.O_TRUNC)
    os.write(fd, data)
    os.close(fd)

  def save_net_db(self):
    data = json.dumps(self.net_db).encode()
    fd = os.open(self.net_db_name, os.O_CREAT|os.O_WRONLY|os.O_TRUNC)
    os.write(fd, data)
    os.close(fd)

  def load_cfg(self):
    raw_dt = b''
    try:
      fd = os.open(self.cfg_db_name, os.O_RDONLY)

      while True:
        payload = os.read(fd, 1024)
        if not payload:
          break
        raw_dt += payload

      self.cfg_db = json.loads(raw_dt)

    except Exception as e:
      print('Error while loading config database: %s', e)

  def load_net(self):
    raw_dt = b''
    try:
      fd = os.open(self.net_db_name, os.O_RDONLY)

      while True:
        payload = os.read(fd, 1024)
        if not payload:
          break
        raw_dt += payload

      self.net_db = json.loads(raw_dt)

    except Exception as e:
      print('Error while loading networks database: %s', e)


  def new_cfg_entry(self, key, val):
    self.cfg_db[key] = val

  def new_net_entry(self, key, val):
    self.net_db[key] = val

  def del_cfg_entry(self, key):
    if key in self.cfg_db:
      self.cfg_db.pop(key)

  def del_net_entry(self, key):
    if key in self.net_db:
      self.net_db.pop(key)

  def get_cfg_data(self, key):
    if key in self.cfg_db:
      return self.cfg_db[key]
    else:
      return None

  def get_net_data(self, key):
    if key in self.net_db:
      return self.net_db[key]
    else:
      return None


AppStorageCore = AppStorageCoreSimple

class AppStorage(AppStorageCore):
  def __init__(self,
               usr_data_dir=USER_DATA_DIR,
               cfg_db_name=USER_DATA_CFG,
               net_db_name=USER_DATA_NET):

    # Check path
    if not os.path.exists(usr_data_dir):
      os.mkdir(usr_data_dir)

    # Initialize Core Storage
    super().__init__(cfg_db_name, net_db_name)

    self.net_db_modified = False
    self.cfg_db_modified = False

  def get_net_prefs_by_bssid(self, bssid):
    return self.get_net_data(bssid)

  def store_net_prefs(self, bssid, data):
    if self.get_net_data(bssid):
      self.del_net_entry(bssid)

    self.new_net_entry(bssid, data)
    self.net_db_modified = True

  def del_net_from_db(self, bssid):
    self.del_net_entry(bssid)
    self.net_db_modified = True

  def close(self):
    if self.cfg_db_modified:
      self.save_cfg_db()

    if self.net_db_modified:
      self.save_net_db()




app = AppContainer()
cnl = CurrentNetworksList()
stor = AppStorage()
