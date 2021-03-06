# Copyright (C) 2009-2011 AG Projects. See LICENSE for details.
#

from AppKit import (NSApp,
                    NSInformationalRequest,
                    NSOKButton)
from Foundation import (NSBundle,
                        NSHeight,
                        NSLocalizedString,
                        NSMakeRect,
                        NSObject,
                        NSOpenPanel,
                        NSURL)
import objc

import unicodedata

from application.notification import NotificationCenter, IObserver
from application.python import Null
from sipsimple.threading.green import run_in_green_thread
from zope.interface import implements

import ListView
from BlinkLogger import BlinkLogger
from HistoryManager import FileTransferHistory
from FileTransferItemView import FileTransferItemView
from FileTransferSession import IncomingFileTransferHandler, OutgoingPushFileTransferHandler, OutgoingPullFileTransferHandler
from util import allocate_autorelease_pool, run_in_gui_thread, format_size


def openFileTransferSelectionDialog(account, dest_uri, filename=None):
    if not NSApp.delegate().contactsWindowController.sessionControllersManager.isMediaTypeSupported('file-transfer'):
        return

    panel = NSOpenPanel.openPanel()
    panel.setTitle_(NSLocalizedString("Select Files or Folders and Click Open to Send", "Window title"))
    panel.setDirectoryURL_(NSURL.URLWithString_(filename))

    panel.setAllowsMultipleSelection_(True)
    panel.setCanChooseDirectories_(True)

    if panel.runModal() != NSOKButton:
        return
    filenames = [unicodedata.normalize('NFC', file) for file in panel.filenames()]
    NSApp.delegate().contactsWindowController.sessionControllersManager.send_files_to_contact(account, dest_uri, filenames)


class FileTransferWindowController(NSObject):
    implements(IObserver)

    window = objc.IBOutlet()
    listView = objc.IBOutlet()
    bottomLabel = objc.IBOutlet()
    transferSpeed = objc.IBOutlet()
    history = []
    loaded = False

    def __new__(cls, *args, **kwargs):
        return cls.alloc().init()

    def __init__(self):
        if self:
            NotificationCenter().add_observer(self, name="BlinkFileTransferInitializing")
            NotificationCenter().add_observer(self, name="BlinkFileTransferRestarting")
            NotificationCenter().add_observer(self, name="BlinkFileTransferDidFail")
            NotificationCenter().add_observer(self, name="BlinkFileTransferDidEnd")
            NotificationCenter().add_observer(self, name="BlinkFileTransferSpeedDidUpdate")
            NotificationCenter().add_observer(self, name="BlinkShouldTerminate")

            NSBundle.loadNibNamed_owner_("FileTransferWindow", self)

            self.transferSpeed.setStringValue_('')
            self.load_transfers_from_history()

    @run_in_green_thread
    @allocate_autorelease_pool
    def get_previous_transfers(self, active_items=[]):
        results = FileTransferHistory().get_transfers(10)
        transfers = [transfer for transfer in reversed(results) if transfer.transfer_id not in active_items]
        self.render_previous_transfers(transfers)

    @run_in_gui_thread
    def render_previous_transfers(self, transfers):
        last_displayed_item = self.listView.subviews().lastObject()

        for transfer in transfers:
            item = FileTransferItemView.alloc().initWithFrame_oldTransfer_(NSMakeRect(0, 0, 100, 100), transfer)
            if last_displayed_item:
                self.listView.insertItemView_before_(item, last_displayed_item)
            else:
                self.listView.addItemView_(item)

            self.listView.relayout()
            self.listView.display()
            h = self.listView.minimumHeight()
            self.listView.scrollRectToVisible_(NSMakeRect(0, h-1, 100, 1))

        count = len(self.listView.subviews())
        if count == 1:
            self.bottomLabel.setStringValue_(NSLocalizedString("1 item", "Label"))
        else:
            self.bottomLabel.setStringValue_(NSLocalizedString("%i items", "Label") % count if count else u"")

        self.loaded = True

    def load_transfers_from_history(self):
        active_items = []
        for item in self.listView.subviews().copy():
            if item.done:
                item.removeFromSuperview()
            else:
                if item.transfer:
                    active_items.append(item.transfer.transfer_id)

        self.listView.relayout()
        self.listView.display()
        self.listView.setNeedsDisplay_(True)

        self.get_previous_transfers(active_items)

    def refresh_transfer_rate(self):
        incoming_transfer_rate = 0
        outgoing_transfer_rate = 0
        for item in self.listView.subviews().copy():
            if item.transfer and item.transfer.transfer_rate is not None:
                if isinstance(item.transfer, IncomingFileTransferHandler):
                    incoming_transfer_rate += item.transfer.transfer_rate
                elif isinstance(item.transfer, OutgoingPushFileTransferHandler):
                    outgoing_transfer_rate += item.transfer.transfer_rate
                elif isinstance(item.transfer, OutgoingPullFileTransferHandler):
                    incoming_transfer_rate += item.transfer.transfer_rate

        if incoming_transfer_rate or outgoing_transfer_rate:
            if incoming_transfer_rate and outgoing_transfer_rate:
                f1 = format_size(incoming_transfer_rate, bits=True)
                f2 = format_size(outgoing_transfer_rate, bits=True)
                text = NSLocalizedString("Incoming %s/s", "Label") % f1 + ", " + NSLocalizedString("Outgoing %s/s", "Label") % f2
            elif incoming_transfer_rate:
                f = format_size(incoming_transfer_rate, bits=True)
                text = NSLocalizedString("Incoming %s/s", "Label") % f
            elif outgoing_transfer_rate:
                f = format_size(outgoing_transfer_rate, bits=True)
                text = NSLocalizedString("Outgoing %s/s", "Label") % f
            self.transferSpeed.setStringValue_(text)
        else:
            self.transferSpeed.setStringValue_('')

    @allocate_autorelease_pool
    @run_in_gui_thread
    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification.sender, notification.data)

    @objc.IBAction
    def close_(self, sender):
        self.window.close()

    def _NH_BlinkShouldTerminate(self, sender, data):
        if self.window:
            self.window.orderOut_(self)

    @objc.IBAction
    def showWindow_(self, sender):
        if NSApp.delegate().contactsWindowController.sessionControllersManager.isMediaTypeSupported('file-transfer'):
            self.window.makeKeyAndOrderFront_(None)

    @run_in_green_thread
    def delete_history_transfers(self):
        FileTransferHistory().delete_transfers()

    @objc.IBAction
    def clearList_(self, sender):
        self.delete_history_transfers()
        self.load_transfers_from_history()

    def _NH_BlinkFileTransferRestarting(self, sender, data):
        self.listView.relayout()

    def _NH_BlinkFileTransferInitializing(self, sender, data):
        item = FileTransferItemView.alloc().initWithFrame_transfer_(NSMakeRect(0, 0, 100, 100), sender)

        self.listView.addItemView_(item)
        h = NSHeight(self.listView.frame())
        self.listView.scrollRectToVisible_(NSMakeRect(0, h-1, 100, 1))

        if 'xscreencapture' not in sender.file_path:
            self.window.orderFront_(None)

        count = len(self.listView.subviews())
        if count == 1:
            self.bottomLabel.setStringValue_(NSLocalizedString("1 item", "Label"))
        else:
            self.bottomLabel.setStringValue_(NSLocalizedString("%i items", "Label") % count)

    def _NH_BlinkFileTransferDidFail(self, sender, data):
        self.listView.relayout()
        self.refresh_transfer_rate()

    def _NH_BlinkFileTransferSpeedDidUpdate(self, sender, data):
        self.refresh_transfer_rate()

    def _NH_BlinkFileTransferDidEnd(self, sender, data):
        self.listView.relayout()
        self.refresh_transfer_rate()
        # jump dock icon and bring window to front
        if isinstance(sender, IncomingFileTransferHandler):
            self.window.orderFront_(None)
            NSApp.requestUserAttention_(NSInformationalRequest)
        elif 'xscreencapture' not in sender.file_path:
            self.window.orderFront_(None)
            NSApp.requestUserAttention_(NSInformationalRequest)


