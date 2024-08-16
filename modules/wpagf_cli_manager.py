import os
import sys
import time

import re

import subprocess as sp

import threading
import selectors

import socket


from wpagf_logger import LOGLEVEL, wlogger


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
    self.target = {'name': None,
                   'issued_by': None,
                   'path': {},
                   'def_fallback': None}
    self.current_op = {'name': None,
                       'running': None,
                       'cli_cmd': None,
                       'start_time': None,
                       'end_time': None,
                       'status': None,
                       'result': None}
    self._connet_attempt = None

    self.ifd = None
    self.ofd = None

    self.rls = []  # wpa_cli raw output lines
    self.ols = []  # wpa_cli command output lines
    self.els = []  # wpa_cli events output lines

    self.dp = None # Dhclient subprocess handler
    self.dfd = None # Dhclient pipe file descriptor

    # Setup events handlers
    self.handlers = {}
    self.add_handler(self.evfdmain_h, b'wpa_cli_evfd')
    self.add_handler(self.evfdmaster_h, b'master_evfd')
    self.add_handler(self.wpa_cli_stdout_h, b'wpa_cli_stdout')
    self.add_handler(self.dhclient_h, b'dhclient_stdout')
    
    self.log = wlogger('WPACLI')
    
    self.run_wpa_cli()
    self.run()
  

  def add_handler(self, handler, handler_name):
    self.handlers[handler_name] = handler

  def add_target(self, name, by=None, def_fallback=None):
    self.target = {'name': name,
                   'issued_by': by,
                   'def_fallback': def_fallback,
                   'curr_step': None,
                   'step_ok': False,
                   'step_ans': {},
                   'path': {}}

  def set_target(self, stepname, curr_status,
                 onsuccess=None, onfail=None,
                 params=None, fallback=None):

    if self.target['curr_step'] is None:
      self.target['curr_step'] = stepname

    step = {}
    step['status'] = curr_status
    step['onsuccess'] = {'land_step': None}
    step['onfail'] = None
    step['status'] = curr_status
    step['params'] = params

    if stepname != 'end':
      step['onsuccess']['land_step'] = onsuccess['land']

      if 'extra' in onsuccess:
        step['onsuccess']['extra'] = onsuccess['extra']

    if onfail:
      step['onfail'] = {'land_step': None}
      step['onfail']['land_step'] = onfail['land']
      if 'extra' in onfail:
        step['onfail']['extra'] = onfail['extra']

    self.target['path'][stepname] = step


  def next_target_step(self):

    step = self.target['path'][self.target['curr_step']]

    if self.target['step_ok']:
      tgt = 'onsuccess'
    else:
      tgt = 'onfail'

    # Set step_ok to default value
    self.target['step_ok'] = False

    if tgt in step:
      if 'extra' in step[tgt]:
        if 'clicmd' in step[tgt]['extra']:
          clist = step[tgt]['extra']['clicmd']
          ccmd = ''
          lclist = len(clist)
          for el in range(0, lclist):
            if type(clist[el]) == str:
              ccmd += clist[el]
            else:
              ccmd += self.target['step_ans'][clist[el]]
            if el < lclist - 1:
              ccmd += ' '

          self.log.log(' ===========', lev=7)
          self.log.log(' =========== ccmd is %s.' % ccmd, lev=7)

          self.submit_cmd(ccmd)

      curr_step = step[tgt]
      self.target['curr_step'] = curr_step['land_step']

      # Set New FSM Status
      self.status = self.target['path'][self.target['curr_step']]['status']

    else:
      # Fallback
      self.fallback()
      return

    self.log.log(' [TARGET] New step: %s, new status: %s' % \
      (self.target['curr_step'], self.status), lev=8)

    # Target reach!
    if self.target['curr_step'] == 'end':
      self.target = None
      self.busy = False
      qans = {'action': 'READY'}
      self.send_to_main(qans)
      if self.is_master_attached:
        self.send_to_master(qans)
      self.log.log(' [TARGET] Target reached!', lev=8)


  def start_target(self, curr_status, cmd):
    # Tell main and master I'm busy
    qans = {'action': 'BUSY'}
    self.send_to_main(qans)
    if self.is_master_attached:
      self.send_to_master(qans)

    self.busy = True
    self.status = curr_status
    self.submit_cmd(cmd)

  def del_target(self):
    self.target = None

    # Reget inet status
    self.add_target('GET_INET_STATUS', by=None)
    self.set_target('parse_inet_st',
                    'PARSE_INET_STATUS',
                    {'land': 'end'})
    self.set_target('end', 'IDLE')
    self.start_target('PARSE_INET_STATUS', 'status')

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
      self.status = 'TERMINATED'
      self.log.log('Got Termination Event from MAIN.')
      self.terminate_wpa_cli()
      return False

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

          self.add_target('SCAN', by='MASTER')
          self.set_target('do_scan',
                          'SCAN',
                          {'land': 'scanning'})
          self.set_target('scanning', 'SCANNING',
                          {'land': 'get_scan_res',
                           'extra': {'clicmd': ('scan_result', )}})
          self.set_target('get_scan_res', 'GET_SCAN_RESULT',
                          {'land': 'get_status',
                           'extra': {'clicmd': ('status', )}})
          self.set_target('get_status',
                          'PARSE_INET_STATUS',
                          {'land': 'end'})
          self.set_target('end', 'IDLE')

          #print(' ========= target:', self.target)

          self.start_target('SCAN', 'scan')

        # Connect Request
        elif qi['action'] == 'MAS_CONNECT':

          self._connet_attempt = {'assoc': {},
                                  'net_details': qi['data']
                                 }

          # Is network protected with psk?
          use_psk = False
          if 'PSK' in self._connet_attempt['net_details']:

            hpsk = self._connet_attempt['net_details']['PSK']
            spsk = ''
            for i in range(0,int(len(hpsk)/2)):
              spsk += chr(int(hpsk[2*i:2*i+2], base=16))

            if not spsk.isalnum():
              self.log.log(' !!!!')
              self.log.log(' !!!! Not Printable PSK! !!!!')
              self._connet_attempt = None
              return True

            self._connet_attempt['net_details']['SPSK'] = '"' + spsk + '"'

            land_des = {'land': 'set_psk',
                        'extra': {'clicmd': ('set_network', 0, 'psk', 1)}}
            use_psk = True
          else:
            # Disable key management
            land_des = {'land': 'end'} # TO BE IMPLEMENTED

          self.add_target('CONNECT', by='MASTER')

          self.set_target('disconnect',
                          'CONFIG_SETUP',
                          {'land': 'get_inet_st',
                           'extra': {'clicmd': ('status', )}})
          self.set_target('get_inet_st',
                          'PARSE_INET_STATUS',
                          {'land': 'list_network',
                           'extra': {'clicmd': ('list_network', )}})
          self.set_target('list_network', 'LIST_NETWORK',
                          land_des,
                          {'land': 'add_network',
                           'extra': {'clicmd': ('add_network', )}},
                          params={'sbssid': True, 'use_psk': use_psk})
          self.set_target('add_network',
                          'CONNECT_ADD_NETWORK',
                          {'land': 'set_bssid',
                           'extra': {'clicmd': ('set_network', 0, 'bssid', 1)}})
          self.set_target('set_bssid',
                          'CONFIG_SETUP',
                          {'land': 'list_network',
                           'extra': {'clicmd': ('list_network', )}})
          self.set_target('set_psk',
                          'CONFIG_SETUP',
                          {'land': 'select_net',
                           'extra': {'clicmd': ('select_network', 0)}},
                          params={'copy_net_num': True})
          self.set_target('select_net',
                          'CONFIG_SETUP',
                          {'land': 'reconnect',
                           'extra': {'clicmd': ('reconnect', )}})
          self.set_target('reconnect',
                          'CONFIG_SETUP',
                          {'land': 'associating'},
                          params={'associating': True})
          self.set_target('associating',
                          'VALIDATING',
                          {'land': 'assoc_success',
                           'extra': {'clicmd': ('status', )}},
                          {'land': 'assoc_failed',
                           'extra': {'clicmd': ('disconnect', )}})
          self.set_target('assoc_success',
                          'PARSE_INET_STATUS',
                          {'land': 'get_addr'},
                          params={'run_dhcp': True})
          self.set_target('get_addr',
                          'GETTING_DHCP_ADDR',
                          {'land': 'got_addr',
                           'extra': {'clicmd': ('status', )}},
                          {'land': 'assoc_failed',
                           'extra': {'clicmd': ('disconnect', )}})
          self.set_target('got_addr',
                          'PARSE_INET_STATUS',
                          {'land': 'end'})
          self.set_target('assoc_failed',
                          'CONFIG_SETUP',
                          {'land': 'assoc_failed_status',
                           'extra': {'clicmd': ('status', )}},
                          params={'fail_connect_report': True})
          self.set_target('assoc_failed_status',
                          'PARSE_INET_STATUS',
                          {'land': 'end'})
          self.set_target('end', 'IDLE')

          qans = {'action': 'ASSOCIATING'}
          self.send_to_master(qans)
          self.send_to_main(qans)

          # Terminate running dhclient if needed
          if self.dp:
            self.terminate_dhclient()

          self.start_target('CONFIG_SETUP', 'disconnect')

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

    self.del_target()


  def check_command(self, cmd):
    if cmd + '\n' == self.current_op['cli_cmd'].decode():
      return True
    return False

  # Check for prompt at the end of lines.
  def check_prompt(self, l):
    if l[-3:] == b'\n> ':
      return True
    return False

  # Handle wpa_cli stdout
  def wpa_cli_stdout_h(self, mask, key):
    # Only read request could be handled
    if not (mask & selectors.EVENT_READ):
      return False

    self.log.log('status = %s' % self.status)
    self.log.log('stdout is filled', lev=8)
    wout = os.read(self.ofd, 1024)
    self.log.log('stdout ::-> %s' % wout, lev=8)

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
            self.add_target('GET_INET_STATUS', by=None)
            self.set_target('parse_inet_st',
                            'PARSE_INET_STATUS',
                            {'land': 'end'})
            self.set_target('end', 'IDLE')
            self.start_target('PARSE_INET_STATUS', 'status')

      # Handle CONNECT Event
      if re.search('CTRL-EVENT-CONNECTED', wcli_ev):
        if self.status == 'VALIDATING':
          self.log.log(' ---->>>>>>>>> VALIDATE SUCCESS <<<<<<<<<<-------')

          self.sel_timeout = None
          self.target['step_ok'] = True
          self.next_target_step()

        if self.status == 'IDLE':
          self.add_target('GET_INET_STATUS', by=None)
          self.set_target('parse_inet_st',
                          'PARSE_INET_STATUS',
                          {'land': 'end'})
          self.set_target('end', 'IDLE')
          self.start_target('PARSE_INET_STATUS', 'status')

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

        self.add_target('GET_INET_STATUS', by=None)
        self.set_target('parse_inet_st',
                        'PARSE_INET_STATUS',
                        {'land': 'end'})
        self.set_target('end', 'IDLE')

        self.start_target('PARSE_INET_STATUS', 'status')

        return True

      else:
        self.log.log('An Error occurred while connecting with'
          'wpa_supplicant!')
        self.status = 'TERMINATE'

        self.ols = []
        return False

    elif self.status == 'PARSE_INET_STATUS':
      self.ols.pop(0)

      run_dhcp = False
      if self.target:
        curr_step = self.target['path'][self.target['curr_step']]
        if 'params' in curr_step:
          if curr_step['params']:
            if 'run_dhcp' in curr_step['params']:
              if curr_step['params']['run_dhcp'] is True:
                run_dhcp = True

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
        self.target['def_fallback']()
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
      self.target['step_ok'] = True

      # Clean output
      self.ols = []

      # Check if inet status has changed
      if not self.inetstatus == pairs:
        self.log.log(" @@@@ STATUS has changed @@@@ ")
        self.inetstatus = pairs
        print(self.inetstatus)

        # Tell Main and Master
        qans = {'action': 'UPDATE_INET_STATUS',
                'data': self.inetstatus}
        self.send_to_main(qans)
        if self.is_master_attached:
          self.send_to_master(qans)

      if run_dhcp:
        # Launch dhclient
        self.sel_timeout = self.dhcp_timeout
        self._connet_attempt['dhcp_start'] = time.time_ns()
        self.run_dhclient()

      # Set NEW state.
      self.next_target_step()

      return True


    ### Scan section
    elif self.status == 'SCAN':
      self.ols.pop(0)

      if self.ols.pop(0) == 'OK':

        self.target['step_ok'] = True
        self.next_target_step()

        self.sel_timeout = self.scan_timeout
        self.log.log(' ^^^^^^ Scanning for Networks... ^^^^^^')

        # Warn Main Thread
        qreq = {'action': 'SCANNING', 'data': self.scan_res}
        self.send_to_main(qreq)
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

      self.target['step_ok'] = True

      self.busy = False

      self.ols = []

      if len(nets) == 0:
        self.log.log('No Networks found!', lev=8)

      self.scan_res['scan_end'] = time.time_ns()
      self.scan_res['results'] = nets

      qreq = {'action': 'SCAN_RESULTS', 'data': self.scan_res}

      if self.scan_res['request_from'] == 'MASTER':
        self.send_to_master(qreq)

      self.send_to_main(qreq)

      self.next_target_step()

      return True
    ### Scan section ends

    ### Connect section
    elif self.status == 'CONFIG_SETUP':
      self.ols.pop(0)

      if self.target:
        curr_step = self.target['path'][self.target['curr_step']]
        if 'params' in curr_step:
          if curr_step['params']:
            if 'copy_net_num' in curr_step['params']:
              if curr_step['params']['copy_net_num'] is True:
                self.target['step_ans'] = \
                  [str(self._connet_attempt['assoc']['netnum']), ]
            if 'associating' in curr_step['params']:
              if curr_step['params']['associating'] is True:
                self._connet_attempt['assoc_start'] = time.time_ns()
                self.sel_timeout = self.assoc_timeout
            if 'fail_connect_report' in curr_step['params']:
              if curr_step['params']['fail_connect_report'] is True:
                qans = {'action': 'CONNECTION_FAILED', 'data': 'CONN_FAILED'}
                self.send_to_main(qans)
                if self.is_master_attached:
                  self.send_to_master(qans)

      ans = self.ols.pop(0)

      if ans == 'OK':
        self.target['step_ok'] = True
        self.next_target_step()
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

      self.target['step_ok'] = True

      curr_step = self.target['path'][self.target['curr_step']]
      search_bssid = False
      use_psk = False

      if 'params' in curr_step:
        if 'sbssid' in curr_step['params']:
          if curr_step['params']['sbssid'] is True:
            search_bssid = True
        if 'use_psk' in curr_step['params']:
          if curr_step['params']['use_psk'] is True:
            use_psk = True

      if search_bssid:
        s_bssid = self._connet_attempt['net_details']['BSSID']
        if s_bssid in self.net_list:
          self.target['step_ans'] = [self.net_list[s_bssid][0], ]
          if use_psk:
            self.target['step_ans'].append(
              self._connet_attempt['net_details']['SPSK'])
          self._connet_attempt['assoc']['netnum'] = \
            int(self.net_list[s_bssid][0])
          self.target['step_ok'] = True
          self.log.log(';;;;;;;;;;;; IS in list: netnum %d ' % \
            self._connet_attempt['assoc']['netnum'], lev=9)
        else:
          self.target['step_ok'] = False
          self.log.log(';;;;;;;;;;;; is NOT in list', lev=9)


      self.next_target_step()
      self.busy = False

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
        self._connet_attempt['assoc']['netnum'] = assoc_net_num
        self.target['step_ok'] = True
        self.target['step_ans'] = \
          ['%d' % assoc_net_num,
           self._connet_attempt['net_details']['BSSID']]
        self.next_target_step()
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
        self.target['step_ok'] = True
        self.next_target_step()


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

        self.target['step_ok'] = True
        self.next_target_step()

        return True
      else:
        self.sel_timeout = self.scan_timeout - delta_t
        return True

    elif self.status == 'VALIDATING':
      delta_t = 1 + int((time.time_ns() - \
        self._connet_attempt['assoc_start'])/1e9)
      self.log.log(' ............. Timeout = %d, delta_t = %d' % \
        (self.sel_timeout, delta_t))
      if delta_t >= self.assoc_timeout:
        self.log.log('Validating timeout has elapsed')
        self.sel_timeout = None

        self.target['step_ok'] = False
        self.next_target_step()

        return True

      else:
        self.sel_timeout = self.assoc_timeout - delta_t
        return True

    elif self.status == 'GETTING_DHCP_ADDR':
      delta_t = 1 + int((time.time_ns() - \
        self._connet_attempt['dhcp_start'])/1e9)
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
    
    exs = self.p.wait()
    if exs == 0:
      self.log.log('Terminated.')
    else:
      self.log.log('%s exits with %d!' % (self.prgname, exs))

    self.status = 'TERMINATED'
    #self.p.terminate()
                      
   
  def run(self):
     
    KEEP_RUNNING = True
     
    while KEEP_RUNNING:

      if self.status == 'TERMINATE':
        KEEP_RUNNING = False
        break

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

    if self.status != 'TERMINATED':
      self.terminate_wpa_cli()

