from __future__ import annotations

import json
from pathlib import Path

import pytest

from molecule_ranker.utils.json_io import (
    JsonArtifactTooLargeError,
    iter_json_array,
    load_json_file,
)


def test_iter_json_array_streams_top_level_arrays(tmp_path: Path) -> None:
    path = tmp_path / "large-array.json"
    path.write_text(json.dumps([{"idx": index} for index in range(5)]))

    assert list(iter_json_array(path, chunk_size=8)) == [{"idx": index} for index in range(5)]


def test_load_json_file_enforces_bounded_memory_limit(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text('{"safe": true}')

    assert load_json_file(path, max_bytes=20) == {"safe": True}
    with pytest.raises(JsonArtifactTooLargeError):
        load_json_file(path, max_bytes=4)
