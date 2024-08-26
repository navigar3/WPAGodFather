import sys

sys.path.append('./modules/gui')

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from common import app, cnl
from application_events import app_evs

class MainView(Gtk.Box):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

    self._ui = Gtk.Builder()
    self._ui.add_from_file('data/ui/MainView.ui')
    self._ui.connect_signals(self)

    self.Widget = self._ui.get_object('MainView')
    self.CurrentNets = self._ui.get_object('NetsList')
    self.add(self.Widget)

  def on_btn_pressed_Scan(self, *args):
    print('Scanning...')
    app_evs.emit('scan_for_networks')

  def flush_network_list(self):
    for c in self.CurrentNets:
      self.CurrentNets.remove(c)

  def refresh_networks_list(self):
    self.flush_network_list()

    for bssid, data in cnl.get_networks_list().items():
      self.add_net_in_networks_list(bssid, data)

  def add_net_in_networks_list(self, bssid, data):
    netIt = NetItem(bssid, data)
    netIt.btnConnect.set_name('Connect_' + bssid)
    netIt.btnPreferences.set_name('Prefs_' + bssid)
    self.CurrentNets.add(netIt.Widget)


class NetItem(Gtk.Box):
  def __init__(self, bssid, data):
    self._ui = Gtk.Builder()
    self._ui.add_from_file('data/ui/ItemWidget.ui')

    self.Widget = self._ui.get_object('ListItem')
    self.btnConnect = self._ui.get_object('Connect')
    self.btnPreferences = self._ui.get_object('Preferences')

    self.knownIc = self._ui.get_object('NetKnownIcon')
    self.SecIc = self._ui.get_object('NetSecurityIcon')

    self.knownIc.hide()

    self._ui.connect_signals(self)

    self.initialize_NetItem(bssid, data)

  def initialize_NetItem(self, bssid, data):
    self._ui.get_object('SSID').set_label(data['net_stat']['ssid'])
    self._ui.get_object('BSSID').set_label(bssid)

    if 'WPA2-PSK-CCMP' in data['net_stat']['flags']:
      self.SecIc.show()

    if data['is_known']:
      self.knownIc.show()


  def btnPressed_Connect(self, button=None):
    print(self.get_name())

  def btnPressed_Preferences(self, button=None):
    bssid = button.get_name().split('_')[1]
    app_evs.emit('show_netprefsview', bssid)


class NetPrefsView(Gtk.Box):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

    self._ui = Gtk.Builder()
    self._ui.add_from_file('data/ui/NetPrefsView.ui')

    self.Widget = self._ui.get_object('NetworkPrefsView')
    self.add(self.Widget)

    self.TopBarSSID = self._ui.get_object('TopBarLabelSSID')
    self.TopBarBSSID = self._ui.get_object('TopBarLabelBSSID')

    self.ForgetButton = self._ui.get_object('NetForget')

    self.SecPSK = self._ui.get_object('SecPSK')

    self._ui.connect_signals(self)

    self.view_bssid = None
    self.net_prefs = {}

  def gather_net_prefs(self):
    self.net_prefs['PSK'] = self.SecPSK.get_text()

  def on_btn_pressed_BackToMainView(self, button=None):
    app_evs.emit('show_mainview')

  def on_btn_pressed_Connect(self, button=None):
    self.gather_net_prefs()
    cnl.set_network_properties_by_bssid(self.view_bssid, self.net_prefs)
    app_evs.emit('connect_network', self.view_bssid)

  def on_btn_pressed_SaveNet(self, button=None):
    self.gather_net_prefs()
    cnl.set_network_properties_by_bssid(self.view_bssid, self.net_prefs)
    cnl.save_network_by_bssid(self.view_bssid, self.net_prefs)

  def on_btn_pressed_ForgetNet(self, button=None):
    cnl.del_network_by_bssid(self.view_bssid)
    app_evs.emit('show_mainview')

  def on_chk_toggled_ShowClearPSK(self, button=None):
    if button.get_active():
      self.SecPSK.set_property('visibility', True)
    else:
      self.SecPSK.set_property('visibility', False)

  def initialize_View(self, bssid):
    self.view_bssid = bssid
    net_data = cnl.get_network_by_bssid(bssid)

    if not net_data:
      print('This Network is Not in List!')
      return False

    if net_data['is_known']:
      self.ForgetButton.show()
    else:
      self.ForgetButton.hide()

    if 'PSK' in net_data['net_prefs']:
      self.SecPSK.set_text(net_data['net_prefs']['PSK'])

    ssid = net_data['net_stat']['ssid']
    self.TopBarBSSID.set_label(bssid)
    self.TopBarSSID.set_label(ssid)


class MainWindow(Gtk.ApplicationWindow):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

    self._ui = Gtk.Builder()
    self._ui.add_from_file('data/ui/Main.ui')

    self.MBox = self._ui.get_object('MainBox')
    self.add(self.MBox)

    self.PStatus = self._ui.get_object('ProgStatus')
    self.CStatus = self._ui.get_object('ConnStatus')

    self.MStack = self._ui.get_object('MainStack')

    self.MView = MainView()

    self.NPrefsView = Gtk.Box()

    self.MStack.add_named(self.MView, 'MainView')
    self.MStack.add_named(self.NPrefsView, 'NetworkPrefsView')

    self.MStack.set_visible_child_name('MainView')

    self.set_wh()


  def show_prog_status(self, is_ready):
    if is_ready:
      self.PStatus.set_label('READY')
    else:
      self.PStatus.set_label('BUSY')

  def show_conn_status(self, cstatus):
    self.CStatus.set_label(cstatus)

  def set_wh(self):
    self.set_property("width-request", 500)
    self.set_property("height-request", 400)

  def show_MainView(self):
    self.MStack.set_visible_child_name('MainView')

  def show_NetPreferencesView(self, bssid):
    for c in self.NPrefsView:
      self.NPrefsView.remove(c)

    ViewBox = NetPrefsView()
    ViewBox.initialize_View(bssid)
    self.NPrefsView.add(ViewBox)

    self.MStack.set_visible_child_name('NetworkPrefsView')
    ViewBox.show_all()
