# Copyright (C) 2011 AG Projects. See LICENSE for details.
#

"""
Blink contact extensions
"""

__all__ = ['BlinkContactExtension', 'BlinkContactGroupExtension']

from application.configuration.datatypes import Boolean

from sipsimple.addressbook import ContactURI, ContactExtension, GroupExtension, SharedSetting
from sipsimple.configuration import Setting

SharedSetting.set_namespace('ag-projects:blink')


class BlinkContactExtension(ContactExtension):
    default_uri = SharedSetting(type=str, default=None, nillable=True)
    preferred_media = SharedSetting(type=str, default='audio')
    icon = SharedSetting(type=str, default=None, nillable=True)
    presence_policy = Setting(type=str, default=None, nillable=True)
    dialog_policy = Setting(type=str, default=None, nillable=True)
    auto_answer = SharedSetting(type=Boolean, default=False)


class BlinkGroupExtension(GroupExtension):
    position = Setting(type=int, nillable=True)
    expanded = Setting(type=bool, default=True)


