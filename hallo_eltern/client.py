"""Only login, pinboard/message listings and signed media GETs are permitted."""

import json
import re
import time
from http.client import HTTPException
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

BASE_URL = "https://hallo-api.klassenpinnwand.at/edugroup/api/v1"
MEDIA_URL = BASE_URL + "/media"


class ClientError(Exception):
    """An actionable failure whose message contains no credentials or signed URLs."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ClientError("Unexpected redirect refused; no credentials were forwarded.")


class Client:
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.cookies = CookieJar()
        self.opener = build_opener(NoRedirects(), HTTPCookieProcessor(self.cookies))
        # Attachments carry their own signed URL; never send account credentials/cookies.
        self.media_opener = build_opener(NoRedirects())
        self.access_id = None

    def _request(self, method, path, params=None, credentials=None):
        if (method, path) not in {
            ("POST", "/account/login"), ("GET", "/pinboard"), ("GET", "/messages")
        }:
            raise ClientError("Request blocked by the read-only endpoint policy.")
        headers = {"Accept": "application/json"}
        body = None
        if method == "POST":
            if not credentials:
                raise ClientError("Login requires a username and password.")
            headers.update(mail=credentials[0], password=credentials[1])
            headers["Content-Type"] = "application/json"
            body = b"{}"
        elif self.access_id is None:
            raise ClientError("Log in before reading messages.")
        else:
            headers["mogree-Access-Id"] = str(self.access_id)
        url = BASE_URL + path
        if params:
            url += "?" + urlencode(params)
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                data = json.load(response)
        except HTTPError as exc:
            raise ClientError(f"API returned HTTP {exc.code}.") from None
        except (URLError, TimeoutError, OSError, HTTPException):
            raise ClientError("Could not connect to the API; check your connection and retry.") from None
        except (ValueError, UnicodeError):
            raise ClientError("API returned an invalid JSON response.") from None
        if not isinstance(data, dict):
            raise ClientError("API returned an unexpected response shape.")
        if data.get("statuscode") != 200:
            code = data.get("statuscode")
            if path == "/account/login":
                raise ClientError("Login failed; check your credentials and account validation.")
            raise ClientError(f"API rejected the request (status {code}); try logging in again.")
        return data

    def login(self, username, password):
        data = self._request("POST", "/account/login", credentials=(username, password))
        detail = data.get("detailresponse") or {}
        if not isinstance(detail, dict) or not detail.get("userid"):
            raise ClientError("Login response is missing the access ID.")
        self.access_id = detail["userid"]
        # The live server sends both `Path=` and `Path=/`. CookieJar takes the
        # empty first value and scopes the cookie to /account/, unlike browsers.
        # Apply the server's explicit final path for this one known session cookie.
        for cookie in list(self.cookies):
            if (cookie.name == "mogree-Access-Token-Parent"
                    and cookie.domain.lstrip(".") == "klassenpinnwand.at"
                    and cookie.path == "/edugroup/api/v1/account/"):
                self.cookies.clear(cookie.domain, cookie.path, cookie.name)
                cookie.path = "/"
                cookie.path_specified = True
                self.cookies.set_cookie(cookie)

    def _list(self, data):
        items = data.get("listresponse")
        if items is None:
            return []
        if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
            raise ClientError("API returned an unexpected list shape.")
        return items

    def pinboards(self):
        return self._list(self._request("GET", "/pinboard"))

    def messages(self, pinboard, status="open", search="", page_size=50):
        if status not in {"open", "closed", "all"} or not 1 <= page_size <= 100:
            raise ClientError("Invalid message status or page size.")
        if not pinboard.get("itemid") or not pinboard.get("code"):
            raise ClientError("Pinboard response is missing its ID or child code.")
        seen = set()
        for closed in ([False, True] if status == "all" else [status == "closed"]):
            offset = 0
            for _ in range(1000):
                page = self._list(self._request("GET", "/messages", {
                    "pinboard": pinboard["itemid"], "childcode": pinboard["code"],
                    "closed": str(closed).lower(), "search": search,
                    "pagingindex": offset, "pagingresults": page_size,
                    "time": int(time.time()),
                }))
                if not page:
                    break
                new_items = 0
                for item in page:
                    identifier = str(item.get("itemid", ""))
                    if not identifier:
                        raise ClientError("Message response is missing an ID.")
                    if identifier not in seen:
                        seen.add(identifier)
                        new_items += 1
                        yield item
                if not new_items:
                    raise ClientError("Pagination stopped advancing; refusing to loop indefinitely.")
                # Advance by the returned count: the server may cap page size.
                offset += len(page)
            else:
                raise ClientError("Pagination limit reached; narrow your search.")

    def read(self, pinboard, message_id, page_size=50):
        for message in self.messages(pinboard, status="all", page_size=page_size):
            if str(message["itemid"]) == str(message_id):
                return message
        raise ClientError("Message not found on this pinboard (including closed messages).")

    def download(self, message, directory):
        link = message.get("link")
        if not link:
            raise ClientError("This message has no attachment.")
        parts = urlsplit(link)
        if (parts.scheme != "https" or parts.netloc != urlsplit(BASE_URL).netloc
                or parts.path != urlsplit(MEDIA_URL).path or parts.fragment):
            raise ClientError("Attachment URL is outside the observed media endpoint.")
        name = str(message.get("filename_client") or f"attachment-{message['itemid']}")
        name = re.sub(r'[\x00-\x1f\x7f]', '_', name.replace('\\', '/').split('/')[-1])
        if name in {"", ".", ".."}:
            name = f"attachment-{message['itemid']}"
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        created = False
        try:
            with self.media_opener.open(Request(link, method="GET"), timeout=self.timeout) as response:
                if response.status != 200:
                    raise ClientError("Attachment download did not return HTTP 200.")
                if response.headers.get_content_type() in {"text/html", "application/json"}:
                    raise ClientError("Attachment returned an error page instead of a file.")
                expected = response.headers.get("Content-Length")
                size = 0
                with target.open("xb") as output:
                    created = True
                    while chunk := response.read(64 * 1024):
                        output.write(chunk)
                        size += len(chunk)
                if expected is not None and size != int(expected):
                    raise ClientError("Attachment download was incomplete.")
        except FileExistsError:
            raise ClientError("Output file already exists; choose a different output directory.") from None
        except HTTPError as exc:
            raise ClientError(f"Attachment returned HTTP {exc.code}.") from None
        except (OSError, URLError, ValueError, HTTPException):
            raise ClientError("Attachment download failed; check the connection and output directory.") from None
        finally:
            # Remove partial downloads on all failures, including interruption.
            import sys
            if created and sys.exc_info()[0] is not None:
                target.unlink(missing_ok=True)
        return target
