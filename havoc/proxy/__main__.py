import argparse
import subprocess
import sys
import threading

from havoc.proxy.intercept import Interceptor, TraceWriter


def pump(source, sink, transform, on_end):
    try:
        for line in iter(source.readline, b""):
            sink.write(transform(line))
            sink.flush()
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
    args = parser.parse_args(own_args)
    if not command:
        parser.error("give the server command after --")

    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    interceptor = Interceptor(TraceWriter(args.trace))

    upstream = threading.Thread(
        target=pump,
        args=(sys.stdin.buffer, child.stdin, interceptor.from_client, child.stdin.close),
        daemon=True,
    )
    downstream = threading.Thread(
        target=pump,
        args=(child.stdout, sys.stdout.buffer, interceptor.from_server, lambda: None),
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
