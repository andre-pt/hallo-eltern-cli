import io
import json
import tempfile
import unittest
from email.message import Message
from http.cookiejar import Cookie
from pathlib import Path
from unittest.mock import Mock

from hallo_eltern.client import BASE_URL, MEDIA_URL, Client, ClientError, NoRedirects
from hallo_eltern.cli import display


class Response(io.BytesIO):
    status = 200

    def __init__(self, content=b"", content_type="application/pdf", length=None):
        super().__init__(content)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if length is not None:
            self.headers["Content-Length"] = str(length)


BOARD = {"itemid": "123", "code": "child-code"}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.client.access_id = 42

    def test_only_observed_endpoints_allowed(self):
        self.client.opener = Mock()
        for method, path in [("GET", "/messages/1"), ("PUT", "/messages/1"),
                             ("POST", "/messages"), ("GET", "/account/login"),
                             ("POST", "/banner/read"), ("PUT", "/messages/open_reply/1")]:
            with self.subTest(method=method, path=path), self.assertRaises(ClientError):
                self.client._request(method, path)
        self.client.opener.open.assert_not_called()

    def test_login_credentials_only_sent_to_login(self):
        self.client.opener = Mock()
        self.client.opener.open.side_effect = [
            Response(json.dumps({"statuscode": 200, "detailresponse": {"userid": 42}}).encode()),
            Response(b'{"statuscode":200,"listresponse":[]}')]
        self.client.login("test@example.com", "secret")
        self.client.pinboards()
        login = self.client.opener.open.call_args_list[0].args[0]
        read = self.client.opener.open.call_args_list[1].args[0]
        self.assertEqual(login.method, "POST")
        self.assertEqual(login.data, b"{}")
        self.assertEqual(login.get_header("Password"), "secret")
        self.assertIsNone(read.get_header("Password"))
        self.assertIsNone(read.get_header("Mail"))
        self.assertEqual(read.get_header("Mogree-access-id"), "42")

    def test_normalizes_server_duplicate_path_cookie(self):
        cookie = Cookie(0, "mogree-Access-Token-Parent", "example-token", None, False,
                        ".klassenpinnwand.at", True, True, "/edugroup/api/v1/account/",
                        False, True, None, True, None, None, {})
        self.client.cookies.set_cookie(cookie)
        self.client._request = Mock(return_value={"detailresponse": {"userid": 42}})
        self.client.login("test@example.com", "secret")
        from urllib.request import Request
        request = Request(BASE_URL + "/pinboard")
        self.client.cookies.add_cookie_header(request)
        self.assertEqual(request.get_header("Cookie"), "mogree-Access-Token-Parent=example-token")

    def test_read_searches_open_then_closed_without_detail_get(self):
        self.client._request = Mock(side_effect=[
            {"listresponse": [{"itemid": "1", "unread": True, "message": "first"}]},
            {"listresponse": []},
            {"listresponse": [{"itemid": "2", "message": "closed"}]},
        ])
        self.assertEqual(self.client.read(BOARD, "2")["message"], "closed")
        for call in self.client._request.call_args_list:
            self.assertEqual(call.args[:2], ("GET", "/messages"))
        self.assertEqual(self.client._request.call_args_list[-1].args[2]["closed"], "true")

    def test_pagination_handles_server_cap_and_deduplicates(self):
        self.client._request = Mock(side_effect=[
            {"listresponse": [{"itemid": "1"}, {"itemid": "2"}]},
            {"listresponse": [{"itemid": "2"}, {"itemid": "3"}]},
            {"listresponse": []},
        ])
        self.assertEqual([m["itemid"] for m in self.client.messages(BOARD)], ["1", "2", "3"])
        self.assertEqual([c.args[2]["pagingindex"] for c in self.client._request.call_args_list], [0, 2, 4])

    def test_pagination_refuses_repeated_full_page(self):
        self.client._request = Mock(return_value={"listresponse": [{"itemid": "1"}]})
        with self.assertRaises(ClientError):
            list(self.client.messages(BOARD, page_size=1))

    def test_empty_and_missing_message(self):
        self.client._request = Mock(return_value={"listresponse": []})
        self.assertEqual(list(self.client.messages(BOARD)), [])
        with self.assertRaisesRegex(ClientError, "not found"):
            self.client.read(BOARD, "missing")

    def test_api_errors_do_not_expose_response_secrets(self):
        self.client.opener = Mock()
        self.client.opener.open.return_value = Response(b'{"statuscode":70,"secret":"hidden"}')
        with self.assertRaises(ClientError) as context:
            self.client.pinboards()
        self.assertNotIn("hidden", str(context.exception))

    def test_redirects_are_refused(self):
        with self.assertRaises(ClientError):
            NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.example")

    def test_download_is_streamed_without_auth_and_cannot_overwrite(self):
        self.client.media_opener = Mock()
        self.client.media_opener.open.side_effect = [Response(b"%PDF-test"), Response(b"%PDF-other")]
        message = {"itemid": "1", "link": MEDIA_URL + "?token=example", "filename_client": "../../test.pdf"}
        with tempfile.TemporaryDirectory() as directory:
            target = self.client.download(message, directory)
            self.assertEqual(target, Path(directory) / "test.pdf")
            self.assertEqual(target.read_bytes(), b"%PDF-test")
            with self.assertRaises(ClientError):
                self.client.download(message, directory)
            self.assertEqual(target.read_bytes(), b"%PDF-test")
        request = self.client.media_opener.open.call_args_list[0].args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.headers, {})

    def test_download_rejects_unobserved_urls(self):
        self.client.media_opener = Mock()
        for url in ["http://hallo-api.klassenpinnwand.at/edugroup/api/v1/media", BASE_URL + "/messages/1",
                    "https://evil.example/file", "https://user:password@hallo-api.klassenpinnwand.at/edugroup/api/v1/media"]:
            with self.subTest(url=url), self.assertRaises(ClientError):
                self.client.download({"itemid": "1", "link": url}, "/tmp")
        self.client.media_opener.open.assert_not_called()

    def test_partial_download_is_removed(self):
        self.client.media_opener = Mock()
        self.client.media_opener.open.return_value = Response(b"short", length=100)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ClientError):
                self.client.download({"itemid": "1", "link": MEDIA_URL, "filename_client": "a.pdf"}, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_error_page_is_not_saved(self):
        self.client.media_opener = Mock()
        self.client.media_opener.open.return_value = Response(b"error", "text/html")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ClientError):
                self.client.download({"itemid": "1", "link": MEDIA_URL}, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_decodes_frontend_escapes_and_removes_terminal_controls(self):
        self.assertEqual(display(r"Hi \uD83D\uDE00 \303" + "\x1b"), "Hi 😀 Ã")


if __name__ == "__main__":
    unittest.main()
