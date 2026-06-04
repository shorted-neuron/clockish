"""
Entry point — allows `python -m clockish` as well as the `clockish` CLI command
(the latter is configured in pyproject.toml [project.scripts]).
"""
import sys


def main() -> None:
    """Delegate to the display engine."""
    from clockish.display import main as _display_main
    _display_main()


if __name__ == "__main__":
    sys.exit(main())
