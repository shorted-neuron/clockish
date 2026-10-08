"""tests/test_ssd1306.py

The native SSD1306 I2C driver (clockish/drivers/ssd1306.py).  A fake bus records every write, so none
of this needs a Pi or an OLED.  The expected command stream and the page packing were checked byte for
byte against adafruit_ssd1306 (random images, three panel sizes) when the driver replaced it; the
literals below keep that guarantee after the Adafruit packages are gone.
"""
import os
import pathlib
import random

import pytest
from PIL import Image

from clockish.drivers import ssd1306 as drv
from clockish.drivers.ssd1306 import SSD1306Driver, init_commands, pack_pages

# adafruit_ssd1306.init_display() for 128x64, minus the library's leading display-on (0xAF).
INIT_128X64 = [
    0xAE, 0x20, 0x00, 0x40, 0xA1, 0xA8, 0x3F, 0xC8, 0xD3, 0x00, 0xDA, 0x12, 0xD5, 0x80,
    0xD9, 0xF1, 0xDB, 0x30, 0x81, 0xFF, 0xA4, 0xA6, 0xAD, 0x30, 0x8D, 0x14, 0xAF,
]


def reference_pack(img: Image.Image) -> bytes:
    """Obvious per-pixel packing, independent of the Pillow trick in pack_pages()."""
    w, h = img.size
    out = bytearray()
    for page in range(h // 8):
        for x in range(w):
            byte = 0
            for bit in range(8):
                if img.getpixel((x, page * 8 + bit)):
                    byte |= 1 << bit
            out.append(byte)
    return bytes(out)


class FakeDevice:
    """Stands in for drv._I2CDevice and records every write."""
    instances: list = []

    def __init__(self, bus, addr):
        self.bus, self.addr, self.writes, self.closed = bus, addr, [], False
        self.fail_after = None
        FakeDevice.instances.append(self)

    def write(self, data):
        if self.fail_after is not None and len(self.writes) >= self.fail_after:
            raise OSError(121, "Remote I/O error")
        self.writes.append(bytes(data))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_dev(monkeypatch):
    FakeDevice.instances = []
    monkeypatch.setattr(drv, '_I2CDevice', FakeDevice)
    return FakeDevice


def commands_in(write: bytes) -> list[int]:
    """Undo the 0x80-per-command framing."""
    assert len(write) % 2 == 0 and all(b == 0x80 for b in write[0::2])
    return list(write[1::2])


class TestInitCommands:
    def test_128x64_matches_the_adafruit_stream(self):
        assert init_commands(128, 64) == INIT_128X64

    def test_128x32_differs_only_in_mux_ratio_and_com_pin_config(self):
        a, b = init_commands(128, 64), init_commands(128, 32)
        diffs = {i: (a[i], b[i]) for i in range(len(a)) if a[i] != b[i]}
        assert diffs == {a.index(0xA8) + 1: (0x3F, 0x1F), a.index(0xDA) + 1: (0x12, 0x02)}


class TestPackPages:
    @pytest.mark.parametrize('size', [(128, 64), (128, 32), (64, 32), (8, 8), (16, 24)])
    def test_matches_a_per_pixel_reference_on_random_images(self, size):
        rng = random.Random(size[0] * 1000 + size[1])
        for _ in range(8):
            img = Image.new('1', size)
            img.putdata([rng.choice((0, 255)) for _ in range(size[0] * size[1])])
            assert pack_pages(img) == reference_pack(img)

    @pytest.mark.parametrize('x, y', [(0, 0), (127, 0), (0, 63), (127, 63), (5, 9), (64, 31), (3, 8), (3, 7)])
    def test_one_lit_pixel_sets_exactly_one_bit_in_the_right_page_column_and_bit(self, x, y):
        img = Image.new('1', (128, 64))
        img.putpixel((x, y), 255)
        data = pack_pages(img)
        assert len(data) == 128 * 8
        assert [(i, b) for i, b in enumerate(data) if b] == [((y // 8) * 128 + x, 1 << (y % 8))]

    def test_all_black_and_all_white(self):
        assert pack_pages(Image.new('1', (128, 64), 0)) == bytes(1024)
        assert pack_pages(Image.new('1', (128, 64), 255)) == b'\xff' * 1024


class TestDriver:
    def test_begin_sends_init_then_addresses_then_a_black_frame(self, fake_dev):
        d = SSD1306Driver({'width': 128, 'height': 64}).begin()
        dev = fake_dev.instances[0]
        assert (dev.bus, dev.addr) == (1, 0x3C)
        assert commands_in(dev.writes[0]) == INIT_128X64
        assert commands_in(dev.writes[1]) == [0x21, 0, 127, 0x22, 0, 7]
        assert dev.writes[2] == b'\x40' + bytes(1024)
        assert d.dimensions == (128, 64) and len(dev.writes) == 3

    def test_display_is_two_transfers_address_then_data(self, fake_dev):
        d = SSD1306Driver({'width': 128, 'height': 64}).begin()
        img = Image.new('1', (128, 64), 0)
        img.putpixel((10, 20), 255)
        d.display(img)
        dev = fake_dev.instances[0]
        assert commands_in(dev.writes[-2]) == [0x21, 0, 127, 0x22, 0, 7]
        assert dev.writes[-1] == b'\x40' + reference_pack(img)

    def test_prepare_is_pure_conversion_and_push_is_pure_io(self, fake_dev):
        d = SSD1306Driver({'width': 128, 'height': 64}).begin()
        n = len(fake_dev.instances[0].writes)
        frame = d.prepare(Image.new('RGB', (128, 64), 'white'))
        assert isinstance(frame, bytes) and len(frame) == 1024
        assert len(fake_dev.instances[0].writes) == n                       # prepare wrote nothing
        d.push(frame)
        assert len(fake_dev.instances[0].writes) == n + 2

    @pytest.mark.parametrize('size', [(128, 64), (128, 32), (64, 32)])
    @pytest.mark.parametrize('delta', [-1, 1, -8])
    def test_a_wrong_sized_frame_is_rejected_and_nothing_is_written(self, fake_dev, size, delta):
        d = SSD1306Driver({'width': size[0], 'height': size[1]}).begin()
        n = len(fake_dev.instances[0].writes)
        with pytest.raises(ValueError, match=rf'expected {size[0] * size[1] // 8}'):
            d.push(bytes(size[0] * size[1] // 8 + delta))
        assert len(fake_dev.instances[0].writes) == n

    @pytest.mark.parametrize('size', [(128, 64), (128, 32), (64, 32)])
    def test_a_prepared_frame_always_has_the_size_push_expects(self, fake_dev, size):
        d = SSD1306Driver({'width': size[0], 'height': size[1]}).begin()
        n = len(fake_dev.instances[0].writes)
        d.push(d.prepare(Image.new('RGB', (200, 150), 'white')))
        assert len(fake_dev.instances[0].writes) == n + 2

    def test_non_1bit_and_wrong_size_images_are_converted_and_resized(self, fake_dev):
        d = SSD1306Driver({'width': 128, 'height': 64}).begin()
        frame = d.prepare(Image.new('RGB', (240, 135), (255, 255, 255)))
        assert frame == b'\xff' * 1024

    def test_rotation_is_applied_before_packing(self, fake_dev):
        d = SSD1306Driver({'width': 128, 'height': 64, 'rotation': 180}).begin()
        img = Image.new('1', (128, 64), 0)
        img.putpixel((0, 0), 255)
        assert d.prepare(img) == reference_pack(img.rotate(180, expand=True))

    def test_narrow_panels_are_centred_in_the_128_columns(self, fake_dev):
        SSD1306Driver({'width': 64, 'height': 32}).begin()
        assert commands_in(fake_dev.instances[0].writes[1]) == [0x21, 32, 95, 0x22, 0, 3]

    def test_address_bus_and_string_address(self, fake_dev):
        SSD1306Driver({'i2c_addr': '0x3d', 'i2c_bus': 3}).begin()
        assert (fake_dev.instances[0].bus, fake_dev.instances[0].addr) == (3, 0x3D)

    @pytest.mark.parametrize('key', ['scl_pin', 'sda_pin'])
    def test_blinka_pin_overrides_are_ignored_with_a_warning(self, fake_dev, capsys, key):
        SSD1306Driver({key: 3}).begin()
        out = capsys.readouterr().out
        assert 'WARNING' in out and key in out and 'i2c_bus' in out

    @pytest.mark.parametrize('cfg', [{'height': 12}, {'height': 0}, {'width': 200}, {'width': 0}])
    def test_impossible_sizes_are_rejected(self, cfg):
        with pytest.raises(ValueError, match='SSD1306'):
            SSD1306Driver(cfg)

    def test_no_response_gives_a_wiring_hint_and_releases_the_bus(self, fake_dev):
        original_init = fake_dev.__init__

        def failing(self, bus, addr):
            original_init(self, bus, addr)
            self.fail_after = 0
        fake_dev.__init__ = failing
        try:
            with pytest.raises(RuntimeError, match=r"no response.*0x3C.*i2cdetect -y 1"):
                SSD1306Driver({}).begin()
        finally:
            fake_dev.__init__ = original_init
        assert fake_dev.instances[0].closed

    def test_clear_writes_black_and_swallows_bus_errors(self, fake_dev):
        d = SSD1306Driver({}).begin()
        d.clear()
        assert fake_dev.instances[0].writes[-1] == b'\x40' + bytes(1024)
        fake_dev.instances[0].fail_after = 0
        d.clear()                                                            # must not raise

    def test_close_is_idempotent_and_later_calls_do_nothing(self, fake_dev):
        d = SSD1306Driver({}).begin()
        d.close()
        d.close()
        d.push(bytes(1024))
        d.clear()
        assert fake_dev.instances[0].closed

    def test_clears_on_exit_by_default(self):
        assert SSD1306Driver.DEFAULT_CLEAR_ON_EXIT is True


class TestI2CDevice:
    """The real bus object, with os/fcntl faked."""

    @pytest.fixture(autouse=True)
    def _linux_only(self):
        pytest.importorskip('fcntl')

    def test_opens_the_bus_selects_the_slave_and_writes_once(self, monkeypatch):
        import fcntl
        calls = []
        monkeypatch.setattr(os, 'open', lambda path, flags: calls.append(('open', path)) or 77)
        monkeypatch.setattr(fcntl, 'ioctl', lambda fd, req, arg: calls.append(('ioctl', fd, hex(req), arg)))
        monkeypatch.setattr(os, 'write', lambda fd, data: calls.append(('write', fd, len(data))) or len(data))
        monkeypatch.setattr(os, 'close', lambda fd: calls.append(('close', fd)))
        dev = drv._I2CDevice(1, 0x3C)
        dev.write(b'\x40' + bytes(1024))
        dev.close()
        assert calls == [('open', '/dev/i2c-1'), ('ioctl', 77, '0x703', 0x3C), ('write', 77, 1025), ('close', 77)]

    def test_missing_bus_explains_how_to_enable_i2c(self, monkeypatch):
        def deny(path, flags):
            raise FileNotFoundError(2, 'No such file or directory')
        monkeypatch.setattr(os, 'open', deny)
        with pytest.raises(RuntimeError, match=r"/dev/i2c-1.*raspi-config.*'i2c' group"):
            drv._I2CDevice(1, 0x3C)

    def test_ioctl_failure_closes_the_descriptor(self, monkeypatch):
        import fcntl
        closed = []
        monkeypatch.setattr(os, 'open', lambda path, flags: 5)
        monkeypatch.setattr(os, 'close', closed.append)

        def bad(fd, req, arg):
            raise OSError(16, 'Device or resource busy')
        monkeypatch.setattr(fcntl, 'ioctl', bad)
        with pytest.raises(RuntimeError, match='0x3C'):
            drv._I2CDevice(1, 0x3C)
        assert closed == [5]

    def test_short_write_is_an_error(self, monkeypatch):
        import fcntl
        monkeypatch.setattr(os, 'open', lambda path, flags: 5)
        monkeypatch.setattr(fcntl, 'ioctl', lambda *a: None)
        monkeypatch.setattr(os, 'write', lambda fd, data: len(data) - 1)
        with pytest.raises(OSError, match='short I2C write'):
            drv._I2CDevice(1, 0x3C).write(b'abcd')


class TestNoBlinka:
    def test_driver_imports_no_adafruit_blinka_or_gpio_library(self):
        import ast
        tree = ast.parse(pathlib.Path(drv.__file__).read_text())
        imported = set()
        for node in ast.walk(tree):                                          # includes imports inside functions
            if isinstance(node, ast.Import):
                imported |= {a.name.split('.')[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        forbidden = {'board', 'busio', 'digitalio', 'RPi', 'lgpio', 'gpiod', 'smbus', 'smbus2'}
        assert not imported & forbidden
        assert not {m for m in imported if m.startswith('adafruit')}
