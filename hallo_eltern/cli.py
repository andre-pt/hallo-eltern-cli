import argparse
import getpass
import json
import os
import re
import sys

from .client import Client, ClientError


def display(value):
    """Decode the frontend's escaped emoji/octal text and strip terminal controls."""
    text = str(value or "")
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), text)
    text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    text = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), text)
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", text)


def positive_page_size(value):
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError("must be between 1 and 100")
    return number


def parser():
    p = argparse.ArgumentParser(description="Read hallo! Eltern messages without marking them read.")
    p.add_argument("--username", default=os.environ.get("HALLO_ELTERN_USERNAME"),
                   help="login email (or HALLO_ELTERN_USERNAME)")
    commands = p.add_subparsers(dest="command", required=True)
    for name, help_text in [("pinboards", "List available pinboards"),
                            ("list", "List messages"), ("read", "Read one message"),
                            ("download", "Download a message's attachment")]:
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("--json", action="store_true", help="emit JSON")
        if name != "pinboards":
            cmd.add_argument("--pinboard", help="pinboard ID; required if more than one is available")
            cmd.add_argument("--page-size", type=positive_page_size, default=50)
        if name in {"read", "download"}:
            cmd.add_argument("message_id")
        if name == "list":
            cmd.add_argument("--status", choices=["open", "closed", "all"], default="open")
            cmd.add_argument("--search", default="")
        if name == "download":
            cmd.add_argument("--output-dir", default="downloads")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        username = args.username
        if not username:
            if not sys.stdin.isatty():
                raise ClientError("Set HALLO_ELTERN_USERNAME or pass --username.")
            username = input("Email: ").strip()
        password = os.environ.get("HALLO_ELTERN_PASSWORD")
        if password is None:
            if not sys.stdin.isatty():
                raise ClientError("Set HALLO_ELTERN_PASSWORD for non-interactive use.")
            password = getpass.getpass("Password: ")
        if not username or not password:
            raise ClientError("Username and password must not be empty.")
        client = Client()
        client.login(username, password)
        del password
        boards = client.pinboards()
        if args.command == "pinboards":
            result = boards
        else:
            choices = [b for b in boards if str(b.get("itemid")) == args.pinboard] if args.pinboard else boards
            if len(choices) != 1:
                raise ClientError("Select a pinboard with --pinboard ID; run pinboards to see available IDs.")
            board = choices[0]
            if args.command == "list":
                result = list(client.messages(board, args.status, args.search, args.page_size))
            else:
                result = client.read(board, args.message_id, args.page_size)
                if args.command == "download":
                    result = {"path": str(client.download(result, args.output_dir).resolve())}
        if args.json:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        elif args.command == "pinboards":
            for b in result:
                print(f"{b['itemid']}\t{display(b.get('title'))}\t{display(b.get('subtitle'))}")
        elif args.command == "list":
            print("ID\tDATE\tUNREAD\tATTACHMENT\tTITLE")
            for m in result:
                print(f"{m['itemid']}\t{display(m.get('date'))}\t{bool(m.get('unread'))}\t"
                      f"{bool(m.get('link'))}\t{display(m.get('title')).replace(chr(10), ' ')}")
        elif args.command == "read":
            sender = result.get("sender") or {}
            print(f"{display(result.get('title'))}\nID: {result['itemid']}\n"
                  f"Date: {display(result.get('date'))}\nFrom: {display(sender.get('title'))}\n\n"
                  f"{display(result.get('message'))}")
            if result.get("link"):
                print(f"\nAttachment: {display(result.get('filename_client') or 'attachment')}\n"
                      f"Download: hallo-eltern download {result['itemid']}")
        else:
            print(result["path"])
        return 0
    except (ClientError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Cancelled.", file=sys.stderr)
        return 130
