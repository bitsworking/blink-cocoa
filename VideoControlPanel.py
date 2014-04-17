# Copyright (C) 2014 AG Projects. See LICENSE for details.
#

from AppKit import NSApp, NSWindowController, NSEventTrackingRunLoopMode, NSTrackingMouseEnteredAndExited, NSTrackingActiveAlways, NSFloatingWindowLevel

from Foundation import NSBundle, NSImage, NSRunLoop, NSRunLoopCommonModes, NSTimer, NSView, NSTrackingArea, NSZeroRect


import objc
import time

from application.notification import IObserver, NotificationCenter
from application.python import Null
from zope.interface import implements

from util import allocate_autorelease_pool, run_in_gui_thread

from BlinkLogger import BlinkLogger
from MediaStream import STREAM_CONNECTED, STREAM_IDLE, STREAM_FAILED
from SIPManager import SIPManager

bundle = NSBundle.bundleWithPath_(objc.pathForFramework('ApplicationServices.framework'))
objc.loadBundleFunctions(bundle, globals(), [('CGEventSourceSecondsSinceLastEventType', 'diI')])

IDLE_TIME = 5
ALPHA = 1.0

class VideoControlPanel(NSWindowController):
    implements(IObserver)

    toolbar = objc.IBOutlet()
    toolbarView = objc.IBOutlet()
    visible = False
    full_screen = True
    holdButton = objc.IBOutlet()
    muteButton = objc.IBOutlet()
    fullscreenButton = objc.IBOutlet()
    idle_timer = None
    fade_timer = None
    is_idle = False
    closed = False
    show_time = None
    mouse_in_window = False

    def __new__(cls, *args, **kwargs):
        return cls.alloc().init()

    @run_in_gui_thread
    def __init__(self, videoWindowController):
        BlinkLogger().log_debug('Init %s' % self)
        self.videoWindowController = videoWindowController
        NSBundle.loadNibNamed_owner_("VideoControlPanel", self)
        self.window().setTitle_(self.videoWindowController.title)
        self.notification_center = NotificationCenter()
        self.notification_center.add_observer(self,sender=self.videoWindowController)
        self.notification_center.add_observer(self, name='BlinkMuteChangedState')

        #self.window().setMovable_(False)

    @allocate_autorelease_pool
    @run_in_gui_thread
    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification.sender, notification.data)

    def _NH_BlinkMuteChangedState(self, sender, data):
        self.updateMuteButton()

    def _NH_BlinkVideoWindowFullScreenChanged(self, sender, data):
        if sender.full_screen:
            self.fullscreenButton.setImage_(NSImage.imageNamed_("restore"))
        else:
            self.fullscreenButton.setImage_(NSImage.imageNamed_("fullscreen"))

    def mouseIn(self):
        self.mouse_in_window = True

    def mouseOut(self):
        self.mouse_in_window = False

    @property
    def streamController(self):
        return self.videoWindowController.streamController

    @property
    def sessionController(self):
        return self.videoWindowController.streamController.sessionController

    def startIdleTimer(self):
        if self.idle_timer is None:
            self.idle_timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(0.5, self, "updateIdleTimer:", None, True)
            NSRunLoop.currentRunLoop().addTimer_forMode_(self.idle_timer, NSRunLoopCommonModes)
            NSRunLoop.currentRunLoop().addTimer_forMode_(self.idle_timer, NSEventTrackingRunLoopMode)

    def stopIdleTimer(self):
        if self.idle_timer is not None and self.idle_timer.isValid():
            self.idle_timer.invalidate()
            self.idle_timer = None

    def startFadeTimer(self):
        self.visible = False
        if self.fade_timer is None:
            self.fade_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.05, self, "fade:", None, True)
            NSRunLoop.currentRunLoop().addTimer_forMode_(self.fade_timer, NSRunLoopCommonModes)
            NSRunLoop.currentRunLoop().addTimer_forMode_(self.fade_timer, NSEventTrackingRunLoopMode)

    def stopFadeTimer(self):
        if self.fade_timer is not None and self.fade_timer.isValid():
            self.fade_timer.invalidate()
            self.fade_timer = None

    @run_in_gui_thread
    def hide(self):
        self.stopIdleTimer()
        self.startFadeTimer()

    @run_in_gui_thread
    def show(self):
        if not self.videoWindowController:
            return

        if not self.videoWindowController.mouse_in_window:
            return

        if self.is_idle:
            return

        self.show_time = time.time()
        self.stopFadeTimer()
        self.startIdleTimer()
        self.window().setAlphaValue_(ALPHA)
        self.window().orderFront_(None)
        self.visible = True

    @run_in_gui_thread
    def close(self):
        BlinkLogger().log_debug('Close %s' % self)

        if self.closed:
            return
        self.closed = True

        self.notification_center.remove_observer(self, sender=self.videoWindowController)
        self.notification_center.remove_observer(self, name='BlinkMuteChangedState')

        self.stopIdleTimer()
        self.stopFadeTimer()
        if self.window():
            self.window().close()
        self.videoWindowController = None
        self.notification_center = None

    def dealloc(self):
        self.toolbarView.removeFromSuperview()
        BlinkLogger().log_debug('Dealloc %s' % self)
        super(VideoControlPanel, self).dealloc()

    def awakeFromNib(self):
        self.fullscreenButton.setImage_(NSImage.imageNamed_("restore" if self.videoWindowController.full_screen else "fullscreen"))
        self.updateMuteButton()
        audio_stream = self.sessionController.streamHandlerOfType("audio")
        if audio_stream:
            if audio_stream.status == STREAM_CONNECTED:
                if audio_stream.holdByLocal or audio_stream.holdByRemote:
                    self.holdButton.setImage_(NSImage.imageNamed_("paused-red"))
                else:
                    self.holdButton.setImage_(NSImage.imageNamed_("pause-white"))
            else:
                self.holdButton.setImage_(NSImage.imageNamed_("pause-white"))
        else:
            self.holdButton.setImage_(NSImage.imageNamed_("pause-white"))

    def updateMuteButton(self):
        self.muteButton.setImage_(NSImage.imageNamed_("muted" if SIPManager().is_muted() else "mute-white"))

    def windowDidMove_(self, notification):
        self.stopFadeTimer()
        self.window().setAlphaValue_(ALPHA)
        self.visible = True

    def windowDidBecomeKey_(self, notification):
        self.stopFadeTimer()
        self.window().setAlphaValue_(ALPHA)
        self.visible = True
        if self.videoWindowController and self.videoWindowController.window is not None:
            self.videoWindowController.window.orderFront_(None)

    def windowDidBecomeMain_(self, notification):
        self.stopFadeTimer()
        self.window().setAlphaValue_(ALPHA)
        self.visible = True

    def windowWillClose_(self, sender):
        self.stopFadeTimer()

    def updateIdleTimer_(self, timer):
        if not self.window():
            return
        last_idle_counter = CGEventSourceSecondsSinceLastEventType(1, int(4294967295))
        chat_stream = self.sessionController.streamHandlerOfType("chat")
        if not chat_stream:
            if self.show_time is not None and time.time() - self.show_time < IDLE_TIME:
                return

        if last_idle_counter > IDLE_TIME:
            self.show_time = None
            if not self.is_idle:
                if self.visible:
                    self.startFadeTimer()
                self.is_idle = True
        else:
            if not self.visible:
                self.stopFadeTimer()
                if self.window():
                    self.window().setAlphaValue_(ALPHA)
                    self.window().orderFront_(None)
                    self.visible = True
            self.is_idle = False

    def fade_(self, timer):
        if self.window():
            if self.window().alphaValue() > 0.0:
                self.window().setAlphaValue_(self.window().alphaValue() - 0.025)
            else:
                self.stopFadeTimer()
                self.window().orderOut_(None)

    @objc.IBAction
    def userClickedToolbarButton_(self, sender):
        if not self.videoWindowController:
            return

        if self.videoWindowController.full_screen_in_progress:
            return

        self.stopFadeTimer()
        self.streamController.stopInitialTimer()

        if sender.itemIdentifier() == 'hangup':
            self.stopIdleTimer()
            self.sessionController.end()
            self.hide()
        elif sender.itemIdentifier() == 'fullscreen':
            self.window().orderOut_(None)
            self.videoWindowController.toggleFullScreen()
        elif sender.itemIdentifier() == 'aspect':
            self.videoWindowController.changeAspectRatio()
        elif sender.itemIdentifier() == 'chat':
            if self.videoWindowController.always_on_top:
                self.videoWindowController.toogleAlwaysOnTop()
            chat_stream = self.sessionController.streamHandlerOfType("chat")
            if chat_stream:
                if chat_stream.status in (STREAM_IDLE, STREAM_FAILED):
                    self.sessionController.startChatSession()

            else:
                self.sessionController.addChatToSession()

            if self.videoWindowController.full_screen:
                NSApp.delegate().contactsWindowController.showChatWindow_(None)
                self.videoWindowController.goToWindowMode(NSApp.delegate().contactsWindowController.chatWindowController.window())

        elif sender.itemIdentifier() == 'info':
            if self.sessionController.info_panel is not None:
                self.sessionController.info_panel.toggle()
        elif sender.itemIdentifier() == 'mirror':
            NSApp.delegate().contactsWindowController.toggleLocalVideoWindow_(sender)
        elif sender.itemIdentifier() == 'mute':
            SIPManager().mute(not SIPManager().is_muted())
            self.muteButton.setImage_(NSImage.imageNamed_("muted" if SIPManager().is_muted() else "mute-white"))
        elif sender.itemIdentifier() == 'contacts':
            if self.videoWindowController.full_screen:
                self.videoWindowController.toggleFullScreen()
            NSApp.delegate().contactsWindowController.focusSearchTextField()
        elif sender.itemIdentifier() == 'hold':
            if self.sessionController.hasStreamOfType("audio"):
                audio_stream = self.sessionController.streamHandlerOfType("audio")
                if audio_stream and audio_stream.status == STREAM_CONNECTED and not self.sessionController.inProposal:
                    if audio_stream.holdByLocal:
                        audio_stream.unhold()
                        audio_stream.view.setSelected_(True)
                        sender.setImage_(NSImage.imageNamed_("pause-white"))
                    else:
                        sender.setImage_(NSImage.imageNamed_("paused-red"))
                        audio_stream.hold()


class controlPanelToolbarView(NSView):
    parentWindow = objc.IBOutlet()
    tarea = None

    def awakeFromNib(self):
        rect = NSZeroRect
        rect.size = self.frame().size
        self.tarea = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(rect,
                                                                            NSTrackingMouseEnteredAndExited|NSTrackingActiveAlways, self, None)
        self.addTrackingArea_(self.tarea)

    def mouseEntered_(self, event):
        self.parentWindow.delegate().mouseIn()

    def mouseExited_(self, event):
        self.parentWindow.delegate().mouseOut()

    def dealloc(self):
        self.removeTrackingArea_(self.tarea)
        self.tarea = None
        BlinkLogger().log_debug('Dealloc %s' % self)
        super(controlPanelToolbarView, self).dealloc()
