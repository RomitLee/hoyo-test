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
    assert data["timestamp"] == "00:00:01.000" and data["payload"]["map_name"] == "北俱芦洲"


def test_console_sink():
    stream = io.StringIO()
    ConsoleEventSink(stream).write(Event("evt_1", 0, "inventory_opened"))
    assert "inventory_opened" in stream.getvalue()
