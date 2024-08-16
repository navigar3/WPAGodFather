#!/usr/bin/env /usr/bin/python3
#-*- coding: utf-8 -*-

NAMED_SOCKET="/run/wpagf.sock"

import sys, os
import socket

import selectors

import time

sys.path.append('./modules')

from wsockio import wsockio

def launch_cli():

  # Try to connect with server socket
  try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(NAMED_SOCKET)
  except Exception as e:
    sys.stderr.write("Cannot connect with server: %s\n" % e)
    return False

  # Send New Connection Request
  s.sendall(b"NEWCONREQ")

  if s.recv(1024) == b"OK":
    cli_sock_name = '/run/user/' + str(os.getuid()) + '/wpagf.sock'
    sc = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sc.bind(cli_sock_name)

    print("Sending sock name %s ..." % cli_sock_name)
    s.sendall(cli_sock_name.encode())

    sc.listen()
    (c, a) = sc.accept()
    print("--->", c)

    token = s.recv(1024)
    if token:
      print('token: ', token)
      s.sendall(b'OK')
      s.close()
    else:
      sys.stderr.write("An error occurred!\n")
      sys.exit(1)

  #
  # Handshake completed


  # Start Loop
  c.setblocking(False)
  os.set_blocking(0, False)
  sel = selectors.DefaultSelector()
  sel.register(c, selectors.EVENT_READ, data=b'wpagf_sock')
  sel.register(0, selectors.EVENT_READ, data=b'STDIN')

  STATUS = 'IDLE'
  REQUEST = None
  KEEP_RUNNING = True

  ws = wsockio(c)

  while KEEP_RUNNING:
    ev = sel.select(timeout=None)

    for key, mask in ev:
      if key.data == b'wpagf_sock':

        # Read event
        if mask & selectors.EVENT_READ:
          dt = ws.read()

          if dt is None:
            STATUS = 'ERR_TERMINATE'
            KEEP_RUNNING = False
            break

          print ('Data from wpagf:', dt)

        # Write event
        if mask & selectors.EVENT_WRITE:
          if STATUS == 'TERMINATE':
            ws.write(b'QUIT')
            sel.modify(c, selectors.EVENT_READ,
                       data=b'wpagf_sock')
            KEEP_RUNNING = False
            break

          elif STATUS == 'GET_INET_STATUS':
            ws.write(b'GET_INET_STATUS')
            sel.modify(c, selectors.EVENT_READ,
                       data=b'wpagf_sock')
            STATUS = 'IDLE'

          elif STATUS == 'SCAN':
            ws.write(b'SCAN')
            sel.modify(c, selectors.EVENT_READ,
                       data=b'wpagf_sock')
            STATUS = 'IDLE'

          elif STATUS == 'CONNECT':
            if not REQUEST is None:
              ws.write(REQUEST.encode())
              sel.modify(c, selectors.EVENT_READ,
                         data=b'wpagf_sock')
              REQUEST = None
            STATUS = 'IDLE'

      if key.data == b'STDIN':

        dt = os.read(0, 1024)

        if dt == b'quit\n':
          STATUS = 'TERMINATE'
          sel.modify(c, selectors.EVENT_READ|selectors.EVENT_WRITE,
                     data=b'wpagf_sock')

        elif dt == b'inet_status\n':
          STATUS = 'GET_INET_STATUS'
          sel.modify(c, selectors.EVENT_READ|selectors.EVENT_WRITE,
                     data=b'wpagf_sock')

        elif dt == b'scan\n':
          STATUS = 'SCAN'
          sel.modify(c, selectors.EVENT_READ|selectors.EVENT_WRITE,
                     data=b'wpagf_sock')

        elif dt[0:5] == b'conn ':
          # Connect request

          REQUEST = None

          try:
            params = dt[:-1].decode().split(' ')
            if len(params) >= 3:
              REQUEST = 'CONNECT BSSID %s SECTYPE %s PSK %s IPMODE %s' % \
                (params[1], "psk", params[2], "dhcp")
          except:
            REQUEST = None

          if not REQUEST is None:
            STATUS = 'CONNECT'
            sel.modify(c, selectors.EVENT_READ|selectors.EVENT_WRITE,
                       data=b'wpagf_sock')
          else:
            sys.stderr.write('Malformed request!\n')

        else:
          sys.stderr.write('Unknown command!\n')


  #c.sendall(b'GET_INET_STATUS')
  #print(" Recv:", c.recv(1024))


  c.close()
  sc.close()

  os.remove(cli_sock_name)

  print ('Bye!')

  return True


if __name__ == "__main__":
  if launch_cli():
    sys.exit(0)
