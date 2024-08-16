import os
import sys
import time

import re

import json

import subprocess as sp

import threading
import selectors

import socket

from wsockio import wsockio
from wpagf_logger import LOGLEVEL, wlogger


sel = selectors.DefaultSelector()



class client_manager:
  def __init__(self, tname, client_sock, evfds, thread_queues):

    self.tname = tname
    self.cs = client_sock
    self.evfdr = evfds[1]
    self.evfdw = evfds[0]

    self._qtomain = thread_queues[0]
    self._qfrommain = thread_queues[1]

    self.master = False

    self.clievfdr = None
    self.clievfdw = None
    self._qtocli = None
    self._qfromcli = None

    self.csel = selectors.DefaultSelector()

    # Register events
    self.csel.register(self.cs, selectors.EVENT_READ, data=b'clisock')
    self.csel.register(self.evfdr, selectors.EVENT_READ, data=b'evfd')
    
    self.log = wlogger('Thread %s' % self.tname)

    self.status = 'IDLE'

    self.handlers = {}
    self.add_handler(self.clisock_h, b'clisock')
    self.add_handler(self.evfd_h, b'evfd')
    self.add_handler(self.evfdcli_h, b'clievfd')

    self.client_answer = []

    self.go()


  def add_handler(self, handler, handler_name):
    self.handlers[handler_name] = handler


  def send_to_cclient(self, data):
    self.client_answer.append(json.dumps(data).encode() + b'\n')
    self.csel.modify(self.cs,
                     selectors.EVENT_READ|selectors.EVENT_WRITE,
                     data=b'clisock')
    self.status = 'SEND_ANSWER'


  def send_to_main(self, data):
    self._qtomain.put_nowait(data)
    os.eventfd_write(self.evfdw, 0x10)

  def send_to_cli(self, data):
    self._qtocli.put_nowait(data)
    os.eventfd_write(self.clievfdw, 0x10)

  # Handle requests from and to client socket
  def clisock_h(self, mask, key):

    # Read request
    if mask & selectors.EVENT_READ:
      try:
        r = self.cs.recv(1024)
      except:
        self.log.log('Error while reading!')
        return False

      if not r:
        self.log.log('Warning! Empty buffer!')
        return False

      self.log.log('Recv: %s' % r.decode(), lev=8)

      if r == b'GET_INET_STATUS':
        if self.master:
          # Request Status to cli Thread.
          self.send_to_cli({'action': 'MAS_GET_STATUS'})

          self.status = 'IDLE'

          return True

      elif r == b'SCAN':
        if self.master:
          # Scan Request to cli Thread.
          self.send_to_cli({'action': 'MAS_SCAN'})
          self.status = 'IDLE'
          return True

      elif r[0:8] == b'CONNECT ':
        d={}

        # Parse request
        try:
          fields = \
            {'BSSID':
               ({'match': '([a-f0-9][a-f0-9]:){5}[a-f0-9][a-f0-9]$',
                'next': 'SECTYPE'}, ),
             'SECTYPE':
               ({'match': 'open$', 'next': 'IPMODE'},
                {'match': 'psk$', 'next': 'PSK'}),
             'PSK':
               ({'match': '([a-f0-9][a-f0-9]){2,256}$', 'next': 'IPMODE'}, ),
             'IPMODE':
               ({'match': 'dhcp$', 'next': 'END'}, )
            }

          pars = r.decode().split(' ')

          pars.pop(0)

          cur_p = pars[0]

          if cur_p != 'BSSID':
            cur_p = 'BREAK'

          while cur_p != 'END':

            if cur_p == 'BREAK':
              d = None
              break

            if not cur_p in fields:
              d = None
              break

            next_p = 'BREAK'
            for match in fields[cur_p]:
              f = re.match(match['match'], pars[1])

              if not f is None:
                d[cur_p] = f.string
                next_p = match['next']
                pars.pop(0)
                pars.pop(0)
                fields.pop(cur_p)
                break

            cur_p = next_p

        except:
          self.log.log('Bad CONNECT Request!', lev=8)

        if not d is None:
          self.send_to_cli({'action': 'MAS_CONNECT', 'data': d})
          self.status = 'IDLE'
        else:
          self.log.log('Malformed CONNECT Request!')
          self.send_to_cclient('REQUEST_ERROR')

        return True


      elif r == b'QUIT':
        self.log.log('Quit!')

        return False


      elif self.status == 'IDLE':
        self.log.log('IDLE State: doing nothing...')

        return True

      self.log.log('Warning! Unexpeceted Status!')
      return False

    # Write request
    elif mask & selectors.EVENT_WRITE:

      self.log.log('Sending an answer')
      #print(self.client_answer)

      # Send answer to connected client.
      if self.status == 'SEND_ANSWER':
        if self.client_answer:
          cans = self.client_answer.pop(0)
          self.cs.sendall(cans)
        else:
          self.csel.modify(self.cs, selectors.EVENT_READ, data=key)
          self.status = 'IDLE'
          #self.log.log('Warning: empty answer!')

        return True

      return False



  # Handle requests from and to eventfd Main Thread interface
  def evfd_h(self, mask, key):

    # Only read request could be handled
    if not (mask & selectors.EVENT_READ):
      return False

    dt = os.eventfd_read(self.evfdr)

    # If dt is 1 quit.
    if dt & 0x0f:
      self.status = 'TERMINATED'
      self.log.log('Got Termination Event from MAIN.')
      return False

    # Message in queue from MAIN
    elif dt & 0xf0:
      while not self._qfrommain.empty():
        qi = self._qfrommain.get()
        if qi['action'] == 'SET_MASTER':
          (self.clievfdr, self.clievfdw) = qi['data']['cli_evfd']
          (self._qfromcli, self._qtocli) = qi['data']['cli_queue']
          self.csel.register(self.clievfdr, selectors.EVENT_READ, data=b'clievfd')
          self.master = True

          # Register new master thread to cli Thread.
          self.send_to_cli({'action': 'NEW_MASTER'})

          # Notice connected client.
          self.send_to_cclient({'msg': 'CLI_STATUS_CHANGE',
                                'data': {'is_master': True}
                               }
                              )

      return True

    return False


  # Handle requests from and to eventfd cli Thread interface
  def evfdcli_h(self, mask, key):

    self.log.log('An event from cli Thread.')

    # Only read request could be handled
    if not (mask & selectors.EVENT_READ):
      return False

    dt = os.eventfd_read(self.clievfdr)

    if dt & 0xf0:
      while not self._qfromcli.empty():

        qi = self._qfromcli.get()

        if qi['action'] == 'BUSY':
          self.send_to_cclient({'msg':'BUSY'})

        elif qi['action'] == 'READY':
          self.send_to_cclient({'msg': 'READY'})

        elif qi['action'] == 'UPDATE_INET_STATUS':
          self.send_to_cclient({'msg': 'UPDATE_INET_STATUS',
                                'data': qi['data']})

        elif qi['action'] == 'GOT_INET_STATUS':
          self.send_to_cclient({'msg': 'GOT_INET_STATUS',
                                'data': qi['data']})

        elif qi['action'] == 'SCANNING':
          self.send_to_cclient({'msg': 'SCANNING'})

        elif qi['action'] == 'SCAN_RESULTS':
          self.send_to_cclient({'msg': 'SCAN_RESULTS',
                                'data': qi['data']})

        elif qi['action'] == 'CONNECTION_FAILED':
          self.send_to_cclient({'msg': 'CONNECTION_FAILED',
                                'data': qi['data']})

      return True


  def go(self):

    # Thread main loop
    try:

      KEEP_RUNNING = True

      while KEEP_RUNNING:

        self.log.log('status = %s' % self.status)

        ev = self.csel.select(timeout=None)
        # Debug
        print (' **** [Thread %s]' % self.tname, ev)

        for key, mask in ev:
          if key.data in self.handlers:
            if not self.handlers[key.data](mask, key.data):
              KEEP_RUNNING = False

    finally:
      pass


    # Send termination event to cli Thread if this is
    #  master Thread.
    if self.master == True:
      os.eventfd_write(self.clievfdw, 1)

    # Send termination event to main
    if self.status != 'TERMINATED':
      os.eventfd_write(self.evfdw, 1)

    if not self.clievfdr is None:
      self.csel.unregister(self.clievfdr)

    self.csel.unregister(self.evfdr)
    self.log.log('Closing socket...')
    self.csel.unregister(self.cs)
    self.cs.close()


    self.log.log('Finish.')


# handshake with new clients
class cli_handshake:
  def __init__(self, sock, data):
    self.sock = sock
    self.k = data
    
    self.log = wlogger('MAIN.cli_handshake')
    
    self._ws = wsockio(self.sock, 'MAIN.cli_handshake')

    self._token = b'token:' + data
    self.sc = None
    self._con_open = True
    self.status = 'NEW'
    

  def closecon(self):
    if self._con_open is True:
      sel.unregister(self.sock)
      self.sock.close()

      self._con_open = False


  def communicate(self, mask):

    self.log.log('status = %s' % self.status, lev=5)

    # Read events
    if mask & selectors.EVENT_READ:

      # Read data
      #r = self._read()
      r = self._ws.read()
      if r is None:
        self.closecon()
        return False

      # Status is NEW
      if self.status == 'NEW':

        if r == b'NEWCONREQ':
          self.status = 'OK_NEWCONREQ'
          sel.modify(self.sock, selectors.EVENT_READ|selectors.EVENT_WRITE, data=self.k)
          return True

        else:
          self.log.log('Unknown request!')
          self.closecon()
          return False

      # Status is GETSOCKNAME: get socket name, check if
      #  socket is usable.
      elif self.status == 'GETSOCKNAME':

        handshake_success = False
        
        client_sock_name = r.decode()
        self.log.log('Received socket name %s' % client_sock_name)

        try:
          # Try to connect with client managed socket.

          self.sc = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
          self.sc.connect(client_sock_name)

          handshake_success = True

        except Exception as e:
          self.log.log("Error while opening socket %s due to %s" % \
            (client_sock_name, e))
          self.closecon()

        if handshake_success is True:
          self.status = 'SUCCESS'
          sel.modify(self.sock, selectors.EVENT_READ|selectors.EVENT_WRITE, data=self.k)
          return True

        return False

      # Handshake ends successfull. Check answer and close this socket.
      elif self.status == 'HSHAKE_END':
        if r == b'OK':
          # Everything fine: close and return True
          self.closecon()

          return self.sc

        else:
          self.log.log('Error in handshake while reading token!')

        self.closecon()

        return False

      # Unknown status
      self.log.log('Read end: error while communicating with client!')
      return False



    # Write events
    elif mask & selectors.EVENT_WRITE:

      # Status is OK_NEWCONREQ
      if self.status == 'OK_NEWCONREQ':

        if self._ws.write(b'OK'):
          sel.modify(self.sock, selectors.EVENT_READ, data=self.k)
          self.status = 'GETSOCKNAME'
          return True

        self.closecon()
        return False

      elif self.status == 'SUCCESS':
        if self._ws.write(self._token):
          sel.modify(self.sock, selectors.EVENT_READ, data=self.k)
          self.status = 'HSHAKE_END'
          return True
        
        self.closecon()
        return False

      # Unknown status
      self.log.log('Write end: error while communicating with client!')
      self.closecon()
      return False
