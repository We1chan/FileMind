import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from filemind.library import Library
from filemind.qa import Questions, RemoteSettings
from filemind.search import Search
from filemind.storage import SearchScope


def test_remote_is_opt_in_and_sends_only_scoped_evidence(
    library: Library,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = tmp_path / "first-note.txt", tmp_path / "other-note.txt"
    first.write_text("FirstScopeKeyword: the valid excerpt.", encoding="utf-8")
    second.write_text("FirstScopeKeyword: the other material.", encoding="utf-8")
    library.import_files([first, second])
    file_id = next(file.id for file in library.store.files() if file.name == first.name)
    scope = SearchScope(file_ids=(file_id,))
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            context = json.loads(body["messages"][1]["content"])
            requests.append((self.headers["Authorization"], context))
            evidence = context["evidence"][0]
            reply = {
                "answer": "Verified answer",
                "citations": [{**evidence, "quote": "valid excerpt"}],
            }
            encoded = json.dumps(
                {"choices": [{"message": {"content": json.dumps(reply)}}]}
            ).encode()
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
            assert online.text == "Verified answer" and online.citations[0].quote == "valid excerpt"
            token, context = requests[0]
            assert token == "Bearer example-key"
            assert set(context) == {"question", "evidence"}
            assert len(context["evidence"]) == 1
            assert set(context["evidence"][0]) == {"chunk_id", "version", "quote"}
            assert "other material" not in str(context) and str(tmp_path) not in str(context)
            assert not questions.ask("nonmatching_unique_term", "keyword", scope, remote).citations
            assert len(requests) == 1
        finally:
            server.shutdown()
            thread.join(timeout=3)


@pytest.mark.parametrize("invalid", ["chunk_id", "version", "quote", "changed", "offline"])
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
        else:
            citation[invalid] += 1
        return {"answer": "Unverified model text", "citations": [citation]}

    monkeypatch.setattr("filemind.qa._request_answer", bad_answer)
    remote = RemoteSettings("https://provider.invalid/v1/chat/completions", "mock", "example-key")
    answer = Questions(Search(library)).ask("UniqueKeyword", "keyword", remote=remote)
    assert answer.text == ""
    if invalid == "changed":
        assert not answer.citations and "原文已变更" in answer.notice
    else:
        assert len(answer.citations) == 1 and "verified original" in answer.citations[0].quote
