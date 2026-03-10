"""
Entry point for running nanobot as a module: python -m nanobot
"""

from nanobot.cli.commands import cli_app

if __name__ == "__main__":
    cli_app()
