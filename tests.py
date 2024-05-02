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

import http
import json
import threading
import unittest
import sys
import ssl
import socket
from os.path import dirname
import os
import subprocess
from xmlrpc.server import XMLRPCDocGenerator

SUCCESS = 93
NONE = 95
ERROR = 94

ROOT = dirname(__file__)
TMP_DIR = ROOT.join("tmp")
HOST = "127.0.0.1"
USERNAME = "TestUser"
PASSWORD = "TestPassword"
PORT = "6789"


class NZBGetServer(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        formatted = json.dumps("{}", separators=(",\n", " : "), indent=0)
        self.wfile.write(formatted.encode("utf-8"))

    def do_POST(self):
        self.log_request()
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.end_headers()
        data = '<?xml version="1.0" encoding="UTF-8"?><nzb></nzb>'
        response = XMLRPCDocGenerator.client.dumps(
            (data,), allow_none=False, encoding=None
        )
        self.wfile.write(response.encode("utf-8"))


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
    os.environ["NZBOP_TEMPDIR"] = TMP_DIR


class Tests(unittest.TestCase):

    def test_in_scheduler_mode(self):
        set_defaults_env()
        os.environ["NZBSP_TASKID"] = "ID"
        server = http.server.HTTPServer((HOST, int(PORT)), NZBGetServer)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        [out, code, err] = run_script()
        server.shutdown()
        server.server_close()
        thread.join()
        self.assertEqual(code, SUCCESS)

    def test_manifest(self):
        with open(ROOT + "/manifest.json", encoding="utf-8") as file:
            try:
                json.loads(file.read())
            except ValueError as e:
                self.fail("manifest.json is not valid.")


if __name__ == "__main__":
    unittest.main()
