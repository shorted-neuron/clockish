"""tests/test_stdout_buffering.py

Under systemd stdout is a pipe, which Python block-buffers: startup and backlight lines reached the
journal minutes or hours late (or only when the service stopped).  main() line-buffers stdout first
thing, so every print is flushed at its newline.
"""
from __future__ import annotations

import io
import os
import sys

import pytest

import clockish.display as d


class PipeStdout:
    """sys.stdout as a block-buffered text stream on a pipe, plus a way to read what arrived."""

    def __init__(self, monkeypatch):
        self._monkeypatch = monkeypatch
        self._r, w = os.pipe()
        os.set_blocking(self._r, False)
        self.stream = io.TextIOWrapper(io.BufferedWriter(io.FileIO(w, 'w')), encoding='utf-8')

    def install(self) -> None:
        # Inside the test body, not a fixture: pytest swaps sys.stdout again for the call phase.
        self._monkeypatch.setattr(sys, 'stdout', self.stream)

    def arrived(self) -> str:
        try:
            return os.read(self._r, 4096).decode()
        except BlockingIOError:
            return ''


@pytest.fixture
def pipe_stdout(monkeypatch):
    pipe = PipeStdout(monkeypatch)
    yield pipe
    monkeypatch.undo()
    pipe.stream.close()
    os.close(pipe._r)


def test_a_piped_stdout_holds_prints_back_until_line_buffered(pipe_stdout):
    pipe_stdout.install()
    print('held back')
    assert pipe_stdout.arrived() == ''              # the bug: block-buffered, nothing arrived
    d._line_buffer_stdout()
    print('now flushed')
    assert pipe_stdout.arrived() == 'held back\nnow flushed\n'


def test_main_line_buffers_stdout_before_anything_else_prints(monkeypatch):
    order = []
    monkeypatch.setattr(d, '_line_buffer_stdout', lambda: order.append('buffering'))

    def stop():
        order.append('init')
        raise SystemExit(0)
    monkeypatch.setattr(d, '_init', stop)
    with pytest.raises(SystemExit):
        d.main()
    assert order == ['buffering', 'init']


def test_a_stdout_without_reconfigure_is_left_alone(monkeypatch):
    class Bare:
        def write(self, s):
            return len(s)
    monkeypatch.setattr(sys, 'stdout', Bare())
    d._line_buffer_stdout()                         # must not raise
