import os
import sys
import time

import subprocess as sp

import threading
import selectors

import socket

import queue

sys.path.append('./modules')

from wpagf_logger import LOGLEVEL, wlogger

from wpagf_cli_manager import wpa_cli_manager
from wpagf_clients import sel, cli_handshake, client_manager


PIDFILE='/run/wpagf.pid'
SOCKFILE='/run/wpagf.sock'

WPAGF_GID=1236
WPAGFMASTER_GID=1237


ClientsNum = 0
NewClients = {}

CliMans = {}


class wpa_supplicant_manager:
  def __init__(self,
               prgname='wpa_supplicant',
               driver='nl80211', iface='wlan0', ctrliface='/run/wpagf'):

    self.sel = selectors.DefaultSelector()
    self.evfd = os.eventfd(0)

    self.sel.register(self.evfd, selectors.EVENT_READ, data=b'wpa_supplicant_manager')

    self.prgname=prgname
    self.driver=driver
    self.iface=iface
    self.ctrliface=ctrliface
    
    self.log = wlogger('WPAS')

  def get_evfd(self):
    return self.evfd

  def run(self):
    self.log.log('Running wpa_supplicant.')

    # Run wpa_supplicant
    self.p = sp.Popen([self.prgname,
                       '-D', self.driver, '-i', self.iface,
                       '-C', self.ctrliface], stdout=sp.DEVNULL)

  def terminate(self):
    self.log.log('Terminating...')
    self.p.terminate()


def launch_server():

  global ClientsNum
  
  mlog = wlogger('MAIN')

  mlog.log('Server start')

  # Check if main process pidfile exists
  if os.path.exists(PIDFILE):
    mlog.log("Pidfile %s exist! Please remove it before launching server"
             "Exiting..." % PIDFILE)
    sys.exit(1)

  # Write PIDFILE
  try:
    with open(PIDFILE, "w") as pidf:
      pidf.write(str(os.getpid()))
  except Exception as e:
    mlog.log("Error while writing %s: %s" % (PIDFILE, e))

  # Check for named socket
  if os.path.exists(SOCKFILE):
    os.remove(SOCKFILE)

  # Create and bind socket
  try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
  except Exception as e:
    mlog.log("Error while opening socket: %s" % e)
    sys.exit(1)

  try:
    s.bind(SOCKFILE)
  except Exception as e:
    mlog.log("Error while opening named socket %s: %s" % (SOCKFILE, e))
    sys.exit(1)

  # Change socket group and permissions
  os.chown(SOCKFILE, 0, WPAGF_GID)
  os.chmod(SOCKFILE, 0o770)


  # wpa_supplicant manager
  wpas = wpa_supplicant_manager()
  wpas.run()
  
  # Create eventfd interfaces
  clievfds = (os.eventfd(0), os.eventfd(0))
  os.set_blocking(clievfds[0], False)
  os.set_blocking(clievfds[1], False)
  sel.register(clievfds[0], selectors.EVENT_READ, data=b'clievfd')
  cli_master_fds = (os.eventfd(0), os.eventfd(0))
  os.set_blocking(cli_master_fds[0], False)
  os.set_blocking(cli_master_fds[1], False)
  
  # Initialize two pair of Queue
  #  (first for reading and second for writing).
  wpa_cli_Q = (queue.Queue(), queue.Queue())
  master_thread_Q = (queue.Queue(), queue.Queue())
  
  # Launch wpa_cli thread
  wpa_cli_thread = threading.Thread(target=wpa_cli_manager, 
                                    args=(clievfds, cli_master_fds,
                                          wpa_cli_Q, master_thread_Q))
  wpa_cli_thread.start()


  # Ready or busy?
  wpa_cli_busy = False

  # Device Status
  inet_status = {'wpa_state': 'NOTDEFINED'}

  # Scanned networks
  scan_results = {}

  # Master client?
  has_master = False
  

  # Main loop
  while True:
    
    try:
      # Listen
      s.listen()
    except Exception as e:
      break

    # Set non-blocking
    s.setblocking(False)

    # Register event
    sel.register(s, selectors.EVENT_READ, data=b'main_sock')

    try:
      while True:
        ev = sel.select(timeout=None)
        print (ev)

        for key, mask in ev:

          # Handle new connection from main socket
          if key.data == b'main_sock':
            # Accept new connection
            (c, a) = key.fileobj.accept()

            # Set non-blocking
            c.setblocking(False)

            data = str('cli_%d' % ClientsNum).encode()
            mlog.log('New local connection %d accepted.' % ClientsNum)

            ClientsNum += 1

            # Add new file desc in selector
            sel.register(c, selectors.EVENT_READ, data=data)

            # Append object in NewClients dict
            NewClients[data] = cli_handshake(c, data)

          # Handle new client handshake
          elif key.data[0:4] == b'cli_':
            if key.data in NewClients:
              ans = NewClients[key.data].communicate(mask)

              # An error occurred in handshake: close connection and clean.
              if not ans:
                mlog.log('Removing client %s' % key.data)
                NewClients[key.data].closecon()
                del NewClients[key.data]

              # Handshake ends successfull: start new thread
              elif type(ans) == socket.socket:

                # Clean NewClients entry
                NewClients[key.data].closecon()
                del NewClients[key.data]

                # Create and register a pair of eventfd
                evfds = (os.eventfd(0), os.eventfd(0))
                os.set_blocking(evfds[0], False)
                os.set_blocking(evfds[1], False)
                evfd_ref = ('evfd____%04d' % ClientsNum).encode()
                data = evfd_ref
                sel.register(evfds[0], selectors.EVENT_READ, data=data)

                # Initialize a pair of Queue
                #  (first for reading and second for writing).
                thread_Queues = (queue.Queue(), queue.Queue())

                # Register new thread
                tname = 'th%04d' % ClientsNum
                t = threading.Thread(target=client_manager, 
                                     args=(tname, ans,
                                           evfds, thread_Queues))
                
                CliMetaData = {'th': t,
                               'is_master': False,
                               'is_in_master_grp': True,
                               'evfd': evfds,
                               'evfd_ref': evfd_ref,
                               'Queue': thread_Queues}

                CliMans[tname] = CliMetaData

                # Start thread
                t.start()

                # Check if we have already a master:
                #  if not promote this thread as master.
                if not has_master:
                  if CliMetaData['is_in_master_grp']:
                    qdata = {'action': 'SET_MASTER',
                             'data':
                               {'cli_evfd': cli_master_fds,
                                'cli_queue': master_thread_Q}
                            }
                    CliMetaData['Queue'][1].put_nowait(qdata)
                    os.eventfd_write(CliMetaData['evfd'][1], 0x10)
                    has_master = True
                    CliMans[tname]['is_master'] = True

          
          # Handle event from wpacli thread
          elif key.data[0:8] == b'clievfd':
            dt = os.eventfd_read(clievfds[0])
            if dt & 0xf0:

              # Read queue
              while not wpa_cli_Q[0].empty():
                mlog.log('Message in queue from thread WPACLI', lev=8)
                qcli = wpa_cli_Q[0].get()

                # Update device status
                if qcli['action'] == 'BUSY':
                  mlog.log('WPACLI is busy...', lev=9)
                  wpa_cli_busy = True
                elif qcli['action'] == 'READY':
                  mlog.log('WPACLI is now ready!', lev=9)
                  wpa_cli_busy = False
                elif qcli['action'] == 'UPDATE_INET_STATUS':
                  mlog.log('STATUS change!', lev=8)
                  inet_status = qcli['data']
                elif qcli['action'] == 'SCANNING':
                  mlog.log('Scanning for Networks...', lev=8)
                elif qcli['action'] == 'SCAN_RESULTS':
                  scan_results = qcli['data']
                  if len(scan_results['results']) == 0:
                    mlog.log('No networks found!')
                  else:
                    mlog.log('Scan results:')
                    for k in scan_results['results']:
                      slog = k + ' '
                      for f in scan_results['results'][k]:
                        slog += f + ' '
                      mlog.log(' -> %s' % slog)
                elif qcli['action'] == 'ASSOCIATING':
                  mlog.log('Associating...')
                else:
                  mlog.log('Unknown action %s!' % qcli['action'])


          # Handle event from thread
          elif key.data[0:8] == b'evfd____':
            # Get thread number
            etname = 'th' + key.data[8:].decode()
            if etname in CliMans:
              dt = os.eventfd_read(CliMans[etname]['evfd'][0])

              # Thread has finished, join it.
              if dt & 0x0f:
                mlog.log('Thread %s has finished.' % etname)
                CliMans[etname]['th'].join()
                if CliMans[etname]['is_master']:
                  has_master = False
                del CliMans[etname]

              else:
                mlog.log('Message in queue from thread %s.' % \
                         etname, lev=8)
                qth = CliMans[etname]['Queue'][0].get()

                

    except KeyboardInterrupt:

      # Send terminate event to all threads
      for thname in CliMans:
        mlog.log('Sending termination event to thread %s' % \
          thname)
        th = CliMans[thname]
        os.eventfd_write(th['evfd'][1], 1)
        sel.unregister(th['evfd'][0])
        th['th'].join()

      # Send termination event to wpa_cli_manager
      mlog.log('Waiting thread WPACLI to complete...')
      #os.eventfd_write(clievfds[1], 0x01)
      wpa_cli_thread.join()
      sel.unregister(clievfds[0])
      
      # Terminate wpa_supplicant
      wpas.terminate()
      
      # Unregister main socket and close connection
      sel.unregister(s)
      s.close()
      
      mlog.log('Terminated by user.')
      os.remove(PIDFILE)

    finally:
      pass

  return True


if __name__ == '__main__':
  if launch_server():
    sys.exit(0)

  sys.exit(1)
