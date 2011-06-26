# Copyright (C) 2010 AG Projects. See LICENSE for details.
#

"""
Blink settings extensions.
"""

__all__ = ['SIPSimpleSettingsExtension']

import os

from sipsimple.configuration import Setting, SettingsGroup, SettingsObjectExtension
from sipsimple.configuration.datatypes import NonNegativeInteger, SampleRate
from sipsimple.configuration.settings import AudioSettings, ChatSettings, DesktopSharingSettings, FileTransferSettings, LogsSettings, TLSSettings

from configuration.datatypes import AnsweringMachineSoundFile, HTTPURL, SoundFile, UserDataPath


class AnsweringMachineSettings(SettingsGroup):
    enabled = Setting(type=bool, default=False)
    answer_delay = Setting(type=NonNegativeInteger, default=10)
    max_recording_duration = Setting(type=NonNegativeInteger, default=120)
    unavailable_message = Setting(type=AnsweringMachineSoundFile, default=AnsweringMachineSoundFile(AnsweringMachineSoundFile.DefaultSoundFile('unavailable_message.wav')), nillable=True)


class AudioSettingsExtension(AudioSettings):
    directory = Setting(type=UserDataPath, default=UserDataPath('history'))
    alert_device = Setting(type=unicode, default=u'system_default', nillable=True)
    input_device = Setting(type=unicode, default=u'system_default', nillable=True)
    output_device = Setting(type=unicode, default=u'system_default', nillable=True)
    sample_rate = Setting(type=SampleRate, default=44100)
    automatic_device_switch = Setting(type=bool, default=True)


class ChatSettingsExtension(ChatSettings):
    auto_accept = Setting(type=bool, default=False)
    sms_replication = Setting(type=bool, default=True)
    disabled = Setting(type=bool, default=False)


class DesktopSharingSettingsExtension(DesktopSharingSettings):
    disabled = Setting(type=bool, default=False)
    vnc_client_encryption_warning = Setting(type=bool, default=False)


class FileTransferSettingsExtension(FileTransferSettings):
    disabled = Setting(type=bool, default=False)
    directory = Setting(type=UserDataPath, default=UserDataPath(os.path.expanduser('~/Downloads')))
    auto_accept = Setting(type=bool, default=False)
    render_incoming_video_in_chat_window = Setting(type=bool, default=True)
    render_incoming_image_in_chat_window = Setting(type=bool, default=True)


class LogsSettingsExtension(LogsSettings):
    directory = Setting(type=UserDataPath, default=UserDataPath('logs'))
    trace_sip = Setting(type=bool, default=False)
    trace_pjsip = Setting(type=bool, default=False)
    trace_msrp = Setting(type=bool, default=False)
    trace_xcap = Setting(type=bool, default=False)
    trace_notifications = Setting(type=bool, default=False)


class ServerSettings(SettingsGroup):
    alert_url = Setting(type=HTTPURL, default=None, nillable=True)
    enrollment_url = Setting(type=HTTPURL, default="https://blink.sipthor.net/enrollment.phtml")
    # Collaboration editor taken from http://code.google.com/p/google-mobwrite/
    collaboration_url = Setting(type=HTTPURL, default='http://mobwrite3.appspot.com/scripts/q.py', nillable=True)


class ServiceProviderSettings(SettingsGroup):
    name = Setting(type=str, default=None, nillable=True)
    about_url = Setting(type=HTTPURL, default=None, nillable=True)
    help_url = Setting(type=HTTPURL, default=None, nillable=True)


class SoundsSettings(SettingsGroup):
    audio_inbound = Setting(type=SoundFile, default=SoundFile("ring_inbound.wav"), nillable=True)
    audio_outbound = Setting(type=SoundFile, default=SoundFile("ring_outbound.wav"), nillable=True)
    file_received = Setting(type=SoundFile, default=SoundFile("file_received.wav", volume=20), nillable=True)
    file_sent = Setting(type=SoundFile, default=SoundFile("file_sent.wav", volume=20), nillable=True)
    message_received = Setting(type=SoundFile, default=SoundFile("message_received.wav", volume=10), nillable=True)
    message_sent = Setting(type=SoundFile, default=SoundFile("message_sent.wav", volume=10), nillable=True)


class TLSSettingsExtension(TLSSettings):
    ca_list = Setting(type=UserDataPath, default=None, nillable=True)


class ContactsSettings(SettingsGroup):
    enable_address_book = Setting(type=bool, default=True)


class SIPSimpleSettingsExtension(SettingsObjectExtension):
    answering_machine = AnsweringMachineSettings
    audio = AudioSettingsExtension
    chat = ChatSettingsExtension
    desktop_sharing = DesktopSharingSettingsExtension
    file_transfer = FileTransferSettingsExtension
    logs = LogsSettingsExtension
    server = ServerSettings
    service_provider = ServiceProviderSettings
    sounds = SoundsSettings
    tls = TLSSettingsExtension
    contacts = ContactsSettings


