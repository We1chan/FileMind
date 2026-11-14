import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from filemind.library import Library
from filemind.qa import Questions, RemoteSettings
from filemind.search import Search
from filemind.storage import SearchScope


@pytest.mark.parametrize("reply_style", ["json", "wrapped", "fallback"])
def test_remote_is_opt_in_and_sends_only_scoped_evidence(
    library: Library,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reply_style: str,
) -> None:
    first, second = tmp_path / "first-note.txt", tmp_path / "other-note.txt"
    first.write_text("FirstScopeKeyword: the valid  excerpt.", encoding="utf-8")
    second.write_text("FirstScopeKeyword: the other material.", encoding="utf-8")
    library.import_files([first, second])
    file_id = next(file.id for file in library.store.files() if file.name == first.name)
    scope = SearchScope(file_ids=(file_id,))
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            context = json.loads(body["messages"][1]["content"])
            requests.append((self.headers["Authorization"], body.get("response_format"), context))
            if reply_style == "fallback" and "response_format" in body:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Unsupported response_format"}}')
                return
            evidence = context["evidence"][0]
            citation = {"chunk_id": evidence["chunk_id"], "version": evidence["version"]}
            if reply_style == "json":
                citation["quote"] = "valid excerpt"
            content = json.dumps({"answer": "Verified answer [1]", "citations": [citation]})
            if reply_style != "json":
                content = f"Sources are below.\n```json\n{content}\n```"
            encoded = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            monkeypatch.setenv(
                "FILEMIND_LLM_URL", f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
            )
            monkeypatch.setenv("FILEMIND_LLM_MODEL", "mock")
            monkeypatch.setenv("FILEMIND_LLM_API_KEY", "example-key")
            remote = RemoteSettings.from_environment()
            assert remote is not None
            questions = Questions(Search(library))
            local = questions.ask("FirstScopeKeyword", "keyword", scope)
            assert not requests and not local.text and len(local.citations) == 1
            online = questions.ask("FirstScopeKeyword", "keyword", scope, remote)
            assert online.text == "Verified answer [1]"
            expected_quote = "valid  excerpt" if reply_style == "json" else local.citations[0].quote
            assert online.citations[0].quote == expected_quote
            assert expected_quote in online.citations[0].passage.text
            token, response_format, context = requests[0]
            assert token == "Bearer example-key"
            assert response_format == {"type": "json_object"}
            assert set(context) == {"question", "evidence"}
            assert len(context["evidence"]) == 1
            assert set(context["evidence"][0]) == {"chunk_id", "version", "quote"}
            assert "other material" not in str(context) and str(tmp_path) not in str(context)
            assert not questions.ask("nonmatching_unique_term", "keyword", scope, remote).citations
            if reply_style == "fallback":
                assert requests[1] == (token, None, context)
                assert len(requests) == 2
            else:
                assert len(requests) == 1
        finally:
            server.shutdown()
            thread.join(timeout=3)


@pytest.mark.parametrize(
    "invalid", ["chunk_id", "version", "quote", "marker", "no_marker", "changed", "offline"]
)
def test_unverified_or_unavailable_answers_fall_back(
    library: Library,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    note = tmp_path / "notes.txt"
    note.write_text("UniqueKeyword verified original", encoding="utf-8")
    library.import_files([note])
    file = library.store.files()[0]

    def bad_answer(settings, question, evidence):
        if invalid == "offline":
            raise ConnectionError("simulated connection failure")
        item = evidence[0]
        citation = {
            "chunk_id": item.passage.chunk_id,
            "version": file.version,
            "quote": "verified original",
        }
        if invalid == "changed":
            library.path_for(file).write_text("UniqueKeyword changed original", encoding="utf-8")
        elif invalid == "quote":
            citation[invalid] = "fabricated excerpt"
        elif invalid not in {"marker", "no_marker"}:
            citation[invalid] += 1
        marker = {"marker": " [2]", "no_marker": ""}.get(invalid, " [1]")
        return {"answer": "Unverified model text" + marker, "citations": [citation]}

    monkeypatch.setattr("filemind.qa._request_answer", bad_answer)
    remote = RemoteSettings("https://provider.invalid/v1/chat/completions", "mock", "example-key")
    answer = Questions(Search(library)).ask("UniqueKeyword", "keyword", remote=remote)
    assert answer.text == ""
    if invalid == "changed":
        assert not answer.citations and "原文已变更" in answer.notice
    else:
        assert len(answer.citations) == 1 and "verified original" in answer.citations[0].quote


def test_insufficient_remote_evidence_keeps_local_excerpts(
    library: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = tmp_path / "notes.txt"
    note.write_text("Existing evidence contains only a course title.", encoding="utf-8")
    library.import_files([note])
    monkeypatch.setattr("filemind.qa._request_answer", lambda *_: {"answer": "", "citations": []})
    settings = RemoteSettings("https://provider.invalid/v1/chat/completions", "mock", "example-key")

    result = Questions(Search(library)).ask("course title", "keyword", remote=settings)

    assert not result.text
    assert "当前证据不足以回答" in result.notice
    assert len(result.citations) == 1
    assert result.citations[0].quote == "Existing evidence contains only a course title."
