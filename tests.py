#
# Copyright (C) 2024-2026 Denis <denis@nzbget.com>
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
from os.path import dirname
import os
import subprocess
import xmlrpc.server
import xml.etree.cElementTree as ET
import shutil

SUCCESS = 93
NONE = 95
ERROR = 94

ROOT = dirname(__file__)
TMP_DIR = ROOT + os.sep + "tmp"
TEST_DATA_DIR = ROOT + os.sep + "test_data"
HOST = "127.0.0.1"
USERNAME = "TestUser"
PASSWORD = "TestPassword"
PORT = "6789"


def clean_up():
    if os.path.exists(TMP_DIR):
        shutil.rmtree(TMP_DIR)


def parse_member(member):
    name = member.find("name").text
    value_elem = member.find("value")

    if value_elem.find("i4") is not None:
        value = int(value_elem.find("i4").text)
    elif value_elem.find("boolean") is not None:
        value = value_elem.find("boolean").text == "true"
    elif value_elem.find("array") is not None:
        value = parse_array(value_elem.find("array"))

    return name, value


def parse_array(array_elem):
    array_data = {}
    for member in array_elem.find("data").findall("member"):
        name = member.find("name").text
        value_elem = member.find("value")
        if value_elem.find("i4") is not None:
            value = int(value_elem.find("i4").text)
        elif value_elem.find("boolean") is not None:
            value = value_elem.find("boolean").text == "true"
        array_data[name] = value
    return array_data


class NZBGetServer(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        f = open(TEST_DATA_DIR + "/listgroups_resp.json")
        data = json.load(f)
        formatted = json.dumps(data, separators=(",\n", " : "), indent=0)
        self.wfile.write(formatted.encode("utf-8"))
        f.close()

    def do_POST(self):
        self.log_request()
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.end_headers()
        with open(TEST_DATA_DIR + "/status_resp.xml", "r") as f:
            data = f.read().replace("\n", "").strip()
            root = ET.fromstring(data)
            response_dict = {}
            for member in root.findall("member"):
                name, value = parse_member(member)
                response_dict[name] = value

            # Serialize Python object to XML-RPC response
            response = xmlrpc.client.dumps(
                (response_dict,), allow_none=False, encoding=None
            )

            # Send the serialized response to the client
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
    os.environ["NZBNA_QUEUEDFILE"] = "nzb_filename"


class Tests(unittest.TestCase):

    def test_scheduler_mode(self):
        set_defaults_env()
        os.environ["NZBSP_TASKID"] = "ID"
        server = http.server.HTTPServer((HOST, int(PORT)), NZBGetServer)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        [out, code, err] = run_script()
        server.shutdown()
        server.server_close()
        thread.join()
        del os.environ["NZBSP_TASKID"]
        clean_up()
        self.assertEqual(code, 0)

    def test_queue_mode(self):
        set_defaults_env()
        os.environ["NZBNA_NZBNAME"] = "nzb_filename"
        os.environ["NZBNA_EVENT"] = "NZB_DOWNLOADED"
        os.environ["NZBNA_QUEUEDFILE"] = "nzb_filename.queued"
        server = http.server.HTTPServer((HOST, int(PORT)), NZBGetServer)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        [out, code, err] = run_script()
        server.shutdown()
        server.server_close()
        thread.join()
        del os.environ["NZBNA_NZBNAME"]
        del os.environ["NZBNA_EVENT"]
        del os.environ["NZBNA_QUEUEDFILE"]
        self.assertEqual(code, 0)

    def test_scan_mode(self):
        set_defaults_env()
        os.environ["NZBNP_NZBNAME"] = "nzb_filename"
        os.environ["NZBNP_CATEGORY"] = "Movies"
        os.environ["NZBNP_FILENAME"] = "nzb_filename.queued"
        os.environ["NZBOP_NZBDIR"] = TEST_DATA_DIR
        server = http.server.HTTPServer((HOST, int(PORT)), NZBGetServer)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        [out, code, err] = run_script()
        server.shutdown()
        server.server_close()
        thread.join()
        del os.environ["NZBNP_NZBNAME"]
        del os.environ["NZBNP_CATEGORY"]
        self.assertEqual(code, 0)

    def test_manifest(self):
        with open(ROOT + "/manifest.json", encoding="utf-8") as file:
            try:
                json.loads(file.read())
            except ValueError as e:
                self.fail("manifest.json is not valid.")


class UnitTests(unittest.TestCase):
    def test_is_max_connections_reply(self):
        from main import is_max_connections_reply
        self.assertTrue(is_max_connections_reply("502", "Too many connections"))
        self.assertTrue(is_max_connections_reply("502", "Maximum connections reached"))
        self.assertFalse(is_max_connections_reply("502", "Access denied"))
        self.assertFalse(is_max_connections_reply("480", "Too many connections"))

    def test_has_nzb_parameter(self):
        from main import has_nzb_parameter
        params1 = [{"Name": "Category", "Value": "Movies"}, {"Name": "CnpNZBFileName", "Value": "test.nzb"}]
        params2 = [{"Name": "Category", "Value": "Movies"}]
        self.assertTrue(has_nzb_parameter(params1, "CnpNZBFileName"))
        self.assertFalse(has_nzb_parameter(params2, "CnpNZBFileName"))
        self.assertFalse(has_nzb_parameter(None, "CnpNZBFileName"))
        self.assertFalse(has_nzb_parameter([], "CnpNZBFileName"))

    def test_is_script_paused_job(self):
        from main import is_script_paused_job
        job1 = {"Status": "PAUSED", "Parameters": [{"Name": "CnpNZBFileName", "Value": "test.nzb"}]}
        job2 = {"Status": "DOWNLOADING", "Parameters": [{"Name": "CnpNZBFileName", "Value": "test.nzb"}]}
        job3 = {"Status": "PAUSED", "Parameters": [{"Name": "Category", "Value": "Movies"}]}
        self.assertTrue(is_script_paused_job(job1))
        self.assertFalse(is_script_paused_job(job2))
        self.assertFalse(is_script_paused_job(job3))

    def test_fix_nzb(self):
        from main import fix_nzb
        single_line = "<segment bytes=100>abc</segment><segment bytes=200>def</segment>"
        result = fix_nzb(single_line)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "<segment bytes=100>abc</segment>")
        self.assertEqual(result[1], "<segment bytes=200>def</segment>")

    def test_check_send_server_reply_max_conn(self):
        from main import check_send_server_reply
        import sys
        
        class MockSocket:
            def send(self, data):
                pass
            def close(self):
                pass

        sock = MockSocket()
        reply = "502 Too many connections"

        import main
        main.end_loop = False

        error, id_used, server_reply, msg_id_used = check_send_server_reply(
            sock, reply, "alt.binaries.test", "msgid123", 0, "news.example.com", "user", "pass"
        )

        self.assertFalse(error)
        self.assertFalse(id_used)
        self.assertEqual(server_reply, "MAX_CONN")
        self.assertIsNone(msg_id_used)


if __name__ == "__main__":
    unittest.main()
