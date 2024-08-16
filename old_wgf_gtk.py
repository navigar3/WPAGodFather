#!/usr/bin/env python
#-*- coding: utf-8 -*-

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

class app:
  def __init__(self):
    self.si = Gtk.StatusIcon()
    self.si.set_from_stock(Gtk.STOCK_MEDIA_PLAY)
    self.si.connect('popup-menu', self.on_right_click)

  def connect_server(self):
    #io = GLib.IOChannel(the_socket)
    #self.serv_io = io.add_watch(GLib.IO_IN|GLib.IO_HUP,
    #                            the_handler,
    #                            priority=GLib.PRIORITY_HIGH)
    return True

  def make_main_menu(self, event_button, event_time, data=None):
    self.main_menu = Gtk.Menu()
    close = Gtk.MenuItem("Close App")
    self.main_menu.append(close)
    close.connect_object("activate", self.quit, "Quit")
    close.show()

    self.main_menu.popup(None, None, None, None, event_button, event_time)

  def on_right_click(self, data, event_button, event_time):
    self.make_main_menu(event_button, event_time)

  def quit(self, data):
    Gtk.main_quit()

if __name__ == '__main__':
  app()
  Gtk.main()
