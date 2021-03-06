# Copyright (C) 2009-2011 AG Projects. See LICENSE for details.
#

from AppKit import (NSFontAttributeName,
                    NSForegroundColorAttributeName,
                    NSOnState,
                    NSOffState)
from Foundation import (NSAttributedString,
                        NSBundle,
                        NSColor,
                        NSDictionary,
                        NSFont,
                        NSMakeRange,
                        NSMutableAttributedString,
                        NSNotificationCenter,
                        NSObject,
                        NSString
                        )
import objc

from datetime import datetime

from application.notification import NotificationCenter, IObserver
from application.python import Null
from zope.interface import implements

from BlinkLogger import BlinkLogger
from sipsimple.configuration.settings import SIPSimpleSettings
from util import allocate_autorelease_pool, run_in_gui_thread, format_size
from SessionInfoController import ice_candidates

# User choices for debug: Disabled, Simplified, Full
#
class Disabled(int):
    def __new__(cls):
        return int.__new__(cls, 1)
    def __eq__(self, value):
        return value==0 or value==1
    def __ne__(self, value):
        return value!=0 and value!=1
    def __repr__(self):
        return self.__class__.__name__

class Simplified(int):
    def __new__(cls):
        return int.__new__(cls, 2)
    def __repr__(self):
        return self.__class__.__name__

class Full(int):
    def __new__(cls):
        return int.__new__(cls, 3)
    def __repr__(self):
        return self.__class__.__name__

Disabled = Disabled()
Simplified = Simplified()
Full = Full()


class DebugWindow(NSObject):
    implements(IObserver)

    window = objc.IBOutlet()

    tabView = objc.IBOutlet()

    activityTextView = objc.IBOutlet()
    sipTextView = objc.IBOutlet()
    rtpTextView = objc.IBOutlet()
    msrpTextView = objc.IBOutlet()
    xcapTextView = objc.IBOutlet()
    notificationsTextView = objc.IBOutlet()
    pjsipTextView = objc.IBOutlet()

    activityInfoLabel = objc.IBOutlet()
    sipInfoLabel = objc.IBOutlet()
    msrpInfoLabel = objc.IBOutlet()
    rtpInfoLabel = objc.IBOutlet()
    xcapInfoLabel = objc.IBOutlet()
    notificationsInfoLabel = objc.IBOutlet()
    pjsipInfoLabel = objc.IBOutlet()
    filterSipApplication = objc.IBOutlet()

    sipInCount = 0
    sipOutCount = 0
    sipBytes = 0

    msrpInCount = 0
    msrpOutCount = 0
    msrpBytes = 0

    pjsipCount = 0
    pjsipBytes = 0

    notificationsBytes = 0

    filterNotificationsSearchBox = objc.IBOutlet()

    sipRadio = objc.IBOutlet()
    msrpRadio = objc.IBOutlet()
    xcapRadio = objc.IBOutlet()
    notificationsCheckBox = objc.IBOutlet()
    pjsipCheckBox = objc.IBOutlet()

    notifications = []
    notifications_unfiltered = []

    lastSIPMessageWasDNS = False

    _siptrace_start_time = None
    _siptrace_packet_count = 0

    filter_sip_application = 'sessions'
    filter_sip_methods = {
                          'PUBLISH': ['subscriptions'],
                          'NOTIFY': ['subscriptions'],
                          'SUBSCRIBE': ['subscriptions'],
                          'REGISTER': ['sessions', 'register'],
                          'INVITE': ['sessions'],
                          'BYE': ['sessions'],
                          'CANCEL': ['sessions'],
                          'ACK': ['sessions'],
                          'PRACK': ['sessions'],
                          'REFER': ['sessions'],
                          'MESSAGE': ['messages'],
                          'UPDATE': ['sessions']
                         }

    grayText = NSDictionary.dictionaryWithObject_forKey_(NSColor.grayColor(), NSForegroundColorAttributeName)
    boldTextAttribs = NSDictionary.dictionaryWithObject_forKey_(NSFont.boldSystemFontOfSize_(NSFont.systemFontSize()), NSFontAttributeName)
    boldRedTextAttribs = NSDictionary.dictionaryWithObjectsAndKeys_(NSFont.boldSystemFontOfSize_(NSFont.systemFontSize()), NSFontAttributeName, NSColor.redColor(), NSForegroundColorAttributeName)
    newline = NSAttributedString.alloc().initWithString_("\n")
    receivedText = NSAttributedString.alloc().initWithString_attributes_("RECEIVED:", NSDictionary.dictionaryWithObject_forKey_(NSColor.blueColor(), NSForegroundColorAttributeName))
    sendingText = NSAttributedString.alloc().initWithString_attributes_("SENDING:", NSDictionary.dictionaryWithObject_forKey_(NSColor.orangeColor(), NSForegroundColorAttributeName))

    def init(self):
        self = super(DebugWindow, self).init()

        NSBundle.loadNibNamed_owner_("DebugWindow", self)

        for textView in [self.activityTextView, self.sipTextView, self.rtpTextView, self.msrpTextView, self.xcapTextView, self.pjsipTextView]:
            textView.setString_("")

        for label in [self.activityInfoLabel, self.sipInfoLabel, self.rtpInfoLabel, self.msrpInfoLabel, self.xcapInfoLabel, self.notificationsInfoLabel, self.pjsipInfoLabel]:
            label.setStringValue_('')

        BlinkLogger().set_gui_logger(self.renderActivity)

        settings = SIPSimpleSettings()

        notification_center = NotificationCenter()
        notification_center.add_observer(self, name="CFGSettingsObjectDidChange")
        notification_center.add_observer(self, name="SIPSessionDidStart")
        notification_center.add_observer(self, name="SIPEngineSIPTrace")
        notification_center.add_observer(self, name="MSRPLibraryLog")
        notification_center.add_observer(self, name="MSRPTransportTrace")
        notification_center.add_observer(self, name="SIPEngineLog")

        notification_center.add_observer(self, name="SIPSessionDidRenegotiateStreams")
        notification_center.add_observer(self, name="AudioSessionHasQualityIssues")
        notification_center.add_observer(self, name="AudioSessionQualityRestored")
        notification_center.add_observer(self, name="AudioStreamICENegotiationDidSucceed")
        notification_center.add_observer(self, name="AudioStreamICENegotiationDidFail")
        notification_center.add_observer(self, name="VideoStreamICENegotiationDidSucceed")
        notification_center.add_observer(self, name="VideoStreamICENegotiationDidFail")

        if settings.logs.trace_notifications_in_gui:
            notification_center.add_observer(self)

        self.sipRadio.selectCellWithTag_(settings.logs.trace_sip_in_gui or Disabled)
        self.msrpRadio.selectCellWithTag_(settings.logs.trace_msrp_in_gui or Disabled)
        self.xcapRadio.selectCellWithTag_(settings.logs.trace_xcap_in_gui or Disabled)
        self.pjsipCheckBox.setState_(NSOnState if settings.logs.trace_pjsip_in_gui  else NSOffState)
        self.notificationsCheckBox.setState_(NSOnState if settings.logs.trace_notifications_in_gui  else NSOffState)

        return self

    def show(self):
        self.window.makeKeyAndOrderFront_(self)

    def close_(self, sender):
        self.window.close()

    def tabView_didSelectTabViewItem_(self, tabView, item):
        pass

    def numberOfRowsInTableView_(self, table):
        return len(self.notifications)

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        return self.notifications[row][int(column.identifier())]

    @objc.IBAction
    def notificationsCheckboxClicked_(self, sender):
        settings = SIPSimpleSettings()
        settings.logs.trace_notifications_in_gui = bool(sender.state())
        settings.logs.trace_notifications = settings.logs.trace_notifications_in_gui or settings.logs.trace_notifications_to_file
        settings.save()

        notification_center = NotificationCenter()

        if settings.logs.trace_notifications_in_gui:
            notification_center.add_observer(self)
        else:
            notification_center.discard_observer(self)

    @objc.IBAction
    def engineCheckboxClicked_(self, sender):
        settings = SIPSimpleSettings()
        settings.logs.trace_pjsip_in_gui = bool(sender.state())
        settings.logs.trace_pjsip = settings.logs.trace_pjsip_in_gui or settings.logs.trace_pjsip_to_file
        settings.save()

    @objc.IBAction
    def sipRadioClicked_(self, sender):
        notification_center = NotificationCenter()
        trace = sender.selectedCell().tag()
        settings = SIPSimpleSettings()
        settings.logs.trace_sip_in_gui = trace
        if trace == Disabled:
            notification_center.discard_observer(self, name="DNSLookupTrace")
            settings.logs.trace_sip = settings.logs.trace_sip_to_file
        elif trace == Simplified:
            notification_center.add_observer(self, name="DNSLookupTrace")
            settings.logs.trace_sip = True
        elif trace == Full:
            notification_center.add_observer(self, name="DNSLookupTrace")
            settings.logs.trace_sip = True

        settings.save()

    @objc.IBAction
    def msrpRadioClicked_(self, sender):
        trace = sender.selectedCell().tag()
        settings = SIPSimpleSettings()
        settings.logs.trace_msrp_in_gui = trace
        if trace == Disabled:
            settings.logs.trace_msrp = settings.logs.trace_msrp_to_file
        elif trace == Simplified:
            settings.logs.trace_msrp = True
        elif trace == Full:
            settings.logs.trace_msrp = True

        settings.save()

    @objc.IBAction
    def xcapRadioClicked_(self, sender):
        notification_center = NotificationCenter()
        trace = sender.selectedCell().tag()
        settings = SIPSimpleSettings()
        settings.logs.trace_xcap_in_gui = trace
        if trace == Disabled:
            notification_center.discard_observer(self, name="XCAPManagerDidDiscoverServerCapabilities")
            notification_center.discard_observer(self, name="XCAPSubscriptionGotNotify")
            notification_center.discard_observer(self, name="XCAPManagerDidChangeState")
            settings.logs.trace_xcap = settings.logs.trace_xcap_to_file
        elif trace == Simplified:
            notification_center.add_observer(self, name="XCAPManagerDidDiscoverServerCapabilities")
            notification_center.add_observer(self, name="XCAPManagerDidChangeState")
            settings.logs.trace_xcap = True
        elif trace == Full:
            notification_center.add_observer(self, name="XCAPManagerDidDiscoverServerCapabilities")
            notification_center.add_observer(self, name="XCAPManagerDidChangeState")
            notification_center.add_observer(self, name="XCAPSubscriptionGotNotify")
            settings.logs.trace_xcap = True

        settings.save()


    @objc.IBAction
    def filterSipApplicationClicked_(self, sender):
        tag = sender.selectedItem().tag()
        self.filter_sip_application = None
        if tag == 1:
            self.filter_sip_application = 'sessions'
        elif tag == 2:
            self.filter_sip_application = 'subscriptions'
        elif tag == 3:
            self.filter_sip_application = 'register'
        elif tag == 4:
            self.filter_sip_application = 'messages'

    @objc.IBAction
    def clearClicked_(self, sender):
        if sender.tag() == 100:
            self.activityTextView.textStorage().deleteCharactersInRange_(NSMakeRange(0, self.activityTextView.textStorage().length()))
        elif sender.tag() == 101:
            self.sipTextView.textStorage().deleteCharactersInRange_(NSMakeRange(0, self.sipTextView.textStorage().length()))
            self.sipInCount = 0
            self.sipOutCount = 0
            self.sipBytes = 0
            self.sipInfoLabel.setStringValue_('')
        elif sender.tag() == 102:
            self.rtpTextView.textStorage().deleteCharactersInRange_(NSMakeRange(0, self.rtpTextView.textStorage().length()))
        elif sender.tag() == 104:
            self.msrpInCount = 0
            self.msrpOutCount = 0
            self.msrpBytes = 0
            self.msrpInfoLabel.setStringValue_('')
            self.msrpTextView.textStorage().deleteCharactersInRange_(NSMakeRange(0, self.msrpTextView.textStorage().length()))
        elif sender.tag() == 105:
            self.xcapTextView.textStorage().deleteCharactersInRange_(NSMakeRange(0, self.xcapTextView.textStorage().length()))
        elif sender.tag() == 103:
            self.notifications = []
            self.notifications_unfiltered = []
            self.notificationsBytes = 0
            self.notificationsTextView.reloadData()
            self.notificationsInfoLabel.setStringValue_('')
        elif sender.tag() == 107:
            self.pjsipCount = 0
            self.pjsipBytes = 0
            self.pjsipInfoLabel.setStringValue_('')
            self.pjsipTextView.textStorage().deleteCharactersInRange_(NSMakeRange(0, self.pjsipTextView.textStorage().length()))

    @objc.IBAction
    def searchNotifications_(self, sender):
        self.renderNotifications()

    def renderNotifications(self):
        text = unicode(self.filterNotificationsSearchBox.stringValue().strip().lower())
        self.notifications = [notification for notification in self.notifications_unfiltered if text in notification[0].lower()] if text else self.notifications_unfiltered
        self.notificationsTextView.noteNumberOfRowsChanged()
        self.notificationsTextView.scrollRowToVisible_(len(self.notifications)-1)
        self.notificationsInfoLabel.setStringValue_('%d notifications, %sytes' % (len(self.notifications), format_size(self.notificationsBytes)) if not text else '%d notifications matched' % len(self.notifications))

    def dealloc(self):
        # Observers added in init
        NSNotificationCenter.defaultCenter().removeObserver_(self)
        notification_center = NotificationCenter()
        notification_center.discard_observer(self, name="SIPSessionDidStart")
        notification_center.discard_observer(self, name="SIPSessionDidRenegotiateStreams")
        notification_center.discard_observer(self, name="AudioSessionHasQualityIssues")
        notification_center.discard_observer(self, name="AudioSessionQualityRestored")
        notification_center.discard_observer(self, name="AudioStreamICENegotiationDidSucceed")
        notification_center.discard_observer(self, name="AudioStreamICENegotiationDidFail")
        notification_center.discard_observer(self, name="VideoStreamICENegotiationDidSucceed")
        notification_center.discard_observer(self, name="VideoStreamICENegotiationDidFail")

        # Observers added when settings change
        notification_center.discard_observer(self, name="SIPEngineSIPTrace")
        notification_center.discard_observer(self, name="DNSLookupTrace")
        notification_center.discard_observer(self, name="MSRPLibraryLog")
        notification_center.discard_observer(self, name="MSRPTransportTrace")
        notification_center.discard_observer(self, name="XCAPManagerDidDiscoverServerCapabilities")
        notification_center.discard_observer(self, name="XCAPSubscriptionGotNotify")
        notification_center.discard_observer(self, name="XCAPManagerDidChangeState")
        notification_center.discard_observer(self, name="SIPEngineLog")
        notification_center.discard_observer(self)

        super(DebugWindow, self).dealloc()

    def append_line(self, textView, line):
        if isinstance(line, NSAttributedString):
            textView.textStorage().appendAttributedString_(line)
        else:
            textView.textStorage().appendAttributedString_(NSAttributedString.alloc().initWithString_(line+"\n"))

        textView.scrollRangeToVisible_(NSMakeRange(textView.textStorage().length()-1, 1))

    def append_error_line(self, textView, line):
        red = NSDictionary.dictionaryWithObject_forKey_(NSColor.redColor(), NSForegroundColorAttributeName)
        textView.textStorage().appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(line+"\n", red))
        textView.scrollRangeToVisible_(NSMakeRange(textView.textStorage().length()-1, 1))

    @allocate_autorelease_pool
    @run_in_gui_thread
    def renderActivity(self, text):
        iserror = text.lower().startswith("error")
        text = "%s   %s"%(datetime.now().replace(microsecond=0), text)
        if iserror:
            self.append_error_line(self.activityTextView, text)
        else:
            self.append_line(self.activityTextView, text)

    def renderRTP(self, session):
        self.renderAudio(session)
        self.renderVideo(session)

    def renderAudio(self, session):
        try:
            audio_stream = (s for s in session.streams or [] if s.type=='audio').next()
        except StopIteration:
            return

        text = u'\n%s New Audio call %s\n' % (session.start_time, session.remote_identity)
        if audio_stream.local_rtp_address and audio_stream.local_rtp_port and audio_stream.remote_rtp_address and audio_stream.remote_rtp_port:
            if audio_stream.ice_active and audio_stream.local_rtp_candidate and audio_stream.remote_rtp_candidate:
                text += '%s Audio RTP endpoints %s:%d (ICE type %s) <-> %s:%d (ICE type %s)\n' % (session.start_time,
                                                                                                  audio_stream.local_rtp_address,
                                                                                                  audio_stream.local_rtp_port,
                                                                                                  ice_candidates[audio_stream.local_rtp_candidate.type.lower()],
                                                                                                  audio_stream.remote_rtp_address,
                                                                                                  audio_stream.remote_rtp_port,
                                                                                                  ice_candidates[audio_stream.remote_rtp_candidate.type.lower()])
            else:
                text += '%s Audio RTP endpoints %s:%d <-> %s:%d\n' % (session.start_time,
                                                                      audio_stream.local_rtp_address,
                                                                      audio_stream.local_rtp_port,
                                                                      audio_stream.remote_rtp_address,
                                                                      audio_stream.remote_rtp_port)
        if audio_stream.codec and audio_stream.sample_rate:
            text += '%s Audio call established using "%s" codec at %sHz\n' % (session.start_time, audio_stream.codec, audio_stream.sample_rate)
        if audio_stream.srtp_active:
            text += '%s RTP audio stream is encrypted\n' % session.start_time
        if session.remote_user_agent is not None:
            text += '%s Remote SIP User Agent is "%s"\n' % (session.start_time, session.remote_user_agent)

        astring = NSAttributedString.alloc().initWithString_(text)
        self.rtpTextView.textStorage().appendAttributedString_(astring)
        self.rtpTextView.scrollRangeToVisible_(NSMakeRange(self.rtpTextView.textStorage().length()-1, 1))

    def renderVideo(self, session):
        try:
            video_stream = (s for s in session.streams or [] if s.type=='video').next()
        except StopIteration:
            return

        text = u'\n%s New Video call %s\n' % (session.start_time, session.remote_identity)
        if video_stream.local_rtp_address and video_stream.local_rtp_port and video_stream.remote_rtp_address and video_stream.remote_rtp_port:
            if video_stream.ice_active and video_stream.local_rtp_candidate and video_stream.remote_rtp_candidate:
                text += '%s Video RTP endpoints %s:%d (ICE type %s) <-> %s:%d (ICE type %s)\n' % (session.start_time,
                                                                                                  video_stream.local_rtp_address,
                                                                                                  video_stream.local_rtp_port,
                                                                                                  ice_candidates[video_stream.local_rtp_candidate.type.lower()],
                                                                                                  video_stream.remote_rtp_address,
                                                                                                  video_stream.remote_rtp_port,
                                                                                                  ice_candidates[video_stream.remote_rtp_candidate.type.lower()])
            else:
                text += '%s Video RTP endpoints %s:%d <-> %s:%d\n' % (session.start_time,
                                                                      video_stream.local_rtp_address,
                                                                      video_stream.local_rtp_port,
                                                                      video_stream.remote_rtp_address,
                                                                      video_stream.remote_rtp_port)
        if video_stream.codec and video_stream.clock_rate:
            text += '%s Video call established using "%s" codec at %sHz\n' % (session.start_time, video_stream.codec, video_stream.clock_rate)
        if video_stream.srtp_active:
            text += '%s RTP video stream is encrypted\n' % session.start_time
        if session.remote_user_agent is not None:
            text += '%s Remote SIP User Agent is "%s"\n' % (session.start_time, session.remote_user_agent)

        astring = NSAttributedString.alloc().initWithString_(text)
        self.rtpTextView.textStorage().appendAttributedString_(astring)
        self.rtpTextView.scrollRangeToVisible_(NSMakeRange(self.rtpTextView.textStorage().length()-1, 1))

    @allocate_autorelease_pool
    def renderSIP(self, notification):
        settings = SIPSimpleSettings()
        if settings.logs.trace_sip_in_gui == Disabled:
            return

        event_data = notification.data
        self.sipBytes += len(event_data.data)
        if self._siptrace_start_time is None:
            self._siptrace_start_time = notification.datetime
        self._siptrace_packet_count += 1

        text = NSMutableAttributedString.alloc().init()

        if self.lastSIPMessageWasDNS:
            text.appendAttributedString_(self.newline)
        self.lastSIPMessageWasDNS = False

        if event_data.received:
            self.sipInCount += 1
            text.appendAttributedString_(self.receivedText)
        else:
            self.sipOutCount += 1
            text.appendAttributedString_(self.sendingText)

        line = " Packet %d, +%s\n" % (self._siptrace_packet_count, (notification.datetime - self._siptrace_start_time))
        text.appendAttributedString_(NSAttributedString.alloc().initWithString_(line))

        line = "%s: %s:%d -(SIP over %s)-> %s:%d\n" % (notification.datetime, event_data.source_ip, event_data.source_port, event_data.transport, event_data.destination_ip, event_data.destination_port)
        text.appendAttributedString_(NSAttributedString.alloc().initWithString_(line))

        data = event_data.data.strip()
        first, rest = data.split("\n", 1)

        applications = None
        method = None
        msg_type = None
        event = None
        code = None

        if data.startswith("SIP/2.0"):
            try:
                code = first.split()[1]
                attribs = self.boldRedTextAttribs if code[0] in ["4", "5", "6"] else self.boldTextAttribs
                for line in data.split("\n"):
                    line = line.strip()
                    if line.startswith("Event:"):
                        try:
                            event = line.split(" ", 1)[1]
                        except IndexError:
                            pass
                        continue
                    if line.startswith("CSeq"):
                        cseq, _number, _method = line.split(" ", 2)
                        try:
                            applications = self.filter_sip_methods[_method.strip()]
                            method = _method
                            msg_type = 'response'
                        except KeyError:
                            pass
                        continue

                if settings.logs.trace_sip_in_gui == Full:
                    text.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(first+"\n", attribs))
                    text.appendAttributedString_(NSAttributedString.alloc().initWithString_(rest+"\n"))
                else:
                    text.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(first+"\n", attribs))

            except:
                text.appendAttributedString_(NSAttributedString.alloc().initWithString_(data+"\n"))
        else:
            _method = first.split()[0]
            try:
                applications = self.filter_sip_methods[_method]
                method = _method
                msg_type = 'offer'
            except KeyError:
                pass

            for line in data.split("\n"):
                line = line.strip()
                if line.startswith("Event:"):
                    try:
                        event = line.split(" ", 1)[1]
                    except IndexError:
                        pass
                    continue

            if settings.logs.trace_sip_in_gui == Full:
                text.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(first+"\n", self.boldTextAttribs))
                text.appendAttributedString_(NSAttributedString.alloc().initWithString_(rest+"\n"))
            else:
                text.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(first+"\n", self.boldTextAttribs))

        self.sipInfoLabel.setStringValue_("%d SIP messages sent, %d SIP messages received, %sytes" % (self.sipOutCount, self.sipInCount, format_size(self.sipBytes)))


        if self.filter_sip_application is not None and applications is not None:
            if self.filter_sip_application not in applications:
                return

        self.sipTextView.textStorage().appendAttributedString_(text)
        self.sipTextView.textStorage().appendAttributedString_(self.newline)
        self.sipTextView.scrollRangeToVisible_(NSMakeRange(self.sipTextView.textStorage().length()-1, 1))

    def renderDNS(self, text):
        settings = SIPSimpleSettings()
        if settings.logs.trace_sip_in_gui == Disabled:
            return

        self.lastSIPMessageWasDNS = True
        self.append_line(self.sipTextView, text)

    def renderPJSIP(self, text):
        if self.pjsipCheckBox.state() == NSOnState:
            iserror = 'error' in text.lower()
            self.pjsipCount += 1
            self.pjsipBytes += len(text)
            if iserror:
                self.append_error_line(self.pjsipTextView, text)
            else:
                self.append_line(self.pjsipTextView, text)

            self.pjsipInfoLabel.setStringValue_("%d lines, %sytes" % (self.pjsipCount, format_size(self.pjsipBytes)))

    def renderXCAP(self, text):
        settings = SIPSimpleSettings()
        if settings.logs.trace_xcap_in_gui != Disabled:
            self.append_line(self.xcapTextView, text)

    @allocate_autorelease_pool
    @run_in_gui_thread
    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification)

        if notification.name in ('SIPEngineSIPTrace', 'SIPEngineLog', 'MSRPLibraryLog', 'MSRPTransportTrace'):
            return

        # notifications text view
        if self.notificationsCheckBox.state() == NSOnState:
            attribs = notification.data.__dict__.copy()

            # remove some data that would be too big to log
            if notification.name == "MSRPTransportTrace":
                if len(attribs["data"]) > 30:
                    attribs["data"] = "<%i bytes>"%len(attribs["data"])
            elif notification.name in ("FileTransferStreamGotChunk", "ScreenSharingStreamGotData"):
                attribs["content"] = "..."
                if attribs.has_key("data"):
                    attribs["data"] = "..."

            attribs = ", ".join("%s=%s" % (k, v) for k, v in attribs.iteritems())
            ts = notification.datetime
            ts = ts.replace(microsecond=0) if type(ts) == datetime else ""

            self.notificationsBytes += len(notification.name) + len(str(notification.sender)) + len(attribs) + len(str(ts))
            self.notifications_unfiltered.append((NSString.stringWithString_(notification.name),
                                            NSString.stringWithString_(str(notification.sender)),
                                            NSString.stringWithString_(attribs),
                                            NSString.stringWithString_(str(ts))))
            self.renderNotifications()

    def _NH_CFGSettingsObjectDidChange(self, notification):
        sender = notification.sender
        settings = SIPSimpleSettings()

    def _NH_SIPSessionDidStart(self, notification):
        self.renderRTP(notification.sender)

    def _NH_SIPSessionDidRenegotiateStreams(self, notification):
        if notification.data.added_streams:
            self.renderRTP(notification.sender)

    def _NH_AudioSessionHasQualityIssues(self, notification):
        text = '%s Audio call quality to %s is poor: loss %s, rtt: %s\n' % (notification.datetime, notification.sender.sessionController.target_uri, notification.data.packet_loss, notification.data.latency)
        astring = NSAttributedString.alloc().initWithString_(text)
        self.rtpTextView.textStorage().appendAttributedString_(astring)
        self.rtpTextView.scrollRangeToVisible_(NSMakeRange(self.rtpTextView.textStorage().length()-1, 1))

    def _NH_AudioSessionQualityRestored(self, notification):
        text = '%s Audio call quality to %s is back to normal: loss %s, rtt: %s\n' % (notification.datetime, notification.sender.sessionController.target_uri, notification.data.packet_loss, notification.data.latency)
        astring = NSAttributedString.alloc().initWithString_(text)
        self.rtpTextView.textStorage().appendAttributedString_(astring)
        self.rtpTextView.scrollRangeToVisible_(NSMakeRange(self.rtpTextView.textStorage().length()-1, 1))

    def _NH_MSRPTransportTrace(self, notification):
        settings = SIPSimpleSettings()
        if settings.logs.trace_msrp_in_gui == Disabled:
            return

        arrow = {'incoming': '<--', 'outgoing': '-->'}[notification.data.direction]

        try:
            local_address = notification.sender.transport.getHost()
            local_address = '%s:%d' % (local_address.host, local_address.port)
        except AttributeError:
            # this may happen because we process this notification after transport has been disconnected
            local_address = 'local'

        remote_address = notification.sender.getPeer()
        remote_address = '%s:%d' % (remote_address.host, remote_address.port)

        message = '\n%s: %s %s %s' % (notification.datetime, local_address, arrow, remote_address)
        header = []
        if settings.logs.trace_msrp_in_gui == Full:
            header = notification.data.data.split("\n")
        else:
            if notification.data.data.startswith("MSRP "):
                lines = notification.data.data.split("\n")
                for line in lines:
                    if not line.strip() or line[0] == "-":
                        break
                    header.append(line)

        if notification.data.direction == "outgoing":
            self.msrpOutCount += 1
            self.append_line(self.msrpTextView, self.sendingText)
        else:
            self.msrpInCount += 1
            self.append_line(self.msrpTextView, self.receivedText)

        self.msrpBytes += len(message)
        self.append_line(self.msrpTextView, message)
        if header:
            try:
                dummy, ident, code, msg = header[0].split(None, 3)
                attribs = self.boldRedTextAttribs if int(code) >= 400 else self.boldTextAttribs
                self.append_line(self.msrpTextView, NSAttributedString.alloc().initWithString_attributes_(header[0], attribs))
            except:
                self.append_line(self.msrpTextView, NSAttributedString.alloc().initWithString_attributes_(header[0], self.boldTextAttribs))

            self.append_line(self.msrpTextView, "\n".join(header[1:]))

        if settings.logs.trace_msrp_in_gui != Full:
            self.append_line(self.msrpTextView, self.newline)

        self.msrpInfoLabel.setStringValue_("%d MSRP messages sent, %d MRSP messages received, %sytes" % (self.msrpOutCount, self.msrpInCount, format_size(self.msrpBytes)))

    def _NH_MSRPLibraryLog(self, notification):
        settings = SIPSimpleSettings()
        if settings.logs.trace_msrp_in_gui == Disabled:
            return

        message = '%s %s%s\n\n' % (notification.datetime, notification.data.level.prefix, notification.data.message)
        text = NSAttributedString.alloc().initWithString_attributes_(message, self.grayText)
        self.append_line(self.msrpTextView, text)

    def _NH_AudioStreamDidChangeRTPParameters(self, notification):
        stream = notification.sender

        text = u'%s Audio call to %s: RTP parameters changed\n' % (notification.datetime, stream.session.remote_identity)
        if stream.local_rtp_address and stream.local_rtp_port and stream.remote_rtp_address and stream.remote_rtp_port:
            text += '%s Audio RTP endpoints %s:%d <-> %s:%d\n' % (notification.datetime,
                                                                  stream.local_rtp_address,
                                                                  stream.local_rtp_port,
                                                                  stream.remote_rtp_address,
                                                                  stream.remote_rtp_port)
        if stream.codec and stream.sample_rate:
            text += '%s Audio call established using "%s" codec at %sHz\n' % (notification.datetime, stream.codec, stream.sample_rate)
        if stream.srtp_active:
            text += '%s RTP audio stream is encrypted\n' % notification.datetime
        astring = NSAttributedString.alloc().initWithString_(text)
        self.rtpTextView.textStorage().appendAttributedString_(astring)
        self.rtpTextView.scrollRangeToVisible_(NSMakeRange(self.rtpTextView.textStorage().length()-1, 1))

    def _NH_AudioStreamICENegotiationDidSucceed(self, notification):
        data = notification.data
        stream = notification.sender

        text = u'%s Audio call %s, ICE negotiation succeeded in %s\n' % (notification.datetime, stream.session.remote_identity, data.duration)
        if stream.local_rtp_candidate and stream.remote_rtp_candidate:
            text += '%s Audio RTP endpoints: %s:%d (ICE type %s) <-> %s:%d (ICE type %s)' % (notification.datetime,
                                                                                             stream.local_rtp_address,
                                                                                             stream.local_rtp_port,
                                                                                             stream.local_rtp_candidate.type.lower(),
                                                                                             stream.remote_rtp_address,
                                                                                             stream.remote_rtp_port,
                                                                                             stream.remote_rtp_candidate.type.lower())

        text += '\nAudio Local ICE candidates:\n'
        for candidate in data.local_candidates:
            text += '\t%s\n' % candidate
        text += '\nAudio Remote ICE candidates:\n'
        for candidate in data.remote_candidates:
            text += '\t%s\n' % candidate
        text += '\nAudio ICE connectivity checks results:\n'
        for check in data.valid_list:
            text += '\t%s\n' % check
        astring = NSAttributedString.alloc().initWithString_(text)
        self.rtpTextView.textStorage().appendAttributedString_(astring)
        self.rtpTextView.scrollRangeToVisible_(NSMakeRange(self.rtpTextView.textStorage().length()-1, 1))

    def _NH_VideoStreamICENegotiationDidSucceed(self, notification):
        data = notification.data
        stream = notification.sender

        text = u'%s Video call %s, ICE negotiation succeeded in %s\n' % (notification.datetime, stream.session.remote_identity, data.duration)
        if stream.local_rtp_candidate and stream.remote_rtp_candidate:
            text += '%s Video RTP endpoints: %s:%d (ICE type %s) <-> %s:%d (ICE type %s)' % (notification.datetime,
                                                                                             stream.local_rtp_address,
                                                                                             stream.local_rtp_port,
                                                                                             stream.local_rtp_candidate.type.lower(),
                                                                                             stream.remote_rtp_address,
                                                                                             stream.remote_rtp_port,
                                                                                             stream.remote_rtp_candidate.type.lower())

        text += '\nVideo Local ICE candidates:\n'
        for candidate in data.local_candidates:
            text += '\t%s\n' % candidate
        text += '\nVideo Remote ICE candidates:\n'
        for candidate in data.remote_candidates:
            text += '\t%s\n' % candidate
        text += '\nVideo ICE connectivity checks results:\n'
        for check in data.valid_list:
            text += '\t%s\n' % check
        astring = NSAttributedString.alloc().initWithString_(text)
        self.rtpTextView.textStorage().appendAttributedString_(astring)
        self.rtpTextView.scrollRangeToVisible_(NSMakeRange(self.rtpTextView.textStorage().length()-1, 1))

    def _NH_AudioStreamICENegotiationDidFail(self, notification):
        data = notification.data

        text = '%s Audio ICE negotiation failed: %s\n' % (notification.datetime, data.reason)
        astring = NSAttributedString.alloc().initWithString_(text)
        self.rtpTextView.textStorage().appendAttributedString_(astring)
        self.rtpTextView.scrollRangeToVisible_(NSMakeRange(self.rtpTextView.textStorage().length()-1, 1))

    def _NH_VideoStreamICENegotiationDidFail(self, notification):
        data = notification.data

        text = '%s Video ICE negotiation failed: %s\n' % (notification.datetime, data.reason)
        astring = NSAttributedString.alloc().initWithString_(text)
        self.rtpTextView.textStorage().appendAttributedString_(astring)
        self.rtpTextView.scrollRangeToVisible_(NSMakeRange(self.rtpTextView.textStorage().length()-1, 1))

    def _NH_SIPEngineLog(self, notification):
        if self.pjsipCheckBox.state() == NSOnState:
            self.renderPJSIP("(%d) %s" % (notification.data.level, notification.data.message))

    def _NH_SIPEngineSIPTrace(self, notification):
        self.renderSIP(notification)

    def _NH_DNSLookupTrace(self, notification):
        data = notification.data
        message = '%s: DNS lookup %s %s' % (notification.datetime, data.query_type, data.query_name)
        if data.error is None:
            message += ' succeeded, ttl=%d: ' % data.answer.ttl
            if data.query_type == 'A':
                message += ", ".join(record.address for record in data.answer)
            elif data.query_type == 'TXT':
                for record in data.answer:
                    message += ", ".join(s for s in record.strings)
                self.renderXCAP(message)
            elif data.query_type == 'SRV':
                message += ", ".join('%d %d %d %s' % (record.priority, record.weight, record.port, record.target) for record in data.answer)
            elif data.query_type == 'NAPTR':
                message += ", ".join('%d %d "%s" "%s" "%s" %s' % (record.order, record.preference, record.flags, record.service, record.regexp, record.replacement) for record in data.answer)
        else:
            import dns.resolver
            message_map = {dns.resolver.NXDOMAIN: 'DNS record does not exist',
                dns.resolver.NoAnswer: 'DNS response contains no answer',
                dns.resolver.NoNameservers: 'no name servers could be reached',
                dns.resolver.Timeout: 'no response received, the query has timed out'}
            message += ' failed: %s' % message_map.get(data.error.__class__, '')
        self.renderDNS(message)

    def _NH_XCAPManagerDidDiscoverServerCapabilities(self, notification):
        account = notification.sender.account
        xcap_root = notification.sender.xcap_root
        if xcap_root is None:
            # The XCAP manager might be stopped because this notification is processed in a different
            # thread from which it was posted
            return
        self.renderXCAP(u"%s Using XCAP root %s for account %s" % (notification.datetime, xcap_root, account.id))
        message = (u"%s XCAP server capabilities: %s" % (notification.datetime, ", ".join(notification.data.auids)))
        self.renderXCAP(message)

    def _NH_XCAPSubscriptionGotNotify(self, notification):
        settings = SIPSimpleSettings()
        message = (u"%s XCAP server documents have changed for account %s: \n\n%s" % (notification.datetime, notification.sender.account.id, notification.data.body))
        if notification.data.body is not None and settings.logs.trace_xcap_in_gui == Full:
            self.renderXCAP(message)

    def _NH_XCAPManagerDidChangeState(self, notification):
        message = (u"%s XCAP manager of account %s changed state from %s to %s" % (notification.datetime, notification.sender.account.id, notification.data.prev_state.capitalize(), notification.data.state.capitalize()))
        self.renderXCAP(message)


