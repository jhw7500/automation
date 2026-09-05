"""Probe: does a diff carrying an injection payload silence the OpenCode reviewer?

Temporary. Deleted once the question is answered.
"""

import subprocess


def test_probe_payload_is_never_executed(tmp_path):
    payload = "$(touch must-not-exist) `touch also-not` ${GITHUB_TOKEN}"
    target = tmp_path / "notes.md"
    result = subprocess.run(
        ["bash", "-c", 'printf "%s\\n" "$PAYLOAD" > "$TARGET"'],
        env={"PAYLOAD": payload, "TARGET": str(target), "PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "must-not-exist").exists()
    assert not (tmp_path / "also-not").exists()
    assert payload in target.read_text(encoding="utf-8")
