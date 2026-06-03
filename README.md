# clockish

Clockish is a customizable clock and _whatever_ display for Raspberry Pi with a PIL compatible display driver. 
It supports multiple "panel" widgets that can show time, date, weather, and more.

Unique in the universe?  Doubtful.  But I had fun making it.

---

## Table of Contents
- [Overview](#overview)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Project Structure](#project-structure)
- [On the Shoulders of Giants](#on-the-shoulders-of-giants)
- [AI / Artificial Intelligence Disclaimer](#ai--artificial-intelligence-disclaimer)
- [Third-Party / Upstream Code](#third-party--upstream-code)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

TODO: describe the project.

Target hardware: Raspberry Pi (Raspberry Pi OS / Ubuntu, armv7l / aarch64).

---

## Requirements

- Python 3.9 or newer
- Raspberry Pi OS Bookworm / Ubuntu 22.04+ (for hardware features)
- On Windows: most features work in a normal venv; GPIO / I²C stubs are skipped

---

## Installation

### On the Raspberry Pi (target device)

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/clockish.git
cd clockish

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package (editable mode for dev, or plain for prod)
pip install -e .
# -- or for a clean production install:
pip install .
```

### On Windows (development only)

```powershell
# In PyCharm the venv is usually created automatically.
# If not:
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
```

---

## Usage

```python
from clockish import ...   # TODO
```

Or from the command line (if entry-point is configured):

```bash
clockish
```

---

## Project Structure

```
clockish/
├── src/
│   └── clockish/          ← your package (importable as `import clockish`)
│       ├── __init__.py
│       └── ...
├── third_party/           ← upstream / third-party code included verbatim
│   └── README.md          ← explains what is here and why
├── tests/                 ← pytest test suite
├── scripts/               ← shell / Python helper scripts for the Pi
├── docs/                  ← documentation
├── pyproject.toml         ← packaging metadata & tool config
├── NOTICE.md              ← attribution for third-party code
├── .gitattributes         ← line-ending rules (critical for Windows→Linux)
└── .gitignore
```

---

## On the Shoulders of Giants

clockish is built on the shoulders of giants, trolls, hobbits, hacks, and
some people who are amazing and friendly beyond description.  I would
especially like to thank:

- Larry Wall, creator of Perl, the first language I really loved and learned to program in.
- [Electronic Frontier Foundation](https://www.eff.org)
- [SparkFun](https://www.sparkfun.com) — got me interested in software meeting hardware
- [Adafruit](https://www.adafruit.com) and Limor Fried — ditto
- The [Python Software Foundation](https://www.python.org/psf/)
- The [Raspberry Pi Foundation](https://www.raspberrypi.org)
- The open source community at large, and all the people who have contributed to it
- My parents, for being human instead of insects or something

---

## AI / Artificial Intelligence Disclaimer

This software was definitely assisted by AI.  I'm an old-ass programmmer
who understands every bit, and I sure am thankful i got to make a good
living at this sort of thing while that was still possible.

There's mistakes in here.  You're welcome to submit PRs, but they should
be small, focused, and deliver meaningful benefit to the project.

Meaningful is determined solely by the authors and authorize maintainers.

Contributions always welcome.  Fork this if you like, see the [LICENSE](LICENSE).

---

## Third-Party / Upstream Code

See [`NOTICE.md`](./NOTICE.md) for full attribution.
Third-party code lives in [`third_party/`](./third_party/) and is kept **unmodified** from
upstream wherever possible so that diffs against upstream are easy to produce.

---

## Contributing

Yeah... do it.  Contributing is fun.  Please be respectful and see the AI disclaimer and other missives.
Or don't, no one really cares.

---

## License

This project is licensed under the MIT License — see [`LICENSE`](./LICENSE).
Individual files in `third_party/` may carry their own licenses; see each
subdirectory's `LICENSE` file.

