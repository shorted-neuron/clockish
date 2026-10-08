"""tests/test_packaging.py

Packaging rules that keep the Pi 1 GPIO problem from coming back (see AGENTS.md "GPIO backends"):

  * rpi-lgpio, RPi.GPIO and Adafruit Blinka are not dependencies or extras.  rpi-lgpio and RPi.GPIO
    install the same module (whichever pip installs last wins) and rpi-lgpio rejects a Pi 1 at import;
    Blinka hard-requires RPi.GPIO.
  * install.sh does not pip-install them either, and its I2C check (needed by the SSD1306 driver)
    behaves: a commented-out dtparam does not count as enabled.
"""
import pathlib
import re
import shutil
import subprocess
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / 'install.sh'

FORBIDDEN = {'rpi-lgpio', 'rpi-gpio', 'adafruit-blinka'}


def _name(requirement: str) -> str:
    """'RPi.GPIO>=0.7.1; platform_machine == ...' -> 'rpi-gpio' (PEP 503 normalised)."""
    base = re.split(r'[<>=!~;\[ ]', requirement.strip(), maxsplit=1)[0]
    return re.sub(r'[-_.]+', '-', base).lower()


def _all_requirements() -> dict[str, list[str]]:
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    groups = {'dependencies': project['dependencies']}
    groups.update({f'extra:{k}': v for k, v in project['optional-dependencies'].items()})
    return groups


class TestPyproject:
    @pytest.mark.parametrize('group', list(_all_requirements()))
    def test_no_conflicting_gpio_library_or_blinka(self, group):
        names = {_name(r) for r in _all_requirements()[group]}
        assert not names & FORBIDDEN, f"{group} pulls in {sorted(names & FORBIDDEN)}"
        assert not {n for n in names if n.startswith('adafruit-circuitpython')}, group

    def test_the_ssd1306_extra_still_exists_but_installs_nothing(self):
        # keeps `pip install clockish[ssd1306]` working for people who have it in a script
        assert _all_requirements()['extra:ssd1306'] == []

    def test_st7789_extra_still_has_what_the_driver_needs(self):
        names = {_name(r) for r in _all_requirements()['extra:st7789']}
        assert {'st7789', 'gpiod', 'gpiodevice'} <= names


class TestInstallSh:
    def test_bash_syntax(self):
        if shutil.which('bash') is None:
            pytest.skip('no bash')
        assert subprocess.run(['bash', '-n', str(INSTALL_SH)], capture_output=True).returncode == 0

    def test_does_not_install_the_conflicting_libraries(self):
        code = [ln for ln in INSTALL_SH.read_text().splitlines() if not ln.lstrip().startswith('#')]
        text = '\n'.join(code).lower()
        for needle in ('rpi-lgpio>=', 'pip install rpi-lgpio', 'adafruit-blinka', 'adafruit-circuitpython-ssd1306'):
            assert needle not in text, needle

    def test_apt_installs_the_gpio_backends(self):
        text = INSTALL_SH.read_text()
        assert 'python3-libgpiod' in text and 'python3-lgpio' in text


@pytest.fixture
def i2c_enabled(tmp_path):
    """Run install.sh's i2c_enabled() against a fake device node and fake boot configs."""
    bash = shutil.which('bash')
    if bash is None:
        pytest.skip('no bash')
    text = INSTALL_SH.read_text()
    start, end = text.index('# >>> i2c-check'), text.index('# <<< i2c-check')
    func = tmp_path / 'i2c_func.sh'
    func.write_text(text[start:end])

    def run(config_lines=None, device=False) -> bool:
        cfg = tmp_path / 'config.txt'
        if cfg.exists():
            cfg.unlink()
        if config_lines is not None:
            cfg.write_text('\n'.join(config_lines) + '\n')
        dev = tmp_path / 'i2c-1'
        if device:
            dev.write_text('')
        configs = f"{cfg} {tmp_path / 'absent.txt'}"                        # the second one never exists
        env = {'I2C_DEVICE': str(dev), 'BOOT_CONFIGS': configs, 'PATH': '/usr/bin:/bin'}
        return subprocess.run([bash, '-c', f'source {func}; i2c_enabled'], env=env).returncode == 0
    return run


class TestI2CCheck:
    def test_device_node_counts_as_enabled(self, i2c_enabled):
        assert i2c_enabled(device=True)

    @pytest.mark.parametrize('line', [
        'dtparam=i2c_arm=on',
        'dtparam=i2c_arm',
        'dtparam=i2c=on',
        'dtparam=i2c_arm=on,i2c_arm_baudrate=400000',
        'dtparam=audio=on,i2c_arm=on',
        'dtparam=i2c_arm=yes',
        'device_tree_param=i2c_arm=on',
    ])
    def test_enabling_dtparams_count(self, i2c_enabled, line):
        assert i2c_enabled(['# comment', 'dtparam=spi=on', line])

    @pytest.mark.parametrize('lines', [
        ['#dtparam=i2c_arm=on'],                 # exactly what clockish3w's boot config had
        ['  #dtparam=i2c_arm=on', 'dtparam=spi=on'],
        ['dtparam=i2c_arm=off'],
        ['dtparam=spi=on', 'dtoverlay=vc4-kms-v3d'],
        ['dtparam=i2c1=on'],                     # not what raspi-config's get_i2c accepts either
        [],
    ])
    def test_everything_else_is_not_enabled(self, i2c_enabled, lines):
        assert not i2c_enabled(lines)

    def test_missing_boot_config_is_not_enabled(self, i2c_enabled):
        assert not i2c_enabled(None)
