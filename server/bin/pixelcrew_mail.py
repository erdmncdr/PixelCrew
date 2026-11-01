"""The agents' side of PixelCrew's mailbox: a job's CLI process cannot reach the server over the
network from inside its sandbox, so a request is a JSON file dropped into the job's mailbox folder
(env PIXELCREW_MAILBOX) and the answer is the matching reply file the server writes next to it."""
import json
import os
import sys
import time
import uuid


def mailbox():
    path = os.environ.get("PIXELCREW_MAILBOX")
    if not path or not os.path.isdir(path):
        sys.stderr.write("This command only works inside a PixelCrew job.\n")
        sys.exit(2)
    return path


def request(kind, args, timeout):
    """Write <id>.req.json, wait for <id>.res.json, return its dict (ok, text)."""
    box = mailbox()
    rid = uuid.uuid4().hex[:12]
    tmp = os.path.join(box, rid + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"kind": kind, "args": args, "ts": time.time()}, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(box, rid + ".req.json"))
    res = os.path.join(box, rid + ".res.json")
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(res):
            try:
                with open(res, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                time.sleep(0.2)  # being written
                continue
        time.sleep(0.4)
    return {"ok": False, "text": "No reply within %d seconds; go on with your best assumption and say so in your report." % timeout}


def finish(reply):
    text = str(reply.get("text") or "")
    if text:
        print(text)
    sys.exit(0 if reply.get("ok") else 1)
