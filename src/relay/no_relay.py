#!/usr/bin/env python3
"""Tiny TCP relay so an off-box client can reach a localhost-only port.

Nuclear Option binds its remote-command server to 127.0.0.1:5504 (localhost only),
which the vote bot can't reach from another machine. This relay runs INSIDE the
container (launched by the server wrapper) and forwards an externally-reachable
port to that localhost port:

    python3 no_relay.py 0.0.0.0:5550 127.0.0.1:5504

It's stdlib-only and transparent (just pumps bytes both ways), so the bot's
length-prefixed JSON protocol passes straight through.
"""
import socket
import sys
import threading


def _pump(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _handle(client, target):
    try:
        upstream = socket.create_connection(target, timeout=10)
    except OSError as e:
        sys.stderr.write(f"[relay] upstream connect failed: {e}\n")
        client.close()
        return
    t = threading.Thread(target=_pump, args=(client, upstream), daemon=True)
    t.start()
    _pump(upstream, client)
    for s in (client, upstream):
        try:
            s.close()
        except OSError:
            pass


def main():
    if len(sys.argv) != 3:
        sys.stderr.write("usage: no_relay.py LISTEN_HOST:PORT TARGET_HOST:PORT\n")
        sys.exit(2)
    lh, lp = sys.argv[1].rsplit(":", 1)
    th, tp = sys.argv[2].rsplit(":", 1)
    target = (th, int(tp))

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((lh, int(lp)))
    srv.listen(16)
    sys.stdout.write(f"[relay] listening on {lh}:{lp} -> {th}:{tp}\n")
    sys.stdout.flush()
    while True:
        try:
            client, _addr = srv.accept()
        except OSError as e:
            sys.stderr.write(f"[relay] accept error: {e}\n")
            continue
        threading.Thread(target=_handle, args=(client, target), daemon=True).start()


if __name__ == "__main__":
    main()
