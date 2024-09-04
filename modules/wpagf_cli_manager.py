import os
import sys
import time

import re

import subprocess as sp

import threading
import selectors

import socket


from wpagf_logger import LOGLEVEL, wlogger

class tgt_node:
  def __init__(self,
               current_status,
               onsuccess=None,
               onsuccess_exec=None,
               onfail=None,
               onfail_exec=None,
               params=None):
    self.current_status = current_status
    self.onsuccess = onsuccess
    self.onsuccess_exec = onsuccess_exec
    self.onfail = onfail
    self.onfail_exec = onfail_exec
    self.params = params

  def get_param(self, param_name):
    if self.params:
      if param_name in self.params:
        return self.params[param_name]


class tgt:
  def __init__(self, targetname=None,
               by=None, gparams={}):
    self.targetname = targetname
    self.by = by
    self.nodes = {}
    self.cnode = None
    self.gparams = gparams

    self.log = wlogger('WPACLI.Target')

    self.cnode_success = False
    self.status_params = []
    self.tgt_error = False

  def set_name(self, name, by=None):
    self.targetname = name
    self.by = by

  def start(self):
    self.log.log('Starting target %s by %s' % \
      (self.targetname, self.by))

  def set_gparam(self, gparam_name, gparam):
    if not gparam_name in self.gparams:
      self.gparams[gparam_name] = gparam

  def get_gparam(self, gparam_name):
    if self.gparams:
      if gparam_name in self.gparams:
        return self.gparams[gparam_name]

  def set_status_params(self, status_params):
    self.status_params.append(status_params)

  def get_status_params(self):
    if len(self.status_params) > 0:
      return self.status_params.pop(0)
    else:
      return ()

  def set_cnode_success(self, success):
    if self.targetname:
      self.cnode_success = success

  def add_node(self, nodename,
               current_status,
               onsuccess=None,
               onsuccess_exec=None,
               onfail=None,
               onfail_exec=None,
               params=None):

    # Nodename must NOT be already declared
    if nodename in self.nodes:
      self.log.log('Error while adding TARGET Node: Node %s already present!' % nodename)
      self.tgt_error = True
      return False

    # Add node
    self.nodes[nodename] = tgt_node(current_status,
                                    onsuccess,
                                    onsuccess_exec,
                                    onfail,
                                    onfail_exec,
                                    params)

    # Set entrypoint now if not already set
    if self.cnode is None:
      self.cnode = nodename

  def get_cnode_params(self, p):
    return self.nodes[self.cnode].get_param(p)

  def make_cmd_line(self, clist, cmd_params):
    lclist = len(clist)
    ccmd = ''
    for el in range(0, lclist):
      if type(clist[el]) == str:
        ccmd += clist[el]
      else:
        ccmd += cmd_params[clist[el]]
      if el < lclist - 1:
        ccmd += ' '

    self.log.log(' ===========', lev=7)
    self.log.log(' =========== ccmd is %s.' % ccmd, lev=7)

    return ccmd

  def pre_exec_cmd_line(self):
    pre_exec = None
    if self.cnode_success:
      pre_exec = self.nodes[self.cnode].onsuccess_exec
    else:
      pre_exec = self.nodes[self.cnode].onfail_exec

    if pre_exec:
      return self.make_cmd_line(pre_exec, self.get_status_params())
    else:
      return None

  def next_node(self):
    if self.targetname is None:
      return {'status': 'idle'}

    if self.tgt_error:
      return {'status': 'fail', 'opt': 'error'}

    if self.cnode_success:
      self.cnode = self.nodes[self.cnode].onsuccess
    else:
      self.cnode = self.nodes[self.cnode].onfail

    current_status = self.nodes[self.cnode].current_status
    ans = {'fsm_status': current_status}

    self.log.log('New node: %s, new status: %s' % \
      (self.cnode, current_status), lev=8)

    if self.cnode == 'end':
      self.log.log('Target reached!', lev=8)
      ans['status'] = 'success'
      self.flush_target()
    else:
      ans['status'] = 'running'

    return ans


  def flush_target(self):
    self.targetname = None
    self.nodes = {}
    self.cnode = None
    self.gparams = {}

    self.cnode_success = False
    self.status_params = []
    self.tgt_error = False




class wpa_cli_manager:
  def __init__(self,
               evfd, master_evfd,
               thread_queues, master_th_queues,
               prgname='wpa_cli', ctrliface='/run/wpagf'):

    self.prgname = prgname
    self.ctrliface = ctrliface
    
    self.sel = selectors.DefaultSelector()
    self.evfdr = evfd[1]
    self.evfdw = evfd[0]

    self.msevfdr = master_evfd[1]
    self.msevfdw = master_evfd[0]
    
    self.sel.register(self.evfdr, selectors.EVENT_READ,
                      data=b'wpa_cli_evfd')
    self.sel.register(self.msevfdr, selectors.EVENT_READ,
                      data=b'master_evfd')

    self.sel_timeout = None
    
    self._qtomain = thread_queues[0]
    self._qfrommain = thread_queues[1]

    self._qtomaster = master_th_queues[0]
    self._qfrommaster = master_th_queues[1]

    self.is_master_attached = False
    
    self.busy = True
    self.status = 'INIT'
    self.inetstatus = {'wpa_state': 'NOTDEFINED'}
    self.scan_timeout = 10
    self.assoc_timeout = 30
    self.dhcp_timeout = 30
    self.net_list = {}
    self.scan_res = {'scan_started': None,
                     'scan_end': None,
                     'request_from': None,
                     'results': None}
    self.target = tgt()
    self.current_op = {'name': None,
                       'running': None,
                       'cli_cmd': None,
                       'start_time': None,
                       'end_time': None,
                       'status': None,
                       'result': None}

    self.ifd = None
    self.ofd = None

    self.rls = []  # wpa_cli raw output lines
    self.ols = []  # wpa_cli command output lines
    self.els = []  # wpa_cli events output lines

    self.ipp = None # Ip subprocess handler
    self.ipod = None # Ip stdout pipe file handler
    self.iped = None # Ip stderr pipe file handler

    self.dp = None # Dhclient subprocess handler
    self.dfd = None # Dhclient pipe file descriptor

    # Setup events handlers
    self.handlers = {}
    self.add_handler(self.evfdmain_h, b'wpa_cli_evfd')
    self.add_handler(self.evfdmaster_h, b'master_evfd')
    self.add_handler(self.wpa_cli_stdout_h, b'wpa_cli_stdout')
    self.add_handler(self.dhclient_h, b'dhclient_stdout')
    self.add_handler(self.ip_stdout_h, b'ip_stdout')
    self.add_handler(self.ip_stderr_h, b'ip_stderr')
    
    self.log = wlogger('WPACLI')
    
    self.run_wpa_cli()
    self.run()
  

  def add_handler(self, handler, handler_name):
    self.handlers[handler_name] = handler

  def run_target(self, init_status, init_cmd=None):
    # Tell main and master I'm busy
    qans = {'action': 'BUSY'}
    self.send_to_main(qans)
    if self.is_master_attached:
      self.send_to_master(qans)

    self.busy = True
    self.status = init_status
    if init_cmd:
      self.submit_cmd(init_cmd)
    self.target.start()

  def next_target_node(self):
    pre_exec_cmd_line = self.target.pre_exec_cmd_line()
    if pre_exec_cmd_line:
      self.submit_cmd(pre_exec_cmd_line)

    status = self.target.next_node()
    if status['status'] == 'idle':
      self.log.log('Target is not set.')
    elif status['status'] == 'running':
      self.status = status['fsm_status']
    elif status['status'] == 'success':
      self.status = status['fsm_status']
      self.busy = False
      qans = {'action': 'READY'}
      self.send_to_main(qans)
      if self.is_master_attached:
        self.send_to_master(qans)

  def send_to_main(self, data):
    self._qtomain.put_nowait(data)
    os.eventfd_write(self.evfdw, 0x10)

  def send_to_master(self, data):
    self._qtomaster.put_nowait(data)
    os.eventfd_write(self.msevfdw, 0x10)

  # Handle eventfd interface request from MAIN
  def evfdmain_h(self, mask, key):
    # Only read request could be handled
    if not (mask & selectors.EVENT_READ):
      return False

    dt = os.eventfd_read(self.evfdr)

    # If dt is 1 quit.
    if dt & 0x0f:
      self.log.log('Got Termination Event from MAIN.')
      self.log.log('Terminating...')
      #self.submit_cmd('quit')
      return True

    elif dt & 0xf0:
      # Message in queue from MAIN

      # If busy empty queue and send BUSY!
      if self.busy:
        self.log.log('I''m busy just now!')
        while not self._qfrommaster.empty():
          qi = self._qfrommain.get()
        qans = {'action': 'BUSY'}
        self._qtomain.put_nowait(qans)
        os.eventfd_write(self.evfdw, 0x10)

      return True

    return False


  # Handle eventfd interface request from MASTER Thread
  def evfdmaster_h(self, mask, key):
    # Only read request could be handled
    if not (mask & selectors.EVENT_READ):
      return False

    dt = os.eventfd_read(self.msevfdr)

    # Check Queue
    if dt & 0xf0:

      # If busy empty queue and send BUSY!
      if self.busy:
        self.log.log('I''m busy just now!')
        while not self._qfrommaster.empty():
          qi = self._qfrommaster.get()
        qans = {'action': 'BUSY'}
        self._qtomaster.put_nowait(qans)
        os.eventfd_write(self.msevfdw, 0x10)


      while not self._qfrommaster.empty():
        qi = self._qfrommaster.get()
        if qi['action'] == 'NEW_MASTER':
          self.log.log('New Master Thread registered.')
          self.is_master_attached = True

        # Send INET STATUS
        elif qi['action'] == 'MAS_GET_STATUS':
          qans = {'action': 'GOT_INET_STATUS',
                  'data': self.inetstatus}
          self.send_to_master(qans)

        # Scan request
        elif qi['action'] == 'MAS_SCAN':
          self.scan_res['scan_started'] = time.time_ns()
          self.scan_res['scan_end'] = None
          self.scan_res['request_from'] = 'MASTER'
          self.scan_res['results'] = None

          self.target.set_name('SCAN', 'MASTER')
          self.target.add_node('do_scan', 'SCAN',
                               onsuccess='scanning')
          self.target.add_node('scanning', 'SCANNING',
                               onsuccess='get_scan_res',
                               onsuccess_exec=('scan_result', ))
          self.target.add_node('get_scan_res', 'GET_SCAN_RESULT',
                               onsuccess='get_status',
                               onsuccess_exec=('status', ))
          self.target.add_node('get_status', 'PARSE_INET_STATUS',
                               onsuccess='end')
          self.target.add_node('end', 'IDLE')

          self.run_target('SCAN', 'scan')

        # Connect Request
        elif qi['action'] == 'MAS_CONNECT':
          connect_attempt = {'assoc': {},
                             'net_details': qi['data']
                            }

          bssid = connect_attempt['net_details']['BSSID']

          # Is network protected with psk?
          use_psk = False
          if 'PSK' in connect_attempt['net_details']:

            hpsk = connect_attempt['net_details']['PSK']
            spsk = ''
            for i in range(0,int(len(hpsk)/2)):
              spsk += chr(int(hpsk[2*i:2*i+2], base=16))

            if not spsk.isalnum():
              self.log.log(' !!!!')
              self.log.log(' !!!! Not Printable PSK! !!!!')
              return True

            connect_attempt['net_details']['SPSK'] = '"' + spsk + '"'

            setup_net = 'set_psk'
            setup_net_exec = ('set_network', 0, 'psk', 1)

            use_psk = True
          else:
            # Disable key management
            land_des = {'land': 'end'} # TO BE IMPLEMENTED

          self.target.set_name('CONNECT', 'MASTER')
          self.target.set_gparam('connect_attempt', connect_attempt)


          self.target.add_node('disconnect', 'CONFIG_SETUP',
                               onsuccess='get_inet_st',
                               onsuccess_exec=('status', ),
                               params={'run_ip_cmds': True,
                                       'ip_cmds': ['flush', 'down', 'up']})
          self.target.add_node('get_inet_st', 'PARSE_INET_STATUS',
                               onsuccess='list_network',
                               onsuccess_exec=('list_network', ))
          self.target.add_node('list_network', 'LIST_NETWORK',
                               onsuccess=setup_net,
                               onsuccess_exec=setup_net_exec,
                               onfail='add_network',
                               onfail_exec=('add_network', ),
                               params={'sbssid': True,
                                       'bssid': bssid,
                                       'use_psk': use_psk})
          self.target.add_node('add_network', 'CONNECT_ADD_NETWORK',
                               onsuccess='set_bssid',
                               onsuccess_exec=('set_network', 0, 'bssid', 1))
          self.target.add_node('set_bssid', 'CONFIG_SETUP',
                               onsuccess='list_network',
                               onsuccess_exec=('list_network', ))
          self.target.add_node('set_psk', 'CONFIG_SETUP',
                               onsuccess='select_net',
                               onsuccess_exec=('select_network', 0),
                               params={'copy_net_num': True})
          self.target.add_node('select_net', 'CONFIG_SETUP',
                               onsuccess='reconnect',
                               onsuccess_exec=('reconnect', ))
          self.target.add_node('reconnect', 'CONFIG_SETUP',
                               onsuccess='associating',
                               params={'associating': True})
          self.target.add_node('associating', 'VALIDATING',
                               onsuccess='assoc_success',
                               onsuccess_exec=('status', ),
                               onfail='assoc_failed',
                               onfail_exec=('disconnect', ))
          self.target.add_node('assoc_success', 'PARSE_INET_STATUS',
                               onsuccess='get_addr',
                               params={'run_dhcp': True})
          self.target.add_node('get_addr', 'GETTING_DHCP_ADDR',
                               onsuccess='got_addr',
                               onsuccess_exec=('status', ),
                               onfail='assoc_failed',
                               onfail_exec=('disconnect', ))
          self.target.add_node('got_addr', 'PARSE_INET_STATUS',
                               onsuccess='end')
          self.target.add_node('assoc_failed', 'CONFIG_SETUP',
                               onsuccess='assoc_failed_status',
                               onsuccess_exec=('status', ),
                               params={'fail_connect_report': True})
          self.target.add_node('assoc_failed_status',
                               'PARSE_INET_STATUS',
                               onsuccess='end')
          self.target.add_node('end', 'IDLE')

          qans = {'action': 'ASSOCIATING'}
          self.send_to_master(qans)
          self.send_to_main(qans)

          # Terminate running dhclient if needed
          if self.dp:
            self.terminate_dhclient()

          self.run_target('CONFIG_SETUP', 'disconnect')

        else:
          self.log.log('Unknown request! Discarded.')

      return True

    # Master thread has finished.
    elif dt & 0x0f:
      self.log.log('Master Thread has finished.')
      self.is_master_attached = False

      return True

    self.log.log('An error has occurred while handling event '
                 'from master thread!')

    return True

  # Submit a command
  def submit_cmd(self, cmd, name=None):

    # Set busy Flag
    self.busy = True

    cli_command = (cmd + '\n').encode()
    self.current_op['name'] = name
    self.current_op['running'] = True
    self.current_op['cli_cmd'] = cli_command
    self.current_op['start_time'] = time.time_ns()
    self.current_op['end_time'] = None
    self.current_op['result'] = None

    # Run command
    os.write(self.ifd, cli_command)


  def fallback(self, flags=None):
    # Clean output
    self.ols = []

    self.log.log('  !!!!!! Fallback !!!!')

    self.target.flush_target()

    self.target.set_name('GET_INET_STATUS')
    self.target.add_node('parse_inet_st',
                          'PARSE_INET_STATUS',
                          onsuccess='end')
    self.target.add_node('end', 'IDLE')

    self.run_target('PARSE_INET_STATUS', 'status')


  # Handle wpa_cli stdout
  def wpa_cli_stdout_h(self, mask, key):
    # Only read request could be handled
    if not (mask & selectors.EVENT_READ):
      return False

    self.log.log('status = %s' % self.status)
    self.log.log('stdout is filled', lev=8)
    wout = os.read(self.ofd, 1024)
    self.log.log('stdout ::-> %s' % wout, lev=8)

    if wout == b'\r\x1b[K':
      self.log.log('That\'s all folks!')
      self.status = 'TERMINATED'
      return False

    try:
      self.rls += wout.decode().split('\n')
    except:
      self.log.log('Error while decoding output!')
      return True

    i = 0
    with_prompt = False

    # Filter output, check for prompt
    for l in self.rls:
      if re.match('^<3>', l) or \
         re.match('^\r<3>', l) or \
         re.match('^> \r<3>', l):
        if l != '':
          self.els.append(l)

      # Found Prompt: output is ready.
      elif l == '> ':
        i += 1
        with_prompt = True
        break

      else:
        if l != '':
          self.ols.append(l)

      i += 1

    self.rls = self.rls[i:]

    while self.els != []:
      wcli_ev = self.els.pop(0)

      # Remove starting CR
      if wcli_ev[0] == '\r':
        wcli_ev = wcli_ev[1:]

      self.log.log(' ==== Event Detected ====', lev=8)
      self.log.log('%s' % wcli_ev, lev=8)
      self.log.log(' ========================', lev=8)

      # Handle DISCONNECT Event
      if re.search('CTRL-EVENT-DISCONNECTED', wcli_ev):
        if re.search('locally_generated=1', wcli_ev):
          self.log.log(' ===> Locally generated DISCONNECT!')
        elif re.search('reason=2', wcli_ev) and self.status == 'VALIDATING':
          self.log.log(' ===> Something wrong while connecting?')
        else:
          if self.status != 'IDLE':
            self.fallback()
          else:
            self.target.set_name('GET_INET_STATUS', None)
            self.target.add_node('parse_inet_st', 'PARSE_INET_STATUS',
                                 onsuccess='end')
            self.target.add_node('end', 'IDLE')
            self.run_target('PARSE_INET_STATUS', 'status')

      # Handle CONNECT Event
      if re.search('CTRL-EVENT-CONNECTED', wcli_ev):
        if self.status == 'VALIDATING':
          self.log.log(' ---->>>>>>>>> VALIDATE SUCCESS <<<<<<<<<<-------')

          self.sel_timeout = None
          self.target.set_cnode_success(True)
          self.next_target_node()

        if self.status == 'IDLE':
          self.target.set_name('GET_INET_STATUS', None)
          self.target.add_node('parse_inet_st', 'PARSE_INET_STATUS',
                               onsuccess='end')
          self.target.add_node('end', 'IDLE')
          self.run_target('PARSE_INET_STATUS', 'status')

    if not with_prompt:
      self.log.log('   >>>>>>> Waiting for prompt...', lev=10)
      return True

    while self.ols != []:
      if self.wpa_cli_stdout_lines_loop() is False:
        return False

    return True


  def wpa_cli_stdout_lines_loop(self):

    wout = b''

    if self.status == 'INIT':
      # Check if connection with wpa_supplicant is
      #  established
      for l in self.ols:
        if l.find('\nConnection established.\n'):
          self.log.log('OK')

        self.ols = []

        self.target.set_name('GET_INET_STATUS')
        self.target.add_node('parse_inet_st',
                             'PARSE_INET_STATUS',
                             onsuccess='end')
        self.target.add_node('end', 'IDLE')

        self.run_target('PARSE_INET_STATUS', 'status')

        return True

      else:
        self.log.log('An Error occurred while connecting with'
          'wpa_supplicant!')
        self.status = 'TERMINATE'

        self.ols = []
        return False

    elif self.status == 'PARSE_INET_STATUS':
      self.ols.pop(0)

      # Try to parse output
      try:
        parsed = self.ols
        pairs={}
        for l in parsed:
          pair = l.split('=')
          pairs[pair[0]]=pair[1]
      except:
        self.log.log('Unexpected ISSUE_GET_INET_STATUS answer!')
        self.ols = []
        return True

      # Check for all keys presence
      for k in ('wpa_state', 'p2p_device_address',
                'address', 'uuid'):
        if not k in pairs:
          self.log.log('In GET_INET_STATUS answer, '
            'key %s not found!' % (k))
          self.ols = []
          self.target['def_fallback']()
          return True

      # All ok!
      self.target.set_cnode_success(True)

      # Clean output
      self.ols = []

      # Check if inet status has changed
      if not self.inetstatus == pairs:
        self.log.log(" @@@@ INET STATUS has changed @@@@ ")
        self.inetstatus = pairs
        print(self.inetstatus)

        # Tell Main and Master
        qans = {'action': 'UPDATE_INET_STATUS',
                'data': self.inetstatus}
        self.send_to_main(qans)
        if self.is_master_attached:
          self.send_to_master(qans)

      if self.target.get_cnode_params('run_dhcp'):
        # Launch dhclient
        self.sel_timeout = self.dhcp_timeout
        self.target.set_gparam('dhcp_start', time.time_ns())
        self.run_dhclient()

      # Set NEW state.
      self.next_target_node()

      return True


    ### Scan section
    elif self.status == 'SCAN':
      self.ols.pop(0)

      if self.ols.pop(0) == 'OK':

        self.target.set_cnode_success(True)
        self.next_target_node()

        self.sel_timeout = self.scan_timeout
        self.log.log(' ^^^^^^ Scanning for Networks... ^^^^^^')

        # Warn Main Thread
        qreq = {'action': 'SCANNING', 'data': self.scan_res}
        self.send_to_main(qreq)
        if self.is_master_attached:
          self.send_to_master(qreq)

        return True

      else:
        self.log.log('Unknown answer from wpa_cli!')
        self.fallback()
        return True

    elif self.status == 'SCANNING':
      return True

    elif self.status == 'GET_SCAN_RESULT':
      self.ols.pop(0)

      nets={}

      try:
        rlines = self.ols
        rlines = rlines[1:] # Cut out first line
        for l in rlines:
          fs = l.split('\t')
          nets[fs[0]] = fs[1:]
      except:
        self.log.log('Unexpected GET_SCAN_RESULT answer!')
        self.fallback()
        return True

      self.target.set_cnode_success(True)

      self.ols = []

      if len(nets) == 0:
        self.log.log('No Networks found!', lev=8)

      self.scan_res['scan_end'] = time.time_ns()
      self.scan_res['results'] = nets

      qreq = {'action': 'SCAN_RESULTS', 'data': self.scan_res}

      if self.scan_res['request_from'] == 'MASTER':
        self.send_to_master(qreq)

      self.send_to_main(qreq)

      self.next_target_node()

      return True
    ### Scan section ends

    ### Connect section
    elif self.status == 'CONFIG_SETUP':
      self.ols.pop(0)

      if self.target.get_cnode_params('associating'):
        self.target.set_gparam('assoc_start', time.time_ns())
        self.sel_timeout = self.assoc_timeout

      if self.target.get_cnode_params('fail_connect_report'):
        qans = {'action': 'CONNECTION_FAILED', 'data': 'CONN_FAILED'}
        self.send_to_main(qans)
        if self.is_master_attached:
          self.send_to_master(qans)

      if self.target.get_cnode_params('copy_net_num'):
        self.target.set_status_params(["%d" % \
          self.target.get_gparam('assoc_net_num'), ])

      ans = self.ols.pop(0)

      if self.target.get_cnode_params('run_ip_cmds'):
        ip_cmds = self.target.get_cnode_params('ip_cmds')
        for c in ip_cmds:
          if self.run_ip_cmd(c) is False:
            self.fallback()

      if ans == 'OK':
        self.target.set_cnode_success(True)
        self.next_target_node()
        return True
      else:
        self.log.log('Unexpected answer from wpa_cli!')
        self.fallback()
        return True

    elif self.status == 'LIST_NETWORK':
      self.ols.pop(0)

      nlist = {}

      try:
        rlines = self.ols
        rlines = rlines[1:] # Cut out first line
        for l in rlines:
          fs = l.split('\t')
          if fs[2]:
            nlist[fs[2]] = (fs[0], fs[1], fs[3])
      except:
        self.log.log('Unexpected LIST_NETWORK answer!')
        self.fallback()
        return True

      self.ols = []

      self.net_list = nlist

      if self.target.get_cnode_params('sbssid'):
        s_bssid = self.target.get_cnode_params('bssid')
        if s_bssid in nlist:
          self.target.set_gparam('assoc_net_num', int(nlist[s_bssid][0]))
          self.log.log(';;;;;;;;;;;; IS in list: netnum %s ' % \
            nlist[s_bssid][0], lev=9)
          if self.target.get_cnode_params('use_psk'):
            self.target.set_status_params(
              [nlist[s_bssid][0],
               self.target.get_gparam('connect_attempt')['net_details']['SPSK']])
          else:
            self.target.set_status_params([nlist[s_bssid][0], ])
          self.target.set_cnode_success(True)
        else:
          self.log.log(';;;;;;;;;;;; is NOT in list', lev=9)
          self.target.set_cnode_success(False)


      self.next_target_node()

      return True


    elif self.status == 'CONNECT_ADD_NETWORK':
      self.ols.pop(0)

      assoc_net_num = None

      try:
        assoc_net_num = int(self.ols.pop(0))
      except:
        self.log.log('Unknown answer from wpa_cli!')
        self.fallback()
        return True

      if not assoc_net_num is None:
        self.target.set_gparam('assoc_net_num', assoc_net_num)
        self.target.set_cnode_success(True)
        self.target.set_status_params(
          ['%d' % assoc_net_num,
           self.target.get_gparam('connect_attempt')['net_details']['BSSID']])
        self.next_target_node()
      else:
        self.log.log('Cannot parse wpa_cli answer!')
        self.fallback()

      return True
    ### Connect section ends

    elif self.status == 'IDLE':
      self.log.log('IDLE state, doing nothing...')
      time.sleep(3)
      return True

    self.log.log('This should not happen!')

    return False


  def run_ip_cmd(self, action=None, iface='wlan0'):
    if not action:
      return True

    expect_output = False
    expect_error = False

    if action == 'down':
      cmd_args = ['ip', 'link', 'set', 'dev', iface, 'down']
    elif action == 'up':
      cmd_args = ['ip', 'link', 'set', 'dev', iface, 'up']
    elif action == 'flush':
      cmd_args = ['ip', 'addr', 'flush', 'dev', iface]

    scmd = ''
    for i in cmd_args:
      scmd += i
      scmd += ' '

    self.log.log('Running %s' % scmd)

    self.ipp = sp.Popen(cmd_args,
                        stdout=sp.PIPE, stderr=sp.PIPE)

    self.ipod = self.ipp.stdout.fileno()
    self.iped = self.ipp.stderr.fileno()


    exs = self.ipp.wait()
    if exs == 0:
      self.log.log('ip exits successfully.')
      return True
    else:
      expect_error = True
      self.log.log('ip exits with %d' % exs)

    if expect_output:
      self.sel.register(self.ipod, selectors.EVENT_READ,
                        data=b'ip_stdout')
    else:
      os.close(self.ipod)

    if expect_error:
      if self.iped:
        os.close(self.iped)
        self.sel.unregister(self.iped)
        self.iped = None

      self.sel.register(self.iped, selectors.EVENT_READ,
                        data=b'ip_stderr')
    else:
      os.close(self.iped)

    return False


  def ip_stdout_h(self, mask, key):
    # Only read request could be handled
    if not (mask & selectors.EVENT_READ):
      return False

    while True:
      wout = os.read(self.ipod, 1024)

      if wout == b'':
        break

      lws = wout.decode().split('\n')

      for l in lws:
        self.log.log(' (IP) %s' % l, lev=8)

    os.close(self.ipod)
    self.sel.unregister(self.ipod)
    self.ipod = None

    return True


  def ip_stderr_h(self, mask, key):
    # Only read request could be handled
    if not (mask & selectors.EVENT_READ):
      return False

    while True:
      wout = os.read(self.iped, 1024)

      if wout == b'':
        break

      lws = wout.decode().split('\n')

      for l in lws:
        self.log.log(' (IP ERR) %s' % l, lev=8)

    os.close(self.iped)
    self.sel.unregister(self.iped)
    self.iped = None

    return True


  def run_dhclient(self, iface='wlan0'):
    if self.dp:
      self.terminate_dhclient()

    self.log.log('Running dhclient...')

    # Run dhclient
    self.dp = sp.Popen(['dhclient', '-d', '-v', iface],
                       stdout=sp.DEVNULL, stderr=sp.PIPE)

    # Register stderr
    self.dfd = self.dp.stderr.fileno()
    self.log.log('dhclient stderr fileno is %d' % self.dfd)

    self.sel.register(self.dfd, selectors.EVENT_READ,
                      data=b'dhclient_stdout')

    return True


  def terminate_dhclient(self):
    self.log.log('  (DHCLIENT) - Terminating...')

    if self.dp.poll() is None:
      self.dp.terminate()

    self.sel.unregister(self.dfd)
    self.dfd = None
    self.dp = None


  def dhclient_h(self, mask, key):
    # Only read request could be handled
    if not (mask & selectors.EVENT_READ):
      return False

    wout = os.read(self.dfd, 1024)

    lws = wout.decode().split('\n')

    for l in lws:
      self.log.log(' (DHCLIENT) %s' % l, lev=8)

      if re.search('^bound to ', l):
        r = l.split(' ')
        addr = r[2]
        renewal = r[6]
        self.log.log(' GOT ADDR %s, renewal %s' % (addr, renewal))

        self.sel_timeout = None

        self.target.set_cnode_success(True)
        self.next_target_node()

    return True

  # Handle non-event related requests.
  def noneventreq(self):
    self.log.log(' ............... noneventreq ..............')
    if self.status == 'SCANNING':
      delta_t = 1 + \
        int((time.time_ns() - self.scan_res['scan_started'])/1e9)
      self.log.log(' ............. Timeout = %d, delta_t = %d' % \
        (self.sel_timeout, delta_t))
      if delta_t >= self.scan_timeout:
        self.log.log('Scanning timeout has elapsed')
        self.busy = True
        self.sel_timeout = None

        self.target.set_cnode_success(True)
        self.next_target_node()

        return True
      else:
        self.sel_timeout = self.scan_timeout - delta_t
        return True

    elif self.status == 'VALIDATING':
      delta_t = 1 + int((time.time_ns() - \
        self.target.get_gparam('assoc_start')) / 1e9)
      self.log.log(' ............. Timeout = %d, delta_t = %d' % \
        (self.sel_timeout, delta_t))
      if delta_t >= self.assoc_timeout:
        self.log.log('Validating timeout has elapsed')
        self.sel_timeout = None

        self.target.set_cnode_success(False)
        self.next_target_node()

        return True

      else:
        self.sel_timeout = self.assoc_timeout - delta_t
        return True

    elif self.status == 'GETTING_DHCP_ADDR':
      delta_t = 1 + int((time.time_ns() - \
        self.target.get_gparam('dhcp_start')) / 1e9)
      self.log.log(' ............. Timeout = %d, delta_t = %d' % \
        (self.sel_timeout, delta_t))
      if delta_t >= self.dhcp_timeout:
        self.log.log('Validating timeout has elapsed')
        self.sel_timeout = None

        self.target['step_ok'] = False
        self.next_target_step()

        return True

      else:
        self.sel_timeout = self.dhcp_timeout - delta_t
        return True

    return False



  # Run wpa_cli process and redirect stdin and stdout
  def run_wpa_cli(self):
    self.log.log('Running wpa_cli.')
    
    # Run wpa_supplicant
    self.p = sp.Popen([self.prgname, '-p', self.ctrliface], 
                       stdin=sp.PIPE, stdout=sp.PIPE)
    
    # Register stdin and stdout
    self.ifd = self.p.stdin.fileno()
    self.ofd = self.p.stdout.fileno()
    
    self.log.log('ifd=%d, ofd=%d' % (self.ifd, self.ofd))
    
    self.sel.register(self.ofd, selectors.EVENT_READ, 
                      data=b'wpa_cli_stdout')
    

  # Terminate process
  def terminate_wpa_cli(self):
    self.log.log('Terminating...')
    os.write(self.ifd, b'quit\n')
                      
   
  def run(self):
     
    KEEP_RUNNING = True
     
    while KEEP_RUNNING:

      ev = self.sel.select(self.sel_timeout)

      # Check for Timeout triggered
      self.noneventreq()

      # Triggerd timeout
      if ev == []:
        continue

      for key, mask in ev:
        if key.data in self.handlers:
          if not self.handlers[key.data](mask, key.data):
            KEEP_RUNNING = False

    exs = self.p.wait()
    if exs == 0:
      self.log.log('Terminated.')
    else:
      self.log.log('%s exits with %d!' % (self.prgname, exs))


