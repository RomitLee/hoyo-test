import numpy as np

from hoyo_analyzer.models import FramePacket
from hoyo_analyzer.sampler import AdaptiveSampler, LatestFrameQueue, SamplingProfile


def packets(count, step=100):
    image = np.zeros((1, 1, 3), dtype=np.uint8)
    return [FramePacket(i, i * step, image) for i in range(count)]


def test_sampler_5fps_from_10fps():
    sampler = AdaptiveSampler(SamplingProfile(normal_fps=5, battle_fps=10, burst_fps=20))
    selected = list(sampler.sample(packets(11)))
    assert [p.timestamp_ms for p in selected] == [0, 200, 400, 600, 800, 1000]


def test_latest_queue_discards_oldest():
    queue = LatestFrameQueue(2)
    for packet in packets(3):
        queue.put(packet)
    assert len(queue) == 2 and queue.dropped == 1 and queue.get().frame_index == 1
