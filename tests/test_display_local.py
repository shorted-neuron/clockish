"""tests/test_display_local.py

display-local.yaml: per-host keys merged over the display profile by display._resolve_config, and
the testbed deploy script that writes or removes it from the inventory's display_overrides.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import types

import pytest
import yaml

import clockish.display as d
from clockish.config_validator import DISPLAY_ROTATIONS, validate_config_dict

_ROOT = pathlib.Path(__file__).parent.parent


def _write(path: pathlib.Path, data) -> pathlib.Path:
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data))
    return path


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    """A config dir with a row config and a display profile; HOME points at an empty dir."""
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    _write(tmp_path / 'display.yaml', {'display': {'driver': 'ssd1306', 'width': 128, 'rotation': 0}})
    return tmp_path


def _resolve(cfgdir, config=None):
    args = types.SimpleNamespace(config=str(cfgdir / 'clockish-config.yaml'))
    return d._resolve_config(config if config is not None else {}, args)


class TestResolve:
    def test_no_local_file_keeps_profile(self, cfgdir, capsys):
        _, disp = _resolve(cfgdir)
        assert disp == {'driver': 'ssd1306', 'width': 128, 'rotation': 0}
        assert 'overrides' not in capsys.readouterr().out

    def test_local_keys_replace_profile_keys(self, cfgdir, capsys):
        _write(cfgdir / 'display-local.yaml', {'display': {'rotation': 180}})
        cfg, disp = _resolve(cfgdir)
        assert disp == {'driver': 'ssd1306', 'width': 128, 'rotation': 180}
        assert cfg['display'] is disp
        assert 'Display overrides from' in capsys.readouterr().out

    def test_nested_value_replaced_whole(self, cfgdir):
        _write(cfgdir / 'display.yaml', {'display': {'driver': 'ssd1306', 'duty_cycle': {'steps': [{'for': 60}]}}})
        _write(cfgdir / 'display-local.yaml', {'display': {'duty_cycle': {'period': 60, 'show_at': 0,
                                                                          'show_for': 10}}})
        _, disp = _resolve(cfgdir)
        assert disp['duty_cycle'] == {'period': 60, 'show_at': 0, 'show_for': 10}

    def test_applies_over_inline_display(self, cfgdir):
        _write(cfgdir / 'display-local.yaml', {'display': {'rotation': 270}})
        _, disp = _resolve(cfgdir, {'display': {'driver': 'framebuffer', 'rotation': 0}})
        assert disp == {'driver': 'framebuffer', 'rotation': 270}

    def test_home_config_dir_fallback(self, cfgdir):
        home = cfgdir / 'home' / '.config' / 'clockish'
        home.mkdir(parents=True)
        _write(home / 'display-local.yaml', {'display': {'rotation': 90}})
        assert _resolve(cfgdir)[1]['rotation'] == 90

    @pytest.mark.parametrize('body', ['rotation: 180\n', 'display: 180\n', 'display: [180]\n', '- a\n',
                                      'display: {rotation: [\n'])
    def test_bad_file_warns_and_is_ignored(self, cfgdir, capsys, body):
        _write(cfgdir / 'display-local.yaml', body)
        assert _resolve(cfgdir)[1]['rotation'] == 0
        assert 'display overrides' in capsys.readouterr().err

    def test_key_missing_from_profile_warns_but_applies(self, cfgdir, capsys):
        _write(cfgdir / 'display-local.yaml', {'display': {'rotaton': 180}})
        assert _resolve(cfgdir)[1]['rotaton'] == 180
        assert 'rotaton not in the display profile' in capsys.readouterr().err

    def test_empty_file_is_silent(self, cfgdir, capsys):
        _write(cfgdir / 'display-local.yaml', '')
        assert _resolve(cfgdir)[1]['rotation'] == 0
        out = capsys.readouterr()
        assert 'overrides' not in out.out + out.err


# --- display.rotation validation ---------------------------------------------------

_ROWS = {'orientation': 'landscape', 'rows': [{'name': 'r', 'height': 1.0, 'panels': [{'type': 'text', 'label': 'x'}]}]}


class TestRotationValidation:
    @pytest.mark.parametrize('rot', [0, 90, 180, 270])
    def test_valid(self, rot):
        assert not validate_config_dict({**_ROWS, 'display': {'rotation': rot}}).issues

    @pytest.mark.parametrize('rot', [45, '180', True, -90, 360])
    def test_invalid(self, rot):
        res = validate_config_dict({**_ROWS, 'display': {'rotation': rot}})
        assert any('rotation' in i.message for i in res.errors)

    def test_schema_matches_constant(self):
        schema = yaml.safe_load((_ROOT / 'configs' / 'schema' / 'clockish-config.schema.yaml').read_text())
        assert tuple(schema['properties']['display']['properties']['rotation']['enum']) == DISPLAY_ROTATIONS


# --- scripts/testbed.py -------------------------------------------------------------

@pytest.fixture(scope='module')
def testbed():
    spec = importlib.util.spec_from_file_location('testbed', _ROOT / 'scripts' / 'testbed.py')
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _vars(testbed, **extra):
    return {**testbed.DEFAULTS, 'clockish_config': 'configs/tiny.yaml', **extra}


def _run_display_local(testbed, home: pathlib.Path, overrides):
    """Run just the generated display_local function + call against a fake HOME."""
    v = _vars(testbed, **({'display_overrides': overrides} if overrides else {}))
    lines = testbed.deploy_script(v, 'main', False, True).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith('display_local()'))
    (home / '.config' / 'clockish').mkdir(parents=True, exist_ok=True)
    end = next(i for i, ln in enumerate(lines) if ln.startswith('.venv/bin/clockish-validate'))
    return subprocess.run(['bash', '-c', '\n'.join(lines[start:end])], env={'HOME': str(home), 'PATH': '/usr/bin:/bin'},
                          capture_output=True, text=True, check=True)


class TestTestbedDeploy:
    def test_writes_overrides_before_validate(self, testbed, tmp_path):
        script = testbed.deploy_script(_vars(testbed, display_overrides={'rotation': 180}), 'main', False, True)
        assert script.index('display_local ') < script.index('clockish-validate')
        _run_display_local(testbed, tmp_path, {'rotation': 180})
        text = (tmp_path / testbed.LOCAL_FILE).read_text()
        assert text.splitlines()[0] == testbed.LOCAL_MARKER
        assert yaml.safe_load(text) == {'display': {'rotation': 180}}

    def test_rewrites_and_removes_its_own_file(self, testbed, tmp_path):
        _run_display_local(testbed, tmp_path, {'rotation': 180})
        _run_display_local(testbed, tmp_path, {'rotation': 270})
        assert yaml.safe_load((tmp_path / testbed.LOCAL_FILE).read_text()) == {'display': {'rotation': 270}}
        _run_display_local(testbed, tmp_path, None)
        assert not (tmp_path / testbed.LOCAL_FILE).exists()

    def test_hand_made_file_is_kept(self, testbed, tmp_path):
        own = tmp_path / testbed.LOCAL_FILE
        own.parent.mkdir(parents=True)
        own.write_text('display: {rotation: 90}\n')
        assert 'kept' in _run_display_local(testbed, tmp_path, {'rotation': 180}).stderr
        assert _run_display_local(testbed, tmp_path, None).stderr == ''
        assert own.read_text() == 'display: {rotation: 90}\n'

    @pytest.mark.parametrize('bad', [180, 'rotation: 180', [180], {}])
    def test_rejects_non_mapping(self, testbed, bad):
        with pytest.raises(ValueError, match='display_overrides'):
            testbed.deploy_script(_vars(testbed, display_overrides=bad), 'main', False, True)

    def test_status_shows_overrides(self, testbed):
        assert testbed.LOCAL_FILE in testbed.service_script(testbed.DEFAULTS, 'status')
