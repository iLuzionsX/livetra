import asyncio

from session import SessionHub


def test_projector_joins_at_latest_final_and_current_partial_without_erasing_history():
    async def run():
        hub = SessionHub()
        producer = object()
        await hub.attach_producer("audience", producer)
        for utterance_id in range(1, 101):
            await hub.broadcast_transcript(
                "audience",
                {"type": "final", "utterance_id": utterance_id,
                 "original": "Welcome", "translation": "Bienvenidos"},
                producer,
            )
        await hub.broadcast_transcript(
            "audience",
            {"type": "partial", "utterance_id": 101,
             "original": "Today", "translation": "Hoy"},
            producer,
        )
        snapshot = await hub.attach_viewer("audience", object())
        assert [caption["utterance_id"] for caption in snapshot] == [100, 101]
        assert len(hub._rooms["audience"].transcript_state) == 101
    asyncio.run(run())


def test_projector_join_handles_empty_room_and_final_only():
    async def run():
        hub = SessionHub()
        viewer = object()
        assert await hub.attach_viewer("empty", viewer) == []
        await hub.detach("empty", viewer)
        producer = object()
        await hub.attach_producer("final", producer)
        final = {"type": "polished", "utterance_id": 9,
                 "original": "Thank you", "translation": "Gracias"}
        await hub.broadcast_transcript("final", final, producer)
        assert await hub.attach_viewer("final", viewer) == [final]
    asyncio.run(run())
