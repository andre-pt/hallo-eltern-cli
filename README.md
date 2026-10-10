# hallo-eltern-cli

A read-only Python CLI for [hallo! Eltern](https://hallo-eltern.klassenpinnwand.at/).
Lists messages, reads individual message text, and downloads attachments without
marking messages read, confirming them, or sending replies.

Requires Python 3.10 or newer. There are no runtime dependencies.

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/hallo-eltern --username you@example.com list
```

The password is prompted with hidden input. Alternatively set
`HALLO_ELTERN_USERNAME` and `HALLO_ELTERN_PASSWORD` in your environment for scripts.
Credentials and session cookies are kept in memory for one invocation; they are
never written to disk. There is deliberately no password command-line argument.
You can also run `python3 -m hallo_eltern` directly from this directory without installing.

```sh
hallo-eltern pinboards
hallo-eltern list
hallo-eltern list --status all --search Elternpost --json
hallo-eltern read MESSAGE_ID
hallo-eltern download MESSAGE_ID --output-dir ./downloads
```

`--username` precedes the command; other flags follow it. Set the username environment
variable to use the examples above without an email prompt.
If you have multiple pinboards, add `--pinboard PINBOARD_ID` to list/read/download.
`list` defaults to open messages; `--status closed` and `--status all` are supported.
Pagination is automatic; `--page-size` accepts 1–100 (default 50).
Each command supports `--json`. Message JSON preserves the API's original fields;
text output decodes the escaped emoji used by the website. JSON can include signed
attachment links and child codes, so treat exports as private account data.

Downloads stream to disk, sanitize filenames, refuse redirects, and refuse to
overwrite existing files. Failed partial downloads are removed. Exit codes are
0 for success, 1 for errors, 2 for invalid arguments, and 130 for cancellation.

Reading scans the open and closed message listings for the requested ID. It does
not call the detail endpoint, whose read-state effects are unverified. The listing
includes message text and the single attachment supported by the frontend, but
does not promise full reply threads or confirmation/poll detail. Finding an old
message may therefore require several listing requests.

The transport allows only `POST /account/login`, `GET /pinboard`, `GET /messages`,
and signed `GET /media` attachment URLs on the observed API host. Login necessarily
creates an authentication session. It does not update message/account settings.

```sh
python3 -m unittest discover -s tests -v
```

The tests run offline and verify the request policy, pagination, decoding, and
download handling. See [the REA investigation](docs/investigation.md) for the
observed protocol and live validation scope. This is an unofficial client; the
website's API may change.
