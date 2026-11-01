"""Brain contracts: fallbacks, structured parsing, routing, hybrid fusion."""

from __future__ import annotations

import pytest

from raphael.brain.agents import Answerer, Appraiser, IntentRouter, Linker, Separator, Synthesizer
from raphael.brain.llm import LLMError, _extract_json
from raphael.brain.memory import HybridRetriever
from raphael.models import Note
from raphael.utils import chunk_text, content_hash, now


def _note(note_id: str, body: str, title: str = "Judul") -> Note:
    return Note(
        id=note_id,
        title=title,
        body=body,
        tags=["uji"],
        created_at=now(),
        updated_at=now(),
        content_hash=content_hash(body),
    )


def test_extract_json_tolerates_fences_and_prose() -> None:
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('Sure: {"a": 2} done') == {"a": 2}
    assert _extract_json("no json here") == {}
    assert _extract_json("[1, 2]") == {}


def test_appraiser_heuristic_fallback(service) -> None:
    result = service.appraiser.appraise(
        "Ide mekanik baru: sistem reputasi faksi di Tokyo berubah berdasarkan cuaca malam."
    )
    assert result.engine == "heuristic"
    assert result.title
    assert result.tags
    assert result.category in {"inbox", "projects", "references", "synthesized", "journal"}


def test_appraiser_parses_structured_output(fake_llm) -> None:
    agent = Appraiser(fake_llm())
    result = agent.appraise("teks apa saja")
    assert result.title == "Judul Uji"
    assert result.tags == ["tag-satu", "tag-dua"]  # normalised, '#' stripped
    assert result.entities == ["Shibuya"]
    assert result.category == "projects"
    assert result.engine == "local"


def test_appraiser_rejects_unknown_category(fake_llm) -> None:
    agent = Appraiser(fake_llm(appraisal={"title": "T", "summary": "S", "tags": [], "entities": [], "category": "hacker"}))
    assert agent.appraise("x", default_category="references").category == "references"


def test_linker_uses_llm_classification(fake_llm) -> None:
    class LinkingLLM(type(fake_llm())):
        def json_chat(self, messages, schema=None, **kwargs):
            return {"links": [{"index": 1, "relation_type": "contradicts", "reasoning": "jam bentrok"}]}

    llm = LinkingLLM()
    source = _note("a", "jam malam 22.00", "A")
    target = _note("b", "jam malam 20.00", "B")
    links = Linker(llm).link(source, [(target, 0.91)])
    assert links[0].relation_type == "contradicts"
    assert links[0].reasoning == "jam bentrok"
    assert links[0].similarity_score == 0.91


def test_linker_falls_back_to_similar(fake_llm) -> None:
    class BrokenLLM(type(fake_llm())):
        def json_chat(self, *a, **k):
            raise LLMError("down")

    links = Linker(BrokenLLM()).link(_note("a", "x"), [(_note("b", "y"), 0.8)])
    assert links[0].relation_type == "similar"


def test_answerer_without_engine_returns_sources(service) -> None:
    notes = [_note("a", "Isi catatan", "Catatan A")]
    answer = Answerer(service.llm, service.db).answer("apa isinya?", "konteks", notes)
    assert "Catatan A" in answer


def test_answerer_without_context(service) -> None:
    answer = Answerer(service.llm, service.db).answer("apa isinya?", "", [])
    assert "Tidak ada catatan" in answer


def test_synthesizer_fallback_merges_notes(service) -> None:
    notes = [_note("a", "Isi A", "Catatan A"), _note("b", "Isi B", "Catatan B")]
    result = Synthesizer(service.llm, service.db).synthesize(notes)
    assert result.engine == "heuristic"
    assert "# " in result.markdown
    assert "Isi A" in result.markdown and "Isi B" in result.markdown
    assert result.sources == ["a", "b"]


def test_separator_heuristic_extracts_tasks(service) -> None:
    result = Separator(service.llm, service.db).split(
        "Rencana besar\n- Menulis bab satu cerita\n- Merancang sistem reputasi\n- Menyusun jadwal rilis"
    )
    assert len(result.tasks) >= 2
    assert all(task.title for task in result.tasks)


def test_separator_parses_llm_tasks(fake_llm) -> None:
    llm = fake_llm(split={"goal": "Rilis", "tasks": [
        {"title": "Tulis draf", "priority": "high", "estimate": "2h"},
        {"title": "Review", "priority": "urgent"},
    ]})
    result = Separator(llm).split("materi")
    assert [t.title for t in result.tasks] == ["Tulis draf", "Review"]
    assert result.tasks[0].priority == "high"
    assert result.tasks[1].priority == "medium"  # invalid value coerced


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Apa aturan karakter di Tokyo?", "ask"),
        ("Bagaimana sistem reputasi bekerja", "ask"),
        ("Ide mekanik baru: reputasi faksi", "note"),
        ("what are the rules", "ask"),
        ("catat rapat besok", "note"),
        ("Ringkasan proyek", "note"),
        ("todo: beli kopi", "note"),
    ],
)
def test_intent_router(text: str, expected: str) -> None:
    assert IntentRouter.route(text) == expected


def test_chunking_respects_size_and_overlap() -> None:
    text = "\n\n".join(f"Paragraf {i} " + ("kata " * 40) for i in range(6))
    chunks = chunk_text(text, size=300, overlap=50)
    assert len(chunks) > 1
    assert all(len(chunk) <= 320 for chunk in chunks)


def test_chunking_single_short_text() -> None:
    assert chunk_text("pendek", size=100) == ["pendek"]
    assert chunk_text("   ", size=100) == []


def test_hybrid_search_fuses_keyword_and_vector(service, monkeypatch) -> None:
    service.capture("Sistem reputasi faksi Tokyo berubah saat malam.", appraise=False)
    service.capture("Jadwal latihan pedang setiap pagi.", appraise=False)

    monkeypatch.setattr(service.llm, "local_available", lambda refresh=False: True)
    monkeypatch.setattr(service.llm, "embed_one", lambda text: [1.0, 0.0, 0.0])
    monkeypatch.setattr(service.llm, "embed", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])

    service.retriever.embed_pending()

    hits = service.search("reputasi faksi")
    assert hits
    assert "Tokyo" in hits[0].title or "reputasi" in hits[0].snippet.lower()

    # Vector-only path: a query with no shared vocabulary still returns a hit.
    vector_hits = service.search("zzz-unmatched-query")
    assert vector_hits
    assert vector_hits[0].match in {"vector", "hybrid"}


def test_related_notes_respects_threshold(service, monkeypatch) -> None:
    monkeypatch.setattr(service.llm, "local_available", lambda refresh=False: True)
    monkeypatch.setattr(service.llm, "embed_one", lambda text: [1.0, 0.0, 0.0])
    monkeypatch.setattr(service.llm, "embed", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])

    first = service.capture("Catatan pertama tentang Tokyo.", appraise=False).note
    service.capture("Catatan kedua tentang Osaka.", appraise=False)
    service.retriever.embed_pending()

    related = service.retriever.related_notes(first, threshold=0.9)
    assert len(related) == 1
    assert related[0][0].id != first.id
    assert service.retriever.related_notes(first, threshold=1.1) == []
