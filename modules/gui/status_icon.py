import sys

sys.path.append('./modules/gui')

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from common import app
from application_events import app_evs

class StatusIcon(Gtk.StatusIcon):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

    self.set_tooltip_text('WPA GodFather')
    self.set_from_icon_name('network-connect')
    self.set_visible(True)
    self.connect("activate", self._on_popup_menu)
    self.connect("popup-menu", self._on_activate)

  def _on_activate(self, *args):
    app_evs.emit("quit_now")

  def _on_popup_menu(self, *args):
    app_evs.emit("showhide_main_win")
    print('popup menu called with ', args)
