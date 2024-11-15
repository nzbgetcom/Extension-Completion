#
# Completion.py script for NZBGet
#
# Copyright (C) 2014-2017 kloaknet.
# Copyright (C) 2024 Denis <denis@nzbget.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#


import os
import urllib.request, urllib.error, urllib.parse
import base64
import json
import time
import sys
import socket
import ssl
import traceback
import html.parser
import errno
from xmlrpc.client import ServerProxy
from operator import itemgetter

sys.stdout.reconfigure(encoding="utf-8")


# Defining constants
AGE_LIMIT = int(os.environ.get("NZBPO_AgeLimit", 4))
AGE_LIMIT_SEC = 3600 * AGE_LIMIT
AGE_SORT_LIMIT = int(os.environ.get("NZBPO_AgeSortLimit", 48))
if AGE_LIMIT > AGE_SORT_LIMIT:
    AGE_SORT_LIMIT = AGE_LIMIT
AGE_SORT_LIMIT_SEC = 3600 * AGE_SORT_LIMIT
CHECK_DUPES = os.environ.get("NZBPO_CheckDupes", "No")
if CHECK_DUPES != "No" and os.environ.get("NZBOP_DUPECHECK") == "No":
    print(
        "[WARNING] DupeCheck should be enabled in NZBGet, otherwise "
        + "the CheckDupes option of this script that you have enabled "
        + "does not work"
    )
FORCE_FAILURE = os.environ.get("NZBPO_ForceFailure", "No") == "Yes"
CATEGORIES = os.environ.get("NZBPO_Categories", "").lower().split(",")
CATEGORIES = [c.strip(" ") for c in CATEGORIES]
SERVERS = os.environ.get("NZBPO_Servers", "").lower().split(",")
SERVERS = [c.strip(" ") for c in SERVERS]
FILL_SERVERS = os.environ.get("NZBPO_FillServers", "").lower().split(",")
FILL_SERVERS = [c.strip(" ") for c in FILL_SERVERS]
MAX_FAILURE = int(os.environ.get("NZBPO_MaxFailure", 0))
CHECK_METHOD = "STAT"
VERBOSE = os.environ.get("NZBPO_Verbose", "No") == "Yes"
EXTREME = os.environ.get("NZBPO_Extreme", "No") == "Yes"
IGNORE_QUEUE_PRIORITY = os.environ.get("NZBPO_IgnoreQueuePriority", "No") == "Yes"
CHECK_LIMIT = int(os.environ.get("NZBPO_CheckLimit", 10))
MAX_ARTICLES = int(os.environ.get("NZBPO_MaxArticles", 1000))
MIN_ARTICLES = int(os.environ.get("NZBPO_MinArticles", 50))
FULL_CHECK_NO_PARS = os.environ.get("NZBPO_FullCheckNoPars", "Yes") == "Yes"
NNTP_TIME_OUT = 2  # low, but should be sufficient for connection check
SOCKET_CREATE_INTERVAL = 0.000  # optional delay to avoid handshake time outs
SOCKET_LOOP_INTERVAL = 0.200  # max delay single loop on data received
HOST = os.environ["NZBOP_CONTROLIP"]  # NZBGet host
if HOST == "0.0.0.0":
    HOST = "127.0.0.1"  # fix to localhost
PORT = os.environ["NZBOP_CONTROLPORT"]  # NZBGet port
USERNAME = os.environ["NZBOP_CONTROLUSERNAME"]  # NZBGet username
PASSWORD = os.environ["NZBOP_CONTROLPASSWORD"]  # NZBGet password


def unpause_nzb(nzb_id):
    """
    resume the nzb with NZBid in the NZBGet queue via RPC-API
    """
    NZBGet = connect_to_nzbget()
    NZBGet.editqueue("GroupResume", 0, "", [int(nzb_id)])  # Resume nzb
    NZBGet.editqueue("GroupPauseExtraPars", 0, "", [int(nzb_id)])  # Pause pars


def unpause_nzb_dupe(dupe_nzb_id, nzb_id):
    """
    resume the nzb with NZBid in the NZBGet history via RPC-API, move the
    other one to history.
    """
    NZBGet = connect_to_nzbget()
    # Return item from history (before deleting, to avoid NZBGet automatically
    # returning a DUPE instead of the script).
    NZBGet.editqueue("HistoryRedownload", 0, "", [int(dupe_nzb_id)])  # Return
    NZBGet.editqueue("GroupResume", 0, "", [int(dupe_nzb_id)])  # Resume nzb
    # Pause pars
    NZBGet.editqueue("GroupPauseExtraPars", 0, "", [int(dupe_nzb_id)])
    # Remove item in queue, send back to history as DUPE
    NZBGet.editqueue("GroupDupeDelete", 0, "", [int(nzb_id)])


def mark_bad(nzb_id):
    """
    mark the nzb with NZBid BAD in the NZBGet queue via RPC-API
    """
    NZBGet = connect_to_nzbget()
    NZBGet.editqueue("GroupDelete", 0, "", [int(nzb_id)])  # need to delete
    NZBGet.editqueue("HistoryMarkBad", 0, "", [int(nzb_id)])  # mark bad


def mark_bad_dupe(dupe_nzb_id):
    """
    mark the nzb with NZBid BAD in the NZBGet history via RPC-API, item is
    already in history, so no moving.
    """
    NZBGet = connect_to_nzbget()
    NZBGet.editqueue("HistoryMarkBad", 0, "", [int(dupe_nzb_id)])  # mark bad


def force_failure(nzb_id):
    """
    mark BAD doesn't do the trick for FailureLink, Sonarr, SickBeard
    Forces failure, removes all files but one .par2
    sending deleted nzb back to queue will restore the deleted files.
    When available, the smallest par and smallest other file will be kept.
    """
    if VERBOSE:
        print("[V] force_failure(nzb_id=" + str(nzb_id) + ")")
    NZBGet = connect_to_nzbget()
    data = NZBGet.listfiles(0, 0, [int(nzb_id)])
    id_list = []
    file_size_low = 100000000000  # 100 Gb, file size will never occur.
    par_size_low = 100000000000
    for f in data:  # find smallest par2 and file
        file_name = f.get("Filename").lower()
        if ".par2" not in file_name:
            temp = f.get("FileSizeLo")
            if temp < file_size_low:
                file_size_low = temp
        else:
            temp = f.get("FileSizeLo")
            if temp < par_size_low:
                par_size_low = temp
    for f in data:  # match files on size for deletion
        temp = f.get("FileSizeLo")
        if temp != file_size_low and temp != par_size_low:
            id = int(f.get("ID"))
            id_list.append(id)
        else:
            if VERBOSE:
                print(
                    "[V] Leaving file: " + str(f.get("Filename")) + " in the NZB file"
                )
    print("[WARNING] Forcing failure of NZB:")
    sys.stdout.flush()  # force message before lot of NZBGet messages
    time.sleep(0.1)  # create time to flush
    # delete all listed files
    NZBGet.editqueue("FileDelete", 0, "", id_list)
    # Resume nzb in queue to download single remaining file in NZB
    NZBGet.editqueue("GroupResume", 0, "", nzb_id)


def force_failure_dupe(dupe_nzb_id):
    """
    mark BAD doesn't do the trick for FailureLink, Sonarr, SickBeard
    Forces failure, removes all files but one .par2
    sending deleted nzb back to queue will restore the deleted files.
    although no dupes are expected from Sonarr and the likes, maybe a RSS
    feed for movies or other stuff is used that might produce dupes.
    """
    if VERBOSE:
        print("[V] force_failure_dupe(nzb_id=" + str(dupe_nzb_id) + ")")
    NZBGet = connect_to_nzbget()
    if VERBOSE:
        print("[V] Pausing failed DUPE NZB before returning to queue.")
    # pause all files before returning to queue
    NZBGet.editqueue("GroupPause", 0, "", dupe_nzb_id)
    if VERBOSE:
        print("[V] Returning failed DUPE NZB to queue.")
    # return item back to queue to be able to force a failure
    NZBGet.editqueue("HistoryReturn", 0, "", dupe_nzb_id)
    force_failure(dupe_nzb_id)


def connect_to_nzbget():
    """
    Establish connection to NZBGet via RPC-API using HTTP.
    """
    # Build an URL for XML-RPC requests:
    xmlRpcUrl = "http://%s:%s@%s:%s/xmlrpc" % (USERNAME, PASSWORD, HOST, PORT)
    # Create remote server object
    NZBGet = ServerProxy(xmlRpcUrl)
    return NZBGet


def call_nzbget_direct(url_command):
    """
    Connect to NZBGet and call an RPC-API-method without using of python's
    XML-RPC. XML-RPC is easy to use but it is slow for large amounts of
    data.
    """
    # Building http-URL to call the method
    http_url = "http://%s:%s/jsonrpc/%s" % (HOST, PORT, url_command)
    request = urllib.request.Request(http_url)
    base_64_string = (
        base64.b64encode(("%s:%s" % (USERNAME, PASSWORD)).encode("utf-8"))
        .decode("utf-8")
        .strip()
    )
    request.add_header("Authorization", "Basic %s" % base_64_string)
    response = urllib.request.urlopen(request)  # get some data from NZBGet
    # data is a JSON raw-string, contains ALL properties each NZB in queue
    data = response.read().decode("utf-8")
    return data


def get_nzb_filename(parameters):
    """
    get the real nzb_filename from the added parameter CnpNZBFileName or from env
    """
    file_name = os.environ.get("NZBNA_QUEUEDFILE")
    if file_name:
        return file_name

    for p in parameters:
        if p["Name"] == "CnpNZBFileName":
            break
    return p["Value"]


def get_max_failed_limit(critical_health) -> float:
    return round(100 - critical_health / 10.0, 1)


def get_nzb_status(nzb):
    """
    check if amount of failed articles is not too much. If too much keep
    paused, if too old and too much failure mark bad / force failure,
    otherwise resume. When an -1 or -2 is returned from check_nzb(), the
    nzb is unpaused, hoping NZBGet can still process the file, while the
    script can't.
    """
    if VERBOSE:
        print("[V] get_nzb_status(nzb=" + str(nzb) + ")")
    print('Checking: "' + nzb[1] + '"')
    # collect rar msg ids that need to be checked
    rar_msg_ids = get_nzb_data(nzb[1])
    if rar_msg_ids == -1:  # no such NZB file
        succes = True  # file send back to queue
        print(
            "[WARNING] The NZB file "
            + str(nzb[1])
            + " does not seem to "
            + "exist, resuming NZB."
        )
        unpause_nzb(nzb[0])  # unpause based on NZBGet ID
    elif rar_msg_ids == -2:  # empty NZB or no group
        succes = True  # file send back to queue
        print(
            "[WARNING] The NZB file "
            + str(nzb[1])
            + " appears to be "
            + "invalid, resuming NZB."
        )
        unpause_nzb(nzb[0])  # unpause based on NZBGet ID
    elif rar_msg_ids == -3:  # NZB without RAR files.
        succes = True  # file send back to queue
        print(
            "[WARNING] The NZB file "
            + str(nzb[1])
            + " does not contain "
            + "any .rar files and has been moved back to the queue."
        )
        unpause_nzb(nzb[0])  # unpause based on NZBGet ID
    else:
        failed_limit = get_max_failed_limit(nzb[3])
        print("Maximum failed articles limit for NZB: " + str(failed_limit) + "%")
        if MAX_FAILURE > 0:
            print(
                "Maximum failed articles limit for highest level news server: "
                + str(MAX_FAILURE)
                + "%"
            )
        failed_ratio = check_failure_status(rar_msg_ids, failed_limit, nzb[2])
        if VERBOSE:
            print("[V] Total failed ratio: " + str(round(failed_ratio, 1)) + "%")
        if (
            failed_ratio < failed_limit
            and (failed_ratio < MAX_FAILURE or MAX_FAILURE == 0)
        ) or failed_ratio == 0:
            succes = True
            print('Resuming: "' + nzb[1] + '"')
            sys.stdout.flush()
            unpause_nzb(nzb[0])  # unpause based on NZBGet ID
        elif (
            failed_ratio >= failed_limit
            or (failed_ratio >= MAX_FAILURE and MAX_FAILURE > 0)
        ) and nzb[2] < (int(time.time()) - int(AGE_LIMIT_SEC)):
            succes = False
            if VERBOSE:
                if not FORCE_FAILURE:
                    print('[V] Marked as BAD: "' + nzb[1] + '"')
                    sys.stdout.flush()  # otherwise NZBGet sends message first
            if FORCE_FAILURE:
                force_failure(nzb[0])
            else:
                mark_bad(nzb[0])
        else:
            succes = False
            # dupekey should not be '', that would mean it is not added by RSS
            if CHECK_DUPES != "no" and nzb[4] != "":
                if get_dupe_nzb_status(nzb):
                    print(
                        '"'
                        + nzb[1]
                        + '" moved to history as DUPE, '
                        + "complete DUPE returned to queue."
                    )
                else:
                    print(
                        '[WARNING] "'
                        + nzb[1]
                        + '", remains paused for next check, '
                        + "no suitable/complete DUPEs found in history"
                    )
            elif CHECK_DUPES != "no" and nzb[4] == "" and VERBOSE:
                print(
                    "[V] "
                    + nzb[1]
                    + " is not added via RSS, therefore "
                    + "the dupekey is empty and checking for DUPEs in the history "
                    + "is skipped."
                )
    return succes


def get_dupe_nzb_status(nzb):
    """
    check dupes in the history on their possible completion when the item
    in the queue is not yet complete. When complete DUPE item, move it
    back into the queue, and move the otherone to history.
    """
    if VERBOSE:
        print("[V] get_dupe_nzb_status(nzb=" + str(nzb) + ")")
    # get the data from the active history
    data = call_nzbget_direct("history")
    jobs = json.loads(data)
    duplicate = False
    num_duplicates = 0
    list_duplicates = []
    for job in jobs["result"]:
        if (
            job["Status"] == "DELETED/DUPE"
            and job["DupeKey"] == nzb[4]
            and "CnpNZBFileName" in str(job)
        ):
            if CHECK_DUPES == "yes":
                duplicate = True
                num_duplicates += 1
                list_duplicates.append(job)
            elif CHECK_DUPES == "SameScore" and job["DupeScore"] >= nzb[5]:
                duplicate = True
                num_duplicates += 1
                list_duplicates.append(job)
            else:
                if VERBOSE:
                    print(
                        "[V] DUPE NZB found with lower dupe score, "
                        + "ignored due to SameScore setting."
                    )
    if duplicate:
        # sort on nzb age, then on dupescore. Higher score items will be on
        # top. Oldest file has lowest maxposttime.
        if VERBOSE:
            print(
                "[V] "
                + str(num_duplicates)
                + " duplicate of "
                + nzb[1]
                + " found in history"
            )
        t = sorted(list_duplicates, key=itemgetter("MaxPostTime"))
        sorted_duplicates = sorted(t, key=itemgetter("DupeScore"), reverse=True)
        i = 0
        # loop through all DUPE items (with optional matching DUPEscore)
        for job in sorted_duplicates:
            i += 1
            nzb_id = job["NZBID"]
            nzb_filename = get_nzb_filename(job["Parameters"])
            nzb_age = job["MaxPostTime"]  # nzb age
            nzb_critical_health = job["CriticalHealth"]
            print(
                'Checking DUPE: "'
                + nzb_filename
                + '" ['
                + str(i)
                + "/"
                + str(num_duplicates)
                + "]"
            )
            rar_msg_ids = get_nzb_data(nzb[1])
            if rar_msg_ids == -1:  # no such NZB file
                success = False  # file marked BAD
                if VERBOSE:
                    print("[WARNING] [V] No such DUPE NZB file, marking BAD.")
                if FORCE_FAILURE:
                    force_failure_dupe(nzb_id)  #
                else:
                    mark_bad_dupe(nzb_id)
            elif rar_msg_ids == -2:  # empty NZB or no group
                success = False  # file marked BAD
                if VERBOSE:
                    print("[WARNING] [V] DUPE NZB appears invalid, marking BAD.")
                if FORCE_FAILURE:
                    force_failure_dupe(nzb_id)  #
                else:
                    mark_bad_dupe(nzb_id)
            else:
                failed_limit = get_max_failed_limit(nzb[3])
                print("[V] Maximum failed articles limit: " + str(failed_limit) + "%")
                failed_ratio = check_failure_status(rar_msg_ids, failed_limit, nzb[2])
                if VERBOSE:
                    print(
                        "[V] Total failed ratio: " + str(round(failed_ratio, 1)) + "%"
                    )

                if (
                    failed_ratio < failed_limit
                    and (failed_ratio < MAX_FAILURE or MAX_FAILURE == 0)
                ) or failed_ratio == 0:
                    success = True
                    print('Resuming DUPE: "' + nzb_filename + '"')
                    sys.stdout.flush()
                    unpause_nzb_dupe(nzb_id, nzb[0])  # resume on NZBGet ID
                    break
                elif (
                    failed_ratio >= failed_limit
                    or (failed_ratio >= MAX_FAILURE and MAX_FAILURE > 0)
                ) and nzb_age < (int(time.time()) - int(AGE_LIMIT_SEC)):
                    success = False
                    if VERBOSE:
                        if not FORCE_FAILURE:
                            print('[V] Marked as BAD: "' + nzb[1] + '"')
                        else:
                            print('[V] Forcing failure of: "' + nzb[1] + '"')
                        sys.stdout.flush()
                    if FORCE_FAILURE:
                        force_failure_dupe(nzb_id)
                    else:
                        mark_bad_dupe(nzb_id)
                else:
                    success = False
    else:
        if VERBOSE:
            print("[V] No DUPE of " + nzb[1] + " found in history.")
        success = False
    return success


def is_number(s):
    """
    Checks if the string can be converted to a number
    """
    try:
        float(s)
        return True
    except ValueError:
        return False


def check_send_server_reply(
    sock, reply: str, group: str, id, i, host, username, password
):
    """
    Check NNTP server messages, send data for next recv.
    After connecting, there will be a 200 message, after each message, a
    reply (t) will be send to get a next message.

    More info on NNTP server responses:
    The first digit of the response broadly indicates the success,
    failure, or progress of the previous command:
       1xx - Informative message
       2xx - Command completed OK
       3xx - Command OK so far; send the rest of it
       4xx - Command was syntactically correct but failed for some reason
       5xx - Command unknown, unsupported, unavailable, or syntax error
    The next digit in the code indicates the function response category:
       x0x - Connection, setup, and miscellaneous messages
       x1x - Newsgroup selection
       x2x - Article selection
       x3x - Distribution functions
       x4x - Posting
       x8x - Reserved for authentication and privacy extensions
       x9x - Reserved for private use (non-standard extensions
    """
    if EXTREME:
        print(
            "[E] check_send_server_reply(sock= "
            + str(sock)
            + ", t= "
            + reply
            + " ,group= "
            + str(group)
            + " , id= "
            + str(id)
            + " , i= "
            + str(i)
            + " )"
        )
    try:
        id_used = False  # is id used via HEAD / STAT request to NNTP server
        msg_id_used = None
        error = False
        server_reply = str(reply[:3])  # only first 3 chars are relevant
        # no correct NNTP server code received, most likely still propagating?
        if not is_number(server_reply):
            if VERBOSE:
                print(
                    "[WARNING] [V] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply incorrect:"
                    + str(reply.split())
                )
            server_reply = "NNTP reply incorrect."
            error = True  # pass these vars so that next article will be sent
            id_used = True  # pass these vars so that next article will be sent
            return (error, id_used, server_reply, msg_id_used)
        # checking NNTP server server_replies
        if server_reply in ("411", "420", "423", "430"):
            # 411 no such group
            # 420 no current article has been selected
            # 423 no such article number in this group
            # 430 no such article found
            if VERBOSE:
                print(
                    "[WARNING] [V] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
            error = True  # article is not there
        elif server_reply in ("412"):  # 412 no newsgroup has been selected
            text = "GROUP " + group + "\r\n"
            if EXTREME:
                print(
                    "[E] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
                print("[E] Socket: " + str(i) + " " + str(host) + ", Send: " + text)
            sock.send(text.encode("utf-8"))
        elif server_reply in ("221"):
            # 221 article retrieved - head follows (reply on HEAD)
            msg_id_used = reply.split()[2][1:-1]  # get msg id to identify ok article
            if EXTREME:
                print(
                    "[E] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
        elif server_reply in ("223"):
            # 223 article retrieved - request text separately (reply on STAT)
            msg_id_used = reply.split()[2][1:-1]  # get msg id to identify ok article
            if EXTREME:
                print(
                    "[E] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
        elif server_reply in ("200", "201"):
            # 200 service available, posting permitted
            # 201 service available, posting prohibited
            if EXTREME:
                print(
                    "[INFO] [E] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
            text = CHECK_METHOD + " <" + id + ">\r\n"  # STAT is faster than HEAD
            if EXTREME:
                print(
                    "[E] Socket: " + str(i) + " " + str(host) + ", Send: " + str(text)
                )
            sock.send(text.encode("utf-8"))
        elif server_reply in ("381"):  # 381 Password required
            text = "AUTHINFO PASS %s\r\n" % (password)
            if EXTREME:
                print(
                    "[E] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
                print(
                    "[E] Socket: " + str(i) + " " + str(host) + ", Send: " + str(text)
                )
            sock.send(text.encode("utf-8"))
        elif server_reply in ("281"):  # 281 Authentication accepted
            if EXTREME:
                print(
                    "[E] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
        elif server_reply in ("211"):  # 211 group selected (group)
            if EXTREME:
                print(
                    "[INFO] [E] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
        elif server_reply in ("480"):  # 480 AUTHINFO required
            text = "AUTHINFO USER %s\r\n" % (username)
            if EXTREME:
                print(
                    "[E] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
                print(
                    "[E] Socket: " + str(i) + " " + str(host) + ", Send: " + str(text)
                )
            sock.send(text.encode("utf-8"))
        elif str(server_reply[:2]) in ("48", "50"):
            # 48X or 50X incorrect news server account settings
            print(
                "[ERROR] Socket: "
                + str(i)
                + " "
                + str(host)
                + ", Incorrect news server account settings: "
                + reply
            )
        elif server_reply in ("205"):  # NNTP Service exits normally
            sock.close()
            if EXTREME:
                print(
                    "[E] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
            if VERBOSE:
                print("[V] Socket " + str(i) + " closed.")
        elif server_reply in ("999"):  # script code for very slow news server
            if VERBOSE:
                print(
                    "[WARNING] [V] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", NNTP reply: "
                    + str(reply.split())
                )
            error = True  # article is assumed to be not there
            id_used = True
        else:
            if VERBOSE:
                print(
                    "[WARNING] [V] Socket: "
                    + str(i)
                    + " "
                    + str(host)
                    + ", Not covered NNTP server reply code: "
                    + str(reply.split())
                )
        if VERBOSE or EXTREME:
            sys.stdout.flush()
        if end_loop == False and server_reply in (
            "211",
            "221",
            "223",
            "281",
            "411",
            "420",
            "423",
            "430",
        ):
            # Send next message
            text = CHECK_METHOD + " <" + id + ">\r\n"  # STAT is faster than HEAD
            id_used = True
            if EXTREME:
                print(
                    "[E] Socket: " + str(i) + " " + str(host) + ", Send: " + str(text)
                )
            sock.send(text.encode("utf-8"))
        elif end_loop and server_reply not in ("205"):
            text = "QUIT\r\n"
            sock.send(text.encode("utf-8"))
            if EXTREME:
                print(
                    "[E] Socket: " + str(i) + " " + str(host) + ", Send: " + str(text)
                )
        if VERBOSE or EXTREME:
            sys.stdout.flush()
        return (error, id_used, server_reply, msg_id_used)
    except:
        print(
            "Exception LINE: "
            + str(traceback.print_exc())
            + ": "
            + str(sys.exc_info()[1])
        )
        return (False, False, server_reply, -1)


def fix_nzb(nzb_lines):
    """
    some nzbs may contain all data on 1 single line, to handle this
    correctly in check_nzb(), the single line is splitted on the >< mark
    """
    if VERBOSE:
        print("[V] fix_nzb(nzb_lines=" + str(nzb_lines) + ")")
        print("[V] Splitting NZB data into separate lines.")
        sys.stdout.flush()
    nzb_lines = str(nzb_lines)
    positions = [n for n in range(len(nzb_lines)) if nzb_lines.find("><", n) == n]
    first = 0
    last = 0
    corrected_lines = []
    for n in positions:
        last = n + 1
        corrected_lines.append(nzb_lines[first:last])
        first = last
    if VERBOSE:
        print("[V] Data in NZB splitted into separate lines.")
        sys.stdout.flush()
    return corrected_lines


def get_nzb_data(fname):
    """
    extract the nzb info from the NZB file, and return data set of articles
    to be checked
    """
    if VERBOSE:
        print("[V] get_nzb_data(fname=" + str(fname) + ")")
        sys.stdout.flush()
    if os.path.isfile(fname):
        file_exists = True
        fd = open(fname, encoding="utf-8")
        lines = fd.readlines()
        fd.close()
        if len(lines) == 1:  # single line NZB
            lines = fix_nzb(lines)
    else:
        file_exists = False
        print("[ERROR] No such nzb file.")
        return -1
    if file_exists:
        all_msg_ids = []  # list of message ids for NNTP server
        group = None
        groups = None
        for line in lines:
            low_line = line.lower()
            if "<segment bytes" in low_line:  # msg id
                message_id = line.split(">")[1].split("<")[0]
                ok = -1  # = no check / failed; 1,2,.. ok for server num
                all_msg_ids.append([subject, par, groups, message_id, ok])
            elif "<file" in low_line:  # look for par2 files
                subject = line.split("subject=")[1].split(">")[0]
                if ".par2" in low_line:
                    par = 1  # found a par file, next msg_ids of par2s
                else:
                    par = 0  # not a par file, next msg ids of files
            elif "<groups>" in low_line:  # set of groups
                # new list of groups found
                groups = []
            elif "<group>" in low_line:  # group name
                group = line.split(">")[1].split("<")[0]
                groups.append(group)
    if not group:
        print("[ERROR] No group found in NZB file.")
        if VERBOSE:
            print("[V] group: " + str(group))
        return -2
    if len(all_msg_ids) == 0:
        print("[ERROR] No message-ids found in NZB file")
        if VERBOSE:
            print("[V] all_msg_ids: " + str(all_msg_ids))
        return -2
    rar_msg_ids = []
    par_msg_ids = []
    for msg_id in all_msg_ids:  # split par2 from other files
        if msg_id[1] == 0:
            rar_msg_ids.append(msg_id)
        else:
            par_msg_ids.append(msg_id)
    all_articles = len(all_msg_ids)
    rar_articles = len(rar_msg_ids)
    par_articles = len(par_msg_ids)
    temp = len(rar_msg_ids)
    if temp == 0:
        # No .rar articles in NZB.
        return -3
    # check if more than 1 pars are available or not.
    if FULL_CHECK_NO_PARS and par_articles < 1:
        each = 1  # check each article
        if VERBOSE:
            print("[V] No par files in release, all articles will be " + "checked.")
    elif FULL_CHECK_NO_PARS and par_articles == 1:
        each = 1  # check each article
        if VERBOSE:
            print("[V] 1 par file in release, all articles will be " + "checked.")
    else:
        each = int(100 / CHECK_LIMIT)  # check each Xth article only
        if temp / each > MAX_ARTICLES:
            each = int(temp / MAX_ARTICLES)
            if VERBOSE:
                print(
                    "[V] Amount of to be checked articles limited to about "
                    + str(MAX_ARTICLES)
                    + " articles."
                )
        elif temp / each < MIN_ARTICLES:
            each = int(temp / MIN_ARTICLES)
            if each == 0:
                each = 1
            if VERBOSE:
                print(
                    "[V] Amount of to be checked articles increased to "
                    + "about "
                    + str(MIN_ARTICLES)
                    + " articles."
                )
    t = rar_msg_ids[::each]
    rar_msg_ids = t
    # parsing to be used ids, skipping subject parsing
    for i, rar_msg_id in enumerate(rar_msg_ids):
        rar_msg_ids[i][3] = html.unescape(rar_msg_id[3])
    articles_to_check = len(rar_msg_ids)
    if VERBOSE:
        print(
            "[V] NZB contains "
            + str(all_articles)
            + " articles, "
            + str(rar_articles)
            + " rar articles, "
            + str(par_articles)
            + " par2 articles."
        )
        print("[V] " + str(articles_to_check) + " rar articles will be checked.")
        sys.stdout.flush()
    return rar_msg_ids


def get_server_settings(nzb_age):
    """
    Get the settings for all the active news-servers in NZBGet, and store
    them in a list. Filter out all but 1 server in same group.
    """
    if VERBOSE:
        print("[V] get_server_settings(nzb_age=" + str(nzb_age) + ")")
    # get news server settings for each server
    NZBGet = connect_to_nzbget()
    nzbget_status = NZBGet.status()
    servers_status = nzbget_status["NewsServers"]
    temp = []
    servers = []
    i = 0
    skip = False
    for server_status in servers_status:
        # extract all relevant data for each server:
        s = str(server_status["ID"])  # 9
        active = os.environ["NZBOP_Server" + s + ".Active"] == "yes"  # 10
        level = os.environ["NZBOP_Server" + s + ".Level"]  # 0
        group = os.environ["NZBOP_Server" + s + ".Group"]  # 1
        host = os.environ["NZBOP_Server" + s + ".Host"]  # 2
        port = os.environ["NZBOP_Server" + s + ".Port"]  # 3
        username = os.environ["NZBOP_Server" + s + ".Username"]  # 4
        password = os.environ["NZBOP_Server" + s + ".Password"]  # 5
        encryption = os.environ["NZBOP_Server" + s + ".Encryption"] == "yes"  # 6
        connections = os.environ["NZBOP_Server" + s + ".Connections"]  # 7
        retention = os.environ["NZBOP_Server" + s + ".Retention"]  # 8
        if retention == "":
            retention = 0
        temp.append(
            [
                level,
                group,
                host,
                port,
                username,
                password,
                encryption,
                connections,
                retention,
                s,
                active,
            ]
        )
    nzb_age_days = (int(time.time()) - nzb_age) / 3600.0 / 24.0
    for server in temp:
        skip = False
        retention = float(server[8])
        # Active or not
        if server[10] == False:
            skip = True
            if VERBOSE:
                print(
                    "[V] Skipping server: "
                    + server[2]
                    + ", disabled in "
                    + "NZBGet settings."
                )
        # Server ID in SERVERS list
        elif (
            SERVERS[0] != ""
            and server[9] not in SERVERS
            and server[9] not in FILL_SERVERS
        ):
            skip = True
            if VERBOSE:
                print(
                    "[V] Skipping server: "
                    + server[2]
                    + ", not listed "
                    + "as Server or FillServer in script settings."
                )
        # Server ID in FILL_SERVERS list and nzb older than AGE_LIMIT
        elif server[9] in FILL_SERVERS and nzb_age_days * 24.0 < AGE_LIMIT:
            skip = True
            if VERBOSE:
                print(
                    "[V] Skipping Fill server: "
                    + server[2]
                    + ", NZB age of "
                    + str(round(nzb_age_days * 24.0, 1))
                    + " hours within AgeLimit of "
                    + str(AGE_LIMIT)
                    + " hours"
                )
        # Server retention lower than nzb age
        if retention < nzb_age_days and retention != 0:
            skip = True
            if VERBOSE:
                print(
                    "[V] Skipping server: "
                    + server[2]
                    + ", retention of "
                    + str(retention)
                    + " days is less than NZB age of "
                    + str(round(nzb_age_days, 1))
                    + " days."
                )
        # removing all to be skipped servers
        if skip == False:
            servers.append(server)
    if VERBOSE:
        print(
            "[V] All news servers after filtering on Active, Servers, "
            + "FillServers + AgeLimit and Retention, "
            + " BEFORE filtering on NZBGet ServerX.Group: "
        )
        for server in servers:
            print(
                "[V] * "
                + str(server[2])
                + ":"
                + str(server[3])
                + ", SSL: "
                + str(server[6])
                + ", connections: "
                + str(server[7])
            )
    # sort on groups, followed by lvl, so that all identical group numbers > 0
    # can be removed
    servers.sort(key=itemgetter(1, 0))
    a = None
    c = []
    # remove all identical groups from server:
    for server in servers:
        b = int(server[1])
        # only allow 1 server per group
        if a != b:
            c.append(server)
        # guarantee that all group 0 (no group) servers remain
        if b > 0:
            a = b
    servers = c
    if VERBOSE:
        print(
            "[V] All active news servers AFTER filtering and sorting "
            + "on NZBGet ServerX.Group:"
        )
        for server in servers:
            print(
                "[V] * "
                + str(server[2])
                + ":"
                + str(server[3])
                + ", SSL: "
                + str(server[6])
                + ", connections: "
                + str(server[7])
            )
    if servers == []:
        print(
            "[WARNING] No news servers after filtering, marking NZB as"
            + " FAILED or BAD. May run in Verbose mode and check your settings!"
        )
    return servers


def create_sockets(server, articles_to_check):
    """
    create the sockets for the server that will be used to send in
    check_send_server_reply() and receive in check_failure_status()
    server dependent sockets, ssl / non ssl
    """
    if EXTREME:
        print(
            "[E] create_sockets(server="
            + str(server)
            + ",articles_to_check= "
            + str(articles_to_check)
        )
    server_no = -1
    conn_err = 0
    server_no += 1
    host = server[2]
    port = int(server[3])
    encryption = server[6]  # ssl
    num_conn = int(server[7])
    start_sock = 0
    end_sock = num_conn
    if end_sock >= articles_to_check:
        # avoiding making more sockets than headers that need to be checked.
        num_conn = int(articles_to_check / 2.0 + 0.5)
        end_sock = num_conn
        if VERBOSE:
            print(
                "[V] Limiting the number of sockets to "
                + str(end_sock)
                + " to keep the number of sockets below the number of articles"
            )
    sockets = [None] * num_conn
    failed_sockets = [-1] * num_conn
    if VERBOSE:
        print("[V] Creating sockets for server: " + host)
        sys.stdout.flush()
    try:
        # check if we *must* use IPv6 for this host
        for res in sorted(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)):
            # sorted so IPv4 will be first
            af, socktype, proto, canonname, sa = res
            if af == socket.AF_INET or af == socket.AF_INET6:
                break
        if af == socket.AF_INET6:
            if VERBOSE:
                print("[V] Using IPv6 for " + host)
                sys.stdout.flush()
        else:
            af = socket.AF_INET  # Default to IPv4, even if unexpected response
            if VERBOSE:
                print("[V] Using IPv4 for " + host)
                sys.stdout.flush()
        # create connections
        if encryption:
            context = ssl.create_default_context()

            for i in range(start_sock, end_sock):
                s = socket.socket(af, socket.SOCK_STREAM)
                try:
                    sockets[i] = context.wrap_socket(s, server_hostname=host)
                except ssl.SSLError as e:
                    print(
                        f"[WARNING] Error creating SSL connection for socket {i}: {e}"
                    )
                    failed_sockets[i] = i
        else:
            # Non SSL
            for i in range(start_sock, end_sock):
                sockets[i] = socket.socket(af, socket.SOCK_STREAM)
        for i in range(start_sock, end_sock):
            # set timeout for trying to connect (e.g. wrong port config)
            sockets[i].settimeout(NNTP_TIME_OUT)
            try:
                sockets[i].connect((host, port))
                if VERBOSE:
                    print("[V] Socket " + str(i) + " created.")
                    sys.stdout.flush()
                # remove time out, so socket is closed after completed message
                sockets[i].settimeout(0)
                # some minor delay to not hammer / create connection time outs
                time.sleep(SOCKET_CREATE_INTERVAL)
            except Exception as e:
                print(
                    "[WARNING] Socket: "
                    + str(i)
                    + " "
                    + str(e)
                    + ", check host, port and number of connections settings"
                    + " for server "
                    + host
                )
                sys.stdout.flush()
                failed_sockets[i] = i
                conn_err += 1
                continue  # for i
        if queue_time != -1:
            req_wait = queue_time + 5 - time.time() + SOCKET_LOOP_INTERVAL
            if req_wait > 0:
                if VERBOSE:
                    print(
                        "[V] Waiting "
                        + str(round(req_wait, 2))
                        + " sec "
                        + "while NZBGet closes its news server connections."
                    )
                    sys.stdout.flush()
                time.sleep(
                    req_wait
                )  # NZBGet sends QUIT after 5 seconds of innactivity (of a particular connection).
        if conn_err >= num_conn:
            print("[ERROR] Creation of all sockets for server " + host + " failed.")
    except:
        print(
            (
                "Exception LINE: "
                + str(traceback.print_exc())
                + ": "
                + str(sys.exc_info()[1])
            )
        )
    return (sockets, failed_sockets, conn_err)


def check_failure_status(rar_msg_ids, failed_limit, nzb_age):
    """
    Get the failed_ratio for each news server, if nth server failed_ratio
    below failed_limit, return ok failure ratio for resuming
    """
    if EXTREME:
        print(
            "[E] check_failure_status(rar_msg_ids="
            + str(rar_msg_ids)
            + ", failed_limit="
            + str(failed_limit)
            + ")"
        )
    articles_to_check = len(rar_msg_ids)
    # message on each 25 %
    message_on = [
        1,
        int(articles_to_check * failed_limit * 0.01),
        int(articles_to_check * 0.25),
        int(articles_to_check * 0.50),
        int(articles_to_check * 0.75),
        int(articles_to_check),
    ]
    servers = get_server_settings(nzb_age)  # get news server provider settings
    if servers == []:
        return 100
    num_server = 0
    global end_loop
    # looping through servers, until limited failure
    failed_ratio = 0
    for server in servers:
        if failed_ratio > MAX_FAILURE and MAX_FAILURE != 0:
            print("[WARNING] failure ratio > MaxFailure.")
            break
        host = server[2]
        username = server[4]
        password = server[5]
        num_conn = int(server[7])
        start_sock = 0
        end_sock = num_conn
        if end_sock >= articles_to_check:
            # avoiding making more sockets than headers that need to be checked.
            num_conn = int(articles_to_check / 2.0 + 0.5)
            end_sock = num_conn
        failed_articles = 0
        send_articles = 0
        end_loop = False
        socket_loop_count = [-1] * num_conn
        failed_wait_count = 0
        loop_fail = False
        chunk = 4096
        start_time = time.time()
        print("Using server: " + host)
        sys.stdout.flush()
        # build the (non) ssl sockets per server
        (sockets, failed_sockets, conn_err) = create_sockets(server, articles_to_check)
        if conn_err >= num_conn:
            print("[WARNING] Skipping server: " + host)
            num_server += 1
            failed_ratio = 100
            continue
        failed_ratio = 0
        num_server += 1
        # filtering failed sockets
        socket_list = []
        for i in range(start_sock, end_sock):
            if len(failed_sockets) > 0:
                if i not in failed_sockets:
                    socket_list.append(i)
            else:
                socket_list.append(i)
        num_conn = len(socket_list)
        # loop through all rar_msg_ids, check each one if available
        # if to much failed for server, skip check and move to next
        # send_articles has range 0 to x-1, while articles to check = x
        while (
            send_articles <= articles_to_check - 1
            and (failed_ratio < failed_limit or failed_ratio == 0)
            or send_articles <= articles_to_check - 1
            and (failed_ratio < MAX_FAILURE or failed_ratio == 0)
        ):
            # check each connection for data receive
            if loop_fail:  # exit while loop after looping
                failed_ratio = 100
                break
            for i in socket_list:  # loop through ok sockets
                reply = None
                # break looping through sockets when already finished
                if send_articles > articles_to_check - 1:
                    break
                try:
                    reply = sockets[i].recv(chunk).decode("utf-8")
                except:  # each error would trigger the same effect
                    # avoid continuous looping on fast machines by adding delays
                    #            EAGAIN, EWOULDBLOCK, ssl.SSLWantReadError
                    #            socket.timeout:  # catching timeout for non SSL in 2.7.9+
                    #            # https://bugs.python.org/issue10272
                    # managing all possible socket errors
                    err = sys.exc_info()
                    if socket_loop_count[i] < 5:
                        if EXTREME:
                            print(
                                "[E] Socket: "
                                + str(i)
                                + " "
                                + str(err[0])
                                + " "
                                + str(err[1])
                            )
                            print(
                                "[E] Socket: "
                                + str(i)
                                + " Failed to "
                                + "get complete reply from server, waiting "
                                + str(int(SOCKET_LOOP_INTERVAL * 1000 / num_conn))
                                + " ms to avoid looping."
                            )
                            sys.stdout.flush()
                        time.sleep(SOCKET_LOOP_INTERVAL / num_conn)
                        socket_loop_count[i] += 1
                        continue
                    if socket_loop_count[i] == 5:
                        if VERBOSE:
                            print(
                                "[V] Socket: "
                                + str(i)
                                + " No data "
                                + "received on 5th retry, pausing script for 2 sec."
                            )
                            sys.stdout.flush()
                        socket_loop_count[i] += 1
                        time.sleep(2)
                        continue
                    elif socket_loop_count[i] >= 5:
                        if VERBOSE:
                            print(
                                "[V] Socket: "
                                + str(i)
                                + " "
                                + str(err[0])
                                + " "
                                + str(err[1])
                            )
                            print(
                                "[V] Socket: "
                                + str(i)
                                + " Still no data "
                                + "received after waiting for 1 sec, "
                                + " marking requested article as failed."
                            )
                            sys.stdout.flush()
                        reply = "999 Article marked as failed by script."
                        failed_wait_count += 1
                        if failed_wait_count >= 20:
                            print(
                                "[WARNING] Skipping current server as "
                                + "it is replying very slow on header "
                                + "requests for this NZB file"
                            )
                            loop_fail = True
                            break  # exit for i loop
                        pass
                else:
                    # normal reply received
                    socket_loop_count[i] += 1
                # reply will be empty string when error
                if reply != None and rar_msg_ids[send_articles][4] > -1:
                    socket_loop_count[i] = 0
                    # loop over ok articles on previous servers
                    while (
                        send_articles < articles_to_check - 1
                        and rar_msg_ids[send_articles][4] > -1
                    ):
                        if EXTREME:
                            print(
                                "[E] Article "
                                + str(send_articles)
                                + " already checked and available on server "
                                + servers[rar_msg_ids[send_articles][4] - 1][2]
                            )
                        send_articles += 1
                        if send_articles in message_on:
                            print(
                                "Requested ["
                                + str(send_articles)
                                + "/"
                                + str(articles_to_check)
                                + "] articles, "
                                + str(failed_articles)
                                + " failed."
                            )
                            sys.stdout.flush()
                # msg received, and msg not checked/ok yet, and not all
                # articles send:
                if reply != None and rar_msg_ids[send_articles][4] == -1:
                    socket_loop_count[i] = 0
                    id = rar_msg_ids[send_articles][3]
                    groups = rar_msg_ids[send_articles][2]
                    group = groups[0]  # might not sufficient for cross posts
                    (error, id_used, server_reply, msg_id_used) = (
                        check_send_server_reply(
                            sockets[i], reply, group, id, i, host, username, password
                        )
                    )
                    if id_used and error:
                        # ID of missing article is not returned by server
                        failed_articles += 1
                    # found ok article on server, store success:
                    if id_used and not error and server_reply == "223":
                        # find row index for successfully send article
                        # (with reply)
                        for j, rar_msg_id in enumerate(rar_msg_ids):
                            if msg_id_used == rar_msg_id[3]:
                                # store success serv num
                                rar_msg_ids[j][4] = num_server
                                break  # for j loop
                    if id_used:  # avoids removing ids send before AUTH etc
                        # rar_msg_ids starts with base 0
                        send_articles += 1
                        if send_articles in message_on:
                            print(
                                "Requested ["
                                + str(send_articles)
                                + "/"
                                + str(articles_to_check)
                                + "] articles, "
                                + str(failed_articles)
                                + " failed."
                            )
                            sys.stdout.flush()
                failed_ratio = failed_articles * 100.0 / articles_to_check
        # loop through all sockets, to catch the last server replies
        # without sending new STAT messages, and allowing sockets to close
        end_loop = True
        end_count = 0
        # loop over all sockets to try to catch all remaining replies
        if EXTREME:
            print("[E] Receiving remaining replies:")
        # Start first loop after last socket used for receive to avoid errors
        m = socket_list.index(i)
        for k in range(0, 8):  # loop multiple so all data will be received
            for i in socket_list[m:]:  # loop through ok sockets
                reply = None
                try:
                    reply = sockets[i].recv(chunk).decode("utf-8")
                except:  # managing all socket errors
                    err = sys.exc_info()
                    if socket_loop_count[i] < 5:
                        if EXTREME:
                            print(
                                "[E] Socket: "
                                + str(i)
                                + " "
                                + str(err[0])
                                + " "
                                + str(err[1])
                            )
                            print(
                                "[E] Socket: "
                                + str(i)
                                + " Failed to "
                                + "get complete reply from server, waiting "
                                + str(int(SOCKET_LOOP_INTERVAL * 1000 / num_conn))
                                + " ms to avoid looping."
                            )
                            sys.stdout.flush()
                        time.sleep(SOCKET_LOOP_INTERVAL / num_conn)
                        socket_loop_count[i] += 1
                        continue
                    if socket_loop_count[i] == 5:
                        if VERBOSE:
                            print(
                                "[V] Socket: "
                                + str(i)
                                + ", no data "
                                + "received on 5th retry, pausing script for 1 sec."
                            )
                            sys.stdout.flush()
                        socket_loop_count[i] += 1
                        time.sleep(1)
                        continue
                    elif socket_loop_count[i] >= 5:
                        if VERBOSE:
                            print(
                                "[V] Socket: "
                                + str(i)
                                + " "
                                + str(err[0])
                                + " "
                                + str(err[1])
                            )
                            print(
                                "[V] Socket: "
                                + str(i)
                                + " Still no data "
                                + "received after waiting for 1 sec, "
                                + " marking request as failed."
                            )
                            sys.stdout.flush()
                        reply = "999 request marked as failed by script."
                        pass
                if reply != None:
                    socket_loop_count[i] = 0
                    (error, id_used, server_reply, msg_id_used) = (
                        check_send_server_reply(
                            sockets[i],
                            reply,
                            group,
                            id,
                            i,
                            host,
                            username,
                            password,
                        )
                    )
                    if error and server_reply in ("411", "420", "423", "430"):
                        # ID of missing article is not returned by server
                        failed_articles += 1
                        end_count += 1
                        if end_count >= num_conn:
                            print(
                                "All requested replies received, "
                                + str(failed_articles)
                                + " failed."
                            )
                    # found ok article on server, store success:
                    elif not error and server_reply == "223":
                        # find row index for successfully send article
                        # (with recv reply)
                        for j, rar_msg_id in enumerate(rar_msg_ids):
                            if msg_id_used == rar_msg_id[3]:
                                # store success serv num
                                rar_msg_ids[j][4] = num_server
                                break  # for j loop
                        end_count += 1
                        if end_count >= num_conn:
                            print(
                                "All requested article replies received, "
                                + str(failed_articles)
                                + " failed."
                            )
                    elif not error and server_reply == "205":
                        # socket closed in check_send_server_reply
                        socket_list.remove(i)
                if failed_ratio != 100:
                    failed_ratio = failed_articles * 100.0 / articles_to_check
            m = 0
            if len(socket_list) == 0:
                break  # for k loop
        if len(socket_list) > 0:  # kill still open sockets
            for i in socket_list:
                try:
                    sockets[i].send("QUIT\r\n")
                    sockets[i].close
                except:
                    continue
            time.sleep(
                SOCKET_LOOP_INTERVAL
            )  # waiting and pray the connection will be closed
        print(
            "Failed ratio for server: "
            + host
            + ": "
            + str(round(failed_ratio, 1))
            + "%. Server check completed in "
            + str(round(time.time() - start_time, 2))
            + " sec."
        )
        if failed_ratio < failed_limit or failed_ratio == 0:  # ok on last provider
            break
    return failed_ratio


def lock_file():
    """
    This function checks if the .lock file is there, if it is created
    before or after a restart of NZBGet. This prevents the script from
    running twice at the same time. It returns True when there is a valid
    .lock file, otherwise it will return false and create one.
    """
    if VERBOSE:
        print("[V] lock_file()")
    NZBGet = connect_to_nzbget()
    nzbget_status = NZBGet.status()  # Get NZB status info XML-RPC
    server_time = nzbget_status["ServerTime"]
    up_time = nzbget_status["UpTimeSec"]
    tmp_path = os.environ["NZBOP_TEMPDIR"] + os.sep + "completion"
    try:
        os.makedirs(tmp_path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(tmp_path):
            pass
        else:
            raise
    f_name = tmp_path + os.sep + "completion.lock"
    file_exists = os.path.isfile(f_name)
    if file_exists:
        fd = open(f_name, encoding="utf-8")
        time_stamp = int(fd.readline())
        if VERBOSE:
            print(
                "[V] time_stamp from completion.lock file= " + str(time_stamp)
            )  ## added for debug issue reported by blackhawkpr, probably no time stamp in lock file
        fd.close()
        # Check if the .lock file was created before or after the last restart
        if server_time - up_time > time_stamp:
            # .lock created before restart, overwrite .lock file time_stamp
            fd = open(f_name, encoding="utf-8", mode="w")
            fd.write(str(server_time))
            fd.close()
            if VERBOSE:
                print("[V] Old completion.lock file overwritten.")
            return False
        # Check if the .lock file is not older than 30 minutes
        elif server_time - 1800 > time_stamp:
            print(
                "[ERROR] Script seems to be running for more than 30 "
                + "minutes and has most likely crashed. Check your logs and "
                "report the log and errors at "
                + "https://github.com/nzbgetcom/Extension-Completion/issues"
            )
            # overwrite .lock file time_stamp
            fd = open(f_name, encoding="utf-8", mode="w")
            fd.write(str(server_time))
            fd.close()
            if VERBOSE:
                print("[V] Existing completion.lock file overwritten.")
            nzbget_resume()
            return False
        else:
            # .lock created after restart, script is running at this moment
            # don't start script
            if VERBOSE:
                print("[V] Script is already running, check canceled.")
            return True
    else:
        fd = open(f_name, encoding="utf-8", mode="w")
        fd.write(str(server_time))
        fd.close()
        if VERBOSE:
            ## added for debug issue reported by blackhawkpr
            print("[V] server_time= " + str(server_time))
            print("[V] New completion.lock file created.")
        return False


def del_lock_file():
    """
    Delete the .lock file
    """
    if VERBOSE:
        print("[V] del_lock_file()")
    f_name = os.path.join(os.environ["NZBOP_TEMPDIR"], "completion", "completion.lock")
    os.remove(f_name)
    if VERBOSE:
        print("[V] completion.lock file deleted")


def nzbget_paused():
    """
    Pause NZBGet if not already paused, when paused don't start the check.
    give the NZBGet sockets some time to close the connections, and avoid
    48X warnings on number of connections.
    """
    if VERBOSE:
        print("[V] nzbget_paused()")
    NZBGet = connect_to_nzbget()
    nzbget_status = NZBGet.status()
    nzbget_paused = nzbget_status["DownloadPaused"]
    if nzbget_paused:
        paused = True
    else:
        paused = False
        nzbget_status = NZBGet.status()
        download_rate = nzbget_status["DownloadRate"]
        NZBGet.pausedownload()  # pause downloading in NZBGet
        if VERBOSE:
            print("[V] Waiting for NZBGet to end downloading")
            sys.stdout.flush()
        while download_rate > 0:  # avoid double use of connections
            if VERBOSE:
                print(
                    "[V] Download rate: "
                    + str(round(download_rate / 1000.0, 1))
                    + " kB/s, waiting 1 sec to stop downloading"
                )
                sys.stdout.flush()
            time.sleep(1)  # let the connections cool down 1 sec
            nzbget_status = NZBGet.status()
            download_rate = nzbget_status["DownloadRate"]
            if download_rate == 0:
                if VERBOSE:
                    print(
                        "[V] Waiting 5 sec while NZBGet closes the news "
                        + "server connections."
                    )
                    sys.stdout.flush()
                time.sleep(
                    5
                )  # NZBGet sends QUIT after 5 seconds of innactivity (of a particular connection).
        if VERBOSE:
            print("[V] Downloading for NZBGet paused")
            sys.stdout.flush()
    return paused


def nzbget_resume():
    """
    Resume NZBGet
    """
    if VERBOSE:
        print("[V] nzbget_resume()")
    NZBGet = connect_to_nzbget()
    NZBGet.resumedownload()  # resume downloading in NZBGet
    if VERBOSE:
        print("[V] Downloading for NZBGet resumed")


def get_prio_nzb(jobs, paused_jobs):
    """
    Get queue data from NZBGet marked paused_jobs in scan_call, sort data based
    on priority and age (oldest first, less chance of propagation, bigger
    chance it will be DMCAed. Check the first item in sorted queue, if file
    is incomplete, check next item etc. Only resume first succesfull file.
    """
    if EXTREME:
        print("[E] get_prio_nzb(paused_jobs=")
        for job in paused_jobs:
            print("[E] " + str(job))
    start_time = time.time()
    do_check = False
    if not IGNORE_QUEUE_PRIORITY:
        max_queued_priority = -1.7976931348623157e308
        # check if something is downloading, loop through jobs, extract max priority
        # of DOWNLOADING / QUEUED items
        for job in jobs:
            if job["Status"] in ("DOWNLOADING", "QUEUED"):
                nzb_priority = job["MaxPriority"]
                if nzb_priority > max_queued_priority:
                    max_queued_priority = nzb_priority
        if VERBOSE and max_queued_priority != -1.7976931348623157e308:
            print(
                "[V] Maximum priority of DOWNLOADING / QUEUED NZBs = "
                + str(max_queued_priority)
            )
        for job in paused_jobs:
            nzb_priority = job["MaxPriority"]
            if nzb_priority > max_queued_priority:
                do_check = True
                if VERBOSE and max_queued_priority != -1.7976931348623157e308:
                    print(
                        (
                            "[V] QUEUED / DOWNLOADING NZBs have lower priority "
                            + "than by script paused items, starting check"
                        )
                    )
                break
            else:
                do_check = False
        if do_check == False and max_queued_priority != -1.7976931348623157e308:
            if VERBOSE:
                print(
                    "[V] QUEUED / DOWNLOADING NZBs have higher or equal "
                    + "priority than by script paused items, skipping check"
                )
    else:
        do_check = True
        print("[V] Ignoring priority of existing items")
    if do_check:
        paused = nzbget_paused()  # check if NZBGet is paused, +pause NZBGet
        if paused:  # NZBGet is paused by user, no check
            if VERBOSE:
                print("[V] Not started because download is paused")
    if do_check and not paused:
        if VERBOSE:
            print("[V] Paused UNSORTED NZBs in queue that will be processed:")
            for job in paused_jobs:
                nzb_filename = get_nzb_filename(job["Parameters"])
                print(
                    "[V] * "
                    + str(nzb_filename)
                    + ", Age: "
                    + str(round((int(time.time()) - job["MaxPostTime"]) / 3600.0, 1))
                    + " hours, Priority: "
                    + str(job["MaxPriority"])
                )
        # sort on nzb age, but move older than max-age to bottom, then
        # sort of priority. Priority items will be on top.
        if VERBOSE:
            print(
                "[V] Ignoring sorting priority of items older than "
                + "AgeSortLimit of "
                + str(AGE_SORT_LIMIT)
                + " hours"
            )
        max_age = int(time.time()) - int(AGE_SORT_LIMIT_SEC)
        t1 = sorted(
            (j for j in paused_jobs if float(j["MaxPostTime"]) >= max_age),
            key=itemgetter("MaxPostTime"),
        )
        t2 = []
        for j in paused_jobs:
            if float(j["MaxPostTime"]) < max_age:
                t2.append(j)
        for t in t2:
            t1.append(t)
        jobs_sorted = sorted(t1, key=itemgetter("MaxPriority"), reverse=True)
        if VERBOSE:
            print("[V] Paused and SORTED NZBs in queue that will be processed:")
            for job in jobs_sorted:
                nzb_filename = get_nzb_filename(job["Parameters"])
                print(
                    "[V] * "
                    + str(nzb_filename)
                    + ", Age: "
                    + str(round((int(time.time()) - job["MaxPostTime"]) / 3600.0, 1))
                    + " hours, Priority: "
                    + str(job["MaxPriority"])
                )
        for job in jobs_sorted:
            nzb_filename = get_nzb_filename(job["Parameters"])
            nzb_id = job["NZBID"]
            nzb_age = job["MaxPostTime"]  # nzb age
            nzb_critical_health = job["CriticalHealth"]
            nzb_dupe_key = job["DupeKey"]  # if empty returns u''
            if nzb_dupe_key == "":
                nzb_dupe_key = "NONE"
            nzb_dupe_score = job["DupeScore"]
            nzb = [
                nzb_id,
                nzb_filename,
                nzb_age,
                nzb_critical_health,
                nzb_dupe_key,
                nzb_dupe_score,
            ]
            # do a completion check, returns true if ok and resumed
            if get_nzb_status(nzb):
                break
        print(
            "Overall check completed in "
            + str(round(time.time() - start_time, 2))
            + " sec."
        )
        nzbget_resume()


def scheduler_call():
    """
    Script is called as scheduler script
    check if files in the queue should be checked by the completion script
    """
    global queue_time
    queue_time = -1  # NZBGet closes connection after 5 sec. Avoid too much conn
    if VERBOSE:
        print("[V] scheduler_call()")
    # data contains ALL properties each NZB in queue
    data = call_nzbget_direct("listgroups")
    jobs = json.loads(data)
    # check if nzb in queue, and check if paused by this script
    if len(jobs["result"]) > 0 and "CnpNZBFileName" in str(jobs):
        if not lock_file():  # check if script is not already running
            paused_jobs = []
            for job in jobs["result"]:
                # send only nzbs paused by the script
                if "CnpNZBFileName" in str(job) and job["Status"] in ("PAUSED"):
                    paused_jobs.append(job)
            if len(paused_jobs) > 0:
                get_prio_nzb(jobs["result"], paused_jobs)
            del_lock_file()
    elif VERBOSE:
        print("[V] Empty queue")


def queue_call():
    """
    Script is called as queue script
    check if new files in queue should be checked by the completion script
    Option NZBGet EventInterval set to -1 avoids script being called each
    time a part is donwloaded.
    """
    global queue_time
    queue_time = -1
    if VERBOSE:
        print("[V] queue_call()")
    print(os.environ["NZBNA_QUEUEDFILE"])
    # check if NZB is added, otherwise it will call on each downloaded part
    event = os.environ["NZBNA_EVENT"]
    if (
        event == "NZB_ADDED"
        or event == "NZB_DOWNLOADED"
        or event == "NZB_DELETED"
        or event == "NZB_MARKED"
    ):
        # when NZB_DOWNLOADED occurs, the NZB is still in queue, with the
        # paused par2 etc.
        # data contains ALL properties each NZB in queue
        data = call_nzbget_direct("listgroups")
        jobs = json.loads(data)
        # check if nzb in queue, and check if paused by this script
        if len(jobs["result"]) > 0 and "CnpNZBFileName" in str(jobs):
            if not lock_file():  # check if script is not already running
                paused_jobs = []
                for job in jobs["result"]:
                    # send only nzbs paused by the script
                    if "CnpNZBFileName" in str(job) and job["Status"] in ("PAUSED"):
                        paused_jobs.append(job)
                if len(paused_jobs) > 0:
                    if event == "NZB_DOWNLOADED":
                        queue_time = time.time()
                    get_prio_nzb(jobs["result"], paused_jobs)
                del_lock_file()


def scan_call():
    """
    Script is called as scan script. This part of the script pauses the NZB
    and marks the file as paused by the script. Files not paused by the
    script won't be checked on completion.
    NZBGet doesn't provide the actual name of the file when in the queue.
    if 2 same filename items appear at the same time the 2nd file wiil be
    _2.nzb.queued and if 2 items are added after eachoter, they will be
    nzb.queued and nzb.2.queued. NZBGet does not provide the _2. or .2. in
    e.g. queue, scheduler calls, 'listgroups' or 'history'. The scan script
    adds the NZBPR_CnpNZBFileName variable to know the exact file name, and
    uses it to recognize if a nzb is paused by the script.
    """
    if VERBOSE:
        print("[V] scan_call()")
    # Check if NZB should be paused.
    if os.environ["NZBNP_CATEGORY"].lower() in CATEGORIES or CATEGORIES[0] == "":
        # NZBNP_FILENAME needs to be written to other NZBPR_var for later use.
        nzb_filename = os.environ["NZBNP_FILENAME"]
        nzb_dir = os.environ["NZBOP_NZBDIR"]
        if nzb_dir[-1:] == "\\" or nzb_dir[-1:] == "/":
            print(
                "[WARNING] Please correct your NZBGet PATHS Settings "
                + 'by removing the trailing "'
                + str(os.sep)
                + '"!'
            )
        if os.sep == "\\":  # windows \
            if nzb_dir.find("/") != -1:
                print(
                    "[WARNING] Please correct your NZBGet PATHS Settings "
                    + 'using "'
                    + str(os.sep)
                    + '" only!'
                )
        else:  # nix
            if nzb_dir.find("\\") != -1:
                print("nix")
                print(
                    "[WARNING] Please correct your NZBGet PATHS Settings "
                    + 'using "'
                    + str(os.sep)
                    + '" only!'
                )
        nzb_filename = nzb_filename.replace(nzb_dir + os.sep, "")
        l_nzb = len(nzb_filename)  # length for file matching, with .nzb ext
        c = 0
        dupe_list_num = []
        for file in os.listdir(nzb_dir):
            if file.endswith(".queued") and file[:l_nzb] == nzb_filename:
                # found file with same file name + .nzb
                # count identical files
                c += 1
                # extract possible number between .nzb. and .queued
                dupe_num = file[file.rfind(".nzb.") + 5 : -7]
                dupe_list_num.append(dupe_num)
        if c > 0:  # already 1 file with same name in queue/history
            if VERBOSE:
                print(
                    "[V] Found "
                    + str(c)
                    + " queued / history nzb with identical name: "
                    + nzb_filename
                )
            # num between .nzb. .queued is lowest num not in dupe_list_num
            for x in range(1, c + 1):
                if x == 1:
                    t = ""
                else:
                    t = str(x)
                if t not in dupe_list_num:
                    if t == "":
                        nzb_filename = nzb_filename + ".queued"
                    else:
                        nzb_filename = nzb_filename + "." + t + ".queued"
                    break
                elif c == 1 or c == x:
                    t = str(x + 1)
                    nzb_filename = nzb_filename + "." + t + ".queued"
                # else: nothing
        else:  # no identical file names
            nzb_filename = nzb_filename + ".queued"
        if VERBOSE:
            print('[V] Expected queued file name: "' + nzb_filename + '"')
        print("[NZB] NZBPR_CnpNZBFileName=" + nzb_filename)
        # pausing NZB
        if VERBOSE:
            print('[V] Pausing: "' + os.environ["NZBNP_NZBNAME"] + '"')
        print("[NZB] PAUSED=1")


def main():
    """
    Check for which script type the script is called
    """
    # check if the script is called as Scheduler Script
    if "NZBSP_TASKID" in os.environ:
        scheduler_call()
    # Check if the script is called as Queue Script.
    if "NZBNA_NZBNAME" in os.environ:
        queue_call()
    # check if the script is called as Scan Script
    if "NZBNP_NZBNAME" in os.environ:
        scan_call()
    # check if the script is called via button
    if "NZBCP_COMMAND" in os.environ:
        scheduler_call()
        sys.exit(93)


def write_to_file(input):
    """
    For testing purposes only
    """
    tmp_path = os.environ["NZBOP_TEMPDIR"] + os.sep + "completion"
    try:
        os.makedirs(tmp_path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(tmp_path):
            pass
        else:
            raise
    f_name = tmp_path + os.sep + "log.txt"
    fd = open(f_name, encoding="utf-8", mode="a")
    fd.write(str(input) + "\n\n")
    fd.close()


main()

""" 
TODO:
    - User blackhawkpr had an issue related to a wrong or missing time stamp in
      .lock file, can not reproduce, added additinal logging for it in VERBOSE 
      logging.
    - HEAD will fails as it does not check if all packets are received before 
      asking for next HEAD. (Complete HEAD data ends with a .) HEAD 
      implementation for python 3 only. STAT always returns 1 packet
      http://code.activestate.com/recipes/408859/

    ADDED/FIXED:
    - Fixed an issue in where a filename might contained a } sign, resulting in
      the no such nzb file message. Issue reported by user barenaked.
    - Added check for correct python version.
    - Removed Prioritize option
    - Added option AgeSortLimit
    - Added the option to run a scheduler check manually, using the button 
      option introduced in NZBget v19, works only in NZBget 19+
    - Added check on correct use of path separators, if incorrect a warning
      message will be shown.

Script structure:
- main() -> scan / queue / schedule / button call
- scan -> pause typical incoming NZBs
- queue / schedule / button -> start whole completion check loop, get queue data list
    - lock_file() -> check if not running, otherwise create lock file
    - get_prio_nzb() -> sent highest prio / oldest within to check
        - nzbget_paused() -> check if NZBGet not paused, pause NZBGet for check
        - get_nzb_status() -> handle results of article check: resume / keep
          paused / mark bad / mark failed
            - get_nzb_data() -> extract the data from the nzb
                - fix_nzb() -> fix 1 line nzbs
            - check_failure_status() -> recv messages
                - get_server_settings() -> extract NZBGet server info
                - create_sockets() -> build sockets
                - check_send_server_reply() -> check recv messages, article 
                  ok/nok,
                    login, send messages.
                    - is_number() -> check if a str is a number
            - unpause_nzb() -> resume nzb if requested
            - mark_bad() -> mark nzb bad
            - force_failure() -> force a failure of nzb
            - get_dupe_nzb_status()
                - unpause_nzb_dupe() return dupe into queue
                - mark_bad_dupe() mark dupe nzb bad
                - force_failure_dupe() force nzb bad while returning to queue
        - nzbget_resume() -> resume NZBGet if paused by nzbget_paused()
    - del_lock_file -> delete created lock file.

- connect_to_nzbget() -> connection to get data from NZBGet
- call_nzbget_direct() -> connect and get data from NZBGet
"""
