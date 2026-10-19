"""A skill's Python files are imported as they are on disk now.

Python trusts a cached ``.pyc`` whose recorded size and modification second
match the source. A tool file saved twice within one second at the same size
(``withdraw`` renamed to ``take_out``, eight letters each) would come back as
the old code: on a fast machine the Studio's Tools page then kept running a
function the user had just renamed. The stale cache is written here on purpose,
so the test does not depend on how fast the machine is.
"""
from __future__ import annotations

import importlib.util
import os
import py_compile
from pathlib import Path

from aigenos_flow.skills import _import_handlers, _import_module_file

BEFORE = "def withdraw():\n    return 1\n"
AFTER = "def take_out():\n    return 2\n"


def _stale_cache_then_same_size_edit(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BEFORE, encoding="utf-8")
    first = os.stat(path)
    # What a normal import leaves behind: bytecode recording this size and second.
    py_compile.compile(str(path), cfile=importlib.util.cache_from_source(str(path)), doraise=True)
    path.write_text(AFTER, encoding="utf-8")
    os.utime(path, (first.st_atime, first.st_mtime))
    assert os.stat(path).st_size == first.st_size, "the edit keeps the file the same size"


def test_a_tool_file_saved_twice_in_one_second_at_the_same_size_is_read_again(tmp_path):
    path = tmp_path / "tools" / "bank.py"
    _stale_cache_then_same_size_edit(path)
    module = _import_module_file(path, "_flow_tools")
    assert hasattr(module, "take_out") and not hasattr(module, "withdraw")


def test_a_handlers_file_is_read_as_it_is_on_disk_too(tmp_path):
    path = tmp_path / "my-skill" / "handlers.py"
    _stale_cache_then_same_size_edit(path)
    module = _import_handlers(path)
    assert module.take_out() == 2
