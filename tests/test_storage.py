import io
import json

from hoyo_analyzer.models import Event
from hoyo_analyzer.storage import ConsoleEventSink, JsonlEventSink


def test_jsonl_sink_writes_utf8(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlEventSink(path)
    sink.write(Event("evt_1", 1000, "map_entered", payload={"map_name": "北俱芦洲"}))
    sink.close()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["timestamp"]) == 19
    assert data["timestamp"][10] == " "
    assert data["elapsed_timestamp"] == "00:00:01.000"
    assert data["type"] == "进入地图"
    assert data["event_type"] == "map_entered"
    assert data["payload"]["map_name"] == "北俱芦洲"


def test_console_sink():
    stream = io.StringIO()
    ConsoleEventSink(stream).write(Event("evt_1", 0, "inventory_opened"))
    assert "打开背包" in stream.getvalue()
    assert "inventory_opened" not in stream.getvalue()
