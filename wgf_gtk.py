#!/usr/bin/env /usr/bin/python3
#-*- coding: utf-8 -*-

import sys

sys.path.append('./modules/gui')

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from application import Application
from common import app

if __name__ == "__main__":
  app.register_app(Application())
  app.app.run(sys.argv)
