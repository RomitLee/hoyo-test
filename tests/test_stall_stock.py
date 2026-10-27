from pathlib import Path

import cv2
import numpy as np

from hoyo_analyzer.panels import PanelDetector, PanelObservation
from hoyo_analyzer.stall_stock import StallStockDetector, StallStockTracker


FIXTURES = Path(__file__).parent / "fixtures" / "stall"


def image(name: str) -> np.ndarray:
    return cv2.imdecode(np.fromfile(FIXTURES / name, dtype=np.uint8), cv2.IMREAD_COLOR)


def test_buy_and_sell_titles_are_distinct_and_checked_sell_slots_are_counted():
    panels = PanelDetector()
    stock = StallStockDetector()
    buy = image("buy_title.png")
    sell = image("sell_panel.png")
    assert panels.analyze(buy).panel_type == "stall_buy"
    panel = panels.analyze(sell)
    assert panel.panel_type == "stall"
    assert stock.count(sell, panel) == 20
    enlarged = cv2.resize(sell, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_LINEAR)
    scaled_box = tuple(round(value * 1.25) for value in panel.bbox)
    assert stock.count(enlarged, PanelObservation("stall", panel.confidence, scaled_box)) == 20
    one_removed = sell.copy()
    one_removed[65:87, 77:99] = (120, 120, 120)
    assert stock.count(one_removed, panel) == 19
    changed_tab = sell.copy()
    changed_tab[35:57, 47:113] = 0
    assert stock.count(changed_tab, panel) is None
    changed_page = sell.copy()
    changed_page[87:100, 310:320] = 255
    assert stock.count(changed_page, panel) is None


def test_stock_count_needs_three_frames_and_five_seconds():
    tracker = StallStockTracker()
    assert tracker.update(20, 0) is None
    assert tracker.update(20, 100) is None
    assert tracker.update(20, 200) is None
    assert tracker.update(20, 5000) == 20
    assert tracker.update(19, 5100) is None
    assert tracker.update(19, 5200) is None
    assert tracker.update(19, 10100) == 19
    assert tracker.update(None, 10200) is None
    assert tracker.update(18, 10300) is None
