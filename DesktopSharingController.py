# Copyright (C) 2009-2011 AG Projects. See LICENSE for details.     
#

from Foundation import *
from AppKit import *

from application.notification import IObserver, NotificationCenter
from application.python import Null
from zope.interface import implements

from sipsimple.streams.msrp import DesktopSharingStream, ExternalVNCServerHandler, ExternalVNCViewerHandler, VNCConnectionError

from MediaStream import *
from util import allocate_autorelease_pool, run_in_gui_thread


DS_CLEANUP_DELAY = 4.0 


class StatusItem(NSObject):
    items = []
    menu = None
    statusItem = None

    def show(self, item):
        if not self.items:
            self.statusItem = NSStatusBar.systemStatusBar().statusItemWithLength_(30)
            self.menu = NSMenu.alloc().init()
            image = NSImage.imageNamed_("display").copy()
            image.setSize_(NSMakeSize(24, 24))
            self.statusItem.setImage_(image)
            self.statusItem.setMenu_(self.menu)
        self.items.append(item)
        mitem = self.menu.addItemWithTitle_action_keyEquivalent_("%s - Waiting" % item.sessionController.getTitle(),  "activateItem:", "")
        mitem.setTag_(item.sessionController.identifier)
        mitem.setTarget_(self)

    def activateItem_(self, sender):
        for item in self.items:
            if item.sessionController.identifier == sender.tag():
                item.end()
                break

    def remove(self, item):
        if self.menu:
            mitem = self.menu.itemWithTag_(item.sessionController.identifier)
            if mitem:
                self.menu.removeItem_(mitem)
                self.items.remove(item)
            if not self.items:
                NSStatusBar.systemStatusBar().removeStatusItem_(self.statusItem)
                self.statusItem = None
                self.menu = None

    def update(self, item, state):
        if self.menu:
            mitem = self.menu.itemWithTag_(item.sessionController.identifier)
            if mitem:
                name = item.sessionController.getTitleShort()
                if state == STREAM_CONNECTED:
                    mitem.setTitle_("Disconnect %s" % name)
                    mitem.setEnabled_(True)
                elif state in (STREAM_INCOMING, STREAM_PROPOSING, STREAM_WAITING_DNS_LOOKUP):
                    mitem.setTitle_("%s - Waiting" % name)
                    mitem.setEnabled_(True)
                elif state in (STREAM_DISCONNECTING, STREAM_CANCELLING):
                    mitem.setTitle_("%s %s" % (state.title(), name))
                    mitem.setEnabled_(False)
                else:
                    mitem.setTitle_(name)
                    mitem.setEnabled_(False)


class DesktopSharingController(MediaStream):
    implements(IObserver)

    viewer = None
    vncServerPort = None
    
    statusItem = StatusItem.alloc().init()
    
    statusWindow = objc.IBOutlet()
    statusLabel = objc.IBOutlet()
    statusProgress = objc.IBOutlet()

    @classmethod
    def createStream(cls, account):
        handler = None

        ret = NSRunAlertPanel("Desktop Sharing", 
                "Please select whether you'd like the to request the remote party to view and control your desktop or whether you'd like to request permission to view the remote desktop?",
                "Request Remote Desktop", "Cancel", "Offer My Desktop")
        if ret == NSAlertDefaultReturn:
            handler = ExternalVNCViewerHandler()
        elif ret == NSAlertOtherReturn:
            handler = ExternalVNCServerHandler(("localhost", cls.vncServerPort))
        else:
            return None
        return DesktopSharingStream(account, handler)

    def initWithOwner_stream_(self, scontroller, stream):
        self = super(DesktopSharingController, self).initWithOwner_stream_(scontroller, stream)
        if self:
            self.stream = stream
            self.direction = stream.handler.type
        return self

    def startIncoming(self, is_update):
        if self.direction == "active": # viewer
            # open viewer
            self.sessionController.log_info("Preparing to view remote desktop")
            self.stream.handler = ExternalVNCViewerHandler()
        else:
            self.sessionController.log_info("Preparing to offer desktop for viewing")
            self.stream.handler = ExternalVNCServerHandler(("localhost", self.vncServerPort))
            NSBundle.loadNibNamed_owner_("DesktopServerWindow", self)            
            self.statusProgress.startAnimation_(None)
            self.statusWindow.setTitle_("Desktop Sharing")
            self.statusItem.show(self)
        NotificationCenter().add_observer(self, sender=self.stream.handler)
        NotificationCenter().add_observer(self, sender=self.stream)
        self.changeStatus(STREAM_INCOMING)

    def startOutgoing(self, is_update):
        if self.direction == "active": # viewer
            # open viewer
            self.sessionController.log_info("Requesting to view remote desktop")
        else:
            self.sessionController.log_info("Offering desktop for viewing")
            NSBundle.loadNibNamed_owner_("DesktopServerWindow", self)
            self.statusProgress.startAnimation_(None)
            self.statusWindow.setTitle_("Desktop Sharing")
            self.statusItem.show(self)
        NotificationCenter().add_observer(self, sender=self.stream.handler)
        NotificationCenter().add_observer(self, sender=self.stream)
        self.changeStatus(STREAM_PROPOSING if is_update else STREAM_WAITING_DNS_LOOKUP)

    def end(self):
        if self.status in (STREAM_DISCONNECTING, STREAM_CANCELLING, STREAM_IDLE, STREAM_FAILED):
            if self.statusWindow:
                self.statusWindow.close()
                self.statusWindow = None
        elif self.status == STREAM_PROPOSING:
            self.sessionController.log_info("Cancelling desktop sharing session as per user request")
            self.sessionController.cancelProposal(self.stream)
            self.changeStatus(STREAM_CANCELLING)
        elif self.status in (STREAM_CONNECTED, STREAM_INCOMING):
            self.sessionController.log_info("Removing Desktop Stream from Session")                    
            self.sessionController.endStream(self)
            self.changeStatus(STREAM_DISCONNECTING)
        else:
            self.sessionController.log_info("Cancelling desktop sharing session as per user request")
            self.sessionController.end()
            self.changeStatus(STREAM_DISCONNECTING)

    def updateStatusIcon(self):
        pass
    
    def sessionStateChanged(self, newstate, detail):
        if newstate == STATE_DNS_FAILED:
            if self.statusWindow:
                self.statusLabel.setStringValue_("Error Establishing Session\nCould not find route to destination.")
                self.statusProgress.stopAnimation_(None)
            else:
                NSRunAlertPanel("Desktop Sharing", "Desktop sharing session could not be started:\n%s"%detail, "OK", None, None)
            self.changeStatus(STREAM_FAILED, detail)
        elif newstate == STATE_FAILED:
            if self.statusWindow:
                self.statusProgress.stopAnimation_(None)
            else:
                if detail and detail.lower() != "session cancelled" and not self.sessionController.hasStreamOfType("audio"):
                    NSRunAlertPanel("Desktop Sharing", "There was an error starting the desktop sharing session\n%s" % detail, "OK", "", "")
            self.changeStatus(STREAM_FAILED, detail)
        elif newstate == STATE_CONNECTED:
            # if the session is in connected state (ie, got SessionDidStart), we should have already
            # received MediaStreamDidStart or DidEnd to indicate whether we got accepted
            if self.status == STREAM_IDLE:
                if self.direction == "passive":
                    # we got rejected
                    self.statusLabel.setStringValue_("Desktop Sharing could not be started")
                    self.statusProgress.stopAnimation_(None)

    def changeStatus(self, newstate, fail_reason=None):
        if newstate == STREAM_CONNECTED:
            self.sessionController.log_info("Desktop stream started")
            if self.direction == "active":
                ip, port = self.stream.handler.address
                self.sessionController.log_info("Desktop sharing stream started, initiating external viewer on port %s" % port)
                url = NSURL.URLWithString_("vnc://localhost:%i" % (port))
                NSWorkspace.sharedWorkspace().openURL_(url)
            else:
                self.statusWindow.makeKeyAndOrderFront_(None)
                self.statusProgress.stopAnimation_(None)

        if self.direction == "passive":
            self.statusItem.update(self, newstate)
            if self.statusLabel:
                label = None
                if newstate == STREAM_CONNECTED:
                    label = "%s is seeing your desktop" % self.sessionController.getTitleShort()
                    self.statusProgress.stopAnimation_(None)
                elif newstate == STREAM_DISCONNECTING:
                    label = "Disconnecting..."
                elif newstate == STREAM_CANCELLING:
                    label = "Cancelling..."
                elif newstate == STREAM_CONNECTING:
                    label = "Waiting for connection..."
                elif newstate == STREAM_FAILED:
                    if self.sessionController.failureReason or fail_reason:
                        label = "Could not establish desktop sharing stream:\n%s" % (self.sessionController.failureReason or fail_reason)
                    else:
                        label = "Could not establish desktop sharing stream"
                    self.statusProgress.stopAnimation_(None)
                elif newstate == STREAM_IDLE:
                    if self.status in (STREAM_DISCONNECTING, STREAM_CONNECTED):
                        label = "Desktop Sharing Ended"
                        self.statusProgress.stopAnimation_(None)
                if label:
                    self.statusLabel.setStringValue_(label)

        if newstate == STREAM_IDLE:
            if self.direction == "passive":
                self.statusItem.remove(self)
            self.removeFromSession()
            # auto-close everything in 4s
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(DS_CLEANUP_DELAY, self, "closeWindows:", None, False)
            NotificationCenter().discard_observer(self, sender=self.stream.handler)
            NotificationCenter().discard_observer(self, sender=self.stream)
        self.status = newstate

    def closeWindows_(self, timer):
        if self.statusWindow:
            self.statusWindow.close()
            self.statusWindow = None

    @allocate_autorelease_pool
    @run_in_gui_thread
    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification.sender, notification.data)

    def _NH_MediaStreamDidStart(self, sender, data):
        self.changeStatus(STREAM_CONNECTED)

    def _NH_MediaStreamDidFail(self, sender, data):
        if data.failure.type == VNCConnectionError:
            self.changeStatus(STREAM_IDLE)
            self.sessionController.log_info("Desktop stream ended by closed VNC viewer")
        else:
            self.sessionController.log_info("Desktop stream failed: %s" % data.reason)
            data.failure.printTraceback()
            self.changeStatus(STREAM_FAILED)

    def _NH_MediaStreamDidEnd(self, sender, data):
        self.sessionController.log_info("Desktop stream ended")
        self.changeStatus(STREAM_IDLE)

    def _NH_DesktopSharingHandlerDidFail(self, sender, data):
        if data.failure.type == VNCConnectionError:
            self.sessionController.log_info("Desktop sharing: %s" % data.reason)
            # middleware is supposed to end the session now
        else:
            self.sessionController.log_info("Desktop sharing error: %s" % data.reason)


class DesktopSharingViewerController(DesktopSharingController):
    @classmethod
    def createStream(cls, account):        
        handler = ExternalVNCViewerHandler()
        return DesktopSharingStream(account, handler)


class DesktopSharingServerController(DesktopSharingController):
    @classmethod
    def createStream(cls, account):        
        handler = ExternalVNCServerHandler(("localhost", cls.vncServerPort))
        return DesktopSharingStream(account, handler)

