# Copyright (C) 2009-2011 AG Projects. See LICENSE for details.
#

from zope.interface import implements, Interface

from application.python.util import Singleton

import cjson
import datetime
import os
import re
import sys
import urllib
import urllib2

from socket import gethostbyname

from application.notification import NotificationCenter, IObserver
from application.python.util import Null
from application.system import host, unlink
from collections import defaultdict
from eventlet import api
from gnutls.crypto import X509Certificate, X509PrivateKey
from gnutls.errors import GNUTLSError

from sipsimple.application import SIPApplication
from sipsimple.account import AccountManager, BonjourAccount, Account
from sipsimple.audio import WavePlayer
from sipsimple.configuration.datatypes import STUNServerAddress
from sipsimple.configuration.settings import SIPSimpleSettings
from sipsimple.configuration.backend.file import FileBackend
from sipsimple.core import SIPURI, PJSIPError, SIPCoreError
from sipsimple.lookup import DNSLookup
from sipsimple.session import SessionManager
from sipsimple.streams import AudioStream, ChatStream, FileTransferStream, DesktopSharingStream
from sipsimple.threading import run_in_twisted_thread
from sipsimple.threading.green import run_in_green_thread
from sipsimple.util import TimestampedNotificationData

from SessionRinger import Ringer
from FileTransferSession import OutgoingFileTransfer
from BlinkLogger import BlinkLogger, FileLogger
from SessionHistory import SessionHistory

from configuration.account import AccountExtension, BonjourAccountExtension
from configuration.datatypes import ResourcePath
from configuration.settings import SIPSimpleSettingsExtension
from util import *



STATUS_PHONE = "phone"

PresenceStatusList =  [(1, "Available", None), 
                       (1, "Working", None),
                       (0, "Appointment", None),
                       (0, "Busy", None),
                       (0, "Breakfast", None),
                       (0, "Lunch", None),
                       (0, "Dinner", None),
                       (0, "Travel", None),
                       (0, "Driving", None),
                       (0, "Playing", None),
                       (0, "Spectator", None),
                       (0, "TV", None),
                       (-1, "Away", None),
                       (-1, "Invisible", None),
                       (-1, "Meeting", None),
                       (-1, "On the phone", STATUS_PHONE),
                       (-1, "Presentation", None),
                       (-1, "Performance", None),
                       (-1, "Sleeping", None),
                       (-1, "Vacation", None),
                       (-1, "Holiday", None)]


class BackendError(Exception):
    pass


class NotificationPrinter(object):
    implements(IObserver)

    def handle_notification(self, notification):
        print "got notification", notification


class ISIPManagerDelegate(Interface):
    def sip_account_activated(self, account):
        pass

    def sip_account_registration_succeeded(self, account):
        pass

    def sip_account_registration_ended(self, account):
        pass

    def sip_account_registration_failed(self, account):
        pass

    def sip_account_list_refresh(self):
        pass

    def handle_incoming_session(self, session, streams):
        pass

    def sip_session_missed(self, session):
        pass

    def sip_error(self, message):
        pass

    def sip_nat_detected(self, nat_type):
        pass


class IPAddressMonitor(object):
    def __init__(self):
        self.greenlet = None

    @run_in_green_thread
    def start(self):
        notification_center = NotificationCenter()

        if self.greenlet is not None:
            return
        self.greenlet = api.getcurrent()

        current_address = host.default_ip
        while True:
            new_address = host.default_ip
            # make sure the address stabilized
            api.sleep(5)
            if new_address != host.default_ip:
                continue
            if new_address != current_address:
                notification_center.post_notification(name='SystemIPAddressDidChange', sender=self, data=TimestampedNotificationData(old_ip_address=current_address, new_ip_address=new_address))
                current_address = new_address
            api.sleep(5)

    @run_in_twisted_thread
    def stop(self):
        if self.greenlet is not None:
            api.kill(self.greenlet, api.GreenletExit())
            self.greenlet = None


def parse_history_line(line):
    toks = line.split("\t", 2)
    if len(toks) != 3:
        return None
    try:
        unescaped = eval(toks[2], {"__builtins__":None}, {})
        try:
            sender = toks[1][1:-1]
        except:
            sender = ""
        return (parse_datetime(toks[0]), sender, unescaped)
    except:
        pass
    return None


def format_date(dt):
    if not dt:
        return "unknown"
    now = datetime.datetime.now()
    delta = now - dt
    if (dt.year,dt.month,dt.day) == (now.year,now.month,now.day):
        return dt.strftime("%H:%M")
    elif delta.days <= 1:
        return "Yesterday (%s)" % dt.strftime("%H:%M")
    elif delta.days < 7:
        return dt.strftime("%A")
    elif delta.days < 300:
        return dt.strftime("%B %d")
    else:
        return dt.strftime("%Y-%m-%d")

_pstn_addressbook_chars = "(\(\s?0\s?\)|[-() ])"
_pstn_addressbook_chars_substract_regexp = re.compile(_pstn_addressbook_chars)
_pstn_match_regexp = re.compile("^\+?([0-9]|%s)+$" % _pstn_addressbook_chars)
_pstn_plus_regexp = re.compile("^\+")

def format_uri(uri, default_domain, idd_prefix = None, prefix = None):
    if default_domain is not None:
        if "@" not in uri:
            if _pstn_match_regexp.match(uri):
                username = strip_addressbook_special_characters(uri)
                if idd_prefix:
                    username = _pstn_plus_regexp.sub(idd_prefix, username)
                if prefix:
                    username = prefix + username
            else:
                username = uri
            uri = "%s@%s" % (username, default_domain)
        elif "." not in uri.split("@", 1)[1]:
            uri += "." + default_domain
    if not uri.startswith("sip:") and not uri.startswith("sips:"):
        uri = "sip:%s" % uri
    return uri


def strip_addressbook_special_characters(contact):  
    return _pstn_addressbook_chars_substract_regexp.sub("", contact)


class SIPManager(object):
    __metaclass__ = Singleton

    implements(IObserver)

    def __init__(self):

        self._app = SIPApplication()
        self._delegate = None
        self._selected_account = None
        self._active_transfers = []
        self._version = None
        self.ip_address_monitor = IPAddressMonitor()
        self.ringer = Ringer(self)
        
        self.notification_center = NotificationCenter()
        self.notification_center.add_observer(self, sender=self._app)
        self.notification_center.add_observer(self, sender=self._app.engine)
        self.notification_center.add_observer(self, name='AudioStreamGotDTMF')
        self.notification_center.add_observer(self, name='BlinkSessionDidEnd')
        self.notification_center.add_observer(self, name='BlinkSessionDidFail')
        self.notification_center.add_observer(self, name='CFGSettingsObjectDidChange')
        self.notification_center.add_observer(self, name='SIPAccountDidActivate')
        self.notification_center.add_observer(self, name='SIPAccountDidDeactivate')
        self.notification_center.add_observer(self, name='SIPAccountRegistrationDidSucceed')
        self.notification_center.add_observer(self, name='SIPAccountRegistrationDidEnd')
        self.notification_center.add_observer(self, name='SIPAccountRegistrationDidFail')
        self.notification_center.add_observer(self, name='SIPAccountMWIDidGetSummary')
        self.notification_center.add_observer(self, name='SIPSessionNewIncoming')
        self.notification_center.add_observer(self, name='SIPSessionNewOutgoing')
        self.notification_center.add_observer(self, name='SIPSessionDidFail')

    def set_delegate(self, delegate):
        # doesnt work for PyCocoa objects 
        # ISIPManagerDelegate.implementedBy(delegate)
        self._delegate= delegate

    def init(self, platform_options, version):
        self._version = version

        if not os.path.exists(platform_options["config_file"]):
            first_start = True
        else:
            first_start = False

        config_be = FileBackend(platform_options["config_file"])

        Account.register_extension(AccountExtension)
        BonjourAccount.register_extension(BonjourAccountExtension)
        SIPSimpleSettings.register_extension(SIPSimpleSettingsExtension)
        try:
            self._app.start(config_backend=config_be)
        except PJSIPError, exc:
            if str(exc).find("(PJSIP_TLS_ECACERT)") >= 0:
                BlinkLogger().log_error("Invalid TLS settings detected. Resetting and restarting...")
                SIPSimpleSettings().tls.certificate = None
                SIPSimpleSettings().save()
                self._app.start(config_backend=config_be)
            else:
              raise exc

        self.log_directory = platform_options["log_directory"]
        self.init_configurations(first_start)

        settings = SIPSimpleSettings()

        settings.resources_directory = platform_options["resources_directory"]
        settings.save()

        # 
        SessionHistory().init()

        # start session mgr
        sm = SessionManager()

    def init_configurations(self, first_time=False):
        account_manager = AccountManager()
        settings = SIPSimpleSettings()

        self.notification_center.add_observer(self, sender=settings)

        # fixup default account
        self._selected_account = account_manager.default_account
        if self._selected_account is None:
            self._selected_account = account_manager.get_accounts()[0]

        download_folder = settings.file_transfer.directory.normalized
        if not os.path.exists(download_folder):
            os.mkdir(download_folder, 0700)

        #if options.no_relay:
        #    account.msrp.use_relay_for_inbound = False
        #    account.msrp.use_relay_for_outbound = False
        #if options.msrp_tcp:
        #    settings.msrp.transport = 'tcp'

    def save_certificates(self, response):
        passport = response["passport"]
        address = response["sip_address"]

        crt = passport["crt"].strip() + os.linesep
        key = passport["key"].strip() + os.linesep
        ca = passport["ca"].strip() + os.linesep

        try:
            X509Certificate(crt)
            X509Certificate(ca)
            X509PrivateKey(key)
        except GNUTLSError, e:
            BlinkLogger().log_error("Invalid certificate data: %s" % e)
            return None

        home_directory = os.path.expanduser('~/')
        tls_folder = os.path.join(SIPSimpleSettings().user_data_directory, "tls")
        if not os.path.exists(tls_folder):
            os.mkdir(tls_folder, 0700)

        crt_path = os.path.join(tls_folder, address + ".crt")
        f = open(crt_path, "w")
        os.chmod(crt_path, 0600)
        f.write(crt)
        f.write(key)
        f.close()
        BlinkLogger().log_info("Saved new TLS Certificate and Private Key to %s" % crt_path)
        if crt_path.startswith(home_directory):
            crt_path = crt_path.replace(home_directory, '~/')

        ca_path = os.path.join(tls_folder, 'ca.crt')

        try:
            existing_cas = open(ca_path, "r").read().strip() + os.linesep
        except:
            existing_cas = None
            ca_list = ca
        else:
            ca_list = existing_cas if ca in existing_cas else existing_cas + ca

        if ca_list != existing_cas:
            f = open(ca_path, "w")
            os.chmod(ca_path, 0600)
            f.write(ca_list)
            f.close()
            BlinkLogger().log_info("Added new CA to %s" % ca_path)
            if ca_path.startswith(home_directory):
                ca_path = ca_path.replace(home_directory, '~/')
            SIPSimpleSettings().tls.ca_list = ca_path
            SIPSimpleSettings().save()
        else:
            BlinkLogger().log_info("CA already present in %s" % ca_path)

        return crt_path

    def fetch_account(self):
        """Fetch the SIP account from ~/.blink_account and create/update it as needed"""
        filename = os.path.expanduser('~/.blink_account')
        if not os.path.exists(filename):
            return
        try:
            data = open(filename).read()
            data = cjson.decode(data.replace('\\/', '/'))
        except (OSError, IOError), e:
            BlinkLogger().log_error("Failed to read json data from ~/.blink_account: %s" % e)
            return
        except cjson.DecodeError, e:
            BlinkLogger().log_error("Failed to decode json data from ~/.blink_account: %s" % e)
            return
        finally:
            unlink(filename)
        data = defaultdict(lambda: None, data)
        account_id = data['sip_address']
        if account_id is None:
            return
        account_manager = AccountManager()
        try:
            account = account_manager.get_account(account_id)
        except KeyError:
            account = Account(account_id)
            account.display_name = data['display_name']
            default_account = account
        else:
            default_account = account_manager.default_account
        account.auth.username = data['auth_username']
        account.auth.password = data['password'] or ''
        account.sip.outbound_proxy = data['outbound_proxy']
        account.xcap.xcap_root = data['xcap_root']
        account.nat_traversal.msrp_relay = data['msrp_relay']
        account.server.conference_server = data['conference_server']
        account.server.settings_url = data['settings_url']
        account.service_provider.name = data['service_provider_name']
        account.service_provider.help_url = data['service_provider_help_url']
        account.service_provider.about_url = data['service_provider_about_url']
        if data['passport'] is not None:
            cert_path = self.save_certificates(data)
            account.tls.certificate = cert_path
        account.enabled = True
        account.save()
        account_manager.default_account = default_account

    def enroll(self, display_name, username, password, email):
        url = SIPSimpleSettings().server.enrollment_url

        tzname = ""
        if sys.platform == "darwin":
            # this is for macosx
            # the try..except is only for safety, /etc/localtime should always be a link on OSX. -Luci
            try:
                tzname = '/'.join(os.readlink('/etc/localtime').split('/')[-2:])
            except:
                pass

        if not tzname:
            BlinkLogger().log_warning("Unable to determine timezone")

        values = {'password' : password.encode("utf8"),
                  'username' : username.encode("utf8"),
                  'email' : email.encode("utf8"),
                  'display_name' : display_name.encode("utf8"),
                  'tzinfo' : tzname }

        BlinkLogger().log_info("Requesting creation of a new SIP account at %s"%url)

        data = urllib.urlencode(values)
        req = urllib2.Request(url, data)
        raw_response = urllib2.urlopen(req)
        json_data = raw_response.read()

        response = cjson.decode(json_data.replace('\\/', '/'))
        if response:
            if not response["success"]:
                BlinkLogger().log_info("Enrollment Server failed to create SIP account: %(error_message)s" % response)
                raise Exception(response["error_message"])
            else:
                BlinkLogger().log_info("Enrollment Server successfully created SIP account %(sip_address)s" % response)
                data = defaultdict(lambda: None, response)
                certificate_path = None if data['passport'] is None else self.save_certificates(data)
                return data['sip_address'], certificate_path, data['outbound_proxy'], data['xcap_root'], data['msrp_relay'], data['settings_url']
        else:
            BlinkLogger().log_info("Enrollment Server returned no response")

        raise Exception("No response received from %s"%url)

    def has_accounts(self):
        am = AccountManager()
        return any(a for a in am.get_accounts() if not isinstance(a, BonjourAccount))

    def lookup_sip_proxies(self, account, target_uri, session_controller):
        assert isinstance(target_uri, SIPURI)

        lookup = DNSLookup()
        lookup.type = 'sip_proxies'
        lookup.owner = session_controller
        self.notification_center.add_observer(self, sender=lookup)
        settings = SIPSimpleSettings()

        if isinstance(account, Account) and account.sip.outbound_proxy is not None:
            uri = SIPURI(host=account.sip.outbound_proxy.host, port=account.sip.outbound_proxy.port, 
                parameters={'transport': account.sip.outbound_proxy.transport})
            BlinkLogger().log_info("Initiating DNS Lookup for SIP routes of %s (through proxy %s)"%(target_uri, uri))
        elif isinstance(account, Account) and account.sip.always_use_my_proxy:
            uri = SIPURI(host=account.id.domain)
            BlinkLogger().log_info("Initiating DNS Lookup for SIP routes of %s (through account %s proxy)"%(target_uri, account.id))
        else:
            uri = target_uri
            BlinkLogger().log_info("Initiating DNS Lookup for SIP routes of %s"%target_uri)
        lookup.lookup_sip_proxy(uri, settings.sip.transport_list)

    def lookup_stun_servers(self, account):
        lookup = DNSLookup()
        lookup.type = 'stun_servers'
        lookup.owner = account
        self.notification_center.add_observer(self, sender=lookup)
        settings = SIPSimpleSettings()
        if not isinstance(account, BonjourAccount):
            # lookup STUN servers, as we don't support doing this asynchronously yet
            if account.nat_traversal.stun_server_list:
                account.nat_traversal.stun_server_list = [STUNServerAddress(gethostbyname(address.host), address.port) for address in account.nat_traversal.stun_server_list]
                address = account.nat_traversal.stun_server_list[0]
            else:
                lookup.lookup_service(SIPURI(host=account.id.domain), "stun")
                BlinkLogger().log_info("Initiating DNS Lookup for STUN servers of domain %s"%account.id.domain)

    def parse_sip_uri(self, target_uri, account):
        try:
            target_uri = str(target_uri)
        except:
            self._delegate.sip_error("SIP address must not contain unicode characters (%s)" % target_uri)
            return None

        if '@' not in target_uri and isinstance(account, BonjourAccount):
            self._delegate.sip_error("SIP address must contain host in bonjour mode (%s)" % target_uri)
            return None

        target_uri = format_uri(target_uri, account.id.domain if not isinstance(account, BonjourAccount) else None, account.pstn.idd_prefix if not isinstance(account, BonjourAccount) else None, account.pstn.prefix if not isinstance(account, BonjourAccount) else None)

        try:
            target_uri = SIPURI.parse(target_uri)
        except SIPCoreError:
            self._delegate.sip_error('Illegal SIP URI: %s' % target_uri)
            return None
        return target_uri

    def send_files_to_contact(self, account, contact_uri, files_and_types):
        target_uri = self.parse_sip_uri(contact_uri, self.get_default_account())

        for file, ftype in files_and_types:
            try:
                if type(file) == unicode:
                    file = file.encode("utf8")
                xfer = OutgoingFileTransfer(account, target_uri, file, ftype)
                self._active_transfers.append(xfer)
                xfer.start()
            except Exception, exc:
                import traceback
                traceback.print_exc()
                BlinkLogger().log_error("Error while attempting to transfer file %s: %s"%(file, exc))

    def get_chat_history_directory(self):
        dirname = unicode(SIPSimpleSettings().chat.directory).strip()
        if dirname == "":
            return os.path.join(self.log_directory, "history")
        elif os.path.isabs(dirname):
            return dirname
        else:
            return os.path.join(self.log_directory, dirname)

    def get_call_history_directory(self):
        dirname = unicode(SIPSimpleSettings().audio.directory).strip()
        if dirname == "":
            return os.path.join(self.log_directory, "history")
        elif os.path.isabs(dirname):
            return dirname
        else:
            return os.path.join(self.log_directory, dirname)

    
    def _open_call_history_file(self):
        dirname = self.get_call_history_directory()
        makedirs(dirname, 0700)
        fname = os.path.join(self.get_call_history_directory(), 'calls.txt')
        return open(fname, "a+")


    @allocate_autorelease_pool
    @run_in_gui_thread
    def log_incoming_session_missed(self, session, data):
        account = session.account
        if account is BonjourAccount():
            return
        f = self._open_call_history_file()
        if f:
            streams = ",".join(s.type for s in session.streams or session.proposed_streams or [])
            line = "missed\t%s\t%s\t%s\t%s" % (streams, account.id, format_identity(session.remote_identity, check_contact=True), data.timestamp)
            f.write(line.encode(sys.getfilesystemencoding())+"\n")
            f.close()

    @allocate_autorelease_pool
    @run_in_gui_thread
    def log_incoming_session_ended(self, session, data):
        account = session.account
        if account is BonjourAccount():
            return
        f = self._open_call_history_file()
        if f:
            streams = ",".join(data.streams)
            line = "in\t%s\t%s\t%s\t%s\t%s" % (streams, account.id, format_identity(session.remote_identity, check_contact=True), session.start_time, session.end_time)
            f.write(line.encode(sys.getfilesystemencoding())+"\n")
            f.close()

    @allocate_autorelease_pool
    @run_in_gui_thread
    def log_incoming_session_answered_elsewhere(self, session, data):
        account = session.account
        if account is BonjourAccount():
            return
        f = self._open_call_history_file()
        if f:
            streams = ",".join(s.type for s in session.streams or session.proposed_streams or [])
            line = "in\t%s\t%s\t%s\t%s" % (streams, account.id, format_identity(session.remote_identity, check_contact=True), data.timestamp)
            f.write(line.encode(sys.getfilesystemencoding())+"\n")
            f.close()

    @allocate_autorelease_pool
    @run_in_gui_thread
    def log_outgoing_session_failed(self, session, data):
        account = session.account
        if account is BonjourAccount():
            return
        f = self._open_call_history_file()
        if f:
            streams = ",".join(data.streams)
            participants = ",".join(data.participants)
            focus = 1 if data.focus else 0
            line = "failed\t%s\t%s\t%s\t%s\t%s\t%s\t%s"%(streams, account.id, data.target_uri, data.timestamp, data.timestamp, focus, participants)
            f.write(line.encode(sys.getfilesystemencoding())+"\n")
            f.close()

    @allocate_autorelease_pool
    @run_in_gui_thread
    def log_outgoing_session_cancelled(self, session, data):
        account = session.account
        if account is BonjourAccount():
            return
        f = self._open_call_history_file()
        if f:
            streams = ",".join(data.streams)
            participants = ",".join(data.participants)
            focus = 1 if data.focus else 0
            line = "cancelled\t%s\t%s\t%s\t%s\t%s\t%s\t%s"%(streams, account.id, data.target_uri, data.timestamp, data.timestamp, focus, participants)
            f.write(line.encode(sys.getfilesystemencoding())+"\n")
            f.close()

    @allocate_autorelease_pool
    @run_in_gui_thread
    def log_outgoing_session_ended(self, session, data):
        account = session.account
        if account is BonjourAccount():
            return
        f = self._open_call_history_file()
        if f:
            streams = ",".join(data.streams)
            participants = ",".join(data.participants)
            focus = 1 if data.focus else 0
            line = "out\t%s\t%s\t%s\t%s\t%s\t%s\t%s"%(streams, account.id, data.target_uri, session.start_time, session.end_time, focus, participants)
            f.write(line.encode(sys.getfilesystemencoding())+"\n")
            f.close()

    def get_audio_recordings_directory(self):
        return self.get_chat_history_directory()

    def get_audio_recordings(self):
        result = []
        historydir = self.get_audio_recordings_directory()

        for acct in os.listdir(historydir):
            dirname = historydir + "/" + acct
            if not os.path.isdir(dirname):
                continue

            files = [dirname+"/"+f for f in os.listdir(dirname) if f.endswith(".wav")]

            for file in files:
                try:
                    toks = file.split("/")[-1].split("-", 2)
                    if len(toks) == 3:
                        date, time, rest = toks
                        timestamp = date[:4]+"/"+date[4:6]+"/"+date[6:8]+" "+time[:2]+":"+time[2:4]

                        pos = rest.rfind("-")
                        if pos >= 0:
                            remote = rest[:pos]
                        else:
                            remote = rest
                        try:
                            identity = SIPURI.parse('sip:'+str(remote))
                            remote_party = format_identity(identity, check_contact=True)
                        except SIPCoreError:
                            remote_party = "%s" % (remote)

                    else:
                        try:
                            identity = SIPURI.parse('sip:'+str(file[:-4]))
                            remote_party = format_identity(identity, check_contact=True)
                        except SIPCoreError:
                            remote_party = file[:-4]
                        timestamp = datetime.datetime.fromtimestamp(int(stat.st_ctime)).strftime("%E %T")

                    stat = os.stat(file)
                    result.append((timestamp, remote_party, file))
                except:
                    import traceback
                    traceback.print_exc()
                    pass

        result.sort(lambda a,b: cmp(a[0],b[0]))
        return result

    def get_last_outgoing_call_info(self):
        path = "%s/calls.txt" % (self.get_call_history_directory())

        if not os.path.exists(path):
            return None

        f = open(path, "r")
        last_call = None
        for line in f:
            if line.startswith("out") or line.startswith("failed") or line.startswith("cancelled"):
                last_call = line
        f.close()

        if last_call:
            toks = last_call.split("\t")
            streams = toks[1].split(",")
            account_name = toks[2]
            address, display_name, full_uri, fancy_uri = format_identity_from_text(toks[3])

            try:
                account = AccountManager().get_account(account_name)
            except:
                account = None

            return (account, address, streams)

        return None

    def get_last_call_history_entries(self, count, isfocus=False):
        path = "%s/calls.txt"%(self.get_call_history_directory())

        if not os.path.exists(path):
            return None

        in_lines = []
        out_lines = []
        missed_lines = []

        f = open(path, "r")
        for line in f:
            if line.startswith("in"):
                in_lines.append(line.rstrip("\n"))
            elif line.startswith("out") or line.startswith("failed") or line.startswith("cancelled"):
                out_lines.append(line.rstrip("\n"))
            elif line.startswith("missed"):
                missed_lines.append(line.rstrip("\n"))
        f.close()

        in_lines = in_lines[-count:]
        out_lines = out_lines[-count:]
        missed_lines = missed_lines[-count:]

        active_account = self.get_default_account()

        in_entries = []
        out_entries = []
        missed_entries = []

        for line in in_lines:
            toks = line.split("\t")
            i = len(toks) 
            if i < 8:
               while i < 8:
                   toks.append("")
                   i = i + 1
            t, streams,account,party,start,end,focus,participants = toks
            if isfocus and focus != '1':
                continue
            address, display_name, full_uri, fancy_uri = format_identity_from_text(party)
            item = {
            "streams":streams.split(","),
            "account":account,
            "party": fancy_uri,
            "address":address,
            "start":parse_datetime(start),
            "end":parse_datetime(end),
            "when":format_date(parse_datetime(start)),
            "focus":focus,
            "participants":participants.split(",") if participants else []
            }
            if item["start"] and item["end"]:
                item["duration"] = item["end"] - item["start"]
            else:
                item["duration"] = None
            in_entries.append(item)

        for line in out_lines:
            toks = line.split("\t")
            i = len(toks) 
            if i < 8:
               while i < 8:
                   toks.append("")
                   i = i + 1
            t, streams,account,party,start,end,focus,participants = toks
            if isfocus and focus != '1':
                continue
            address, display_name, full_uri, fancy_uri = format_identity_from_text(party)

            item = {"streams": streams.split(","),
                    "account": account,
                    "party":   fancy_uri,
                    "address": address,
                    "start":   parse_datetime(start),
                    "end":     parse_datetime(end),
                    "when":    format_date(parse_datetime(start)),
                    "focus":   focus,
                    "participants":participants.split(",") if participants else [], 
                    "result":  t}

            if item["start"] and item["end"]:
                item["duration"] = item["end"] - item["start"]
            else:
                item["duration"] = None

            out_entries.append(item)

        for line in missed_lines:
            toks = line.split("\t")
            t, streams,account,party,start = toks
            address, display_name, full_uri, fancy_uri = format_identity_from_text(party)
            item = {"streams":  streams.split(","),
                    "account":  account,
                    "party":    fancy_uri,
                    "address":  address,
                    "start":    parse_datetime(start),
                    "when":     format_date(parse_datetime(start)),
                    "duration": None}

            missed_entries.append(item)

        in_entries.reverse()
        out_entries.reverse()
        missed_entries.reverse()
        return in_entries, out_entries, missed_entries

    def clear_call_history(self):
        path = "%s/calls.txt"%(self.get_call_history_directory())
        os.remove(path)

    def add_contact_to_call_session(self, session, contact):
        pass

    def format_session_details(self, sess):
        try:
            details = u"Codec: '%s' at %dHz\n" % (sess.audio_codec, sess.audio_sample_rate)
            details += u"Audio RTP endpoints %s:%d <-> %s:%d\n" % (sess.audio_local_rtp_address, sess.audio_local_rtp_port, sess.audio_remote_rtp_address_sdp, sess.audio_remote_rtp_port_sdp)
        except:
            details = ""
        return details

    def format_incoming_session_update_message(self, session, streams):
        party = format_identity(session.remote_identity)

        default_action = u"Accept"

        if len(streams) != 1:
            type_names = [s.type.replace('-', ' ').capitalize() for s in streams]
            if "Desktop sharing" in type_names:
                ds = [s for s in streams if s.type == "desktop-sharing"]
                if ds:
                    type_names.remove("Desktop sharing")
                    if ds[0].handler.type == "active":
                        type_names.append("Remote Desktop offered by")
                    else:
                        type_names.append("Access to my Desktop requested by")
                message = u"Addition of %s" % " and ".join(type_names)
            else:
                message = u"Addition of %s to Session requested by" % " and ".join(type_names)

            alt_action = u"Chat Only"
        elif type(streams[0]) is AudioStream:
            message = u"Addition of Audio to existing session requested by"
            alt_action = None
        elif type(streams[0]) is ChatStream:
            message = u"Addition of Chat to existing session requested by"
            alt_action = None
        elif type(streams[0]) is FileTransferStream:
            message = u"Transfer of File '%s' (%s) offered by" % (streams[0].file_selector.name, format_size(streams[0].file_selector.size, 1024))
            alt_action = None
        elif type(streams[0]) is DesktopSharingStream:
            if streams[0].handler.type == "active":
                message = u"Remote Desktop offered by"
            else:
                message = u"Access to my Desktop requested by"
            alt_action = None
        else:
            message = u"Addition of unknown Stream to existing Session requested by"
            alt_action = None
            print "Unknown Session contents"
        return (message, party), default_action, alt_action

    def format_incoming_session_message(self, session, streams):
        party = format_identity(session.remote_identity)

        default_action = u"Accept"
        alt_action = None

        if len(streams) != 1:                    
            type_names = [s.type.replace('-', ' ').capitalize() for s in streams]
            if "Chat" in type_names:
                alt_action = u"Chat Only"
            elif "Audio" in type_names and len(type_names) > 1:
                alt_action = u"Audio Only"
            if "Desktop sharing" in type_names:
                ds = [s for s in streams if s.type == "desktop-sharing"]
                if ds:
                    type_names.remove("Desktop sharing")
                    if ds[0].handler.type == "active":
                        type_names.append("Remote Desktop offered by")
                    else:
                        type_names.append("Access to my Desktop requested by")
                message = u"%s" % " and ".join(type_names)
            else:
                message = u"%s session requested by" % " and ".join(type_names)
        elif type(streams[0]) is AudioStream:
            message = u"Audio Session requested by"
        elif type(streams[0]) is ChatStream:
            message = u"Chat Session requested by"
        elif type(streams[0]) is DesktopSharingStream:
            if streams[0].handler.type == "active":
                message = u"Remote Desktop offered by"
            else:
                message = u"Access to my Desktop requested by"
        elif type(streams[0]) is FileTransferStream:
            message = u"Transfer of File '%s' (%s) offered by" % (streams[0].file_selector.name.decode("utf8"), format_size(streams[0].file_selector.size, 1024))
        else:
            message = u"Incoming Session request from"
            BlinkLogger().log_warning("Unknown Session content %s"%streams)
        return (message, party), default_action, alt_action

    def reject_incoming_session(self, session, code=603, reason=None):
        BlinkLogger().log_info(u"Rejecting Session from %s (code %s)"%(session.remote_identity, code))
        session.reject(code, reason)

    def is_muted(self):
        return self._app.voice_audio_mixer and self._app.voice_audio_mixer.muted

    def mute(self, flag):
        self._app.voice_audio_mixer.muted = flag

    def is_silent(self):
        return SIPSimpleSettings().audio.silent

    def silent(self, flag):
        SIPSimpleSettings().audio.silent = flag
        SIPSimpleSettings().save()

    def get_default_account(self):
        return AccountManager().default_account

    def is_account_registration_failed(self, account):
        return account in self._failed_accounts

    def set_default_account(self, account):
        if account != AccountManager().default_account:
            AccountManager().default_account = account
        self.ringer.update_ringtones()
    
    def account_for_contact(self, contact):
        return AccountManager().find_account(contact)

    def post_in_main(self, name, sender, data=None):
        call_in_gui_thread(self.notification_center.post_notification, name, sender, data)

    @allocate_autorelease_pool
    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification.sender, notification.data)

    def _NH_SIPApplicationFailedToStartTLS(self, sender, data):
        BlinkLogger().log_info('Failed to start TLS transport: %s' % data.error)

    def _NH_SIPApplicationWillStart(self, sender, data):
        settings = SIPSimpleSettings()
        settings.user_agent = "Blink %s (MacOSX)" % self._version
        settings.save()
        # Although this setting is set at enrollment time, people who have downloaded previous versions will not have it
        account_manager = AccountManager()
        for account in account_manager.iter_accounts():
            if account.id.domain == "sip2sip.info" and account.server.settings_url is None:
                account.server.settings_url = "https://blink.sipthor.net/settings.phtml"
                account.save()
        logger = FileLogger()
        logger.start()

    def _NH_SIPApplicationDidStart(self, sender, data):
        self.ip_address_monitor.start()
        self.ringer.start()
        self.ringer.update_ringtones()

        bonjour_account = BonjourAccount()
        if bonjour_account.enabled:
            settings = SIPSimpleSettings()
            for transport in settings.sip.transport_list:
                try:
                    BlinkLogger().log_info('Bonjour Account listens on %s' % bonjour_account.contact[transport])
                except KeyError:
                    pass

        self.lookup_stun_servers(self._selected_account)

    def _NH_SIPApplicationWillEnd(self, sender, data):
        self.ip_address_monitor.stop()
        self.ringer.stop()

    def _NH_DNSLookupDidFail(self, lookup, data):
        self.notification_center.remove_observer(self, sender=lookup)

        if lookup.type == 'stun_servers':
            account = lookup.owner
            message = u"DNS Lookup for STUN servers for %s failed: %s" % (account.id.domain, data.error)
            # stun lookup errors can be ignored
        elif lookup.type == 'sip_proxies':
            session_controller = lookup.owner
            message = u"DNS Lookup for SIP proxies for %s failed: %s" % (unicode(session_controller.target_uri), data.error)
            call_in_gui_thread(session_controller.setRoutesFailed, message)
        else:
            # we should never get here
            raise RuntimeError("Got DNSLookup failure for unknown request type: %s: %s" % (lookup.type, data.error))
        BlinkLogger().log_error(message)

    def _NH_DNSLookupDidSucceed(self, lookup, data):
        self.notification_center.remove_observer(self, sender=lookup)

        if lookup.type == 'stun_servers':
            account = lookup.owner
            BlinkLogger().log_info("DNS Lookup for STUN servers of domain %s succeeded: %s" % (account.id.domain, data.result))
        elif lookup.type == 'sip_proxies':
            session_controller = lookup.owner
            BlinkLogger().log_info("DNS Lookup for SIP routes of %s succeeded: %s" % (session_controller.target_uri, data.result))
            routes = data.result
            if not routes:
                call_in_gui_thread(session_controller.setRoutesFailed, "No routes found to SIP Proxy")
            else:
                call_in_gui_thread(session_controller.setRoutesResolved, routes)
        else:
            # we should never get here
            raise RuntimeError("Got DNSLookup result for unknown request type: %s" % lookup.type)

    def _NH_SIPEngineGotException(self, sender, data):
        print "SIP Engine Exception", data

    def _NH_SIPAccountDidActivate(self, account, data):
        BlinkLogger().log_debug("%s activated" % account)
        call_in_gui_thread(self._delegate.sip_account_list_refresh)

    def _NH_SIPAccountDidDeactivate(self, account, data):
        BlinkLogger().log_debug("%s deactivated" % account)
        MWIData.remove(account)
        call_in_gui_thread(self._delegate.sip_account_list_refresh)

    def _NH_SIPAccountRegistrationDidSucceed(self, account, data):
        message = '%s Registered Contact Address "%s" for sip:%s at %s:%d;transport=%s (expires in %d seconds).\n' % (datetime.datetime.now().replace(microsecond=0), data.contact_header.uri, account.id, data.registrar.address, data.registrar.port, data.registrar.transport, data.expires)
        contact_header_list = data.contact_header_list
        if len(contact_header_list) > 1:
            message += 'Other registered Contact Addresses:\n%s\n' % '\n'.join('  %s (expires in %s seconds)' % (other_contact_header.uri, other_contact_header.expires) for other_contact_header in contact_header_list if other_contact_header.uri!=data.contact_header.uri)
        BlinkLogger().log_info(message)
        call_in_gui_thread(self._delegate.sip_account_registration_succeeded, account)

    def _NH_SIPAccountRegistrationDidEnd(self, account, data):
        BlinkLogger().log_info("%s was unregistered" % account)
        call_in_gui_thread(self._delegate.sip_account_registration_ended, account)

    def _NH_SIPAccountRegistrationDidFail(self, account, data):
        BlinkLogger().log_info("%s failed to register: %s (retrying in %.2f seconds)" % (account, data.error, data.timeout))
        call_in_gui_thread(self._delegate.sip_account_registration_failed, account, data.error)

    @run_in_gui_thread
    def _NH_SIPAccountMWIDidGetSummary(self, account, data):
        BlinkLogger().log_info("Got NOTIFY for MWI of account %s" % account.id)
        summary = data.message_summary
        if summary.summaries.get('voice-message') is None:
            return
        voice_messages = summary.summaries['voice-message']
        growl_data = TimestampedNotificationData()
        growl_data.new_messages = int(voice_messages['new_messages'])
        growl_data.old_messages = int(voice_messages['old_messages'])
        MWIData.store(account, summary)
        if summary.messages_waiting and growl_data.new_messages > 0:
            self.notification_center.post_notification("GrowlGotMWI", sender=self, data=growl_data)

    def _NH_CFGSettingsObjectDidChange(self, account, data):
        if isinstance(account, Account):
            if 'message_summary.enabled' in data.modified:
                if not account.message_summary.enabled:
                    MWIData.remove(account)
            call_in_gui_thread(self._delegate.sip_account_list_refresh)

    def _NH_SIPSessionNewIncoming(self, session, data):
        self.ringer.add_incoming(session, data.streams)
        call_in_gui_thread(self._delegate.handle_incoming_session, session, data.streams)

    def _NH_SIPSessionNewOutgoing(self, session, data):
        BlinkLogger().log_info("Outgoing Session request to %s (%s)" % (session.remote_identity, [s.type for s in data.streams]))
        self.ringer.add_outgoing(session, data.streams)

    def _NH_SIPEngineDetectedNATType(self, engine, data):
        if data.succeeded:
            call_in_gui_thread(self._delegate.sip_nat_detected, data.nat_type)

    def _NH_BlinkSessionDidEnd(self, session, data):
        if session.direction == "incoming":
            self.log_incoming_session_ended(session, data)
        else:
            self.log_outgoing_session_ended(session, data)

    @allocate_autorelease_pool
    @run_in_gui_thread
    def _NH_SIPSessionDidFail(self, session, data):
        if session.direction == "incoming":
            if data.code == 487 and data.failure_reason == 'Call completed elsewhere':
                self.log_incoming_session_answered_elsewhere(session, data)
            else:
                self.log_incoming_session_missed(session, data)

            if data.code == 487 and data.failure_reason != 'Call completed elsewhere':
                growl_data = TimestampedNotificationData()
                growl_data.caller = format_identity_simple(session.remote_identity, check_contact=True)
                growl_data.timestamp = data.timestamp
                if (len(session.proposed_streams) == 1 and session.proposed_streams[0].type == 'file-transfer'):
                    return
                growl_data.streams = ",".join(s.type for s in session.proposed_streams or [])
                growl_data.account = session.account.id.username + '@' + session.account.id.domain
                self.notification_center.post_notification("GrowlMissedCall", sender=self, data=growl_data)
                self._delegate.sip_session_missed(session)

    @allocate_autorelease_pool
    @run_in_gui_thread
    def _NH_BlinkSessionDidFail(self, session, data):
        if session.direction == "outgoing":
            if data.code == 487:
                self.log_outgoing_session_cancelled(session, data)
            else:
                self.log_outgoing_session_failed(session, data)

    def _NH_AudioStreamGotDTMF(self, sender, data):
        key = data.digit
        filename = 'dtmf_%s_tone.wav' % {'*': 'star', '#': 'pound'}.get(key, key)
        wave_player = WavePlayer(SIPApplication.voice_audio_mixer, ResourcePath(filename).normalized)
        self.notification_center.add_observer(self, sender=wave_player)
        SIPApplication.voice_audio_bridge.add(wave_player)
        wave_player.start()

    def _NH_WavePlayerDidFail(self, sender, data):
        self.notification_center.remove_observer(self, sender=sender)

    def _NH_WavePlayerDidEnd(self, sender, data):
        self.notification_center.remove_observer(self, sender=sender)


class MWIData(object):
    """Saves Message-Summary information in memory"""

    _data = {}

    @classmethod
    def store(cls, account, message_summary):
        if message_summary.summaries.get('voice-message') is None:
            return
        voice_messages = message_summary.summaries['voice-message']
        d = dict(messages_waiting=message_summary.messages_waiting, new_messages=int(voice_messages.get('new_messages', 0)), old_messages=int(voice_messages.get('old_messages', 0)))
        cls._data[account.id] = d

    @classmethod
    def remove(cls, account):
        cls._data.pop(account.id, None)

    @classmethod
    def get(cls, account_id):
        return cls._data.get(account_id, None)

