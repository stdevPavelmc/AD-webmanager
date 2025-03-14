# -*- coding: utf-8 -*-

# Copyright (C) 2012-2015 Stéphane Graber
# Author: Stéphane Graber <stgraber@ubuntu.com>

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You can find the license on Debian systems in the file
# /usr/share/common-licenses/GPL-2

from collections import UserList
from flask import request, Response, g, session, abort
from functools import wraps
import ldap
from ldap import modlist
import struct
import uuid
from settings import Settings

LDAP_SCOPES = {"base": ldap.SCOPE_BASE,
               "onelevel": ldap.SCOPE_ONELEVEL,
               "subtree": ldap.SCOPE_SUBTREE}

LDAP_AD_GROUPTYPE_VALUES = {1: ('System', False),
                            2: ('Global', True),
                            4: ('Local Domain', True),
                            8: ('Universal', True),
                            16: ('APP_BASIC', False),
                            32: ('APP_QUERY', False)}

#!!! This need editing for open-source release
LDAP_AD_USERACCOUNTCONTROL_VALUES = {2: (u"Deactivated", True),
                                     64: (u"User can't change password", False),
                                     512: ("Normal Account", False),
                                     4096: ("PC Trusted Account", False),
                                     8192: ("Server Trusted Account", False),
                                     65536: (u"Password does not expire", True),
                                     8388608: (u"Password expired", False)
                                     }

LDAP_AD_BOOL_ATTRIBUTES = ['showInAdvancedViewOnly']
LDAP_AD_GUID_ATTRIBUTES = ['objectGUID']
LDAP_AD_MULTIVALUE_ATTRIBUTES = ['member', 'memberOf', 'objectClass', 'repsTo', 'macAddress', 'dnsRecord', 'ipsecOwnersReference',
                                 'servicePrincipalName', 'sshPublicKey', 'managedObjects', 'directReports', 'wellKnownObjects',
                                 'proxyAddresses', 'otherMailbox', 'dsCorePropagationData',
                                 'msSFU30SearchAttributes', 'msSFU30ResultAttributes', 'msSFU30KeyAttributes',
                                 'ipsecNFAReference', 'dNSProperty', 'otherHomePhone', 'otherMobile', 'otherTelephone']
LDAP_AD_SID_ATTRIBUTES = ['objectSid']
LDAP_AD_UINT_ATTRIBUTES = ['userAccountControl', 'groupType']
LDAP_AD_Object_ATTRIBUTES = ['jpegPhoto', 'ipsecData', 'dnsRecord']


def ldap_change_password(old_password, new_password, username=None):
    """
        Change the password of the user.
    """

    if 'connection' not in g.ldap:
        return False

    connection = g.ldap['connection']

    user = ldap_get_user(username)
    if not user:
        return False

    old_password_u16 = ('"%s"' % old_password).encode("utf-16-le")
    new_password_u16 = ('"%s"' % new_password).encode("utf-16-le")

    if old_password:
        # User password change
        attributes = [(ldap.MOD_DELETE, 'unicodePwd', old_password_u16),
                      (ldap.MOD_ADD, 'unicodePwd', new_password_u16)]
    else:
        # Admin password change
        attributes = [(ldap.MOD_REPLACE, 'unicodePwd', new_password_u16),
                      (ldap.MOD_REPLACE, 'unicodePwd', new_password_u16)]
    connection.modify_s(user['distinguishedName'], attributes)


def ldap_create_entry(dn, attributes):
    """
        Create a new entry and set the attributes passed as parameter.
    """

    if 'connection' not in g.ldap:
        return False

    connection = g.ldap['connection']
    #dn = dn.encode('utf-8')

    connection.add_s(dn, modlist.addModlist(attributes))

    return True


def ldap_delete_entry(dn):
    """
        Delete an entry as identified by its distinguishedName.
    """

    if 'connection' not in g.ldap:
        return False

    connection = g.ldap['connection']
    connection.delete_s(dn)

    return True


def ldap_get_user(username=None, key="sAMAccountName"):
    """
        Return the attributes for the user or None if it doesn't exist.
    """

    if not username:
        username = g.ldap['username']

    return ldap_get_entry_simple({'objectClass': 'user',
                                  key: username})


def ldap_get_group(groupname, key="sAMAccountName"):
    """
        Return the attributes for the group or None if it doesn't exist.
    """

    return ldap_get_entry_simple({'objectClass': 'group',
                                  key: groupname})


def ldap_get_ou(ou_name, key="distinguishedName"):
    """
        Return the attributes for the ou or None if it doesn't exist.
    """

    return ldap_get_entry_simple({'objectClass': 'organizationalUnit',
                                  key: ou_name})


def ldap_get_entry_simple(filter_dict):
    """
        Return the attributes for the entry matching the filter.
        The filter is a key/value dictionary.
        The entry that matches all the values will be returned.
    """

    if not filter_dict or not isinstance(filter_dict, dict):
        return False

    for entry in g.ldap_cache.values():
        for key, value in filter_dict.items():
            if key not in entry:
                break

            if isinstance(entry[key], list):
                if value not in entry[key]:
                    break
                continue

            if entry[key] != value:
                break
        else:
            # We've got a match!
            return entry

    ldap_filter = ""
    if len(filter_dict) == 1:
        ldap_filter = "%s=%s" % list(filter_dict.items())[0]
    else:
        fields = ""
        for key, value in filter_dict.items():
            fields += "(%s=%s)" % (key, value)
        ldap_filter = "(&%s)" % fields
    return ldap_get_entry(ldap_filter)


def ldap_get_entry(ldap_filter):
    """
        Return the attributes for a single entry or None if it doesn't exist or
        if the filter matches multiple entries and False on errors.
    """
    entries = ldap_get_entries(ldap_filter)
    # Only allow a single entry
    if isinstance(entries, list) and len(entries) == 1:
        return entries[0]

    return None


def ldap_get_entries(ldap_filter, base=None, scope=None, attrlist=None, ignore_erros=False):
    """
        Return the attributes for an entry or None if it doesn't exist and
        False on errors.
    """
    if 'connection' not in g.ldap:
        return False

    if not base:
        base = g.ldap['dn']

    if scope:
        if scope in LDAP_SCOPES:
            scope = LDAP_SCOPES[scope]
        else:
            return False
    else:
        scope = ldap.SCOPE_SUBTREE

    connection = g.ldap['connection']

    # Grab the LDAP entry
    result = connection.search_s(base, scope, ldap_filter, attrlist)
    # Check that we at least have something
    if not result or not result[0] or not result[0][0]:
        return []

    entries = []
    for entry in result:
        # Simplify the list by only keeping the attributes we known can contain
        # multiple values as list and decode everything to unicode.

        if entry[0] == None:
            continue
        attributes = {}
        for key, value in entry[1].items():
            attributes[key] = _ldap_decode_attribute(key, value)

        # Expand some attributes
        if 'primaryGroupID' in attributes:
            # Retrieve primary group for user
            group = ldap_get_group('%s-%s' %
                                   (g.ldap['domain_sid'], attributes['primaryGroupID']), 'objectSid')
            attributes['__primaryGroup'] = group['distinguishedName']

        # Cache or refresh the entry
        if attrlist:
            g.ldap_cache[attributes[attrlist[0]]] = attributes
        else:
            g.ldap_cache[attributes['objectGUID']] = attributes
        entries.append(attributes)
    return entries


def ldap_obj_has_children(base):
    scope = 'onelevel'
    filter = None
    attrlist = ['distinguishedName']
    result = ldap_get_entries(filter, base, scope, attrlist)
    if len(result):
        return True
    return False


def ldap_get_all_users(filter=None, attrset=None):
    base = g.ldap['search_dn']
    scope = 'subtree'
    attrlist = attrset  # set to None if need to get all
    if not filter:
        filter = '(objectClass=organizationalPerson)'
    else:
        filter = f'(&(objectClass=organizationalPerson)({filter}))'
    user_list = ldap_get_entries(filter, base, scope, attrlist)
    return user_list


def ldap_get_members(name=None):
    """
        Return the list of all groups the entry is a memberOf.
    """

    entry = ldap_get_group(name)
    if not entry:
        return None

    members = []
    # Start with all the simple members
    if 'member' in entry:
        members += entry['member']

    # Add all the members that have the group as primaryGroup
    members += [member['distinguishedName']
                for member in ldap_get_entries(
                    "primaryGroupID=%s" % entry['objectSid'].split("-")[-1])]

    return members


def ldap_get_membership(name=None):
    """
        Return the list of all groups the entry is a memberOf.
    """
    entry = ldap_get_entry_simple({'sAMAccountName': name})
    if not entry:
        return None

    groups = []

    # Always start by the primary group if present
    if '__primaryGroup' in entry:
        groups.append(entry['__primaryGroup'])

    # Retrieve secondary groups for user
    if 'memberOf' in entry:
        groups += entry['memberOf']
    return groups


def ldap_in_group(groupname, username=None):
    """
        Checks whether a user is a member of a given group name.
    """

    if not username:
        username = g.ldap['username']

    group = ldap_get_group(groupname)
    groups = ldap_get_membership(username)

    if group is None:
        return False
    # Start by looking at direct membership
    if group['distinguishedName'] in groups:
        return True

    # Recurse through all the groups
    to_check = set(groups)
    checked = set()

    while to_check != checked:
        for entry in to_check - checked:
            attr = ldap_get_group(entry, "distinguishedName")
            if attr is None:
                return None
            if 'memberOf' in attr:
                if group['distinguishedName'] in attr['memberOf']:
                    return True
                to_check.update(attr['memberOf'])
            checked.add(entry)

    return group['distinguishedName'] in checked


def ldap_update_attribute(dn, attribute, value=None, new_parent=None, objectClass=None):
    """
        Set/Update a given attribute.
    """

    if 'connection' not in g.ldap:
        return False

    connection = g.ldap['connection']
    current_entry = ldap_get_entry_simple({'distinguishedName': dn})
    #old_value = current_entry[attribute]
    mod_attrs = []

    if not current_entry:
        raise Exception(dn)

    if (attribute == 'distinguishedName'):
        connection.rename_s(dn, value, new_parent)

    # if objectClass and objectClass not in current_entry['objectClass']:
    #     # It's add a new class to the object,  its not an attribute update
    #     new_class_list = current_entry['objectClass'].append(objectClass)
    #     old = {'objectClass': current_entry['objectClass']}
    #     new = {'objectClass': new_class_list}
    #     ldif = modlist.modifyModlist(old, new)
    #     connection.modify_s(dn, ldif)

    elif isinstance(value, list):
        # Flush all entries and re-add everything
        new_values = []

        if len(value) > 0:
            for i in value:
                a = i.encode('utf-8')
                new_values.append(a)
            mod_attrs.append((ldap.MOD_REPLACE, attribute, new_values))
        else:
            mod_attrs.append((ldap.MOD_DELETE, attribute, None))

    elif not value and attribute in current_entry:
        # Erase attribute
        mod_attrs.append((ldap.MOD_DELETE, attribute, None))
    elif attribute in current_entry:
        # Update an attribute
        if attribute == 'jpegPhoto':
            mod_attrs.append((ldap.MOD_REPLACE, attribute, value))
        else:
            mod_attrs.append(
                (ldap.MOD_REPLACE, attribute, value.encode('utf-8')))
    elif value:
        # add a new attribute
        if attribute == 'jpegPhoto':
            mod_attrs.append((ldap.MOD_ADD, attribute, [value]))
        else:
            mod_attrs.append(
                (ldap.MOD_ADD, attribute, [value.encode('utf-8')]))

    if len(mod_attrs) != 0:
        connection.modify_s(dn, mod_attrs)


def ldap_update_attribute_old(dn, attribute, value, objectclass=None):
    """
        Set/Update a given attribute.
    """

    if 'connection' not in g.ldap:
        return False

    connection = g.ldap['connection']
    attribute = [attribute.encode('utf-8')]
    current_entry = ldap_get_entry_simple({'distinguishedName': dn})

    if not current_entry:
        raise Exception(dn)

    changes = []

    if dn.lower().startswith("%s=" % attribute.lower()):
        # It's a rename, not an attribute update
        connection.rename_s(dn, "%s=%s" % (attribute, value))
        return True

    if objectclass and objectclass not in current_entry['objectClass']:
        connection.modify_s(dn, [(ldap.MOD_ADD, "objectClass", objectclass)])

    if isinstance(value, list):
        # Flush all entries and re-add everything
        if attribute in current_entry:
            changes.append((ldap.MOD_DELETE, attribute, None))

        for entry in value:
            if entry:
                changes.append((ldap.MOD_ADD, attribute, entry))
    elif not value and attribute in current_entry:
        # Drop current attribute
        changes.append((ldap.MOD_DELETE, attribute, None))
    elif attribute in current_entry:
        # Update current attribute
        changes.append((ldap.MOD_REPLACE, attribute, value))
    elif value:
        # Add the attribute
        changes.append((ldap.MOD_ADD, attribute, value))

    if not changes:
        return True
    connection.modify_s(dn, changes)
    return True


def ldap_add_users_to_group(dn, attribute, value):

    if 'connection' not in g.ldap:
        return False

    connection = g.ldap['connection']
    mod_attrs = []
    new_values = []

    for i in value:
        a = i.encode('utf-8')
        new_values.append(a)

    mod_attrs.append((ldap.MOD_REPLACE, attribute, new_values))
    if len(mod_attrs) != 0:
        connection.modify_s(dn, mod_attrs)


def ldap_user_exists(username=None):
    """
        Return True if the user exists. False otherwise.
    """

    if ldap_get_user(username):
        return True

    return False


def ldap_group_exists(groupname=None):
    """
        Return True if the group exists. False otherwise.
    """

    if ldap_get_group(groupname):
        return True

    return False


def ldap_ou_exists(ou_name=None):
    """
        Return True if the OU exists. False otherwise.
    """

    if ldap_get_ou(ou_name):
        return True

    return False


# Private
def _ldap_authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response('Could not verify your access level for that URL.\n'
                    'You have to login with proper credentials', 401,
                    {'WWW-Authenticate': 'Basic realm="Login Required"'})


def _ldap_connect(username, password):
    # Already connected
    if 'connection' in g.ldap:
        return True

    ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_NEVER)
    ldap.set_option(ldap.OPT_REFERRALS, 0)
    ldap.set_option(ldap.OPT_PROTOCOL_VERSION, 3)

    if isinstance(g.ldap['server'], list):
        servers = g.ldap['server']
    else:
        servers = [g.ldap['server']]

    for server in servers:
        connection = ldap.initialize("ldaps://%s:636" % server)
        try:
            connection.simple_bind_s("%s@%s" % (username, g.ldap['domain']),
                                     password)

            g.ldap['connection'] = connection
            g.ldap['server'] = server
            g.ldap['username'] = username

            # Get domain SID
            # Can't go through ldap_get_entry as it requires domain_sid be set.
            result = connection.search_s(g.ldap['dn'], ldap.SCOPE_BASE)
            g.ldap['domain_sid'] = _ldap_decode_attribute(
                "objectSid", result[0][1]['objectSid'])

            return True
        except ldap.INVALID_CREDENTIALS:
            return False
        # except:
        #   continue

    #raise Exception("No server reachable at this point.")


def _ldap_sid2str(sid):
    version = struct.unpack('B', sid[0:1])[0]
    assert version == 1, version
    lenght = struct.unpack('B', sid[1:2])[0]
    authority = struct.unpack(b'>Q', b'\x00\x00' + sid[2:8])[0]
    string = 'S-%d-%d' % (version, authority)
    sid = sid[8:]
    assert len(sid) == 4 * lenght
    for i in range(lenght):
        value = struct.unpack('<L', sid[4*i:4*(i+1)])[0]
        string += '-%d' % value
    return string


def _ldap_decode_attribute(key, value):
    # Handle multi-value attributes
    if key in LDAP_AD_MULTIVALUE_ATTRIBUTES and isinstance(value, list):
        return [_ldap_decode_attribute(key, entry) for entry in value]

    if isinstance(value, list):
        if len(value) > 1:
            #raise Exception("Unknown multiple value field: %s" % key)
            print("Unknown multiple value field: %s" % key)
            return value
        else:
            value = value[0]

    # Decode SID values
    if key in LDAP_AD_SID_ATTRIBUTES:
        return _ldap_sid2str(value)
    # Decode GUIID values
    if key in LDAP_AD_GUID_ATTRIBUTES:
        return str(uuid.UUID(bytes_le=value))

    # Decode Unsigned Integer values
    if key in LDAP_AD_UINT_ATTRIBUTES:
        return struct.unpack("I", struct.pack("i", int(value)))[0]

    # Decode boolean values
    if key in LDAP_AD_BOOL_ATTRIBUTES:
        return value == "TRUE"

    # Do nothing to binary object files
    if key in LDAP_AD_Object_ATTRIBUTES:
        return value

    # Decode the rest to unicode
    try:
        return value.decode('utf-8')
    except:
        #raise Exception("Unknown type: %s" % key)
        print("Unknown multiple value field: %s" % key)
        return value


# Decorators
def ldap_auth(group=None):
    def _my_decorator(view_func):
        def _decorator(*args, **kwargs):

            auth = request.authorization

            if 'logout' in session:
                session.pop('logout')
                return _ldap_authenticate()

            if not auth or not _ldap_connect(auth.username, auth.password):
                return _ldap_authenticate()
            if group and not ldap_in_group(group):
                return _ldap_authenticate()

            return view_func(*args, **kwargs)
        return wraps(view_func)(_decorator)
    return _my_decorator


def tryFunc():
    """
    docstring
    """
    pass


def move(dn, attribute, value):
    connection = g.ldap['connection']
    attribute = [attribute.encode('utf-8')]
    connection.rename_s(dn, "%s=%s" % (attribute, value))
