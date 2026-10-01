## How tick alignment works in clockish

### Goal

Put several clockish units side by side and their seconds should change at the
same instant -- close enough that a person sees one tick, not a ripple. Target:
under 100 ms from the true top of the second, on any Pi and any display, with
the simplest code that gets there.

The clock source is already good enough: chrony on these Pis holds system time
within roughly 0.03-1.2 ms of NTP. The work is all in *when clockish puts the
frame on the glass*.

### What was wrong

The main loop rendered a frame, pushed it, then slept to the next whole second
of `time.monotonic()`.

The monotonic clock counts from an arbitrary point (boot), so its whole seconds
have no relation to real seconds. Every unit ticked at its own random offset
anywhere in 0-999 ms, plus the time spent rendering and pushing after waking.
Two clocks next to each other visibly disagreed.

### The fix, in two steps

**1. Tick on the wall clock, render ahead.** The loop now targets the next
whole second of `time.time()` (NTP-disciplined), not monotonic. Each pass:

1. picks `tick` = the next whole wall-clock second
2. renders the frame showing `tick` (not "now") -- `show_rows(at=tick)` feeds
   `tick` to every clock/date/bit_clock panel via `_now_in_tz(tz, at)`
3. sleeps until shortly before `tick`
4. pushes the frame

The push starts early by **half its measured duration**, so the push midpoint
lands on the boundary. Push duration is a running average
(`(avg + last) / 2`) of measured push times -- not configured -- so the same code
adapts to a 1 ms framebuffer write or a 130 ms SPI transfer, on a Pi 4 or an
original Zero. `tick = max(tick + 1, int(time.time()) + 1)` means a second is
never shown twice, and the loop skips ahead after a stall or config reload.

Why the midpoint: SPI panels repaint top-to-bottom *during* the transfer, so
some pixels change at the start of the push and some at the end. Centring the
transfer on the second keeps the worst-case pixel within half a transfer of it.

**2. Split conversion from transfer (`prepare()` / `push()`).** Step 1 alone
measured well on fast displays, but on an ILI9486 480x320 panel the seconds
digits landed 40-51 ms late. Timing the driver showed why: its 173 ms
`display()` was 62 ms of Python/numpy pixel conversion -- during which nothing
on screen changes -- followed by 111 ms of SPI transfer. The lead treated the
whole thing as transfer, so the real pixel writes started too late.

`DisplayDriver` now has two optional hooks:

- `prepare(image) -> frame` -- convert to the panel's wire format (CPU only)
- `push(frame)` -- send it (the part where pixels actually change)

`show_rows()` calls `prepare()` *before* sleeping and times only `push()`. The
base class defaults are pass-through (`prepare` returns the image, `push` calls
`display()`), so a driver that doesn't split still works -- its conversion just
counts as transfer. All four shipped drivers split, and each one's `display()`
is now `push(prepare(image))`:

| Driver | `prepare()` | `push()` |
|---|---|---|
| framebuffer | rotate + pack to RGB565 / XRGB bytes | `mmap` write |
| ili9486 | `pyili9486.image_to_data()` (RGB666/565) | `set_window` + `WRMEM` + SPI data |
| st7789 | Pimoroni `image_to_data()` (rotate + RGB565) | `set_window` + 4096-byte SPI chunks |
| ssd1306 | convert to 1-bit, `lcd.image()` (slow per-pixel Python loop) | `lcd.show()` (the I2C write) |

With the split, the ILI9486 seconds row moved from +40..+51 ms to +10..+21 ms.

### Measured results

Each unit ran for 45 s with its normal config, `--debug`. "Tick offset" is
the push midpoint relative to the second the frame shows (+ = late). Conversion
now happens before the sleep, so it no longer affects when pixels land.

| Board / display | Conversion (before the tick) | Push | Tick offset |
|---|---|---|---|
| Pi 4, framebuffer 800x480 | 16 ms | 1 ms | +0 ms |
| Pi 2B, ILI9486 480x320 | 54 ms | ~130 ms | ±4 ms; seconds digits change +10 to +21 ms |
| Zero 2 W, ST7789 240x240 | 6 ms | ~61 ms | -1 to +4 ms (one 105 ms push spike: +23 ms, then -11 ms the next frame) |
| Zero v1, ST7789 240x135 | ~22 ms | ~62 ms (was 69-98) | -3 to +9 ms |
| Pi 2B, SSD1306 I2C 128x64 | 55-65 ms | 96 ms flat | +0 ms every frame |

Before the split (step 1 only), under load: the Pi 4 stayed within ±8 ms with
all four cores pegged, and the Zero v1 within ±12 ms with its single core
pegged. Rendering and pushing roughly double under load, but the lead tracks the
push, so ticks barely move.

Things the tick offset does **not** include:

- **Where on the panel the digits are.** For SPI panels the offset is the
  transfer midpoint. A row's real change time is `push_start + (y / height) *
  transfer`. On the ILI9486 the top row changes about 63 ms early and the
  bottom row about 66 ms late. The seconds row (y = 163-214 of 320) lands
  +10..+21 ms.
- **Panel refresh.** LCD controllers scan their memory out at about 60 Hz, which
  adds 0-16 ms. Units with identical hardware add the same delay.
- **Clock error.** chrony's offset adds directly to the tick offset. A freshly
  booted Pi can sit at about 10 ms RMS until chrony settles.

### Measuring it yourself

```bash
./run-clockish.sh --debug configs/my.yaml
# show_rows: render=82ms  disp=61ms  tick=+1ms  [ntp=0ms, tz=0ms, draw=60ms, conv=22ms]
```

- `render` -- draw + `prepare()`, done before the sleep
- `conv` -- `prepare()` alone
- `disp` -- `push()` duration (what the lead is built from)
- `tick` -- push midpoint vs. the second shown

The first frame or two after startup can be off by tens of ms while the push
average settles from the slow first frame (fonts and caches warming up).

To time actual pixel writes on an SPI panel, wrap the driver library's send
call (e.g. `pyili9486.ILI9486.send`) in a throwaway script, record wall-clock
start/end of the large data transfer, and interpolate by row. That is how the
ILI9486 row timings above were measured.

### Budget and failure modes

- Render + `prepare()` + `push()` must fit in about 1 s. The slowest frame
  measured (original Zero, single core pegged) used about 300 ms.
- A frame that misses the boundary is pushed late, showing the correct second.
  The next pass re-targets from the wall clock, so the loop self-heals.
- A wall-clock step during the sleep (rare under chrony, which slews) mistimes
  one frame only.

### Ideas not built

- **Send only the changed region.** `pyili9486.display(image, x0, y0)` can
  write a sub-window. Sending only the changed rows would shrink a seconds-only
  update to about 15% of the frame. Catch: the push size then jumps at each
  minute rollover, so the lead would have to scale with the region's area.
  Only worth it if a panel misses the target.
- **Faster SPI writes.** Both SPI libraries send Python lists or slices through
  `writebytes`/`xfer`. `spidev.writebytes2()` with a byte buffer would cut
  transfer time, but means bypassing the libraries' data/command pin handling.
- **Faster I2C.** The SSD1306's flat 96 ms push suggests a 100 kHz bus.
  `dtparam=i2c_arm_baudrate=400000` would cut it to about 25 ms. Not needed
  for timing, since the push is centred either way.
- **Per-display offset setting.** Not needed while push duration is measured.

### Adding a display driver

If the new driver's `display()` does real conversion work, implement
`prepare()` (convert) and `push()` (send), and make `display()` =
`push(prepare(image))`. Otherwise its ticks land late by the conversion time.
`push()` must assume it is called right after the matching `prepare()` --
`show_rows()` guarantees that, and the SSD1306 driver relies on it (its
`prepare()` fills the library's internal buffer).
