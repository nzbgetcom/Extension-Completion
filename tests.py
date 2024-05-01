#
# Copyright (C) 2024 Denis <denis@nzbget.com>
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with the program.  If not, see <https://www.gnu.org/licenses/>.
#

import json
import unittest
import sys
import ssl
import socket
from os.path import dirname
import os
import subprocess

SUCCESS = 93
NONE = 95
ERROR = 94

ROOT = dirname(__file__)
HOST = "127.0.0.1"
USERNAME = "TestUser"
PASSWORD = "TestPassword"
PORT = "6789"


def get_python():
    if os.name == "nt":
        return "python"
    return "python3"


def run_script():
    sys.stdout.flush()
    proc = subprocess.Popen(
        [get_python(), ROOT + "/main.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy(),
    )
    out, err = proc.communicate()
    proc.pid
    ret_code = proc.returncode
    return (out.decode(), int(ret_code), err.decode())


def set_defaults_env():
    # NZBGet global options
    os.environ["NZBOP_CONTROLPORT"] = PORT
    os.environ["NZBOP_CONTROLIP"] = HOST
    os.environ["NZBOP_CONTROLUSERNAME"] = USERNAME
    os.environ["NZBOP_CONTROLPASSWORD"] = PASSWORD


# class Tests(unittest.TestCase):

#     def test_connection(self):
#         host = "news.astraweb.com"
#         port = 563
#         context = ssl.create_default_context()
#         s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#         socket = context.wrap_socket(s, server_hostname=host)
#         socket.connect((host, port))

#     def test_manifest(self):
#         with open(ROOT + "/manifest.json", encoding="utf-8") as file:
#             try:
#                 json.loads(file.read())
#             except ValueError as e:
#                 self.fail("manifest.json is not valid.")


# if __name__ == "__main__":
#     unittest.main()
try:
    host = "news.astraweb.com"
    port = 563
    NNTP_TIME_OUT = 2
    context = ssl.create_default_context()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock = context.wrap_socket(s, server_hostname=host)
    sock.settimeout(NNTP_TIME_OUT)
    sock.connect((host, port))
    sock.settimeout(0.1)
    reply = ""
    chunk = 4098
    try:
        reply = sock.recv(chunk).decode("utf-8")
        print(reply)
    except Exception as e:
        print("recv error:", e)
except Exception as e:
    print(e)
