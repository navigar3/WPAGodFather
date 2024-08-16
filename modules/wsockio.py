import socket

from wpagf_logger import wlogger

class wsockio:
  def __init__(self, s, wlogprefix=''):
    self._s = s
    
    self.log = wlogger(wlogprefix)
  
  # Read from socket
  def read(self, bsz=1024):
    r=b''
    try:
      r = self._s.recv(bsz)
    except Exception as e:
      self.log.log('Error while reading from socket due to %s!' % e)
      return None

    if not r:
      self.log.log('Error while reading from socket: empty data buffer!')
      
      #self.closecon()

      return None

    self.log.log('Recvd %s' % r, lev=10)

    return r
  
  # Write to socket
  def write(self, d):
    try:
      d = self._s.sendall(d)
      return True
    except Exception as e:
      self.log.log('Error while writing into socket due to %s!' % e)
      #sel.unregister(self.sock)
      #self.sock.close()
      return False
