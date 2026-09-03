import numpy as np

from hoyo_analyzer.models import FramePacket, format_timestamp


def test_format_timestamp():
    assert format_timestamp(252480) == "00:04:12.480"


def test_frame_packet_timestamp():
    packet = FramePacket(3, 1000, np.zeros((2, 2, 3), dtype=np.uint8))
    assert packet.timestamp == "00:00:01.000"
