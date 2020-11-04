#!/bin/python3

# -*- coding: utf-8 -*-
# Copyright 2019, 2020 Awesome Technologies Innovationslabor GmbH
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function
import logging
import os
import sys
import yaml
import zipfile
import requests
import json
import getpass
import string
import secrets
import time
from emoji import emojize
import slackdown
import re
from files import process_attachments, process_files
from utils import send_event, print


channelTypes = ["dms.json", "groups.json", "mpims.json", "channels.json", "users.json"]
userLUT = {}
nameLUT = {}
roomLUT = {}
roomLUT2 = {}
dmLUT = {}
eventLUT = {}
threadLUT = {}
replyLUT = {}
later = []
read_luts = False

if not os.path.isfile("config.yaml"):
    print("Config file does not exist.")
    sys.exit(1)

f = open("config.yaml", "r")
config_yaml = yaml.load(f.read(), Loader=yaml.FullLoader)

# load luts from previous run
if os.path.isfile("luts.yaml"):
    f = open("luts.yaml", "r")
    luts = yaml.load(f.read(), Loader=yaml.FullLoader)
    userLUT = luts["userLUT"]
    nameLUT = luts["nameLUT"]
    roomLUT = luts["roomLUT"]
    roomLUT2 = luts["roomLUT2"]
    dmLUT = luts["dmLUT"]
    read_luts = True

def test_config(yaml):
    if not config_yaml["zipfile"]:
        print("No zipfile defined in config")
        sys.exit(1)

    if not config_yaml["homeserver"]:
        print("No homeserver defined in config")
        sys.exit(1)

    if not config_yaml["as_token"]:
        print("No Application Service token defined in config")
        sys.exit(1)

    dry_run = config_yaml["dry-run"]
    skip_archived = config_yaml["skip-archived"]

    config = { "zipfile": config_yaml["zipfile"], "dry-run": dry_run, "homeserver": config_yaml["homeserver"], "skip-archived": skip_archived, "as_token": config_yaml["as_token"], "skip-files": config_yaml["skip-files"]}

    return config

def loadZip(config):
    zipName = config["zipfile"]
    print("Opening zipfile: " + zipName)
    archive = zipfile.ZipFile(zipName, 'r')
    jsonFiles = {}
    for channelType in channelTypes:
        try:
            jsonFiles[channelType] = archive.open(channelType)
            print("Found " + channelType + " in archive. Adding.")
        except:
            print("Warning: Couldn't find " + channelType + " in archive. Skipping.")
    return jsonFiles

def loadZipFolder(config, folder):
    with zipfile.ZipFile(config["zipfile"], 'r') as file:
        archive = file.infolist()

        fileList = []
        for entry in archive:
            file_basename = entry.filename.split("/", maxsplit=1)[0]
            if entry.is_dir() == False and folder == file_basename:
                fileList.append(entry.filename)

        return fileList

# update_progress() : Displays or updates a console progress bar
## Accepts a float between 0 and 1. Any int will be converted to a float.
## A value under 0 represents a 'halt'.
## A value at 1 or bigger represents 100%
def update_progress(progress):
    barLength = 40 # Modify this to change the length of the progress bar
    status = ""
    if isinstance(progress, int):
        progress = float(progress)
    if not isinstance(progress, float):
        progress = 0
        status = "error: progress var must be float\r\n"
    if progress < 0:
        progress = 0
        status = "Halt...\r\n"
    if progress >= 1:
        progress = 1
        status = "Done...\r\n"
    block = int(round(barLength*progress))
    text = "\rPercent: [{0}] {1:3.2f}% {2}".format( "#"*block + "-"*(barLength-block), progress*100, status)
    sys.stdout.write(text)
    sys.stdout.flush()

def login(server_location):
    try:
        default_user = getpass.getuser()
    except Exception:
        default_user = None

    if default_user:
        admin_user = input("Admin user localpart [%s]: " % (default_user,))
        if not admin_user:
            admin_user = default_user
    else:
        admin_user = input("Admin user localpart: ")

    if not admin_user:
        print("Invalid user name")
        sys.exit(1)

    admin_password = getpass.getpass("Password: ")

    if not admin_password:
        print("Password cannot be blank.")
        sys.exit(1)

    url = "%s/_matrix/client/r0/login" % (server_location,)
    data = {
        "type": "m.login.password",
        "user": admin_user,
        "password": admin_password,
    }

    # Get the access token
    r = requests.post(url, json=data, verify=False)

    if r.status_code != 200:
        print("ERROR! Received %d %s" % (r.status_code, r.reason))
        if 400 <= r.status_code < 500:
            try:
                print(r.json()["error"])
            except Exception:
                pass
        return False

    access_token = r.json()["access_token"]

    return admin_user, access_token

def getMaxUploadSize(config, access_token):
    # get maxUploadSize from Homeserver
    url = "%s/_matrix/media/r0/config?access_token=%s" % (config_yaml["homeserver"],access_token,)
    r = requests.get(url, verify=False)

    if r.status_code != 200:
        print("ERROR! Received %d %s" % (r.status_code, r.reason))
        if 400 <= r.status_code < 500:
            try:
                print(r.json()["error"])
            except Exception:
                pass

    maxUploadSize = r.json()["m.upload.size"]
    return maxUploadSize

def register_user(
    user,
    password,
    displayname,
    server_location,
    access_token,
    admin=False,
    user_type=None,
):

    url = "%s/_synapse/admin/v2/users/@%s:%s" % (server_location, user, config_yaml['domain'])

    headers = {'Authorization': ' '.join(['Bearer', access_token])}

    data = {
        "password": password,
        "displayname": "".join([user, config_yaml["name-suffix"]]),
        "admin": admin,
    }

    r = requests.put(url, json=data, headers=headers, verify=False)

    if r.status_code != 200 and r.status_code != 201:
        print("ERROR! Received %d %s" % (r.status_code, r.reason))
        if 400 <= r.status_code < 500:
            try:
                print(r.json()["error"])
            except Exception:
                pass
        return False

    return r

def register_room(
    name,
    creator,
    topic,
    invitees,
    preset,
    server_location,
    as_token,
):
    # register room
    url = "%s/_matrix/client/r0/createRoom?user_id=%s" % (server_location,creator,)

    body = {
        "preset": preset,
		"visibility": "public",
        "name": "".join([name, config_yaml["room-suffix"]]),
		"room_alias_name": name,
        "topic": topic,
        "creation_content": {
            "m.federate": config_yaml["federate-rooms"]
        },
        "invite": invitees,
        "is_direct": True if preset == "trusted_private_chat" else False,
    }

    #_print("Sending registration request...")
    r = requests.post(url, headers={'Authorization': 'Bearer ' + as_token}, json=body, verify=False)

    if r.status_code != 200:
        print("ERROR! Received %d %s" % (r.status_code, r.reason))
        if 400 <= r.status_code < 500:
            try:
                print(r.json()["error"])
            except Exception:
                pass
        return False

    return r

def autojoin_users(
    invitees,
    roomId,
    config,
):
    for user in invitees:
        #POST /_matrix/client/r0/rooms/{roomId}/join
        url = "%s/_matrix/client/r0/rooms/%s/join?user_id=%s" % (config["homeserver"],roomId,user,)

        #_print("Sending registration request...")
        r = requests.post(url, headers={'Authorization': 'Bearer ' + config["as_token"]}, verify=False)

        if r.status_code != 200:
            print("ERROR! Received %d %s" % (r.status_code, r.reason))
            if 400 <= r.status_code < 500:
                try:
                    print(r.json()["error"])
                except Exception:
                    pass

def migrate_users(userFile, config, access_token):
    userlist = []
    userData = json.load(userFile)
    for user in userData:
        if user["is_bot"] == True:
            continue

        # ignore slackbot
        if user["id"] == "USLACKBOT":
            continue

        _servername = config["homeserver"].split('/')[2]
        _matrix_user = user["name"]
        _matrix_id = '@' + user["name"] + ':' + config_yaml["domain"]

        # check if display name is set
        if "real_name" in user["profile"]:
            _real_name = user["profile"]["real_name"]
        else:
            _real_name = ""

        # check if email is set
        if "email" in user["profile"]:
            _email = user["profile"]["email"]
        else:
            _email = ""

        # generate password
        _alphabet = string.ascii_letters + string.digits
        _password = ''.join(secrets.choice(_alphabet) for i in range(20)) # for a 20-character password

        userDetails = {
            "slack_id": user["id"],
            "slack_team_id": user["team_id"],
            "slack_name": user["name"],
            "slack_real_name": _real_name,
            "slack_email": _email,
            "matrix_id": _matrix_id,
            "matrix_user": _matrix_user,
            "matrix_password": _password,
        }

        print("Registering Slack user " + userDetails["slack_id"] + " -> " + userDetails["matrix_id"])
        if not config["dry-run"]:
            res = register_user(userDetails["matrix_user"], userDetails["matrix_password"], userDetails["slack_real_name"], config["homeserver"], access_token)
            if res == False:
                print("ERROR while registering user '" + userDetails["matrix_id"] + "'")
                continue

            # TODO force password change at next login

        userLUT[userDetails["slack_id"]] = userDetails["matrix_id"]
        nameLUT[userDetails["matrix_id"]] = userDetails["slack_real_name"]
        userlist.append(userDetails)
    return userlist


def migrate_rooms(roomFile, config, admin_user):
    roomlist = []

    # channels
    channelData = json.load(roomFile)
    for channel in channelData:
        if config["skip-archived"]:
            if channel["is_archived"] == True:
                continue

        if config_yaml["create-as-admin"]:
            _mxCreator = "".join(["@", admin_user, ":", config_yaml["domain"]])
        else:
            # if user is not in LUT (maybe its a shared channel), default to admin_user
            if channel["creator"] in userLUT:
                _mxCreator = userLUT[channel["creator"]]
            else:
                _mxCreator = "".join(["@", admin_user, ":", config_yaml["domain"]])

        _invitees = []
        if config_yaml["invite-all"]:
            for user in nameLUT.keys():
                if user != _mxCreator:
                    _invitees.append(user)
        else:
            for user in channel["members"]:
                if user != channel["creator"]:
                    if user in userLUT: # ignore dropped users like bots
                        _invitees.append(userLUT[user])

        roomDetails = {
            "slack_id": channel["id"],
            "slack_name": channel["name"],
            "slack_members": channel["members"],
            "slack_topic": channel["topic"],
            "slack_purpose": channel["purpose"],
            "slack_created": channel["created"],
            "slack_creator": channel["creator"],
            "matrix_id": '',
            "matrix_creator": _mxCreator,
            "matrix_topic": channel["topic"]["value"],
        }

        room_preset = "private_chat" if config_yaml["import-as-private"] else "public_chat"

        if not config["dry-run"]:
            res = register_room(roomDetails["slack_name"], roomDetails["matrix_creator"], roomDetails["matrix_topic"], _invitees, room_preset, config["homeserver"], config["as_token"])

            if res == False:
                print("ERROR while registering room '" + roomDetails["slack_name"] + "'")
                continue
            else:
                _content = json.loads(res.content)
                roomDetails["matrix_id"] = _content["room_id"]
            print("Registered Slack channel " + roomDetails["slack_name"] + " -> " + roomDetails["matrix_id"])

            #autojoin all members
            autojoin_users(_invitees, roomDetails["matrix_id"], config)

        roomLUT[roomDetails["slack_id"]] = roomDetails["matrix_id"]
        roomLUT2[roomDetails["slack_id"]] = roomDetails["slack_name"]
        roomlist.append(roomDetails)

    return roomlist

def migrate_dms(roomFile, config):
    roomlist = []

    # channels
    channelData = json.load(roomFile)
    for channel in channelData:
        if config["skip-archived"]:
            if channel["is_archived"] == True:
                continue

        # skip dms with slackbot
        if channel["user"] == "USLACKBOT":
            continue

        _mxCreator = userLUT[channel["user"]]

        _invitees = []
        for user in channel["members"]:
            if user != channel["user"]:
                _invitees.append(userLUT[user])

        roomDetails = {
            "slack_id": channel["id"],
            "slack_members": channel["members"],
            "slack_created": channel["created"],
            "slack_creator": channel["user"],
            "matrix_id": '',
            "matrix_creator": _mxCreator,
        }

        if not config["dry-run"]:
            res = register_room('', roomDetails["matrix_creator"], '', _invitees, "trusted_private_chat", config["homeserver"], config["as_token"])

            if res == False:
                print("ERROR while registering room '" + roomDetails["slack_name"] + "'")
                continue
            else:
                _content = json.loads(res.content)
                roomDetails["matrix_id"] = _content["room_id"]
            print("Registered Slack DM channel " + roomDetails["slack_id"] + " -> " + roomDetails["matrix_id"])

            #autojoin all members
            autojoin_users(_invitees, roomDetails["matrix_id"], config)

        dmLUT[roomDetails["slack_id"]] = roomDetails["matrix_id"]
        roomlist.append(roomDetails)

    return roomlist

def send_reaction(config, roomId, eventId, reactionKey, userId, txnId):

    content = {
        "m.relates_to": {
            "event_id": eventId,
            "key": reactionKey,
            "rel_type": "m.annotation",
        },
    }

    res = send_event(config, content, roomId, userId, "m.reaction", txnId)

    return res

def replace_mention(matchobj):
    _slack_id = matchobj.group(0)[2:-1]

    if not _slack_id in userLUT:
        return ''
    user_id = userLUT[_slack_id]
    displayname = nameLUT[user_id]

    return "<a href='https://matrix.to/#/" + user_id + "'>" + displayname + "</a>"

def getFallbackHtml(roomId, replyEvent):
    originalBody = replyEvent["body"]
    originalHtml = replyEvent["formatted_body"]
    if not replyEvent["body"]:
        originalHtml = originalBody

    return '<mx-reply><blockquote><a href="https://matrix.to/#/' + roomId + '/' + replyEvent["event_id"] + '">In reply to</a><a href="https://matrix.to/#/' + replyEvent["sender"] + '">' + replyEvent["sender"] + '</a><br />' + originalHtml + '</blockquote></mx-reply>'

def getFallbackText(replyEvent):
    originalBody = replyEvent["body"]
    originalBody = originalBody.split("\n")
    originalBody = "\n> ".join(originalBody)
    return '> <' + replyEvent["sender"] + '> ' + originalBody

def parse_and_send_message(config, message, matrix_room, txnId, is_later):
    content = {}
    is_thread = False
    is_reply = False

    if message["type"] == "message":
        if "subtype" in message:
            if (message["subtype"] == "bot_message" or
                message["subtype"] == "bot_remove" or
				message["subtype"] == "slackbot_response" or
                message["subtype"] == "channel_name" or
                message["subtype"] == "channel_join" or
                message["subtype"] == "channel_purpose" or
                message["subtype"] == "group_name" or
                message["subtype"] == "group_join" or
                message["subtype"] == "group_purpose"):
                    return txnId

            if message["subtype"] == "file_comment":
                # TODO migrate file_comments
                return txnId

        # ignore hidden messages
        if "hidden" in message:
            if message["hidden"] == True:
                return txnId

        if "user" in message: #TODO what messages have no user?
            if not message["user"] in userLUT:
                # ignore messages from bots
                return txnId
        else:
            print("Message without user")
            print(message)

        # list of subtypes
        '''
        bot_message	A message was posted by an app or integration
        me_message	A /me message was sent
        message_changed	A message was changed
        message_deleted	A message was deleted
        channel_join	A member joined a channel
        channel_leave	A member left a channel
        channel_topic	A channel topic was updated
        channel_purpose	A channel purpose was updated
        channel_name	A channel was renamed
        channel_archive	A channel was archived
        channel_unarchive	A channel was unarchived
        group_join	A member joined a group
        group_leave	A member left a group
        group_topic	A group topic was updated
        group_purpose	A group purpose was updated
        group_name	A group was renamed
        group_archive	A group was archived
        group_unarchive	A group was unarchived
        file_share	A file was shared into a channel
        file_reply	A reply was added to a file
        file_mention	A file was mentioned in a channel
        pinned_item	An item was pinned in a channel
        unpinned_item	An item was unpinned from a channel
        '''

        body = message["text"]

        # TODO do not migrate empty messages?
        #if body == "":
        #
        #    return txnId

        # replace mentions
        body = body.replace("<!channel>", "@room");
        body = body.replace("<!here>", "@room");
        body = body.replace("<!everyone>", "@room");
        body = re.sub('<@[A-Z0-9]+>', replace_mention, body)

        if "files" in message:
            if "subtype" in message:
                print(message["subtype"])
                if message["subtype"] == "file_comment" or message["subtype"] == "thread_broadcast":
                    #TODO treat as reply
                    print("")
                else:
                    txnId = process_files(message["files"], matrix_room, userLUT[message["user"]], body, txnId, config)
            else:
                txnId = process_files(message["files"], matrix_room, userLUT[message["user"]], body, txnId, config)

        if "attachments" in message:
            if message["user"] in userLUT: # ignore attachments from bots
                txnId = process_attachments(message["attachments"], matrix_room, userLUT[message["user"]], body, txnId, config)
                for attachment in message["attachments"]:
                    if "is_share" in attachment and attachment["is_share"]:
                        if body:
                            body += "\n"
                        attachment_footer = "no footer"
                        if "footer" in attachment:
                            attachment_footer = attachment["footer"]
                        attachment_text = "no text"
                        if "text" in attachment:
                            attachment_text = attachment["text"]
                        body += "".join(["&gt; _Shared (", attachment_footer, "):_ ", attachment_text, "\n"])

        if "replies" in message: # this is the parent of a thread
            is_thread = True
            previous_message = None
            for reply in message["replies"]:
                if "user" in message and "ts" in message:
                    first_message = message["user"]+message["ts"]
                    current_message = reply["user"]+reply["ts"]
                    if not previous_message:
                        previous_message = first_message
                    replyLUT[current_message] = previous_message
                    if config_yaml["threads-reply-to-previous"]:
                        previous_message = current_message

        # replys / threading
        if "thread_ts" in message and "parent_user_id" in message and not "replies" in message and not (message["user"]=="USLACKBOT"): # this message is a reply to another message
            is_reply = True
            if not message["user"]+message["ts"] in replyLUT:
                # seems like we don't know the thread yet, save event for later
                if not is_later:
                    later.append(message)
                return txnId
            if not message["user"].startswith('USLACKBOT'):
                slack_event_id = replyLUT[message["user"]+message["ts"]]
                matrix_event_id = eventLUT[slack_event_id]
            else:
                return txnId

        # TODO pinned / stared items?

        # replace emojis
        body = emojize(body, use_aliases=True)

        # TODO some URLs with special characters (e.g. _ ) are parsed wrong
        formatted_body = slackdown.render(body)

        if not is_reply:
            content = {
                    "body": body,
                    "msgtype": "m.text",
                    "format": "org.matrix.custom.html",
                    "formatted_body": formatted_body,
            }
        else:
            replyEvent = threadLUT[message["parent_user_id"]+message["thread_ts"]]
            fallbackHtml = getFallbackHtml(matrix_room, replyEvent);
            fallbackText = getFallbackText(replyEvent);
            body = fallbackText + "\n\n" + body
            formatted_body = fallbackHtml + formatted_body
            content = {
                "m.relates_to": {
                    "m.in_reply_to": {
                        "event_id": matrix_event_id,
                    },
                },
                "msgtype": "m.text",
                "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": formatted_body,
            }

        # send message
        ts = message["ts"].replace(".", "")[:-3]
        res = send_event(config, content, matrix_room, userLUT[message["user"]], "m.room.message", txnId, ts)
        # save event id
        if res == False:
            print("ERROR while sending event '" + message["user"] + " " + message["ts"] + "'")
        else:
            _content = json.loads(res.content)
            # use "user" combined with "ts" as id like Slack does as "client_msg_id" is not always set
            if "user" in message and "ts" in message:
                eventLUT[message["user"]+message["ts"]] = _content["event_id"]
            txnId = txnId + 1
            if is_thread:
                threadLUT[message["user"]+message["ts"]] = {"body": body, "formatted_body": formatted_body, "sender": userLUT[message["user"]], "event_id": _content["event_id"]}

            # handle reactions
            if "reactions" in message:
                roomId = matrix_room
                eventId = eventLUT[message["user"]+message["ts"]]
                for reaction in message["reactions"]:
                    for user in reaction["users"]:
                        #print("Send reaction in room " + roomId)
                        send_reaction(config, roomId, eventId, emojize(reaction["name"], use_aliases=True), userLUT[user], txnId)
                        txnId = txnId + 1

    else:
        print("Ignoring message type " + message["type"])
    return txnId

def migrate_messages(fileList, matrix_room, config, tick):
    global later
    archive = zipfile.ZipFile(config["zipfile"], 'r')
    txnId = 1
    progress = 0

    for file in fileList:
        try:
            fileData = archive.open(file)
            messageData = json.load(fileData)
        except:
            print("Warning: Couldn't load data from file " + file + " in archive. Skipping this file.")

        for message in messageData:
            txnId = parse_and_send_message(config, message, matrix_room, txnId, False)

        progress = progress + tick
        update_progress(progress)

    # process postponed messages
    for message in later:
        txnId = parse_and_send_message(config, message, matrix_room, txnId, True)

    # clean up postponed messages
    later = []

def kick_imported_users(server_location, admin_user, access_token, tick):
    headers = {'Authorization': ' '.join(['Bearer', access_token])}
    progress = 0

    for room in roomLUT.values():
        url = "%s/_matrix/client/r0/rooms/%s/kick" % (server_location, room)

        for name in nameLUT.keys():
            data = {"user_id": name}

            r = requests.post(url, json=data, headers=headers, verify=False)

            if r.status_code != 200 and r.status_code != 201:
                print("ERROR! Received %d %s" % (r.status_code, r.reason))
                if 400 <= r.status_code < 500:
                    try:
                        print(r.json()["error"])
                    except Exception:
                        pass

        progress = progress + tick
        update_progress(progress)

def main():
    logging.captureWarnings(True)

    config = test_config(yaml)

    jsonFiles = loadZip(config)

    # login with admin user to gain access token
    admin_user, access_token = login(config["homeserver"])

    maxUploadSize = getMaxUploadSize(config, access_token)
    config["maxUploadSize"] = maxUploadSize

    if access_token == False:
        print("ERROR! Admin user could not be logged in.")
        exit(1)

    # create users in matrix and match them to slack users
    if "users.json" in jsonFiles and not userLUT:
        userlist = migrate_users(jsonFiles["users.json"], config, access_token)

    # create rooms and match to channels
    # Slack channels
    if "channels.json" in jsonFiles and not roomLUT:
        roomlist_channels = migrate_rooms(jsonFiles["channels.json"], config, admin_user)

    # Slack groups
    if "groups.json" in jsonFiles and not roomLUT:
        roomlist_groups = migrate_rooms(jsonFiles["groups.json"], config, admin_user)

    # create DMs
    if "dms.json" in jsonFiles and not dmLUT:
        roomlist_dms = migrate_dms(jsonFiles["dms.json"], config, admin_user)

    # write LUTs to file to be able to load from later if something goes wrong
    if not read_luts:
        data = dict(
            userLUT = userLUT,
            nameLUT = nameLUT,
            roomLUT = roomLUT,
            roomLUT2 = roomLUT2,
            dmLUT = dmLUT,
            users = userlist,
        )
        with open('luts.yaml', 'w') as outfile:
            yaml.dump(data, outfile, default_flow_style=False)

    # send events to rooms
    print("Migrating messages to rooms. This may take a while...")
    for slack_room, matrix_room in roomLUT.items():
        print("Migrating messages for room: " + roomLUT2[slack_room])
        fileList = sorted(loadZipFolder(config, roomLUT2[slack_room]))
        if fileList:
            tick = 1/len(fileList)
            migrate_messages(fileList, matrix_room, config, tick)

    # clean up postponed messages
    later = []

    # send events to dms
    print("Migrating messages to DMs. This may take a while...")
    for slack_room, matrix_room in dmLUT.items():
        fileList = sorted(loadZipFolder(config, slack_room))
        if fileList:
            tick = 1/len(fileList)
            migrate_messages(fileList, matrix_room, config, tick)

    # clean up postponed messages
    later = []

    # kick imported users from non-dm rooms
    if config_yaml["kick-imported-users"]:
        print("Kicking imported users from rooms. This may take a while...")
        tick = 1/len(roomLUT)
        kick_imported_users(config["homeserver"], admin_user, access_token, tick)


if __name__ == "__main__":
    main()
