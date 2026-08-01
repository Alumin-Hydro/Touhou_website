"""Domain rules shared by member and staff post-editing paths."""

BIRDING_BOARD_NAME = "观鸟记录"


def normalize_post_metadata(
    board_name: str,
    bird_name: str | None,
    location: str | None,
) -> tuple[str, str]:
    """Trim optional metadata and enforce complete real-world birding records."""

    normalized_bird_name = (bird_name or "").strip()
    normalized_location = (location or "").strip()
    if board_name == BIRDING_BOARD_NAME and (
        not normalized_bird_name or not normalized_location
    ):
        raise ValueError("观鸟记录必须填写鸟种名称和观察地点")
    return normalized_bird_name, normalized_location
