# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Unit tests for BM25 full-text backend and query sanitization.

The sanitization logic mirrors PowerMem's ``_sanitize_fts5_input`` (#1125):
punctuation-heavy / operator-like queries must not silently void the
full-text recall channel. Source: oceanbase/powermem@b1930b6.
"""

import logging
from unittest import mock

import pytest

from hugegraph_llm.indices.fulltext import bm25_fulltext
from hugegraph_llm.indices.fulltext.bm25_fulltext import (
    BM25FullTextBackend,
    sanitize_fulltext_query,
)


# ── sanitize_fulltext_query ─────────────────────────────────


def test_sanitize_keeps_version_tokens():
    # "v1.1.6" -> punctuation stripped, word tokens kept literally
    assert sanitize_fulltext_query("v1.1.6") == "v1 1 6"


def test_sanitize_strips_symbol_heavy():
    # "C++" -> the "+" is dropped, leaving the literal word token "C"
    assert sanitize_fulltext_query("C++") == "C"


def test_sanitize_treats_operators_as_literals():
    # boolean operators are literal terms, not query syntax
    assert sanitize_fulltext_query("AND/OR/NOT logic") == "AND OR NOT logic"


def test_sanitize_pure_punctuation_empty():
    assert sanitize_fulltext_query("@#$%^&*()") == ""


def test_sanitize_empty_and_none():
    assert sanitize_fulltext_query("") == ""
    assert sanitize_fulltext_query(None) == ""
    assert sanitize_fulltext_query("   ") == ""


def test_sanitize_chinese_kept():
    assert sanitize_fulltext_query("张明 擅长 关系图谱") == "张明 擅长 关系图谱"


# ── BM25FullTextBackend.search robustness ──────────────────


def _backend_with_docs():
    be = BM25FullTextBackend()
    be.add_documents(
        [
            "张明 擅长 关系图谱 和 图数据库 运维",
            "C++ 是 一种 编程 语言",
            "PowerMem 版本 v1.1.6 已发布",
        ],
        ["m1", "m2", "m3"],
    )
    return be


def test_search_normal_query_ranked():
    be = _backend_with_docs()
    results = be.search("关系图谱", top_k=5)
    assert results
    assert results[0]["id"] == "m1"
    assert results[0]["score"] > 0


def test_search_symbol_query_does_not_crash():
    be = _backend_with_docs()
    # Previously this silently returned [] due to token dropping; now it
    # attempts the literal token and never raises.
    results = be.search("C++", top_k=5)
    assert isinstance(results, list)


def test_search_pure_punctuation_returns_empty_no_raise():
    be = _backend_with_docs()
    # Pure punctuation must not raise; returns empty list gracefully.
    results = be.search("@#$%", top_k=5)
    assert results == []


def test_search_logs_debug_on_empty_sanitized():
    be = _backend_with_docs()
    with mock.patch.object(bm25_fulltext.log, "debug") as mock_debug:
        results = be.search("@#$%", top_k=5)
    assert results == []
    assert mock_debug.called
    assert any("sanitization" in str(c.args) for c in mock_debug.call_args_list)


def test_search_version_query_attempts_match():
    be = _backend_with_docs()
    # "v1.1.6" -> tokens ["v1", "1", "6"]; "v1" is a real index token so it
    # should surface m3 (which contains "v1.1.6").
    results = be.search("v1.1.6", top_k=5)
    assert isinstance(results, list)
    ids = [r["id"] for r in results]
    assert "m3" in ids


def test_search_empty_backend():
    be = BM25FullTextBackend()
    assert be.search("anything", top_k=5) == []


def test_index_keeps_compact_tokens():
    # Indexing still drops single-char tokens (default min_token_len=2).
    be = BM25FullTextBackend()
    be.add_documents(["ab c"], ["x"])
    # "c" (len 1) is dropped at index time; only "ab" remains.
    assert be._tokenize("ab c", min_token_len=2) == ["ab"]
    # Search side keeps single chars so short queries still attempt a match.
    assert be._tokenize("c", min_token_len=1) == ["c"]
