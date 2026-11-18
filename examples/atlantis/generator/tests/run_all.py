#!/usr/bin/env python3
"""Run every generator test file in its own process (features register globally on import, so files must not
share an interpreter). Usage: python3 tests/run_all.py [-j N] [name ...]"""
import argparse, concurrent.futures as cf, glob, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


def run(path):
    t = time.time()
    p = subprocess.run([sys.executable, path], cwd=os.path.dirname(HERE), capture_output=True, text=True)
    return path, p.returncode, time.time() - t, p.stdout[-2000:] + p.stderr[-4000:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-j', type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument('names', nargs='*')
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(HERE, 'test_*.py')))
    if a.names:
        files = [f for f in files if any(n in os.path.basename(f) for n in a.names)]
    failed = 0
    with cf.ThreadPoolExecutor(a.j) as ex:
        for path, rc, dt, out in ex.map(run, files):
            print(f'{"ok  " if rc == 0 else "FAIL"} {os.path.basename(path)} ({dt:.0f}s)', flush=True)
            if rc:
                failed += 1
                print(out)
    print(f'{len(files) - failed}/{len(files)} test files passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
