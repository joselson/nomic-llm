"""Engine output must conform to the protocol models the client mirrors."""
from app import protocol as P
from game import dsl_examples as ex

from .helpers import make_world, run


def test_client_messages_parse():
    for raw in ['{"t":"join","name":"Ana"}', '{"t":"input","dx":1,"dy":-1,"seq":3}', '{"t":"interact"}',
                '{"t":"use_ability","ability":"dash","direction":90}', '{"t":"use_item","item":"apple"}',
                '{"t":"idea","text":"hi"}', '{"t":"vote","idea_id":null}',
                '{"t":"prompt_answer","prompt_id":"q1","option":0}', '{"t":"chat","text":"yo"}',
                '{"t":"start","settings":{"play_seconds":60}}']:
        P.client_msg.validate_json(raw)


def test_engine_messages_conform():
    w = make_world(ex.APPLE_TREES, ex.HOT_POTATO, ex.KING_OF_THE_HILL, ex.APPLE_DASH, ex.ORACLE, players=4)
    for p in w.players.values():
        p.items["apple"] = 6
    w.apply_status(w.players["p1"], "bomb")
    for i in range(400):
        for p in w.players.values():
            w.set_input(p.id, 1 if i % 40 < 20 else -1, 0.5, seq=i)
            if i % 20 == 0:
                w.queue_action("interact", p.id)
                w.queue_action("use_item", p.id, "apple")
                w.queue_action("use_ability", p.id, "dash", None, None, 45.0)
        run(w, 0.05)
    out = w.take_outbox()
    kinds = set()
    for _, m in out:
        P.server_msg.validate_python(m)
        kinds.add(m["t"])
    assert "points" in kinds
    common = w.snapshot_common()
    snap = dict(w.snapshot_for("p1", common), t="snapshot")
    P.server_msg.validate_python(snap)
    P.server_msg.validate_python({"t": "defs", **w.defs()})
