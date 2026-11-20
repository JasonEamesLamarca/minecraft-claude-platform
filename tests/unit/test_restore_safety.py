"""restore.sh must refuse archives that could write outside the data folder (threat model T10/T15)."""
import io
import subprocess
import tarfile


def make_tar(path, member_name):
    with tarfile.open(path, "w:gz") as t:
        data = b"x"
        info = tarfile.TarInfo(member_name)
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))


def run_restore(workdir, archive):
    (workdir / ".env").write_text(f"MC_DATA_DIR={workdir / 'data'}\nBACKUP_DIR={workdir / 'b'}\n")
    return subprocess.run(["bash", str(workdir / "scripts" / "restore.sh"), str(archive), "--yes"],
                          cwd=workdir, capture_output=True, text=True, timeout=60)


def test_rejects_parent_traversal(workdir):
    a = workdir / "evil.tar.gz"; make_tar(a, "../../etc/evil")
    r = run_restore(workdir, a)
    assert r.returncode != 0 and "Unsafe paths" in r.stderr


def test_rejects_traversal_early_in_a_large_archive(workdir):
    # The unsafe entry comes first and the listing is long: a 'grep -q' that exits early would SIGPIPE tar, and
    # with pipefail the check would read as "no unsafe paths".
    a = workdir / "big.tar.gz"
    with tarfile.open(a, "w:gz") as t:
        for i, name in enumerate(["../../etc/evil"] + [f"world/region/f{i:06d}.dat" for i in range(60000)]):
            info = tarfile.TarInfo(name)
            t.addfile(info, io.BytesIO(b""))
    r = run_restore(workdir, a)
    assert r.returncode != 0 and "Unsafe paths" in r.stderr, (r.returncode, r.stdout[-300:], r.stderr[-300:])


def test_rejects_absolute_paths(workdir):
    a = workdir / "evil2.tar.gz"; make_tar(a, "/tmp/evil")
    r = run_restore(workdir, a)
    assert r.returncode != 0 and "Unsafe paths" in r.stderr


def test_rejects_non_archive(workdir):
    a = workdir / "junk.tar.gz"; a.write_text("not a tar")
    r = run_restore(workdir, a)
    assert r.returncode != 0


def test_missing_file(workdir):
    r = run_restore(workdir, workdir / "nope.tar.gz")
    assert r.returncode != 0 and "No such file" in r.stderr
