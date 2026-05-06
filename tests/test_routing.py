from ksch.routing import (
    segments_clear_existing,
    segments_clear_obstacles,
    split_segments_at_coordinates,
)


def test_segments_clear_existing_rejects_touching_segments_only() -> None:
    assert segments_clear_existing(
        [(0.0, 0.0, 10.0, 0.0)],
        [(0.0, 5.0, 10.0, 5.0)],
    )
    assert not segments_clear_existing(
        [(0.0, 0.0, 10.0, 0.0)],
        [(5.0, -5.0, 5.0, 5.0)],
    )
    assert not segments_clear_existing(
        [(0.0, 0.0, 10.0, 0.0)],
        [(10.0, 0.0, 20.0, 0.0)],
    )


def test_segments_clear_obstacles_respects_allowed_coordinates() -> None:
    segment = [(0.0, 0.0, 10.0, 0.0)]

    assert not segments_clear_obstacles(
        segment,
        obstacles={(5.0, 0.0), (5.0, 5.0)},
        allowed=set(),
    )
    assert segments_clear_obstacles(
        segment,
        obstacles={(5.0, 0.0), (5.0, 5.0)},
        allowed={(5.0, 0.0)},
    )


def test_split_segments_at_coordinates_ignores_off_segment_points() -> None:
    assert split_segments_at_coordinates(
        [(0.0, 0.0, 10.0, 0.0)],
        {(2.5, 0.0), (7.5, 0.0), (5.0, 5.0)},
    ) == [
        (0.0, 0.0, 2.5, 0.0),
        (2.5, 0.0, 7.5, 0.0),
        (7.5, 0.0, 10.0, 0.0),
    ]
