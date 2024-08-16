import os
import time

LOGLEVEL = 9


class wlogger:
  def __init__(self, lprefix,
               outfd_def=2,
               date_format='%d %b %Y %H:%M:%S'):
    self._ofd = outfd_def
    self._lprefix = lprefix
    self._df = date_format
  
  def log(self, msg, out='def', lev=0):
    # Check log level
    if lev <= LOGLEVEL:
      
      # Set output file descriptor
      if out == 'def':
        ofd = self._ofd
      else:
        ofd = out
      
      # Compose message
      lmsg = time.strftime(self._df) + \
        ' [' + self._lprefix + '] ' + msg + '\n'
      
      # Write message to appropriate file descriptor
      os.write(ofd, lmsg.encode())
