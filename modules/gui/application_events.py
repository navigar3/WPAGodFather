import sys

sys.path.append('./modules/gui')

from gi.repository import GObject

from common import app

class AppEvents(GObject.GObject):
  __gsignals__ = {
    'quit_now':
      (GObject.SIGNAL_RUN_FIRST,
       None,
       ()),
    'showhide_main_win':
      (GObject.SIGNAL_RUN_FIRST,
       None,
       ()),
    'scan_for_networks':
      (GObject.SIGNAL_RUN_FIRST,
       None,
       ()),
    'scan_results':
      (GObject.SIGNAL_RUN_FIRST,
       None,
       ()),
    'set_busyready':
      (GObject.SIGNAL_RUN_FIRST,
       None,
       (int, )),
    'update_inet_status':
      (GObject.SIGNAL_RUN_FIRST,
       None,
       ()),
    'show_netprefsview':
      (GObject.SIGNAL_RUN_FIRST,
       None,
       (str, )),
    'show_mainview':
      (GObject.SIGNAL_RUN_FIRST,
       None,
       ()),
    'connect_network':
      (GObject.SIGNAL_RUN_FIRST,
       None,
       (str, )),}

  def do_quit_now(self):
    print('quit_now rosen')
    app.app.ud_quit()

  def do_showhide_main_win(self):
    print('showhide_main_win rosen')
    app.app.ud_showhide_main_win()

  def do_scan_for_networks(self):
    app.app.ud_scan_for_networks()

  def do_scan_results(self):
    app.app.ud_refresh_scan_results()

  def do_set_busyready(self, data):
    app.app.ud_set_ui_status(data)

  def do_update_inet_status(self):
    app.app.ud_set_ui_inet_status()

  def do_show_netprefsview(self, data):
    app.app.ud_show_net_prefs_view(data)

  def do_show_mainview(self):
    app.app.ud_show_mainview()

  def do_connect_network(self, data):
    app.app.ud_connect_network(data)


app_evs = AppEvents()
