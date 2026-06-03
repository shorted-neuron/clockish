"""
Entry point — allows `python -m clockish` as well as a `clockish` CLI command
(configure the latter in pyproject.toml [project.scripts]).
"""
import sys


def main() -> None:
    """Main entry point."""
    print("clockish running")


if __name__ == "__main__":
    sys.exit(main())

