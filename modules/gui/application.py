import sys

sys.path.append('./modules/gui')

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Gio

from status_icon import StatusIcon
from main_win import MainWindow
from client_server_io import Client
from common import cnl, stor


class Application(Gtk.Application):
  def __init__(self, *args, **kwargs):
    super().__init__(
      *args,
      application_id="org.example.wpagf",
      flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
      **kwargs
    )
    self.window = None

    self.add_main_option(
      "test",
      ord("t"),
      GLib.OptionFlags.NONE,
      GLib.OptionArg.NONE,
      "Command line test",
      None,
    )

  def do_startup(self):
    Gtk.Application.do_startup(self)

    action = Gio.SimpleAction.new("about", None)
    action.connect("activate", self.on_about)
    self.add_action(action)

  def do_activate(self):
    self.status_icon = StatusIcon()

    self.client = Client()

    if self.client.ConnectServer():
      if not self.window:
        self.window = MainWindow(application=self, title="WPA GodFather")

        # Hide window on delete-event
        self.window.connect('delete-event', self.on_main_win_delete)

        # Register new socket event watch
        self.serv_io = \
          GLib.io_add_watch(self.client.get_client_sock().fileno(),
                            GLib.IO_IN,
                            self.client.HandleIncoming)

        self.server_ready = True
        self.net_status = {}

        self.client.GetNetStatus()

    else:
      if not self.window:
        self.window = Gtk.Dialog(application=self, title="Error")
        self.window.add_buttons(
          Gtk.STOCK_CANCEL,
          Gtk.ResponseType.CANCEL,
          Gtk.STOCK_OK, Gtk.ResponseType.OK
        )

        self.window.set_default_size(150, 100)

        label = Gtk.Label(label="Cannot connect with server! Is server alive?")

        box = self.window.get_content_area()
        box.add(label)
        self.window.show_all()

        # Hide window on delete-event
        self.window.connect('delete-event', self.on_main_win_delete)


  def do_command_line(self, command_line):
    options = command_line.get_options_dict()
    # convert GVariantDict -> GVariant -> dict
    options = options.end().unpack()

    if "test" in options:
      # This is printed on the main instance
      print("Test argument recieved: %s" % options["test"])

    self.activate()
    return 0

  def on_about(self, action, param):
    about_dialog = Gtk.AboutDialog(transient_for=self.window, modal=True)
    about_dialog.present()

  def on_main_win_delete(self, action, param):
    if self.window:
      if self.window.is_visible():
        self.window.hide()
    return Gdk.EVENT_STOP

  def set_net_status(self, ns):
    self.net_status = ns

  def ud_set_ui_inet_status(self):
    if self.net_status['wpa_state'] == 'DISCONNECTED':
      self.window.show_conn_status('Disconnected')
    elif self.net_status['wpa_state'] == 'COMPLETED':
      if 'ip_address' in self.net_status:
        ip_address = self.net_status['ip_address']
      else:
        ip_address = 'Not Set'
      self.window.show_conn_status('Connected with %s (%s)' % \
        (self.net_status['ssid'], ip_address))

  def ud_set_ui_status(self, is_ready):
    if is_ready == 0:
      self.window.show_prog_status(False)
    else:
      self.window.show_prog_status(True)

  def ud_scan_for_networks(self):
    self.window.MView.flush_network_list()
    self.client.ScanReq()

  def ud_refresh_scan_results(self):
    self.window.MView.refresh_networks_list()

  def ud_show_mainview(self):
    self.window.MView.refresh_networks_list()
    self.window.show_MainView()

  def ud_show_net_prefs_view(self, bssid):
    self.window.show_NetPreferencesView(bssid)

  def ud_connect_network(self, bssid):
    self.window.show_MainView()
    self.client.ConnectToNetworkReq(bssid)

  def ud_quit(self):
    self.client.Close()
    stor.close()
    print('Bye')
    self.quit()

  def ud_showhide_main_win(self):
    if self.window:
      if self.window.is_visible():
        print("Hiding window...")
        self.window.hide()
      else:
        print("Showing window...")
        self.window.show_all()
        self.window.MView.refresh_networks_list()
