"""Convert a Gmail Takeout .mbox into the mail_extract.jsonl.gz that ingest expects.

One JSON object per line with keys:
  date, from, to, subject, labels, message_id, in_reply_to, references, body

Usage:
  python -m support_triage_agent.mbox_extract \
      --mbox "/path/to/All mail Including Spam and Trash.mbox" \
      --out  data/mail_extract.jsonl.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import mailbox
from email.header import decode_header, make_header
from email.message import Message


def _hdr(msg: Message, name: str) -> str:
    raw = msg.get(name, "")
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _is_bulk(msg: Message) -> bool:
    """Bulk/automated mail markers — promo, newsletters, notifications. Not real customers."""
    if msg.get("List-Unsubscribe") or msg.get("List-Id"):
        return True
    prec = str(msg.get("Precedence") or "").lower()
    if prec in ("bulk", "list", "junk"):
        return True
    return False


def _body(msg: Message) -> str:
    """Best-effort plain-text body. Prefer text/plain, fall back to text/html stripped."""
    plain, html = None, None
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            payload = _decode_part(part)
            if ctype == "text/plain" and plain is None:
                plain = payload
            elif ctype == "text/html" and html is None:
                html = payload
    else:
        payload = _decode_part(msg)
        if msg.get_content_type() == "text/html":
            html = payload
        else:
            plain = payload

    text = plain if plain else _strip_html(html or "")
    return (text or "").strip()


def _decode_part(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    import re
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&lt;", "<", html)
    html = re.sub(r"&gt;", ">", html)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n\s*\n+", "\n\n", html)
    return html


def extract(mbox_path: str, out_path: str, limit: int | None = None) -> int:
    box = mailbox.mbox(mbox_path)
    opener = gzip.open if out_path.endswith(".gz") else open
    n = 0
    with opener(out_path, "wt", encoding="utf-8") as f:
        for msg in box:
            rec = {
                "date": _hdr(msg, "Date"),
                "from": _hdr(msg, "From"),
                "to": _hdr(msg, "To"),
                "subject": _hdr(msg, "Subject"),
                "labels": _hdr(msg, "X-Gmail-Labels"),
                "message_id": _hdr(msg, "Message-ID"),
                "in_reply_to": _hdr(msg, "In-Reply-To"),
                "references": _hdr(msg, "References"),
                "body": _body(msg),
                "bulk": _is_bulk(msg),
            }
            # Some customers put the whole complaint in the subject and send an
            # empty body. Fold the subject in so triage isn't blind to it.
            if not rec["body"].strip() and rec["subject"].strip():
                rec["body"] = rec["subject"]
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            if n % 1000 == 0:
                print(f"  ...{n} messages")
            if limit and n >= limit:
                break
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Gmail Takeout .mbox -> mail_extract.jsonl.gz")
    ap.add_argument("--mbox", required=True, help="path to the .mbox file")
    ap.add_argument("--out", default="data/mail_extract.jsonl.gz", help="output jsonl(.gz)")
    ap.add_argument("--limit", type=int, default=None, help="max messages (debug)")
    args = ap.parse_args()
    print(f"Reading {args.mbox}")
    n = extract(args.mbox, args.out, args.limit)
    print(f"Done. Wrote {n} messages -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
