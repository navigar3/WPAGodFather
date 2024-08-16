NAMED_SOCKET="/run/wpagf.sock"

import sys, os
import socket

import time

import json

class Client:
  def __init__(self, NetsObject, GuiHandler):
    self.nets = NetsObject
    self.guih = GuiHandler

  def Close(self):
    self.sock.sendall(b'QUIT')
    self.sock.close()
    self._listener_sock.close()
    os.remove(self.cli_sock_name)

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
          return False

        if data['msg'] == 'SCAN_RESULTS':
          self.nets.make_net_list(data['data'])
          self.guih.refresh_nets_list()

    return True

  def ScanReq(self, opt=None, dt=None):
    self.sock.sendall(b'SCAN')

  def ConnectToNetworkReq(self, net_data):
    bssid = net_data['bssid']
    PSK = net_data['PSK']
    hex_PSK = ''
    for c in PSK:
      hex_PSK += '%02x' % ord(c)

    r = 'CONNECT BSSID %s SECTYPE %s PSK %s IPMODE %s' % \
      (bssid, 'psk', hex_PSK, 'dhcp')
    print(r)
    self.sock.sendall(r.encode())

  def MakeNewConnection(self):

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

      self._listener_sock = sc
      self.sock = c
      self.cli_sock_name = cli_sock_name

      #
      # Handshake completed
