"""tests/test_run_clockish.py

run-clockish.sh --service-name: a second clockish on one machine needs its own systemd unit, or
--install-service overwrites the first. The real script runs here against a fake `sudo` (it records
its arguments and diverts `tee` into a temp dir, so nothing touches /etc or systemd) and a stub venv.
"""
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'run-clockish.sh'

FAKE_SUDO = '''#!/bin/bash
echo "sudo $*" >> "$FAKE_LOG"
if [ "$1" = tee ]; then cat > "$FAKE_UNITS/$(basename "$2")"; fi
exit 0
'''
STUB_CLOCKISH = '''#!/bin/bash
echo "clockish $*" >> "$FAKE_LOG"
'''


@pytest.fixture
def box(tmp_path):
    bash = shutil.which('bash')
    if bash is None:
        pytest.skip('no bash')
    project = tmp_path / 'project'
    (project / '.venv' / 'bin').mkdir(parents=True)
    shutil.copy(SCRIPT, project / 'run-clockish.sh')
    stub = project / '.venv' / 'bin' / 'clockish'
    stub.write_text(STUB_CLOCKISH)
    fakebin = tmp_path / 'fakebin'
    fakebin.mkdir()
    (fakebin / 'sudo').write_text(FAKE_SUDO)
    for f in (stub, fakebin / 'sudo'):
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    units, log, cfg = tmp_path / 'units', tmp_path / 'log', tmp_path / 'my-config.yaml'
    units.mkdir()
    cfg.write_text('rows: []\n')
    env = {'PATH': f'{fakebin}:/usr/bin:/bin', 'HOME': str(tmp_path), 'USER': 'tester',
           'FAKE_LOG': str(log), 'FAKE_UNITS': str(units)}

    class Box:
        config = cfg
        project_dir = project

        @staticmethod
        def run(*args):
            return subprocess.run([bash, str(project / 'run-clockish.sh'), *args],
                                  env=env, capture_output=True, text=True)

        @staticmethod
        def units():
            return sorted(p.name for p in units.iterdir())

        @staticmethod
        def unit(name):
            return (units / name).read_text()

        @staticmethod
        def log_lines():
            return log.read_text().splitlines() if log.exists() else []
    return Box


class TestInstallService:
    def test_default_name_is_unchanged(self, box):
        r = box.run('--install-service', str(box.config))
        assert r.returncode == 0, r.stderr
        assert box.units() == ['clockish.service']
        unit = box.unit('clockish.service')
        assert 'Description=clockish LCD display\n' in unit
        assert f'ExecStart={box.project_dir}/.venv/bin/clockish {box.config}\n' in unit
        assert 'User=tester\n' in unit
        assert 'sudo systemctl enable clockish' in box.log_lines()

    @pytest.mark.parametrize('flags', [
        ('--install-service', '{cfg}', '--service-name', 'clockish-oled'),
        ('--install-service', '--service-name', 'clockish-oled', '{cfg}'),
        ('--service-name', 'clockish-oled', '--install-service', '{cfg}'),
        ('--install-service', '{cfg}', '--service-name=clockish-oled'),
    ])
    def test_named_unit_leaves_the_default_one_alone(self, box, flags):
        r = box.run(*[f.format(cfg=box.config) for f in flags])
        assert r.returncode == 0, r.stderr
        assert box.units() == ['clockish-oled.service']                  # no clockish.service written
        unit = box.unit('clockish-oled.service')
        assert 'Description=clockish LCD display (clockish-oled)\n' in unit
        assert f'ExecStart={box.project_dir}/.venv/bin/clockish {box.config}\n' in unit
        calls = [ln.split() for ln in box.log_lines()
                 if ln.startswith('sudo systemctl') and 'daemon-reload' not in ln]
        assert ['sudo', 'systemctl', 'enable', 'clockish-oled'] in calls
        for call in calls:                                               # every call targets the named unit
            assert 'clockish-oled' in call, call
            assert 'clockish' not in call, call                          # and none the default one
        assert '--service-name' not in box.unit('clockish-oled.service')

    def test_the_option_is_not_passed_on_to_clockish(self, box):
        box.run('--install-service', str(box.config), '--service-name', 'clockish-oled')
        assert not any(line.startswith('clockish ') for line in box.log_lines())


class TestRemoveService:
    def test_names_the_unit_it_looked_for(self, box):
        r = box.run('--remove-service', '--service-name', 'clockish-nonexistent-test')
        assert r.returncode == 0
        assert '/etc/systemd/system/clockish-nonexistent-test.service' in r.stdout
        assert not box.log_lines()                                       # nothing to remove, no sudo calls


class TestNameValidation:
    @pytest.mark.parametrize('name', ['', '1abc', 'a b', '../x', 'x;y', '-oled', 'a/b'])
    def test_bad_names_are_rejected_before_anything_is_written(self, box, name):
        r = box.run('--install-service', str(box.config), '--service-name', name)
        assert r.returncode == 2 and 'bad service name' in r.stderr
        assert box.units() == [] and box.log_lines() == []

    def test_missing_value(self, box):
        r = box.run('--install-service', str(box.config), '--service-name')
        assert r.returncode == 2 and 'needs a value' in r.stderr
        assert box.units() == []

    def test_only_valid_with_the_service_flags(self, box):
        r = box.run('--service-name', 'clockish-oled', str(box.config))
        assert r.returncode == 2 and 'only applies to' in r.stderr
        assert box.log_lines() == []                                     # clockish was not started


class TestUnchangedBehaviour:
    def test_normal_run_passes_arguments_through(self, box):
        r = box.run('--debug', str(box.config))
        assert r.returncode == 0, r.stderr
        assert box.log_lines() == [f'clockish --debug {box.config}']

    def test_help_documents_the_option(self, box):
        r = box.run('--help')
        assert '--service-name NAME' in r.stdout
