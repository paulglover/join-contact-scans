"""The command line, including the lines the macOS droplet reads back."""
import numpy as np
import pytest

import dngfixture as fix
from joincontactscans import cli, dng


def test_joins_a_folder(tmp_path, capsys):
    _, joined = fix.write_roll(tmp_path, count=3)
    assert cli.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"wrote {tmp_path / 'S0220.dng'}" in out
    assert "verified 3/3 sections pixel-identical" in out
    assert np.array_equal(dng.read_joined_dng(str(tmp_path / "S0220.dng")),
                          joined)


def test_joins_named_files(tmp_path, capsys):
    paths, joined = fix.write_roll(tmp_path, count=4)
    assert cli.main([str(p) for p in paths]) == 0
    assert np.array_equal(dng.read_joined_dng(str(tmp_path / "S0220.dng")),
                          joined)


def test_the_wrote_line_is_what_the_droplet_parses(tmp_path, capsys):
    """contrib/macos reads the path back out of this line to offer "Show in
    Finder", by taking what is between "wrote " and the two spaces before the
    size. Changing its shape breaks that button."""
    fix.write_roll(tmp_path, count=2, height=5, width=7)
    cli.main([str(tmp_path)])
    line = [l for l in capsys.readouterr().out.splitlines()
            if l.startswith("wrote ")][0]
    path, _, rest = line[len("wrote "):].partition("  (")
    assert path == str(tmp_path / "S0220.dng")
    assert rest == "7x10, uint16)"


def test_out_option(tmp_path):
    fix.write_roll(tmp_path, count=2)
    out = tmp_path / "joined"
    assert cli.main([str(tmp_path), "--out", str(out)]) == 0
    assert (out / "S0220.dng").exists()
    assert not (tmp_path / "S0220.dng").exists()


def test_dry_run_writes_nothing(tmp_path, capsys):
    fix.write_roll(tmp_path, count=2)
    assert cli.main([str(tmp_path), "--dry-run"]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert not (tmp_path / "S0220.dng").exists()


def test_no_verify_skips_the_readback(tmp_path, capsys):
    fix.write_roll(tmp_path, count=2)
    assert cli.main([str(tmp_path), "--no-verify"]) == 0
    out = capsys.readouterr().out
    assert "no pixel verification" in out
    assert "pixel-identical" not in out


def test_existing_output_warns_and_exits_nonzero(tmp_path, capsys):
    """"a warning if the file exists already" — and an exit code the droplet
    notices, because the roll the user asked for was not written."""
    fix.write_roll(tmp_path, count=2)
    (tmp_path / "S0220.dng").write_bytes(b"precious")
    assert cli.main([str(tmp_path)]) == 1
    assert "SKIPPED S0220" in capsys.readouterr().err
    assert (tmp_path / "S0220.dng").read_bytes() == b"precious"


def test_force_overrides_that(tmp_path):
    fix.write_roll(tmp_path, count=2)
    (tmp_path / "S0220.dng").write_bytes(b"stale")
    assert cli.main([str(tmp_path), "--force"]) == 0


def test_nothing_to_join_is_a_usage_error(tmp_path, capsys):
    assert cli.main([str(tmp_path)]) == 2
    assert "no scan sections" in capsys.readouterr().err


def test_a_lone_section_is_reported(tmp_path, capsys):
    fix.write_roll(tmp_path, roll="S0220", count=1)
    assert cli.main([str(tmp_path)]) == 2
    assert "at least two" in capsys.readouterr().err


def test_mixed_success_and_failure(tmp_path, capsys):
    fix.write_roll(tmp_path, roll="S0220", count=3)
    fix.write_roll(tmp_path, roll="S0221", count=1)
    assert cli.main([str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "wrote" in captured.out and "S0220.dng" in captured.out
    assert "PROBLEM" in captured.err and "S0221" in captured.err


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "joincontactscans" in capsys.readouterr().out
