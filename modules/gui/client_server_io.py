NAMED_SOCKET="/run/wpagf.sock"

import sys, os
import socket

import time

import json

from application_events import app, app_evs
from common import cnl

class Client:
  def __init__(self):
    self._listener_sock = None
    self.cli_sock_name = None
    self.sock = None
    self.is_server_connected = False
    self.is_master = False


  def get_client_sock(self):
    return self.sock

  def Close(self):
    if self.sock:
      self.sock.sendall(b'QUIT')
      self.sock.close()
    if self._listener_sock:
      self._listener_sock.close()
    if self.cli_sock_name:
      os.remove(self.cli_sock_name)

  def GetNetStatus(self):
    self.sock.sendall(b'GET_INET_STATUS')

  def ScanReq(self):
    self.sock.sendall(b'SCAN')

  def ConnectToNetworkReq(self, bssid):
    net_data = cnl.get_network_by_bssid(bssid)

    PSK = net_data['net_prefs']['PSK']
    hex_PSK = ''
    for c in PSK:
      hex_PSK += '%02x' % ord(c)

    r = 'CONNECT BSSID %s SECTYPE %s PSK %s IPMODE %s' % \
      (bssid, 'psk', hex_PSK, 'dhcp')
    print(r)
    self.sock.sendall(r.encode())

  def HandleIncoming(self, opt=None, dt=None):
    raw_data = self.sock.recv(1024)
    print('Recv ' + str(raw_data))
    for r in raw_data.decode().split('\n'):
      if r != '':
        try:
          data = json.loads(r)
        except:
          print('Error while decoding json payload from server!')
          self.Close()
          self.is_server_connected = False
          return False

        print(data)

        if data['msg'] == 'READY':
          app_evs.emit('set_busyready', 1)

        elif data['msg'] == 'BUSY':
          app_evs.emit('set_busyready', 0)

        elif data['msg'] == 'SCAN_RESULTS':
          if 'data' in data:
            if 'results' in data['data']:
              cnl.flush_netslist()
              for k, v in data['data']['results'].items():
                cnl.add_network(k, v)

              app_evs.emit('scan_results')

        elif data['msg'] == 'GOT_INET_STATUS' or data['msg'] == 'UPDATE_INET_STATUS':
          app.app.set_net_status(data['data'])
          app_evs.emit('update_inet_status')



    # Must return True because is a watch handler
    return True


  def ConnectServer(self):
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

      try:
        sc = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sc.bind(cli_sock_name)
      except:
        print('Cannot bind local socket')
        return False

      print("Sending username %s ..." % os.getlogin())
      s.sendall(os.getlogin().encode())

      hs_token = s.recv(1024)

      s.sendall(b'GOON')

      while True:
        sc.listen()
        (c, a) = sc.accept()
        twin_token = c.recv(1024)

        if twin_token == hs_token:
          c.sendall(b'OK')
          break

        else:
          sys.stderr.write("Handshake token mismatch!\n")
          c.sendall(b'ERR_HS_TOKEN')
          c.close()

      token = s.recv(1024)
      if token:
        print('token: ', token)
        s.sendall(b'OK')
        s.close()
      else:
        sys.stderr.write("An error occurred!\n")
        return False

      self._listener_sock = sc
      self.sock = c
      self.cli_sock_name = cli_sock_name

      self.is_server_connected = True

      return True

      #
      # Handshake completed
