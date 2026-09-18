"""The command line, including the lines the macOS droplet reads back."""
import numpy as np
import pytest

import dngfixture as fix
from joincontactscans import cli, dng


def _args(paths, *rest, roll="S0220"):
    return ["--roll-id", roll, *(str(p) for p in paths), *rest]


def test_joins_named_files(tmp_path, capsys):
    paths, joined = fix.write_roll(tmp_path, count=4)
    assert cli.main(_args(paths)) == 0
    out = capsys.readouterr().out
    assert f"wrote {tmp_path / 'S0220.dng'}" in out
    assert "verified 4/4 sections pixel-identical" in out
    assert np.array_equal(dng.read_joined_dng(str(tmp_path / "S0220.dng")),
                          joined)


def test_the_roll_id_names_the_output(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    assert cli.main(["-i", "Portra 400", *map(str, paths)]) == 0
    assert (tmp_path / "Portra 400.dng").exists()
    assert not (tmp_path / "S0220.dng").exists()


def test_the_roll_id_is_required(tmp_path, capsys):
    paths, _ = fix.write_roll(tmp_path, count=2)
    with pytest.raises(SystemExit) as e:
        cli.main([str(p) for p in paths])
    assert e.value.code == 2
    assert "--roll-id" in capsys.readouterr().err


def test_a_blank_roll_id_is_a_usage_error(tmp_path, capsys):
    paths, _ = fix.write_roll(tmp_path, count=2)
    assert cli.main(_args(paths, roll="  ")) == 2
    assert "blank" in capsys.readouterr().err


def test_the_wrote_line_is_what_the_droplet_parses(tmp_path, capsys):
    """contrib/macos reads the path back out of this line to offer "Show in
    Finder", by taking what is between "wrote " and the two spaces before the
    size. Changing its shape breaks that button."""
    paths, _ = fix.write_roll(tmp_path, count=2, height=5, width=7)
    cli.main(_args(paths))
    line = [l for l in capsys.readouterr().out.splitlines()
            if l.startswith("wrote ")][0]
    path, _, rest = line[len("wrote "):].partition("  (")
    assert path == str(tmp_path / "S0220.dng")
    assert rest == "7x10, uint16)"


def test_out_option(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    out = tmp_path / "joined"
    assert cli.main(_args(paths, "--out", str(out))) == 0
    assert (out / "S0220.dng").exists()
    assert not (tmp_path / "S0220.dng").exists()


def test_dry_run_writes_nothing(tmp_path, capsys):
    paths, _ = fix.write_roll(tmp_path, count=2)
    assert cli.main(_args(paths, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and f"-> {tmp_path / 'S0220.dng'}" in out
    assert not (tmp_path / "S0220.dng").exists()


def test_no_verify_skips_the_readback(tmp_path, capsys):
    paths, _ = fix.write_roll(tmp_path, count=2)
    assert cli.main(_args(paths, "--no-verify")) == 0
    out = capsys.readouterr().out
    assert "no pixel verification" in out
    assert "pixel-identical" not in out


def test_existing_output_warns_and_exits_nonzero(tmp_path, capsys):
    """"a warning if the file exists already" — and an exit code the droplet
    notices, because the sheet the user asked for was not written."""
    paths, _ = fix.write_roll(tmp_path, count=2)
    (tmp_path / "S0220.dng").write_bytes(b"precious")
    assert cli.main(_args(paths)) == 1
    assert "SKIPPED S0220" in capsys.readouterr().err
    assert (tmp_path / "S0220.dng").read_bytes() == b"precious"


def test_force_overrides_that(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    (tmp_path / "S0220.dng").write_bytes(b"stale")
    assert cli.main(_args(paths, "--force")) == 0


def test_a_folder_is_a_usage_error(tmp_path, capsys):
    fix.write_roll(tmp_path, count=2)
    assert cli.main(_args([tmp_path])) == 2
    assert "is a folder" in capsys.readouterr().err


def test_a_lone_section_is_a_usage_error(tmp_path, capsys):
    paths, _ = fix.write_roll(tmp_path, count=1)
    assert cli.main(_args(paths)) == 2
    assert "at least two" in capsys.readouterr().err


def test_several_problems_are_listed_one_per_line(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("")
    assert cli.main(_args([tmp_path / "a.txt", tmp_path / "b.dng"])) == 2
    err = capsys.readouterr().err.splitlines()
    assert err[0] == "error:"
    assert err[1].startswith("  a.txt: ") and "no such file" in err[2]


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "joincontactscans" in capsys.readouterr().out
