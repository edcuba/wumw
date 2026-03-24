import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        print("usage: wumw <command> [args...]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1:]
    result = subprocess.run(command, capture_output=True)

    if result.stdout:
        sys.stdout.buffer.write(result.stdout)
    if result.stderr:
        sys.stderr.buffer.write(result.stderr)

    sys.exit(result.returncode)
