#!/usr/bin/env python
#-*- coding: utf-8 -*-

import sys

sys.path.append('./modules/gui')

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from client_handler import Client


class NetworkEditor:
  def __init__(self, bssid, ssid=None):
    self.bssid = bssid

  def update_net_data_from_gui(self):
    self.PSK = MainWin.NetworkPrefPSK.get_text()

  def get_all_net_data(self):
    net_data = {'bssid': self.bssid,
                'PSK': self.PSK}
    return net_data


class MainWindowHandler:
  def onDestroy(self, *args):
    MainWin.destroy()

  def btnPressed_SwitchSecond(self, button=None):
    MainWin.MainStack.set_visible_child(MainWin.SecondView)

  def btnPressed_SwitchMain(self, button=None):
    MainWin.open_MainView()

  def btnPressed_Scan(self, button=None):
    Conn.ScanReq()

  def btnPressed_ConnectNow(self, button=None):
    MainWin.NetworkEdit.update_net_data_from_gui()
    net_data = MainWin.NetworkEdit.get_all_net_data()
    MainWin.open_MainView()

    Conn.ConnectToNetworkReq(net_data)


class MainWindow(Gtk.Window):
  def __init__(self):
    self.Win = None
    self.NetworkEdit = None

  def destroy(self):
    if self.Win:
      self.Win.close()
    self.Win = None

  def create(self):
    self._ui = Gtk.Builder()
    self._ui.add_from_file('data/ui/MainWin.ui')

    self.Win = self._ui.get_object('MainWindow')
    self.MainStack = self._ui.get_object('MainStack')

    self.MainView = self._ui.get_object('MainView')
    self.SecondView = self._ui.get_object('SecondView')
    self.NetPrefView = self._ui.get_object('NetPrefView')

    self.LC = self._ui.get_object('ListContainerBox')

    self.NetworkPrefPSK = self._ui.get_object('NetworkPrefPSK')

    self._ui.connect_signals(MainWindowHandler)

  def refresh_nets_list(self):
    for c in self.LC.get_children():
      self.LC.remove(c)

    for k in Nets.nets:
      It = NetItem(Nets.nets[k][3], k)
      It.btnConnect.set_name('Connect_' + k)
      It.btnPreferences.set_name('Prefs_' + k)
      self.LC.add(It.Box)

  def open_NetPrefs_view(self, bssid):
    self.NetworkEdit = NetworkEditor(bssid)

    titleSSID = self._ui.get_object('PrefNetSSID')
    titleBSSID = self._ui.get_object('PrefNetBSSID')

    titleBSSID.set_label(bssid)
    titleSSID.set_label(Nets.nets[bssid][3])

    MainWin.MainStack.set_visible_child(MainWin.NetPrefView)

  def open_MainView(self):
    self.NetworkEdit = None
    MainWin.MainStack.set_visible_child(MainWin.MainView)

  def show(self):
    self.refresh_nets_list()
    self.Win.show()


class NetItemHandler:
  def btnPressed_Connect(self, button=None):
    print(self.get_name())

  def btnPressed_Preferences(self, button=None):
    bssid = self.get_name().split('_')[1]
    MainWin.open_NetPrefs_view(bssid)

class NetItem(Gtk.Box):
  def __init__(self, ssid=None, bssid=None):
    self._ui = Gtk.Builder()
    self._ui.add_from_file('data/ui/ItemWidget.ui')

    self.Box = self._ui.get_object('ListItem')
    self.btnConnect = self._ui.get_object('Connect')
    self.btnPreferences = self._ui.get_object('Preferences')

    self._ui.connect_signals(NetItemHandler)

    if ssid:
      self.set_net_ssid(ssid)

    if bssid:
      self.set_net_bssid(bssid)

  def set_net_ssid(self, data):
    self._ui.get_object('SSID').set_label(data)

  def set_net_bssid(self, data):
    self._ui.get_object('BSSID').set_label(data)


class SysTrayIconHandler:
  def show_MainWin(self, button=None):
    if MainWin.Win:
      MainWin.destroy()
    else:
      MainWin.create()
      MainWin.show()

  def show_MainMenu(self, button=None, data=None):
    print('Show Main Menu')
    MainMenu.create()
    MainMenu.show(button, data)

class SysTrayIcon(Gtk.StatusIcon):
  def __init__(self):
    self._ui = Gtk.Builder()
    self._ui.add_from_file('data/ui/SysTrayIcon.ui')

    self._ui.connect_signals(SysTrayIconHandler)


class MainTrayMenu:
  def __init__(self):
    self.obj = None

  def create(self):
    self.obj = Gtk.Menu()
    close = Gtk.MenuItem("Close App")
    self.obj.append(close)
    close.connect_object("activate", MainQuit, "Quit")

  def show(self, event_button, event_time):
    for child in self.obj.get_children():
      child.show()
    self.obj.popup(None, None, None, None, event_button, event_time)


class Networks:
  def __init__(self):
    self.nets = {}

  def make_net_list(self, data):
    self.nets = data['results']

class GuiUpdater:
  def refresh_nets_list(self):
    MainWin.refresh_nets_list()


def MainQuit(data=None):
  Conn.Close()
  Gtk.main_quit()


Nets = Networks()
UpdateGui = GuiUpdater()

Conn = Client(Nets, UpdateGui)
Conn.MakeNewConnection()
serv_io = GLib.io_add_watch(Conn.sock.fileno(),
                            GLib.IO_IN,
                            Conn.HandleIncoming)


MainMenu = MainTrayMenu()

MainWin = MainWindow()

SysTrayIt = SysTrayIcon()

Gtk.main()
