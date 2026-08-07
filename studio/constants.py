GENRES = [
    "逆袭爽文",
    "都市修真",
    "霸总甜宠",
    "重生复仇",
    "悬疑惊悚",
    "玄幻修仙",
]

EPISODE_COUNT = 60

# Keep the legacy integer minute value for the existing project model. New generation
# uses the second-based short_drama_v3 contract below.
EPISODE_DURATION_MINUTES = 2
EPISODE_DURATION_MIN_SECONDS = 60
EPISODE_DURATION_TARGET_SECONDS = 75
EPISODE_DURATION_MAX_SECONDS = 90
EPISODE_DURATION_LABEL = "60–90 秒（目标 75 秒）"
PACING_PROFILE_VERSION = "short_drama_v3"

COLD_OPEN_DURATION_SECONDS = 3
NEXT_CRISIS_MIN_SECONDS = 5
NEXT_CRISIS_MAX_SECONDS = 8
MAX_INFORMATION_GAP_SECONDS = 10
# "后 40%" means the source beat starts at or after the 60% position.
COLD_OPEN_SOURCE_MIN_POSITION_RATIO = 0.6

# Persisted v2 pacing remains readable and exportable. These values must not be
# used by new generation requests.
LEGACY_PACING_PROFILE_VERSION = "content_adaptive_v2"
LEGACY_EPISODE_DURATION_MAX_SECONDS = 300
LEGACY_NEXT_CRISIS_MIN_SECONDS = 10
LEGACY_NEXT_CRISIS_MAX_SECONDS = 30
LEGACY_MAX_INFORMATION_GAP_SECONDS = 15

OUTLINE_CANDIDATE_COUNT = 6
MIN_STORYBOARD_SHOTS = 12
MAX_STORYBOARD_SHOTS = 15
