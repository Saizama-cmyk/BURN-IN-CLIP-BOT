from clipbot.config import DetectorCfg
from clipbot.detector import Detector, KeywordMatcher
from clipbot.models import Platform, StreamTarget

T = StreamTarget(Platform.TWITCH, "streamer", "Streamer", "Just Chatting", 1000)


def feed_flat(det: Detector, start: float, seconds: int, per_s: int, text: str = "hello there"):
    """``per_s`` messages every second from ``start``, evaluating each tick. Returns events."""
    events = []
    for s in range(seconds):
        t = start + s
        for i in range(per_s):
            det.add_message(T.key, text, t + i / (per_s + 1))
        ev = det.evaluate(T, t + 1)
        if ev:
            events.append(ev)
    return events


def test_flat_chat_stays_quiet():
    det = Detector(DetectorCfg())
    assert feed_flat(det, 0.0, 400, 3) == []
    assert det.stats(T.key)["warm"] is True


def test_chat_spike_fires_once_then_cools_down():
    cfg = DetectorCfg()
    det = Detector(cfg)
    assert feed_flat(det, 0.0, 200, 2) == []
    # burst: 25 msgs/s for 12 s
    events = feed_flat(det, 200.0, 12, 25)
    assert len(events) == 1, "cooldown must suppress repeat fires"
    ev = events[0]
    assert ev.kind in ("chat", "mixed")
    assert ev.chat_z >= cfg.z_threshold
    assert ev.chat_rate >= cfg.min_msgs_per_s
    assert len(ev.chat_sample) == cfg.chat_sample_size


def test_no_fire_before_warmup():
    det = Detector(DetectorCfg(min_history_s=60))
    assert feed_flat(det, 0.0, 30, 30) == []


def test_keyword_needs_threshold_and_own_baseline():
    cfg = DetectorCfg(min_msgs_per_s=500)  # disable the chat trigger to isolate keywords
    det = Detector(cfg)
    # a chat that always spams KEKW: high keyword baseline
    assert feed_flat(det, 0.0, 200, 3, "KEKW KEKW") == []
    # same density of KEKW does not fire (not 3x its own baseline)
    assert feed_flat(det, 200.0, 20, 3, "KEKW") == []
    det2 = Detector(cfg)
    feed_flat(det2, 0.0, 200, 3, "just talking")
    events = feed_flat(det2, 200.0, 12, 3, "CLIP IT no way")
    assert events and events[0].kind == "keyword"
    assert "clip it" in events[0].keywords_hit


def test_keyword_matching_rules():
    m = KeywordMatcher({"clip it": 3, "KEKW": 1, "W": 0.5}, short_len=2)
    assert m.score("omg CLIP IT now")[0] == 3            # multi-word substring, any case
    assert m.score("KEKWait")[0] == 0                     # single word = token match only
    assert m.score("KEKW KEKW")[0] == 1                   # once per message
    assert m.score("W")[0] == 0.5                         # short keyword = whole message
    assert m.score("W streamer")[0] == 0                  # …not inside a sentence


def test_audio_spike():
    cfg = DetectorCfg(min_msgs_per_s=500, keyword_threshold=500)
    det = Detector(cfg)
    feed_flat(det, 0.0, 100, 1)
    for i in range(cfg.audio_history):
        det.add_audio(T.key, -30.0 + (i % 3) * 0.2, 100.0 + i)
    det.add_audio(T.key, -8.0, 121.0)
    ev = det.evaluate(T, 121.5)
    assert ev is not None and ev.kind == "audio" and ev.audio_z >= cfg.audio_z_threshold


def test_stats_and_sparkline():
    cfg = DetectorCfg(sparkline_points=10)
    det = Detector(cfg)
    feed_flat(det, 0.0, 30, 2)
    st = det.stats(T.key)
    assert len(st["spark"]) == 10 and st["rate"] > 0
