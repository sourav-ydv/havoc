import os
import argparse
import subprocess
import sys
import threading

from havoc.faults import load_plan
from havoc.proxy.intercept import Interceptor, TraceWriter


def pump(source, deliver, transform, on_end):
    try:
        for line in iter(source.readline, b""):
            out = transform(line)
            if out is not None:
                deliver(out)
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        on_end()


def split_command(argv):
    if "--" not in argv:
        return argv, []
    cut = argv.index("--")
    return argv[:cut], argv[cut + 1 :]


def main():
    own_args, command = split_command(sys.argv[1:])
    parser = argparse.ArgumentParser(prog="havoc.proxy")
    parser.add_argument("--trace")
    parser.add_argument("--faults")
    parser.add_argument("--guard", choices=["refund-claim"])
    args = parser.parse_args(own_args)
    if not command:
        parser.error("give the server command after --")

    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out_lock = threading.Lock()

    def to_client(data):
        with out_lock:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    def to_server(data):
        child.stdin.write(data)
        child.stdin.flush()

    plan = load_plan(args.faults) if args.faults else []
    guard_db = os.environ.get("HAVOC_STATE_DB") if args.guard == "refund-claim" else None
    interceptor = Interceptor(TraceWriter(args.trace), plan, emit=to_client, guard_db=guard_db)

    upstream = threading.Thread(
        target=pump,
        args=(sys.stdin.buffer, to_server, interceptor.from_client, child.stdin.close),
        daemon=True,
    )
    downstream = threading.Thread(
        target=pump,
        args=(child.stdout, to_client, interceptor.from_server, lambda: None),
        daemon=True,
    )
    upstream.start()
    downstream.start()

    try:
        downstream.join()
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
    finally:
        if child.poll() is None:
            child.kill()

    return child.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
