"""Console entry point for ``aigenos-flow-server``.

Kept free of server imports: ``pip install aigenos-flow`` installs this command
too, and without the ``[server]`` extra the real entry point cannot even be
imported. This says what is missing instead of printing a traceback.
"""
from __future__ import annotations


def main(argv: list[str] | None = None) -> None:
    try:
        from .main import main as serve
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"aigenos-flow-server needs the server extra ({exc.name!r} is not installed).\n"
            '    pip install "aigenos-flow[server]"'
        ) from None
    serve(argv)
